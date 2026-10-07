# [M1] Pull-request CI workflow

## Goal
Run quality checks on every PR.

## Acceptance criteria
- [ ] `.github/workflows/pull-request.yml` runs lint, type-check, tests, builds
- [ ] Uses uv and npm caching
- [ ] Required status check documented in CONTRIBUTING
- [ ] `make check` passes
- [ ] `make eval-smoke` passes

## Files in scope
.github/workflows/pull-request.yml, CONTRIBUTING.md

## Out of scope / do not touch
copilot-setup-steps.yml

## References
See `docs/spec.md` and `.github/instructions/`.

## Security-sensitive?
No

## Execution
Do this in VS Code agent mode yourself (`/implement-issue`).
