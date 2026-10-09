from collections.abc import Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from accelerator.api.audit import AuthFailureAuditMiddleware, router as audit_router
from accelerator.api.chat import ChatTurnPort, router as chat_router
from accelerator.api.cost_guard import (
    ContextDependency,
    create_cost_guard_dependency,
    handle_token_budget_exceeded,
)
from accelerator.api.health import router as health_router
from accelerator.api.retrieval_diagnostics import (
    InMemoryRetrievalDiagnosticsStore,
    RetrievalDiagnosticsStore,
    router as retrieval_diagnostics_router,
)
from accelerator.configuration.settings import Settings
from accelerator.domain.audit import AuditRepository
from accelerator.identity.authentication import AppRole, require_any_role
from accelerator.identity.jwt_validator import EntraTokenValidator
from accelerator.identity.scope_resolver import (
    configure_scope_resolver,
    install_scope_boundary,
)
from accelerator.security_core.authorisation.memberships import ScopeMembershipRepository
from accelerator.security_core.cost_guard import RateLimiter, TokenBudgetExceeded
from accelerator.security_core.infrastructure.database import SessionFactory


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            app.state.http_client = client
            yield
    finally:
        for callback in reversed(app.state.shutdown_callbacks):
            await callback()


def create_app(
    settings: Settings,
    diagnostics_store: RetrievalDiagnosticsStore | None = None,
    *,
    audit_repository: AuditRepository | None = None,
    get_execution_context: ContextDependency | None = None,
    rate_limiter: RateLimiter | None = None,
    chat_turn: ChatTurnPort | None = None,
    scope_repository: ScopeMembershipRepository | None = None,
    session_factory: SessionFactory | None = None,
    on_shutdown: Sequence[Callable[[], Awaitable[None]]] = (),
) -> FastAPI:
    app = FastAPI(
        title="FDE AI Solution Accelerator API",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
        dependencies=[Depends(require_any_role(*AppRole))],
        responses={
            401: {"description": "Missing or invalid bearer token."},
            403: {"description": "Insufficient app role."},
            503: {"description": "Identity, scope, or audit persistence is unavailable."},
        },
    )
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.shutdown_callbacks = tuple(on_shutdown)
    app.state.retrieval_diagnostics_store = (
        diagnostics_store
        if diagnostics_store is not None
        else InMemoryRetrievalDiagnosticsStore()
    )
    app.add_exception_handler(TokenBudgetExceeded, handle_token_budget_exceeded)
    if get_execution_context is not None:
        app.state.request_cost_guard = create_cost_guard_dependency(
            settings,
            get_execution_context,
            rate_limiter=rate_limiter,
        )
    app.state.token_validator = EntraTokenValidator(settings)
    app.state.audit_repository = audit_repository
    app.add_middleware(AuthFailureAuditMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.web_origin],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    app.include_router(retrieval_diagnostics_router)
    app.include_router(audit_router)
    if chat_turn is not None:
        app.state.chat_turn = chat_turn
    if scope_repository is not None:
        configure_scope_resolver(app, scope_repository)
    app.include_router(chat_router)
    install_scope_boundary(app)
    return app
