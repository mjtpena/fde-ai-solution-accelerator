# [M2] Server-side scope resolver

## Goal
Derive the caller's allowed scopes on the server only.

## Acceptance criteria
- [ ] `ExecutionContext.scope_ids` resolved from token + DB
- [ ] Tests prove request body/query cannot widen scope
- [ ] Correlation ID propagated
- [ ] `make check` passes
- [ ] `make eval-smoke` passes

## Files in scope
apps/api/src/*/identity/**, packages/security_core/**

## Out of scope / do not touch
packages/agent_core/**

## References
See `docs/spec.md` and `.github/instructions/`.

## Security-sensitive?
Yes — touches scope, approvals, auth, retrieval filters or telemetry

## Execution
Do this in VS Code agent mode yourself (`/implement-issue`).
