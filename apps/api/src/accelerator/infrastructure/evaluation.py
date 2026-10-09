"""Trusted composition for ``make eval-full`` against the deployed Azure services.

``create_full_evaluation_runtime`` builds the same grounded-answer workflow the API
serves (Azure AI Search, Foundry, the sufficiency gate and citation validation),
with same-turn evaluation context capture enabled. The execution context belongs to
one configured evaluation principal; its scopes are resolved from the
``scope_memberships`` table exactly as the API resolves a signed-in user's, never
from dataset rows.

Configuration: the usual ``API_*`` settings (Foundry, Search, database, managed
identity) plus ``EVALUATION_PRINCIPAL_OBJECT_ID``. Requires the ``evaluation``
extra (``fde-evaluation-core``), which the API image does not install.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from azure.core.credentials_async import AsyncTokenCredential
from azure.identity.aio import DefaultAzureCredential
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import async_sessionmaker

from accelerator.configuration.settings import Settings
from accelerator.evaluation_core.evaluators.full import FullEvaluationRuntime
from accelerator.infrastructure.database import create_database_engine
from accelerator.infrastructure.grounded_answer import build_azure_grounded_answer
from accelerator.security_core.data_boundaries.context import ExecutionContext
from accelerator.security_core.infrastructure.memberships import (
    SqlAlchemyScopeMembershipRepository,
)

EVALUATION_ROLES = frozenset({"reader"})


class EvaluationPrincipalSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVALUATION_", extra="ignore")

    # The Entra object ID whose scope memberships bound every evaluated turn.
    principal_object_id: str = Field(min_length=1, max_length=36)
    # One context spans the whole run; tool timeouts are capped by this deadline.
    deadline_seconds: float = Field(default=3600.0, gt=0, allow_inf_nan=False)


CredentialFactory = Callable[[Settings], AsyncTokenCredential]


def _default_credential(settings: Settings) -> AsyncTokenCredential:
    return DefaultAzureCredential(managed_identity_client_id=settings.managed_identity_client_id)


async def resolve_evaluation_scopes(
    settings: Settings, principal_object_id: str, credential: AsyncTokenCredential
) -> frozenset[str]:
    """Read the principal's memberships with the API's own repository."""
    engine = create_database_engine(settings, credential=credential)
    try:
        repository = SqlAlchemyScopeMembershipRepository(async_sessionmaker(engine))
        return await repository.scope_ids_for(principal_object_id)
    finally:
        await engine.dispose()


def create_full_evaluation_runtime(
    *,
    settings: Settings | None = None,
    principal: EvaluationPrincipalSettings | None = None,
    credential_factory: CredentialFactory = _default_credential,
) -> FullEvaluationRuntime:
    settings = settings or Settings()  # type: ignore[call-arg]  # read from API_* env
    principal = principal or EvaluationPrincipalSettings()  # type: ignore[call-arg]
    if not settings.azure_services_configured or settings.database_url is None:
        raise ValueError(
            "Full evaluation needs the API's Foundry, Azure AI Search and database settings."
        )

    async def resolve() -> frozenset[str]:
        credential = credential_factory(settings)
        try:
            return await resolve_evaluation_scopes(
                settings, principal.principal_object_id, credential
            )
        finally:
            await credential.close()

    scope_ids = asyncio.run(resolve())
    if not scope_ids:
        raise ValueError("The evaluation principal has no scope memberships.")

    credential = credential_factory(settings)
    # The credential closes last; clients registered after it close first.
    shutdown: list[Callable[[], Awaitable[None]]] = [credential.close]

    async def close() -> None:
        for callback in reversed(shutdown):
            await callback()

    try:
        workflow = build_azure_grounded_answer(
            settings, credential, shutdown, capture_evaluation_context=True
        )
    except BaseException:
        # Composition failed after allocating some clients: release them before raising.
        asyncio.run(close())
        raise

    started = datetime.now(UTC)
    return FullEvaluationRuntime(
        workflow=workflow,
        context=ExecutionContext(
            correlation_id=f"evaluation-full-{started:%Y%m%dT%H%M%SZ}",
            user_id=principal.principal_object_id,
            roles=EVALUATION_ROLES,
            scope_ids=scope_ids,
            deadline_utc=started + timedelta(seconds=principal.deadline_seconds),
        ),
        close=close,
    )
