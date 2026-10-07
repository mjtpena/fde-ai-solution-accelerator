import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import ClassVar, cast
from unittest import IsolatedAsyncioTestCase
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.dialects import postgresql

from accelerator.agent_core.approvals import (
    Approval,
    ApprovalAuditEvent,
    ApprovalContext,
    ApprovalExpiredError,
    ApprovalMismatchError,
    ApprovalReplayError,
    ApprovalScopeError,
    ApprovalService,
    ApprovalTool,
    canonical_args_hash,
)
from accelerator.agent_core.tools import EnterpriseTool, ExecutionContextProtocol, ToolRisk
from sqlalchemy.ext.asyncio import AsyncSession

from accelerator.infrastructure.approvals import (
    SQLAlchemyApprovalRepository,
)
from accelerator.security_core.data_boundaries.context import ExecutionContext


@dataclass(frozen=True)
class Context:
    user_id: str = "user-1"
    scope_ids: frozenset[str] = frozenset({"scope-1"})
    correlation_id: str = "correlation-1"
    roles: frozenset[str] = frozenset()
    session_id: str | None = None
    deadline_utc: datetime = datetime(2030, 1, 1, tzinfo=UTC)


class Arguments(BaseModel):
    amount: int
    note: str


class Result(BaseModel):
    completed: bool


class Tool(EnterpriseTool[Arguments, Result]):
    name: ClassVar[str] = "write_record"
    description: ClassVar[str] = "Write a record after approval."
    risk: ClassVar[ToolRisk] = ToolRisk.LOW_IMPACT_WRITE
    args_model: ClassVar[type[BaseModel]] = Arguments

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, args: Arguments, ctx: ExecutionContextProtocol) -> Result:
        self.calls += 1
        return Result(completed=True)


class DifferentTool(Tool):
    name: ClassVar[str] = "different_write"


class InMemoryApprovalRepository:
    def __init__(self) -> None:
        self.approvals: dict[UUID, Approval] = {}
        self.audit_events: list[ApprovalAuditEvent] = []
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        async with self._lock:
            approvals = dict(self.approvals)
            audit_events = list(self.audit_events)
            try:
                yield
            except Exception:
                self.approvals = approvals
                self.audit_events = audit_events
                raise

    async def get_for_update(self, approval_id: UUID) -> Approval | None:
        return self.approvals.get(approval_id)

    async def add(self, approval: Approval) -> None:
        self.approvals[approval.id] = approval

    async def update(self, approval: Approval) -> None:
        self.approvals[approval.id] = approval

    async def add_audit_event(self, event: ApprovalAuditEvent) -> None:
        self.audit_events.append(event)


