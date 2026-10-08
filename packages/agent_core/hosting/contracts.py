"""Trusted application composition, separate from the hosted HTTP transport."""

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InvocationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1, max_length=16000, pattern=r"\S")


class HostedAbstention(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: str = Field(min_length=1)
    evidence_ids: tuple[str, ...]


class InvocationResult(BaseModel):
    """Wire representation of the explicit grounded-answer workflow result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["answered", "abstained"]
    answer: str | None
    citations: tuple[str, ...]
    abstention: HostedAbstention | None

    @model_validator(mode="after")
    def validate_outcome(self) -> "InvocationResult":
        if self.status == "answered":
            if (
                not self.answer
                or not self.answer.strip()
                or not self.citations
                or self.abstention is not None
            ):
                raise ValueError("Answered outcomes require text and citations, not abstention")
            if any(not chunk_id.strip() for chunk_id in self.citations):
                raise ValueError("Citations must contain nonempty chunk IDs")
        elif self.answer is not None or self.citations or self.abstention is None:
            raise ValueError("Abstained outcomes require only an abstention")
        return self


class InvocationUnauthorized(Exception):
    """The application could not establish trusted caller context."""


class HostedApplication(Protocol):
    async def invoke(self, query: str, authorization: str | None) -> InvocationResult:
        """Resolve trusted ExecutionContext, then run the #23 grounded workflow.

        The composition owns credential verification and server-side scope resolution.
        It must reject missing/unverifiable credentials with InvocationUnauthorized.
        Use #19's factory only behind the workflow's answer-generator port.
        """
        ...
