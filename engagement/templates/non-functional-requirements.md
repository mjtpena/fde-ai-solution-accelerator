# Non-functional requirements

## Record

- Engagement / revision / date: [name / revision / date]
- Reviewer: [technical and security roles]
- Measurement environment: [region, model/version, configuration and workload]

## Measurable requirements

| ID       | Area                        | Requirement / threshold                            | Verification and workload        | Owner role | Acceptance ID |
| -------- | --------------------------- | -------------------------------------------------- | -------------------------------- | ---------- | ------------- |
| [NFR-01] | Authorisation               | [zero cross-scope evidence]                        | [negative tests]                 | [role]     | [AC-ID]       |
| [NFR-02] | Grounding and safety        | [citation and abstention thresholds]               | [fixed dataset; injection cases] | [role]     | [AC-ID]       |
| [NFR-03] | Performance                 | [percentile, latency, concurrency and time window] | [repeatable load test]           | [role]     | [AC-ID]       |
| [NFR-04] | Reliability / recovery      | [availability or recovery target]                  | [fault/recovery exercise]        | [role]     | [AC-ID]       |
| [NFR-05] | Privacy / observability     | [redaction, audit and retention limits]            | [redacted trace inspection]      | [role]     | [AC-ID]       |
| [NFR-06] | Cost                        | [currency, cap, alert and stop thresholds]         | [usage and budget evidence]      | [role]     | [AC-ID]       |
| [NFR-07] | Accessibility / operability | [standard and operational criteria]                | [manual and automated checks]    | [role]     | [AC-ID]       |

## Measurement rules

[Specify sample sizes, denominators, exclusions, evidence retention, metric
owners and treatment of missing evidence. Targets are not measured results.]

Missing or failed safety evidence blocks release. Record any other exception
with rationale, reviewer, expiry and remediation in
[acceptance criteria](acceptance-criteria.md); do not silently weaken gates.
