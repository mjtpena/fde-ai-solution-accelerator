---
mode: agent
description: Create or extend an evaluation dataset
---
Create/extend `evaluations/datasets/${input:file:name}.jsonl` for category `${input:category:factual|synthesis|conflict|unsupported|tool_selection|injection|isolation}`.

- Every row validates against `contracts/evaluation/dataset.schema.json`.
- Ground every expected answer and evidence ID in the actual corpus; cite file and section.
- Include negative cases (should abstain / should not call tools).
- Do not modify thresholds or baselines.
- Run the dataset through `make eval-smoke` and report results.
