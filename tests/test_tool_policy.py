from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import ClassVar
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel

from accelerator.agent_core.approvals import (
    Approval,
    ApprovalAuditEvent,
    ApprovalExpiredError,
    ApprovalMismatchError,
    ApprovalReplayError,
    ApprovalService,
)
from accelerator.agent_core.middleware.tool_policy import (
    ToolCallLimitExceeded,
    ToolCallLimits,
    ToolExecutionTimeout,
    ToolPolicyMiddleware,
)
from accelerator.agent_core.tools import EnterpriseTool, ExecutionContextProtocol, ToolRisk
from accelerator.security_core.data_boundaries.context import ExecutionContext
from accelerator.security_core.tool_policy.models import (
    ApprovalRequired,
    ToolPolicyViolation,
)


class Risk(StrEnum):
    READ_ONLY = "read_only"
    LOW_IMPACT_WRITE = "low_impact_write"
    HIGH_IMPACT_WRITE = "high_impact_write"
    PRIVILEGED = "privileged"
    PROHIBITED = "prohibited"


class ToolArgs(BaseModel):
    value: str


class ToolResult(BaseModel):
    value: str


@dataclass(frozen=True)
class Context:
    correlation_id: str = "correlation-1"
    user_id: str = "requester"
    roles: frozenset[str] = frozenset()
    scope_ids: frozenset[str] = frozenset({"scope-a"})
    session_id: str | None = "session-1"
    deadline_utc: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc) + timedelta(minutes=1)
    )


@dataclass(frozen=True)
class FakeApproval:
    id: UUID
    tool_name: str
    args_hash: str
    scope_id: str
    requested_by: str
    status: str
    decided_by: str | None
    expires_at: datetime
    correlation_id: str


class FakeTool:
    def __init__(
        self,
        risk: Risk = Risk.READ_ONLY,
        *,
        timeout_seconds: float = 1.0,
        delay_seconds: float = 0.0,
    ) -> None:
        self.name = "sample_tool"
        self.risk = risk
        self.timeout_seconds = timeout_seconds
        self.delay_seconds = delay_seconds
        self.calls = 0

    async def execute(self, args: ToolArgs, ctx: Context) -> ToolResult:
        self.calls += 1
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        return ToolResult(value=f"{ctx.user_id}:{args.value}")


class FakeApprovalService:
    def __init__(self) -> None:
        self.created: list[FakeApproval] = []
        self.executed: list[UUID] = []

    async def create(self, *, tool_name: str, args: ToolArgs, ctx: Context) -> FakeApproval:
        approval = FakeApproval(
            id=uuid4(),
            tool_name=tool_name,
            args_hash=_canonical_args_hash(args),
            scope_id=next(iter(ctx.scope_ids)),
            requested_by=ctx.user_id,
            status="pending",
            decided_by=None,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
            correlation_id=ctx.correlation_id,
        )
        self.created.append(approval)
        return approval

    async def approve(self, *, approval_id: UUID, ctx: Context) -> FakeApproval:
        approval = next(item for item in self.created if item.id == approval_id)
        decided = replace(approval, status="approved", decided_by=ctx.user_id)
        self.created[self.created.index(approval)] = decided
        return decided

    async def execute(
        self,
        *,
        approval_id: UUID,
        tool: FakeTool,
        args: ToolArgs,
        ctx: Context,
    ) -> ToolResult:
        matching = next(item for item in self.created if item.id == approval_id)
        if matching.status != "approved" or matching.args_hash != _canonical_args_hash(args):
            raise ToolPolicyViolation("approval_not_valid_for_tool_call")
        result = await tool.execute(args, ctx)
        executed = replace(matching, status="executed")
        self.created[self.created.index(matching)] = executed
        self.executed.append(approval_id)
        return result


