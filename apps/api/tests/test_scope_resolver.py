import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import create_async_engine

from accelerator.api.app import create_app
from accelerator.configuration.settings import Settings
from accelerator.identity.authentication import AppRole, Principal, get_current_principal
from accelerator.identity.scope_resolver import configure_scope_resolver, get_execution_context
from accelerator.security_core.data_boundaries.context import ExecutionContext
from accelerator.security_core.infrastructure.database import Base, create_session_factory
from accelerator.security_core.infrastructure.memberships import (
    ScopeMembership,
    SqlAlchemyScopeMembershipRepository,
)

TENANT = UUID("00000000-0000-0000-0000-000000000001")
CALLER = "00000000-0000-0000-0000-000000000002"
OTHER = "00000000-0000-0000-0000-000000000003"
CORRELATION = str(uuid4())


@pytest.fixture
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(
    key: rsa.RSAPrivateKey, object_id: str | None = CALLER, *, forged: bool = False
) -> str:
    return jwt.encode(
        {
            "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
            "aud": "api://test",
            "sub": "subject-not-object-id",
            "oid": object_id,
            "roles": ["Admin"],
            "scope_ids": ["scope-private"],
            "exp": 4_102_444_800,
        },
        rsa.generate_private_key(public_exponent=65537, key_size=2048) if forged else key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )


@pytest.fixture
async def client(signing_key: rsa.RSAPrivateKey) -> AsyncIterator[httpx.AsyncClient]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    app = create_app(
        Settings(environment="test", entra_tenant_id=TENANT, entra_audience="api://test")
    )
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
    jwk["kid"] = "test-key"
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = create_session_factory(engine)
        async with factory.begin() as session:
            session.add_all(
                [
                    ScopeMembership(object_id=CALLER, scope_id="scope-a"),
                    ScopeMembership(object_id=OTHER, scope_id="scope-private"),
                ]
            )
        configure_scope_resolver(app, SqlAlchemyScopeMembershipRepository(factory))

        @app.get("/identity/context")
        async def context_test(
            context: ExecutionContext = Depends(get_execution_context),
        ) -> ExecutionContext:
            return context

        @app.post("/scope-test")
        async def scope_test(
            context: ExecutionContext = Depends(get_execution_context),
        ) -> ExecutionContext:
            return context

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"keys": [jwk]}))
        ) as identity_client:
            app.state.http_client = identity_client
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as test_client:
                yield test_client
    finally:
        await engine.dispose()


async def test_signed_token_and_database_resolve_context(
    client: httpx.AsyncClient, signing_key: rsa.RSAPrivateKey
) -> None:
    response = await client.get(
        "/identity/context",
        headers={"Authorization": f"Bearer {token(signing_key)}", "X-Correlation-ID": CORRELATION},
    )
    assert response.status_code == 200
    context = ExecutionContext.model_validate(response.json())
    assert context.user_id == CALLER
    assert context.roles == frozenset({"Admin"})
    assert context.scope_ids == frozenset({"scope-a"})
    assert context.correlation_id == response.headers["x-correlation-id"] == CORRELATION
    assert context.session_id is None
    assert 0 < (context.deadline_utc - datetime.now(UTC)).total_seconds() <= 30


@pytest.mark.parametrize(
    "payload",
    [
        {"scope_ids": ["scope-private"]},
        {"object_id": OTHER, "user_id": OTHER, "roles": ["Admin"]},
        {"execution_context": {"scope_ids": ["scope-private"]}, "session_id": "forged"},
    ],
)
async def test_body_and_query_cannot_widen_scopes(
    client: httpx.AsyncClient, signing_key: rsa.RSAPrivateKey, payload: dict[str, object]
) -> None:
    response = await client.post(
        "/scope-test?scope_ids=scope-private&scope_ids=scope-a&object_id=" + OTHER,
        json=payload,
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )
    assert response.status_code == 200
    context = ExecutionContext.model_validate(response.json())
    assert context.scope_ids == frozenset({"scope-a"})
    assert context.user_id == CALLER
    assert context.session_id is None


async def test_unknown_member_cannot_claim_scopes_in_token_or_query(
    client: httpx.AsyncClient, signing_key: rsa.RSAPrivateKey
) -> None:
    response = await client.get(
        "/identity/context?scope_ids=scope-private",
        headers={"Authorization": f"Bearer {token(signing_key, str(uuid4()))}"},
    )
    assert response.status_code == 200
    assert response.json()["scope_ids"] == []


async def test_missing_oid_is_denied_even_with_body_oid(
    client: httpx.AsyncClient, signing_key: rsa.RSAPrivateKey
) -> None:
    response = await client.post(
        "/scope-test",
        json={"object_id": CALLER},
        headers={"Authorization": f"Bearer {token(signing_key, None)}"},
    )
    assert response.status_code == 403


async def test_forged_token_is_denied(
    client: httpx.AsyncClient, signing_key: rsa.RSAPrivateKey
) -> None:
    response = await client.get(
        "/identity/context",
        headers={"Authorization": f"Bearer {token(signing_key, OTHER, forged=True)}"},
    )
    assert response.status_code == 401
    UUID(response.headers["x-correlation-id"])


