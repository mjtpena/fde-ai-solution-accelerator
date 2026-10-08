import logging
from typing import Annotated, cast

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from accelerator.application.audit import AuditRecorder
from accelerator.domain.audit import AuditPage, AuditRepository, EventType
from accelerator.identity.scope_resolver import get_execution_context
from accelerator.security_core.data_boundaries.context import ExecutionContext
from accelerator.security_core.infrastructure.database import SessionFactory
from accelerator.infrastructure.audit import PostgresAuditRepository


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/audit-events", tags=["audit"])


def configure_audit(app: FastAPI, session_factory: SessionFactory) -> None:
    app.state.audit_repository = PostgresAuditRepository(session_factory)


def get_audit_repository(request: Request) -> AuditRepository:
    repository = getattr(request.app.state, "audit_repository", None)
    if repository is None:
        logger.error(
            "audit_repository_unconfigured",
            extra={"correlation_id": request.state.correlation_id},
        )
        raise HTTPException(status_code=503, detail="Audit persistence is unavailable.")
    return cast(AuditRepository, repository)


def get_audit_recorder(
    repository: Annotated[AuditRepository, Depends(get_audit_repository)],
) -> AuditRecorder:
    return AuditRecorder(repository)


async def require_audit_admin(
    context: Annotated[ExecutionContext, Depends(get_execution_context)],
) -> ExecutionContext:
    if "Admin" not in context.roles:
        raise HTTPException(status_code=403, detail="Admin role required")
    return context


@router.get("", response_model=AuditPage)
async def query_audit_events(
    context: Annotated[ExecutionContext, Depends(require_audit_admin)],
    repository: Annotated[AuditRepository, Depends(get_audit_repository)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
    event_type: EventType | None = None,
) -> AuditPage:
    try:
        return await repository.query(limit=limit, offset=offset, event_type=event_type)
    except SQLAlchemyError as exc:
        logger.error(
            "audit_query_failed",
            extra={"correlation_id": context.correlation_id},
        )
        raise HTTPException(status_code=503, detail="Audit persistence is unavailable.") from exc


class AuthFailureAuditMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        correlation_id: str = request.state.correlation_id
        response_replaced = False

        async def audited_send(message: Message) -> None:
            nonlocal response_replaced
            if response_replaced:
                return
            if message["type"] == "http.response.start" and message["status"] == 401:
                try:
                    recorder = AuditRecorder(get_audit_repository(request))
                    await recorder.auth_failure(correlation_id)
                except HTTPException as exc:
                    response_replaced = True
                    response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
                    await response(scope, receive, send)
                    return
                except SQLAlchemyError:
                    response_replaced = True
                    logger.error(
                        "auth_failure_audit_failed",
                        extra={"correlation_id": correlation_id},
                    )
                    response = JSONResponse(
                        {"detail": "Audit persistence is unavailable."}, status_code=503
                    )
                    await response(scope, receive, send)
                    return
            await send(message)

        await self.app(scope, receive, audited_send)
