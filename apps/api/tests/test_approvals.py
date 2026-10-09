import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar, cast
from uuid import UUID

import pytest
from pydantic import BaseModel
from sqlalchemy.dialects import postgresql
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from accelerator.agent_core.approvals import (
    ApprovalAuthorizationError,
    Approval,
    ApprovalAuditEvent,
    ApprovalContext,
    ApprovalExpiredError,
    ApprovalMismatchError,
    ApprovalReplayError,
    ApprovalScopeError,
    ApprovalService,
    ApprovalStateError,
    ApprovalTool,
    canonical_args_hash,
)
from accelerator.agent_core.tools import (
    EnterpriseTool,
    ExecutionContextProtocol,
    IdempotentWriteTool,
    ToolRisk,
)
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


APPROVER = Context(user_id="approver-1", roles=frozenset({"Approver"}))


class Arguments(BaseModel):
    amount: int
    note: str


class Result(BaseModel):
    completed: bool


class Tool(IdempotentWriteTool[Arguments, Result]):
    name: ClassVar[str] = "write_record"
    description: ClassVar[str] = "Write a record after approval."
    risk: ClassVar[ToolRisk] = ToolRisk.LOW_IMPACT_WRITE
    args_model: ClassVar[type[BaseModel]] = Arguments

    def __init__(self) -> None:
        self.calls = 0
        self.execution_ids: list[UUID] = []

    async def execute_approved(
        self, args: Arguments, ctx: ExecutionContextProtocol, *, execution_id: UUID
    ) -> Result:
        self.calls += 1
        self.execution_ids.append(execution_id)
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


