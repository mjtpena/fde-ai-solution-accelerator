"""Async ``SufficiencyChecker`` for the grounded-answer workflow."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from .policy import SufficiencyDecision, SufficiencyPolicy

ScoreField = Literal["score", "reranker_score"]


class _ScoredEvidence(Protocol):
    @property
    def chunk_id(self) -> str: ...

    @property
    def score(self) -> float: ...

    @property
    def reranker_score(self) -> float | None: ...


@dataclass(frozen=True, slots=True)
class _Scored:
    chunk_id: str
    score: float


class EvidenceSufficiencyChecker:
    """Apply a ``SufficiencyPolicy`` to same-turn evidence on a chosen score scale.

    Hybrid queries with semantic ranking produce fused retrieval scores that are not
    comparable across queries; the semantic reranker score (0-4) is. Evidence without
    the configured score never qualifies, so a ranker outage cannot pass the gate.
    """

    def __init__(self, policy: SufficiencyPolicy, *, score_field: ScoreField = "score") -> None:
        self._policy = policy
        self._score_field = score_field

    async def evaluate(self, evidence: Sequence[_ScoredEvidence]) -> SufficiencyDecision:
        scored = [
            _Scored(item.chunk_id, value)
            for item in evidence
            if (value := getattr(item, self._score_field)) is not None
        ]
        decision = self._policy.evaluate(scored)
        if evidence and not scored:
            return decision.model_copy(
                update={"reason": f"No retrieved evidence carried a {self._score_field}."}
            )
        return decision
