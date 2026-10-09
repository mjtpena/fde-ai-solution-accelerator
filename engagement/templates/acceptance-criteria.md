# Acceptance criteria

## Acceptance record

- Engagement / baseline revision: [values]
- Reviewer / delivery owner: [roles]
- Test environment and configuration: [versions, workload and region]
- Evidence location and retention: [access-controlled, redacted references]
- Status: [planned, under review, accepted or rejected]

## Gates and evidence

| ID      | Deliverable / requirement | Test and pass threshold              | Evidence reference           | Result              | Reviewer role |
| ------- | ------------------------- | ------------------------------------ | ---------------------------- | ------------------- | ------------- |
| [AC-01] | [DEL-ID / NFR-ID]         | [repeatable procedure and threshold] | [pending or actual evidence] | [pending/pass/fail] | [role]        |

Cover happy paths, cross-scope denial, same-turn citations, insufficient
evidence abstention, injection resistance, error handling and operational
checks as relevant. Writes, if included, need approved args-bound approvals.

## Decision procedure

Run the agreed checks against the frozen baseline. Store redacted outputs and
actual command results; the offline smoke gate is not model evaluation.
Missing evidence is pending, not passed. Failed mandatory safety gates block
acceptance. Record other deviations with an owner, expiry and explicit reviewer
decision before any conditional acceptance.

- Deviations / remedial actions: [IDs, owners and due dates, or none]
- Final decision / reviewer role / date / reference: [values]
- Re-test triggers: [configuration, model, dataset, permission or code changes]

References: [scope](fixed-price-scope.md),
[requirements](non-functional-requirements.md), [handover](handover-checklist.md).