def _canonical_args_hash(args: BaseModel) -> str:
    canonical_json = json.dumps(
        args.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


@pytest.mark.asyncio
async def test_read_only_tool_executes_without_approval() -> None:
    tool = FakeTool()
    middleware = ToolPolicyMiddleware()

    result = await middleware.invoke(tool, ToolArgs(value="read"), Context(), turn_id="turn-1")

    assert result == ToolResult(value="requester:read")
    assert tool.calls == 1


@pytest.mark.asyncio
async def test_write_tool_returns_approval_card_and_does_not_execute() -> None:
    tool = FakeTool(Risk.LOW_IMPACT_WRITE)
    args = ToolArgs(value="write")
    service = FakeApprovalService()
    middleware = ToolPolicyMiddleware(approval_service=service)

    result = await middleware.invoke(tool, args, Context(), turn_id="turn-1")

    assert isinstance(result, ApprovalRequired)
    assert result.approval_id == service.created[0].id
    assert result.tool_name == tool.name
    assert result.arguments == args
    assert result.args_hash == _canonical_args_hash(args)
    assert result.scope_id == "scope-a"
    assert result.requested_by == "requester"
    assert tool.calls == 0


@pytest.mark.asyncio
async def test_approved_write_is_executed_by_the_approval_service() -> None:
    tool = FakeTool(Risk.HIGH_IMPACT_WRITE)
    service = FakeApprovalService()
    middleware = ToolPolicyMiddleware(approval_service=service)
    args = ToolArgs(value="approved")
    approval = await service.create(tool_name=tool.name, args=args, ctx=Context())
    approval = await service.approve(
        approval_id=approval.id,
        ctx=Context(user_id="approver"),
    )

    result = await middleware.invoke(
        tool,
        args,
        Context(),
        turn_id="turn-1",
        approval=approval,
    )

    assert result == ToolResult(value="requester:approved")
    assert service.executed == [approval.id]
    assert service.created[0].status == "executed"
    assert tool.calls == 1


@pytest.mark.asyncio
async def test_approval_service_rejects_changed_arguments_without_execution() -> None:
    tool = FakeTool(Risk.LOW_IMPACT_WRITE)
    service = FakeApprovalService()
    middleware = ToolPolicyMiddleware(approval_service=service)
    approval = await service.create(
        tool_name=tool.name,
        args=ToolArgs(value="approved"),
        ctx=Context(),
    )
    approval = await service.approve(
        approval_id=approval.id,
        ctx=Context(user_id="approver"),
    )

    with pytest.raises(ToolPolicyViolation, match="approval_not_valid_for_tool_call"):
        await middleware.invoke(
            tool,
            ToolArgs(value="changed"),
            Context(),
            turn_id="turn-1",
            approval=approval,
        )

    assert tool.calls == 0
    assert service.executed == []


@pytest.mark.asyncio
async def test_write_without_approval_service_fails_closed() -> None:
    tool = FakeTool(Risk.LOW_IMPACT_WRITE)
    middleware = ToolPolicyMiddleware()

    with pytest.raises(ToolPolicyViolation, match="approval_service_unavailable"):
        await middleware.invoke(tool, ToolArgs(value="write"), Context(), turn_id="turn-1")

    assert tool.calls == 0


@pytest.mark.asyncio
async def test_unapproved_write_cannot_execute() -> None:
    tool = FakeTool(Risk.LOW_IMPACT_WRITE)
    service = FakeApprovalService()
    middleware = ToolPolicyMiddleware(approval_service=service)
    approval = await service.create(
        tool_name=tool.name,
        args=ToolArgs(value="pending"),
        ctx=Context(),
    )

    with pytest.raises(ToolPolicyViolation, match="approval_not_approved"):
        await middleware.invoke(
            tool,
            ToolArgs(value="pending"),
            Context(),
            turn_id="turn-1",
            approval=approval,
        )

    assert tool.calls == 0


@pytest.mark.asyncio
async def test_privileged_write_requires_distinct_approver_with_configured_role() -> None:
    tool = FakeTool(Risk.PRIVILEGED)
    service = FakeApprovalService()
    middleware = ToolPolicyMiddleware(
        approval_service=service,
        privileged_approver_roles=frozenset({"tool_approver"}),
    )
    args = ToolArgs(value="privileged")
    approval = await service.create(tool_name=tool.name, args=args, ctx=Context())
    approver = Context(user_id="approver", roles=frozenset({"tool_approver"}))
    approval = await service.approve(approval_id=approval.id, ctx=approver)

    result = await middleware.invoke(
        tool,
        args,
        Context(),
        turn_id="turn-1",
        approval=approval,
        approver_context=approver,
    )

    assert result == ToolResult(value="requester:privileged")


@pytest.mark.asyncio
async def test_privileged_write_fails_without_approver_role() -> None:
    tool = FakeTool(Risk.PRIVILEGED)
    service = FakeApprovalService()
    middleware = ToolPolicyMiddleware(
        approval_service=service,
        privileged_approver_roles=frozenset({"tool_approver"}),
    )
    args = ToolArgs(value="privileged")
    approval = await service.create(tool_name=tool.name, args=args, ctx=Context())
    approval = await service.approve(
        approval_id=approval.id,
        ctx=Context(user_id="approver"),
    )

    with pytest.raises(ToolPolicyViolation, match="privileged_approver_role_required"):
        await middleware.invoke(
            tool,
            args,
            Context(),
            turn_id="turn-1",
            approval=approval,
            approver_context=Context(user_id="approver"),
        )

    assert tool.calls == 0


@pytest.mark.asyncio
async def test_prohibited_tool_never_executes() -> None:
    tool = FakeTool(Risk.PROHIBITED)
    middleware = ToolPolicyMiddleware()

    with pytest.raises(ToolPolicyViolation, match="prohibited_tool"):
        await middleware.invoke(tool, ToolArgs(value="blocked"), Context(), turn_id="turn-1")

    assert tool.calls == 0


@pytest.mark.asyncio
async def test_tool_calls_are_limited_per_turn_and_session() -> None:
    tool = FakeTool()
    middleware = ToolPolicyMiddleware(
        limits=ToolCallLimits(max_calls_per_turn=2, max_calls_per_session=3)
    )
    context = Context()

    await middleware.invoke(tool, ToolArgs(value="1"), context, turn_id="turn-1")
    await middleware.invoke(tool, ToolArgs(value="2"), context, turn_id="turn-1")
    with pytest.raises(ToolCallLimitExceeded, match="per-turn"):
        await middleware.invoke(tool, ToolArgs(value="3"), context, turn_id="turn-1")

    await middleware.invoke(tool, ToolArgs(value="3"), context, turn_id="turn-2")
    with pytest.raises(ToolCallLimitExceeded, match="per-session"):
        await middleware.invoke(tool, ToolArgs(value="4"), context, turn_id="turn-3")

    assert tool.calls == 3


@pytest.mark.asyncio
async def test_tool_call_requires_session_and_turn_identifiers() -> None:
    tool = FakeTool()
    middleware = ToolPolicyMiddleware()

    with pytest.raises(ToolPolicyViolation, match="tool_call_requires_session"):
        await middleware.invoke(
            tool,
            ToolArgs(value="missing-session"),
            Context(session_id=None),
            turn_id="turn-1",
        )
    with pytest.raises(ToolPolicyViolation, match="tool_call_requires_turn"):
        await middleware.invoke(tool, ToolArgs(value="missing-turn"), Context(), turn_id=" ")

    assert tool.calls == 0


@pytest.mark.asyncio
async def test_tool_execution_is_stopped_at_declared_timeout() -> None:
    tool = FakeTool(timeout_seconds=0.01, delay_seconds=0.1)
    middleware = ToolPolicyMiddleware()

    with pytest.raises(ToolExecutionTimeout, match="policy timeout"):
        await middleware.invoke(tool, ToolArgs(value="slow"), Context(), turn_id="turn-1")


@pytest.mark.asyncio
async def test_write_tool_requires_one_context_scope() -> None:
    tool = FakeTool(Risk.LOW_IMPACT_WRITE)
    service = FakeApprovalService()
    middleware = ToolPolicyMiddleware(approval_service=service)

    with pytest.raises(ToolPolicyViolation, match="write_tool_requires_single_authorized_scope"):
        await middleware.invoke(
            tool,
            ToolArgs(value="write"),
            Context(scope_ids=frozenset({"scope-a", "scope-b"})),
            turn_id="turn-1",
        )

    assert service.created == []
    assert tool.calls == 0


class InMemoryApprovalRepository:
    def __init__(self) -> None:
        self.approvals: dict[UUID, Approval] = {}
        self.audit_events: list[ApprovalAuditEvent] = []
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        async with self._lock:
            yield

    async def get_for_update(self, approval_id: UUID) -> Approval | None:
        return self.approvals.get(approval_id)

    async def add(self, approval: Approval) -> None:
        self.approvals[approval.id] = approval

    async def update(self, approval: Approval) -> None:
        self.approvals[approval.id] = approval

    async def add_audit_event(self, event: ApprovalAuditEvent) -> None:
        self.audit_events.append(event)


class WriteTool(EnterpriseTool[ToolArgs, ToolResult]):
    name = "write_tool"
    description = "Write only after an args-bound approval."
    risk = ToolRisk.LOW_IMPACT_WRITE
    args_model: ClassVar[type[BaseModel]] = ToolArgs

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, args: ToolArgs, ctx: ExecutionContextProtocol) -> ToolResult:
        self.calls += 1
        return ToolResult(value=f"{ctx.user_id}:{args.value}")


