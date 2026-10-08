from __future__ import annotations

from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict


ArgsT = TypeVar("ArgsT", bound=BaseModel)


class ApprovalRequired(BaseModel, Generic[ArgsT]):
    model_config = ConfigDict(frozen=True)

    approval_id: UUID
    tool_name: str
    arguments: ArgsT
    args_hash: str
    scope_id: str
    requested_by: str
    correlation_id: str


class ToolPolicyViolation(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
