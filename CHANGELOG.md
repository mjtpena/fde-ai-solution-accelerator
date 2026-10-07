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
- Added a manifest-driven project generator, engagement templates with a
  fictional example, and operations runbook and handover checklist.
- Added repository-specific Copilot instructions and reusable prompts.

### Known limitations

- The repository remains a scaffold; the README documents capabilities that
  are still roadmap items.
- The `make eval-smoke` target currently reports that it is not implemented.
- `make eval-full` is not defined.
- There is no `make deploy-dev` target or checked-in Bicep/deployment workflow
  on `main`.
- The v0.1.0 definition of done in `docs/spec.md` is not met on `main`.

### Release readiness snapshot (2026-10-07; `main` at `907f009b5764`)

This snapshot is based on the merged tree at
`907f009b5764a39ce21e70ccfce81e2ea988cb69`. It is a readiness assessment, not
release approval.

- [ ] Clean clone, setup and local startup provide working chat over example
  documents. There are workflow primitives, but no complete chat/retrieval
  path; the streaming chat PR #79 is still open.
- [ ] Bicep and GitHub Actions deploy a dev environment from zero without
  stored secrets. No Bicep or deployment workflow is on `main`; PRs #77 and
  #80 are open, with #80 still a draft.
- [ ] Grounded answers cite valid evidence and unsupported questions abstain.
  Search indexing, citation validation, and evidence sufficiency remain in
  open PRs #62, #53, and #58.
- [ ] Tests prove write tools require args-bound approval. Approval and tool
  policy work remains in open PRs #70 and #74.
- [ ] Tests prove cross-scope retrieval is impossible. Server-side scope
  resolution is present, but the scope-bound search adapter PR #62 is not
  merged.
- [ ] Every request has a full trace visible in Application Insights. The
  OpenTelemetry PR #55 is open; no end-to-end trace evidence is available.
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

`make check` passed on this `main` snapshot: Ruff and strict mypy passed,
148 Python tests passed, the generated project passed its own checks (including
148 Python and 9 web tests), and the root web lint, format, typecheck, and
3 Vitest tests passed. The environment used Node 24.16.0 despite the web
workspace specifying Node 22, producing engine warnings.

`make eval-smoke` exits successfully but only prints
`eval-smoke: not implemented until M5 (issue 30)`; no evaluation ran.
`make eval-full` fails because there is no such Make target. No real or
service-backed evaluation is available.

Open milestone issues are #10, #12, #15–#17, #19–#22, #24–#27, and #29–#35.
The remaining open implementation PRs at this snapshot are:

- M2: no open PR; issue #10 remains open although scope-resolution code is on
  `main` from merged PR #60.
- M3: #52, #53, #58, #62.
- M4: #51, #70, #72, #74, #76, #79.
- M5: #55, #65, #66, #68, #69.
- M6: #54, #75, #77, #80.

PRs #68 and #80 are drafts. Stacked PRs include #70 on #51, #74 on #70,
#79 on #74, #77 on #75, and #80 on #77; #62 is based on the M3 contracts PR
#52, and #66/#69 depend on M5 PRs #65/#55 respectively. PR #54's latest
security workflow has failing Trivy repository and web-image checks. The M4
agent factory PR #72 has a failing quality check; PR #70's quality check was
in progress at the time of this snapshot.

The v0.1.0 release remains **Unreleased**. No v0.1.0 tag or GitHub Release was
found. Do not tag or publish until all DoD items are verified, deployment and
security gates pass, and real smoke/full service-backed evaluation is
available and passing.
