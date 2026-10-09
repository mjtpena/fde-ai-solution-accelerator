from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Generic, Literal, Never, Protocol, TypeVar, cast

from agent_framework import (
    Case,
    Default,
    Executor,
    WorkflowBuilder,
    WorkflowContext,
    handler,
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


@dataclass(frozen=True, slots=True)
class Abstention:
    reason: str
    evidence_ids: tuple[str, ...]


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


@dataclass(frozen=True, slots=True)
class _WorkflowInput:
    query: str
    context: object


@dataclass(frozen=True, slots=True)
class _RetrievedEvidence:
    query: str
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True, slots=True)
class _AssessedEvidence:
    query: str
    evidence: tuple[Evidence, ...]
    decision: SufficiencyDecision
    evaluation_context: tuple[RetrievedEvidenceContext, ...] | None


@dataclass(frozen=True, slots=True)
class _GeneratedResponse:
    assessed: _AssessedEvidence
    generated: GeneratedAnswer


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
        ctx: WorkflowContext[Never, GroundedAnswerResult],
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
        await ctx.yield_output(
            GroundedAnswerResult(
                status="answered",
                answer=message.generated.answer,
                citations=citations,
                citation_sources=citation_sources,
                abstention=None,
                evaluation_context=message.assessed.evaluation_context,
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
    ) -> None:
        self._retriever = retriever
        self._sufficiency_checker = sufficiency_checker
        self._answer_generator = answer_generator
        self._citation_validator = citation_validator
        self._retrieval_request_factory = retrieval_request_factory
        self._capture_evaluation_context = capture_evaluation_context

    async def run(self, workflow_input: str, ctx: ContextT) -> GroundedAnswerResult:
        retrieve = _RetrieveExecutor(self._retriever, self._retrieval_request_factory)
        sufficiency = _SufficiencyExecutor(
            self._sufficiency_checker, self._capture_evaluation_context
        )
        generate = _GenerateExecutor(self._answer_generator)
        abstain = _AbstainExecutor()
        validate = _CitationValidationExecutor(self._citation_validator)
        workflow = (
            WorkflowBuilder(name="Grounded answer", start_executor=retrieve)
            .add_edge(retrieve, sufficiency)
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
            .build()
        )
        run_result = await workflow.run(_WorkflowInput(workflow_input, ctx))
        outputs = run_result.get_outputs()
        if len(outputs) != 1 or not isinstance(outputs[0], GroundedAnswerResult):
            raise RuntimeError("Grounded-answer workflow did not yield exactly one result.")
        return outputs[0]
