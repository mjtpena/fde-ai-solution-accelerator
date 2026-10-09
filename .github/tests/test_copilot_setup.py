"""The Copilot coding-agent setup must run the same gates as the pull-request quality job."""

from pathlib import Path
from typing import Any

import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / "workflows"


def run_lines(path: Path, job: str) -> list[str]:
    # BaseLoader only builds strings, lists and dicts, so it cannot construct objects.
    text = path.read_text(encoding="utf-8")
    config: dict[str, Any] = yaml.load(text, Loader=yaml.BaseLoader)  # noqa: S506
    return [
        line.strip()
        for step in config["jobs"][job]["steps"]
        for line in step.get("run", "").splitlines()
        if line.strip().startswith("make ")
    ]


def test_copilot_setup_runs_the_quality_gates() -> None:
    quality = run_lines(WORKFLOWS / "pull-request.yml", "quality")
    setup = run_lines(WORKFLOWS / "copilot-setup-steps.yml", "copilot-setup-steps")
    [quality_check] = [line for line in quality if line.startswith("make check ")]
    [setup_check] = [line for line in setup if line.startswith("make check ")]
    # Coverage is what enforces [tool.coverage.report] fail_under.
    for argument in ("SKIP_GENERATOR=1", "--cov ", "--cov-report=term"):
        assert argument in quality_check
        assert argument in setup_check
    for gate in ("make openapi-check", "make eval-smoke"):
        assert gate in quality
        assert gate in setup
