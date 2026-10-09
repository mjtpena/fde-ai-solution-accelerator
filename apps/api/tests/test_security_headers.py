from unittest.mock import AsyncMock
from uuid import UUID

import httpx

from accelerator.api.app import create_app
from accelerator.configuration.settings import Settings
from accelerator.domain.audit import AuditRepository


def app_settings() -> Settings:
    return Settings(
        environment="test",
        entra_tenant_id=UUID("00000000-0000-0000-0000-000000000001"),
        entra_audience="api://test",
        web_origin="https://app.example.test",
    )


async def request(method: str, path: str, **kwargs: object) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=create_app(app_settings(), audit_repository=AsyncMock(spec=AuditRepository))
        ),
        base_url="https://api.test",
    ) as client:
        return await client.request(method, path, **kwargs)  # type: ignore[arg-type]


async def test_json_responses_carry_security_headers_and_are_not_cached() -> None:
    response = await request("GET", "/healthz")

    assert response.headers["strict-transport-security"].startswith("max-age=31536000")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["content-security-policy"] == (
        "default-src 'none'; frame-ancestors 'none'"
    )
    assert response.headers["cache-control"] == "no-store"


async def test_error_responses_carry_security_headers_too() -> None:
    response = await request("GET", "/audit-events")

    assert response.status_code == 401
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_cors_allows_only_explicit_methods_and_headers() -> None:
    allowed = await request(
        "OPTIONS",
        "/chat/stream",
        headers={
            "Origin": "https://app.example.test",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    wrong_method = await request(
        "OPTIONS",
        "/chat/stream",
        headers={"Origin": "https://app.example.test", "Access-Control-Request-Method": "DELETE"},
    )
    wrong_header = await request(
        "OPTIONS",
        "/chat/stream",
        headers={
            "Origin": "https://app.example.test",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-scope-id",
        },
    )
    other_origin = await request(
        "OPTIONS",
        "/chat/stream",
        headers={"Origin": "https://evil.example.test", "Access-Control-Request-Method": "POST"},
    )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://app.example.test"
    assert wrong_method.status_code == 400
    assert wrong_header.status_code == 400
    assert other_origin.status_code == 400


async def test_invalid_correlation_id_rejection_carries_security_headers() -> None:
    response = await request("GET", "/healthz", headers={"X-Correlation-ID": "not-a-uuid"})

    assert response.status_code == 400
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


async def test_unhandled_server_errors_carry_security_headers() -> None:
    app = create_app(app_settings(), audit_repository=AsyncMock(spec=AuditRepository))

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("unexpected")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="https://api.test",
    ) as client:
        response = await client.get("/boom")

    assert response.status_code == 500
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == (
        "default-src 'none'; frame-ancestors 'none'"
    )
    assert response.headers["cache-control"] == "no-store"
    assert "x-correlation-id" in response.headers
