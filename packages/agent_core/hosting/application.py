"""Compose existing workflow and trusted context resolution without a second agent loop."""

from typing import Generic, Protocol, TypeVar

from .contracts import (
    HostedAbstention,
    InvocationResult,
)

ContextT = TypeVar("ContextT")
ContextCo = TypeVar("ContextCo", covariant=True)
ContextContra = TypeVar("ContextContra", contravariant=True)


class ContextResolver(Protocol[ContextCo]):
    async def resolve(self, authorization: str | None) -> ContextCo:
        """Verify the credential and resolve scope from server-side membership."""
        ...


class WorkflowAbstention(Protocol):
    @property
    def reason(self) -> str: ...

    @property
    def evidence_ids(self) -> tuple[str, ...]: ...


class WorkflowResult(Protocol):
    @property
    def status(self) -> str: ...

    @property
    def answer(self) -> str | None: ...

    @property
    def citations(self) -> tuple[str, ...]: ...

    @property
    def abstention(self) -> WorkflowAbstention | None: ...


class GroundedWorkflow(Protocol[ContextContra]):
    async def run(self, query: str, ctx: ContextContra) -> WorkflowResult: ...


class WorkflowHostedApplication(Generic[ContextT]):
    def __init__(
        self, resolver: ContextResolver[ContextT], workflow: GroundedWorkflow[ContextT]
    ) -> None:
        self._resolver = resolver
        self._workflow = workflow

    async def invoke(self, query: str, authorization: str | None) -> InvocationResult:
        ctx = await self._resolver.resolve(authorization)
        result = await self._workflow.run(query, ctx)
        abstention = result.abstention
        return InvocationResult.model_validate(
            {
                "status": result.status,
                "answer": result.answer,
                "citations": result.citations,
                "abstention": (
                    HostedAbstention(reason=abstention.reason, evidence_ids=abstention.evidence_ids)
                    if abstention is not None
                    else None
                ),
            }
        )
