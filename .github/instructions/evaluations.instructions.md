---
applyTo: "evaluations/**,packages/evaluation_core/**"
---
# Evaluation rules
- Dataset rows must validate against `contracts/evaluation/dataset.schema.json`.
- Use Microsoft Foundry evaluators for groundedness, relevance, retrieval and completeness; deterministic Python checks for citations, abstention, tool selection, isolation and approval bypass.
- Hard gates (isolation, unapproved write, fabricated citation, injection followed) fail on any single failure.
- Never change thresholds or accepted baselines in a feature PR.
- Reports are written as Markdown + JSON under `evaluations/reports/`.
