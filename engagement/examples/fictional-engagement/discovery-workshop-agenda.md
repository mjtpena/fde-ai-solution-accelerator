# Discovery workshop agenda

## Session

- Engagement / date / duration: Synthetic Reference Assistant / 2030-04-01 / 90 minutes
- Facilitator: Technical owner
- Required participants: Sponsor, Technical owner, Evaluation reviewer,
  Security reviewer, Operations owner
- Decision authority: Sponsor for scope; Security reviewer for safety gates
- Record status: filled hypothetical workshop outcome, not an actual meeting

## Preparation

Participants review the [problem statement](problem-statement.md), the proposed
20-document synthetic corpus, a staging-only architecture and a provisional
monthly operating estimate. No real document text, personal details or Azure
identifiers are included.

## Time-boxed agenda

| Minutes | Topic                                | Lead role           | Output                                   |
| ------- | ------------------------------------ | ------------------- | ---------------------------------------- |
| 0-15    | Problem and unknown baseline         | Evaluation reviewer | Three measurable outcomes                |
| 15-35   | Data, identity and safety boundaries | Security reviewer   | Synthetic-only; two isolated scopes      |
| 35-55   | Smallest slice and exclusions        | Technical owner     | Read-only staging scope                  |
| 55-75   | Quality, cost and delivery gates     | Operations owner    | Eight acceptance gates and budget review |
| 75-90   | Decisions and next actions           | Sponsor             | Role-owned prerequisites                 |

## Decision and action log

| ID   | Decision or unanswered question                               | Owner role        | Due date   | Status / evidence                                      |
| ---- | ------------------------------------------------------------- | ----------------- | ---------- | ------------------------------------------------------ |
| D-01 | Use a read-only staging demonstration, not production         | Sponsor           | 2030-04-01 | Agreed within fictional scenario; slice document       |
| D-02 | Validate staging access and identity readiness                | Technical owner   | 2030-04-02 | Pending; A-01                                          |
| D-03 | Replace illustrative operating estimates with current pricing | Operations owner  | 2030-04-02 | Pending; A-03                                          |
| D-04 | Do not accept without safety and recovery evidence            | Security reviewer | 2030-04-01 | Agreed within fictional scenario; acceptance procedure |

The planned scope is selected; access and pricing remain prerequisites, not
assumed completed actions. References: [roles](stakeholder-map.md),
[assumptions](assumptions-and-constraints.md).
