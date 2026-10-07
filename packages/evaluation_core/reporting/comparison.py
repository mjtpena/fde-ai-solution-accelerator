"""Strict, adapter-independent baseline comparisons."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StringConstraints

MetricName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]


class ReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class EvaluationResult(ReportModel):
    metrics: dict[MetricName, FiniteFloat] = Field(min_length=1)
    hard_failures: tuple[MetricName, ...] = ()


class MetricThreshold(ReportModel):
    direction: Literal["higher", "lower"]
    tolerance: Annotated[FiniteFloat, Field(ge=0)]


class Thresholds(ReportModel):
    metrics: dict[MetricName, MetricThreshold] = Field(min_length=1)


class MetricComparison(ReportModel):
    name: MetricName
    baseline: FiniteFloat
    current: FiniteFloat
    delta: FiniteFloat
    direction: Literal["higher", "lower"]
    tolerance: FiniteFloat
    passed: bool


class ComparisonReport(ReportModel):
    metrics: tuple[MetricComparison, ...]
    hard_failures: tuple[MetricName, ...]

    @property
    def passed(self) -> bool:
        return not self.hard_failures and all(metric.passed for metric in self.metrics)


def compare(
    current: EvaluationResult, baseline: EvaluationResult, thresholds: Thresholds
) -> ComparisonReport:
    """Compare absolute metric movement; equality at the tolerance passes."""
    if baseline.hard_failures:
        raise ValueError("An accepted baseline cannot contain hard-gate failures")
    if current.metrics.keys() != baseline.metrics.keys():
        raise ValueError("Current and baseline metric names must match exactly")
    if baseline.metrics.keys() != thresholds.metrics.keys():
        raise ValueError("Every baseline metric must have exactly one threshold")
    comparisons: list[MetricComparison] = []
    for name, accepted in sorted(baseline.metrics.items()):
        measured = current.metrics[name]
        rule = thresholds.metrics[name]
        delta = Decimal(str(measured)) - Decimal(str(accepted))
        regression = -delta if rule.direction == "higher" else delta
        comparisons.append(
            MetricComparison(
                name=name,
                baseline=accepted,
                current=measured,
                delta=float(delta),
                direction=rule.direction,
                tolerance=rule.tolerance,
                passed=regression <= Decimal(str(rule.tolerance)),
            )
        )
    return ComparisonReport(
        metrics=tuple(comparisons), hard_failures=tuple(sorted(set(current.hard_failures)))
    )


def markdown_summary(report: ComparisonReport, *, fixture: bool = False) -> str:
    status = "PASS" if report.passed else "FAIL"
    lines = [
        "<!-- evaluation-report -->",
        f"## Evaluation smoke: {status}",
        "",
        (
            "Deterministic fixture comparison only; not an accepted project baseline."
            if fixture
            else "Compared with the accepted project baseline using absolute tolerances."
        ),
        "",
        "| Metric | Baseline | Current | Delta | Direction | Tolerance | Result |",
        "| --- | ---: | ---: | ---: | --- | ---: | --- |",
    ]
    for metric in report.metrics:
        lines.append(
            f"| {metric.name} | {metric.baseline:.6g} | {metric.current:.6g} | "
            f"{metric.delta:+.6g} | {metric.direction} | {metric.tolerance:.6g} | "
            f"{'PASS' if metric.passed else 'FAIL'} |"
        )
    lines.extend(["", f"Hard gates: {'FAIL' if report.hard_failures else 'PASS'}"])
    lines.extend(f"- {failure}" for failure in report.hard_failures)
    return "\n".join(lines) + "\n"
