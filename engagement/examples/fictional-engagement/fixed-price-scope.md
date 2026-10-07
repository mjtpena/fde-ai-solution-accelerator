# Fixed-price scope

## Commercial baseline

- Engagement / revision / status: Synthetic Reference Assistant / 1 / planned
- Scope approver / delivery owner: Sponsor / Technical owner
- Delivery window: 2030-04-01 through 2030-04-12, 10 working days;
  prerequisites due 2030-04-02
- Fixed fee / currency / tax basis: 8,000 AUD, exclusive of tax; fictional only
- Payment milestones: 4,000 AUD after scope and prerequisites are approved;
  4,000 AUD after all acceptance and handover gates pass; each due within 10
  working days of its trigger in this fictional arrangement

## Included deliverables

| ID     | Deliverable                             | Boundary / quantity                                    | Acceptance IDs             | Accountable role    |
| ------ | --------------------------------------- | ------------------------------------------------------ | -------------------------- | ------------------- |
| DEL-01 | Read-only grounded staging assistant    | 20 synthetic documents, at most 1 MiB each; two scopes | AC-01, AC-02, AC-03, AC-04 | Technical owner     |
| DEL-02 | Repeatable quality evidence pack        | 50 quality cases plus 100 measured load requests       | AC-05, AC-06               | Evaluation reviewer |
| DEL-03 | Operational handover pack and rehearsal | One staging environment, cost alerts and rollback      | AC-07, AC-08               | Operations owner    |

## Exclusions

Production, real data, integrations, write tools, custom training, ongoing cloud
consumption, taxes and support after the correction window are excluded.
No business entities or workflows are added to the accelerator.

## Dependencies and obligations

A-01 through A-04 must be validated by their owners on 2030-04-02. A late
prerequisite pauses dependent work and requires a Sponsor-approved new schedule,
not silent use of the reserve. One reserve day is included in the fee.

One correction window of five working days after sign-off covers reproducible
defects against this baseline at no additional delivery fee; enhancements and
changed inputs require separate approval. The fee includes this correction
obligation; ten days is the planned delivery capacity, not a support-hour quota.

## Change control and agreement

Technical owner records reason, changed deliverables, gates, fee and date impact.
Sponsor approves the revised baseline before changed work starts. Security
reviewer cannot be bypassed for unsafe scope. Cloud cap changes also require
Operations owner review.

- Decision: fictional scope selected in D-01 on 2030-04-01; commercial
  authorisation remains pending A-01 through A-04. This is not a real offer.

References: [slice](smallest-valuable-slice.md), [cost](cost-model.md),
[delivery](delivery-plan.md), [acceptance](acceptance-criteria.md).
