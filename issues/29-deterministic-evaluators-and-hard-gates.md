# [M5] Deterministic evaluators and hard gates

## Goal
Fast, exact checks.

## Acceptance criteria
- [ ] Citation validity, abstention, tool selection, scope isolation, approval bypass
- [ ] Hard gates fail on single failure
- [ ] Deliberately broken fixture proves each gate
- [ ] `make check` passes
- [ ] `make eval-smoke` passes

## Files in scope
packages/evaluation_core/**

## Out of scope / do not touch
Anything not listed in scope

## References
See `docs/spec.md` and `.github/instructions/`.

## Security-sensitive?
Yes — touches scope, approvals, auth, retrieval filters or telemetry

## Execution
Do this in VS Code agent mode yourself (`/implement-issue`).
