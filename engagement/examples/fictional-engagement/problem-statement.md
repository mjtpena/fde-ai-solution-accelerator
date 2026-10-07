# Problem statement

## Record

- Engagement: Synthetic Reference Assistant
- Status / revision / date: planned / 1 / 2030-04-01
- Accountable owner: Technical owner
- Decision reviewer: Sponsor

## Problem and evidence

The fictional Evaluation reviewer needs a repeatable way to test whether a
read-only assistant can answer questions from synthetic technical references
without exposing another scope or inventing unsupported claims. The current
demonstration has no recorded retrieval or safety evidence.

| Observation                        | Evidence location                                | Impact                                    | Confidence |
| ---------------------------------- | ------------------------------------------------ | ----------------------------------------- | ---------- |
| No repeatable answer review exists | Invented discovery scenario; no actual study     | Readiness cannot be assessed consistently | Assumed    |
| No negative scope test is recorded | Invented discovery scenario; no actual execution | Data isolation is unproven                | Assumed    |

## Desired outcome

| Measure                       | Baseline                                   | Target                                       | Measurement method                     |
| ----------------------------- | ------------------------------------------ | -------------------------------------------- | -------------------------------------- |
| Supported answers             | Unknown; Technical owner measures on day 2 | At least 18 of 20 answerable cases succeed   | Frozen synthetic evaluation set; AC-02 |
| Unsupported answers           | Unknown; Technical owner measures on day 2 | 10 of 10 insufficient-evidence cases abstain | Fixed negative cases; AC-03            |
| Cross-scope evidence exposure | Unmeasured, not assumed safe               | Zero in 10 negative cases                    | Two synthetic scopes; AC-01            |

## Boundaries and decision

- In scope: one read-only staging assistant, 20 fabricated technical documents.
- Out of scope: production, real data, write tools and external integrations.
- Data boundary: synthetic documents only; no real personal or customer data.
- Open decision: Technical owner validates staging access by 2030-04-02 (A-01).
- Agreement: fictional Sponsor selected this planned slice on 2030-04-01;
  D-01 in the [discovery record](discovery-workshop-agenda.md). No real approval exists.

Next: [smallest valuable slice](smallest-valuable-slice.md).
