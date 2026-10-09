from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import ClassVar
from uuid import UUID

import pytest
from pydantic import BaseModel

from accelerator.security_core.data_boundaries.context import ExecutionContext
from accelerator.security_core.tool_policy.models import (
    ApprovalRequired,
    ToolPolicyViolation,
)

from ...approvals import (
    Approval,
    ApprovalAuditEvent,
    ApprovalAuthorizationError,
    ApprovalExpiredError,
    ApprovalMismatchError,
    ApprovalReplayError,
    ApprovalScopeError,
    ApprovalService,
    ApprovalStateError,
    canonical_args_hash,
)
from ...tools import EnterpriseTool, ExecutionContextProtocol, IdempotentWriteTool, ToolRisk
from ..tool_policy import (
    ToolCallLimitExceeded,
    ToolCallLimits,
    ToolExecutionTimeout,
    ToolPolicyMiddleware,
)

Risk = ToolRisk


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
        default_factory=lambda: datetime.now(UTC) + timedelta(minutes=1)
    )


class TestTool(EnterpriseTool[ToolArgs, ToolResult]):
    __test__ = False
    name = "sample_tool"
    description = "Test tool."
    risk = Risk.READ_ONLY
    args_model: ClassVar[type[BaseModel]] = ToolArgs

    def __init__(
        self,
        *,
        delay_seconds: float = 0.0,
    ) -> None:
        self.delay_seconds = delay_seconds
        self.calls = 0

    async def execute(self, args: ToolArgs, ctx: ExecutionContextProtocol) -> ToolResult:
        self.calls += 1
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        return ToolResult(value=f"{ctx.user_id}:{args.value}")


class WriteTestTool(IdempotentWriteTool[ToolArgs, ToolResult]):
    name = "sample_tool"
    description = "Test write tool."
    risk = Risk.LOW_IMPACT_WRITE
    args_model: ClassVar[type[BaseModel]] = ToolArgs

    def __init__(
        self,
        *,
        delay_seconds: float = 0.0,
    ) -> None:
        self.delay_seconds = delay_seconds
        self.calls = 0
        self.execution_ids: list[UUID] = []

    async def execute_approved(
        self,
        args: ToolArgs,
        ctx: ExecutionContextProtocol,
        *,
        execution_id: UUID,
    ) -> ToolResult:
        self.calls += 1
        self.execution_ids.append(execution_id)
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        return ToolResult(value=f"{ctx.user_id}:{args.value}")


def FakeTool(
    risk: ToolRisk = Risk.READ_ONLY,
    *,
    timeout_seconds: float = 1.0,
    delay_seconds: float = 0.0,
) -> TestTool | WriteTestTool:
    selected_risk = risk
    selected_timeout = timeout_seconds

    if risk in (Risk.LOW_IMPACT_WRITE, Risk.HIGH_IMPACT_WRITE, Risk.PRIVILEGED):
        class ConfiguredWriteTool(WriteTestTool):
            risk = selected_risk
            timeout_seconds = selected_timeout

        return ConfiguredWriteTool(delay_seconds=delay_seconds)

    class ConfiguredTool(TestTool):
        risk = selected_risk
        timeout_seconds = selected_timeout

    return ConfiguredTool(delay_seconds=delay_seconds)


class FakeApprovalService(ApprovalService[ToolArgs, ToolResult]):
    def __init__(self) -> None:
        self.repository = InMemoryApprovalRepository()
        super().__init__(self.repository)

    def force_decision(self, approval_id: UUID, decided_by: str) -> Approval:
        """Persist a decision the service itself refuses, e.g. a legacy or tampered row."""
        approval = self.repository.approvals[approval_id].model_copy(
            update={"status": "approved", "decided_by": decided_by}
        )
        self.repository.approvals[approval_id] = approval
        return approval

    @property
    def created(self) -> list[Approval]:
        return list(self.repository.approvals.values())

    @property
    def executed(self) -> list[UUID]:
        return [
            event.approval_id
            for event in self.repository.audit_events
            if event.transition == "executed"
        ]


@pytest.mark.asyncio
async def test_read_only_tool_executes_without_approval() -> None:
    tool = FakeTool()
    middleware = ToolPolicyMiddleware[ToolArgs, ToolResult]()

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
    assert result.args_hash == canonical_args_hash(args)
    assert result.scope_id == "scope-a"
    assert result.requested_by == "requester"
    assert tool.calls == 0


