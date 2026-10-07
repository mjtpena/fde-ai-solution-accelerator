"""Regression checks for the repository's merge-blocking scanner configuration."""

from pathlib import Path
from typing import Any

import pytest
import yaml

GITHUB = Path(__file__).resolve().parents[1]


def load_config(path: Path) -> dict[str, Any]:
    # BaseLoader preserves the GitHub Actions `on` key instead of YAML 1.1 booleans.
    config: dict[str, Any] = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    return config


@pytest.fixture
def workflow() -> dict[str, Any]:
    return load_config(GITHUB / "workflows" / "security.yml")


def test_scans_run_on_every_pull_request_and_default_branch(
    workflow: dict[str, Any],
) -> None:
    events = workflow["on"]
    assert events["pull_request"] == ""
    assert events["merge_group"] == ""
    assert events["push"] == {"branches": ["main"]}
    assert events["schedule"]
    assert "workflow_dispatch" in events
    assert "pull_request_target" not in events
    assert workflow["permissions"] == {"contents": "read"}


def test_codeql_covers_all_languages_with_extended_queries(
    workflow: dict[str, Any],
) -> None:
    job = workflow["jobs"]["codeql"]
    assert set(job["strategy"]["matrix"]["language"]) == {
        "python", "javascript-typescript", "actions"
    }
    assert job["strategy"]["fail-fast"] == "false"
    assert job["permissions"] == {"contents": "read", "security-events": "write"}
    init = next(step for step in job["steps"] if "/init@" in step.get("uses", ""))
    assert init["with"]["queries"] == "security-extended"
    assert init["with"]["build-mode"] == "none"
    assert any("/analyze@" in step.get("uses", "") for step in job["steps"])


@pytest.mark.parametrize("job_id", ["trivy", "trivy-images"])
def test_trivy_findings_fail_without_ignoring_unfixed_issues(
    workflow: dict[str, Any], job_id: str
) -> None:
    job = workflow["jobs"][job_id]
    step = next(step for step in job["steps"] if "trivy-action@" in step.get("uses", ""))
    options = step["with"]
    assert options["exit-code"] == "1"
    assert options["severity"] == "HIGH,CRITICAL"
    assert options["ignore-unfixed"] == "false"
    assert "vuln" in options["scanners"].split(",")
    assert "secret" in options["scanners"].split(",")
    assert "trivyignores" not in options
    if job_id == "trivy":
        assert options["scan-type"] == "fs"
        assert options["scan-ref"] == "."
        assert "misconfig" in options["scanners"].split(",")
        assert options["skip-dirs"] == ".git"
    else:
        assert job["strategy"]["matrix"]["include"] == [
            {"image": "web", "dockerfile": "apps/web/Dockerfile"},
            {"image": "ingestion", "dockerfile": "workers/ingestion/Dockerfile"},
        ]
        assert any("docker build" in step.get("run", "") for step in job["steps"])


def test_dependency_review_rejects_high_severity_changes(
    workflow: dict[str, Any],
) -> None:
    job = workflow["jobs"]["dependency-review"]
    assert job["if"] == "github.event_name == 'pull_request'"
    step = next(step for step in job["steps"] if "dependency-review-action@" in step.get("uses", ""))
    assert step["with"]["fail-on-severity"] == "high"


def test_scanner_failures_are_not_suppressed(workflow: dict[str, Any]) -> None:
    for job in workflow["jobs"].values():
        assert "continue-on-error" not in job
        for step in job["steps"]:
            assert "continue-on-error" not in step
            assert "|| true" not in step.get("run", "")
            if "checkout@" in step.get("uses", ""):
                assert step["with"]["persist-credentials"] == "false"


def test_dependabot_covers_locked_workspaces_actions_and_images() -> None:
    config = load_config(GITHUB / "dependabot.yml")
    assert config["version"] == "2"
    updates = {update["package-ecosystem"]: update for update in config["updates"]}
    assert set(updates) == {"uv", "npm", "github-actions", "docker"}
    for ecosystem in ["uv", "npm", "github-actions"]:
        assert updates[ecosystem]["directory"] == "/"
    assert set(updates["docker"]["directories"]) == {"/apps/web", "/workers/ingestion"}
    assert all(update["schedule"]["interval"] == "weekly" for update in updates.values())
    assert all(int(update["open-pull-requests-limit"]) > 0 for update in updates.values())
