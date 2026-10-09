"""Human decisions on args-bound approvals.

Listing and deciding require the ``Approver`` app role. ``ApprovalService`` enforces
the rest under a row lock: the approval must be in one of the caller's
server-resolved scopes, still pending and unexpired, and the decider must not be
the requester. Every transition is written to ``approval_audit_events`` and
decisions to the central ``audit_event`` log in the same transaction. Bound
arguments are never returned; only their hash is persisted.
"""

import logging
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict

from accelerator.agent_core.approvals import (
    APPROVER_ROLE,
    Approval,
    ApprovalAuthorizationError,
    ApprovalError,
    ApprovalExpiredError,
    ApprovalNotFoundError,
    ApprovalScopeError,
    ApprovalService,
    ApprovalStateError,
)
from accelerator.identity.scope_resolver import get_execution_context
from accelerator.infrastructure.approvals import SQLAlchemyApprovalRepository
from accelerator.security_core.data_boundaries.context import ExecutionContext
from accelerator.security_core.infrastructure.database import SessionFactory

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/approvals", tags=["approvals"])


class ApprovalView(BaseModel):
    model_config = ConfigDict(frozen=True)

    approval_id: UUID
    tool_name: str
    status: Literal["pending", "approved", "rejected", "executed", "expired"]
    scope_id: str
    requested_by: str
    decided_by: str | None
    expires_at: AwareDatetime
    can_decide: bool

    @classmethod
    def of(cls, approval: Approval, context: ExecutionContext) -> "ApprovalView":
        return cls(
            approval_id=approval.id,
            tool_name=approval.tool_name,
            status=approval.status,
            scope_id=approval.scope_id,
            requested_by=approval.requested_by,
            decided_by=approval.decided_by,
            expires_at=approval.expires_at,
            can_decide=approval.status == "pending" and approval.requested_by != context.user_id,
        )


class ApprovalList(BaseModel):
    items: list[ApprovalView]


async def get_approval_repository(
    request: Request,
) -> AsyncGenerator[SQLAlchemyApprovalRepository]:
    """One request-scoped session shared by the repository and the service."""
    session_factory: SessionFactory | None = getattr(request.app.state, "session_factory", None)
    if session_factory is None:
        raise HTTPException(status_code=503, detail="Approval persistence is unavailable.")
    async with session_factory() as session:
        yield SQLAlchemyApprovalRepository(session)


async def get_approval_service(
    repository: Annotated[SQLAlchemyApprovalRepository, Depends(get_approval_repository)],
) -> ApprovalService[BaseModel, BaseModel]:
    return ApprovalService(repository)


async def require_approver(
    context: Annotated[ExecutionContext, Depends(get_execution_context)],
) -> ExecutionContext:
    if APPROVER_ROLE not in context.roles:
        raise HTTPException(status_code=403, detail="Approver role required.")
    return context


def _decision_error(error: ApprovalError, context: ExecutionContext) -> HTTPException:
    logger.info(
        "approval_decision_refused",
        extra={"correlation_id": context.correlation_id, "reason": type(error).__name__},
    )
    # Out-of-scope approvals are indistinguishable from missing ones.
    if isinstance(error, ApprovalNotFoundError | ApprovalScopeError):
        return HTTPException(status_code=404, detail="Approval not found.")
    if isinstance(error, ApprovalAuthorizationError):
        return HTTPException(status_code=403, detail=str(error))
    if isinstance(error, ApprovalExpiredError):
        return HTTPException(
            status_code=409, detail={"code": "approval_expired", "message": str(error)}
        )
    if isinstance(error, ApprovalStateError):
        return HTTPException(
            status_code=409, detail={"code": "approval_not_pending", "message": str(error)}
        )
    return HTTPException(status_code=409, detail={"code": "approval_refused"})


@router.get("", response_model=ApprovalList)
async def list_pending_approvals(
    context: Annotated[ExecutionContext, Depends(require_approver)],
    repository: Annotated[SQLAlchemyApprovalRepository, Depends(get_approval_repository)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ApprovalList:
    pending = await repository.list_pending(
        context.scope_ids, now=datetime.now(UTC), limit=limit
    )
    return ApprovalList(items=[ApprovalView.of(approval, context) for approval in pending])


ApprovalId = Annotated[UUID, Path(description="Approval identifier from an approval card.")]
DECISION_RESPONSES: dict[int | str, dict[str, object]] = {
    404: {"description": "No such approval in the caller's scopes."},
    409: {"description": "The approval is expired or no longer pending."},
}


@router.post("/{approval_id}/approve", response_model=ApprovalView, responses=DECISION_RESPONSES)
async def approve(
    approval_id: ApprovalId,
    context: Annotated[ExecutionContext, Depends(require_approver)],
    service: Annotated[ApprovalService[BaseModel, BaseModel], Depends(get_approval_service)],
) -> ApprovalView:
    try:
        decided = await service.approve(approval_id=approval_id, ctx=context)
    except ApprovalError as error:
        raise _decision_error(error, context) from error
    return ApprovalView.of(decided, context)


@router.post("/{approval_id}/reject", response_model=ApprovalView, responses=DECISION_RESPONSES)
async def reject(
    approval_id: ApprovalId,
    context: Annotated[ExecutionContext, Depends(require_approver)],
    service: Annotated[ApprovalService[BaseModel, BaseModel], Depends(get_approval_service)],
) -> ApprovalView:
    try:
        decided = await service.reject(approval_id=approval_id, ctx=context)
    except ApprovalError as error:
        raise _decision_error(error, context) from error
    return ApprovalView.of(decided, context)
