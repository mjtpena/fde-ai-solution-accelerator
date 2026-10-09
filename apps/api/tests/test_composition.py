from collections.abc import AsyncGenerator
from typing import Any, Self, cast
from unittest.mock import AsyncMock

import pytest
from azure.core.credentials import AccessToken
from fastapi import HTTPException
from sqlalchemy.pool import QueuePool
from starlette.requests import Request

from accelerator.agent_core.approvals import ApprovalService
from accelerator.api.approvals import get_approval_repository, get_approval_service
from accelerator.api.composition import build_application
from accelerator.configuration.settings import Settings
from accelerator.infrastructure.approvals import SQLAlchemyApprovalRepository
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


async def test_approval_persistence_is_unavailable_without_database() -> None:
    app = build_application(settings())
    request = Request({"type": "http", "app": app})

    with pytest.raises(HTTPException) as raised:
        await anext(get_approval_repository(request))

    assert raised.value.status_code == 503


async def test_approval_service_binds_a_request_scoped_session() -> None:
    app = build_application(settings(database_url="postgresql://u:p@localhost/db"))
    request = Request({"type": "http", "app": app})

    repositories: AsyncGenerator[SQLAlchemyApprovalRepository] = get_approval_repository(request)
    repository = await anext(repositories)
    service = await get_approval_service(repository)
    assert isinstance(service, ApprovalService)
    await repositories.aclose()


def test_managed_identity_connections_verify_the_server_certificate() -> None:
    import ssl

    from accelerator.infrastructure.database import verified_tls_context

    context = verified_tls_context()

    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.check_hostname is True


@pytest.mark.parametrize(
    "scheme", ["postgresql+psycopg2", "postgresql+psycopg", "postgresql+pg8000"]
)
def test_non_asyncpg_drivers_are_rejected(scheme: str) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="asyncpg"):
        settings(database_url=f"{scheme}://u:p@localhost/db")


async def test_every_shutdown_callback_runs_even_when_one_fails() -> None:
    app = build_application(settings())
    calls: list[str] = []

    async def failing() -> None:
        calls.append("failing")
        raise RuntimeError("dispose failed")

    async def closing() -> None:
        calls.append("closing")

    app.state.shutdown_callbacks = (closing, failing)
    with pytest.raises(ExceptionGroup) as raised:
        async with app.router.lifespan_context(app):
            pass

    assert calls == ["failing", "closing"]
    assert [str(error) for error in raised.value.exceptions] == ["dispose failed"]


def test_importing_the_entry_point_builds_nothing() -> None:
    import importlib
    import sys

    sys.modules.pop("accelerator.api.main", None)
    main = importlib.import_module("accelerator.api.main")

    assert callable(main.create_application)
    assert not hasattr(main, "app")


def azure_settings(**overrides: Any) -> Settings:
    return settings(
        foundry_project_endpoint="https://foundry.example.test/api/projects/p",
        foundry_model_deployment="chat-model",
        foundry_embedding_deployment="embedding-model",
        search_endpoint="https://search.example.test",
        search_index_name="chunks",
        search_vector_dimensions=3,
        **overrides,
    )


def test_development_without_azure_services_has_no_chat_workflow() -> None:
    app = build_application(settings())

    assert not hasattr(app.state, "chat_turn")


def test_configured_azure_services_wire_the_grounded_answer_workflow() -> None:
    from accelerator.agent_core.workflows.grounded_answer import GroundedAnswerWorkflow

    credential = FakeCredential()
    app = build_application(azure_settings(), credential=credential)

    from accelerator.api.tool_turns import PolicyEnforcedChatTurn

    assert isinstance(app.state.chat_turn, PolicyEnforcedChatTurn)
    assert isinstance(app.state.chat_turn._workflow._inner, GroundedAnswerWorkflow)
    # Telemetry and the Search, embedding and Foundry chat clients close with the app.
    assert len(app.state.shutdown_callbacks) == 4


def test_azure_workflow_refuses_to_build_without_a_credential() -> None:
    from accelerator.api.composition import default_chat_turn

    with pytest.raises(ValueError, match="credential"):
        default_chat_turn(azure_settings(), None, [])
