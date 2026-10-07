# [M6] Managed identities and RBAC

## Goal
Secretless Azure access.

## Acceptance criteria
- [ ] UAMI per container app
- [ ] Least-privilege role assignments
- [ ] Local/key auth disabled where supported
- [ ] `make check` passes
- [ ] `make eval-smoke` passes

## Files in scope
infrastructure/**

## Out of scope / do not touch
Anything not listed in scope

## References
See `docs/spec.md` and `.github/instructions/`.

## Security-sensitive?
Yes — touches scope, approvals, auth, retrieval filters or telemetry

## Execution
Do this in VS Code agent mode yourself (`/implement-issue`).
