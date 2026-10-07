from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Generic, Literal, Protocol, TypeVar

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

    async def run(self, query: str, ctx: ContextT) -> GroundedAnswerResult:
        evidence = tuple(
            await self._retriever.retrieve(self._retrieval_request_factory(query), ctx)
        )
        decision = await self._sufficiency_checker.evaluate(evidence)
        retrieved_chunk_ids = frozenset(item.chunk_id for item in evidence)
        evaluation_context = (
            tuple(
                RetrievedEvidenceContext(chunk_id=item.chunk_id, text=item.text)
                for item in evidence
            )
            if self._capture_evaluation_context
            else None
        )

        if not decision.sufficient:
            unsupported_ids = set(decision.evidence_ids) - retrieved_chunk_ids
            if unsupported_ids:
                raise ValueError(
                    "Sufficiency decision referenced evidence not retrieved in this turn."
                )
            return GroundedAnswerResult(
                status="abstained",
                answer=None,
                citations=(),
                citation_sources=(),
                abstention=Abstention(
                    reason=decision.reason,
                    evidence_ids=tuple(decision.evidence_ids),
                ),
                evaluation_context=evaluation_context,
            )

        generated = await self._answer_generator.generate(query, evidence)
        citations = tuple(generated.citations)
        if not citations:
            raise ValueError("Generated grounded answers must include chunk citations.")
        self._citation_validator.validate(citations, retrieved_chunk_ids)
        evidence_by_id = {item.chunk_id: item for item in evidence}
        citation_sources = tuple(
            CitationSource(
                chunk_id=chunk_id,
                document_title=evidence_by_id[chunk_id].document_title,
                source_uri=evidence_by_id[chunk_id].source_uri,
            )
            for chunk_id in citations
        )
        return GroundedAnswerResult(
            status="answered",
            answer=generated.answer,
            citations=citations,
            citation_sources=citation_sources,
            abstention=None,
            evaluation_context=evaluation_context,
        )
