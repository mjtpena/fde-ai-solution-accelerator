"""Composition root: build the deployable API from settings.

This is the only place that turns configuration into concrete adapters. Tests and
alternative hosts keep using ``create_app`` with injected fakes.
"""

import logging
from collections.abc import AsyncGenerator, Awaitable, Callable

from azure.core.credentials_async import AsyncTokenCredential
from azure.identity.aio import DefaultAzureCredential
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from accelerator.agent_core.approvals import ApprovalService
from accelerator.api.app import create_app
from accelerator.api.chat import ChatTurnPort
from accelerator.configuration.settings import Settings
from accelerator.identity.scope_resolver import get_execution_context
from accelerator.infrastructure.approvals import SQLAlchemyApprovalRepository
from accelerator.infrastructure.audit import PostgresAuditRepository
from accelerator.infrastructure.database import create_database_engine
from accelerator.security_core.infrastructure.database import (
    SessionFactory,
    create_session_factory,
)
from accelerator.security_core.infrastructure.memberships import (
    SqlAlchemyScopeMembershipRepository,
)

logger = logging.getLogger(__name__)

ChatTurnFactory = Callable[[Settings], ChatTurnPort | None]


def _no_chat_turn(settings: Settings) -> ChatTurnPort | None:
    del settings
    return None


def build_application(
    settings: Settings,
    *,
    credential: AsyncTokenCredential | None = None,
    chat_turn_factory: ChatTurnFactory = _no_chat_turn,
) -> FastAPI:
    """Build the API with real persistence, scope resolution, audit and cost guards.

    Development and test may run without ``API_DATABASE_URL``; the persistence-backed
    routes then fail closed with 503. Production settings validation guarantees every
    Azure dependency is configured before this runs.
    """
    shutdown: list[Callable[[], Awaitable[None]]] = []
    owned_credential: DefaultAzureCredential | None = None
    if credential is None and settings.database_auth_mode == "managed_identity":
        owned_credential = DefaultAzureCredential()
        credential = owned_credential
        shutdown.append(owned_credential.close)

    session_factory: SessionFactory | None = None
    if settings.database_url is not None:
        engine = create_database_engine(settings, credential=credential)
        session_factory = create_session_factory(engine)
        shutdown.append(engine.dispose)
    else:
        logger.warning(
            "database_unconfigured",
            extra={"environment": settings.environment},
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
        chat_turn=chat_turn_factory(settings),
        session_factory=session_factory,
        on_shutdown=shutdown,
    )
    return app


async def get_approval_service(
    request: Request,
) -> AsyncGenerator[ApprovalService[BaseModel, BaseModel]]:
    """Yield an ``ApprovalService`` bound to one request-scoped database session."""
    session_factory: SessionFactory | None = getattr(request.app.state, "session_factory", None)
    if session_factory is None:
        raise HTTPException(status_code=503, detail="Approval persistence is unavailable.")
    async with session_factory() as session:
        yield ApprovalService(SQLAlchemyApprovalRepository(session))
