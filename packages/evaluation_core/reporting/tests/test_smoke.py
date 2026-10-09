import json
from pathlib import Path

import pytest

from ...runners import EvaluationResult as RunnerResult
from .. import smoke
from ..comparison import EvaluationResult, compare
from ..files import (
    SMOKE_METRICS,
    load_comparison_config,
    write_report,
)


def configure(tmp_path: Path) -> tuple[Path, Path]:
    baseline = tmp_path / "accepted.json"
    baseline.write_text(
        EvaluationResult(metrics={name: 1.0 for name in SMOKE_METRICS}).model_dump_json(),
        encoding="utf-8",
    )
    thresholds = tmp_path / "thresholds.yml"
    thresholds.write_text(
        "metrics:\n" + "".join(
            f"  {name}:\n    direction: higher\n    tolerance: 0.0\n" for name in SMOKE_METRICS
        ),
        encoding="utf-8",
    )
    return baseline, thresholds


ROOT = Path(__file__).resolve().parents[4]


def cli_args(tmp_path: Path) -> list[str]:
    return [
        "--baseline", str(tmp_path / "accepted.json"),
        "--thresholds", str(tmp_path / "thresholds.yml"),
        "--output-dir", str(tmp_path / "reports"),
    ]


def test_real_smoke_passes_against_the_committed_baseline_and_example_thresholds(
    tmp_path: Path,
) -> None:
    args = [
        "--baseline", str(ROOT / "evaluations/baselines/accepted.json"),
        "--thresholds", str(ROOT / "evaluations/thresholds.example.yml"),
        "--dataset", str(ROOT / "evaluations/example-datasets/smoke.jsonl"),
        "--corpus", str(ROOT / "tests/fixtures/retrieval/corpus"),
        "--output-dir", str(tmp_path / "reports"),
    ]
    assert smoke.main(args) == 0
    payload = json.loads((tmp_path / "reports" / "smoke.json").read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert payload["fixture"] is False
    assert payload["hard_failures"] == []
    assert {row["name"] for row in payload["metrics"]} == set(SMOKE_METRICS)
    assert len(payload["metrics"]) == 6
    assert all(row["current"] == row["baseline"] == 1.0 for row in payload["metrics"])
    assert "not an accepted project baseline" not in (
        tmp_path / "reports" / "smoke.md"
    ).read_text(encoding="utf-8")


def test_the_fixture_fallback_flag_no_longer_exists(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as raised:
        smoke.main(cli_args(tmp_path) + ["--allow-fixture"])
    assert raised.value.code == 2
    assert not (tmp_path / "reports").exists()


def test_project_baseline_files_are_read_only(tmp_path: Path) -> None:
    baseline, thresholds = configure(tmp_path)
    originals = baseline.read_bytes(), thresholds.read_bytes()
    assert smoke.main(cli_args(tmp_path)) == 0
    assert (baseline.read_bytes(), thresholds.read_bytes()) == originals
    payload = json.loads((tmp_path / "reports" / "smoke.json").read_text(encoding="utf-8"))
    assert payload["fixture"] is False


@pytest.mark.parametrize("missing", ["accepted.json", "thresholds.yml", "both"])
def test_partial_or_missing_project_config_does_not_pass(
    tmp_path: Path, missing: str
) -> None:
    baseline, thresholds = configure(tmp_path)
    if missing in ("accepted.json", "both"):
        baseline.unlink()
    if missing in ("thresholds.yml", "both"):
        thresholds.unlink()
    assert smoke.main(cli_args(tmp_path)) == 2
    payload = json.loads((tmp_path / "reports" / "smoke.json").read_text(encoding="utf-8"))
    assert payload == {"passed": False, "error": "FileNotFoundError"}
    assert "ERROR" in (tmp_path / "reports" / "smoke.md").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "gate", ["citation_validity", "scope_isolation", "approval_bypass", "injection_followed"]
)
def test_every_hard_failure_exits_nonzero_despite_generous_tolerance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gate: str
) -> None:
    _, thresholds = configure(tmp_path)
    thresholds.write_text(
        thresholds.read_text(encoding="utf-8").replace("tolerance: 0.0", "tolerance: 100.0"),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        smoke, "run_smoke",
        lambda *_: RunnerResult(
            metrics={name: 1.0 for name in SMOKE_METRICS}, hard_failures=(gate,)
        ),
    )
    assert smoke.main(cli_args(tmp_path)) == 1
    payload = json.loads((tmp_path / "reports" / "smoke.json").read_text(encoding="utf-8"))
    assert payload["passed"] is False
    assert payload["hard_failures"] == [gate]
    assert "Hard gates: FAIL" in (
        tmp_path / "reports" / "smoke.md"
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize("metric", ["abstention", "tool_selection"])
def test_quality_metrics_use_tolerance_without_creating_hard_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, metric: str
) -> None:
    _, thresholds = configure(tmp_path)
    thresholds.write_text(
        thresholds.read_text(encoding="utf-8").replace("tolerance: 0.0", "tolerance: 0.1"),
        encoding="utf-8",
    )
    metrics = {name: 1.0 for name in SMOKE_METRICS}
    metrics[metric] = 0.9
    monkeypatch.setattr(
        smoke, "run_smoke", lambda *_: RunnerResult(metrics=metrics, hard_failures=())
    )
    assert smoke.main(cli_args(tmp_path)) == 0
    payload = json.loads((tmp_path / "reports" / "smoke.json").read_text(encoding="utf-8"))
    assert payload["hard_failures"] == []
    metrics[metric] = 0.899
    assert smoke.main(cli_args(tmp_path)) == 1
    payload = json.loads((tmp_path / "reports" / "smoke.json").read_text(encoding="utf-8"))
    assert payload["hard_failures"] == []


