import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel


class _EvidenceScore(Protocol):
    @property
    def chunk_id(self) -> str: ...

    @property
    def score(self) -> float: ...


class SufficiencyDecision(BaseModel):
    sufficient: bool
    reason: str
    evidence_ids: list[str]


class AbstentionResponse(BaseModel):
    abstained: Literal[True] = True
    reason: str
    evidence_ids: list[str]


@dataclass(frozen=True, slots=True)
class SufficiencyPolicy:
    minimum_score: float
    minimum_evidence_count: int

    def __post_init__(self) -> None:
        if not math.isfinite(self.minimum_score):
            raise ValueError("minimum_score must be finite")
        if (
            isinstance(self.minimum_evidence_count, bool)
            or not isinstance(self.minimum_evidence_count, int)
            or self.minimum_evidence_count < 1
        ):
            raise ValueError("minimum_evidence_count must be an integer of at least 1")

    def evaluate(self, evidence: Sequence[_EvidenceScore]) -> SufficiencyDecision:
        qualified_scores: dict[str, float] = {}
        for item in evidence:
            if not math.isfinite(item.score):
                raise ValueError("evidence scores must be finite")
            if item.score >= self.minimum_score:
                previous_score = qualified_scores.get(item.chunk_id)
                if previous_score is None or item.score > previous_score:
                    qualified_scores[item.chunk_id] = item.score

        evidence_ids = list(qualified_scores)
        sufficient = len(evidence_ids) >= self.minimum_evidence_count
        if sufficient:
            reason = "Evidence meets the configured sufficiency thresholds."
        elif not evidence:
            reason = "No evidence was retrieved."
        else:
            reason = (
                f"{len(evidence_ids)} unique evidence item(s) met the minimum score "
                f"of {self.minimum_score:g}; at least {self.minimum_evidence_count} required."
            )

        return SufficiencyDecision(
            sufficient=sufficient,
            reason=reason,
            evidence_ids=evidence_ids,
        )


def build_abstention_response(decision: SufficiencyDecision) -> AbstentionResponse:
    if decision.sufficient:
        raise ValueError("cannot create an abstention response for sufficient evidence")
    return AbstentionResponse(reason=decision.reason, evidence_ids=decision.evidence_ids)
