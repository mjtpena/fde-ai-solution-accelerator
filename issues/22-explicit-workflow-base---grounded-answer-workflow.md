# [M4] Explicit workflow base + grounded-answer workflow

## Goal
Deterministic RAG workflow reused by projects.

## Acceptance criteria
- [ ] Workflow base class
- [ ] Grounded-answer workflow: retrieve → sufficiency → generate → validate citations
- [ ] Integration test
- [ ] `make check` passes
- [ ] `make eval-smoke` passes

## Files in scope
packages/agent_core/workflows/**

## Out of scope / do not touch
Anything not listed in scope

## References
See `docs/spec.md` and `.github/instructions/`.

## Security-sensitive?
No

## Execution
Suitable to assign to the Copilot coding agent.
