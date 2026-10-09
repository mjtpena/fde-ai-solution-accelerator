from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Generic, Literal, Never, Protocol, TypeVar, cast, runtime_checkable

from agent_framework import (
    Case,
    Default,
    Executor,
    WorkflowBuilder,
    WorkflowContext,
    handler,
)

from accelerator.security_core.content_safety import (
    OUTPUT_BLOCKED,
    PROMPT_ATTACK,
    UNAVAILABLE,
    ContentSafetyChecker,
    ContentSafetyPolicy,
    ContentSafetyUnavailableError,
    ScreenedDocument,
)
from accelerator.security_core.content_safety import (
    REFUSAL_REASONS as REFUSAL_REASONS,  # re-exported for callers of this module
)

from .base import Workflow

RequestT = TypeVar("RequestT")
ContextT = TypeVar("ContextT")
EvidenceT = TypeVar("EvidenceT", bound="Evidence")
RetrieverRequestT = TypeVar("RetrieverRequestT", contravariant=True)
RetrieverContextT = TypeVar("RetrieverContextT", contravariant=True)
RetrieverEvidenceT = TypeVar("RetrieverEvidenceT", bound="Evidence", covariant=True)
SufficiencyEvidenceT = TypeVar("SufficiencyEvidenceT", contravariant=True)
AnswerEvidenceT = TypeVar("AnswerEvidenceT", contravariant=True)


class Evidence(Protocol):
    @property
    def chunk_id(self) -> str: ...

    @property
    def text(self) -> str: ...

    @property
    def document_title(self) -> str: ...

    @property
    def source_uri(self) -> str: ...


class SufficiencyDecision(Protocol):
    @property
    def sufficient(self) -> bool: ...

    @property
    def reason(self) -> str: ...

    @property
    def evidence_ids(self) -> Sequence[str]: ...


class GeneratedAnswer(Protocol):
    @property
    def answer(self) -> str: ...

    @property
    def citations(self) -> Sequence[str]: ...


class Retriever(Protocol[RetrieverRequestT, RetrieverContextT, RetrieverEvidenceT]):
    async def retrieve(
        self, req: RetrieverRequestT, ctx: RetrieverContextT
    ) -> Sequence[RetrieverEvidenceT]: ...


class SufficiencyChecker(Protocol[SufficiencyEvidenceT]):
    async def evaluate(
        self, evidence: Sequence[SufficiencyEvidenceT]
    ) -> SufficiencyDecision: ...


class AnswerGenerator(Protocol[AnswerEvidenceT]):
    async def generate(
        self, query: str, evidence: Sequence[AnswerEvidenceT]
    ) -> GeneratedAnswer: ...


class CitationValidator(Protocol):
    def validate(
        self, citations: Sequence[str], retrieved_chunk_ids: frozenset[str]
    ) -> None: ...


INSUFFICIENT_EVIDENCE: Final = "insufficient_evidence"
AbstentionCode = Literal[
    "insufficient_evidence",
    "content_safety_prompt_attack",
    "content_safety_output_blocked",
    "content_safety_unavailable",
]
CONTENT_SAFETY_CODES: Final[frozenset[str]] = frozenset(
    {PROMPT_ATTACK, OUTPUT_BLOCKED, UNAVAILABLE}
)
# Fixed refusal texts (``REFUSAL_REASONS``) live in security_core so every host,
# including the Foundry hosted agent, refuses with the same words.


@dataclass(frozen=True, slots=True)
class Abstention:
    reason: str
    evidence_ids: tuple[str, ...]
    # Stable, machine-readable cause; ``reason`` is the human-readable text.
    code: AbstentionCode = "insufficient_evidence"


@dataclass(frozen=True, slots=True)
class CitationSource:
    chunk_id: str
    document_title: str
    source_uri: str


@dataclass(frozen=True, slots=True)
class RetrievedEvidenceContext:
    chunk_id: str
    text: str


@dataclass(frozen=True, slots=True)
class GroundedAnswerResult:
    status: Literal["answered", "abstained"]
    answer: str | None
    citations: tuple[str, ...]
    citation_sources: tuple[CitationSource, ...]
    abstention: Abstention | None
    evaluation_context: tuple[RetrievedEvidenceContext, ...] | None = field(
        default=None, repr=False
    )
    # Retrieved chunks that content safety flagged as document attacks and dropped
    # from evidence before generation. IDs only, never their text.
    screened_out_chunk_ids: tuple[str, ...] = ()


