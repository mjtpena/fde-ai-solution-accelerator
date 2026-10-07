# [M1] Quality tooling and pre-commit

## Goal
Enforce lint, types and tests locally.

## Acceptance criteria
- [ ] Ruff, mypy --strict, pytest configured
- [ ] ESLint, Prettier, Vitest configured
- [ ] pre-commit hooks installed by `make setup`
- [ ] `make check` runs all of the above
- [ ] `make check` passes
- [ ] `make eval-smoke` passes

## Files in scope
pyproject.toml, apps/web/package.json, .pre-commit-config.yaml, Makefile

## Out of scope / do not touch
apps/**/src/**

## References
See `docs/spec.md` and `.github/instructions/`.

## Security-sensitive?
No

## Execution
Do this in VS Code agent mode yourself (`/implement-issue`).
