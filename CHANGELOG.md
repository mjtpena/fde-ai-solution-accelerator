# Changelog

## [0.1.0] - Unreleased

> Draft only. This entry records work present on `main`; v0.1.0 is not released.
> Finalize it after the release definition of done in `docs/spec.md` is verified.

### Added

- Established the Python and npm workspace scaffolding, API and web starter
  baselines, shared package boundaries, local Docker Compose, quality tooling,
  and pull request CI.
- Added repository-specific Copilot instructions and reusable prompts.
- Production-readiness stack (PRs #87–#119, building on #53, #54, #58, #62, #75,
  #77 and #80):
  - Typed settings, an API composition root, Alembic migrations, PostgreSQL in
    CI, health and readiness probes, a cost guard, and digest-pinned non-root
    container images.
  - A hybrid Azure AI Search adapter with a server-side scope filter, evidence
    sufficiency and abstention, same-turn citation validation, and cross-scope
    isolation tests.
  - Approval routes with the `Approver` role and separation of duties, tool
    policy in the chat path, and real token streaming.
  - A queue-driven ingestion worker with Blob, AI Search and PostgreSQL adapters.
  - Hardened Entra token validation, strict CORS and security headers, tracing
    with redaction, and an untrusted-data wrapper for retrieved text.
  - A deterministic offline smoke evaluation over a fixture corpus that fails
    the PR against an accepted baseline, and `make eval-full` backed by Foundry
    evaluators.
  - CI for Playwright, Docker builds, coverage, OpenAPI drift and SHA-pinned
    actions; Dependabot, CodeQL, Trivy, a gitleaks hook, CODEOWNERS and
    `SECURITY.md`.
  - Bicep for Container Apps, PostgreSQL, Storage, AI Search, Foundry, Key Vault
    and monitoring with managed identities, least-privilege RBAC and key auth
    disabled; `make deploy-dev` and an OIDC `deploy-dev.yml` workflow.
  - ADRs, getting-started, deployment and observability guides, and a
    machine-readable threat model.
  - Generator fixes: the `TITLE` placeholder replaces `DISPLAY`, and generated
    projects receive only the workflows they can run.
- `make e2e-local` and an `e2e-local` CI job: the API, the ingestion worker and
  the web app as real processes against PostgreSQL and Azurite
  (`docs/testing-strategy.md`). The worker's `INGESTION_SKIP_SEARCH_INDEXING`
  local mode ends documents as `indexing_skipped` and is refused in production.

### Changed

- A requester can no longer approve or reject their own approval request.
- Approval endpoints return 503, not 403, when audit persistence is unavailable.
- API ingress is internal; the web app proxies to it.
- Database access uses separate API, worker and operator roles.
- Malformed PDFs (`PdfReadError`, decompression limits) are rejected on first
  delivery instead of being retried as transient failures.
- An unreachable database returns 503, not 500, from the audit middleware, the
  audit query and the scope resolver.
- The Compose Azurite skips its API-version check, as CI already did, so the
  storage SDKs in `uv.lock` can reach it.

### Known limitations

- The dev deployment is verified by Bicep build, what-if and script tests, not
  yet by a recorded end-to-end run against a live subscription.
- Retrieval recall@k has not been measured against a live index.
- `make eval-full` needs a Foundry project and a deployed environment.
- No code calls Azure AI Content Safety; see `docs/testing-strategy.md`.
- The **Trivy repository** check fails on two HIGH findings with no upstream
  fix: `braces` 3.0.3 (CVE-2026-93687, web dev tooling) and `nltk` 3.10.3
  (CVE-2026-81726, via `azure-ai-evaluation` in the optional `evaluation`
  extra). The gate is not weakened; upgrade when fixes are released.
- Deploying the hosted agent needs the factory variables set; production
  promotion covers only the web app and worker; worker logs drop structured
  fields. See `docs/deployment-guide.md` and `docs/observability-standard.md`.

### Release status

v0.1.0 is not released. No tag or GitHub Release exists. Verify each item of the
definition of done in `docs/spec.md` §14 against a deployed environment, with
the security and evaluation gates passing, before tagging.
