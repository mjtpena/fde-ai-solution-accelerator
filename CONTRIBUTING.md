# Contributing

Use Python 3.12, uv, Node.js 22, npm 10 or later, and GNU Make.
Run `make setup` to install the locked workspace dependencies and pre-commit
hooks. Keep changes focused on an issue and include tests for behaviour changes.

## Local checks

Before opening a pull request, run:

```sh
make check
uv build --all-packages
npm run build --workspace apps/web
make eval-smoke
```

`make check` runs Python linting, strict type-checking and tests, then the web
workspace's API-client generation, linting, formatting check, type-checking and
tests. The build commands produce Python distributions and the standalone
Next.js application.

`make eval-smoke` currently reports that evaluations are not implemented until
M5 (issue #30). A successful placeholder invocation is not evidence that model
quality has been evaluated.

## Required pull-request status check

Configure the `main` branch protection rule or ruleset to require the
**Quality checks** status check from the **Pull-request CI** workflow
(`.github/workflows/pull-request.yml`) before merging. The workflow runs on
every pull request, including documentation-only changes and fork contributions;
it has no path filters or privileged pull-request trigger.

The job installs frozen Python and npm workspace dependencies, using uv and npm
caches keyed by their lockfiles, and runs all four commands above. A failed
lint, type-check, test, build or smoke command fails the required check.
Keep the job name stable so the required-check configuration continues to match.
Repository administrators must enable this requirement in GitHub after the
workflow has reported its first status; documenting it does not enable branch
protection automatically.

Use the repository pull-request template, link the issue, and record the exact
validation commands and outcomes. Do not commit secrets or generated build
artifacts.
