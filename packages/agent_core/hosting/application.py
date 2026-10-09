"""Compose existing workflow and trusted context resolution without a second agent loop."""

import logging
from typing import Generic, Protocol, TypeVar

from accelerator.security_core.content_safety import REFUSAL_REASONS, UNAVAILABLE

from .contracts import (
    HostedAbstention,
    InvocationResult,
)
from .screening import ScreeningEnforcement

logger = logging.getLogger(__name__)

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

    @property
    def code(self) -> str: ...


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
    """Resolve trusted context, run the grounded workflow, return its wire result.

    With ``screening`` (always, in the packaged production composition) a result is
    released only when that turn's content-safety verdicts cover it; otherwise the
    caller gets a ``content_safety_unavailable`` refusal.
    """

    def __init__(
        self,
        resolver: ContextResolver[ContextT],
        workflow: GroundedWorkflow[ContextT],
        *,
        screening: ScreeningEnforcement | None = None,
    ) -> None:
        self._resolver = resolver
        self._workflow = workflow
        self._screening = screening

    async def invoke(self, query: str, authorization: str | None) -> InvocationResult:
        ctx = await self._resolver.resolve(authorization)
        if self._screening is None:
            return _wire_result(await self._workflow.run(query, ctx))
        with self._screening.turn() as turn:
            result = await self._workflow.run(query, ctx)
            abstention = result.abstention
            reason = turn.unscreened_reason(
                query,
                status=result.status,
                answer=result.answer,
                citations=result.citations,
                code=abstention.code if abstention is not None else None,
            )
        if reason is None:
            return _wire_result(result)
        logger.error("hosted_content_safety_unscreened", extra={"reason": reason})
        return InvocationResult(
            status="abstained",
            answer=None,
            citations=(),
            abstention=HostedAbstention(
                reason=REFUSAL_REASONS[UNAVAILABLE], evidence_ids=(), code=UNAVAILABLE
            ),
        )


def _wire_result(result: WorkflowResult) -> InvocationResult:
    abstention = result.abstention
    return InvocationResult.model_validate(
        {
            "status": result.status,
            "answer": result.answer,
            "citations": result.citations,
            "abstention": (
                {
                    "reason": abstention.reason,
                    "evidence_ids": abstention.evidence_ids,
                    "code": abstention.code,
                }
                if abstention is not None
                else None
            ),
        }
    )
