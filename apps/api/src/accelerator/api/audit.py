import logging
from dataclasses import dataclass
from threading import Lock
from typing import Annotated, cast

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from accelerator.application.audit import AuditRecorder
from accelerator.domain.audit import AuditPage, AuditRepository, EventType
from accelerator.identity.scope_resolver import get_execution_context
from accelerator.infrastructure.audit import PostgresAuditRepository
from accelerator.security_core.cost_guard import RateLimitExceeded, SlidingWindowRateLimiter
from accelerator.security_core.data_boundaries.context import ExecutionContext
from accelerator.security_core.infrastructure.database import SessionFactory

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


@dataclass(slots=True)
class _ThrottleKey:
    correlation_id: str
    user_id: str
    scope_ids: frozenset[str] = frozenset()


class AuthFailureAuditThrottle:
    """Bound audit writes caused by unauthenticated traffic.

    Each 401 normally writes one audit row, so a token-less flood would turn into
    a database write flood. Inserts are capped per client address and globally per
    window; anything above the caps is counted and reported in a structured log
    line instead of a row, and the client still receives its 401.
    """

    def __init__(self, *, per_client_limit: int, global_limit: int, window_seconds: float) -> None:
        self._per_client = SlidingWindowRateLimiter(per_client_limit, window_seconds)
        self._global = SlidingWindowRateLimiter(global_limit, window_seconds)
        self._suppressed = 0
        self._lock = Lock()

    def allow(self, client_host: str | None) -> bool:
        try:
            # Global first: once the global cap is hit, unseen clients allocate no
            # per-client state, so high-cardinality floods stay bounded in memory.
            self._global.check(_ThrottleKey("", "all-unauthenticated"))
            self._per_client.check(_ThrottleKey("", f"client:{client_host or 'unknown'}"))
        except RateLimitExceeded:
            with self._lock:
                self._suppressed += 1
            return False
        return True

    def take_suppressed_count(self) -> int:
        with self._lock:
            count, self._suppressed = self._suppressed, 0
        return count


class AuthFailureAuditMiddleware:
    """Record 401 (authentication) and 403 (authorization) responses before they are sent.

    Recording is throttled per client and globally: past the throttle, a denial is
    sent without an event and only counted in a later ``auth_failure_audit_suppressed``
    log. If the audit write fails the response becomes a 503: an unaudited denial is
    never reported as an ordinary one. Request headers, paths, bodies and query
    strings are never stored.
    """

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
            if message["type"] == "http.response.start" and message["status"] in (401, 403):
                throttle: AuthFailureAuditThrottle | None = getattr(
                    request.app.state, "auth_failure_audit_throttle", None
                )
                client_host = scope["client"][0] if scope.get("client") else None
                if throttle is not None and not throttle.allow(client_host):
                    await send(message)
                    return
                if throttle is not None and (suppressed := throttle.take_suppressed_count()):
                    logger.warning(
                        "auth_failure_audit_suppressed",
                        extra={"correlation_id": correlation_id, "suppressed_count": suppressed},
                    )
                try:
                    recorder = AuditRecorder(get_audit_repository(request))
                    if message["status"] == 401:
                        await recorder.auth_failure(correlation_id)
                    else:
                        await recorder.authorization_failure(
                            correlation_id,
                            getattr(request.state, "principal_object_id", None),
                        )
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
