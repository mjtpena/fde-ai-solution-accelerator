# Operations handover checklist

Complete this checklist with the receiving operator before transferring
responsibility. It is a reusable, domain-free template: enter role/team names,
resource references, workflow links, and build identifiers only. Do not enter
personal data, secret values, connection strings, access tokens, prompts, or
document contents.

## Service record

- [ ] Service/repository and deployed revision or immutable artifact digest:
- [ ] Environment and Azure resource references (names/portal links only):
- [ ] Service owner role/team and backup owner role/team:
- [ ] Operations/on-call role and escalation path:
- [ ] Change approver role and protected deployment environment:
- [ ] Source repository and deployment/evaluation workflow links:
- [ ] Accepted evaluation baseline and project-owned threshold references:
- [ ] Known limitations, dependencies, and out-of-scope recovery assumptions:

## Access and security

- [ ] Web/API identity and app-role assignments are documented for the
      environment; scope is resolved server-side, not supplied by prompts or
      requests.
- [ ] Each runtime has its assigned managed identity and least-privilege RBAC
      for required services.
- [ ] GitHub Actions deployment uses OIDC federation and protected environment
      approvals; no stored cloud credentials are required.
- [ ] Key-based Azure service authentication is disabled where supported.
- [ ] Any unavoidable third-party secret is held in Key Vault; only its
      reference, owner role, and rotation procedure are documented here.
- [ ] Production prompt/response capture is off by default and redaction is
      enabled for telemetry.
- [ ] Operators know how to report exposed credentials or suspected cross-scope
      access through the incident process.

## Deploy and rollback

- [ ] The approved deployment workflow exists, its permissions and environment
      protections are understood, and a successful run is linked.
- [ ] Required pre-deploy gates are documented: `make check` and the
      solution-specific smoke and full evaluation suites.
- [ ] The solution-specific evaluation suite, project-owned thresholds, and
      accepted baseline exist; the candidate revision passed the required
      evaluation gates. The offline `make eval-smoke` gate and `make check`
      alone are not model-quality evidence.
- [ ] The current deployed revision, last known-good immutable artifact, and
      matching configuration are recorded.
- [ ] Compatibility/recovery considerations for database, index, and contract
      changes are documented.
- [ ] Smoke-test steps cover health, authentication, scope isolation, and a
      representative retrieval query using approved test data.
- [ ] Rollback procedure uses the protected deployment path and has an owner
      who can authorize it.
- [ ] Post-deploy and post-rollback evaluation and telemetry checks are assigned.

## Re-index and data operations

- [ ] Authorized source location, ingestion owner, target index/environment, and
      approved re-index mechanism are recorded.
- [ ] Re-index trigger, source version/content-hash tracking, and job status
      location are documented.
- [ ] Operator can verify completion, expected document/chunk status, scoped
      retrieval, citations, and retrieval evaluation results.
- [ ] Idempotent upsert behavior and recovery/rebuild approach are documented.
- [ ] Hard-delete steps, required approval, and recovery plan are documented
      separately; operators understand that all related stores must be handled
      consistently.

## Credentials and model changes

- [ ] Azure resource access uses managed identity; Azure account-key rotation is
      not used as a substitute for repairing identity/RBAC.
- [ ] For each unavoidable third-party secret, owner role, Key Vault reference,
      consumer, rotation window, validation, and old-credential revocation steps are
      documented without recording the value.
- [ ] Current Foundry model deployment/configuration and last accepted
      configuration are recorded.
- [ ] Model changes require a reviewed change, smoke evaluation, full dev
      evaluation, approval, and monitored promotion.
- [ ] The rollback configuration and response to safety or threshold regression
      are documented.

## Evaluation and observability

- [ ] The solution-specific smoke and full evaluation suites run real
      evaluations, project-owned thresholds are configured, and an accepted
      baseline exists. Until all are available and required gates pass,
      deployment and model promotion remain blocked and handover status must be
      `blocked`.
- [ ] Dataset, evaluator, threshold, and accepted-baseline ownership is assigned
      to a role/team.
- [ ] Safety failures and out-of-tolerance regressions are understood to block
      release; baseline/threshold changes require an explicit reviewed change.
- [ ] Evaluation reports can be found by build/revision and compared with the
      accepted baseline.
- [ ] Application Insights access and dashboards/queries for request/error
      rates, P50/P95 latency, tokens, abstentions, approvals, retrieval outcomes,
      and evaluation pass rate are documented.
- [ ] Operators can correlate a request trace and understand
      `authz.resolve_scope`, retrieval, model, tool, approval, citation-validation,
      and response spans.
- [ ] Telemetry review uses redacted metadata; operators know not to place
      prompts, retrieved content, secrets, or personal data in incident records.

## Acceptance and open items

- [ ] Receiving operator has reviewed the runbook:
      [`operations-runbook.md`](operations-runbook.md).
- [ ] A deployment and rollback walkthrough has been completed or is explicitly
      marked blocked because required automation is unavailable.
- [ ] Re-index, credential rotation, model change, and evaluation regression
      procedures have been reviewed with the responsible role/team.
- [ ] Open gaps have an owner role/team, target date, and tracking reference.
- [ ] Known limitations and recovery expectations have been acknowledged by
      both the service owner and receiving operations role.

Handover status: `ready` only when every mandatory item above is verified,
including a real solution-specific evaluation suite, project-owned thresholds,
and an accepted baseline; otherwise `blocked`.

Outstanding blockers and tracking references:

-
