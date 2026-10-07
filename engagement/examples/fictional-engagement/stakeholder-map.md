# Stakeholder map

## Record

- Engagement / revision / date: Synthetic Reference Assistant / 1 / 2030-04-01
- Maintainer: Technical owner
- Contact directory: not supplied; roles are fictional and have no real contacts

## Roles and authority

| Role                | Interest / success measure                    | Responsibility                        | Decision authority                              | Consultation cadence   |
| ------------------- | --------------------------------------------- | ------------------------------------- | ----------------------------------------------- | ---------------------- |
| Sponsor             | Bounded demonstration within fee              | Funding and escalation                | Scope, fee and schedule changes                 | Entry and exit reviews |
| Technical owner     | Repeatable end-to-end slice                   | Design, implementation, test evidence | Technical implementation within scope           | Daily                  |
| Security reviewer   | Zero exposure or unsafe instruction following | Boundary and injection review         | Veto release on safety failure                  | Days 2, 7 and 9        |
| Evaluation reviewer | Eight measurable gates                        | Review dataset and evidence           | Functional acceptance with security concurrence | Days 2, 7 and 9        |
| Operations owner    | Recoverable, budgeted staging service         | Deployment, monitoring and handover   | Operational readiness                           | Days 2, 8 and 9        |

## Decisions and escalation

- Scope changes: Technical owner records impact; Sponsor approves the revised baseline.
- Acceptance disputes: Evaluation reviewer escalates to Sponsor within one working
  day; Sponsor cannot waive mandatory safety gates.
- Safety incidents: Security reviewer stops the demonstration; Operations owner
  contains it before any restart review.
- Unavailable approver: Sponsor arranges a role delegate within one working day;
  affected work remains paused until delegation is recorded.
- Communication: hypothetical restricted delivery channel; redacted summaries only.

Consultation is not approval. Decisions belong in
[acceptance](acceptance-criteria.md) and [handover](handover-checklist.md).
