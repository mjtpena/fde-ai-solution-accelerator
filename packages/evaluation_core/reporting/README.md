# Evaluation reporting

`make eval-smoke` runs the deterministic `runners.run_smoke()` API, fails on
any hard-gate failure or out-of-tolerance regression, and writes
`evaluations/reports/smoke.json` and `evaluations/reports/smoke.md`.
Exit codes: 0 passed, 1 gate/regression failure, 2 configuration/reporting error.
Reports contain aggregate metrics only, not prompts or retrieved text.

The smoke runner returns six pass-rate metrics: `citation_validity`, `abstention`,
`tool_selection`, `scope_isolation`, `approval_bypass`, and `injection_followed`.
`injection_followed` measures the fraction of observations where injection was
**not** followed (higher is better). The runner owns hard-gate classification:
citation validity, scope isolation, approval bypass, and injection resistance
populate `hard_failures`; abstention and tool selection are tolerance-based
metrics only. Reporting preserves those failures without redefining gate policy.

The accelerator has no accepted production baseline. If **both**
`evaluations/baselines/accepted.json` and `evaluations/thresholds.yml` are
absent, the Make target explicitly permits fixture comparison, labeled in
logs and both reports. Those fixtures expect 1.0 and zero tolerance for each
deterministic check; they are not written into the baseline directory.
Once either project file exists, both are required and no fixture fallback
is allowed. Production gates should invoke the CLI **without**
`--allow-fixture`.

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
full suite; a missing suite is an error. Only the smoke suite can opt into
fixtures. A single-suite document remains supported with the shapes above.

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
gates fail. A separate `workflow_run` job, using the default-branch workflow
without checking out or executing PR/artifact code, updates one bot comment
on the matching open PR. Fork evaluation receives no write token. Publication
starts after this workflow is present on the default branch; the first PR
introducing it has artifacts/job summaries but cannot yet receive its own
`workflow_run` comment. Stale-commit runs do not overwrite a newer PR report.
