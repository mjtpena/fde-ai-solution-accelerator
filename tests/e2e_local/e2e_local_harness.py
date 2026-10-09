"""Real-process harness for the local end-to-end suite.

Everything here is real: a throwaway PostgreSQL database migrated by the
production migration CLI, an RSA signing key whose JWKS is served over HTTP, and
the API and ingestion worker started as separate operating-system processes with
their production entry points. Nothing is patched inside those processes; tests
talk to them over TCP exactly as clients and the platform do.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import zlib
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

T = TypeVar("T")

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Variables that would point a child process at real Azure resources or change how
# it authenticates; the children get a minimal, explicit environment instead.
_INHERITED = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT", "VIRTUAL_ENV")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def asyncpg_url(dsn: str) -> str:
    scheme, separator, rest = dsn.partition("://")
    if scheme in {"postgres", "postgresql"}:
        return f"postgresql+asyncpg{separator}{rest}"
    return dsn


def child_environment(values: Mapping[str, str]) -> dict[str, str]:
    environment = {name: os.environ[name] for name in _INHERITED if name in os.environ}
    environment["PYTHONUNBUFFERED"] = "1"
    environment.update(values)
    return environment


# --- PostgreSQL ------------------------------------------------------------------


async def _autocommit(dsn: str, statement: str) -> None:
    engine = create_async_engine(asyncpg_url(dsn), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(text(statement))
    finally:
        await engine.dispose()


@contextmanager
def migrated_database(server_dsn: str, log_dir: Path) -> Iterator[str]:
    """A fresh database, migrated by ``python -m accelerator.migrations``; dropped after."""
    name = f"accelerator_e2e_{uuid4().hex}"
    asyncio.run(_autocommit(server_dsn, f'CREATE DATABASE "{name}"'))
    url = make_url(asyncpg_url(server_dsn)).set(database=name)
    database_url = url.render_as_string(hide_password=False)
    try:
        log = log_dir / f"migrate-{name}.log"
        with log.open("wb") as output:
            completed = subprocess.run(
                [sys.executable, "-m", "accelerator.migrations", "upgrade", "head"],
                env=child_environment(
                    {"API_DATABASE_URL": database_url, "API_DATABASE_AUTH_MODE": "password"}
                ),
                cwd=REPO_ROOT,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=120,
                check=False,
            )
        if completed.returncode != 0:
            raise RuntimeError(f"migration failed:\n{log.read_text(errors='replace')}")
        yield database_url
    finally:
        asyncio.run(_autocommit(server_dsn, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


# --- Identity provider -----------------------------------------------------------


def _new_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@dataclass
class SigningAuthority:
    """A stand-in Entra tenant: real RS256 keys, a JWKS over HTTP, signed tokens.

    ``/keys`` serves the signing key; ``/slow/keys`` serves it after
    ``slow_seconds`` to exercise the API's identity-provider deadlines.
    """

    tenant_id: str = field(default_factory=lambda: str(uuid4()))
    audience: str = "api://e2e-local-accelerator"
    kid: str = field(default_factory=lambda: f"e2e-{uuid4().hex[:8]}")
    slow_seconds: float = 10.0
    _key: rsa.RSAPrivateKey = field(default_factory=_new_key)
    _foreign_key: rsa.RSAPrivateKey = field(default_factory=_new_key)
    _server: ThreadingHTTPServer | None = None
    requests: list[str] = field(default_factory=list)

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("the authority is not serving")
        host, port = self._server.server_address[:2]
        return f"http://{host!s}:{port}"

    @property
    def issuer(self) -> str:
        return f"{self.base_url}/{self.tenant_id}/v2.0"

    @property
    def jwks_uri(self) -> str:
        return f"{self.base_url}/keys"

    @property
    def slow_jwks_uri(self) -> str:
        return f"{self.base_url}/slow/keys"

    def jwks(self) -> dict[str, Any]:
        public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self._key.public_key()))
        public.update({"kid": self.kid, "use": "sig", "alg": "RS256"})
        return {"keys": [public]}

    def serve(self) -> None:
        authority = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - http.server API
                authority.requests.append(self.path)
                if self.path == "/slow/keys":
                    time.sleep(authority.slow_seconds)
                elif self.path != "/keys":
                    self.send_error(404)
                    return
                body = json.dumps(authority.jwks()).encode()
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    return  # the client gave up first, which is what the test wants

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    def token(
        self,
        *,
        object_id: str,
        roles: tuple[str, ...] = ("Reader",),
        audience: str | None = None,
        issuer: str | None = None,
        lifetime: timedelta = timedelta(minutes=30),
        issued_at: datetime | None = None,
        kid: str | None = None,
        foreign_signature: bool = False,
        extra: Mapping[str, Any] | None = None,
        omit: tuple[str, ...] = (),
    ) -> str:
        now = issued_at or datetime.now(UTC)
        claims: dict[str, Any] = {
            "iss": issuer or self.issuer,
            "aud": audience or self.audience,
            "sub": f"sub-{object_id}",
            "oid": object_id,
            "tid": self.tenant_id,
            "roles": list(roles),
            "iat": int(now.timestamp()),
            "nbf": int(now.timestamp()),
            "exp": int((now + lifetime).timestamp()),
        }
        claims.update(extra or {})
        for name in omit:
            claims.pop(name, None)
        key = self._foreign_key if foreign_signature else self._key
        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid or self.kid})


# --- Processes -------------------------------------------------------------------


class ManagedProcess:
    """A child process with its output in a log file and a clean, checked shutdown."""

    def __init__(self, name: str, argv: list[str], env: dict[str, str], log_dir: Path) -> None:
        self.name = name
        self._argv = argv
        self._env = env
        self.log_path = log_dir / f"{name}.log"
        self._process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        output = self.log_path.open("wb")
        self._process = subprocess.Popen(  # the argv is fixed by the harness
            self._argv, env=self._env, cwd=REPO_ROOT, stdout=output, stderr=subprocess.STDOUT
        )
        output.close()

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def log(self) -> str:
        return self.log_path.read_text(errors="replace") if self.log_path.exists() else ""

    def stop(self, timeout: float = 20.0) -> int:
        """SIGTERM, as the platform sends it; returns the exit code."""
        if self._process is None:
            return 0
        if self._process.poll() is None:
            self._process.send_signal(signal.SIGTERM)
            try:
                self._process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
                raise RuntimeError(f"{self.name} ignored SIGTERM:\n{self.log()[-4000:]}") from None
        return self._process.returncode


class ApiProcess(ManagedProcess):
    """``uvicorn --factory accelerator.api.main:create_application``, as in the image."""

    def __init__(self, name: str, settings: Mapping[str, str], log_dir: Path) -> None:
        self.port = free_port()
        super().__init__(
            name,
            [
                sys.executable,
                "-m",
                "uvicorn",
                "--factory",
                "accelerator.api.main:create_application",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--no-server-header",
                "--no-access-log",
            ],
            child_environment(settings),
            log_dir,
        )

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start_and_wait(self, timeout: float = 60.0) -> None:
        self.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.running:
                raise RuntimeError(f"{self.name} exited during start-up:\n{self.log()[-4000:]}")
            try:
                if httpx.get(f"{self.url}/healthz", timeout=1).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.2)
        self.stop()
        raise RuntimeError(f"{self.name} did not become live:\n{self.log()[-4000:]}")


def api_settings(
    database_url: str | None,
    authority: SigningAuthority,
    *,
    web_origin: str,
    rate_limit: int = 1000,
    rate_window_seconds: float = 3600.0,
    audit_per_client_limit: int = 10_000,
    audit_global_limit: int = 10_000,
    jwks_uri: str | None = None,
) -> dict[str, str]:
    """Local/test-mode API settings: real Entra-style validation, no Azure services."""
    settings = {
        "API_ENVIRONMENT": "test",
        "API_LOG_LEVEL": "INFO",
        "API_ENTRA_TENANT_ID": authority.tenant_id,
        "API_ENTRA_AUDIENCE": authority.audience,
        "API_ENTRA_ISSUER": authority.issuer,
        "API_ENTRA_JWKS_URI": jwks_uri or authority.jwks_uri,
        "API_JWT_LEEWAY_SECONDS": "30",
        "API_WEB_ORIGIN": web_origin,
        "API_REQUEST_RATE_LIMIT": str(rate_limit),
        "API_REQUEST_RATE_WINDOW_SECONDS": str(rate_window_seconds),
        "API_AUTH_FAILURE_AUDIT_PER_CLIENT_LIMIT": str(audit_per_client_limit),
        "API_AUTH_FAILURE_AUDIT_GLOBAL_LIMIT": str(audit_global_limit),
        "API_DATABASE_AUTH_MODE": "password",
        "API_DATABASE_CONNECT_TIMEOUT_SECONDS": "2",
        "API_DATABASE_POOL_TIMEOUT_SECONDS": "5",
    }
    if database_url is not None:
        settings["API_DATABASE_URL"] = database_url
    return settings


class WorkerProcess(ManagedProcess):
    """``python -m accelerator.ingestion``, as in the image."""

    def __init__(self, name: str, settings: Mapping[str, str], log_dir: Path) -> None:
        heartbeat = log_dir / f"{name}.heartbeat"
        super().__init__(
            name,
            [sys.executable, "-m", "accelerator.ingestion"],
            child_environment({**settings, "INGESTION_HEARTBEAT_FILE": str(heartbeat)}),
            log_dir,
        )
        self.heartbeat = heartbeat

    def start_and_wait(self, timeout: float = 60.0) -> None:
        self.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.running:
                raise RuntimeError(f"{self.name} exited during start-up:\n{self.log()[-4000:]}")
            if self.heartbeat.exists() and "worker_started" in self.log():
                return
            time.sleep(0.2)
        self.stop()
        raise RuntimeError(f"{self.name} did not start:\n{self.log()[-4000:]}")


# --- Sample documents --------------------------------------------------------------


def pdf_document(objects: list[bytes]) -> bytes:
    """A structurally valid PDF file (header, numbered objects, xref, trailer)."""
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


def _escape(line: str) -> bytes:
    return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("latin-1")


def text_pdf(pages: list[list[str]], *, compress: bool = True) -> bytes:
    """A multi-page Helvetica text PDF, with Flate-compressed content streams."""
    page_count = len(pages)
    font_number = 3 + 2 * page_count
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [%s] /Count %d >>"
        % (b" ".join(b"%d 0 R" % (3 + 2 * index) for index in range(page_count)), page_count),
    ]
    for index, lines in enumerate(pages):
        content = b"BT /F1 11 Tf 14 TL 72 740 Td " + b" ".join(
            b"(%s) Tj T*" % _escape(line) for line in lines
        ) + b" ET"
        stream = zlib.compress(content) if compress else content
        filters = b"/Filter /FlateDecode " if compress else b""
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
            % (font_number, 4 + 2 * index)
        )
        objects.append(
            b"<< /Length %d %s>>\nstream\n%s\nendstream" % (len(stream), filters, stream)
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    return pdf_document(objects)


def decompression_bomb_pdf(expanded_bytes: int = 120_000_000) -> bytes:
    """A small PDF whose single content stream inflates to ``expanded_bytes``."""
    payload = b"BT /F1 11 Tf 72 740 Td (" + b"A" * expanded_bytes + b") Tj ET"
    stream = zlib.compress(payload, 9)
    return pdf_document(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
            b"<< /Length %d /Filter /FlateDecode >>\nstream\n%s\nendstream"
            % (len(stream), stream),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        ]
    )


def cyclic_page_tree_pdf() -> bytes:
    return pdf_document(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [2 0 R] /Count 1 >>",
        ]
    )


def deeply_nested_pdf(depth: int = 20_000) -> bytes:
    return pdf_document(
        [
            b"<< /Type /Catalog /Pages 2 0 R /Nested " + b"[" * depth + b"]" * depth + b" >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>",
        ]
    )


async def eventually(
    description: str,
    check: Callable[[], Awaitable[T | None]],
    *,
    timeout: float,
    interval: float = 0.5,
) -> T:
    """Poll ``check()`` until it returns something other than ``None``."""
    deadline = time.monotonic() + timeout
    while True:
        value = await check()
        if value is not None:
            return value
        if time.monotonic() > deadline:
            raise TimeoutError(f"timed out after {timeout}s waiting for {description}")
        await asyncio.sleep(interval)
