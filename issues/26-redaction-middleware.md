# [M5] Redaction middleware

## Goal
Keep secrets and content out of telemetry.

## Acceptance criteria
- [ ] Patterns for keys, tokens, emails
- [ ] Prompt/response capture off by default in prod
- [ ] Test greps exported spans
- [ ] `make check` passes
- [ ] `make eval-smoke` passes

## Files in scope
packages/security_core/redaction/**, packages/observability_core/**

## Out of scope / do not touch
Anything not listed in scope

## References
See `docs/spec.md` and `.github/instructions/`.

## Security-sensitive?
Yes — touches scope, approvals, auth, retrieval filters or telemetry

## Execution
Do this in VS Code agent mode yourself (`/implement-issue`).
