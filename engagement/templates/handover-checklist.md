# Handover checklist

## Transfer record

- Engagement / release reference / environment: [values]
- Delivering role / receiving operations role: [roles]
- Support window / escalation route: [duration and approved channel]
- Acceptance prerequisite: [approved acceptance record]

## Readiness evidence

Leave items unchecked until evidence is verified by the receiving role.

- [ ] Scope and exclusions reviewed; [acceptance criteria](acceptance-criteria.md) approved.
- [ ] Architecture, dependencies and pinned configuration documented.
- [ ] Data inventory, retention, deletion and server-resolved scope verified.
- [ ] Managed identities, RBAC and access owners reviewed; no secrets transferred.
- [ ] Deployment, rollback and restore steps exercised in the agreed environment.
- [ ] Monitoring, redacted traces, audit access and alert routing verified.
- [ ] Cost thresholds, usage limits and billing ownership transferred.
- [ ] Quality baseline and evaluation rerun commands recorded with real results.
- [ ] Incident triage, stop authority and escalation route exercised.
- [ ] Known limitations, residual risks and unresolved actions assigned.
- [ ] Receiving role demonstrated operation and recovery independently.
- [ ] Temporary delivery access removed or given an approved expiry.

## Evidence and outstanding actions

| Item             | Evidence location / result      | Owner role | Due date | Status                |
| ---------------- | ------------------------------- | ---------- | -------- | --------------------- |
| [checklist item] | [redacted reference or pending] | [role]     | [date]   | [pending or verified] |

## Sign-off

- Receiving role / decision / date / evidence: [values]
- Delivering role / decision / date / evidence: [values]
- Remaining support / exclusions: [scope and expiry]

Do not claim operational readiness while mandatory acceptance or recovery
evidence is missing. Reference [delivery](delivery-plan.md) and
[risks](risk-register.md) for unresolved dependencies.
