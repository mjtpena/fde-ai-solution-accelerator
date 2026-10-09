import re
from pathlib import Path

import yaml


def test_workflow_separates_pr_execution_from_privileged_commenting() -> None:
    root = Path(__file__).resolve().parents[4]
    workflow = yaml.safe_load(
        (root / ".github" / "workflows" / "evaluation.yml").read_text(encoding="utf-8")
    )
    assert workflow["permissions"] == {"contents": "read"}
    smoke = workflow["jobs"]["smoke"]
    assert smoke["if"] == "github.event_name == 'pull_request'"
    assert smoke["permissions"] == {"contents": "read"}
    assert any(step.get("run") == "make eval-smoke" for step in smoke["steps"])
    assert any(
        step.get("uses", "").startswith("actions/upload-artifact@") and step.get("if") == "always()"
        for step in smoke["steps"]
    )
    assert set(workflow[True]) == {"pull_request"}
    report = yaml.safe_load(
        (root / ".github" / "workflows" / "evaluation-report.yml").read_text(encoding="utf-8")
    )
    # PyYAML reads the bare `on` key as True.
    assert report[True] == {"workflow_run": {"workflows": ["Evaluation"], "types": ["completed"]}}
    assert report["permissions"] == {"contents": "read"}
    publish = report["jobs"]["publish"]
    assert "github.event_name == 'workflow_run'" in publish["if"]
    assert "github.event.workflow_run.event == 'pull_request'" in publish["if"]
    assert publish["permissions"] == {"actions": "read", "pull-requests": "write"}
    assert publish["concurrency"]["cancel-in-progress"] is False
    assert "head_repository.full_name" in publish["concurrency"]["group"]
    assert "head_branch" in publish["concurrency"]["group"]
    assert not any("run" in step or "checkout" in step.get("uses", "") for step in publish["steps"])
    scripts = "\n".join(step.get("with", {}).get("script", "") for step in publish["steps"])
    assert "pr.head.sha !== run.head_sha" in scripts
    assert "p.head.repo?.full_name === run.head_repository.full_name" in scripts
    assert "p.head.ref === run.head_branch" in scripts
    assert "(run.pull_requests || []).map(pull => pull.number)" in scripts
    assert "matches = matches.filter(p => runPrNumbers.has(p.number))" in scripts
    assert "matches.length > 1" in scripts
    assert "core.setFailed" in scripts
    assert "github.rest.issues.updateComment" in scripts
    assert "github.rest.issues.createComment" in scripts
    assert "github-actions[bot]" in scripts
    assert "Buffer.byteLength" in scripts


def test_every_action_is_pinned_to_a_commit_sha() -> None:
    root = Path(__file__).resolve().parents[4]
    for path in sorted((root / ".github" / "workflows").glob("*.yml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job in workflow["jobs"].values():
            for step in job.get("steps", ()):
                if "uses" in step:
                    reference = step["uses"].split("@", 1)[1]
                    assert re.fullmatch(r"[0-9a-f]{40}", reference), (path.name, step["uses"])
