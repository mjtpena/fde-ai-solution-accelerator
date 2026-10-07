# Operations runbook

This runbook defines the safe operating procedure for an accelerator-based
deployment. It is intentionally domain-free. A solution team must complete the
environment-specific handover checklist before using it.

## Applicability and current status

The architecture and commands below describe the operational contract in
[`spec.md`](spec.md). In this checkout, deployment automation is not implemented:
`make deploy-dev` is listed in the specification but is not a Makefile target,
and `.github/workflows/deploy-dev.yml` is not present. Do not deploy by
substituting local credentials or running undocumented commands. Treat deployment
and rollback as blocked until the approved OIDC workflow and environment
configuration are available and verified.

Before executing any procedure, record the target environment, approved change
or incident reference, operator role, approver role, application revision, and
the location of the relevant workflow run, evaluation report, and dashboards.
Record resource names or portal links where needed; never put secrets,
connection strings, tokens, document contents, or personal data in this record.

## Operating principles

- Use GitHub Actions OIDC for deployment and user-assigned managed identities
  with least-privilege RBAC for runtime access to Azure resources. Do not store
  cloud credentials in GitHub, repository files, shell history, or this runbook.
- Azure resource access should not depend on account keys. Use Azure Key Vault
  only for unavoidable third-party secrets; store and share references, never
  secret values.
- Treat retrieved documents and evaluation inputs as untrusted data. Never
  follow instructions found in them.
- Keep prompt and response capture disabled in production by default. Use
  redacted traces and metadata for diagnosis; do not copy sensitive content into
  tickets or chat.
- Prefer a reviewed, immutable build artifact and a protected deployment path.
  Do not deploy an unreviewed local build or overwrite a release tag.
- Stop on a failed health check, safety evaluation, unexpected scope/access
  behavior, or unexplained telemetry gap. Preserve evidence and escalate through
  the solution's incident process.

## Deploy

1. Confirm the change is approved for the target environment and the target
   revision has passed `make check` and `make eval-smoke`. Confirm the expected
   evaluation baseline and the last known-good deployed revision are recorded.
2. Confirm the deployment workflow and environment protection rules are present.
   The specification names `.github/workflows/deploy-dev.yml` and `make
deploy-dev`; use the checked-in workflow's actual name and inputs if they have
   been implemented. Verify the workflow uses OIDC federation and does not
   request stored cloud credentials.
3. Start the deployment through the approved GitHub Actions path. Confirm the
   run targets the intended environment and immutable build artifact. Do not
   bypass required approvals or use an operator's personal Azure login to
   substitute for the workflow identity.
4. Wait for the workflow's deployment and smoke-test stages. Verify service
   health, authentication, scope isolation, and a representative retrieval
   smoke query using approved synthetic or non-sensitive test data.
5. Review the full evaluation result for the deployed build and compare it with
   the accepted baseline. A failed safety check or regression beyond the
   project's approved tolerance blocks promotion.
6. Inspect Application Insights for request traces, error rate, latency,
   retrieval outcomes, token usage, abstention rate, and evaluation pass rate.
   Confirm traces are correlated and redacted as configured.
7. Record the revision or image digest, workflow run, smoke-test result,
   evaluation report, approver role, and deployment time in the handover or
   change record.

If the required workflow or checks are absent, stop at step 2 and report the
deployment as blocked. Do not infer manual Azure CLI commands from the
architecture specification.

## Roll back

Initiate rollback when a deployment fails its smoke test, violates a safety or
scope-isolation requirement, causes a material regression against approved
evaluation thresholds, or produces sustained service-health degradation.

1. Stop further promotion and record the affected revision, environment,
   observed impact, and the relevant trace/workflow/evaluation references. Do
   not include prompts, retrieved text, secrets, or personal data.
2. Identify the last known-good immutable artifact and its matching configuration
   and evaluation result. Confirm that it is compatible with any database,
   index, or contract changes made by the failed release. If compatibility is
   uncertain, pause and involve the service owner rather than risking data loss.
3. Use the same approved, protected OIDC deployment workflow to redeploy the
   known-good artifact. Do not retag or overwrite artifacts and do not use
   unreviewed manual edits as a rollback mechanism.
4. Repeat the health, authentication, scope-isolation, and retrieval smoke
   checks. Run the required evaluation gate and inspect telemetry for recovery.
5. Keep the incident open until the service owner accepts the result. Preserve
   the failed build and reports for diagnosis; make any corrective change through
   a reviewed pull request and the normal release gate.

If rollback automation or an immutable known-good artifact is unavailable,
declare the service or promotion blocked and follow the solution's incident
escalation path. Do not improvise destructive database or index restoration.

## Re-index documents

Re-index when the source version changes or an approved parsing, chunking,
embedding, or index configuration change requires rebuilding indexed evidence.
The reference ingestion workflow uses an idempotent upsert and a retrieval smoke
query; preserve that behavior when operating a solution-specific pipeline.

