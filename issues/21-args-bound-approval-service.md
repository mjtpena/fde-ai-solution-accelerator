# [M4] Args-bound approval service

## Goal
Human approval that can't be replayed or tampered with.

## Acceptance criteria
- [ ] Approval bound to tool + args hash + scope
- [ ] Expiry
- [ ] Exactly-once execution with row lock
- [ ] Tests: replay fails, modified args fail, expired fails
- [ ] `make check` passes
- [ ] `make eval-smoke` passes

## Files in scope
packages/agent_core/approvals/**, apps/api/**

## Out of scope / do not touch
Anything not listed in scope

## References
See `docs/spec.md` and `.github/instructions/`.

## Security-sensitive?
Yes — touches scope, approvals, auth, retrieval filters or telemetry

## Execution
Do this in VS Code agent mode yourself (`/implement-issue`).
