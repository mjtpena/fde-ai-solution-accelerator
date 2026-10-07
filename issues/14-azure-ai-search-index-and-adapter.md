# [M3] Azure AI Search index and adapter

## Goal
Hybrid retrieval with mandatory scope filtering.

## Acceptance criteria
- [ ] Index definition as code
- [ ] Hybrid + semantic ranker query
- [ ] Scope filter ALWAYS injected from ExecutionContext
- [ ] Integration test proves cross-scope results impossible
- [ ] `make check` passes
- [ ] `make eval-smoke` passes

## Files in scope
packages/retrieval_core/search/**, packages/retrieval_core/indexing/**,
apps/api/src/accelerator/infrastructure/search/**

## Out of scope / do not touch
Anything not listed in scope. Keep the `Retriever` interface in `retrieval_core`;
Azure SDK calls belong only in the API infrastructure adapter.

## References
See `docs/spec.md` and `.github/instructions/`.

## Security-sensitive?
Yes — touches scope, approvals, auth, retrieval filters or telemetry

## Execution
Do this in VS Code agent mode yourself (`/implement-issue`).
