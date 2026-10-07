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
        step.get("uses") == "actions/upload-artifact@v4" and step.get("if") == "always()"
        for step in smoke["steps"]
    )
    publish = workflow["jobs"]["publish"]
    assert "github.event_name == 'workflow_run'" in publish["if"]
    assert "github.event.workflow_run.event == 'pull_request'" in publish["if"]
    assert publish["permissions"] == {"actions": "read", "pull-requests": "write"}
    assert not any(
        "run" in step or "checkout" in step.get("uses", "")
        for step in publish["steps"]
    )
    scripts = "\n".join(
        step.get("with", {}).get("script", "") for step in publish["steps"]
    )
    assert "pr.head.sha !== run.head_sha" in scripts
    assert "p.head.repo?.full_name === run.head_repository.full_name" in scripts
    assert "github.rest.issues.updateComment" in scripts
    assert "github.rest.issues.createComment" in scripts
    assert "github-actions[bot]" in scripts
    assert "Buffer.byteLength" in scripts
