# Smallest valuable slice

## Slice definition

- Engagement / slice ID / owner role: [name / S-01 / role]
- Intended user role: [role]
- Outcome: [one valuable, observable technical result]
- Hypothesis: [why this slice is sufficient to test the outcome]

## End-to-end path

[Describe the input, server-resolved authorisation boundary, processing,
output and failure/abstention path. Keep application concepts domain-free.]

| Step   | Included capability | Boundary or failure behaviour | Evidence gate   |
| ------ | ------------------- | ----------------------------- | --------------- |
| [step] | [capability]        | [constraint]                  | [acceptance ID] |

## Explicit exclusions

[List capabilities, integrations, environments and data that are deferred.]

## Dependencies and exit

- Prerequisites: [assumption IDs, owners and dates]
- Time box: [duration and stop/review date]
- Success: [acceptance IDs and thresholds]
- Stop or reshape: [failed prerequisite or measurable failure condition]
- Next-slice decision: [reviewer role and evidence needed; no implied commitment]

References: [problem](problem-statement.md), [scope](fixed-price-scope.md),
[acceptance](acceptance-criteria.md).
