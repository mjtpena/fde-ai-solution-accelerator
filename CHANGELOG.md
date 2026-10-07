# Changelog

## [0.1.0] - Unreleased

> Draft only. This entry records work present on `main`; v0.1.0 is not released.
> Finalize it after the release definition of done in `docs/spec.md` is verified.

### Added

- Established the Python and npm workspace scaffolding, API and web starter
  baselines, and shared package boundaries.
- Added a local Docker Compose configuration, quality tooling, and pull request
  CI.
- Added repository-specific Copilot instructions and reusable prompts.

### Known limitations

- The repository remains a scaffold; the README documents capabilities that
  are still roadmap items.
- The `make eval-smoke` target currently reports that it is not implemented.
- `make eval-full` is not defined.
- The v0.1.0 definition of done in `docs/spec.md` is not met on `main`.

### Release readiness snapshot (2026-10-07)

Assessed against `main` at `2a42ae6`. The implementation PRs listed below are
open and are not included in this snapshot.

- [ ] Clean clone, setup and local startup provide working chat over example
  documents.
- [ ] Bicep and GitHub Actions deploy a dev environment from zero without
  stored secrets.
- [ ] Grounded answers cite valid evidence and unsupported questions abstain.
- [ ] Tests prove write tools require args-bound approval.
- [ ] Tests prove cross-scope retrieval is impossible.
- [ ] Every request has a full trace visible in Application Insights.
- [ ] Evaluation runs in CI and blocks regressions.
- [ ] The generator produces a project that builds and passes checks.
- [ ] Engagement templates and handover docs are complete.
- [x] Known limitations are documented honestly in the README.

The M2–M7 issues #9–#39 remain open. Current open implementation PRs are:

- M2: #48, #60, #67.
- M3: #52, #53, #56, #58, #61, #62, #71.
- M4: #51, #63, #70, #72, #74, #76.
- M5: #50, #55, #65, #66, #68, #69.
- M6: #54, #73, #75, #77.
- M7: #49, #59, #64.

M4 issue #24 (streaming chat endpoint and UI) and M6 issue #34 (GitHub OIDC
deployment) have no open PR in the refreshed PR list.

Several PRs depend on other unmerged branches, including #60 on #48, #52 and
#67/#71 on #60, #62 on #52, #66 on #65, #69 on #55, and #77 on #75. The
release PR is #57.

PR #62 is currently reporting failures for Dependency review and the Trivy
repository, web-image, and ingestion-image scans. PR #48's Quality checks are
in progress. PR #68 is a draft. These PR states are another reason the
prerequisites are not ready to land.

No v0.1.0 tag or GitHub Release was found. Do not tag or publish until the
prerequisites have landed, each unchecked definition-of-done item is verified,
and a real evaluation target and service-backed evaluation are available and
passing.