async def test_missing_token_has_generated_correlation(client: httpx.AsyncClient) -> None:
    response = await client.get("/identity/context")
    assert response.status_code == 401
    UUID(response.headers["x-correlation-id"])


async def test_invalid_correlation_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.get("/identity/context", headers={"X-Correlation-ID": "invalid"})
    assert response.status_code == 400
    UUID(response.headers["x-correlation-id"])


@pytest.mark.parametrize("configured", [False, True])
async def test_unavailable_repository_fails_closed_with_correlated_log(
    configured: bool, caplog: pytest.LogCaptureFixture
) -> None:
    app = create_app(
        Settings(environment="test", entra_tenant_id=TENANT, entra_audience="api://test")
    )
    @app.get("/identity/context")
    async def context_test(
        context: ExecutionContext = Depends(get_execution_context),
    ) -> ExecutionContext:
        return context

    async def principal() -> Principal:
        return Principal(subject="subject", object_id=CALLER, roles=frozenset({AppRole.READER}))

    app.dependency_overrides[get_current_principal] = principal
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        if configured:
            # No schema: exercise a real SQLAlchemy failure through the HTTP boundary.
            configure_scope_resolver(
                app, SqlAlchemyScopeMembershipRepository(create_session_factory(engine))
            )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as test_client:
            response = await test_client.get(
                "/identity/context", headers={"X-Correlation-ID": CORRELATION}
            )
    finally:
        await engine.dispose()
    assert response.status_code == 503
    assert response.headers["x-correlation-id"] == CORRELATION
    assert any(
        getattr(record, "correlation_id", None) == CORRELATION for record in caplog.records
    )


def test_scope_context_is_not_exposed_as_a_production_route() -> None:
    app = create_app(
        Settings(environment="test", entra_tenant_id=TENANT, entra_audience="api://test")
    )
    assert "/identity/context" not in app.openapi()["paths"]


async def test_context_is_cached_for_shared_route_dependencies() -> None:
    app = create_app(
        Settings(environment="test", entra_tenant_id=TENANT, entra_audience="api://test")
    )
    lookups: list[str] = []

    class RecordingRepository:
        async def scope_ids_for(self, object_id: str) -> frozenset[str]:
            lookups.append(object_id)
            return frozenset({"scope-a"})

    configure_scope_resolver(app, RecordingRepository())

    async def principal() -> Principal:
        return Principal(subject="subject", object_id=CALLER, roles=frozenset({AppRole.READER}))

    app.dependency_overrides[get_current_principal] = principal

    async def dependent_guard(
        context: ExecutionContext = Depends(get_execution_context),
    ) -> ExecutionContext:
        return context

    @app.get("/shared-context")
    async def shared_context(
        guarded_context: ExecutionContext = Depends(dependent_guard),
        context: ExecutionContext = Depends(get_execution_context),
    ) -> ExecutionContext:
        assert guarded_context is context
        return context

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as test_client:
        response = await test_client.get("/shared-context")
    assert response.status_code == 200
    assert lookups == [CALLER]


@pytest.mark.parametrize("incoming", [None, CORRELATION])
async def test_unhandled_server_error_preserves_correlation(
    incoming: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    app = create_app(
        Settings(environment="test", entra_tenant_id=TENANT, entra_audience="api://test")
    )
    observed_ids: list[str] = []

    class FailingRepository:
        async def scope_ids_for(self, object_id: str) -> frozenset[str]:
            raise RuntimeError("untrusted-sensitive-error-content")

    configure_scope_resolver(app, FailingRepository())

    async def principal() -> Principal:
        return Principal(subject="subject", object_id=CALLER, roles=frozenset({AppRole.READER}))

    app.dependency_overrides[get_current_principal] = principal

    @app.get("/unexpected-error")
    async def unexpected_error(
        request: Request,
    ) -> ExecutionContext:
        observed_ids.append(request.state.correlation_id)
        return await get_execution_context(request, await principal())

    headers = {} if incoming is None else {"X-Correlation-ID": incoming}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as test_client:
        response = await test_client.get("/unexpected-error", headers=headers)

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error."}
    correlation_id = response.headers["x-correlation-id"]
    UUID(correlation_id)
    assert observed_ids == [correlation_id]
    if incoming is not None:
        assert correlation_id == incoming
    assert any(
        record.message == "unexpected_request_failure"
        and getattr(record, "correlation_id", None) == correlation_id
        and getattr(record, "exception_type", None) == "RuntimeError"
        for record in caplog.records
    )
    assert "untrusted-sensitive-error-content" not in response.text
    assert "untrusted-sensitive-error-content" not in caplog.text

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=True),
        base_url="http://test",
    ) as test_client:
        with pytest.raises(RuntimeError, match="untrusted-sensitive-error-content"):
            await test_client.get("/unexpected-error", headers=headers)
