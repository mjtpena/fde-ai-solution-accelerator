# [M2] Entra ID authentication (web + API)

## Goal
Authenticate every request with Entra ID.

## Acceptance criteria
- [ ] Web signs in with MSAL
- [ ] API validates JWT (issuer, audience, signature)
- [ ] App roles Reader/Contributor/Approver/Admin mapped
- [ ] Unauthenticated → 401, wrong role → 403 (tests)
- [ ] `make check` passes
- [ ] `make eval-smoke` passes

## Files in scope
apps/api/src/*/identity/**, apps/web/lib/**

## Out of scope / do not touch
packages/**

## References
See `docs/spec.md` and `.github/instructions/`.

## Security-sensitive?
Yes — touches scope, approvals, auth, retrieval filters or telemetry

## Execution
Do this in VS Code agent mode yourself (`/implement-issue`).
