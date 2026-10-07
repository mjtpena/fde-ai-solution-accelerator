"""Run deterministic evaluators and publish baseline movement."""

import argparse
import json
import logging
from pathlib import Path

from pydantic import ValidationError
import yaml

from ..runners import run_smoke
from .comparison import EvaluationResult, compare
from .files import load_comparison_config, write_report

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=Path("evaluations/baselines/accepted.json"))
    parser.add_argument("--thresholds", type=Path, default=Path("evaluations/thresholds.yml"))
    parser.add_argument("--output-dir", type=Path, default=Path("evaluations/reports"))
    parser.add_argument("--allow-fixture", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    context = {"correlation_id": "evaluation-smoke"}
    try:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "smoke.json").unlink(missing_ok=True)
        (args.output_dir / "smoke.md").unlink(missing_ok=True)
        measured = run_smoke()
        current = EvaluationResult(metrics=measured.metrics, hard_failures=measured.hard_failures)
        baseline, thresholds, fixture = load_comparison_config(
            args.baseline, args.thresholds, allow_fixture=args.allow_fixture
        )
        if fixture:
            logger.warning(
                "correlation_id=evaluation-smoke missing baseline=%s thresholds=%s; "
                "using explicit deterministic fixtures, not a project baseline",
                args.baseline, args.thresholds, extra=context,
            )
        report = compare(current, baseline, thresholds)
        write_report(report, args.output_dir, fixture=fixture)
    except (OSError, ValueError, ValidationError, yaml.YAMLError) as error:
        logger.exception("correlation_id=evaluation-smoke evaluation failed", extra=context)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "smoke.json").write_text(
            json.dumps({"passed": False, "error": type(error).__name__}) + "\n",
            encoding="utf-8",
        )
        (args.output_dir / "smoke.md").write_text(
            "<!-- evaluation-report -->\n## Evaluation smoke: ERROR\n\n"
            "Evaluation/configuration failed; see workflow logs. No baseline comparison passed.\n",
            encoding="utf-8",
        )
        return 2
    logger.info(
        "correlation_id=evaluation-smoke passed=%s fixture=%s metrics=%d hard_failures=%d",
        report.passed, fixture, len(report.metrics), len(report.hard_failures), extra=context,
    )
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
