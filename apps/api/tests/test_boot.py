"""Boot the real ASGI entry point with production-shaped settings.

Builds the app through ``accelerator.api.main:create_application`` exactly as
``uvicorn --factory`` does, against a migrated
PostgreSQL database. Only the Azure edges are replaced: the managed-identity
credential and Entra JWKS endpoint, and the three SDK clients the grounded-answer
workflow talks to (Search, Foundry embeddings, Foundry chat agent). Everything
between them (scope injection, sufficiency, citation validation, SSE) is real.
"""

import importlib
import json
import os
import sys
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
import jwt
import pytest
from azure.core.credentials import AccessToken
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine

from accelerator.configuration.settings import get_settings

TENANT_ID = UUID("00000000-0000-0000-0000-0000000000b0")
AUDIENCE = "api://accelerator-boot-test"
ISSUER = f"https://login.microsoftonline.com/{TENANT_ID}/v2.0"
KEY_ID = "boot-test-key"
USER_OBJECT_ID = str(uuid4())
PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def fake_managed_identity(token: str) -> type:
    class FakeManagedIdentity:
        """Stands in for DefaultAzureCredential: the token is the test role's password,
        exactly as an Entra token is the password for Azure PostgreSQL."""

        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
            del scopes, kwargs
            return AccessToken(token, int(time.time()) + 3600)

        async def close(self) -> None:
            return None

    return FakeManagedIdentity


def tls_ca_file() -> str:
    path = os.environ.get("TEST_POSTGRES_CA_FILE")
    if not path:
        pytest.skip(
            "Set TEST_POSTGRES_CA_FILE to the test server's certificate to boot production."
        )
    return path


def production_environment(database_url: str) -> dict[str, str]:
    url = make_url(database_url)
    password_free = URL.create(
        url.drivername, username=url.username, host=url.host, port=url.port, database=url.database
    )
    return {
        "API_ENVIRONMENT": "production",
        "API_ENTRA_TENANT_ID": str(TENANT_ID),
        "API_ENTRA_AUDIENCE": AUDIENCE,
        "API_WEB_ORIGIN": "https://app.example.test",
        "API_DATABASE_URL": password_free.render_as_string(hide_password=False),
        # Production verifies the server certificate; the test server's CA.
        "API_DATABASE_TLS_CA_FILE": tls_ca_file(),
        "API_DATABASE_AUTH_MODE": "managed_identity",
        "AZURE_CLIENT_ID": "00000000-0000-0000-0000-0000000000c1",
        "API_FOUNDRY_PROJECT_ENDPOINT": "https://foundry.example.test/api/projects/boot",
        "API_FOUNDRY_MODEL_DEPLOYMENT": "chat-model",
        "API_FOUNDRY_EMBEDDING_DEPLOYMENT": "embedding-model",
        "API_SEARCH_VECTOR_DIMENSIONS": "3",
        "API_SEARCH_ENDPOINT": "https://search.example.test",
        "API_SEARCH_INDEX_NAME": "chunks",
        "APPLICATIONINSIGHTS_CONNECTION_STRING": (
            "InstrumentationKey=00000000-0000-0000-0000-000000000000"
        ),
    }


def access_token(roles: list[str]) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": "boot-subject",
            "oid": USER_OBJECT_ID,
            "roles": roles,
            "iat": now,
            "nbf": now,
            "exp": now + 300,
        },
        PRIVATE_KEY,
        algorithm="RS256",
        headers={"kid": KEY_ID},
    )


def jwks_client() -> httpx.AsyncClient:
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(PRIVATE_KEY.public_key()))
    jwk.update({"kid": KEY_ID, "use": "sig", "alg": "RS256"})

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/{TENANT_ID}/discovery/v2.0/keys"
        return httpx.Response(200, json={"keys": [jwk]})

    return httpx.AsyncClient(transport=httpx.MockTransport(respond))


async def grant_scope(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("INSERT INTO scope_memberships (object_id, scope_id) VALUES (:o, 'scope-a')"),
                {"o": USER_OBJECT_ID},
            )
    finally:
        await engine.dispose()


