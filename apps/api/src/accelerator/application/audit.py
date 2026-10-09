from typing import Literal
from uuid import UUID

from accelerator.domain.audit import (
    AuditEvent,
    AuditRepository,
    EventOutcome,
    EventType,
)
from accelerator.security_core.data_boundaries.context import ExecutionContext


class AuditRecorder:
    def __init__(self, repository: AuditRepository) -> None:
        self._repository = repository

    async def auth_failure(self, correlation_id: str) -> None:
        await self._repository.append(
            AuditEvent(
                event_type=EventType.AUTH_FAILURE,
                outcome=EventOutcome.FAILED,
                correlation_id=correlation_id,
            )
        )

    async def authorization_failure(self, correlation_id: str, actor_id: str | None) -> None:
        await self._repository.append(
            AuditEvent(
                event_type=EventType.AUTHORIZATION_FAILURE,
                outcome=EventOutcome.DENIED,
                correlation_id=correlation_id,
                actor_id=actor_id,
            )
        )

    async def approval(
        self,
        context: ExecutionContext,
        *,
        approval_id: UUID,
        outcome: Literal[EventOutcome.APPROVED, EventOutcome.DENIED],
    ) -> None:
        await self._repository.append(
            AuditEvent(
                event_type=EventType.APPROVAL,
                outcome=outcome,
                correlation_id=context.correlation_id,
                actor_id=context.user_id,
                approval_id=approval_id,
            )
        )

    async def tool_execution(
        self,
        context: ExecutionContext,
        *,
        tool_name: str,
        outcome: Literal[EventOutcome.SUCCEEDED, EventOutcome.FAILED],
    ) -> None:
        await self._repository.append(
            AuditEvent(
                event_type=EventType.TOOL_EXECUTION,
                outcome=outcome,
                correlation_id=context.correlation_id,
                actor_id=context.user_id,
                tool_name=tool_name,
            )
        )
