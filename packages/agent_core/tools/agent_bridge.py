"""Expose registered ``EnterpriseTool``s to an agent only through tool policy.

The agent sees one ``FunctionTool`` per registered tool. Calling it never executes
the tool directly: arguments are validated against the tool's Pydantic model and
handed to the policy middleware together with the server-side execution context
for this turn. Write tools therefore yield an ``ApprovalRequired`` card instead of
running, and every call counts against the per-turn and per-session limits.
"""

from collections.abc import Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Protocol

from agent_framework import FunctionTool
from pydantic import BaseModel

from accelerator.security_core.tool_policy import ApprovalRequired

from .base import EnterpriseTool, ExecutionContextProtocol

TURN_PAUSED_MESSAGE = (
    "An approval request is already pending for this turn. No further tools can run "
    "until a person decides it; tell the user."
)
APPROVAL_PENDING_MESSAGE = (
    "This action requires human approval. An approval request was created; "
    "tell the user it is pending. Do not call the tool again."
)


class PolicyInvoker(Protocol):
    async def invoke(
        self,
        tool: EnterpriseTool[Any, Any],
        arguments: Any,
        context: ExecutionContextProtocol,
        *,
        turn_id: str,
    ) -> Any: ...


@dataclass
class ToolTurn:
    """Everything a tool call in this turn may use; built by the server, never the model."""

    invoker: PolicyInvoker
    context: ExecutionContextProtocol
    turn_id: str
    tools: Sequence[EnterpriseTool[Any, Any]]
    approvals: list[ApprovalRequired[Any]] = field(default_factory=list)


current_tool_turn: ContextVar[ToolTurn | None] = ContextVar("current_tool_turn", default=None)


def as_agent_tool(tool: EnterpriseTool[Any, Any], turn: ToolTurn) -> FunctionTool:
    async def invoke(**arguments: Any) -> str:
        if turn.approvals:
            # One approval per turn: the model cannot queue further (hidden) requests
            # or keep acting after it asked a person to decide.
            return TURN_PAUSED_MESSAGE
        validated = tool.args_model.model_validate(arguments)
        result = await turn.invoker.invoke(tool, validated, turn.context, turn_id=turn.turn_id)
        if isinstance(result, ApprovalRequired):
            turn.approvals.append(result)
            return APPROVAL_PENDING_MESSAGE
        if not isinstance(result, BaseModel):
            raise TypeError(f"Tool {tool.name!r} returned a non-model result")
        return result.model_dump_json()

    return FunctionTool(
        name=tool.name,
        description=tool.description,
        func=invoke,
        input_model=tool.args_model,
    )


def tools_for_current_turn() -> list[FunctionTool]:
    """Agent tools for the active turn, or none when no turn (or no tool) is active."""
    turn = current_tool_turn.get()
    if turn is None:
        return []
    return [as_agent_tool(tool, turn) for tool in turn.tools]
