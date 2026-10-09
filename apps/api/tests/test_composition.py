from collections.abc import AsyncGenerator
from typing import Any, Self, cast
from unittest.mock import AsyncMock

import pytest
from azure.core.credentials import AccessToken
from fastapi import HTTPException
from sqlalchemy.pool import QueuePool
from starlette.requests import Request

from accelerator.agent_core.approvals import ApprovalService
from accelerator.api.composition import build_application, get_approval_service
from accelerator.configuration.settings import Settings
from accelerator.infrastructure.audit import PostgresAuditRepository
from accelerator.infrastructure.database import (
    POSTGRES_ENTRA_SCOPE,
    asyncpg_url,
    create_database_engine,
    entra_password_provider,
)

TENANT = "00000000-0000-0000-0000-000000000001"


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "development",
        "entra_tenant_id": TENANT,
        "entra_audience": "api://test",
    }
    values.update(overrides)
    return Settings.model_validate(values)


class FakeCredential:
    def __init__(self) -> None:
        self.scopes: list[tuple[str, ...]] = []
        self.closed = False

    async def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        del kwargs
        self.scopes.append(scopes)
        return AccessToken("entra-token", 4_102_444_800)

    async def close(self) -> None:
        self.closed = True

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()


def test_without_database_persistence_routes_stay_fail_closed() -> None:
    app = build_application(settings())

    assert app.state.session_factory is None
    assert app.state.audit_repository is None
    assert not hasattr(app.state, "scope_resolver")


async def test_with_database_wires_repositories_and_disposes_engine_on_shutdown() -> None:
    app = build_application(
        settings(database_url="postgresql://accelerator:pw@localhost:5432/accelerator")
    )

    assert app.state.session_factory is not None
    assert isinstance(app.state.audit_repository, PostgresAuditRepository)
    assert app.state.scope_resolver is not None
    assert app.state.request_cost_guard is not None
    engine = app.state.session_factory.kw["bind"]
    assert engine.url.drivername == "postgresql+asyncpg"
    assert cast(QueuePool, engine.pool).size() == 5

    dispose = AsyncMock(wraps=engine.dispose)
    app.state.shutdown_callbacks = (dispose,)
    async with app.router.lifespan_context(app):
        pass
    dispose.assert_awaited_once()


def test_engine_uses_explicit_pool_settings() -> None:
    engine = create_database_engine(
        settings(
            database_url="postgresql+asyncpg://u:p@localhost/db",
            database_pool_size=3,
            database_max_overflow=2,
            database_pool_timeout_seconds=4,
        )
    )

    pool = cast(QueuePool, engine.pool)
    assert pool.size() == 3
    assert pool._max_overflow == 2
    assert pool._timeout == 4
    assert pool._pre_ping is True


def test_managed_identity_requires_a_credential() -> None:
    with pytest.raises(ValueError, match="credential"):
        create_database_engine(
            settings(
                database_url="postgresql+asyncpg://identity@localhost/db",
                database_auth_mode="managed_identity",
            )
        )


async def test_managed_identity_password_is_a_fresh_entra_token() -> None:
    credential = FakeCredential()
    password = entra_password_provider(credential)

    assert await password() == "entra-token"
    assert await password() == "entra-token"
    assert credential.scopes == [(POSTGRES_ENTRA_SCOPE,), (POSTGRES_ENTRA_SCOPE,)]


def test_managed_identity_app_uses_injected_credential() -> None:
    credential = FakeCredential()
    app = build_application(
        settings(
            database_url="postgresql://identity@localhost/db",
            database_auth_mode="managed_identity",
        ),
        credential=credential,
    )

    assert app.state.session_factory is not None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql://u@h/db", "postgresql+asyncpg://u@h/db"),
        ("postgres://u@h/db", "postgresql+asyncpg://u@h/db"),
        ("postgresql+asyncpg://u@h/db", "postgresql+asyncpg://u@h/db"),
    ],
)
def test_asyncpg_driver_is_pinned(url: str, expected: str) -> None:
    assert asyncpg_url(url) == expected


async def test_approval_service_is_unavailable_without_database() -> None:
    app = build_application(settings())
    request = Request({"type": "http", "app": app})

    with pytest.raises(HTTPException) as raised:
        await anext(get_approval_service(request))

    assert raised.value.status_code == 503


async def test_approval_service_binds_a_request_scoped_session() -> None:
    app = build_application(settings(database_url="postgresql://u:p@localhost/db"))
    request = Request({"type": "http", "app": app})

    dependency: AsyncGenerator[ApprovalService[Any, Any]] = get_approval_service(request)
    service = await anext(dependency)
    assert isinstance(service, ApprovalService)
    await dependency.aclose()