@runtime_checkable
class _HasDeadline(Protocol):
    @property
    def deadline_utc(self) -> datetime: ...


def _refusal(
    code: str,
    *,
    screened_out_chunk_ids: tuple[str, ...] = (),
    evaluation_context: tuple[RetrievedEvidenceContext, ...] | None = None,
) -> GroundedAnswerResult:
    return GroundedAnswerResult(
        status="abstained",
        answer=None,
        citations=(),
        citation_sources=(),
        abstention=Abstention(
            reason=REFUSAL_REASONS[code],
            evidence_ids=(),
            code=cast(AbstentionCode, code),
        ),
        evaluation_context=evaluation_context,
        screened_out_chunk_ids=screened_out_chunk_ids,
    )


@dataclass(frozen=True, slots=True)
class _WorkflowInput:
    query: str
    context: object


@dataclass(frozen=True, slots=True)
class _RetrievedEvidence:
    query: str
    evidence: tuple[Evidence, ...]
    screened_out_chunk_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _AssessedEvidence:
    query: str
    evidence: tuple[Evidence, ...]
    decision: SufficiencyDecision
    evaluation_context: tuple[RetrievedEvidenceContext, ...] | None
    screened_out_chunk_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _GeneratedResponse:
    assessed: _AssessedEvidence
    generated: GeneratedAnswer


class _ShieldPromptExecutor(Executor):
    """Prompt Shields on the user's prompt, before anything is retrieved."""

    def __init__(
        self,
        checker: ContentSafetyChecker | None,
        deadline_utc: datetime | None,
        capture_evaluation_context: bool,
    ) -> None:
        super().__init__(id="shield-prompt")
        self._checker = checker
        self._deadline = deadline_utc
        # Refusals before generation carry an empty same-turn context when captured.
        self._context: tuple[RetrievedEvidenceContext, ...] | None = (
            () if capture_evaluation_context else None
        )

    @handler
    async def shield(
        self,
        message: _WorkflowInput,
        ctx: WorkflowContext[_WorkflowInput, GroundedAnswerResult],
    ) -> None:
        if self._checker is not None:
            try:
                verdict = await self._checker.shield_prompt(
                    message.query, (), deadline_utc=self._deadline
                )
            except ContentSafetyUnavailableError:
                await ctx.yield_output(_refusal(UNAVAILABLE, evaluation_context=self._context))
                return
            if verdict.user_prompt_attack:
                await ctx.yield_output(_refusal(PROMPT_ATTACK, evaluation_context=self._context))
                return
        await ctx.send_message(message)


class _ShieldDocumentsExecutor(Executor):
    """Prompt Shields on retrieved chunks; attacked chunks are dropped, not rewritten."""

    def __init__(
        self,
        checker: ContentSafetyChecker | None,
        deadline_utc: datetime | None,
        capture_evaluation_context: bool,
    ) -> None:
        super().__init__(id="shield-documents")
        self._checker = checker
        self._deadline = deadline_utc
        # Refusals before generation carry an empty same-turn context when captured.
        self._context: tuple[RetrievedEvidenceContext, ...] | None = (
            () if capture_evaluation_context else None
        )

    @handler
    async def shield(
        self,
        message: _RetrievedEvidence,
        ctx: WorkflowContext[_RetrievedEvidence, GroundedAnswerResult],
    ) -> None:
        if self._checker is None or not message.evidence:
            await ctx.send_message(message)
            return
        try:
            verdict = await self._checker.shield_prompt(
                message.query,
                [ScreenedDocument(item.chunk_id, item.text) for item in message.evidence],
                deadline_utc=self._deadline,
            )
        except ContentSafetyUnavailableError:
            await ctx.yield_output(_refusal(UNAVAILABLE, evaluation_context=self._context))
            return
        if verdict.user_prompt_attack:
            await ctx.yield_output(_refusal(PROMPT_ATTACK, evaluation_context=self._context))
            return
        attacked = frozenset(verdict.attacked_document_ids)
        await ctx.send_message(
            _RetrievedEvidence(
                query=message.query,
                evidence=tuple(item for item in message.evidence if item.chunk_id not in attacked),
                screened_out_chunk_ids=tuple(
                    dict.fromkeys(
                        item.chunk_id for item in message.evidence if item.chunk_id in attacked
                    )
                ),
            )
        )


