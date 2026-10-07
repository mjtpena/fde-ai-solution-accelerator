import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import AsyncContextManager, Generic, Literal, Protocol, TypeVar
from uuid import UUID, uuid4

from pydantic import BaseModel

from .models import Approval, ApprovalAuditEvent

TArgs = TypeVar("TArgs", bound=BaseModel)
TResult = TypeVar("TResult", bound=BaseModel)
TToolArgs = TypeVar("TToolArgs", bound=BaseModel, contravariant=True)
TToolResult = TypeVar("TToolResult", bound=BaseModel, covariant=True)
Decision = Literal["approved", "rejected"]


class ApprovalError(Exception):
    """Base exception for approval failures."""


class ApprovalNotFoundError(ApprovalError):
    pass


class ApprovalScopeError(ApprovalError):
    pass


class ApprovalExpiredError(ApprovalError):
    pass


class ApprovalMismatchError(ApprovalError):
    pass


class ApprovalStateError(ApprovalError):
    pass


class ApprovalReplayError(ApprovalStateError):
    pass


class ApprovalContext(Protocol):
    user_id: str
    scope_ids: frozenset[str]
    correlation_id: str
    roles: frozenset[str]
    session_id: str | None
    deadline_utc: datetime


class ApprovalTool(Protocol, Generic[TToolArgs, TToolResult]):
    name: str

    async def execute(self, args: TToolArgs, ctx: ApprovalContext) -> TToolResult: ...


class ApprovalRepository(Protocol):
    def transaction(self) -> AsyncContextManager[None]: ...

    async def get_for_update(self, approval_id: UUID) -> Approval | None: ...

    async def add(self, approval: Approval) -> None: ...

    async def update(self, approval: Approval) -> None: ...

    async def add_audit_event(self, event: ApprovalAuditEvent) -> None: ...


