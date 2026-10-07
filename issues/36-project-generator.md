# [M7] Project generator

## Goal
Generate new projects from the accelerator.

## Acceptance criteria
- [ ] `accelerator.manifest.yml`
- [ ] `make new-project` renames packages, removes examples, writes ACCELERATOR_VERSION
- [ ] Generated project passes `make check` in CI
- [ ] `make check` passes
- [ ] `make eval-smoke` passes

## Files in scope
scripts/new_project.py, accelerator.manifest.yml, Makefile

## Out of scope / do not touch
packages/**

## References
See `docs/spec.md` and `.github/instructions/`.

## Security-sensitive?
No

## Execution
Suitable to assign to the Copilot coding agent.