@pytest.mark.asyncio
async def test_write_risk_rejects_tool_without_idempotent_execution_capability() -> None:
    class UnsafeWriteTool(TestTool):
        risk = Risk.LOW_IMPACT_WRITE

    tool = UnsafeWriteTool()
    service = FakeApprovalService()
    middleware = ToolPolicyMiddleware(approval_service=service)

    with pytest.raises(ToolPolicyViolation, match="write_tool_requires_idempotent_execution"):
        await middleware.invoke(tool, ToolArgs(value="write"), Context(), turn_id="turn-1")

    assert service.created == []
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
        ctx=Context(user_id="approver", roles=frozenset({"Approver"})),
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
    assert isinstance(tool, IdempotentWriteTool)
    assert tool.execution_ids == [approval.id]


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
        ctx=Context(user_id="approver", roles=frozenset({"Approver"})),
    )

    with pytest.raises(ApprovalMismatchError):
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
    middleware = ToolPolicyMiddleware[ToolArgs, ToolResult]()

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

    with pytest.raises(ApprovalStateError):
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
    approver = Context(user_id="approver", roles=frozenset({"tool_approver", "Approver"}))
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
        ctx=Context(user_id="approver", roles=frozenset({"Approver"})),
    )

    with pytest.raises(ToolPolicyViolation, match="privileged_approver_role_required"):
        await middleware.invoke(
            tool,
            args,
            Context(),
            turn_id="turn-1",
            approval=approval,
            approver_context=Context(user_id="approver", roles=frozenset({"Approver"})),
        )

    assert tool.calls == 0


@pytest.mark.asyncio
async def test_prohibited_tool_never_executes() -> None:
    tool = FakeTool(Risk.PROHIBITED)
    middleware = ToolPolicyMiddleware[ToolArgs, ToolResult]()

    with pytest.raises(ToolPolicyViolation, match="prohibited_tool"):
        await middleware.invoke(tool, ToolArgs(value="blocked"), Context(), turn_id="turn-1")

    assert tool.calls == 0


