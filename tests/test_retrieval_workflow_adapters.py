import asyncio
from dataclasses import dataclass

import pytest

from accelerator.retrieval_core.citations import (
    CitationValidationError,
    SameTurnCitationValidator,
)
from accelerator.retrieval_core.sufficiency import (
    EvidenceSufficiencyChecker,
    SufficiencyPolicy,
)


@dataclass(frozen=True)
class Hit:
    chunk_id: str
    score: float
    reranker_score: float | None


def test_checker_uses_retrieval_score_by_default() -> None:
    checker = EvidenceSufficiencyChecker(SufficiencyPolicy(minimum_score=0.5, minimum_evidence_count=1))

    decision = asyncio.run(checker.evaluate([Hit("a", 0.6, None), Hit("b", 0.1, 3.9)]))

    assert decision.sufficient
    assert decision.evidence_ids == ["a"]


def test_checker_can_gate_on_semantic_reranker_score() -> None:
    checker = EvidenceSufficiencyChecker(
        SufficiencyPolicy(minimum_score=2.0, minimum_evidence_count=1),
        score_field="reranker_score",
    )

    decision = asyncio.run(checker.evaluate([Hit("a", 0.03, 1.2), Hit("b", 0.01, 2.5)]))

    assert decision.sufficient
    assert decision.evidence_ids == ["b"]


def test_missing_reranker_scores_never_pass_the_gate() -> None:
    checker = EvidenceSufficiencyChecker(
        SufficiencyPolicy(minimum_score=0.0, minimum_evidence_count=1),
        score_field="reranker_score",
    )

    decision = asyncio.run(checker.evaluate([Hit("a", 0.9, None)]))

    assert not decision.sufficient
    assert decision.reason == "No retrieved evidence carried a reranker_score."


def test_validator_rejects_a_fabricated_chunk_id() -> None:
    with pytest.raises(CitationValidationError):
        SameTurnCitationValidator().validate(("chunk-1", "fabricated"), frozenset({"chunk-1"}))


def test_validator_accepts_same_turn_citations_and_records_the_span() -> None:
    attributes: dict[str, bool | int] = {}

    class Span:
        def set_attribute(self, key: str, value: bool | int) -> None:
            attributes[key] = value

    SameTurnCitationValidator(Span).validate(("chunk-1",), frozenset({"chunk-1", "chunk-2"}))

    assert attributes["citations.validation.valid"] is True
    assert attributes["citations.validation.retrieved_count"] == 2
