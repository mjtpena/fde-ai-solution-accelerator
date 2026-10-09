import json
from pathlib import Path

import pytest

from ..comparison import compare
from ..files import load_comparison_config, write_report


def test_suite_config_and_report_file_shape(tmp_path: Path) -> None:
    baseline = tmp_path / "accepted.json"
    baseline.write_text(
        '{"suites":{"smoke":{"metrics":{"citation_validity":1}},'
        '"full":{"metrics":{"groundedness":0.9}}}}',
        encoding="utf-8",
    )
    thresholds = tmp_path / "thresholds.yml"
    thresholds.write_text(
        "suites:\n"
        "  smoke:\n    metrics:\n      citation_validity:\n"
        "        direction: higher\n        tolerance: 0\n"
        "  full:\n    metrics:\n      groundedness:\n"
        "        direction: higher\n        tolerance: 0.1\n",
        encoding="utf-8",
    )
    accepted, rules = load_comparison_config(baseline, thresholds, name="full")
    assert accepted.metrics == {"groundedness": 0.9}
    report = compare(accepted, accepted, rules)
    write_report(report, tmp_path, name="full")
    payload = json.loads((tmp_path / "full.json").read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert payload["fixture"] is False
    assert payload["metrics"][0]["name"] == "groundedness"
    assert "## Evaluation full: PASS" in (tmp_path / "full.md").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="Missing accepted baseline suite"):
        load_comparison_config(baseline, thresholds, name="unknown")


def test_threshold_suite_is_required_explicitly(tmp_path: Path) -> None:
    baseline = tmp_path / "accepted.json"
    baseline.write_text('{"metrics":{"score":1}}', encoding="utf-8")
    thresholds = tmp_path / "thresholds.yml"
    thresholds.write_text(
        "suites:\n  full:\n    metrics:\n      score:\n"
        "        direction: higher\n        tolerance: 0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Missing threshold suite"):
        load_comparison_config(baseline, thresholds)


def test_missing_files_are_errors_for_every_suite(tmp_path: Path) -> None:
    baseline, thresholds = tmp_path / "accepted.json", tmp_path / "thresholds.yml"
    with pytest.raises(FileNotFoundError):
        load_comparison_config(baseline, thresholds)
    with pytest.raises(FileNotFoundError):
        load_comparison_config(baseline, thresholds, name="full")
    assert not baseline.exists()
    assert not thresholds.exists()
