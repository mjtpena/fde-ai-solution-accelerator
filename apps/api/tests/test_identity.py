import asyncio
import json
from typing import Any
from uuid import UUID

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from accelerator.api.app import create_app
from accelerator.configuration.settings import Settings
from accelerator.identity.authentication import (
    AppRole,
    Principal,
    get_current_principal,
    require_any_role,
)
from accelerator.identity.errors import AuthProviderUnavailable
from accelerator.identity.jwt_validator import EntraTokenValidator

TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
ISSUER = f"https://login.microsoftonline.com/{TENANT_ID}/v2.0"
AUDIENCE = "api://test"


def make_settings() -> Settings:
    return Settings(
        environment="test",
        entra_tenant_id=TENANT_ID,
        entra_audience=AUDIENCE,
    )


def test_missing_token_returns_401() -> None:
    with TestClient(create_app(make_settings())) as client:
        health_response = client.get("/healthz")
        ready_response = client.get("/readyz")

    assert health_response.status_code == 401
    assert health_response.headers["www-authenticate"] == "Bearer"
    assert ready_response.status_code == 401


def test_public_api_documentation_routes_are_disabled() -> None:
    with TestClient(create_app(make_settings())) as client:
        responses = [
            client.get("/docs"),
            client.get("/redoc"),
            client.get("/openapi.json"),
        ]

    assert [response.status_code for response in responses] == [404, 404, 404]


def test_reader_role_can_access_real_health_routes() -> None:
    app = create_app(make_settings())

    async def reader() -> Principal:
        return Principal(subject="reader", roles=frozenset({AppRole.READER}))

    app.dependency_overrides[get_current_principal] = reader
    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_empty_role_set_is_forbidden_on_real_health_routes() -> None:
    app = create_app(make_settings())

    async def unassigned() -> Principal:
        return Principal(subject="unassigned")

    app.dependency_overrides[get_current_principal] = unassigned
    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 403


def test_configured_web_origin_can_preflight_authenticated_requests() -> None:
    with TestClient(create_app(make_settings())) as client:
        response = client.options(
            "/healthz",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_invalid_token_returns_401() -> None:
    app = create_app(make_settings())

    class RejectingValidator:
        async def validate(self, token: str, client: httpx.AsyncClient) -> Principal:
            raise ValueError("invalid token")

    app.state.token_validator = RejectingValidator()
    with TestClient(app) as client:
        response = client.get("/healthz", headers={"Authorization": "Bearer invalid"})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_identity_provider_unavailable_returns_503() -> None:
    app = create_app(make_settings())

    class UnavailableValidator:
        async def validate(self, token: str, client: httpx.AsyncClient) -> Principal:
            raise AuthProviderUnavailable

    app.state.token_validator = UnavailableValidator()
    with TestClient(app) as client:
        response = client.get("/healthz", headers={"Authorization": "Bearer token"})

    assert response.status_code == 503


def test_insufficient_role_returns_403() -> None:
    app = FastAPI()

    @app.get("/admin-check")
    async def admin_check(
        _principal: Principal = Depends(require_any_role(AppRole.ADMIN)),
    ) -> dict[str, str]:
        return {"status": "ok"}

    async def reader() -> Principal:
        return Principal(subject="user", roles=frozenset({AppRole.READER}))

    app.dependency_overrides[get_current_principal] = reader
    with TestClient(app) as client:
        response = client.get("/admin-check")

    assert response.status_code == 403


def test_valid_jwt_maps_supported_app_roles() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_id = "test-key"
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": key_id, "use": "sig", "alg": "RS256"})
    token = encode_token(
        private_key,
        key_id,
        roles=["Reader", "Contributor", "Approver", "Admin", "unknown"],
    )

    async def verify() -> Principal:
        transport = httpx.MockTransport(
            lambda _: httpx.Response(200, json={"keys": [jwk]})
        )
        async with httpx.AsyncClient(transport=transport) as client:
            validator = EntraTokenValidator(make_settings())
            return await validator.validate(token, client)

    principal = asyncio.run(verify())
    assert principal.subject == "test-subject"
    assert principal.object_id == "test-object"
    assert principal.roles == frozenset(AppRole)


@pytest.mark.parametrize(
    ("issuer", "audience"),
    [
        ("https://attacker.example", AUDIENCE),
        (ISSUER, "api://other"),
    ],
)
def test_invalid_issuer_or_audience_is_rejected(
    issuer: str,
    audience: str,
) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_id = "test-key"
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": key_id, "use": "sig", "alg": "RS256"})
    token = encode_token(
        private_key,
        key_id,
        issuer=issuer,
        audience=audience,
    )

    async def verify() -> None:
        transport = httpx.MockTransport(
            lambda _: httpx.Response(200, json={"keys": [jwk]})
        )
        async with httpx.AsyncClient(transport=transport) as client:
            await EntraTokenValidator(make_settings()).validate(token, client)

    with pytest.raises(ValueError):
        asyncio.run(verify())


def test_invalid_signature_is_rejected() -> None:
    trusted_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    untrusted_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_id = "test-key"
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(trusted_key.public_key()))
    jwk.update({"kid": key_id, "use": "sig", "alg": "RS256"})
    token = encode_token(untrusted_key, key_id)

    async def verify() -> None:
        transport = httpx.MockTransport(
            lambda _: httpx.Response(200, json={"keys": [jwk]})
        )
        async with httpx.AsyncClient(transport=transport) as client:
            await EntraTokenValidator(make_settings()).validate(token, client)

    with pytest.raises(ValueError):
        asyncio.run(verify())


def encode_token(
    private_key: rsa.RSAPrivateKey,
    key_id: str,
    *,
    roles: list[str] | None = None,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
) -> str:
    claims: dict[str, Any] = {
        "iss": issuer,
        "aud": audience,
        "sub": "test-subject",
        "oid": "test-object",
        "exp": 4_102_444_800,
    }
    if roles is not None:
        claims["roles"] = roles
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": key_id})
