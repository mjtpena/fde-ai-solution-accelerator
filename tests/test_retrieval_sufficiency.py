from dataclasses import dataclass

import pytest

from accelerator.retrieval_core.sufficiency import (
    AbstentionResponse,
    SufficiencyPolicy,
    build_abstention_response,
)


@dataclass
class ScoredChunk:
    chunk_id: str
    score: float


def test_evaluate_accepts_evidence_at_configured_threshold() -> None:
    policy = SufficiencyPolicy(minimum_score=0.7, minimum_evidence_count=2)

    decision = policy.evaluate(
        [
            ScoredChunk("chunk-a", 0.7),
            ScoredChunk("chunk-b", 0.9),
            ScoredChunk("chunk-low", 0.69),
        ]
    )

    assert decision.sufficient is True
    assert decision.evidence_ids == ["chunk-a", "chunk-b"]
    assert decision.reason == "Evidence meets the configured sufficiency thresholds."


def test_evaluate_does_not_count_duplicate_chunk_ids_twice() -> None:
    policy = SufficiencyPolicy(minimum_score=0.5, minimum_evidence_count=2)

    decision = policy.evaluate([ScoredChunk("chunk-a", 0.8), ScoredChunk("chunk-a", 0.9)])

    assert decision.sufficient is False
    assert decision.evidence_ids == ["chunk-a"]


def test_insufficient_evidence_builds_structured_abstention() -> None:
    policy = SufficiencyPolicy(minimum_score=0.8, minimum_evidence_count=1)
    decision = policy.evaluate([ScoredChunk("chunk-low", 0.79)])

    response = build_abstention_response(decision)

    assert response == AbstentionResponse(
        abstained=True,
        reason="0 unique evidence item(s) met the minimum score of 0.8; at least 1 required.",
        evidence_ids=[],
    )


def test_empty_evidence_returns_abstention_with_clear_reason() -> None:
    policy = SufficiencyPolicy(minimum_score=0.5, minimum_evidence_count=1)
    decision = policy.evaluate([])

    response = build_abstention_response(decision)

    assert response.abstained is True
    assert response.reason == "No evidence was retrieved."
    assert response.evidence_ids == []


def test_abstention_response_rejects_sufficient_decisions() -> None:
    policy = SufficiencyPolicy(minimum_score=0.5, minimum_evidence_count=1)
    decision = policy.evaluate([ScoredChunk("chunk-a", 0.5)])

    with pytest.raises(ValueError, match="sufficient evidence"):
        build_abstention_response(decision)


@pytest.mark.parametrize("score", [float("nan"), float("inf"), float("-inf")])
def test_evaluate_rejects_non_finite_evidence_scores(score: float) -> None:
    policy = SufficiencyPolicy(minimum_score=0.5, minimum_evidence_count=1)

    with pytest.raises(ValueError, match="scores must be finite"):
        policy.evaluate([ScoredChunk("chunk-a", score)])


def test_policy_rejects_invalid_thresholds() -> None:
    with pytest.raises(ValueError, match="minimum_score must be finite"):
        SufficiencyPolicy(minimum_score=float("nan"), minimum_evidence_count=1)

    with pytest.raises(ValueError, match="minimum_evidence_count"):
        SufficiencyPolicy(minimum_score=0.5, minimum_evidence_count=0)


@pytest.mark.parametrize("score", [float("nan"), float("inf"), float("-inf")])
def test_policy_rejects_non_finite_score_thresholds(score: float) -> None:
    with pytest.raises(ValueError, match="minimum_score must be finite"):
        SufficiencyPolicy(minimum_score=score, minimum_evidence_count=1)


@pytest.mark.parametrize("count", [True, False, -1, 1.5, float("nan"), float("inf")])
def test_policy_rejects_invalid_evidence_counts(count: int) -> None:
    with pytest.raises(ValueError, match="minimum_evidence_count"):
        SufficiencyPolicy(minimum_score=0.5, minimum_evidence_count=count)


def test_evidence_ids_do_not_leak_between_turns() -> None:
    policy = SufficiencyPolicy(minimum_score=0.5, minimum_evidence_count=1)

    first = policy.evaluate([ScoredChunk("previous-turn", 0.8)])
    second = policy.evaluate([ScoredChunk("current-turn", 0.4)])

    assert first.evidence_ids == ["previous-turn"]
    assert second.sufficient is False
    assert second.evidence_ids == []


def test_abstention_serializes_only_structured_decision_fields() -> None:
    policy = SufficiencyPolicy(minimum_score=0.5, minimum_evidence_count=2)
    decision = policy.evaluate([ScoredChunk("qualified", 0.8)])

    response = build_abstention_response(decision)

    assert response.model_dump(mode="json") == {
        "abstained": True,
        "reason": decision.reason,
        "evidence_ids": ["qualified"],
    }


def test_retrieved_text_is_never_read_by_policy() -> None:
    class UntrustedChunk:
        chunk_id = "untrusted-chunk"
        score = 0.4

        @property
        def text(self) -> str:
            raise AssertionError("Retrieved text must not influence sufficiency")

    decision = SufficiencyPolicy(minimum_score=0.5, minimum_evidence_count=1).evaluate(
        [UntrustedChunk()]
    )

    assert decision.sufficient is False
    assert decision.evidence_ids == []
