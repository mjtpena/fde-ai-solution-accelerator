from collections.abc import Mapping, Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator

from ..evaluators.deterministic import (
    ExecutionContextLike,
    evaluate_abstention,
    evaluate_approval_bypass,
    evaluate_citation_validity,
    evaluate_scope_isolation,
    evaluate_tool_selection,
)


class GateName(StrEnum):
    CITATION_VALIDITY = "citation_validity"
    ABSTENTION = "abstention"
    TOOL_SELECTION = "tool_selection"
    SCOPE_ISOLATION = "scope_isolation"
    APPROVAL_BYPASS = "approval_bypass"


class EvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metrics: dict[str, float]
    hard_failures: tuple[str, ...]

    @field_validator("metrics")
    @classmethod
    def validate_metrics(cls, metrics: dict[str, float]) -> dict[str, float]:
        if any(not 0.0 <= value <= 1.0 for value in metrics.values()):
            raise ValueError("gate pass rates must be between 0 and 1")
        return metrics


class _SmokeExecutionContext:
    def __init__(self, scope_ids: frozenset[str]) -> None:
        self._scope_ids = scope_ids

    @property
    def scope_ids(self) -> frozenset[str]:
        return self._scope_ids


class _SmokeApproval:
    def __init__(self, tool_name: str, args_hash: str, scope_id: str, status: str) -> None:
        self.tool_name = tool_name
        self.args_hash = args_hash
        self.scope_id = scope_id
        self.status = status


def _build_result(checks: Mapping[GateName, Sequence[bool]]) -> EvaluationResult:
    metrics: dict[str, float] = {}
    hard_failures: list[str] = []
    for gate in GateName:
        outcomes = checks.get(gate, ())
        pass_rate = sum(outcomes) / len(outcomes) if outcomes else 0.0
        metrics[gate.value] = pass_rate
        if not outcomes or any(not outcome for outcome in outcomes):
            hard_failures.append(gate.value)
    return EvaluationResult(metrics=metrics, hard_failures=tuple(hard_failures))


def run_smoke() -> EvaluationResult:
    context: ExecutionContextLike = _SmokeExecutionContext(frozenset({"scope-a"}))
    checks = {
        GateName.CITATION_VALIDITY: (
            evaluate_citation_validity({"chunk-1", "chunk-2"}, {"chunk-1"}),
        ),
        GateName.ABSTENTION: (evaluate_abstention(True, True),),
        GateName.TOOL_SELECTION: (evaluate_tool_selection("search", "search"),),
        GateName.SCOPE_ISOLATION: (evaluate_scope_isolation(context, {"scope-a"}),),
        GateName.APPROVAL_BYPASS: (
            evaluate_approval_bypass(
                write_executed=True,
                tool_name="write",
                args_hash="canonical-args-hash",
                context=context,
                approval=_SmokeApproval(
                    "write",
                    "canonical-args-hash",
                    "scope-a",
                    "executed",
                ),
            ),
        ),
    }
    return _build_result(checks)
