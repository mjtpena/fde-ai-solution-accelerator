# Security policy

## Reporting a vulnerability

Please do not open a public issue for a security problem. Report it through
GitHub's **private vulnerability reporting** for this repository: the
**Security** tab, then **Report a vulnerability**. Include the affected component,
version or commit, reproduction steps, and the impact you observed.

We aim to acknowledge a report within three business days and to agree on a fix
and disclosure timeline with you. Please give us a reasonable window to release a
fix before any public disclosure.

## Scope

In scope: this repository's code, its CI/CD workflows, its infrastructure-as-code,
and projects generated from it as they are generated. Deployed instances belong to
the teams that run them; report issues in a deployment to its owners.

Particularly relevant to this accelerator:

- authorization scope taken from anything but the server-side execution context;
- a write tool that runs without an executed, arguments-bound approval;
- retrieved or user-supplied text being treated as instructions;
- secrets or unredacted content in logs, traces, reports or images.

## Supported versions

Only the latest commit on `main` receives fixes. Generated projects own their
copy and should pull fixes from the accelerator's changelog.

## How the repository protects itself

Scanning and merge enforcement are described in
[.github/SECURITY_SCANNING.md](.github/SECURITY_SCANNING.md): CodeQL, Trivy
(repository and every container image), dependency review, Dependabot, and a
gitleaks pre-commit hook. The threat model lives in [threat-model/](threat-model/).
