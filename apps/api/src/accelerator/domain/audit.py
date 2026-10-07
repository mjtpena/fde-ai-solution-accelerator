from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Protocol
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class EventType(StrEnum):
    AUTH_FAILURE = "auth_failure"
    APPROVAL = "approval"
    TOOL_EXECUTION = "tool_execution"


class EventOutcome(StrEnum):
    APPROVED = "approved"
    DENIED = "denied"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


Identifier = Annotated[str, Field(min_length=1, max_length=128)]
ToolName = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")]


class AuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: UUID = Field(default_factory=uuid4)
    occurred_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: EventType
    outcome: EventOutcome
    correlation_id: Identifier
    actor_id: Identifier | None = None
    approval_id: UUID | None = None
    tool_name: ToolName | None = None

    @model_validator(mode="after")
    def validate_event_fields(self) -> "AuditEvent":
        if self.event_type == EventType.AUTH_FAILURE:
            valid = (
                self.outcome == EventOutcome.FAILED
                and self.actor_id is None
                and self.approval_id is None
                and self.tool_name is None
            )
        elif self.event_type == EventType.APPROVAL:
            valid = (
                self.outcome in (EventOutcome.APPROVED, EventOutcome.DENIED)
                and self.actor_id is not None
                and self.approval_id is not None
                and self.tool_name is None
            )
        else:
            valid = (
                self.outcome in (EventOutcome.SUCCEEDED, EventOutcome.FAILED)
                and self.actor_id is not None
                and self.tool_name is not None
                and self.approval_id is None
            )
        if not valid:
            raise ValueError("Invalid fields for audit event type")
        return self


class AuditPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[AuditEvent, ...]


class AuditRepository(Protocol):
    async def append(self, event: AuditEvent) -> None: ...

    async def query(
        self, *, limit: int, offset: int, event_type: EventType | None = None
    ) -> AuditPage: ...
