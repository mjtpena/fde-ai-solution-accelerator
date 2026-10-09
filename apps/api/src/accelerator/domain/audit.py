from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Protocol
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class EventType(StrEnum):
    AUTH_FAILURE = "auth_failure"
    AUTHORIZATION_FAILURE = "authorization_failure"
    APPROVAL = "approval"
    TOOL_EXECUTION = "tool_execution"
    CONTENT_SAFETY = "content_safety"


class EventOutcome(StrEnum):
    APPROVED = "approved"
    DENIED = "denied"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


Identifier = Annotated[str, Field(min_length=1, max_length=128)]
ToolName = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")]
# Content-safety refusal codes (``security_core.content_safety``); never free text.
ContentSafetyReason = Literal[
    "content_safety_prompt_attack",
    "content_safety_output_blocked",
    "content_safety_unavailable",
]


class AuditEvent(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", json_schema_serialization_defaults_required=True
    )

    event_id: UUID = Field(default_factory=uuid4)
    occurred_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    event_type: EventType
    outcome: EventOutcome
    correlation_id: Identifier
    actor_id: Identifier | None = None
    approval_id: UUID | None = None
    tool_name: ToolName | None = None
    reason_code: ContentSafetyReason | None = None

    @model_validator(mode="after")
    def validate_event_fields(self) -> "AuditEvent":
        if self.event_type == EventType.CONTENT_SAFETY:
            # A refusal (prompt attack, blocked output) is denied; a screening
            # outage that forced a refusal is failed.
            expected = (
                EventOutcome.FAILED
                if self.reason_code == "content_safety_unavailable"
                else EventOutcome.DENIED
            )
            valid = (
                self.reason_code is not None
                and self.outcome == expected
                and self.actor_id is not None
                and self.approval_id is None
                and self.tool_name is None
            )
        elif self.reason_code is not None:
            valid = False
        elif self.event_type == EventType.AUTH_FAILURE:
            valid = (
                self.outcome == EventOutcome.FAILED
                and self.actor_id is None
                and self.approval_id is None
                and self.tool_name is None
            )
        elif self.event_type == EventType.AUTHORIZATION_FAILURE:
            # The actor is recorded when the token was valid; scope-less denials
            # (no Entra object ID) have none.
            valid = (
                self.outcome == EventOutcome.DENIED
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
