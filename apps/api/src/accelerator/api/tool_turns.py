"""Run chat turns with registered tools behind ``ToolPolicyMiddleware``.

For each turn this builds a policy middleware bound to a request-scoped approval
service and the shared (PostgreSQL) tool-call counter, publishes it with the
server-side execution context as the current ``ToolTurn``, and runs the workflow.
The answer generator offers the registered tools to the model only through
``agent_bridge``, so a model-chosen write produces an approval card, never a write.
"""

import logging
from typing import Any

from pydantic import BaseModel

from accelerator.agent_core.approvals import APPROVER_ROLE, ApprovalService
from accelerator.agent_core.middleware import ToolCallLimits, ToolPolicyMiddleware
from accelerator.agent_core.tools import ToolRegistry
from accelerator.agent_core.tools.agent_bridge import ToolTurn, current_tool_turn
from accelerator.agent_core.workflows.grounded_answer import GroundedAnswerResult
from accelerator.api.chat import ChatTurnPort
from accelerator.infrastructure.approvals import SQLAlchemyApprovalRepository
from accelerator.infrastructure.cost_controls import PostgresToolCallCounter
from accelerator.security_core.data_boundaries.context import ExecutionContext
from accelerator.security_core.infrastructure.database import SessionFactory
from accelerator.security_core.tool_policy import ApprovalRequired

logger = logging.getLogger(__name__)


class PolicyEnforcedChatTurn:
    def __init__(
        self,
        workflow: ChatTurnPort,
        registry: ToolRegistry,
        *,
        session_factory: SessionFactory | None,
        limits: ToolCallLimits,
    ) -> None:
        self._workflow = workflow
        self._registry = registry
        self._session_factory = session_factory
        self._limits = limits

    async def run(
        self, query: str, ctx: ExecutionContext
    ) -> GroundedAnswerResult | ApprovalRequired[BaseModel]:
        tools = self._registry.list_tools()
        if not tools:
            return await self._workflow.run(query, ctx)
        if self._session_factory is None:
            raise RuntimeError("Registered tools require approval persistence.")
        # Chat requests carry no conversation session yet; the request is the session.
        turn_context = (
            ctx
            if ctx.session_id
            else ctx.model_copy(update={"session_id": f"request:{ctx.correlation_id}"})
        )
        async with self._session_factory() as session:
            middleware: ToolPolicyMiddleware[Any, Any] = ToolPolicyMiddleware(
                approval_service=ApprovalService(SQLAlchemyApprovalRepository(session)),
                limits=self._limits,
                counter=PostgresToolCallCounter(self._session_factory),
                privileged_approver_roles=frozenset({APPROVER_ROLE}),
            )
            turn = ToolTurn(
                invoker=middleware, context=turn_context, turn_id=ctx.correlation_id, tools=tools
            )
            token = current_tool_turn.set(turn)
            try:
                result = await self._workflow.run(query, ctx)
            except Exception:
                if not turn.approvals:
                    raise
                # The model asked for an approved action and then produced no
                # citable answer; the pending approval is the outcome of this turn.
                logger.warning(
                    "chat_turn_ended_with_pending_approval",
                    extra={"correlation_id": ctx.correlation_id},
                    exc_info=True,
                )
            finally:
                current_tool_turn.reset(token)
        if turn.approvals:
            return turn.approvals[0]
        return result
