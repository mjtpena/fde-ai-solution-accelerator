# Risk register

## Record and scoring

- Engagement / revision / risk owner role: Synthetic Reference Assistant / 1 /
  Technical owner
- Review cadence: daily; formal gate reviews on days 2, 7 and 9
- Likelihood: low = unlikely in the slice; medium = plausible; high = expected
  without treatment. Qualitative estimates, not measured probabilities.
- Impact: low = less than one day rework; medium = date or budget change; high =
  safety breach or unusable result.
- Priority: any high impact = high; otherwise any medium rating = medium;
  otherwise low. High priority pauses the relevant gate until treatment is verified.

## Risks and treatment

| ID   | Cause and consequence                                      | Likelihood / impact / priority | Owner role          | Mitigation and trigger                                      | Contingency                                                    | Residual risk / status     |
| ---- | ---------------------------------------------------------- | ------------------------------ | ------------------- | ----------------------------------------------------------- | -------------------------------------------------------------- | -------------------------- |
| R-01 | A-01 access delay blocks integration                       | Medium / medium / medium       | Technical owner     | Verify access by day 2; escalate if unavailable             | Pause and rebaseline dates                                     | Medium / open              |
| R-02 | Missing scope filter leaks synthetic cross-scope evidence  | Medium / high / high           | Security reviewer   | AC-01 must pass on day 7; any exposed chunk stops release   | Stop staging, fix boundary, rerun negative tests               | High until verified / open |
| R-03 | Retrieved injection or unsupported claim undermines safety | Medium / high / high           | Evaluation reviewer | AC-02 to AC-04 on day 7; any unsafe claim blocks acceptance | Fix and rerun frozen quality set                               | High until verified / open |
| R-04 | Illustrative prices understate provisioned service cost    | High / medium / medium         | Operations owner    | Validate A-03 by day 2; review at 140 AUD                   | Do not provision or stop at 180 AUD; reapprove sizing/cap      | Medium / open              |
| R-05 | Missing recovery knowledge prevents safe transfer          | Medium / high / high           | Operations owner    | AC-08 rehearsal by day 9; failed rollback stops handover    | Retain delivery ownership; retest within reserve or rebaseline | High until verified / open |

## Review decisions

| Date       | Risk ID      | Decision / evidence                                             | Reviewer role   | Next review / expiry |
| ---------- | ------------ | --------------------------------------------------------------- | --------------- | -------------------- |
| 2030-04-01 | R-01 to R-05 | Hypothetical risks identified; no mitigation completion claimed | Technical owner | 2030-04-02           |

No residual safety risk has been accepted. Security reviewer may stop the
demonstration immediately; Sponsor resolves schedule/budget escalation within
one working day but cannot waive safety controls.

References: [assumptions](assumptions-and-constraints.md),
[requirements](non-functional-requirements.md), [delivery](delivery-plan.md).
