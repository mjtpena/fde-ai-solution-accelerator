"""Playwright drives the real Next.js app against the real API process.

The existing Playwright suite (``npm run test:e2e``) uses a mock API. This test
starts the API as in ``test_api_processes`` and runs
``apps/web/playwright.real-api.config.ts`` against it, then checks in PostgreSQL
that the browser's requests really went through token validation, scope
resolution, the shared rate limiter and the audit log. It needs the web
dependencies and a Chromium for Playwright, so it runs when ``E2E_LOCAL_WEB=1``
(``make e2e-local`` and CI set it).
"""

import asyncio
import hashlib
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from e2e_local_harness import (
    REPO_ROOT,
    ApiProcess,
    SigningAuthority,
    api_settings,
    free_port,
    migrated_database,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.skipif(
    os.environ.get("E2E_LOCAL_WEB") != "1",
    reason="Set E2E_LOCAL_WEB=1 (needs npm ci and a Playwright Chromium).",
)

WEB_USER = str(uuid4())
SCOPE = "scope-web"


async def _query(
    database_url: str, statement: str, **parameters: object
) -> list[dict[str, object]]:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            result = await connection.execute(text(statement), parameters)
            return [dict(row) for row in result.mappings()] if result.returns_rows else []
    finally:
        await engine.dispose()


@pytest.fixture(scope="module")
def stack(
    server_dsn: str, process_logs: Path
) -> Iterator[tuple[ApiProcess, SigningAuthority, str, int]]:
    authority = SigningAuthority()
    authority.serve()
    web_port = free_port()
    try:
        with migrated_database(server_dsn, process_logs) as database_url:
            asyncio.run(
                _query(
                    database_url,
                    "INSERT INTO scope_memberships (object_id, scope_id) VALUES (:oid, :scope)",
                    oid=WEB_USER,
                    scope=SCOPE,
                )
            )
            api = ApiProcess(
                "api-for-web",
                api_settings(
                    database_url,
                    authority,
                    web_origin=f"http://127.0.0.1:{web_port}",
                    rate_limit=1,
                ),
                process_logs,
            )
            api.start_and_wait()
            try:
                yield api, authority, database_url, web_port
            finally:
                api.stop()
    finally:
        authority.close()


def test_the_web_app_against_the_real_api(
    stack: tuple[ApiProcess, SigningAuthority, str, int], process_logs: Path
) -> None:
    api, authority, database_url, web_port = stack
    environment = {
        **os.environ,
        "CI": "true",  # never reuse a dev server that may point at another API
        "NEXT_TELEMETRY_DISABLED": "1",
        "E2E_API_BASE_URL": api.url,
        "E2E_TENANT_ID": authority.tenant_id,
        "E2E_WEB_PORT": str(web_port),
        "E2E_ACCESS_TOKEN": authority.token(object_id=WEB_USER, roles=("Reader",)),
        "E2E_FORGED_TOKEN": authority.token(
            object_id=WEB_USER, roles=("Reader",), foreign_signature=True
        ),
    }
    log = process_logs / "playwright-real-api.log"
    with log.open("wb") as output:
        completed = subprocess.run(
            ["npx", "playwright", "test", "--config", "playwright.real-api.config.ts"],
            cwd=REPO_ROOT / "apps" / "web",
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=600,
            check=False,
        )
    assert completed.returncode == 0, log.read_text(errors="replace")[-6000:]
    assert "2 passed" in log.read_text(errors="replace")

    # The browser's two chat requests reached the shared limiter with the token's
    # identity and the server-resolved scope; the forged one was audited as a 401.
    key = hashlib.sha256(f"{WEB_USER}\x1f{SCOPE}".encode()).hexdigest()
    windows = asyncio.run(
        _query(
            database_url,
            "SELECT sum(request_count) AS n FROM rate_limit_windows WHERE limiter_key = :key",
            key=key,
        )
    )
    failures = asyncio.run(
        _query(
            database_url,
            "SELECT count(*) AS n FROM audit_event WHERE event_type = 'auth_failure'",
        )
    )
    assert windows == [{"n": 2}]
    assert failures == [{"n": 1}]
