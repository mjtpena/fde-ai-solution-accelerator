from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar
from uuid import UUID, uuid4

from pydantic import BaseModel

from accelerator.security_core.data_boundaries.context import ExecutionContext
from accelerator.security_core.tool_policy import ApprovalRequired

from ..agent_bridge import (
    APPROVAL_PENDING_MESSAGE,
    ToolTurn,
    as_agent_tool,
    current_tool_turn,
    tools_for_current_turn,
)
from ..base import EnterpriseTool, ExecutionContextProtocol, IdempotentWriteTool, ToolRisk


class LookupArgs(BaseModel):
    term: str


class LookupResult(BaseModel):
    found: bool


class Lookup(EnterpriseTool[LookupArgs, LookupResult]):
    name: ClassVar[str] = "lookup"
    description: ClassVar[str] = "Read-only lookup."
    risk: ClassVar[ToolRisk] = ToolRisk.READ_ONLY
    args_model: ClassVar[type[BaseModel]] = LookupArgs

    async def execute(self, args: LookupArgs, ctx: ExecutionContextProtocol) -> LookupResult:
        raise AssertionError("the bridge must never call execute directly")


class Update(IdempotentWriteTool[LookupArgs, LookupResult]):
    name: ClassVar[str] = "update"
    description: ClassVar[str] = "A write."
    risk: ClassVar[ToolRisk] = ToolRisk.LOW_IMPACT_WRITE
    args_model: ClassVar[type[BaseModel]] = LookupArgs

    async def execute_approved(
        self, args: LookupArgs, ctx: ExecutionContextProtocol, *, execution_id: UUID
    ) -> LookupResult:
        raise AssertionError("the bridge must never execute a write")


def text_of(result: Any) -> str:
    return "".join(getattr(item, "text", "") or "" for item in result)


def server_context() -> ExecutionContext:
    return ExecutionContext(
        correlation_id="turn-correlation",
        user_id="user-1",
        roles=frozenset({"Reader"}),
        scope_ids=frozenset({"scope-a"}),
        session_id="session-1",
        deadline_utc=datetime.now(UTC) + timedelta(minutes=1),
    )


class RecordingInvoker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, BaseModel, ExecutionContextProtocol, str]] = []

    async def invoke(
        self,
        tool: EnterpriseTool[Any, Any],
        arguments: Any,
        context: ExecutionContextProtocol,
        *,
        turn_id: str,
    ) -> Any:
        self.calls.append((tool.name, arguments, context, turn_id))
        if tool.risk is ToolRisk.READ_ONLY:
            return LookupResult(found=True)
        return ApprovalRequired(
            approval_id=uuid4(),
            tool_name=tool.name,
            arguments=arguments,
            args_hash="0" * 64,
            scope_id="scope-a",
            requested_by=context.user_id,
            correlation_id=context.correlation_id,
        )


async def test_agent_tool_calls_go_through_policy_with_server_context() -> None:
    invoker = RecordingInvoker()
    context = server_context()
    turn = ToolTurn(invoker=invoker, context=context, turn_id="turn-1", tools=[Lookup()])

    result = await as_agent_tool(Lookup(), turn).invoke(arguments={"term": "x"})

    assert text_of(result) == '{"found":true}'
    [(name, arguments, seen_context, turn_id)] = invoker.calls
    assert name == "lookup"
    assert arguments == LookupArgs(term="x")
    assert seen_context is context
    assert turn_id == "turn-1"


async def test_write_tool_call_becomes_a_pending_approval_not_a_write() -> None:
    turn = ToolTurn(
        invoker=RecordingInvoker(), context=server_context(), turn_id="t", tools=[Update()]
    )

    result = await as_agent_tool(Update(), turn).invoke(arguments={"term": "x"})

    assert text_of(result) == APPROVAL_PENDING_MESSAGE
    [approval] = turn.approvals
    assert approval.tool_name == "update"


async def test_model_supplied_scope_never_reaches_the_tool_or_context() -> None:
    invoker = RecordingInvoker()
    context = server_context()
    turn = ToolTurn(invoker=invoker, context=context, turn_id="t", tools=[Lookup()])

    await as_agent_tool(Lookup(), turn).invoke(arguments={"term": "x", "scope_id": "scope-b"})

    [(_, arguments, seen_context, _)] = invoker.calls
    assert arguments.model_dump() == {"term": "x"}
    assert seen_context.scope_ids == frozenset({"scope-a"})


def test_tools_are_offered_only_inside_an_active_turn() -> None:
    assert tools_for_current_turn() == []
    turn = ToolTurn(
        invoker=RecordingInvoker(), context=server_context(), turn_id="t", tools=[Lookup()]
    )
    token = current_tool_turn.set(turn)
    try:
        [offered] = tools_for_current_turn()
    finally:
        current_tool_turn.reset(token)

    assert offered.name == "lookup"
    assert tools_for_current_turn() == []
