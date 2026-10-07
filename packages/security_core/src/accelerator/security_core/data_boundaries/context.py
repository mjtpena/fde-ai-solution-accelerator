from datetime import UTC, datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class ExecutionContext(BaseModel):
    """Trusted, immutable context constructed by the server's identity boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    correlation_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    roles: frozenset[str]
    scope_ids: frozenset[str]
    session_id: str | None = None
    deadline_utc: AwareDatetime

    @field_validator("deadline_utc")
    @classmethod
    def normalize_deadline(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)