class Clock:
    """Mutable, timezone-aware test clock injected into ``ApprovalService``."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock(datetime(2026, 10, 7, 12, 0, tzinfo=UTC))


@pytest.fixture
def repository() -> InMemoryApprovalRepository:
    return InMemoryApprovalRepository()


@pytest.fixture
def service(
    repository: InMemoryApprovalRepository, clock: Clock
) -> ApprovalService[Arguments, Result]:
    return ApprovalService[Arguments, Result](repository, clock=clock)


@pytest.fixture
def context() -> Context:
    return Context()


@pytest.fixture
def args() -> Arguments:
    return Arguments(amount=25, note="approved")


@pytest.fixture
def tool() -> Tool:
    return Tool()


async def _approved(
    service: ApprovalService[Arguments, Result],
    *,
    tool: Tool,
    args: Arguments,
    context: Context,
    expires_at: datetime | None = None,
) -> Approval:
    approval = await service.create(
        tool_name=tool.name,
        args=args,
        ctx=context,
        expires_at=expires_at,
    )
    return await service.approve(approval_id=approval.id, ctx=APPROVER)


async def test_execute_with_matching_approval_succeeds_once(
    service: ApprovalService[Arguments, Result],
    repository: InMemoryApprovalRepository,
    tool: Tool,
    args: Arguments,
    context: Context,
) -> None:
    approval = await _approved(service, tool=tool, args=args, context=context)

    result = await service.execute(approval_id=approval.id, tool=tool, args=args, ctx=context)

    assert result == Result(completed=True)
    assert tool.calls == 1
    assert repository.approvals[approval.id].status == "executed"
    assert [event.transition for event in repository.audit_events] == [
        "pending",
        "approved",
        "executed",
    ]


async def test_replay_fails_without_invoking_tool_again(
    service: ApprovalService[Arguments, Result],
    tool: Tool,
    args: Arguments,
    context: Context,
) -> None:
    approval = await _approved(service, tool=tool, args=args, context=context)
    await service.execute(approval_id=approval.id, tool=tool, args=args, ctx=context)

    with pytest.raises(ApprovalReplayError):
        await service.execute(approval_id=approval.id, tool=tool, args=args, ctx=context)

    assert tool.calls == 1


async def test_concurrent_replay_executes_tool_only_once(
    service: ApprovalService[Arguments, Result],
    tool: Tool,
    args: Arguments,
    context: Context,
) -> None:
    approval = await _approved(service, tool=tool, args=args, context=context)

    async def execute() -> BaseModel | ApprovalReplayError:
        try:
            return await service.execute(approval_id=approval.id, tool=tool, args=args, ctx=context)
        except ApprovalReplayError as error:
            return error

    results = await asyncio.gather(execute(), execute())

    assert sum(isinstance(result, Result) for result in results) == 1
    assert sum(isinstance(result, ApprovalReplayError) for result in results) == 1
    assert tool.calls == 1


async def test_modified_arguments_fail_without_invoking_tool(
    service: ApprovalService[Arguments, Result],
    tool: Tool,
    args: Arguments,
    context: Context,
) -> None:
    approval = await _approved(service, tool=tool, args=args, context=context)

    with pytest.raises(ApprovalMismatchError):
        await service.execute(
            approval_id=approval.id,
            tool=tool,
            args=Arguments(amount=26, note="approved"),
            ctx=context,
        )

    assert tool.calls == 0


async def test_different_tool_fails_without_invoking_tool(
    service: ApprovalService[Arguments, Result],
    tool: Tool,
    args: Arguments,
    context: Context,
) -> None:
    approval = await _approved(service, tool=tool, args=args, context=context)
    other_tool = DifferentTool()

    with pytest.raises(ApprovalMismatchError):
        await service.execute(approval_id=approval.id, tool=other_tool, args=args, ctx=context)

    assert other_tool.calls == 0


async def test_expired_approval_fails_and_is_audited(
    service: ApprovalService[Arguments, Result],
    repository: InMemoryApprovalRepository,
    clock: Clock,
    tool: Tool,
    args: Arguments,
    context: Context,
) -> None:
    approval = await _approved(
        service, tool=tool, args=args, context=context, expires_at=clock.now + timedelta(seconds=1)
    )
    clock.now += timedelta(seconds=1)

    with pytest.raises(ApprovalExpiredError):
        await service.execute(approval_id=approval.id, tool=tool, args=args, ctx=context)

    assert tool.calls == 0
    assert repository.approvals[approval.id].status == "expired"
    assert repository.audit_events[-1].transition == "expired"


@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_expiry_at_decision_time_fails_before_any_approval_or_rejection(
    service: ApprovalService[Arguments, Result],
    repository: InMemoryApprovalRepository,
    clock: Clock,
    tool: Tool,
    args: Arguments,
    context: Context,
    decision: str,
) -> None:
    approval = await service.create(
        tool_name=tool.name,
        args=args,
        ctx=context,
        expires_at=clock.now + timedelta(seconds=1),
    )
    clock.now += timedelta(seconds=1)

    with pytest.raises(ApprovalExpiredError):
        await getattr(service, decision)(approval_id=approval.id, ctx=APPROVER)

    assert repository.approvals[approval.id].status == "expired"
    assert repository.approvals[approval.id].decided_by is None
    assert [event.transition for event in repository.audit_events] == ["pending", "expired"]

    # Rejecting an already-expired, never-decided approval must also fail the
    # same way, rather than silently transitioning to "rejected".
    with pytest.raises(ApprovalExpiredError):
        await service.reject(approval_id=approval.id, ctx=APPROVER)

    assert repository.approvals[approval.id].status == "expired"


class DurableTool(Tool):
    """Test downstream boundary: effect and result share one durable transaction."""

    def __init__(self, database: Path) -> None:
        super().__init__()
        self.database = database

    async def execute_approved(
        self, args: Arguments, ctx: ExecutionContextProtocol, *, execution_id: UUID
    ) -> Result:
        self.calls += 1
        self.execution_ids.append(execution_id)
        assert len(ctx.scope_ids) == 1
        scope = next(iter(ctx.scope_ids))
        engine = create_async_engine(f"sqlite+aiosqlite:///{self.database.as_posix()}")
        try:
            async with engine.begin() as connection:
                await connection.execute(text("BEGIN IMMEDIATE"))
                await connection.execute(
                    text(
                        "CREATE TABLE IF NOT EXISTS writes ("
                        "execution_id TEXT NOT NULL, scope TEXT NOT NULL, "
                        "args_hash TEXT NOT NULL, amount INTEGER NOT NULL, result TEXT NOT NULL, "
                        "PRIMARY KEY (execution_id, scope))"
                    )
                )
                stored = (
                    await connection.execute(
                        text(
                            "SELECT args_hash, result FROM writes "
                            "WHERE execution_id = :id AND scope = :scope"
                        ),
                        {"id": str(execution_id), "scope": scope},
                    )
                ).one_or_none()
                if stored is not None:
                    assert stored.args_hash == canonical_args_hash(args)
                    return Result.model_validate_json(stored.result)
                result = Result(completed=True)
                await connection.execute(
                    text(
                        "INSERT INTO writes VALUES (:id, :scope, :hash, :amount, :result)"
                    ),
                    {
                        "id": str(execution_id),
                        "scope": scope,
                        "hash": canonical_args_hash(args),
                        "amount": args.amount,
                        "result": result.model_dump_json(),
                    },
                )
                return result
        finally:
            await engine.dispose()


@pytest.mark.parametrize("failure", ["update", "audit", "commit", "response"])
async def test_recovery_reuses_durable_execution_key_without_repeating_external_write(
    clock: Clock, args: Arguments, context: Context, failure: str, tmp_path: Path
) -> None:
    class FailingRepository(InMemoryApprovalRepository):
        fail = False

        @asynccontextmanager
        async def transaction(self) -> AsyncIterator[None]:
            async with super().transaction():
                yield
                if self.fail and failure == "commit":
                    raise RuntimeError("injected commit failure")

        async def update(self, approval: Approval) -> None:
            if self.fail and failure == "update":
                raise RuntimeError("injected update failure")
            await super().update(approval)

        async def add_audit_event(self, event: ApprovalAuditEvent) -> None:
            if self.fail and failure == "audit":
                raise RuntimeError("injected audit failure")
            await super().add_audit_event(event)

    class LostResponseTool(DurableTool):
        async def execute_approved(
            self, args: Arguments, ctx: ExecutionContextProtocol, *, execution_id: UUID
        ) -> Result:
            result = await super().execute_approved(args, ctx, execution_id=execution_id)
            if failure == "response":
                raise RuntimeError("injected response failure")
            return result

    database = tmp_path / "downstream.db"
    tool = LostResponseTool(database)
    repository = FailingRepository()
    service = ApprovalService[Arguments, Result](repository, clock=clock)
    approval = await _approved(service, tool=tool, args=args, context=context)
    repository.fail = True

    with pytest.raises(RuntimeError, match=f"injected {failure} failure"):
        await service.execute(approval_id=approval.id, tool=tool, args=args, ctx=context)

    assert tool.calls == 1
    assert repository.approvals[approval.id].status == "approved"
    assert [event.transition for event in repository.audit_events] == ["pending", "approved"]
    repository.fail = False
    recovered_service = ApprovalService[Arguments, Result](repository, clock=clock)
    recovered_tool = DurableTool(database)
    with pytest.raises(ApprovalMismatchError):
        await recovered_service.execute(
            approval_id=approval.id,
            tool=recovered_tool,
            args=Arguments(amount=args.amount + 1, note=args.note),
            ctx=context,
        )
    with pytest.raises(ApprovalScopeError):
        await recovered_service.execute(
            approval_id=approval.id,
            tool=recovered_tool,
            args=args,
            ctx=Context(scope_ids=frozenset({"scope-2"})),
        )
    assert recovered_tool.calls == 0
    result = await recovered_service.execute(
        approval_id=approval.id, tool=recovered_tool, args=args, ctx=context
    )
    assert result == Result(completed=True)
    assert tool.execution_ids == recovered_tool.execution_ids == [approval.id]
    assert repository.approvals[approval.id].status == "executed"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database.as_posix()}")
    try:
        async with engine.connect() as connection:
            row = (await connection.execute(text("SELECT COUNT(*), SUM(amount) FROM writes"))).one()
            assert tuple(row) == (1, args.amount)
    finally:
        await engine.dispose()
    with pytest.raises(ApprovalReplayError):
        await recovered_service.execute(
            approval_id=approval.id, tool=recovered_tool, args=args, ctx=context
        )
    assert recovered_tool.calls == 1
    assert repository.approvals[approval.id].status == "executed"


async def test_validate_approval_receives_persisted_record_before_invocation(
    service: ApprovalService[Arguments, Result],
    tool: Tool,
    args: Arguments,
    context: Context,
) -> None:
    approval = await _approved(service, tool=tool, args=args, context=context)
    seen: list[Approval] = []

    def validate_approval(persisted: Approval) -> None:
        seen.append(persisted)

    result = await service.execute(
        approval_id=approval.id,
        tool=tool,
        args=args,
        ctx=context,
        validate_approval=validate_approval,
    )

    assert result == Result(completed=True)
    assert tool.calls == 1
    assert len(seen) == 1
    # The callback must see the authoritative, locked, persisted row - not a
    # caller-supplied value - including server-decided fields.
    assert seen[0].id == approval.id
    assert seen[0].status == "approved"
    assert seen[0].requested_by == context.user_id
    assert seen[0].decided_by == APPROVER.user_id
    assert seen[0].scope_id == next(iter(context.scope_ids))


async def test_validate_approval_rejection_prevents_execution_and_commits_no_change(
    service: ApprovalService[Arguments, Result],
    repository: InMemoryApprovalRepository,
    tool: Tool,
    args: Arguments,
    context: Context,
) -> None:
    approval = await _approved(service, tool=tool, args=args, context=context)

    class PolicyDenied(Exception):
        pass

    def validate_approval(_: Approval) -> None:
        raise PolicyDenied("privileged role required")

    with pytest.raises(PolicyDenied):
        await service.execute(
            approval_id=approval.id,
            tool=tool,
            args=args,
            ctx=context,
            validate_approval=validate_approval,
        )

    # Tool must never be invoked, and the approval remains "approved" (not
    # "executed") so a corrected/validated retry can still proceed.
    assert tool.calls == 0
    assert repository.approvals[approval.id].status == "approved"
    assert [event.transition for event in repository.audit_events] == ["pending", "approved"]


async def test_approval_scope_cannot_be_widened_or_supplied_by_args(
    service: ApprovalService[Arguments, Result],
    tool: Tool,
    args: Arguments,
    context: Context,
) -> None:
    with pytest.raises(ApprovalScopeError):
        await service.create(
            tool_name=tool.name,
            args=args,
            ctx=Context(scope_ids=frozenset({"scope-1", "scope-2"})),
        )

    approval = await _approved(service, tool=tool, args=args, context=context)
    with pytest.raises(ApprovalScopeError):
        await service.execute(
            approval_id=approval.id,
            tool=tool,
            args=args,
            ctx=Context(scope_ids=frozenset({"scope-2"})),
        )
    assert tool.calls == 0

    with pytest.raises(ApprovalScopeError):
        await service.execute(
            approval_id=approval.id,
            tool=tool,
            args=args,
            ctx=Context(scope_ids=frozenset({"scope-1", "scope-2"})),
        )
    assert tool.calls == 0


async def test_plain_write_tool_cannot_bypass_idempotent_boundary(
    service: ApprovalService[Arguments, Result], args: Arguments, context: Context, tool: Tool
) -> None:
    class UnsafeTool(EnterpriseTool[Arguments, Result]):
        name = tool.name
        description = tool.description
        risk = tool.risk
        args_model = Arguments

        async def execute(self, args: Arguments, ctx: ExecutionContextProtocol) -> Result:
            pytest.fail("non-idempotent tool must not execute")

    approval = await _approved(service, tool=tool, args=args, context=context)
    with pytest.raises(ApprovalStateError, match="IdempotentWriteTool"):
        await service.execute(
            approval_id=approval.id, tool=UnsafeTool(), args=args, ctx=context
        )


async def test_canonical_hash_is_stable_and_exposed() -> None:
    assert canonical_args_hash(Arguments(amount=1, note="a")) == canonical_args_hash(
        Arguments(note="a", amount=1)
    )


async def test_concrete_server_context_and_enterprise_tool_are_compatible(
    service: ApprovalService[Arguments, Result],
    clock: Clock,
    tool: Tool,
    args: Arguments,
) -> None:
    context = ExecutionContext(
        user_id="user-1",
        correlation_id="correlation-1",
        roles=frozenset(),
        scope_ids=frozenset({"scope-1"}),
        deadline_utc=clock.now + timedelta(minutes=5),
    )
    assert ApprovalContext is ExecutionContextProtocol
    assert ApprovalTool is EnterpriseTool
    approval = await service.create(tool_name=tool.name, args=args, ctx=context)
    await service.approve(approval_id=approval.id, ctx=APPROVER)
    result = await service.execute(approval_id=approval.id, tool=tool, args=args, ctx=context)
    assert result == Result(completed=True)
    assert tool.calls == 1


class _ScalarResult:
    def scalar_one_or_none(self) -> None:
        return None


class _RecordingSession:
    def __init__(self) -> None:
        self.statement: object | None = None

    async def execute(self, statement: object) -> _ScalarResult:
        self.statement = statement
        return _ScalarResult()


async def test_get_for_update_uses_postgresql_row_lock() -> None:
    session = _RecordingSession()
    repository = SQLAlchemyApprovalRepository(cast(AsyncSession, session))

    await repository.get_for_update(UUID(int=1))

    assert session.statement is not None
    sql = str(session.statement.compile(dialect=postgresql.dialect()))  # type: ignore[attr-defined]
    assert "FOR UPDATE" in sql


# --- Real PostgreSQL two-session row-lock concurrency evidence ---------------
#
# The tests above prove the service's own locking *contract* (every mutation
# happens inside one ``repository.transaction()``), using an in-memory
# repository whose ``asyncio.Lock`` only serializes callers *within this one
# process*. That is not evidence that the real adapter is safe across two
# independent database connections/sessions. Downstream idempotency is tested
# separately above. The test below exercises the concrete
# ``SQLAlchemyApprovalRepository`` against a real PostgreSQL server using two
# separate engines/sessions (i.e. two separate backend connections), and
# measures that the loser's ``SELECT ... FOR UPDATE`` genuinely blocks at the
# database for the duration the winner holds the row, rather than merely
# observing a final state that could also result from in-process ordering.
#
# It runs against a freshly migrated database whenever TEST_POSTGRES_DSN is set
# (always in CI); otherwise the shared fixture skips it.

LOCK_HOLD_SECONDS = 0.4


class TimingRepository(SQLAlchemyApprovalRepository):
    """Records how long each ``SELECT ... FOR UPDATE`` call actually took.

    Used only to produce direct evidence that a concurrent call blocked at
    the database rather than merely observing a final, already-decided
    state.
    """

    def __init__(self, session: AsyncSession, timings: list[float]) -> None:
        super().__init__(session)
        self._timings = timings

    async def get_for_update(self, approval_id: UUID) -> Approval | None:
        start = time.monotonic()
        try:
            return await super().get_for_update(approval_id)
        finally:
            self._timings.append(time.monotonic() - start)


class SlowTool(Tool):
    """Holds its approval's row lock open for ``LOCK_HOLD_SECONDS``.

    This keeps the winning session's transaction (and therefore its
    ``FOR UPDATE`` row lock) open long enough that the losing session's
    ``get_for_update`` can only be unblocked by a real database commit, not
    by asyncio task scheduling.
    """

    async def execute_approved(
        self, args: Arguments, ctx: ExecutionContextProtocol, *, execution_id: UUID
    ) -> Result:
        await asyncio.sleep(LOCK_HOLD_SECONDS)
        return await super().execute_approved(args, ctx, execution_id=execution_id)


async def test_postgresql_row_lock_serializes_concurrent_execute_across_two_sessions(
    migrated_database_url: str,
) -> None:
    engine_a = create_async_engine(migrated_database_url)
    engine_b = create_async_engine(migrated_database_url)
    try:
        session_factory_a = async_sessionmaker(engine_a, expire_on_commit=False)
        session_factory_b = async_sessionmaker(engine_b, expire_on_commit=False)
        async with session_factory_a() as session_a, session_factory_b() as session_b:
            timings_a: list[float] = []
            timings_b: list[float] = []
            repository_a = TimingRepository(session_a, timings_a)
            repository_b = TimingRepository(session_b, timings_b)
            clock = Clock(datetime(2026, 10, 7, 12, 0, tzinfo=UTC))
            service_a = ApprovalService[Arguments, Result](repository_a, clock=clock)
            service_b = ApprovalService[Arguments, Result](repository_b, clock=clock)
            context = Context()
            args = Arguments(amount=25, note="approved")
            tool = SlowTool()

            approval = await service_a.create(tool_name=tool.name, args=args, ctx=context)
            approval = await service_a.approve(approval_id=approval.id, ctx=APPROVER)

            async def run(
                service: ApprovalService[Arguments, Result],
            ) -> BaseModel | ApprovalReplayError:
                try:
                    return await service.execute(
                        approval_id=approval.id, tool=tool, args=args, ctx=context
                    )
                except ApprovalReplayError as error:
                    return error

            started = time.monotonic()
            results = await asyncio.gather(run(service_a), run(service_b))
            elapsed = time.monotonic() - started

            # Exactly one session executed the tool; the other was told it
            # had already been replayed once it finally acquired the lock.
            assert sum(isinstance(result, Result) for result in results) == 1
            assert sum(isinstance(result, ApprovalReplayError) for result in results) == 1
            assert tool.calls == 1

            # Direct evidence of real cross-session blocking: the loser's
            # own SELECT ... FOR UPDATE call took roughly as long as the
            # winner held the row locked, and the whole exchange could not
            # have completed faster than one lock hold.
            slowest_get_for_update = max(timings_a[0], timings_b[0])
            assert slowest_get_for_update >= LOCK_HOLD_SECONDS * 0.8
            assert elapsed >= LOCK_HOLD_SECONDS * 0.8
    finally:
        await engine_a.dispose()
        await engine_b.dispose()


@pytest.mark.parametrize(
    ("decider", "message"),
    [
        (Context(user_id="approver-1"), "Approver role"),
        (Context(roles=frozenset({"Approver"})), "requester cannot decide"),
    ],
)
@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_decisions_need_an_approver_who_is_not_the_requester(
    service: ApprovalService[Arguments, Result],
    repository: InMemoryApprovalRepository,
    tool: Tool,
    args: Arguments,
    context: Context,
    decider: Context,
    message: str,
    decision: str,
) -> None:
    approval = await service.create(tool_name=tool.name, args=args, ctx=context)

    with pytest.raises(ApprovalAuthorizationError, match=message):
        await getattr(service, decision)(approval_id=approval.id, ctx=decider)

    assert repository.approvals[approval.id].status == "pending"
    assert [event.transition for event in repository.audit_events] == ["pending"]
