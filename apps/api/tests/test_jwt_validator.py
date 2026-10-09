"""Entra access-token validation edge cases."""

import json
import time
from collections.abc import Callable
from typing import Any
from uuid import UUID

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from accelerator.configuration.settings import Settings
from accelerator.identity.errors import AuthProviderUnavailable
from accelerator.identity.jwt_validator import EntraTokenValidator

TENANT = UUID("00000000-0000-0000-0000-000000000001")
AUDIENCE = "api://test"
ISSUER = f"https://login.microsoftonline.com/{TENANT}/v2.0"
JWKS_URL = f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys"


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "test",
        "entra_tenant_id": TENANT,
        "entra_audience": AUDIENCE,
    }
    values.update(overrides)
    return Settings.model_validate(values)


def new_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


KEY = new_key()


def jwk(key: rsa.RSAPrivateKey, kid: str) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    value.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return value


def token(
    key: Any = KEY,
    *,
    kid: str | None = "k1",
    algorithm: str = "RS256",
    **claims: Any,
) -> str:
    now = int(time.time())
    body: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "subject",
        "oid": "object",
        "iat": now,
        "nbf": now,
        "exp": now + 600,
    }
    body.update(claims)
    headers = {"kid": kid} if kid is not None else {}
    return jwt.encode(body, key, algorithm=algorithm, headers=headers)


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class JwksServer:
    def __init__(self, *documents: dict[str, Any] | int) -> None:
        self.responses = list(documents)
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        response = self.responses[0] if len(self.responses) == 1 else self.responses.pop(0)
        if isinstance(response, int):
            return httpx.Response(response)
        return httpx.Response(200, json=response)


async def validate(
    raw: str,
    server: JwksServer,
    *,
    validator: EntraTokenValidator | None = None,
    clock: Callable[[], float] | None = None,
) -> Any:
    validator = validator or EntraTokenValidator(settings(), clock=clock or time.monotonic)
    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as client:
        return await validator.validate(raw, client)


async def test_valid_token_is_accepted() -> None:
    principal = await validate(token(), JwksServer({"keys": [jwk(KEY, "k1")]}))

    assert principal.object_id == "object"


@pytest.mark.parametrize(
    ("claims", "accepted"),
    [
        ({"exp": int(time.time()) - 30}, True),  # within the 60 s default leeway
        ({"exp": int(time.time()) - 120}, False),
        ({"nbf": int(time.time()) + 30}, True),
        ({"nbf": int(time.time()) + 120}, False),
    ],
)
async def test_expiry_and_not_before_respect_clock_leeway(
    claims: dict[str, Any], accepted: bool
) -> None:
    server = JwksServer({"keys": [jwk(KEY, "k1")]})
    if accepted:
        assert await validate(token(**claims), server)
    else:
        with pytest.raises(ValueError):
            await validate(token(**claims), server)


async def test_leeway_is_configurable() -> None:
    validator = EntraTokenValidator(settings(jwt_leeway_seconds=0))
    with pytest.raises(ValueError):
        await validate(
            token(exp=int(time.time()) - 5),
            JwksServer({"keys": [jwk(KEY, "k1")]}),
            validator=validator,
        )


async def test_unsigned_alg_none_token_is_rejected() -> None:
    unsigned = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": "s", "exp": int(time.time()) + 600},
        None,  # type: ignore[arg-type]  # an unsigned token is the point
        algorithm="none",
        headers={"kid": "k1"},
    )
    with pytest.raises(ValueError):
        await validate(unsigned, JwksServer({"keys": [jwk(KEY, "k1")]}))


async def test_hs256_signed_with_the_public_key_is_rejected() -> None:
    # Classic key confusion: HMAC "signed" with the RSA public key as the secret.
    public_pem = KEY.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    with pytest.raises(ValueError):
        await validate(_hs256_with_key(public_pem), JwksServer({"keys": [jwk(KEY, "k1")]}))