class FakeSearchClient:
    """Returns one hit per authorized scope from the scope filter it was given."""

    filters: list[str] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    async def search(self, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        FakeSearchClient.filters.append(kwargs["filter"])

        async def hits() -> AsyncIterator[dict[str, Any]]:
            yield {
                "scope_id": "scope-a",
                "chunk_id": "chunk-a",
                "document_id": "document-a",
                "document_title": "Operations guide",
                "version": "1",
                "text": f"Backups run nightly. {SEEDED_SECRETS[1]}",
                "source_uri": "https://documents.example.test/a",
                "@search.score": 0.03,
                "@search.reranker_score": 3.2,
            }

        return hits()

    async def close(self) -> None:
        return None


class FakeEmbeddingClient:
    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    async def get_embeddings(self, values: list[str], *, options: Any = None) -> list[Any]:
        return [type("Embedding", (), {"vector": [0.1] * options["dimensions"]})()]

    async def close(self) -> None:
        return None


class FakeAgent:
    """Mirrors agent_framework.Agent.run: an awaitable, or a stream with stream=True."""

    chunks = ("Backups run ", "nightly. [cite:", "chunk-a]")

    def run(self, messages: str, *, options: Any, tools: Any = None, stream: bool = False) -> Any:
        assert 'chunk_id="chunk-a"' in messages
        final = type("Response", (), {"text": "".join(self.chunks)})()
        if not stream:

            async def complete() -> Any:
                return final

            return complete()

        class Stream:
            async def __aiter__(inner) -> AsyncIterator[Any]:  # noqa: N805
                for chunk in self.chunks:
                    yield type("Update", (), {"text": chunk})()

            async def get_final_response(inner) -> Any:  # noqa: N805
                return final

        return Stream()


EXPORTERS: list[InMemorySpanExporter] = []
SEEDED_SECRETS = ("api_key=SEEDED-SECRET-QUERY", "password=SEEDED-SECRET-EVIDENCE")


class InMemoryApplicationInsights:
    """Stands in for the Azure Monitor exporter; the sanitizer still wraps it."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def create_exporter(self) -> InMemorySpanExporter:
        # One per app: shutting an app down stops its exporter.
        EXPORTERS.append(InMemorySpanExporter())
        return EXPORTERS[-1]

    def create_sampler(self) -> Any:
        return ALWAYS_ON

    def close(self) -> None:
        return None


def fake_create_agent(self: object, **kwargs: object) -> FakeAgent:
    del self, kwargs
    return FakeAgent()


@pytest.fixture
def production_app(migrated_database_url: str) -> Iterator[FastAPI]:
    with (
        patch("accelerator.infrastructure.grounded_answer.SearchClient", FakeSearchClient),
        patch(
            "accelerator.infrastructure.grounded_answer.FoundryEmbeddingClient",
            FakeEmbeddingClient,
        ),
        patch(
            "accelerator.infrastructure.foundry.agent_runtime."
            "AgentFrameworkFoundryRuntime.create_agent",
            fake_create_agent,
        ),
        patch(
            "accelerator.telemetry.tracing.ApplicationInsightsAdapter",
            InMemoryApplicationInsights,
        ),
        patch.dict("os.environ", production_environment(migrated_database_url), clear=True),
        patch(
            "azure.identity.aio.DefaultAzureCredential",
            fake_managed_identity(make_url(migrated_database_url).password or "unused"),
        ),
    ):
        get_settings.cache_clear()
        sys.modules.pop("accelerator.api.main", None)
        sys.modules.pop("accelerator.api.composition", None)
        main = importlib.import_module("accelerator.api.main")
        try:
            yield main.create_application()
        finally:
            get_settings.cache_clear()
            sys.modules.pop("accelerator.api.main", None)
            sys.modules.pop("accelerator.api.composition", None)


@asynccontextmanager
async def running(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        await app.state.http_client.aclose()
        app.state.http_client = jwks_client()
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://api.test"
            ) as client:
                yield client
        finally:
            await app.state.http_client.aclose()


def bearer(*roles: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token(list(roles))}"}


async def test_production_app_boots_and_serves_authenticated_routes(
    production_app: FastAPI, migrated_database_url: str
) -> None:
    await grant_scope(migrated_database_url)
    settings = production_app.state.settings
    assert settings.is_production
    assert settings.database_auth_mode == "managed_identity"

    async with running(production_app) as client:
        unauthenticated = await client.get("/audit-events")
        audit_page = await client.get("/audit-events?limit=10", headers=bearer("Admin"))
        ready = await client.get("/readyz")

    # A missing token is a 401 that was audited, not a 503 from missing persistence.
    assert unauthenticated.status_code == 401
    assert audit_page.status_code == 200, audit_page.text
    recorded = [item["correlation_id"] for item in audit_page.json()["items"]]
    assert unauthenticated.headers["x-correlation-id"] in recorded
    checks = ready.json()["checks"]
    assert checks["database"]["status"] == "ok", ready.text
    assert checks["identity_provider"]["status"] == "ok", ready.text


async def test_production_chat_stream_is_configured_and_ready(
    production_app: FastAPI, migrated_database_url: str
) -> None:
    await grant_scope(migrated_database_url)

    async with running(production_app) as client:
        ready = await client.get("/readyz")
        response = await client.post(
            f"/chat/stream?{SEEDED_SECRETS[0]}",
            json={"message": f"What is supported? {SEEDED_SECRETS[0]}"},
            headers=bearer("Reader"),
        )
    production_app.state.telemetry.force_flush()

    assert ready.status_code == 200, ready.text
    assert response.status_code == 200, response.text
    assert "event: token" in response.text
    assert '"chunk_id":"chunk-a"' in response.text
    assert "[cite:" not in response.text
    # The scope filter came from the database membership, not from the request.
    assert FakeSearchClient.filters[-1].startswith("(search.in(scope_id, 'scope-a'")

    # One trace per request, under the correlation ID the client received ...
    spans = EXPORTERS[-1].get_finished_spans()
    correlation_id = response.headers["x-correlation-id"]
    traced = [
        span
        for span in spans
        if (span.attributes or {}).get("fde.correlation_id") == correlation_id
    ]
    assert {span.name for span in traced} >= {
        "http.request",
        "workflow.grounded_answer",
        "retrieval.search",
        "retrieval.sufficiency",
        "gen_ai.chat",
        "citations.validate",
    }
    assert len({span.context.trace_id for span in traced}) == 1
    # ... and no secret from any input channel survives export.
    exported = json.dumps(
        [
            {
                "name": span.name,
                "attributes": dict(span.attributes or {}),
                "events": [dict(event.attributes or {}) for event in span.events],
            }
            for span in spans
        ],
        default=str,
    )
    for secret in ("SEEDED-SECRET-QUERY", "SEEDED-SECRET-EVIDENCE", "Bearer "):
        assert secret not in exported