class ApprovalService(Generic[TArgs, TResult]):
    def __init__(
        self,
        repository: ApprovalRepository,
        *,
        approval_ttl: timedelta = timedelta(minutes=10),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if approval_ttl <= timedelta(0):
            raise ValueError("approval_ttl must be positive")
        self._repository = repository
        self._approval_ttl = approval_ttl
        self._clock = clock

    async def create(
        self,
        *,
        tool_name: str,
        args: TArgs,
        ctx: ApprovalContext,
        expires_at: datetime | None = None,
    ) -> Approval:
        scope_id = self._single_scope(ctx)
        now = self._now()
        expiry = expires_at or (now + self._approval_ttl)
        if expiry.tzinfo is None or expiry.utcoffset() is None:
            raise ValueError("expires_at must be timezone-aware")
        if expiry <= now:
            raise ApprovalExpiredError("approval expiry must be in the future")

        approval = Approval(
            id=uuid4(),
            tool_name=tool_name,
            args_hash=canonical_args_hash(args),
            scope_id=scope_id,
            requested_by=ctx.user_id,
            status="pending",
            decided_by=None,
            expires_at=expiry,
            correlation_id=ctx.correlation_id,
        )
        async with self._repository.transaction():
            await self._repository.add(approval)
            await self._audit(approval, "pending", ctx.user_id)
        return approval

    async def approve(self, *, approval_id: UUID, ctx: ApprovalContext) -> Approval:
        return await self._decide(approval_id=approval_id, ctx=ctx, decision="approved")

    async def reject(self, *, approval_id: UUID, ctx: ApprovalContext) -> Approval:
        return await self._decide(approval_id=approval_id, ctx=ctx, decision="rejected")

    async def execute(
        self,
        *,
        approval_id: UUID,
        tool: ApprovalTool[TArgs, TResult],
        args: TArgs,
        ctx: ApprovalContext,
    ) -> TResult:
        result: TResult | None = None
        expired = False
        async with self._repository.transaction():
            approval = await self._locked_approval(approval_id)
            self._validate_binding(approval, tool.name, args, ctx)
            now = self._now()
            if approval.status == "approved" and approval.expires_at <= now:
                expired_approval = approval.model_copy(update={"status": "expired"})
                await self._repository.update(expired_approval)
                await self._audit(expired_approval, "expired", ctx.user_id)
                expired = True
            else:
                self._require_approved(approval)
                result = await tool.execute(args, ctx)
                executed = approval.model_copy(
                    update={"status": "executed", "decided_by": approval.decided_by}
                )
                await self._repository.update(executed)
                await self._audit(executed, "executed", ctx.user_id)
        if expired:
            raise ApprovalExpiredError("approval has expired")
        if result is None:
            raise ApprovalError("approved tool returned no result")
        return result

    async def _decide(
        self,
        *,
        approval_id: UUID,
        ctx: ApprovalContext,
        decision: Decision,
    ) -> Approval:
        expired = False
        decided: Approval | None = None
        async with self._repository.transaction():
            approval = await self._locked_approval(approval_id)
            self._scope_matches(approval, ctx)
            if approval.status == "pending" and approval.expires_at <= self._now():
                expired_approval = approval.model_copy(update={"status": "expired"})
                await self._repository.update(expired_approval)
                await self._audit(expired_approval, "expired", ctx.user_id)
                expired = True
            else:
                if approval.status != "pending":
                    self._require_pending(approval)
                decided = approval.model_copy(update={"status": decision, "decided_by": ctx.user_id})
                await self._repository.update(decided)
                await self._audit(decided, decision, ctx.user_id)
        if expired:
            raise ApprovalExpiredError("approval has expired")
        if decided is None:
            raise ApprovalError("approval decision did not produce a result")
        return decided

    async def _locked_approval(self, approval_id: UUID) -> Approval:
        approval = await self._repository.get_for_update(approval_id)
        if approval is None:
            raise ApprovalNotFoundError("approval does not exist")
        return approval

    def _validate_binding(
        self, approval: Approval, tool_name: str, args: TArgs, ctx: ApprovalContext
    ) -> None:
        self._scope_matches(approval, ctx)
        if approval.tool_name != tool_name or approval.args_hash != canonical_args_hash(args):
            raise ApprovalMismatchError("tool or arguments do not match the approval")

    @staticmethod
    def _scope_matches(approval: Approval, ctx: ApprovalContext) -> None:
        if approval.scope_id not in ctx.scope_ids:
            raise ApprovalScopeError("approval scope is not available in this execution context")

    @staticmethod
    def _single_scope(ctx: ApprovalContext) -> str:
        if len(ctx.scope_ids) != 1:
            raise ApprovalScopeError("approval creation requires one server-resolved scope")
        return next(iter(ctx.scope_ids))

    @staticmethod
    def _require_pending(approval: Approval) -> None:
        if approval.status == "executed":
            raise ApprovalReplayError("approval has already been executed")
        if approval.status == "expired":
            raise ApprovalExpiredError("approval has expired")
        if approval.status == "rejected":
            raise ApprovalStateError(f"approval is {approval.status}")
        raise ApprovalStateError(f"approval must be pending, not {approval.status}")

    @staticmethod
    def _require_approved(approval: Approval) -> None:
        if approval.status == "executed":
            raise ApprovalReplayError("approval has already been executed")
        if approval.status == "expired":
            raise ApprovalExpiredError("approval has expired")
        if approval.status == "rejected":
            raise ApprovalStateError(f"approval is {approval.status}")
        if approval.status != "approved":
            raise ApprovalStateError("approval must be approved before execution")

    async def _audit(
        self,
        approval: Approval,
        transition: Literal["pending", "approved", "rejected", "executed", "expired"],
        actor_id: str,
    ) -> None:
        await self._repository.add_audit_event(
            ApprovalAuditEvent(
                approval_id=approval.id,
                transition=transition,
                actor_id=actor_id,
                occurred_at=self._now(),
                correlation_id=approval.correlation_id,
            )
        )

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return now


def canonical_args_hash(args: BaseModel) -> str:
    canonical = json.dumps(
        args.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