class _RetrieveExecutor(
    Executor, Generic[RequestT, ContextT, EvidenceT]
):
    def __init__(
        self,
        retriever: Retriever[RequestT, ContextT, EvidenceT],
        request_factory: Callable[[str], RequestT],
    ) -> None:
        super().__init__(id="retrieve")
        self._retriever = retriever
        self._request_factory = request_factory

    @handler
    async def retrieve(
        self, message: _WorkflowInput, ctx: WorkflowContext[_RetrievedEvidence]
    ) -> None:
        evidence = tuple(
            await self._retriever.retrieve(
                self._request_factory(message.query), cast(ContextT, message.context)
            )
        )
        await ctx.send_message(
            _RetrievedEvidence(query=message.query, evidence=evidence)
        )


class _SufficiencyExecutor(Executor, Generic[EvidenceT]):
    def __init__(
        self, checker: SufficiencyChecker[EvidenceT], capture_evaluation_context: bool
    ) -> None:
        super().__init__(id="sufficiency")
        self._checker = checker
        self._capture_evaluation_context = capture_evaluation_context

    @handler
    async def assess(
        self, message: _RetrievedEvidence, ctx: WorkflowContext[_AssessedEvidence]
    ) -> None:
        decision = await self._checker.evaluate(
            cast(Sequence[EvidenceT], message.evidence)
        )
        evaluation_context = (
            tuple(
                RetrievedEvidenceContext(chunk_id=item.chunk_id, text=item.text)
                for item in message.evidence
            )
            if self._capture_evaluation_context
            else None
        )
        await ctx.send_message(
            _AssessedEvidence(
                query=message.query,
                evidence=message.evidence,
                decision=decision,
                evaluation_context=evaluation_context,
                screened_out_chunk_ids=message.screened_out_chunk_ids,
            )
        )


class _GenerateExecutor(Executor, Generic[EvidenceT]):
    def __init__(self, generator: AnswerGenerator[EvidenceT]) -> None:
        super().__init__(id="generate")
        self._generator = generator

    @handler
    async def generate(
        self, message: _AssessedEvidence, ctx: WorkflowContext[_GeneratedResponse]
    ) -> None:
        if not message.decision.sufficient:
            raise RuntimeError("Insufficient evidence was routed to answer generation.")
        generated = await self._generator.generate(
            message.query, cast(Sequence[EvidenceT], message.evidence)
        )
        await ctx.send_message(_GeneratedResponse(message, generated))


class _CitationValidationExecutor(Executor):
    def __init__(self, validator: CitationValidator) -> None:
        super().__init__(id="citation-validation")
        self._validator = validator

    @handler
    async def validate(
        self,
        message: _GeneratedResponse,
        ctx: WorkflowContext[GroundedAnswerResult],
    ) -> None:
        evidence = message.assessed.evidence
        citations = tuple(message.generated.citations)
        if not citations:
            raise ValueError("Generated grounded answers must include chunk citations.")
        retrieved_chunk_ids = frozenset(item.chunk_id for item in evidence)
        self._validator.validate(citations, retrieved_chunk_ids)
        evidence_by_id = {item.chunk_id: item for item in evidence}
        citation_sources = tuple(
            CitationSource(
                chunk_id=chunk_id,
                document_title=evidence_by_id[chunk_id].document_title,
                source_uri=evidence_by_id[chunk_id].source_uri,
            )
            for chunk_id in citations
        )
        await ctx.send_message(
            GroundedAnswerResult(
                status="answered",
                answer=message.generated.answer,
                citations=citations,
                citation_sources=citation_sources,
                abstention=None,
                evaluation_context=message.assessed.evaluation_context,
                screened_out_chunk_ids=message.assessed.screened_out_chunk_ids,
            )
        )


class _ScreenOutputExecutor(Executor):
    """Harm-category analysis of the validated answer; blocked answers become refusals."""

    def __init__(
        self,
        checker: ContentSafetyChecker | None,
        policy: ContentSafetyPolicy,
        deadline_utc: datetime | None,
    ) -> None:
        super().__init__(id="screen-output")
        self._checker = checker
        self._policy = policy
        self._deadline = deadline_utc

    @handler
    async def screen(
        self,
        message: GroundedAnswerResult,
        ctx: WorkflowContext[Never, GroundedAnswerResult],
    ) -> None:
        if self._checker is None or message.answer is None:
            await ctx.yield_output(message)
            return
        try:
            analysis = await self._checker.analyze_text(
                message.answer, deadline_utc=self._deadline
            )
            blocked = self._policy.blocked_categories(analysis)
        except ContentSafetyUnavailableError:
            code = UNAVAILABLE
        else:
            if not blocked:
                await ctx.yield_output(message)
                return
            code = OUTPUT_BLOCKED
        await ctx.yield_output(
            _refusal(
                code,
                screened_out_chunk_ids=message.screened_out_chunk_ids,
                evaluation_context=message.evaluation_context,
            )
        )


