# Changelog

## [0.1.0] - Unreleased

> Draft only. This entry records work present on `main`; v0.1.0 is not released.
> Finalize it after the release definition of done in `docs/spec.md` is verified.

### Added

- Established Python and npm workspace scaffolding, API and web starter
  baselines, shared package boundaries, local Docker Compose, quality tooling,
  and pull request CI.
- Added Entra ID authentication, server-side scope resolution, append-only audit
  events, cost guardrails, and retrieval diagnostics.
- Added document parsing and chunking, idempotent ingestion, evaluation dataset
  schemas, and grounded-workflow primitives.
- Added validated document, chunk, evidence, and retrieval-request contracts.
- Added the agent factory, Foundry hosted-agent packaging, tool registry,
  args-bound approval service and policy middleware, plus the streaming chat
  API/UI.
- Added a manifest-driven project generator, engagement templates with a
  fictional example, and operations runbook and handover checklist.
- Added repository-specific Copilot instructions and reusable prompts.

### Known limitations

- The repository remains a scaffold; the README documents capabilities that
  are still roadmap items.
- There is no Azure AI Search index/adapter on `main`, so the grounded workflow
  is not verified end-to-end over example documents.
- The `make eval-smoke` target currently reports that it is not implemented.
- `make eval-full` is not defined.
- There is no `make deploy-dev` target or checked-in Bicep/deployment workflow
  on `main`.
- The v0.1.0 definition of done in `docs/spec.md` is not met on `main`.

### Release readiness snapshot (2026-10-08; `main` at `6a46f8235c69`)

This snapshot is based on the merged tree at
`6a46f8235c697d14a60f0c8baa622f106aa55dd0`. It is a readiness assessment, not
release approval.

- [ ] Clean clone, setup and local startup provide working chat over example
  documents. The streaming chat API/UI and Foundry workflow components are on
  `main`, but the Azure AI Search adapter and end-to-end retrieval path are
  not; PR #62 is still open.
- [ ] Bicep and GitHub Actions deploy a dev environment from zero without
  stored secrets. No Bicep or deployment workflow is on `main`; PR #77 has a
  failing Quality check and PR #80 is a draft.
- [ ] Grounded answers cite valid evidence and unsupported questions abstain.
  The workflow has citation/abstention primitives, but Azure Search integration
  and the M3 citation/abstention PRs #53 and #58 remain open.
- [x] Tests prove write tools require args-bound approval. Merged tests include
  `test_unapproved_write_cannot_execute`,
  `test_approval_service_rejects_changed_arguments_without_execution`, and
  `test_execute_with_matching_approval_succeeds_once`.
- [ ] Tests prove cross-scope retrieval is impossible. Server-side scope
  resolution is present, but the scope-bound search adapter PR #62 is not
  merged.
- [ ] Every request has a full trace visible in Application Insights. The
  OpenTelemetry PR #55 is open and there is no end-to-end Application Insights
  trace evidence.
- [ ] Evaluation runs in CI and blocks regressions. `make eval-smoke` remains a
  placeholder, `make eval-full` is undefined, and evaluator/gating PRs #65,
  #66, and #68 are open.
- [x] The generator produces a project that builds and passes checks. Verified
  by `make check` on this `main` snapshot, including generated-project checks.
- [x] Engagement templates and handover docs are complete. The templates,
  fictional example, runbook, and handover checklist are present on `main`;
  environment-specific operational acceptance is still pending.
- [x] Known limitations are documented honestly in the README and operations
  runbook, including the unavailable deployment and real evaluation gates.

On this `main` snapshot, `make check` passed after restoring dependencies
with `npm ci`: Ruff, strict mypy, and 267 Python tests passed (one skipped);
the 12 generator self-tests and generated-project checks also passed
(267 Python tests passed, one skipped, and 14 web tests passed). Root web
lint, format, typecheck, and all 14 Vitest tests passed. The earlier missing
Playwright types were a local dependency-installation issue, not a remaining
check failure. The environment used Node 24.16.0 despite the web workspace
specifying Node 22, producing engine warnings. `npm ci` reported five
high-severity vulnerabilities; this is not evidence of security acceptance.

`make eval-smoke` exits successfully but only prints
`eval-smoke: not implemented until M5 (issue 30)`; no evaluation ran.
`make eval-full` fails because there is no such Make target. No real or
service-backed evaluation is available.

Open milestone issues are #15–#17, #26, #27, #29–#35, and #40. The
remaining open implementation PRs at this snapshot are:

- M2: no open milestone issue or PR; scope-resolution code is on `main` from
  merged PR #60.
- M3: #53, #58, #62.
- M5: #55, #65, #66, #68, #69.
- M6: #54, #75, #77, #80.

M3 contracts PR #52 and M4 PRs #51, #70, #72, #74, #76, and #79 have merged.
PRs #68 and #80 are drafts. Remaining stacked work includes #66 based on #65,
#69 based on #55, #77 based on #75, and #80 based on #77. PRs #66 and #77 are
marked DIRTY. PR #77's Quality check fails (its Bicep validation passes).
PR #54's Trivy repository scan fails; its other listed checks pass. PR #75's
Bicep validation and Quality checks pass, but its dependent #77 is not ready.
PR #65's Quality check was running at the time of refresh. PRs #53, #55, #58,
and #62 are CLEAN with passing Quality checks, but remain unmerged.

The v0.1.0 release remains **Unreleased**. No v0.1.0 tag or GitHub Release was
found. Do not tag or publish until all DoD items are verified, deployment and
security gates pass, and real smoke/full service-backed evaluation is
available and passing.
