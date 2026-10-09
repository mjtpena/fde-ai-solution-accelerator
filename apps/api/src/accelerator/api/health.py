"""Unauthenticated liveness and readiness probes.

Both routes are exempt from the app-role dependency so platform probes need no
token. Readiness reports only fixed check names and outcomes; it never returns
exception text, hostnames or configuration values.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

from accelerator.api.chat import ChatTurnPort
from accelerator.identity.errors import AuthProviderUnavailable
from accelerator.security_core.infrastructure.database import SessionFactory

logger = logging.getLogger(__name__)

READINESS_CHECK_TIMEOUT_SECONDS = 3.0

CheckStatus = Literal["ok", "failed", "not_configured"]


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ReadinessCheck(BaseModel):
    status: CheckStatus
    reason: str | None = None


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, ReadinessCheck]


router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    return HealthResponse(status="ok")


async def _check_database(request: Request) -> ReadinessCheck:
    session_factory: SessionFactory | None = getattr(request.app.state, "session_factory", None)
    if session_factory is None:
        return ReadinessCheck(status="not_configured", reason="API_DATABASE_URL is not set.")
    async with session_factory() as session:
        await session.execute(text("SELECT 1"))
    return ReadinessCheck(status="ok")


async def _check_identity_provider(request: Request) -> ReadinessCheck:
    validator = request.app.state.token_validator
    try:
        await validator.ensure_signing_keys(
            request.app.state.http_client,
            correlation_id=getattr(request.state, "correlation_id", None),
        )
    except AuthProviderUnavailable:
        return ReadinessCheck(status="failed", reason="Signing keys are unavailable.")
    return ReadinessCheck(status="ok")


async def _check_chat_workflow(request: Request) -> ReadinessCheck:
    if not isinstance(getattr(request.app.state, "chat_turn", None), ChatTurnPort):
        return ReadinessCheck(status="not_configured", reason="No chat workflow is configured.")
    return ReadinessCheck(status="ok")


READINESS_CHECKS: dict[str, Callable[[Request], Awaitable[ReadinessCheck]]] = {
    "database": _check_database,
    "identity_provider": _check_identity_provider,
    "chat_workflow": _check_chat_workflow,
}


async def _run_check(
    name: str, check: Callable[[Request], Awaitable[ReadinessCheck]], request: Request
) -> ReadinessCheck:
    try:
        async with asyncio.timeout(READINESS_CHECK_TIMEOUT_SECONDS):
            return await check(request)
    except TimeoutError:
        result = ReadinessCheck(status="failed", reason="Check timed out.")
    except Exception as exc:  # readiness must report, not raise, any dependency failure
        logger.warning(
            "readiness_check_failed",
            extra={
                "check": name,
                "exception_type": type(exc).__name__,
                "correlation_id": getattr(request.state, "correlation_id", None),
            },
        )
        result = ReadinessCheck(status="failed", reason="Dependency check failed.")
    return result


@router.get(
    "/readyz",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse, "description": "A dependency is not ready."}},
)
async def readyz(request: Request) -> ReadinessResponse | JSONResponse:
    names = tuple(READINESS_CHECKS)
    results = await asyncio.gather(
        *(_run_check(name, READINESS_CHECKS[name], request) for name in names)
    )
    checks = dict(zip(names, results, strict=True))
    if all(check.status == "ok" for check in checks.values()):
        return ReadinessResponse(status="ready", checks=checks)
    body = ReadinessResponse(status="not_ready", checks=checks)
    return JSONResponse(body.model_dump(mode="json"), status_code=503)
