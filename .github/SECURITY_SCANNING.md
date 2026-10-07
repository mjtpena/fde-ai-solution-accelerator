# Security scanning and merge enforcement

`workflows/security.yml` runs on every pull request (including forks), pushes to
`main`, merge groups, weekly, and on demand. No path filters or privileged
pull-request trigger are used. The existing **Quality checks** job is unchanged.

CodeQL uses the extended security suite for Python, JavaScript/TypeScript, and
GitHub Actions, and uploads results to code scanning. Trivy rejects HIGH and
CRITICAL vulnerabilities (including unfixed ones), misconfigurations, and
secrets in the repository (including development dependencies), and vulnerabilities
and secrets in both built
runtime images. Scanner errors also fail the jobs. Dependency review rejects
new HIGH/CRITICAL vulnerable dependencies in pull requests. Dependabot updates
the root uv and npm workspaces, GitHub Actions, and both Dockerfiles weekly.
There are no vulnerability suppressions or automatic merges.

## GitHub administrator setup (required)

Repository files cannot enable native security features or enforce merge rules.
After the workflow first reports its checks, an administrator must configure
an active `main` ruleset or branch protection without removing existing checks:

1. Require **Quality checks**, **Security configuration**, **CodeQL (python)**,
   **CodeQL (javascript-typescript)**, **CodeQL (actions)**, **Dependency review**,
   **Trivy repository**, **Trivy image (web)**, and **Trivy image (ingestion)**.
   Bind status checks to the GitHub Actions app and require branches to be up
   to date. Require pull requests, and do not grant scanner bypasses.
2. Add **Require code scanning results** with tool **CodeQL**, security alert
   threshold **High or higher**, and non-security alert threshold **Errors**.
   A successful CodeQL workflow means analysis completed, not that it found no
   vulnerabilities; the code-scanning rule is essential to block findings.
   Disable CodeQL default setup if it is enabled before using this advanced
   workflow. Allow GitHub Actions to upload code-scanning results.
3. Enable the dependency graph, Dependabot alerts, and Dependabot security
   updates. Version-update configuration alone does not enable these settings.
4. Keep native **Secret scanning** and **Push protection** enabled. Enable
   non-provider patterns where supported. Native secret scanning runs on
   GitHub and does not provide a normal required Actions status; push protection
   prevents supported secrets from being pushed, and the required Trivy check
   provides an additional PR secret gate. Investigate native alerts, revoke
   exposed credentials, and do not use bypasses to land detected secrets.

At implementation time, this public repository had secret scanning and push
protection enabled, but no `main` branch protection or rulesets. Dependabot
alerts and security updates were disabled; CodeQL default setup was not
configured. These observations are not a substitute for administrator setup.
Until the required rules are active, failing jobs do **not** prevent a manual
merge. This PR must not be considered fully enforced until setup is complete.

If a merge queue is introduced, dependency review only runs on pull requests;
retain its PR requirement and configure the queue accordingly. All other
security jobs also run on `merge_group`.

## Validation

Run the regression tests using isolated test-only tools (no runtime dependency
changes):

```sh
uv run --no-project --python 3.12 --with pytest==8.4.2 --with pyyaml==6.0.3 pytest .github/tests -q
actionlint .github/workflows/security.yml
make check
make eval-smoke
```

The tests check coverage, triggers, least privilege, and fail-closed scanner
settings. GitHub-hosted runs provide the actual CodeQL, dependency-review, and
container-scan evidence. Existing vulnerabilities can fail these checks; fix
them in the owning issue rather than weakening gates or adding suppressions.

## Initial baseline blockers

The first hosted scan of this change
([run 37590125202](https://github.com/mjtpena/fde-ai-solution-accelerator/actions/runs/37590125202))
successfully executed all scanners:

- **Quality checks**, **Security configuration**, and all three CodeQL language
  checks passed. CodeQL identified a floating setup-uv action reference introduced
  here; all new workflow action references are now pinned to commit SHAs.
- **Dependency review** failed because the repository's Dependency Graph is
  disabled. An administrator must enable it; retry the check after setup.
- **Trivy repository** failed HIGH DS-0002 in
  `workers/ingestion/Dockerfile`: the existing image has no non-root `USER`.
  The runtime npm/uv lockfiles had no HIGH/CRITICAL findings in this first run.
  Subsequent runs also scan development dependencies.
- **Trivy image (ingestion)** built successfully and failed on 44 HIGH findings
  in the Debian 13.7 base image.
- **Trivy image (web)** built successfully and failed on 11 HIGH Node-package
  findings in the existing runtime image, including bundled npm/tooling
  dependencies (`brace-expansion`, `pacote`, `sigstore`) and an unfixed
  `http-cache-semantics` finding.

These application Dockerfile/base-image changes are outside issue #35's
`.github/**` scope. Keep the failing gates and resolve the baseline findings in
the owning changes before landing. Counts are a point-in-time observation:
mutable base-image tags and updated advisory databases can change later results.
