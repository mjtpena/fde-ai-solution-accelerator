# Getting started

This guide takes a developer from a fresh clone of the accelerator, or of a
project generated from it, to a working local environment with passing checks.
For deploying to Azure, see [`deployment-guide.md`](deployment-guide.md).

## Prerequisites

| Tool | Version | Used for |
| --- | --- | --- |
| Python | 3.12 (`requires-python = "==3.12.*"`) | API, packages, worker, scripts |
| [uv](https://docs.astral.sh/uv/) | current | Python workspace and lockfile (`uv.lock`) |
| Node.js | 22 (`apps/web` requires `>=22 <23`) | Next.js web app |
| npm | 10 or later | npm workspace (`package-lock.json`) |
| GNU Make | any | every command below |
| Docker with Compose | current | local stack (`make up`) and the PostgreSQL/Azurite-backed tests |
| Git | any | `make setup` installs pre-commit hooks into the clone |

Deploying additionally needs the Azure CLI, PowerShell 7 and `psql`; see the
deployment guide.

## Set up the workspace

```sh
make setup
```

This runs `uv sync --all-packages --frozen`, `npm ci` and
`uv run pre-commit install`. Python distributions install under the
`accelerator` namespace (`accelerator.agent_core`, `accelerator.retrieval_core`,
`accelerator.evaluation_core`, `accelerator.observability_core`,
`accelerator.security_core`, `accelerator.ingestion`, and the API's
`accelerator.api`, `accelerator.application` and related layers). In a generated
project, `accelerator` is replaced by the project's module name.

## Repository layout

| Path | Contents |
| --- | --- |
| `apps/api/` | FastAPI API: authentication, scope resolution, chat streaming, approvals, audit, health probes, Alembic migrations (`src/accelerator/migrations`) |
| `apps/web/` | Next.js App Router web app; MSAL sign-in in `lib/auth/`, same-origin API route handlers in `app/api/` |
| `workers/ingestion/` | Queue-driven ingestion worker: parse, chunk, embed and index documents |
| `packages/agent_core/` | Agents, workflows, tools (`tools/base.py`, `tools/registry.py`), approvals, policies, hosting |
| `packages/retrieval_core/` | Parsing, chunking, index schema, sufficiency, citations |
| `packages/security_core/` | Authorization, data boundary, tool policy, redaction |
| `packages/evaluation_core/` | Datasets, runners, Foundry evaluator adapters, baselines and reporting |
| `packages/observability_core/` | OpenTelemetry tracing and middleware |
| `infrastructure/` | Bicep (`main.bicep`, `modules/`, `parameters/`), deployment scripts, Foundry hosted-agent packaging |
| `evaluations/` | Example datasets, accepted baseline, thresholds, generated reports |
| `contracts/` | OpenAPI contract (`api/openapi.json`) and JSON schemas for documents, chunks, evidence, retrieval requests and evaluation datasets |
| `threat-model/` | Threat-model test cases (currently an empty `test-cases/` directory) |
| `engagement/` | Domain-free delivery templates and a fictional filled-in example |
| `docs/` | Reference specification (`spec.md`), ADRs, runbook, handover checklist |
| `scripts/new_project.py` | Project generator (accelerator only; removed from generated projects) |

## Run the local stack

The local stack runs PostgreSQL, Azurite, a one-shot migration, the API, the
web app and the ingestion worker in Docker.

```sh
cp .env.example .env
# edit .env: set the four ENTRA_* values (see below)
make up            # docker compose up --build --detach
```

| Service | Port (default) | Notes |
| --- | --- | --- |
| `postgres` | 5432 | `postgres:16-alpine`, development-only password from `.env` |
| `azurite` | 10000 blob, 10001 queue, 10002 table | Azure Storage emulator |
| `migrate` | none | Runs `python -m accelerator.migrations upgrade head` and exits |
| `api` | 8000 | Starts after a successful migration; health check on `/healthz` |
| `web` | 3000 | Calls the API at `http://api:8000` from its server-side route handlers |
| `worker` | none | Idles (healthy) until Search and Foundry embedding settings are provided |

### Variables in `.env`

Compose refuses to start the API and web services until the Entra values are
set. Everything else has a working default.

| Variable | Required | Purpose |
| --- | --- | --- |
| `ENTRA_TENANT_ID` | yes | Tenant GUID. Passed to the API as `API_ENTRA_TENANT_ID` and compiled into the web bundle as `NEXT_PUBLIC_ENTRA_TENANT_ID` |
| `ENTRA_API_AUDIENCE` | yes | Expected `aud` claim of access tokens; passed to the API as `API_ENTRA_AUDIENCE` |
| `ENTRA_WEB_CLIENT_ID` | yes | Client ID of the web (SPA) app registration; `NEXT_PUBLIC_ENTRA_CLIENT_ID` |
| `ENTRA_API_SCOPE` | yes | Delegated scope the web app requests for the API, e.g. `api://<api-client-id>/<scope>`; `NEXT_PUBLIC_ENTRA_API_SCOPE` |
| `POSTGRES_*`, `AZURITE_*_PORT`, `API_PORT`, `WEB_PORT`, `WEB_ORIGIN` | no | Local ports, database name and credentials (development-only) |
| `API_FOUNDRY_PROJECT_ENDPOINT`, `API_FOUNDRY_MODEL_DEPLOYMENT`, `API_FOUNDRY_EMBEDDING_DEPLOYMENT` | no | Microsoft Foundry project and deployments |
| `API_SEARCH_ENDPOINT`, `API_SEARCH_INDEX_NAME`, `API_SEARCH_VECTOR_DIMENSIONS` | no | Azure AI Search index; dimensions must match the embedding deployment |
| `AZURE_CLIENT_ID` | no | User-assigned managed identity client ID (required in production only) |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | no | Telemetry export |
| `AZURITE_CONNECTION_STRING` | no | Azurite's published emulator account (not a secret); used by the worker only when Search and embedding settings are also set |

`.env` is ignored by Git and never copied by the project generator. Keep real
credentials out of it; Azure access uses `DefaultAzureCredential`, not keys.

Without the Foundry and Search settings the API runs in `development` mode with
no chat workflow: `POST /chat/stream` returns 503 rather than a synthetic
answer, and `GET /readyz` reports `chat_workflow: not_configured`.

### Entra app registrations

The web app signs users in with MSAL (`apps/web/lib/auth/msal.ts`), using the
authority `https://login.microsoftonline.com/<tenant>` (override with
`NEXT_PUBLIC_ENTRA_AUTHORITY_HOST` for sovereign clouds) and session storage.
It signs in with a popup and acquires tokens for `ENTRA_API_SCOPE`
(`apps/web/lib/auth/token.ts`). You need:

1. **An API app registration** that exposes a delegated scope (the value of
   `ENTRA_API_SCOPE`) and defines the app roles the API accepts: `Reader`,
   `Contributor`, `Approver`, `Admin`. Every route except the health probes
   requires a valid token carrying at least one of these roles; approvals need
   `Approver`, `GET /audit-events` needs `Admin`, retrieval diagnostics need
   `Contributor`. Set `ENTRA_API_AUDIENCE` to the `aud` value its tokens carry.
2. **A web (single-page application) registration** whose client ID is
   `ENTRA_WEB_CLIENT_ID`, granted the API scope. Register the local web origin
   (`http://localhost:3000`) as an SPA redirect URI; MSAL uses its default
   redirect URI, the current page.
3. **Role assignments** for the users who will sign in.

A signed-in user only retrieves documents from scopes listed for their Entra
object ID (`oid`) in the `scope_memberships` table; see
[Grant scope memberships](deployment-guide.md#grant-scope-memberships).

## Run the checks

```sh
make check
```

`make check` runs, in order:

1. `ruff check`
2. `mypy --strict` on the workspace packages, then on
   `workers/ingestion/tests` and `apps/api/tests/test_scope_resolver.py`
3. `pytest` (pass extra arguments with `PYTEST_ARGS`, e.g. `PYTEST_ARGS="--cov"`)
4. `npm run check --workspaces --if-present`: API client generation, ESLint,
   Prettier check, `tsc --noEmit` and Vitest for `apps/web`
5. In the accelerator only, `check-generator`: type-checks
   `scripts/new_project.py`, runs its self-tests, then generates a project in a
   temporary directory and runs that project's full `make check`. This is slow;
   skip it with `make check SKIP_GENERATOR=1` (CI runs it as a separate job).

### Database- and Azurite-backed tests

These tests are collected everywhere and **skipped** unless their variables are
set. CI sets all three.

| Variable | Enables |
| --- | --- |
| `TEST_POSTGRES_DSN` | Migration, audit persistence, approval row-lock and production boot tests. The role must be able to create databases: each test gets a throwaway, freshly migrated database (`conftest.py`) |
| `TEST_POSTGRES_CA_FILE` | The managed-identity and production-boot tests that require verified TLS to PostgreSQL (`apps/api/tests/test_migrations.py`, `apps/api/tests/test_boot.py`, `workers/ingestion/tests/test_database.py`). Point it at the test server's certificate |
| `TEST_AZURITE_CONNECTION_STRING` | Ingestion queue and blob tests (`workers/ingestion/tests/test_azurite_queue.py`); `UseDevelopmentStorage=true` for Azurite on its default ports |
| `TEST_AZURE_SEARCH_ENDPOINT` | The live cross-scope Azure AI Search test (`apps/api/src/accelerator/infrastructure/search/tests/test_live_isolation.py`) |

With the local stack running (`make up`), the plain-password tests can use the
compose database and Azurite:

```sh
TEST_POSTGRES_DSN=postgresql://accelerator:local-development-only@127.0.0.1:5432/postgres \
TEST_AZURITE_CONNECTION_STRING=UseDevelopmentStorage=true \
make check SKIP_GENERATOR=1
```

The TLS tests need a server with TLS enabled. CI starts one in
`.github/workflows/pull-request.yml` ("Start PostgreSQL for database-backed
tests") with a throwaway self-signed certificate; reuse that recipe locally and
set `TEST_POSTGRES_CA_FILE` to the certificate.

### Other commands

| Command | What it does |
| --- | --- |
| `make eval-smoke` | Offline smoke evaluation over `evaluations/example-datasets/smoke.jsonl` and the fixture corpus. Compares against `evaluations/baselines/accepted.json` with `evaluations/thresholds.yml` if present, otherwise `evaluations/thresholds.example.yml`. Writes `evaluations/reports/smoke.json` and `smoke.md`. Exit 0 pass, 1 gate or regression failure, 2 configuration error. It checks the control plane, not model quality |
| `make eval-full` | Full evaluation against live Azure services; see the deployment guide |
| `make openapi` | Regenerates `contracts/api/openapi.json` from the FastAPI app and `apps/web/lib/api/schema.d.ts` from it |
| `make openapi-check` | Runs `make openapi` and fails if either file changed. CI runs it; commit the regenerated files with any API change |
| `make migrate` | `python -m accelerator.migrations upgrade head` against `API_DATABASE_URL` (and `API_DATABASE_AUTH_MODE`). `make` does not read `.env`; export the variables first |
| `make e2e-local` | Real-process end-to-end suite: migrations, two API replicas, two ingestion workers and Playwright against the real API, on PostgreSQL and Azurite. Needs `TEST_POSTGRES_DSN` and `TEST_AZURITE_CONNECTION_STRING`; fails rather than skips without them. See `docs/testing-strategy.md`, which also lists the live Azure checks |
| `npm run test:e2e --workspace apps/web` | Playwright end-to-end tests. Starts a mock API (`tests/e2e/mock-api.mjs`, port 8100) and `next dev` on port 3100 with placeholder Entra values; no Azure or Entra access needed |
| `uv build --all-packages` and `npm run build --workspace apps/web` | Build the Python distributions and the standalone Next.js app (also run by PR CI) |

Install a browser before the first Playwright run, as CI does:

```sh
npx --workspace apps/web playwright install --with-deps chromium
npm run test:e2e --workspace apps/web
```

Set `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` to use a preinstalled Chromium instead.

To migrate the compose database from the host:

```sh
API_DATABASE_URL=postgresql+asyncpg://accelerator:local-development-only@localhost:5432/accelerator \
make migrate
```

Before opening a pull request, run what the required **Quality checks** job
runs: `make check`, `make openapi-check`, `uv build --all-packages`,
`npm run build --workspace apps/web` and `make eval-smoke` (see
[`CONTRIBUTING.md`](../CONTRIBUTING.md)).

## Common changes

Reusable agent workflows live in `.github/prompts/`. Use them with Copilot, or
follow them by hand.

| Change | Start from |
| --- | --- |
| Add an agent tool | [`add-tool.prompt.md`](../.github/prompts/add-tool.prompt.md): subclass `EnterpriseTool` (`packages/agent_core/tools/base.py`), declare a `ToolRisk`, define a Pydantic args model with **no** scope or project ID, register it in the `ToolRegistry` (`packages/agent_core/tools/registry.py`), and test success, validation failure, timeout and the write-requires-approval policy. Add at least three tool-selection dataset rows |
| Add an evaluation dataset row | [`add-eval-dataset.prompt.md`](../.github/prompts/add-eval-dataset.prompt.md) and [`contracts/evaluation/README.md`](../contracts/evaluation/README.md). Every row has all nine fields of `contracts/evaluation/dataset.schema.json`; `make check` loads every `*.jsonl` under `evaluations/` and fails on an invalid row. Datasets live in `evaluations/example-datasets/` in the accelerator and `evaluations/datasets/` in a generated project. Never change thresholds or baselines in the same PR |
| Add a document to a knowledge base | Upload it and enqueue an ingestion message; see [`workers/ingestion/README.md`](../workers/ingestion/README.md) and [Ingest documents](deployment-guide.md#ingest-documents). Fixture documents for the offline smoke evaluation live in `tests/fixtures/retrieval/corpus/` with a `manifest.json` |
| Record an architecture decision | [`write-adr.prompt.md`](../.github/prompts/write-adr.prompt.md); ADRs go in `docs/adr/` |
| Implement or review an issue | [`implement-issue.prompt.md`](../.github/prompts/implement-issue.prompt.md), [`review-pr.prompt.md`](../.github/prompts/review-pr.prompt.md) |

## Where the rules live

| File | Scope |
| --- | --- |
| [`AGENTS.md`](../AGENTS.md) | Entry point for non-Copilot agents; points to the files below |
| [`.github/copilot-instructions.md`](../.github/copilot-instructions.md) | Repository-wide non-negotiables: no other agent/RAG frameworks, scope only from `ExecutionContext`, args-bound approvals for write tools, retrieved text is untrusted, citations from the same turn, managed identity only, Azure SDK calls behind `infrastructure/` adapters, domain-free |
| `.github/instructions/*.instructions.md` | Path-specific rules: `agents` (agent code), `bicep` (`infrastructure/**`), `evaluations`, `python` (`**/*.py`), `retrieval` (retrieval code and `workers/**`), `typescript` (`apps/web/**`) |
| `apps/web/AGENTS.md` | Next.js version notes for agents working in the web app |
| [`CONTRIBUTING.md`](../CONTRIBUTING.md) | Local checks and the required PR status check |

## Generate a new project

From an accelerator checkout (the generator is not present in generated
projects):

```sh
make new-project NAME=my-solution TITLE="My Solution"
make new-project NAME=my-solution TITLE="My Solution" DEST=/absolute/path/my-solution
```

| Variable | Rules |
| --- | --- |
| `NAME` | Required. Lowercase kebab-case, at most 64 characters, not a Python keyword, standard-library module or `accelerator`. Becomes the package prefix (`<name>-api`, `<name>-agent-core`, ...) and, with hyphens turned into underscores, the Python namespace that replaces `accelerator` |
| `TITLE` | Display name that replaces "FDE AI Solution Accelerator". Required (it is not `DISPLAY`, the X11 display variable that desktop shells set) |
| `DEST` | Optional. Defaults to a sibling directory of the checkout named `NAME`. Must not exist, must not overlap the checkout, and its parent must exist |

The generator (`scripts/new_project.py`, driven by `accelerator.manifest.yml`):

- copies the paths listed under `copy` in the manifest, skipping caches,
  `node_modules`, `.env` files and build output, and removes
  `engagement/examples` and the generator itself;
- renames packages, the `accelerator` namespace and the display name, and
  `evaluations/example-datasets` to `evaluations/datasets`;
- strips the project-generator block from the Makefile and the generator
  sections from `docs/spec.md`;
- copies the engagement templates into `engagement/project/`, writes a single
  synthetic abstention row to `evaluations/datasets/starter.jsonl`, and writes
  `ACCELERATOR_VERSION` and a new `README.md`;
- runs `uv sync --all-packages --frozen`, an import-sort fix, `npm ci` and
  `make check` in the new project. A failure leaves the output in place for
  diagnosis and exits nonzero; only a passing `make check` counts as generated.

A generated project does not receive `infrastructure/` (Bicep, the deployment
script, the hosted-agent package). It keeps only the CI workflows the manifest
lists (quality, evaluation, security, Copilot setup), and everything between
`# BEGIN ACCELERATOR ONLY` / `# END ACCELERATOR ONLY` markers (in Markdown,
`<!-- BEGIN ACCELERATOR ONLY -->` / `<!-- END ACCELERATOR ONLY -->`) is stripped,
including `make deploy-dev` and the accelerator's own scan evidence. Threat-model
controls implemented partly or wholly in files it does not receive are downgraded to
`partial` or `planned`, with a note saying what is missing. Copy or recreate `infrastructure/` and `deploy-dev.yml` when the
project is ready to deploy.

After generating, `git init` the new directory, fill in `engagement/project/`,
and replace the starter dataset with project-owned evaluation cases.
