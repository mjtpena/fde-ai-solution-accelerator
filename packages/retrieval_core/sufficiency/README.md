# Evidence sufficiency

`SufficiencyPolicy(minimum_score=..., minimum_evidence_count=...)` evaluates
only the evidence retrieved for the current turn. Configure a finite score
threshold on the same scale as `Evidence.score` and a positive integer count.
There is no implicit score normalization or default threshold.

`evaluate(evidence)` returns the spec's `SufficiencyDecision` fields:
`sufficient`, `reason`, and `evidence_ids`. Evidence at the score threshold
qualifies; the count is based on distinct chunk IDs, not repeated results.
Only qualifying IDs appear in the decision, in retrieval order. An empty
retrieval always produces an insufficient decision. Non-finite scores and
invalid thresholds raise `ValueError`.

For an insufficient decision, `build_abstention_response(decision)` returns
a Pydantic response with `abstained: true`, `reason`, and `evidence_ids`.
It rejects sufficient decisions. Neither the policy nor the response
builder reads retrieved document text or generates answer claims.

Callers must pass same-turn retrieval results, gate generation on the
decision, and validate generated citations against that same retrieval set.
Passing a previous turn's evidence or a manually fabricated decision is not
made safe by this policy.

Run the focused tests with:

```powershell
uv run --all-packages pytest packages\retrieval_core\sufficiency\tests
```
