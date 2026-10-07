# [M5] Baseline comparison and PR report

## Goal
Show evaluation movement on every PR.

## Acceptance criteria
- [ ] Compare to `baselines/accepted.json` with tolerances
- [ ] Markdown summary posted to PR
- [ ] `.github/workflows/evaluation.yml`
- [ ] Replace the M1 `make eval-smoke` stub with the real smoke evaluation
- [ ] `make check` passes
- [ ] `make eval-smoke` passes

## Files in scope
packages/evaluation_core/reporting/**, .github/workflows/evaluation.yml, Makefile

## Out of scope / do not touch
evaluations/baselines/**

## References
See `docs/spec.md` and `.github/instructions/`.

## Security-sensitive?
No

## Execution
Suitable to assign to the Copilot coding agent.
