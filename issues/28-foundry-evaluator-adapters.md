# [M5] Foundry evaluator adapters

## Goal
Run Foundry RAG/agent evaluators locally and in CI.

## Acceptance criteria
- [ ] Groundedness, relevance, retrieval, completeness
- [ ] Config-driven judge model
- [ ] Runs via `make eval-full`
- [ ] `make check` passes
- [ ] `make eval-smoke` passes

## Files in scope
packages/evaluation_core/evaluators/**

## Out of scope / do not touch
Anything not listed in scope

## References
See `docs/spec.md` and `.github/instructions/`.

## Security-sensitive?
No

## Execution
Suitable to assign to the Copilot coding agent.
