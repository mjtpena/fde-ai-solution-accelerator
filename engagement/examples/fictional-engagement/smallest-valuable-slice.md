# Smallest valuable slice

## Slice definition

- Engagement / slice ID / owner role: Synthetic Reference Assistant / S-01 / Technical owner
- Intended user role: Evaluation reviewer
- Outcome: repeatable, cited read-only answers with demonstrable safety boundaries
- Hypothesis: 20 fabricated reference documents and 50 fixed cases are sufficient
  to assess a staging demonstration, not general production readiness.

## End-to-end path

| Step | Included capability                                             | Boundary or failure behaviour                                                  | Evidence gate       |
| ---- | --------------------------------------------------------------- | ------------------------------------------------------------------------------ | ------------------- |
| 1    | Ingest 20 synthetic documents, at most 1 MiB each               | Only approved fabricated text; no real uploads                                 | AC-02               |
| 2    | Authenticate test roles and resolve one of two synthetic scopes | Scope comes only from server-side `ExecutionContext`; deny unauthorised access | AC-01               |
| 3    | Retrieve filtered chunks and answer                             | Every claim cites chunks retrieved in that turn; abstain if insufficient       | AC-02, AC-03        |
| 4    | Display answer and redacted diagnostics                         | Retrieved instructions never control the assistant; no write tools             | AC-04, AC-06        |
| 5    | Measure and rehearse operation                                  | Staging only, bounded traffic, rollback and alerts                             | AC-05, AC-07, AC-08 |

## Explicit exclusions

Production rollout, real documents, business workflows, external connectors,
write actions, custom model training and high-availability guarantees are excluded.

## Dependencies and exit

- Prerequisites: A-01 access, A-02 synthetic corpus, A-03 cost review and A-04
  reviewer availability, owned and dated in [assumptions](assumptions-and-constraints.md).
- Time box: 10 working days, 2030-04-01 through 2030-04-12.
- Success: all eight [acceptance gates](acceptance-criteria.md) pass with reviewed evidence.
- Stop or reshape: missing access or failed safety gate pauses affected work;
  Sponsor reapproves any change to fee or schedule.
- Next slice: Sponsor reviews measured results; no production work is implied.

References: [problem](problem-statement.md), [scope](fixed-price-scope.md).
