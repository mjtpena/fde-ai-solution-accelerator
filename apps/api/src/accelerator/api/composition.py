"""Composition root: build the deployable API from settings.

This is the only place that turns configuration into concrete adapters. Tests and
alternative hosts keep using ``create_app`` with injected fakes.
"""

import logging
from collections.abc import Awaitable, Callable

from azure.core.credentials_async import AsyncTokenCredential
from azure.identity.aio import DefaultAzureCredential
from fastapi import FastAPI
from opentelemetry.sdk.trace.export import SpanExporter

from accelerator.agent_core.middleware import ToolCallLimits
from accelerator.agent_core.tools import ToolRegistry
from accelerator.api.app import create_app
from accelerator.api.approvals import get_approval_service
from accelerator.api.chat import ChatTurnPort
from accelerator.api.tool_turns import PolicyEnforcedChatTurn
from accelerator.configuration.settings import Settings
from accelerator.identity.scope_resolver import get_execution_context
from accelerator.infrastructure.audit import PostgresAuditRepository
from accelerator.infrastructure.cost_controls import PostgresRateLimiter
from accelerator.infrastructure.database import create_database_engine
from accelerator.infrastructure.grounded_answer import build_azure_grounded_answer
from accelerator.observability_core import Telemetry
from accelerator.telemetry.traced import TracedChatTurn
from accelerator.telemetry.tracing import build_telemetry
from accelerator.security_core.infrastructure.database import (
    SessionFactory,
    create_session_factory,
)
from accelerator.security_core.infrastructure.memberships import (
    SqlAlchemyScopeMembershipRepository,
)

__all__ = ["build_application", "default_chat_turn", "get_approval_service"]

logger = logging.getLogger(__name__)

ShutdownCallbacks = list[Callable[[], Awaitable[None]]]
ChatTurnFactory = Callable[
    [Settings, AsyncTokenCredential | None, ShutdownCallbacks, Telemetry], ChatTurnPort | None
]


def default_chat_turn(
    settings: Settings,
    credential: AsyncTokenCredential | None,
    shutdown: ShutdownCallbacks,
    telemetry: Telemetry | None = None,
) -> ChatTurnPort | None:
    """The Azure grounded-answer workflow whenever Foundry and Search are configured.

    Production settings validation guarantees they are. Development without them
    gets no workflow, so /chat/stream answers 503 rather than a synthetic reply.
    """
    if not settings.azure_services_configured:
        return None
    if credential is None:
        raise ValueError("The Azure grounded-answer workflow requires an Azure credential.")
    return build_azure_grounded_answer(settings, credential, shutdown, telemetry=telemetry)


def build_application(
    settings: Settings,
    *,
    credential: AsyncTokenCredential | None = None,
    chat_turn_factory: ChatTurnFactory = default_chat_turn,
    tool_registry: ToolRegistry | None = None,
    span_exporter: SpanExporter | None = None,
) -> FastAPI:
    """Build the API with real persistence, scope resolution, audit and cost guards.

    Development and test may run without ``API_DATABASE_URL``; the persistence-backed
    routes then fail closed with 503. Production settings validation guarantees every
    Azure dependency is configured before this runs.
    """
    shutdown: ShutdownCallbacks = []
    telemetry, stop_telemetry = build_telemetry(settings, exporter=span_exporter)
    shutdown.append(stop_telemetry)
    needs_azure = (
        settings.database_auth_mode == "managed_identity" or settings.azure_services_configured
    )
    if credential is None and needs_azure:
        owned_credential = DefaultAzureCredential(
            managed_identity_client_id=settings.managed_identity_client_id
        )
        credential = owned_credential
        shutdown.append(owned_credential.close)

    session_factory: SessionFactory | None = None
    rate_limiter: PostgresRateLimiter | None = None
    if settings.database_url is not None:
        engine = create_database_engine(settings, credential=credential)
        session_factory = create_session_factory(engine)
        shutdown.append(engine.dispose)
        # Shared across replicas; without a database the in-process limiter applies.
        rate_limiter = PostgresRateLimiter(
            session_factory,
            settings.request_rate_limit,
            settings.request_rate_window_seconds,
        )
    else:
        logger.warning(
            "database_unconfigured",
            extra={"environment": settings.environment},
        )

    workflow = chat_turn_factory(settings, credential, shutdown, telemetry)
    chat_turn = (
        PolicyEnforcedChatTurn(
            TracedChatTurn(workflow, telemetry, name="grounded_answer"),
            tool_registry if tool_registry is not None else ToolRegistry(),
            session_factory=session_factory,
            limits=ToolCallLimits(
                max_calls_per_turn=settings.max_tool_calls_per_turn,
                max_calls_per_session=settings.max_tool_calls_per_session,
            ),
        )
        if workflow is not None
        else None
    )
    app = create_app(
        settings,
        audit_repository=(
            PostgresAuditRepository(session_factory) if session_factory is not None else None
        ),
        scope_repository=(
            SqlAlchemyScopeMembershipRepository(session_factory)
            if session_factory is not None
            else None
        ),
        get_execution_context=get_execution_context,
        rate_limiter=rate_limiter,
        chat_turn=chat_turn,
        session_factory=session_factory,
        on_shutdown=shutdown,
        telemetry=telemetry,
    )
    return app