class ApprovalServiceTests(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
        self.repository = InMemoryApprovalRepository()
        self.service = ApprovalService[Arguments, Result](
            self.repository,
            clock=lambda: self.now,
        )
        self.context = Context()
        self.args = Arguments(amount=25, note="approved")
        self.tool = Tool()

    async def _approved(self, *, expires_at: datetime | None = None) -> Approval:
        approval = await self.service.create(
            tool_name=self.tool.name,
            args=self.args,
            ctx=self.context,
            expires_at=expires_at,
        )
        return await self.service.approve(approval_id=approval.id, ctx=self.context)

    async def test_execute_with_matching_approval_succeeds_once(self) -> None:
        approval = await self._approved()

        result = await self.service.execute(
            approval_id=approval.id,
            tool=self.tool,
            args=self.args,
            ctx=self.context,
        )

        self.assertEqual(result, Result(completed=True))
        self.assertEqual(self.tool.calls, 1)
        self.assertEqual(self.repository.approvals[approval.id].status, "executed")
        self.assertEqual(
            [event.transition for event in self.repository.audit_events],
            ["pending", "approved", "executed"],
        )

    async def test_replay_fails_without_invoking_tool_again(self) -> None:
        approval = await self._approved()
        await self.service.execute(
            approval_id=approval.id,
            tool=self.tool,
            args=self.args,
            ctx=self.context,
        )

        with self.assertRaises(ApprovalReplayError):
            await self.service.execute(
                approval_id=approval.id,
                tool=self.tool,
                args=self.args,
                ctx=self.context,
            )

        self.assertEqual(self.tool.calls, 1)

    async def test_concurrent_replay_executes_tool_only_once(self) -> None:
        approval = await self._approved()

        async def execute() -> BaseModel | ApprovalReplayError:
            try:
                return await self.service.execute(
                    approval_id=approval.id,
                    tool=self.tool,
                    args=self.args,
                    ctx=self.context,
                )
            except ApprovalReplayError as error:
                return error

        results = await asyncio.gather(execute(), execute())

        self.assertEqual(sum(isinstance(result, Result) for result in results), 1)
        self.assertEqual(sum(isinstance(result, ApprovalReplayError) for result in results), 1)
        self.assertEqual(self.tool.calls, 1)

    async def test_modified_arguments_fail_without_invoking_tool(self) -> None:
        approval = await self._approved()

        with self.assertRaises(ApprovalMismatchError):
            await self.service.execute(
                approval_id=approval.id,
                tool=self.tool,
                args=Arguments(amount=26, note="approved"),
                ctx=self.context,
            )

        self.assertEqual(self.tool.calls, 0)

    async def test_different_tool_fails_without_invoking_tool(self) -> None:
        approval = await self._approved()
        other_tool = DifferentTool()

        with self.assertRaises(ApprovalMismatchError):
            await self.service.execute(
                approval_id=approval.id,
                tool=other_tool,
                args=self.args,
                ctx=self.context,
            )

        self.assertEqual(other_tool.calls, 0)

    async def test_expired_approval_fails_and_is_audited(self) -> None:
        approval = await self._approved(expires_at=self.now + timedelta(seconds=1))
        self.now += timedelta(seconds=1)

        with self.assertRaises(ApprovalExpiredError):
            await self.service.execute(
                approval_id=approval.id,
                tool=self.tool,
                args=self.args,
                ctx=self.context,
            )

        self.assertEqual(self.tool.calls, 0)
        self.assertEqual(self.repository.approvals[approval.id].status, "expired")
        self.assertEqual(self.repository.audit_events[-1].transition, "expired")

    async def test_approval_scope_cannot_be_widened_or_supplied_by_args(self) -> None:
        with self.assertRaises(ApprovalScopeError):
            await self.service.create(
                tool_name=self.tool.name,
                args=self.args,
                ctx=Context(scope_ids=frozenset({"scope-1", "scope-2"})),
            )

        approval = await self._approved()
        with self.assertRaises(ApprovalScopeError):
            await self.service.execute(
                approval_id=approval.id,
                tool=self.tool,
                args=self.args,
                ctx=Context(scope_ids=frozenset({"scope-2"})),
            )
        self.assertEqual(self.tool.calls, 0)

    async def test_canonical_hash_is_stable_and_exposed(self) -> None:
        self.assertEqual(
            canonical_args_hash(Arguments(amount=1, note="a")),
            canonical_args_hash(Arguments(note="a", amount=1)),
        )

    async def test_concrete_server_context_and_enterprise_tool_are_compatible(self) -> None:
        context = ExecutionContext(
            user_id="user-1",
            correlation_id="correlation-1",
            roles=frozenset(),
            scope_ids=frozenset({"scope-1"}),
            deadline_utc=self.now + timedelta(minutes=5),
        )
        self.assertIs(ApprovalContext, ExecutionContextProtocol)
        self.assertIs(ApprovalTool, EnterpriseTool)
        approval = await self.service.create(
            tool_name=self.tool.name, args=self.args, ctx=context
        )
        await self.service.approve(approval_id=approval.id, ctx=context)
        result = await self.service.execute(
            approval_id=approval.id, tool=self.tool, args=self.args, ctx=context
        )
        self.assertEqual(result, Result(completed=True))
        self.assertEqual(self.tool.calls, 1)


class _ScalarResult:
    def scalar_one_or_none(self) -> None:
        return None


class _RecordingSession:
    def __init__(self) -> None:
        self.statement = None

    async def execute(self, statement: object) -> _ScalarResult:
        self.statement = statement
        return _ScalarResult()


class ApprovalRepositoryTests(IsolatedAsyncioTestCase):
    async def test_get_for_update_uses_postgresql_row_lock(self) -> None:
        session = _RecordingSession()
        repository = SQLAlchemyApprovalRepository(cast(AsyncSession, session))

        await repository.get_for_update(UUID(int=1))

        self.assertIsNotNone(session.statement)
        sql = str(session.statement.compile(dialect=postgresql.dialect()))
        self.assertIn("FOR UPDATE", sql)
