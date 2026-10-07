import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from accelerator.identity.authentication import Principal, get_current_principal
from accelerator.security_core.authorisation.memberships import ScopeMembershipRepository
from accelerator.security_core.data_boundaries.context import ExecutionContext

logger = logging.getLogger(__name__)


class ScopeResolverSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="API_SCOPE_")

    deadline_seconds: float = Field(default=30.0, gt=0, allow_inf_nan=False)


class ScopeResolver:
    def __init__(
        self, repository: ScopeMembershipRepository, settings: ScopeResolverSettings
    ) -> None:
        self._repository = repository
        self._settings = settings

    async def resolve(self, principal: Principal, correlation_id: str) -> ExecutionContext:
        if not principal.object_id:
            raise HTTPException(status_code=403, detail="An Entra object ID is required.")
        deadline = datetime.now(UTC) + timedelta(seconds=self._settings.deadline_seconds)
        scopes = await self._repository.scope_ids_for(principal.object_id)
        context = ExecutionContext(
            correlation_id=correlation_id,
            user_id=principal.object_id,
            roles=frozenset(role.value for role in principal.roles),
            scope_ids=scopes,
            deadline_utc=deadline,
        )
        logger.info("scope_resolved", extra={"correlation_id": correlation_id})
        return context


def configure_scope_resolver(
    app: FastAPI,
    repository: ScopeMembershipRepository,
    settings: ScopeResolverSettings | None = None,
) -> None:
    app.state.scope_resolver = ScopeResolver(repository, settings or ScopeResolverSettings())


class CorrelationIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        correlation_id = str(uuid4())
        request = Request(scope)
        incoming = request.headers.get("x-correlation-id")
        if incoming is not None:
            try:
                correlation_id = str(UUID(incoming))
            except ValueError:
                response = JSONResponse(
                    {"detail": "X-Correlation-ID must be a UUID."},
                    status_code=400,
                    headers={"X-Correlation-ID": correlation_id},
                )
                await response(scope, receive, send)
                return
        request.state.correlation_id = correlation_id

        async def send_with_correlation(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (name, value)
                    for name, value in message["headers"]
                    if name.lower() != b"x-correlation-id"
                ]
                headers.append((b"x-correlation-id", correlation_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_correlation)


async def get_execution_context(
    request: Request,
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ExecutionContext:
    correlation_id: str = request.state.correlation_id
    resolver = getattr(request.app.state, "scope_resolver", None)
    if not isinstance(resolver, ScopeResolver):
        logger.error("scope_repository_unconfigured", extra={"correlation_id": correlation_id})
        raise HTTPException(status_code=503, detail="Scope resolver is unavailable.")
    try:
        context = await resolver.resolve(principal, correlation_id)
    except SQLAlchemyError as exc:
        logger.error("scope_resolution_failed", extra={"correlation_id": correlation_id})
        raise HTTPException(status_code=503, detail="Scope resolver is unavailable.") from exc
    request.state.execution_context = context
    return context


def install_scope_boundary(app: FastAPI) -> None:
    app.add_middleware(CorrelationIdMiddleware)