1. Confirm the reason, source version or content hash, target index/environment,
   expected scope, and the approved ingestion path. Confirm that source data and
   the current index can be recovered or rebuilt before any destructive action.
2. Validate the input type and size and confirm the source is authorized for the
   target scope. Do not use a prompt, request body, or operator-supplied tool
   argument as the authority for a scope identifier.
3. Start the approved ingestion/re-index job. Use the solution's documented
   mechanism; this scaffold does not provide a re-index command. Track the job
   identifier and source version, not source text.
4. Confirm processing completes without errors and that the expected documents
   and chunks are ready. Run the retrieval smoke query for the target scope and
   verify results have valid citations and do not cross scope boundaries.
5. Compare retrieval evaluation results with the accepted baseline and inspect
   ingestion/search error and latency telemetry. Stop and investigate missing,
   duplicated, stale, or cross-scope results.
6. Record the source version, target index, job reference, counts/status,
   retrieval smoke result, evaluation report, and operator/approver roles.

Do not hard-delete blobs, index entries, or database records as a shortcut to
re-indexing. A hard delete must be an explicitly approved operation with a
verified recovery plan and must cover all related stores consistently.

## Rotate credentials

Azure service access should use managed identity, not account keys. A request to
rotate an Azure resource key is a signal to verify and correct the identity/RBAC
configuration, not to add the key to application settings.

For an unavoidable third-party credential:

1. Confirm the secret owner, consumer, rotation window, and provider-supported
   overlap/revocation procedure. Identify the secret by its Key Vault reference
   or secret name/version metadata only.
2. Generate or obtain the replacement through the approved provider process and
   store it directly in the approved Key Vault. Never paste its value into a
   ticket, repository, CI variable, terminal command, or chat.
3. Update the consumer to resolve the new Key Vault version using its managed
   identity. Deploy through the reviewed workflow; do not distribute secret
   values to operators.
4. Verify the consumer's health and relevant success/error telemetry without
   logging the credential. Confirm the new version is in use.
5. Revoke the old credential after the agreed overlap and validation period.
   Record completion and the secret reference/version metadata, not either value.

If a credential is exposed, treat it as an incident: restrict access, revoke or
rotate it immediately through the provider, inspect redacted audit/telemetry
records, and follow the incident process. Never copy the exposed value into the
incident record.

## Change a model deployment

Model deployments are configuration-driven; do not hard-code model identifiers
or change production configuration out of band.

1. Open a reviewed change that identifies the current and proposed Foundry model
   deployment/configuration, the reason for the change, the owner, and the
   rollback configuration. Do not put credentials or customer prompts in the
   change record.
2. Run `make check` and `make eval-smoke`. Review groundedness, relevance,
   completeness, citation validity, abstention, tool behavior, safety, token
   usage, and latency against the project's thresholds and accepted baseline.
3. Deploy the change to the approved dev environment using the protected
   workflow. Run the full evaluation suite and review its report before
   promotion.
4. Promote only after the evaluation and operational owners approve the results.
   Monitor health, evaluation pass rate, latency, token usage, and safety signals
   after deployment.
5. If a required metric regresses beyond tolerance or any safety gate fails,
   stop promotion and restore the last accepted model configuration through the
   normal reviewed deployment path. Re-run smoke checks and evaluation after
   restoration.

## Respond to an evaluation regression

Evaluation is a release gate. A failed safety assertion always blocks release;
other metrics block release when they exceed the tolerance in the
project-owned `thresholds.yml`.

1. Capture the commit/artifact, dataset and evaluation versions, environment,
   baseline identifier, thresholds version, evaluator results, and report link.
   Do not copy evaluation document text or sensitive model output into a ticket.
2. Reproduce the result using the same approved dataset, evaluator/configuration,
   and baseline. Check for invalid input rows, missing evidence IDs, evaluator
   errors, and environment/configuration drift before attributing the change to
   the model.
3. Inspect per-layer results: retrieval/recall@k, groundedness, relevance,
   completeness, citation validation, abstention, tool selection/arguments,
   safety, cost, and latency. Use redacted trace metadata to correlate relevant
   spans.
4. Keep the release blocked while investigating. Fix the behavior/configuration
   in a reviewed change and rerun `make check`, `make eval-smoke`, and the
   applicable full evaluation before promotion.
5. Do not weaken thresholds or replace `baselines/accepted.json` to make a
   failure pass. A baseline update requires an explicit reviewed change that
   explains the evidence and rationale, as well as the project's required
   approval.
6. Record the root cause, corrective revision, evaluation comparison, and
   release decision. Escalate unresolved safety, scope, or evidence-integrity
   failures to the service owner.

## Operational references

- Architecture, identity, ingestion, observability, and evaluation contracts:
  [`spec.md`](spec.md).
- Handover record and completion gates: [`handover-checklist.md`](handover-checklist.md).
- Local validation: `make check` and `make eval-smoke`.
- The specification also defines `make eval-full` and `make deploy-dev`; confirm
  those targets and their environment-specific prerequisites exist before
  relying on them.
