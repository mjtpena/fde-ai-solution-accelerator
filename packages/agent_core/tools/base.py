"""Typed tool contracts; execution is mediated by tool-policy middleware."""

from abc import ABC, abstractmethod
from datetime import datetime
from enum import StrEnum
from typing import ClassVar, Generic, Protocol, TypeVar, final
from uuid import UUID

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


class IdempotentWriteTool(EnterpriseTool[TArgs, TResult]):
    """Capability required for write/privileged tools.

    Only the approval executor may call execute_approved after validating an
    args-bound approval. execution_id is the approval's stable UUID, not model
    input or a new UUID per retry. Possession of a UUID does not grant approval.

    Implementations must durably deduplicate that key together with trusted
    scope at the side-effect boundary: atomically commit the effect and its
    stored result, then return that result on retries. For remote effects, use
    the downstream service's durable idempotency facility. If neither is
    available, the write cannot satisfy this contract and must not register.
    Row locking an approval alone does not make an external write exactly-once.
    """

    @final
    async def execute(self, args: TArgs, ctx: ExecutionContextProtocol) -> TResult:
        raise PermissionError("Write tools require approved execution with a stable execution_id")

    @abstractmethod
    async def execute_approved(
        self, args: TArgs, ctx: ExecutionContextProtocol, *, execution_id: UUID
    ) -> TResult:
        """Execute with durable effect/result deduplication of the approval UUID."""
        ...
