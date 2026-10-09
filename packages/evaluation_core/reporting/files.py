"""File contracts shared by smoke and full evaluation reporting."""

import json
from pathlib import Path
import re

from pydantic import Field, TypeAdapter
import yaml

from .comparison import (
    ComparisonReport,
    EvaluationResult,
    MetricName,
    ReportModel,
    Thresholds,
    markdown_summary,
)

SMOKE_METRICS = (
    "citation_validity", "abstention", "tool_selection", "scope_isolation",
    "approval_bypass", "injection_followed",
)


class AcceptedBaselines(ReportModel):
    suites: dict[MetricName, EvaluationResult] = Field(min_length=1)


class SuiteThresholds(ReportModel):
    suites: dict[MetricName, Thresholds] = Field(min_length=1)


def load_comparison_config(
    baseline_path: Path,
    thresholds_path: Path,
    *,
    name: str = "smoke",
) -> tuple[EvaluationResult, Thresholds]:
    """Both project files are required; there is no fixture fallback."""
    accepted: EvaluationResult | AcceptedBaselines = TypeAdapter(
        EvaluationResult | AcceptedBaselines
    ).validate_json(
        baseline_path.read_text(encoding="utf-8")
    )
    configured: Thresholds | SuiteThresholds = TypeAdapter(
        Thresholds | SuiteThresholds
    ).validate_python(
        yaml.safe_load(thresholds_path.read_text(encoding="utf-8"))
    )
    if isinstance(accepted, AcceptedBaselines):
        if name not in accepted.suites:
            raise ValueError(f"Missing accepted baseline suite: {name}")
        baseline = accepted.suites[name]
    else:
        baseline = accepted
    if isinstance(configured, SuiteThresholds):
        if name not in configured.suites:
            raise ValueError(f"Missing threshold suite: {name}")
        thresholds = configured.suites[name]
    else:
        thresholds = configured
    return baseline, thresholds


def write_report(
    report: ComparisonReport,
    output_dir: Path,
    *,
    name: str = "smoke",
    fixture: bool = False,
) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise ValueError("Report name must be a lowercase identifier")
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = report.model_dump(mode="json")
    payload.update(passed=report.passed, fixture=fixture)
    (output_dir / f"{name}.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    markdown = markdown_summary(report, fixture=fixture).replace(
        "## Evaluation smoke:", f"## Evaluation {name}:"
    )
    (output_dir / f"{name}.md").write_text(markdown, encoding="utf-8")
