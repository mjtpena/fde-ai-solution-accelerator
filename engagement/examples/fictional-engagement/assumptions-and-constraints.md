# Assumptions and constraints

## Record

- Engagement / revision / date: Synthetic Reference Assistant / 1 / 2030-04-01
- Maintainer: Technical owner
- Review cadence: daily and before any scope change

## Assumptions to validate

| ID   | Assumption                                               | Validation method                                                       | Owner role          | Due date   | Status  | If false                                               |
| ---- | -------------------------------------------------------- | ----------------------------------------------------------------------- | ------------------- | ---------- | ------- | ------------------------------------------------------ |
| A-01 | Staging services and managed identities are available    | Access and RBAC checks without copying identifiers into these artefacts | Technical owner     | 2030-04-02 | Pending | Pause integration; rebaseline schedule                 |
| A-02 | 20 fabricated text documents fit the agreed limits       | Synthetic-only review; count and size checks                            | Evaluation reviewer | 2030-04-02 | Pending | Reduce corpus through approved scope change            |
| A-03 | Current pricing supports a 200 AUD monthly operating cap | Region-specific provider estimate and budget review                     | Operations owner    | 2030-04-02 | Pending | Do not provision until revised cap or sizing is agreed |
| A-04 | Review roles are available on days 7-9                   | Confirm gate review slots by role                                       | Sponsor             | 2030-04-02 | Pending | Rebaseline acceptance and handover dates               |

## Hard constraints

| ID   | Constraint                                                       | Rationale / source               | Verification                            | Owner role          |
| ---- | ---------------------------------------------------------------- | -------------------------------- | --------------------------------------- | ------------------- |
| C-01 | Synthetic data only; no real personal data or credentials        | Fictional demonstration boundary | Corpus and artefact review              | Security reviewer   |
| C-02 | Authorisation scope only from `ExecutionContext`                 | Accelerator security contract    | AC-01 negative tests                    | Technical owner     |
| C-03 | Read-only; no write tools                                        | S-01 boundary                    | Registry/configuration inspection       | Security reviewer   |
| C-04 | Same-turn citations, abstention and untrusted retrieved text     | Accelerator safety contract      | AC-02, AC-03, AC-04                     | Evaluation reviewer |
| C-05 | Managed identity; no embedded secrets                            | Accelerator identity contract    | Identity/configuration review           | Operations owner    |
| C-06 | Staging in hypothetical Australia East; 10-day delivery time box | Fictional scope baseline         | Deployment metadata and delivery review | Technical owner     |

## Change handling

Owners attach redacted evidence before changing an assumption to verified.
Sponsor approves fee or date changes; Security reviewer retains veto over
unsafe operation. Failed prerequisites pause affected work. Later write
capability would require separate scope and approved args-bound approvals;
this engagement grants none.

References: [risks](risk-register.md), [scope](fixed-price-scope.md),
[delivery](delivery-plan.md).