def _server_context() -> ExecutionContext:
    return ExecutionContext(
        correlation_id="correlation-1",
        user_id="requester",
        roles=frozenset(),
        scope_ids=frozenset({"scope-a"}),
        session_id="session-1",
        deadline_utc=datetime.now(timezone.utc) + timedelta(minutes=1),
    )


@pytest.mark.asyncio
async def test_public_contract_creates_card_and_executes_real_approval_once() -> None:
    repository = InMemoryApprovalRepository()
    service = ApprovalService[ToolArgs, ToolResult](repository)
    middleware = ToolPolicyMiddleware(approval_service=service)
    tool = WriteTool()
    context = _server_context()
    args = ToolArgs(value="approved")

    card = await middleware.invoke(tool, args, context, turn_id="turn-1")

    assert isinstance(card, ApprovalRequired)
    assert repository.approvals[card.approval_id].status == "pending"
    assert card.scope_id == next(iter(context.scope_ids))
    assert tool.calls == 0
    approval = await service.approve(approval_id=card.approval_id, ctx=context)
    result = await middleware.invoke(tool, args, context, turn_id="turn-2", approval=approval)

    assert result == ToolResult(value="requester:approved")
    assert repository.approvals[approval.id].status == "executed"
    assert [event.transition for event in repository.audit_events] == [
        "pending",
        "approved",
        "executed",
    ]
    with pytest.raises(ApprovalReplayError):
        await middleware.invoke(tool, args, context, turn_id="turn-3", approval=approval)
    assert tool.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["changed_args", "expired", "wrong_scope"])
async def test_real_approval_failures_never_invoke_tool(failure: str) -> None:
    repository = InMemoryApprovalRepository()
    now = datetime.now(timezone.utc)
    service = ApprovalService[ToolArgs, ToolResult](repository, clock=lambda: now)
    middleware = ToolPolicyMiddleware(approval_service=service)
    tool = WriteTool()
    context = _server_context()
    args = ToolArgs(value="approved")
    approval = await service.create(tool_name=tool.name, args=args, ctx=context)
    approval = await service.approve(approval_id=approval.id, ctx=context)
    expected_error: type[Exception]
    if failure == "changed_args":
        args = ToolArgs(value="changed")
        expected_error = ApprovalMismatchError
    elif failure == "expired":
        now += timedelta(minutes=11)
        expected_error = ApprovalExpiredError
    else:
        context = context.model_copy(update={"scope_ids": frozenset({"scope-b"})})
        expected_error = ToolPolicyViolation

    with pytest.raises(expected_error):
        await middleware.invoke(tool, args, context, turn_id="turn-1", approval=approval)

    assert tool.calls == 0
    assert not any(event.transition == "executed" for event in repository.audit_events)
