# [M3] Retrieval diagnostics endpoint

## Goal
Make retrieval inspectable.

## Acceptance criteria
- [ ] Endpoint returns query, filters, results, scores by correlation_id
- [ ] Contributor role required
- [ ] Content redacted per settings
- [ ] `make check` passes
- [ ] `make eval-smoke` passes

## Files in scope
apps/api/**

## Out of scope / do not touch
Anything not listed in scope

## References
See `docs/spec.md` and `.github/instructions/`.

## Security-sensitive?
Yes — exposes retrieval filters/results and enforces access to diagnostic data

## Execution
Do this in VS Code agent mode yourself (`/implement-issue`).