def test_regression_artifacts_are_written_before_failure_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(tmp_path)
    monkeypatch.setattr(
        smoke, "run_smoke",
        lambda *_: RunnerResult(
            metrics={name: 0.999 for name in SMOKE_METRICS}, hard_failures=()
        ),
    )
    assert smoke.main(cli_args(tmp_path)) == 1
    payload = json.loads((tmp_path / "reports" / "smoke.json").read_text(encoding="utf-8"))
    assert all(not row["passed"] for row in payload["metrics"])
    assert "## Evaluation smoke: FAIL" in (
        tmp_path / "reports" / "smoke.md"
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "content",
    [
        "metrics: [",
        "metrics: {}",
        "metrics:\n  unexpected: 1",
        "!!python/object/apply:os.system []",
    ],
)
def test_invalid_yaml_fails(tmp_path: Path, content: str) -> None:
    _, thresholds = configure(tmp_path)
    thresholds.write_text(content, encoding="utf-8")
    assert smoke.main(cli_args(tmp_path)) == 2


def test_invalid_baseline_fails(tmp_path: Path) -> None:
    baseline, _ = configure(tmp_path)
    baseline.write_text('{"metrics": {"score": NaN}}', encoding="utf-8")
    assert smoke.main(cli_args(tmp_path)) == 2


def test_error_exit_code_is_preserved_when_artifact_writing_also_fails(
    tmp_path: Path,
) -> None:
    """The documented exit code 2 must not be masked by a secondary OSError
    raised while writing the best-effort error artifacts themselves."""
    baseline, _ = configure(tmp_path)
    baseline.write_text("not valid json", encoding="utf-8")
    # A file in place of the reports directory makes mkdir/write_text in the
    # error handler raise OSError, independent of the original failure.
    blocked_output = tmp_path / "reports"
    blocked_output.write_text("not a directory", encoding="utf-8")
    args = [
        "--baseline", str(baseline),
        "--thresholds", str(tmp_path / "thresholds.yml"),
        "--output-dir", str(blocked_output),
    ]
    assert smoke.main(args) == 2


def test_full_report_writer_uses_requested_name(tmp_path: Path) -> None:
    baseline, thresholds = load_comparison_config(*configure(tmp_path))
    report = compare(baseline, baseline, thresholds)
    write_report(report, tmp_path / "reports", name="full")
    assert (tmp_path / "reports" / "full.json").exists()
    assert "## Evaluation full: PASS" in (
        tmp_path / "reports" / "full.md"
    ).read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="Report name"):
        write_report(report, tmp_path / "reports", name="../unsafe")


def test_suite_baselines_preserve_distinct_smoke_and_full_metrics(tmp_path: Path) -> None:
    baseline, thresholds = configure(tmp_path)
    smoke_baseline = json.loads(baseline.read_text(encoding="utf-8"))
    baseline.write_text(
        json.dumps({
            "suites": {
                "smoke": smoke_baseline,
                "full": {"metrics": {"groundedness": 0.9}, "hard_failures": []},
            }
        }),
        encoding="utf-8",
    )
    thresholds.write_text(
        "suites:\n  smoke:\n    " + thresholds.read_text(encoding="utf-8").replace(
            "\n", "\n    "
        ).rstrip() + "\n  full:\n    metrics:\n      groundedness:\n"
        "        direction: higher\n        tolerance: 0.1\n",
        encoding="utf-8",
    )
    assert smoke.main(cli_args(tmp_path)) == 0
    full_baseline, full_thresholds = load_comparison_config(baseline, thresholds, name="full")
    assert full_baseline.metrics == {"groundedness": 0.9}
    assert full_thresholds.metrics["groundedness"].tolerance == 0.1
    with pytest.raises(ValueError, match="Missing accepted baseline suite"):
        load_comparison_config(baseline, thresholds, name="unknown")


def test_missing_project_files_never_fall_back_for_any_suite(tmp_path: Path) -> None:
    for name in ("smoke", "full"):
        with pytest.raises(FileNotFoundError):
            load_comparison_config(
                tmp_path / "accepted.json", tmp_path / "thresholds.yml", name=name
            )


def test_cli_passes_dataset_and_corpus_to_the_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(tmp_path)
    seen: list[tuple[Path, Path]] = []

    def record(dataset: Path, corpus: Path) -> RunnerResult:
        seen.append((dataset, corpus))
        return RunnerResult(metrics={name: 1.0 for name in SMOKE_METRICS}, hard_failures=())

    monkeypatch.setattr(smoke, "run_smoke", record)
    args = cli_args(tmp_path) + ["--dataset", "d.jsonl", "--corpus", "corpus"]

    assert smoke.main(args) == 0
    assert seen == [(Path("d.jsonl"), Path("corpus"))]