@pytest.mark.asyncio
async def test_tool_calls_are_limited_per_turn_and_session() -> None:
    tool = FakeTool()
    middleware = ToolPolicyMiddleware[ToolArgs, ToolResult](
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
    middleware = ToolPolicyMiddleware[ToolArgs, ToolResult]()

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
    middleware = ToolPolicyMiddleware[ToolArgs, ToolResult]()

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


class WriteTool(IdempotentWriteTool[ToolArgs, ToolResult]):
    name = "write_tool"
    description = "Write only after an args-bound approval."
    risk = ToolRisk.LOW_IMPACT_WRITE
    args_model: ClassVar[type[BaseModel]] = ToolArgs

    def __init__(self) -> None:
        self.calls = 0
        self.execution_ids: list[UUID] = []

    async def execute_approved(
        self,
        args: ToolArgs,
        ctx: ExecutionContextProtocol,
        *,
        execution_id: UUID,
    ) -> ToolResult:
        self.calls += 1
        self.execution_ids.append(execution_id)
        return ToolResult(value=f"{ctx.user_id}:{args.value}")


def _server_approver() -> ExecutionContext:
    return ExecutionContext(
        correlation_id="correlation-2",
        user_id="approver",
        roles=frozenset({"Approver"}),
        scope_ids=frozenset({"scope-a"}),
        session_id="session-2",
        deadline_utc=datetime.now(UTC) + timedelta(minutes=1),
    )


def _server_context() -> ExecutionContext:
    return ExecutionContext(
        correlation_id="correlation-1",
        user_id="requester",
        roles=frozenset(),
        scope_ids=frozenset({"scope-a"}),
        session_id="session-1",
        deadline_utc=datetime.now(UTC) + timedelta(minutes=1),
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
    approval = await service.approve(approval_id=card.approval_id, ctx=_server_approver())
    result = await middleware.invoke(tool, args, context, turn_id="turn-2", approval=approval)

    assert result == ToolResult(value="requester:approved")
    assert tool.execution_ids == [approval.id]
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
    now = datetime.now(UTC)
    service = ApprovalService[ToolArgs, ToolResult](repository, clock=lambda: now)
    middleware = ToolPolicyMiddleware(approval_service=service)
    tool = WriteTool()
    context = _server_context()
    args = ToolArgs(value="approved")
    approval = await service.create(tool_name=tool.name, args=args, ctx=context)
    approval = await service.approve(approval_id=approval.id, ctx=_server_approver())
    expected_error: type[Exception]
    if failure == "changed_args":
        args = ToolArgs(value="changed")
        expected_error = ApprovalMismatchError
    elif failure == "expired":
        now += timedelta(minutes=11)
        expected_error = ApprovalExpiredError
    else:
        context = context.model_copy(update={"scope_ids": frozenset({"scope-b"})})
        expected_error = ApprovalScopeError

    with pytest.raises(expected_error):
        await middleware.invoke(tool, args, context, turn_id="turn-1", approval=approval)

    assert tool.calls == 0
    assert not any(event.transition == "executed" for event in repository.audit_events)


@pytest.mark.asyncio
async def test_privileged_self_approval_never_executes() -> None:
    service = FakeApprovalService()
    middleware = ToolPolicyMiddleware(
        approval_service=service, privileged_approver_roles=frozenset({"tool_approver"})
    )
    tool = FakeTool(Risk.PRIVILEGED)
    context = Context(roles=frozenset({"tool_approver", "Approver"}))
    args = ToolArgs(value="write")
    approval = await service.create(tool_name=tool.name, args=args, ctx=context)
    with pytest.raises(ApprovalAuthorizationError, match="requester cannot decide"):
        await service.approve(approval_id=approval.id, ctx=context)
    # Defense in depth: even a persisted self-approval never executes.
    approval = service.force_decision(approval.id, decided_by=context.user_id)

    with pytest.raises(ToolPolicyViolation, match="privileged_approver_must_be_distinct"):
        await middleware.invoke(
            tool, args, context, turn_id="turn", approval=approval, approver_context=context
        )

    assert tool.calls == 0
    assert service.executed == []


@pytest.mark.asyncio
async def test_capacity_fails_closed_and_completed_session_releases_counters() -> None:
    middleware = ToolPolicyMiddleware[ToolArgs, ToolResult](
        limits=ToolCallLimits(max_calls_per_turn=1, max_calls_per_session=2, max_tracked_sessions=2)
    )
    tool = FakeTool()
    args = ToolArgs(value="read")
    for session in ("one", "two"):
        await middleware.invoke(tool, args, Context(session_id=session), turn_id="turn")

    for index in range(100):
        with pytest.raises(ToolCallLimitExceeded, match="capacity"):
            await middleware.invoke(tool, args, Context(session_id=f"new-{index}"), turn_id="turn")
    with pytest.raises(ToolCallLimitExceeded, match="per-turn"):
        await middleware.invoke(tool, args, Context(session_id="one"), turn_id="turn")

    await middleware.release_session("one")
    await middleware.invoke(tool, args, Context(session_id="three"), turn_id="turn")
    await middleware.invoke(tool, args, Context(session_id="two"), turn_id="next")
    with pytest.raises(ToolCallLimitExceeded, match="per-session"):
        await middleware.invoke(tool, args, Context(session_id="two"), turn_id="another")

    assert tool.calls == 4


class SlowApprovalService(FakeApprovalService):
    cancelled = False

    async def create(
        self,
        *,
        tool_name: str,
        args: ToolArgs,
        ctx: ExecutionContextProtocol,
        expires_at: datetime | None = None,
    ) -> Approval:
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return await super().create(tool_name=tool_name, args=args, ctx=ctx, expires_at=expires_at)


@pytest.mark.asyncio
async def test_elapsed_deadline_prevents_approval_creation() -> None:
    service = FakeApprovalService()
    middleware = ToolPolicyMiddleware(approval_service=service)
    tool = FakeTool(Risk.LOW_IMPACT_WRITE)

    with pytest.raises(ToolExecutionTimeout, match="elapsed"):
        await middleware.invoke(
            tool,
            ToolArgs(value="write"),
            Context(deadline_utc=datetime.now(UTC) - timedelta(seconds=1)),
            turn_id="turn",
        )

    assert service.created == []
    assert tool.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("use_deadline", [False, True])
async def test_stalled_approval_creation_is_cancelled_at_policy_timeout(
    use_deadline: bool,
) -> None:
    service = SlowApprovalService()
    middleware = ToolPolicyMiddleware(approval_service=service)
    tool = FakeTool(Risk.LOW_IMPACT_WRITE, timeout_seconds=1 if use_deadline else 0.01)
    context = Context(
        deadline_utc=datetime.now(UTC) + timedelta(seconds=0.01 if use_deadline else 60)
    )

    with pytest.raises(ToolExecutionTimeout, match="policy timeout"):
        await middleware.invoke(tool, ToolArgs(value="write"), context, turn_id="turn")

    assert service.cancelled
    assert service.created == []
    assert tool.calls == 0


@pytest.mark.asyncio
async def test_tool_domain_timeout_is_not_reclassified() -> None:
    error = TimeoutError("domain timeout")

    class TimeoutTool(TestTool):
        async def execute(self, args: ToolArgs, ctx: ExecutionContextProtocol) -> ToolResult:
            raise error

    middleware = ToolPolicyMiddleware[ToolArgs, ToolResult]()
    with pytest.raises(TimeoutError) as raised:
        await middleware.invoke(TimeoutTool(), ToolArgs(value="read"), Context(), turn_id="turn")

    assert raised.value is error


@pytest.mark.asyncio
async def test_approval_creation_domain_timeout_is_not_reclassified() -> None:
    error = TimeoutError("persistence timeout")

    class TimeoutService(FakeApprovalService):
        async def create(
            self,
            *,
            tool_name: str,
            args: ToolArgs,
            ctx: ExecutionContextProtocol,
            expires_at: datetime | None = None,
        ) -> Approval:
            raise error

    middleware = ToolPolicyMiddleware(approval_service=TimeoutService())
    with pytest.raises(TimeoutError) as raised:
        await middleware.invoke(
            FakeTool(Risk.LOW_IMPACT_WRITE), ToolArgs(value="write"), Context(), turn_id="turn"
        )

    assert raised.value is error


@pytest.mark.asyncio
@pytest.mark.parametrize("forged_field", ["decided_by", "requested_by"])
async def test_forged_caller_approval_cannot_authorize_persisted_record(
    forged_field: str,
) -> None:
    service = FakeApprovalService()
    middleware = ToolPolicyMiddleware(
        approval_service=service, privileged_approver_roles=frozenset({"tool_approver"})
    )
    tool = FakeTool(Risk.PRIVILEGED)
    context = Context()
    approver = Context(user_id="approver", roles=frozenset({"tool_approver", "Approver"}))
    args = ToolArgs(value="write")
    requester = context if forged_field == "decided_by" else Context(user_id="other")
    persisted = await service.create(tool_name=tool.name, args=args, ctx=requester)
    if forged_field == "decided_by":
        persisted = service.force_decision(persisted.id, decided_by=context.user_id)
    else:
        persisted = await service.approve(approval_id=persisted.id, ctx=approver)
    forged = persisted.model_copy(
        update={"decided_by": approver.user_id, "requested_by": context.user_id}
    )
    reason = (
        "privileged_approver_must_be_distinct"
        if forged_field == "decided_by"
        else "approval_requester_mismatch"
    )

    with pytest.raises(ToolPolicyViolation, match=reason):
        await middleware.invoke(
            tool, args, context, turn_id="turn", approval=forged, approver_context=approver
        )

    assert tool.calls == 0
    assert service.created[0].status == "approved"
    assert service.executed == []


@pytest.mark.asyncio
async def test_caller_approval_fields_are_ignored_when_persisted_record_is_valid() -> None:
    service = FakeApprovalService()
    middleware = ToolPolicyMiddleware(approval_service=service)
    tool = FakeTool(Risk.LOW_IMPACT_WRITE)
    args = ToolArgs(value="write")
    context = Context()
    persisted = await service.create(tool_name=tool.name, args=args, ctx=context)
    persisted = await service.approve(
        approval_id=persisted.id, ctx=Context(user_id="approver", roles=frozenset({"Approver"}))
    )
    stale = persisted.model_copy(
        update={"status": "pending", "requested_by": "forged", "scope_id": "forged"}
    )

    result = await middleware.invoke(tool, args, context, turn_id="turn", approval=stale)

    assert result == ToolResult(value="requester:write")
    assert tool.calls == 1
    assert service.created[0].status == "executed"
