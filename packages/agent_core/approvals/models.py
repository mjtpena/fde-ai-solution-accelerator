from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

ApprovalStatus = Literal["pending", "approved", "rejected", "executed", "expired"]


class Approval(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    tool_name: str
    args_hash: str
    scope_id: str
    requested_by: str
    status: ApprovalStatus
    decided_by: str | None
    expires_at: datetime
    correlation_id: str

    @field_validator("expires_at")
    @classmethod
    def expires_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expires_at must be timezone-aware")
        return value


class ApprovalAuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    approval_id: UUID
    transition: Literal["pending", "approved", "rejected", "executed", "expired"]
    actor_id: str
    occurred_at: datetime
    correlation_id: str

    @field_validator("occurred_at")
    @classmethod
    def occurred_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value
