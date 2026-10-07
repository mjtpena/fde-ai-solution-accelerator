# [M1] Docker Compose local stack

## Goal
Run the full local stack with one command.

## Acceptance criteria
- [ ] Postgres, Azurite, API, web, worker start with `make up`
- [ ] Health checks on every service
- [ ] `.env.example` documents every variable
- [ ] Placeholder ingestion worker has a minimal entry point with a structured startup log and graceful shutdown, plus a Dockerfile; real ingestion is out of scope until M3
- [ ] `make check` passes
- [ ] `make eval-smoke` passes

## Files in scope
docker-compose.yml, .env.example, Makefile, workers/ingestion/** (placeholder only)

## Out of scope / do not touch
apps/**/src/**

## References
See `docs/spec.md` and `.github/instructions/`.

## Security-sensitive?
No

## Execution
Do this in VS Code agent mode yourself (`/implement-issue`).
