# Threat model

A machine-readable register of the threats this accelerator defends against and the
controls that address them. The sources are `docs/spec.md` section 7 (security
baseline) and the non-negotiable rules in `.github/copilot-instructions.md`.

| File | Contents |
|---|---|
| `threats.yml` | Threats `T-001`… with STRIDE category, rating and mitigating controls |
| `controls.yml` | Controls `C-001`… with the code that implements them and the evidence that verifies them |
| `test-cases/` | Reserved for adversarial test inputs. The executable cases live in the paths listed under `verified_by` and in the smoke dataset (`evaluations/example-datasets/smoke.jsonl`) |

`tests/test_threat_model.py` validates both files, and `make check` runs it.

## Schema

Both files have a top-level `schema_version: 1`.

### `threats.yml` → `threats: [...]`

| Key | Type | Rule |
|---|---|---|
| `id` | string | `T-` followed by three digits, unique |
| `title` | string | Short name |
| `description` | string | The attack and the harm it causes |
| `stride` | enum | `spoofing`, `tampering`, `repudiation`, `information_disclosure`, `denial_of_service` or `elevation_of_privilege` |
| `assets` | list of strings | What is at risk |
| `likelihood` | enum | `low`, `medium` or `high`, assuming the listed controls are absent |
| `impact` | enum | `low`, `medium` or `high` |
| `controls` | list of `C-` ids | At least one. Every id must exist in `controls.yml` |

### `controls.yml` → `controls: [...]`

| Key | Type | Rule |
|---|---|---|
| `id` | string | `C-` followed by three digits, unique |
| `title`, `description` | string | What the control does, stated as behaviour |
| `type` | enum | `preventive`, `detective` or `corrective` |
| `status` | enum | `implemented`, `partial` or `planned` |
| `notes` | string | **Required** unless `status` is `implemented`. Says what is missing |
| `implemented_in` | list of repo paths | Files or directories that implement the control. Must exist. Must be non-empty unless `planned` |
| `verified_by` | list of repo paths | Test files, CI workflow files or other checks that prove the control. Must exist |
| `gates` | list of strings | Optional. `make eval-smoke` gates that exercise the control. Each value must be a `GateName` in `packages/evaluation_core/runners/smoke.py`. The hard gates are `citation_validity`, `scope_isolation`, `approval_bypass`, `injection_followed` and `content_safety` |

A control that is not `planned` must have `verified_by` or `gates` evidence. All
paths are relative to the repository root. Every control must be referenced by at
least one threat.

## Keeping it current

Update these files in the same pull request as the change that affects them:

* **New security-relevant behaviour** (a guard, a validation, an RBAC assignment,
  a CI gate): add or extend a control. List its implementation and test paths, then
  reference it from each threat it mitigates.
* **Moved or renamed files**: `tests/test_threat_model.py` fails on stale paths.
  Fix the paths rather than deleting entries.
* **New attack surface** (a new tool, route, data store, identity, external
  dependency or ingestion format): add a threat, or extend an existing one's
  description and controls.
* **Status changes**: when a gap in `notes` is closed, set `status: implemented`,
  remove or update `notes`, and add the test that proves it. Never mark a control
  `implemented` unless a test or CI gate covers it.
* **New smoke gates**: add the `GateName` value to the relevant controls' `gates`.
* Never reuse or renumber ids. To retire an entry, remove it and every reference to
  it in the same change.

Run `uv run pytest -q tests/test_threat_model.py` before you push.

## Known gaps (from `notes`)

* C-005: the `fde.retrieval.injection_signal_count` span attribute has no test and
  no alert rule.
* C-019: only successful approved write-tool executions reach the audit log.
  Read-tool runs and failed approved writes are not recorded, and no production code
  calls `AuditRecorder.tool_execution`.
* C-021: PostgreSQL server-side `require_secure_transport` is not pinned in Bicep.
  It relies on the platform default.
* C-023: security scans are not required status checks until a repository ruleset is
  configured (`.github/SECURITY_SCANNING.md`).
* C-024: the web Dockerfile pins its base image by tag, not by digest.
* C-025: parsers run in the separate worker container without a dedicated sandbox or
  per-document resource limits.
* C-026: there is no network egress restriction for tool code.
* C-029: streamed answers are screened after generation, so token frames can reach
  the client before a blocked answer is withdrawn; no test calls a live Content
  Safety account; the hosted agent screens only if its workflow factory adds a
  checker.
