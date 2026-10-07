# [M1] Next.js baseline with generated API client

## Goal
Stand up the web app with a typed client generated from OpenAPI.

## Acceptance criteria
- [ ] App Router, TypeScript strict
- [ ] Node.js 22 LTS is pinned in `apps/web/.nvmrc` and declared in `apps/web/package.json` engines
- [ ] Standalone Docker build
- [ ] Typed API client generated from `contracts/api/openapi.json`
- [ ] Home page calls `/healthz`
- [ ] `make check` passes
- [ ] `make eval-smoke` passes

## Files in scope
apps/web/**

## Out of scope / do not touch
apps/api/**

## References
See `docs/spec.md` and `.github/instructions/`.
Depends on #2 (`contracts/api/openapi.json`).

## Security-sensitive?
No

## Execution
Do this in VS Code agent mode yourself (`/implement-issue`).
