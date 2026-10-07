# [M3] Citation validation

## Goal
Reject citations that weren't retrieved in the turn.

## Acceptance criteria
- [ ] Validator checks every cited chunk_id
- [ ] Fake citation fails response (test)
- [ ] Validation result in trace
- [ ] `make check` passes
- [ ] `make eval-smoke` passes

## Files in scope
packages/retrieval_core/citations/**

## Out of scope / do not touch
Anything not listed in scope

## References
See `docs/spec.md` and `.github/instructions/`.

## Security-sensitive?
Yes — touches scope, approvals, auth, retrieval filters or telemetry

## Execution
Do this in VS Code agent mode yourself (`/implement-issue`).
