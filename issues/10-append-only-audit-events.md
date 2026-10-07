# [M2] Append-only audit events

## Goal
Record security-relevant events immutably.

## Acceptance criteria
- [ ] `audit_event` table, insert-only (no update/delete in code)
- [ ] Written for auth failures, approvals, tool execution
- [ ] Query endpoint for admins
- [ ] `make check` passes
- [ ] `make eval-smoke` passes

## Files in scope
apps/api/**

## Out of scope / do not touch
packages/agent_core/**

## References
See `docs/spec.md` and `.github/instructions/`.

## Security-sensitive?
No

## Execution
Suitable to assign to the Copilot coding agent.
