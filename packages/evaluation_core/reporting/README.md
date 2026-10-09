# Evaluation reporting

`make eval-smoke` runs the offline smoke evaluation (`runners.run_smoke()`, see
`runners/README.md`) over `evaluations/example-datasets/smoke.jsonl`, fails on
any hard-gate failure or out-of-tolerance regression, and writes
`evaluations/reports/smoke.json` and `evaluations/reports/smoke.md`.
Exit codes: 0 passed, 1 gate/regression failure, 2 configuration/reporting error.
Reports contain aggregate metrics only, not prompts or retrieved text.

The smoke runner returns seven pass-rate metrics: `citation_validity`, `abstention`,
`tool_selection`, `scope_isolation`, `approval_bypass`, `injection_followed`, and
`content_safety`.
`injection_followed` measures the fraction of observations where injection was
**not** followed (higher is better). The runner owns hard-gate classification:
citation validity, scope isolation, approval bypass, injection resistance and content safety
populate `hard_failures`; abstention and tool selection are tolerance-based
metrics only. Reporting preserves those failures without redefining gate policy.

`evaluations/baselines/accepted.json` holds the accepted smoke baseline, and
`evaluations/thresholds.example.yml` the shipped tolerances. `make eval-smoke`
uses `evaluations/thresholds.yml` instead when a project creates one. Both files
are required: there is no fixture fallback, and a missing or invalid file is a
configuration error (exit 2). Baseline and threshold changes are separate,
reviewed PRs, never part of a feature PR.

The `Evaluation` workflow runs this on every pull request and fails the job on
any hard-gate failure or regression beyond tolerance. Make its `smoke` job a
required status check in branch protection so a failing gate blocks merge.

Accepted JSON shape (project-owned; baseline updates require a separate PR):

```json
{"metrics": {"citation_validity": 1.0}, "hard_failures": []}
```

Threshold YAML shape (include exactly every baseline metric):

```yaml
metrics:
  citation_validity:
    direction: higher
    tolerance: 0.0
```

When smoke and full have different metrics, both files can instead wrap their
respective configurations in `suites: {smoke: ..., full: ...}`. This keeps both
accepted results in the same `accepted.json` without silently dropping metrics.
`load_comparison_config(baseline_path, thresholds_path, name="full")` selects the
full suite; a missing suite is an error.
A single-suite document remains supported with the shapes above.

Names must be lowercase identifiers. Values must be finite. Tolerances are
absolute, nonnegative, and inclusive at the boundary. `higher` measures
regression as baseline minus current; `lower` measures current minus baseline.
Unknown, missing, or mismatched metrics/configuration fail explicitly.
Hard failures remain fatal regardless of tolerance or metric improvements.
The accepted baseline cannot contain hard failures.

For adapter outputs, construct `EvaluationResult(metrics=..., hard_failures=...)`,
call `compare(current, baseline, thresholds)`, then
`write_report(report, Path("evaluations/reports"), name="full")` for
`full.json` / `full.md`. The full-suite runner/Make target is owned by the
Foundry evaluator adapters, not this reporting package.
Successful JSON reports have `metrics` (ordered rows with `name`, `baseline`,
`current`, `delta`, `direction`, `tolerance`, `passed`), `hard_failures`,
overall `passed`, and `fixture`. Configuration errors produce `passed: false`
and an `error` class instead of a success-shaped comparison.

The Evaluation workflow uploads artifacts and adds a job summary even when
gates fail. The separate `Evaluation report` workflow (`workflow_run`), from the default branch
without checking out or executing PR/artifact code, updates one bot comment
on the matching open PR. Fork evaluation receives no write token. Publication
starts after this workflow is present on the default branch; the first PR
introducing it has artifacts/job summaries but cannot yet receive its own
`workflow_run` comment. Stale-commit runs do not overwrite a newer PR report.
