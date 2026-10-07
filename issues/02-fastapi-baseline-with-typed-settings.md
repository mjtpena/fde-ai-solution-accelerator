# [M1] FastAPI baseline with typed settings

## Goal
Stand up the API with health endpoints and fail-fast configuration.

## Acceptance criteria
- [ ] `/healthz` and `/readyz` endpoints
- [ ] OpenAPI generated to `contracts/api/openapi.json`
- [ ] `pydantic-settings` config fails fast on missing values
- [ ] Unit tests for both endpoints
- [ ] `make check` passes
- [ ] `make eval-smoke` passes

## Files in scope
apps/api/**, contracts/api/**

## Out of scope / do not touch
packages/**

## References
See `docs/spec.md` and `.github/instructions/`.

## Security-sensitive?
No

## Execution
Do this in VS Code agent mode yourself (`/implement-issue`).
