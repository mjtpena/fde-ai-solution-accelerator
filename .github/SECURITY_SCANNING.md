# Security scanning and merge enforcement

`workflows/security.yml` runs on every pull request (including forks), pushes to
`main`, merge groups, weekly, and on demand. No path filters or privileged
pull-request trigger are used. The existing **Quality checks** job is unchanged.

CodeQL uses the extended security suite for Python, JavaScript/TypeScript, and
GitHub Actions, and uploads results to code scanning. Trivy rejects HIGH and
CRITICAL vulnerabilities (including unfixed ones), misconfigurations, and
secrets in the repository (including development dependencies), and
vulnerabilities and secrets in both built runtime images. Trivy exits nonzero
for findings at those thresholds; scanner errors also fail their jobs.
Dependency review rejects newly introduced HIGH/CRITICAL vulnerable
dependencies in pull requests. A successful CodeQL workflow means analysis
completed, not that no alerts were found. Required status checks block failed
workflow jobs only when configured in an active ruleset or branch-protection
rule; CodeQL alerts additionally require the **Require code scanning results**
rule in a ruleset. Dependabot updates the root uv and npm workspaces, GitHub
Actions, and both Dockerfiles weekly. There are no vulnerability suppressions
or automatic merges.

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

Repository status recorded on 2026-10-07: GitHub reports no repository
rulesets, so merge enforcement is not configured. Repository vulnerability
alerts and Dependabot security updates are enabled, and the Dependency Graph is
active; CodeQL default setup was not configured. The Dependency Review check
subsequently passed after the Dependency Graph was enabled
([run 37590593815, attempt 2](https://github.com/mjtpena/fde-ai-solution-accelerator/actions/runs/37590593815/attempts/2)).
Until required status checks are enforced in an active ruleset or
branch-protection rule, failing workflow checks do **not** prevent a manual
merge. Even when the CodeQL job is a required status check, its success does
not establish that there are no alerts; an active **Require code scanning
results** rule with the documented threshold is also needed to block
qualifying alerts. Do not describe this repository as merge-gated until an
administrator verifies the rules are active.

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

## Initial scan baseline

The findings below are retained scan evidence, not a claim that the affected
dependencies or images have been remediated. Scanner results can change as
commits, base-image tags, and advisory databases change.

The first hosted scan of this change
([run 37590125202](https://github.com/mjtpena/fde-ai-solution-accelerator/actions/runs/37590125202))
successfully executed all scanners:

- **Quality checks**, **Security configuration**, and all three CodeQL language
  checks passed. CodeQL identified a floating setup-uv action reference introduced
  here; all new workflow action references are now pinned to commit SHAs.
- **Dependency review** initially failed because the repository's Dependency
  Graph was disabled. The graph is now enabled and a later Dependency Review
  run passed; see the current status above.
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

## Outstanding findings and integration status

The latest status recorded for issue #35 on 2026-10-07 is:

| Area | Latest evidence and remaining work |
| --- | --- |
| Ingestion Dockerfile filesystem finding | Cleared on the integrated PR #54 head `fd7b0be906642cdb42a5a9dfc2cfceb9d145f1cf` after merging `main` at `32aaf1d`, including owning PR #61's non-root Alpine remediation. The hosted repository scan reports no remaining DS-0002 finding. |
| npm development dependencies | `braces` 3.0.3 remains HIGH for CVE-2026-93687, with no patched release available. Removing npm from the runtime image does not resolve this development-lockfile finding; development dependencies remain included in the repository scan. |
| Ingestion runtime image | **Passed** the unchanged hosted image gate on integrated head `fd7b0be906642cdb42a5a9dfc2cfceb9d145f1cf`. The old 44 HIGH Debian findings are historical; the merged Alpine remediation clears this check without suppressions. |
| Web runtime image | **Still failed** on integrated head `fd7b0be906642cdb42a5a9dfc2cfceb9d145f1cf`: 11 HIGH Node-package findings. The separate PR #79 owner-head local scan passed after npm removal, but that fix is not present in this integrated head. Its local result does not replace this hosted failure. The report lists 10 distinct CVE identifiers for 11 findings; no extra CVE is inferred. |

The current-base rescan is
[security run 37606050590](https://github.com/mjtpena/fde-ai-solution-accelerator/actions/runs/37606050590).
Security configuration, all three CodeQL language jobs, Dependency Review, and
Trivy ingestion image passed. Trivy repository failed only on the unfixed
`braces` finding; Trivy web image failed on the findings above.
[Quality run 37606050822](https://github.com/mjtpena/fde-ai-solution-accelerator/actions/runs/37606050822)
passed. Local configuration tests (7), actionlint, and `make check` (70 Python
and 9 web tests) also passed after reconciliation. `make eval-smoke` still exits
successfully with the existing placeholder, not a model-quality evaluation.

These dependency and image findings require remediation in their owning
changes, outside issue #35's `.github/**` scope. Keep the thresholds and
fail-closed checks unchanged; do not add suppressions or claim remediation
without a passing scan of the exact integrated head. Counts are point-in-time
observations and may change with advisory databases or base-image tags.
