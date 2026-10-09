"""The eval-full factory composes the API workflow for a membership-scoped principal."""

import asyncio
from typing import Any, Self

import pytest
from azure.core.credentials import AccessToken
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from accelerator.configuration.settings import Settings
from accelerator.evaluation_core.evaluators.full import FullEvaluationRuntime
from accelerator.infrastructure import evaluation
from accelerator.infrastructure.evaluation import (
    EvaluationPrincipalSettings,
    create_full_evaluation_runtime,
)

TENANT = "00000000-0000-0000-0000-000000000001"
PRINCIPAL = "00000000-0000-0000-0000-0000000000e1"


class FakeCredential:
    instances: list["FakeCredential"] = []

    def __init__(self) -> None:
        self.closed = False
        FakeCredential.instances.append(self)

    async def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        raise AssertionError("Composition must not request tokens")

    async def close(self) -> None:
        self.closed = True

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()


def azure_settings(database_url: str | None) -> Settings:
    return Settings(
        environment="test",
        entra_tenant_id=TENANT,  # type: ignore[arg-type]
        entra_audience="api://test",
        database_url=database_url,  # type: ignore[arg-type]
        database_auth_mode="password",
        foundry_project_endpoint="https://foundry.example.test/api/projects/p",  # type: ignore[arg-type]
        foundry_model_deployment="chat-model",
        foundry_embedding_deployment="embedding-model",
        search_endpoint="https://search.example.test",  # type: ignore[arg-type]
        search_index_name="chunks",
        search_vector_dimensions=3,
    )


def factory(database_url: str | None) -> FullEvaluationRuntime:
    FakeCredential.instances.clear()
    return create_full_evaluation_runtime(
        settings=azure_settings(database_url),
        principal=EvaluationPrincipalSettings(principal_object_id=PRINCIPAL),
        credential_factory=lambda settings: FakeCredential(),  # type: ignore[arg-type,return-value]
    )


async def add_memberships(database_url: str, *pairs: tuple[str, str]) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            for object_id, scope_id in pairs:
                await connection.execute(
                    text("INSERT INTO scope_memberships (object_id, scope_id) VALUES (:o, :s)"),
                    {"o": object_id, "s": scope_id},
                )
    finally:
        await engine.dispose()


def test_runtime_uses_the_principals_memberships_and_captures_evidence(
    migrated_database_url: str,
) -> None:
    asyncio.run(
        add_memberships(
            migrated_database_url,
            (PRINCIPAL, "scope-a"),
            (PRINCIPAL, "scope-c"),
            ("00000000-0000-0000-0000-0000000000ff", "scope-b"),
        )
    )

    runtime = factory(migrated_database_url)

    assert runtime.context.scope_ids == frozenset({"scope-a", "scope-c"})
    assert runtime.context.user_id == PRINCIPAL
    assert runtime.workflow._capture_evaluation_context is True  # type: ignore[attr-defined]
    # The membership lookup's credential is closed before the run starts.
    assert FakeCredential.instances[0].closed
    assert runtime.close is not None
    asyncio.run(runtime.close())
    assert all(credential.closed for credential in FakeCredential.instances)


def test_a_composition_failure_closes_allocated_clients_and_the_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[str] = []

    async def scopes(*args: Any) -> frozenset[str]:
        return frozenset({"scope-a"})

    def failing_build(settings: Any, credential: Any, shutdown: list[Any], **kw: Any) -> Any:
        async def close_client() -> None:
            closed.append("client")

        shutdown.append(close_client)
        raise FileNotFoundError("grounded_answer.md")

    monkeypatch.setattr(evaluation, "resolve_evaluation_scopes", scopes)
    monkeypatch.setattr(evaluation, "build_azure_grounded_answer", failing_build)

    with pytest.raises(FileNotFoundError):
        factory("postgresql+asyncpg://unused/db")

    assert closed == ["client"]
    assert FakeCredential.instances and all(c.closed for c in FakeCredential.instances)


def test_a_principal_without_memberships_cannot_be_evaluated(migrated_database_url: str) -> None:
    with pytest.raises(ValueError, match="no scope memberships"):
        factory(migrated_database_url)


def test_incomplete_azure_or_database_settings_are_rejected() -> None:
    with pytest.raises(ValueError, match="database settings"):
        factory(None)


def test_principal_must_be_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EVALUATION_PRINCIPAL_OBJECT_ID", raising=False)
    with pytest.raises(ValueError):
        EvaluationPrincipalSettings()  # type: ignore[call-arg]
