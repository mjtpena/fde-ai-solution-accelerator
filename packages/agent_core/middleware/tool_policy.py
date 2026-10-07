from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Generic, TypeVar

from pydantic import BaseModel

from accelerator.security_core.tool_policy.models import ApprovalRequired, ToolPolicyViolation
from accelerator.security_core.tool_policy.policy import ToolPolicy

if TYPE_CHECKING:
    from ..approvals import Approval, ApprovalService
    from ..tools import EnterpriseTool, ExecutionContextProtocol


ArgsT = TypeVar("ArgsT", bound=BaseModel)
ResultT = TypeVar("ResultT", bound=BaseModel)


@dataclass(frozen=True)
class ToolCallLimits:
    max_calls_per_turn: int = 10
    max_calls_per_session: int = 100

    def __post_init__(self) -> None:
        if self.max_calls_per_turn < 1 or self.max_calls_per_session < 1:
            raise ValueError("tool call limits must be positive")


class ToolCallLimitExceeded(RuntimeError):
    pass


class ToolExecutionTimeout(TimeoutError):
    pass


class _InMemoryCallCounter:
    def __init__(self) -> None:
        self._session_counts: dict[str, int] = {}
        self._turn_counts: dict[tuple[str, str], int] = {}
        self._lock = asyncio.Lock()

    async def consume(
        self,
        session_id: str,
        turn_id: str,
        limits: ToolCallLimits,
    ) -> None:
        key = (session_id, turn_id)
        async with self._lock:
            session_count = self._session_counts.get(session_id, 0)
            turn_count = self._turn_counts.get(key, 0)
            if session_count >= limits.max_calls_per_session:
                raise ToolCallLimitExceeded("per-session tool call limit exceeded")
            if turn_count >= limits.max_calls_per_turn:
                raise ToolCallLimitExceeded("per-turn tool call limit exceeded")
            self._session_counts[session_id] = session_count + 1
            self._turn_counts[key] = turn_count + 1


class ToolPolicyMiddleware(Generic[ArgsT, ResultT]):
    def __init__(
        self,
        *,
        approval_service: ApprovalService[ArgsT, ResultT] | None = None,
        policy: ToolPolicy | None = None,
        limits: ToolCallLimits | None = None,
        privileged_approver_roles: frozenset[str] = frozenset(),
    ) -> None:
        self._approval_service = approval_service
        self._policy = policy or ToolPolicy()
        self._limits = limits or ToolCallLimits()
        self._privileged_approver_roles = privileged_approver_roles
        self._counter = _InMemoryCallCounter()

    async def invoke(
        self,
        tool: EnterpriseTool[ArgsT, ResultT],
        arguments: ArgsT,
        context: ExecutionContextProtocol,
        *,
        turn_id: str,
        approval: Approval | None = None,
        approver_context: ExecutionContextProtocol | None = None,
    ) -> ResultT | ApprovalRequired[ArgsT]:
        session_id = context.session_id
        if session_id is None or not session_id.strip():
            raise ToolPolicyViolation("tool_call_requires_session")
        if not turn_id.strip():
            raise ToolPolicyViolation("tool_call_requires_turn")
        await self._counter.consume(session_id, turn_id, self._limits)

        action = self._policy.evaluate(tool.risk.value)
        if action == "approval" and approval is None:
            return await self._create_approval(tool, arguments, context)
        if action == "approval":
            self._validate_approval_context(
                tool=tool,
                context=context,
                approval=approval,
                approver_context=approver_context,
            )

        timeout_seconds = self._effective_timeout(tool.timeout_seconds, context.deadline_utc)
        try:
            if action == "approval":
                if self._approval_service is None or approval is None:
                    raise ToolPolicyViolation("approval_service_unavailable")
                return await asyncio.wait_for(
                    self._approval_service.execute(
                        approval_id=approval.id,
                        tool=tool,
                        args=arguments,
                        ctx=context,
                    ),
                    timeout=timeout_seconds,
                )
            return await asyncio.wait_for(
                tool.execute(arguments, context),
                timeout=timeout_seconds,
            )
        except TimeoutError as error:
            raise ToolExecutionTimeout(f"tool {tool.name!r} exceeded its policy timeout") from error

    async def _create_approval(
        self,
        tool: EnterpriseTool[ArgsT, ResultT],
        arguments: ArgsT,
        context: ExecutionContextProtocol,
    ) -> ApprovalRequired[ArgsT]:
        if self._approval_service is None:
            raise ToolPolicyViolation("approval_service_unavailable")
        if len(context.scope_ids) != 1:
            raise ToolPolicyViolation("write_tool_requires_single_authorized_scope")
        approval = await self._approval_service.create(
            tool_name=tool.name,
            args=arguments,
            ctx=context,
        )
        if (
            approval.tool_name != tool.name
            or approval.status != "pending"
            or approval.requested_by != context.user_id
            or approval.scope_id not in context.scope_ids
            or approval.correlation_id != context.correlation_id
        ):
            raise ToolPolicyViolation("approval_service_returned_unbound_approval")
        return ApprovalRequired(
            approval_id=approval.id,
            tool_name=tool.name,
            arguments=arguments.model_copy(deep=True),
            args_hash=approval.args_hash,
            scope_id=approval.scope_id,
            requested_by=approval.requested_by,
            correlation_id=approval.correlation_id,
        )

    def _validate_approval_context(
        self,
        *,
        tool: EnterpriseTool[ArgsT, ResultT],
        context: ExecutionContextProtocol,
        approval: Approval | None,
        approver_context: ExecutionContextProtocol | None,
    ) -> None:
        if approval is None:
            raise ToolPolicyViolation("approved_tool_call_requires_approval")
        if approval.status != "approved":
            raise ToolPolicyViolation("approval_not_approved")
        if approval.tool_name != tool.name:
            raise ToolPolicyViolation("approval_tool_mismatch")
        if approval.requested_by != context.user_id:
            raise ToolPolicyViolation("approval_requester_mismatch")
        if approval.scope_id not in context.scope_ids:
            raise ToolPolicyViolation("approval_scope_not_authorized")
        if tool.risk.value == "privileged":
            if approval.decided_by == approval.requested_by:
                raise ToolPolicyViolation("privileged_approver_must_be_distinct")
            if approver_context is None or approver_context.user_id != approval.decided_by:
                raise ToolPolicyViolation("privileged_approver_context_required")
            if not (approver_context.roles & self._privileged_approver_roles):
                raise ToolPolicyViolation("privileged_approver_role_required")
            if approval.scope_id not in approver_context.scope_ids:
                raise ToolPolicyViolation("approver_scope_not_authorized")

    @staticmethod
    def _effective_timeout(tool_timeout: float, deadline_utc: datetime) -> float:
        if not math.isfinite(tool_timeout) or tool_timeout <= 0:
            raise ToolPolicyViolation("invalid_tool_timeout")
        if deadline_utc.tzinfo is None or deadline_utc.utcoffset() is None:
            raise ToolPolicyViolation("execution_deadline_timezone_missing")
        remaining_seconds = (deadline_utc - datetime.now(timezone.utc)).total_seconds()
        if remaining_seconds <= 0:
            raise ToolExecutionTimeout("execution deadline has elapsed")
        return min(tool_timeout, remaining_seconds)
