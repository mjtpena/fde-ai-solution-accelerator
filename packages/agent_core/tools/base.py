"""Typed tool contracts; execution is mediated by tool-policy middleware."""

from abc import ABC, abstractmethod
from datetime import datetime
from enum import StrEnum
from typing import ClassVar, Generic, Protocol, TypeVar

from pydantic import BaseModel


class ExecutionContextProtocol(Protocol):
    """Structural seam for the server's context model, not a competing model.

    The shared identity/context implementation may satisfy this protocol without
    importing tool code. Never populate context from model-supplied tool args.
    """

    @property
    def correlation_id(self) -> str: ...

    @property
    def user_id(self) -> str: ...

    @property
    def roles(self) -> frozenset[str]: ...

    @property
    def scope_ids(self) -> frozenset[str]: ...

    @property
    def session_id(self) -> str | None: ...

    @property
    def deadline_utc(self) -> datetime: ...


class ToolRisk(StrEnum):
    READ_ONLY = "read_only"
    LOW_IMPACT_WRITE = "low_impact_write"
    HIGH_IMPACT_WRITE = "high_impact_write"
    PRIVILEGED = "privileged"
    PROHIBITED = "prohibited"


TArgs = TypeVar("TArgs", bound=BaseModel)
TResult = TypeVar("TResult", bound=BaseModel)


class EnterpriseTool(ABC, Generic[TArgs, TResult]):
    """Implement a tool, not its authorization policy.

    Callers must route execution through policy middleware. Write tools require
    an args-bound approval; privileged tools require a distinct approver.
    """

    name: ClassVar[str]
    description: ClassVar[str]
    risk: ClassVar[ToolRisk]
    args_model: ClassVar[type[BaseModel]]
    timeout_seconds: ClassVar[float] = 20.0

    @abstractmethod
    async def execute(self, args: TArgs, ctx: ExecutionContextProtocol) -> TResult:
        """Execute only after the policy layer authorizes this invocation."""
        ...