class _AbstainExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="abstain")

    @handler
    async def abstain(
        self,
        message: _AssessedEvidence,
        ctx: WorkflowContext[Never, GroundedAnswerResult],
    ) -> None:
        if message.decision.sufficient:
            raise RuntimeError("Sufficient evidence was routed to abstention.")
        retrieved_ids = frozenset(item.chunk_id for item in message.evidence)
        unsupported_ids = set(message.decision.evidence_ids) - retrieved_ids
        if unsupported_ids:
            raise ValueError(
                "Sufficiency decision referenced evidence not retrieved in this turn."
            )
        await ctx.yield_output(
            GroundedAnswerResult(
                status="abstained",
                answer=None,
                citations=(),
                citation_sources=(),
                abstention=Abstention(
                    reason=message.decision.reason,
                    evidence_ids=tuple(message.decision.evidence_ids),
                ),
                evaluation_context=message.evaluation_context,
                screened_out_chunk_ids=message.screened_out_chunk_ids,
            )
        )


class GroundedAnswerWorkflow(
    Workflow[str, ContextT, GroundedAnswerResult],
    Generic[RequestT, ContextT, EvidenceT],
):
    def __init__(
        self,
        *,
        retriever: Retriever[RequestT, ContextT, EvidenceT],
        sufficiency_checker: SufficiencyChecker[EvidenceT],
        answer_generator: AnswerGenerator[EvidenceT],
        citation_validator: CitationValidator,
        retrieval_request_factory: Callable[[str], RequestT],
        capture_evaluation_context: bool = False,
        content_safety_checker: ContentSafetyChecker | None = None,
        content_safety_policy: ContentSafetyPolicy | None = None,
    ) -> None:
        """Without ``content_safety_checker`` the workflow runs unscreened. That is for
        library and unit-test use only; the API's composition root always supplies a
        checker in production, and the settings refuse to disable it there."""
        self._retriever = retriever
        self._sufficiency_checker = sufficiency_checker
        self._answer_generator = answer_generator
        self._citation_validator = citation_validator
        self._retrieval_request_factory = retrieval_request_factory
        self._capture_evaluation_context = capture_evaluation_context
        self._content_safety = content_safety_checker
        self._content_safety_policy = content_safety_policy or ContentSafetyPolicy()

    async def run(self, workflow_input: str, ctx: ContextT) -> GroundedAnswerResult:
        # Screening calls are bounded by the request deadline when the context has one.
        deadline = ctx.deadline_utc if isinstance(ctx, _HasDeadline) else None
        shield_prompt = _ShieldPromptExecutor(
            self._content_safety, deadline, self._capture_evaluation_context
        )
        retrieve = _RetrieveExecutor(self._retriever, self._retrieval_request_factory)
        shield_documents = _ShieldDocumentsExecutor(
            self._content_safety, deadline, self._capture_evaluation_context
        )
        sufficiency = _SufficiencyExecutor(
            self._sufficiency_checker, self._capture_evaluation_context
        )
        generate = _GenerateExecutor(self._answer_generator)
        abstain = _AbstainExecutor()
        validate = _CitationValidationExecutor(self._citation_validator)
        screen_output = _ScreenOutputExecutor(
            self._content_safety, self._content_safety_policy, deadline
        )
        workflow = (
            WorkflowBuilder(name="Grounded answer", start_executor=shield_prompt)
            .add_edge(shield_prompt, retrieve)
            .add_edge(retrieve, shield_documents)
            .add_edge(shield_documents, sufficiency)
            .add_switch_case_edge_group(
                sufficiency,
                [
                    Case(
                        condition=lambda message: message.decision.sufficient,
                        target=generate,
                    ),
                    Default(target=abstain),
                ],
            )
            .add_edge(generate, validate)
            .add_edge(validate, screen_output)
            .build()
        )
        run_result = await workflow.run(_WorkflowInput(workflow_input, ctx))
        outputs = run_result.get_outputs()
        if len(outputs) != 1 or not isinstance(outputs[0], GroundedAnswerResult):
            raise RuntimeError("Grounded-answer workflow did not yield exactly one result.")
        return outputs[0]
