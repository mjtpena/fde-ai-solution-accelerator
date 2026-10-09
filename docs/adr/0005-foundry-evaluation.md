# ADR-0005: Deterministic smoke gate on every PR, Foundry evaluators for full evaluation

**Status:** Accepted
**Date:** 2026-10-09

## Context

Design principle 4 (spec §1) makes evaluation a release gate. Spec §9 lists what must
be measured: retrieval, response quality, citations, abstention, tools, safety, and
cost/latency. The gate has two jobs that pull in opposite directions:

- **Every PR** needs a check that is fast and deterministic. It must run without Azure
  credentials (including fork PRs) and fail when a safety control is broken.
- **Model quality** (groundedness, relevance, completeness) can only be judged by
  running the deployed model and a judge model. That is slow, costs money, and gives
  different results from run to run.

Principle 5 forbids running several evaluation frameworks side by side (RAGAS,
DeepEval, Promptfoo).

## Decision

Use two suites, in one package (`packages/evaluation_core`).

**1. Offline smoke gate: `make eval-smoke`, on every pull request.**

`runners.run_smoke()` runs each row of `evaluations/example-datasets/smoke.jsonl`
through the product's real control plane:

- `GroundedAnswerWorkflow`
- `EvidenceSufficiencyChecker`
- `SameTurnCitationValidator`
- `AgentAnswerGenerator`, with its untrusted-evidence wrapping
- the tool bridge, `ToolPolicyMiddleware` and `ApprovalService`

Two offline stand-ins (`runners/offline.py`) replace Azure:

- `OfflineRetriever`: lexical scoring over `tests/fixtures/retrieval/corpus`,
  filtered by the context's scopes
- `OfflineModel`: a deterministic, extractive model that deliberately obeys injected
  directives found outside an `<evidence>` element

Two fixture tools are used, one read-only and one write. The `ExecutionContext` is
built by the harness. A dataset `scope_id` never becomes the context.

The suite produces six pass-rate metrics:

- **Hard gates.** `citation_validity`, `scope_isolation`, `approval_bypass` and
  `injection_followed` fail the build on any single failing row, whatever the
  tolerance.
- **Tolerance metrics.** `abstention` and `tool_selection` are compared with
  `evaluations/baselines/accepted.json`, using the tolerances in
  `evaluations/thresholds.example.yml`. A project-owned `evaluations/thresholds.yml`
  takes precedence when one exists. The shipped tolerances are all `0.0` because the
  runtime is deterministic.

Exit codes are 0 (pass), 1 (gate failure or regression) and 2 (configuration error;
there is no fallback when files are missing). Reports (`evaluations/reports/smoke.json`
and `smoke.md`) hold aggregate metrics only, never prompts or retrieved text.
`.github/workflows/evaluation.yml` runs the suite on every pull request. It then checks
the report verdict again and uploads the report. `deploy-dev.yml` runs it again before
deploying.

**2. Full evaluation: `make eval-full`, against a deployed environment.**

`evaluators/full.py` loads a trusted factory
(`accelerator.infrastructure.evaluation:create_full_evaluation_runtime`). That factory
builds the same Azure workflow the API serves, with
`capture_evaluation_context=True`. The `ExecutionContext` is resolved from
`scope_memberships` for one configured evaluation principal.

Answered rows are scored by `FoundryEvaluatorAdapters` (`evaluators/foundry.py`),
which wraps the `azure-ai-evaluation` SDK's groundedness, relevance, retrieval and
response-completeness evaluators. Scores (1–5) are validated, the judge deployment
uses Entra ID, and API keys are not accepted. Abstained rows are not judged.

The suite runs in `deploy-dev.yml`'s `full_evaluation` job on the VNet runner, after
the applications are deployed on `main`. Production promotion depends on that job.

**Baselines and thresholds** change only in a dedicated, reviewed PR that explains the
change, never inside a feature PR (spec §9; `reporting/README.md`). The accepted
baseline cannot contain hard failures.

## Consequences

Positive:

- Every PR proves that the safety controls are still wired correctly.
  `runners/tests/test_offline_smoke.py` breaks each control in turn (scope filter,
  evidence wrapper, approval path, citation validation, sufficiency gate, tool
  routing) and checks that the matching metric fails.
- The smoke gate needs no cloud access or secrets, and it gives the same verdict for
  the same commit.
- Model-quality scoring uses Microsoft's own evaluators. No second evaluation
  framework is added.

Negative / trade-offs:

- The smoke gate does **not** measure model judgement, real retrieval ranking, or
  resistance to injections that stay inside their evidence element (stated in
  `runners/README.md`).
- The full suite currently only **logs** aggregate metrics. It fails on workflow or
  SDK errors, but it does not yet compare results with a baseline or write a
  `full.json` report artefact. `accepted.json` holds only the `smoke` suite. The
  reporting package already supports a `full` suite (`load_comparison_config(...,
  name="full")`, `write_report(..., name="full")`). Wiring it in is outstanding, so
  spec §9's "deploy to dev: compared to `baselines/accepted.json`" is not yet true.
- Spec §9 also lists Foundry agent (tool) evaluators and cost/latency aggregation.
  Neither is implemented in the full suite.
- Spec §2 names `azure-ai-projects` for evaluation, but the implementation uses the
  `azure-ai-evaluation` SDK's local evaluator classes. Results are not published to a
  Foundry project's evaluation view.
- The example dataset has 11 rows. That is enough to exercise every category, but it
  is not a statistically meaningful quality sample. Projects must supply their own
  dataset (`EVALUATION_DATASET`).

## Alternatives considered

- **Run the Foundry evaluators on every PR.** It would need cloud credentials on PR
  runners (including forks), and it would be slow and non-deterministic. A judge-model
  wobble would block merges.
- **Smoke gate only.** It cannot detect a drop in groundedness or relevance after a
  model or prompt change.
- **RAGAS, DeepEval or Promptfoo.** All are valid, but adding one would mean a second
  framework next to Foundry evaluation (principle 5).
- **Tolerance-only gating for safety metrics.** A small tolerance on `scope_isolation`
  or `approval_bypass` would accept a real leak. These metrics are hard gates.

## References

- `docs/spec.md` §1 (principles 4, 5), §2, §5.6, §9
- `packages/evaluation_core/runners/README.md`, `runners/offline.py`
- `packages/evaluation_core/evaluators/README.md`, `evaluators/foundry.py`,
  `evaluators/full.py`
- `packages/evaluation_core/reporting/README.md`
- `apps/api/src/accelerator/infrastructure/evaluation.py`
- `evaluations/baselines/accepted.json`, `evaluations/thresholds.example.yml`,
  `evaluations/example-datasets/smoke.jsonl`
- `.github/workflows/evaluation.yml`, `.github/workflows/deploy-dev.yml`, `Makefile`
