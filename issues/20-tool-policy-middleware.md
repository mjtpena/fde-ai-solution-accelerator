# [M4] Tool policy middleware

## Goal
Enforce tool risk outside the model.

## Acceptance criteria
- [ ] Write tools return ApprovalRequired
- [ ] Per-turn and per-session call limits
- [ ] Tool timeouts
- [ ] Policy tests
- [ ] `make check` passes
- [ ] `make eval-smoke` passes

## Files in scope
packages/agent_core/middleware/**, packages/security_core/tool_policy/**

## Out of scope / do not touch
Anything not listed in scope

## References
See `docs/spec.md` and `.github/instructions/`.

## Security-sensitive?
Yes — touches scope, approvals, auth, retrieval filters or telemetry

## Execution
Do this in VS Code agent mode yourself (`/implement-issue`).
