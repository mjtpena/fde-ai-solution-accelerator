from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ExecutionContext(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    correlation_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    roles: frozenset[str]
    scope_ids: frozenset[str]
    session_id: str | None = None
    deadline_utc: datetime
