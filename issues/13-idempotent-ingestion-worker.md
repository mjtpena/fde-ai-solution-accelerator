# [M3] Idempotent ingestion worker

## Goal
Ingest documents reliably and repeatably.

## Acceptance criteria
- [ ] Hash-based dedupe
- [ ] Upsert by chunk_id; re-index on version change
- [ ] Delete removes blob + index + DB
- [ ] Failed state with reason
- [ ] `make check` passes
- [ ] `make eval-smoke` passes

## Files in scope
workers/ingestion/**

## Out of scope / do not touch
apps/web/**

## References
See `docs/spec.md` and `.github/instructions/`.

## Security-sensitive?
No

## Execution
Suitable to assign to the Copilot coding agent.
