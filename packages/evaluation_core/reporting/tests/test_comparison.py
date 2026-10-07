import pytest
from pydantic import ValidationError

from ..comparison import (
    EvaluationResult,
    MetricThreshold,
    Thresholds,
    compare,
    markdown_summary,
)


@pytest.mark.parametrize(
    ("direction", "baseline", "current", "tolerance", "passed"),
    [
        ("higher", 0.9, 0.8, 0.1, True),
        ("higher", 0.9, 0.799999, 0.1, False),
        ("higher", 0.9, 1.0, 0.0, True),
        ("higher", 0.9, 0.9, 0.0, True),
        ("lower", 100.0, 110.0, 10.0, True),
        ("lower", 100.0, 110.000001, 10.0, False),
        ("lower", 100.0, 90.0, 0.0, True),
        ("lower", 100.0, 100.000001, 0.0, False),
    ],
)
def test_absolute_tolerance_boundaries(
    direction: str, baseline: float, current: float, tolerance: float, passed: bool
) -> None:
    thresholds = Thresholds.model_validate(
        {"metrics": {"score": {"direction": direction, "tolerance": tolerance}}}
    )
    report = compare(
        EvaluationResult(metrics={"score": current}),
        EvaluationResult(metrics={"score": baseline}),
        thresholds,
    )
    assert report.passed is passed


def test_hard_failure_cannot_be_hidden_by_metric_improvement() -> None:
    report = compare(
        EvaluationResult(metrics={"score": 1.0}, hard_failures=("scope_isolation",)),
        EvaluationResult(metrics={"score": 0.9}),
        Thresholds(metrics={"score": MetricThreshold(direction="higher", tolerance=1.0)}),
    )
    assert not report.passed
    assert "Hard gates: FAIL\n- scope_isolation" in markdown_summary(report)


@pytest.mark.parametrize(
    ("current", "baseline", "thresholds"),
    [
        ({"other": 1.0}, {"score": 1.0}, {"score": {"direction": "higher", "tolerance": 0}}),
        ({"score": 1.0}, {"score": 1.0}, {"other": {"direction": "higher", "tolerance": 0}}),
    ],
)
def test_missing_or_unconfigured_metrics_fail_explicitly(
    current: dict[str, float], baseline: dict[str, float], thresholds: dict[str, object]
) -> None:
    with pytest.raises(ValueError, match="metric|threshold"):
        compare(
            EvaluationResult(metrics=current),
            EvaluationResult(metrics=baseline),
            Thresholds.model_validate({"metrics": thresholds}),
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_results_are_rejected(value: float) -> None:
    with pytest.raises(ValidationError):
        EvaluationResult(metrics={"score": value})


@pytest.mark.parametrize("value", [-0.1, float("inf"), float("nan")])
def test_invalid_tolerances_are_rejected(value: float) -> None:
    with pytest.raises(ValidationError):
        MetricThreshold(direction="higher", tolerance=value)


def test_accepted_baseline_with_safety_failure_is_rejected() -> None:
    with pytest.raises(ValueError, match="accepted baseline"):
        compare(
            EvaluationResult(metrics={"score": 1}),
            EvaluationResult(metrics={"score": 1}, hard_failures=("injection_followed",)),
            Thresholds(metrics={"score": MetricThreshold(direction="higher", tolerance=0)}),
        )


def test_report_shape_and_markdown_are_stable() -> None:
    report = compare(
        EvaluationResult(metrics={"score": 0.8}),
        EvaluationResult(metrics={"score": 0.9}),
        Thresholds(metrics={"score": MetricThreshold(direction="higher", tolerance=0.05)}),
    )
    assert report.model_dump(mode="json") == {
        "metrics": [
            {
                "name": "score", "baseline": 0.9, "current": 0.8, "delta": -0.1,
                "direction": "higher", "tolerance": 0.05, "passed": False,
            }
        ],
        "hard_failures": [],
    }
    assert markdown_summary(report) == (
        "<!-- evaluation-report -->\n"
        "## Evaluation smoke: FAIL\n\n"
        "Compared with the accepted project baseline using absolute tolerances.\n\n"
        "| Metric | Baseline | Current | Delta | Direction | Tolerance | Result |\n"
        "| --- | ---: | ---: | ---: | --- | ---: | --- |\n"
        "| score | 0.9 | 0.8 | -0.1 | higher | 0.05 | FAIL |\n\n"
        "Hard gates: PASS\n"
    )
    assert "not an accepted project baseline" in markdown_summary(report, fixture=True)


@pytest.mark.parametrize("name", ["a|b", "a\nb", "<script>", ""])
def test_metric_names_cannot_inject_markdown(name: str) -> None:
    with pytest.raises(ValidationError):
        EvaluationResult(metrics={name: 1})
