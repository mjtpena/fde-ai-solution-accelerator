# [M1] Monorepo scaffold (uv workspace + npm)

## Goal
Create the repository skeleton so every later issue has a consistent home.

## Acceptance criteria
- [ ] Folder structure matches spec §4
- [ ] `uv` workspace with `apps/api`, `packages/*`, `workers/ingestion`
- [ ] Python import package is `accelerator`; distributions are `fde-accelerator-api`, `fde-agent-core`, `fde-retrieval-core`, `fde-evaluation-core`, `fde-observability-core`, `fde-security-core`, and `fde-ingestion-worker`
- [ ] `make setup` works on a clean machine
- [ ] `pyproject.toml` pins Python 3.12
- [ ] Include an MIT `LICENSE` with copyright `Michael John Peña`
- [ ] `make eval-smoke` prints `eval-smoke: not implemented until M5 (issue 30)` and exits successfully
- [ ] `make check` passes

## Files in scope
Makefile, pyproject.toml, uv.lock, LICENSE,
apps/api/pyproject.toml,
packages/agent_core/pyproject.toml,
packages/retrieval_core/pyproject.toml,
packages/evaluation_core/pyproject.toml,
packages/observability_core/pyproject.toml,
packages/security_core/pyproject.toml,
workers/ingestion/pyproject.toml

## Out of scope / do not touch
infrastructure/**

## References
See `docs/spec.md` and `.github/instructions/`.

## Security-sensitive?
No

## Execution
Do this in VS Code agent mode yourself (`/implement-issue`).
