"""Model-chosen tool calls in the chat path go through policy, approvals and limits."""

from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar
from uuid import UUID

import pytest
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from accelerator.agent_core.middleware import ToolCallLimitExceeded, ToolCallLimits
from accelerator.agent_core.tools import (
    EnterpriseTool,
    ExecutionContextProtocol,
    IdempotentWriteTool,
    ToolRegistry,
    ToolRisk,
)
from accelerator.agent_core.tools.agent_bridge import tools_for_current_turn
from accelerator.agent_core.workflows.grounded_answer import Abstention, GroundedAnswerResult
from accelerator.api.tool_turns import PolicyEnforcedChatTurn
from accelerator.security_core.data_boundaries.context import ExecutionContext
from accelerator.security_core.infrastructure.database import create_session_factory
from accelerator.security_core.tool_policy import ApprovalRequired


class Args(BaseModel):
    value: str


class Out(BaseModel):
    value: str


class Lookup(EnterpriseTool[Args, Out]):
    name: ClassVar[str] = "lookup"
    description: ClassVar[str] = "Read-only lookup."
    risk: ClassVar[ToolRisk] = ToolRisk.READ_ONLY
    args_model: ClassVar[type[BaseModel]] = Args

    async def execute(self, args: Args, ctx: ExecutionContextProtocol) -> Out:
        return Out(value=f"{ctx.user_id}:{args.value}")


class Update(IdempotentWriteTool[Args, Out]):
    name: ClassVar[str] = "update_record"
    description: ClassVar[str] = "Write a record."
    risk: ClassVar[ToolRisk] = ToolRisk.LOW_IMPACT_WRITE
    args_model: ClassVar[type[BaseModel]] = Args
    executions = 0

    async def execute_approved(
        self, args: Args, ctx: ExecutionContextProtocol, *, execution_id: UUID
    ) -> Out:
        Update.executions += 1
        return Out(value="written")


ABSTAINED = GroundedAnswerResult(
    status="abstained",
    answer=None,
    citations=(),
    citation_sources=(),
    abstention=Abstention(reason="No evidence.", evidence_ids=()),
)


class ModelCallingTools:
    """Stands in for a workflow whose model chooses these tool calls."""

    def __init__(self, *calls: tuple[str, dict[str, Any]]) -> None:
        self.calls = calls
        self.outputs: list[str] = []

    async def run(self, query: str, ctx: ExecutionContext) -> GroundedAnswerResult:
        offered = {tool.name: tool for tool in tools_for_current_turn()}
        for name, arguments in self.calls:
            result = await offered[name].invoke(arguments=arguments)
            self.outputs.append("".join(item.text or "" for item in result))
        return ABSTAINED


def context() -> ExecutionContext:
    return ExecutionContext(
        correlation_id="5d1b8f2e-7a43-4c55-9d1e-3f0f6b9a0c11",
        user_id="requester",
        roles=frozenset({"Contributor"}),
        scope_ids=frozenset({"scope-a"}),
        deadline_utc=datetime.now(UTC) + timedelta(minutes=1),
    )


def registry() -> ToolRegistry:
    tools = ToolRegistry()
    tools.register(Lookup())
    tools.register(Update())
    return tools


async def test_without_registered_tools_the_workflow_runs_unchanged() -> None:
    turn = PolicyEnforcedChatTurn(
        ModelCallingTools(), ToolRegistry(), session_factory=None, limits=ToolCallLimits()
    )

    assert await turn.run("q", context()) is ABSTAINED


async def test_read_runs_and_write_becomes_a_persisted_pending_approval(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    workflow = ModelCallingTools(
        ("lookup", {"value": "a"}), ("update_record", {"value": "b", "scope_id": "scope-b"})
    )
    turn = PolicyEnforcedChatTurn(
        workflow, registry(), session_factory=create_session_factory(engine), limits=ToolCallLimits()
    )
    try:
        result = await turn.run("Please update the record", context())
        async with engine.connect() as connection:
            approvals = (
                await connection.execute(
                    text("SELECT tool_name, status, requested_by, scope_id FROM approvals")
                )
            ).all()
    finally:
        await engine.dispose()

    assert workflow.outputs[0] == '{"value":"requester:a"}'
    assert isinstance(result, ApprovalRequired)
    assert result.tool_name == "update_record"
    assert result.scope_id == "scope-a"
    assert [tuple(row) for row in approvals] == [
        ("update_record", "pending", "requester", "scope-a")
    ]
    assert Update.executions == 0


async def test_tool_call_limits_apply_to_model_chosen_calls(migrated_database_url: str) -> None:
    engine = create_async_engine(migrated_database_url)
    turn = PolicyEnforcedChatTurn(
        ModelCallingTools(("lookup", {"value": "1"}), ("lookup", {"value": "2"})),
        registry(),
        session_factory=create_session_factory(engine),
        limits=ToolCallLimits(max_calls_per_turn=1),
    )
    try:
        with pytest.raises(ToolCallLimitExceeded):
            await turn.run("q", context())
    finally:
        await engine.dispose()