def _hs256_with_key(secret: bytes) -> str:
    import base64
    import hashlib
    import hmac

    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": "k1"}).encode())
    payload = b64(
        json.dumps(
            {"iss": ISSUER, "aud": AUDIENCE, "sub": "s", "exp": int(time.time()) + 600}
        ).encode()
    )
    signature = hmac.new(secret, f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{b64(signature)}"


async def test_token_without_key_id_is_rejected() -> None:
    with pytest.raises(ValueError):
        await validate(token(kid=None), JwksServer({"keys": [jwk(KEY, "k1")]}))


async def test_rotated_key_is_fetched_once_for_an_unknown_kid() -> None:
    rotated = new_key()
    server = JwksServer(
        {"keys": [jwk(KEY, "k1")]},
        {"keys": [jwk(KEY, "k1"), jwk(rotated, "k2")]},
    )
    validator = EntraTokenValidator(settings())

    await validate(token(), server, validator=validator)
    principal = await validate(token(rotated, kid="k2"), server, validator=validator)

    assert principal.subject == "subject"
    assert len(server.requests) == 2


async def test_unknown_kids_cannot_force_repeated_refreshes() -> None:
    server = JwksServer({"keys": [jwk(KEY, "k1")]})
    validator = EntraTokenValidator(settings())
    await validate(token(), server, validator=validator)

    for attempt in range(3):
        with pytest.raises(ValueError):
            await validate(token(new_key(), kid=f"forged-{attempt}"), server, validator=validator)

    assert len(server.requests) == 2  # initial fetch + one throttled forced refresh


async def test_refresh_failure_serves_stale_keys_until_the_stale_limit() -> None:
    clock = Clock()
    server = JwksServer({"keys": [jwk(KEY, "k1")]}, 503, 503, 503)
    validator = EntraTokenValidator(settings(jwks_max_stale_seconds=3600), clock=clock)
    await validate(token(), server, validator=validator)

    clock.now += 600  # cache expired; the refresh fails
    assert await validate(token(), server, validator=validator)

    clock.now += 3600  # beyond the stale limit
    with pytest.raises(AuthProviderUnavailable):
        await validate(token(), server, validator=validator)


async def test_failed_refresh_without_any_keys_is_unavailable_not_unauthorized() -> None:
    with pytest.raises(AuthProviderUnavailable):
        await validate(token(), JwksServer(500))


async def test_non_rsa_and_malformed_keys_are_skipped_not_fatal() -> None:
    ec_key = ec.generate_private_key(ec.SECP256R1())
    ec_jwk: dict[str, Any] = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(ec_key.public_key()))
    ec_jwk["kid"] = "ec-1"
    document = {
        "keys": [
            ec_jwk,
            {"kty": "RSA", "kid": "broken", "n": "@@", "e": "AQAB"},
            {"kty": "oct", "kid": "symmetric", "k": "c2VjcmV0"},
            jwk(KEY, "k1"),
        ]
    }

    assert await validate(token(), JwksServer(document))


async def test_sovereign_cloud_authority_sets_issuer_and_jwks_url() -> None:
    authority = "https://login.microsoftonline.us"
    sovereign_issuer = f"{authority}/{TENANT}/v2.0"
    server = JwksServer({"keys": [jwk(KEY, "k1")]})
    validator = EntraTokenValidator(settings(entra_authority_host=authority))

    principal = await validate(token(iss=sovereign_issuer), server, validator=validator)

    assert principal.subject == "subject"
    assert server.requests == [f"{authority}/{TENANT}/discovery/v2.0/keys"]
    with pytest.raises(ValueError):
        await validate(token(), server, validator=validator)  # public-cloud issuer


async def test_unknown_kid_during_an_outage_is_unavailable_not_unauthorized() -> None:
    clock = Clock()
    rotated = new_key()
    server = JwksServer({"keys": [jwk(KEY, "k1")]}, 503)
    validator = EntraTokenValidator(settings(), clock=clock)
    await validate(token(), server, validator=validator)

    with pytest.raises(AuthProviderUnavailable):
        await validate(token(rotated, kid="k2"), server, validator=validator)
    assert await validate(token(), server, validator=validator)  # stale k1 still serves


async def test_keys_restricted_from_verification_are_skipped() -> None:
    encrypt_only = jwk(KEY, "k1") | {"key_ops": ["encrypt"]}
    with pytest.raises(AuthProviderUnavailable):
        await validate(token(), JwksServer({"keys": [encrypt_only]}))

    verify = jwk(KEY, "k1") | {"key_ops": ["verify"]}
    assert await validate(token(), JwksServer({"keys": [verify]}))


async def test_jwks_warnings_carry_the_request_correlation_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    validator = EntraTokenValidator(settings())
    document = {"keys": [{"kty": "RSA", "kid": "broken", "n": "@@", "e": "AQAB"}]}
    async with httpx.AsyncClient(transport=httpx.MockTransport(JwksServer(document))) as client:
        with pytest.raises(AuthProviderUnavailable):
            await validator.validate(token(), client, correlation_id="corr-1")

    records = {record.message: record for record in caplog.records}
    assert records["jwks_key_skipped"].correlation_id == "corr-1"
    assert records["jwks_refresh_failed"].correlation_id == "corr-1"
