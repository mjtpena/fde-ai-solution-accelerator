# Testing strategy

This repository has four layers of tests. Each layer states what it checks and
what it does not. Where a check needs Azure, this page gives the command to run
it once credentials exist. No layer fakes a model answer or an embedding: where
Azure AI Search or Foundry would be called, the tests either use a stated
deterministic stand-in inside a unit test, or assert that the system refuses to
continue.

| Layer | Runs | Real | Stand-ins |
| --- | --- | --- | --- |
| Unit and component (`make check`) | Every PR (`quality` job) | Python and TypeScript code in process | Fakes behind the ports: repositories, queues, the chat workflow, `httpx.MockTransport` for JWKS |
| Database and emulator (`make check` with `TEST_*` variables) | Every PR (`quality` job) | PostgreSQL (migrations, triggers, row locks, advisory locks), Azurite queues and blobs | The API and worker run inside the test process |
| Real processes (`make e2e-local`) | Every PR (`e2e-local` job) | Separate API, worker, migration and Next.js processes; PostgreSQL; Azurite; RS256 tokens checked against a JWKS served over HTTP; Chromium | A local signing authority in place of Entra ID; Search indexing turned off by an explicit setting |
| Live Azure | On demand, and after `deploy-dev` | Azure AI Search, Foundry models, Entra ID, managed identities | None |

## Real-process suite (`tests/e2e_local`)

```sh
# PostgreSQL (a role that can create databases) and Azurite on its default ports.
make up   # or start them yourself; see "Starting the services" below
TEST_POSTGRES_DSN=postgresql://accelerator:local-development-only@127.0.0.1:5432/postgres \
TEST_AZURITE_CONNECTION_STRING=UseDevelopmentStorage=true \
make e2e-local
```

`make e2e-local` sets `E2E_LOCAL=1`. Without that variable the suite is not
collected, so a plain `pytest` run is unaffected. With it, missing services make
the run fail instead of skipping. The browser test also needs `npm ci` and a
Playwright Chromium (`npx --workspace apps/web playwright install chromium`). Add
`E2E_LOCAL_WEB=0` to leave it out; it is then reported as skipped, with the
reason. Child-process logs go to a temporary directory, or to
`E2E_LOCAL_LOG_DIR` when it is set. CI sets it and uploads the logs when the job
fails. A full run takes about 90 seconds; 15 of them are the worker's real
retry backoff.

Each module creates its own throwaway database, migrates it with
`python -m accelerator.migrations upgrade head` and drops it at the end. It
never touches the database named in `TEST_POSTGRES_DSN`. Queue and container
names carry a random suffix.

### What runs and what it checks

**API** (`test_api_processes.py`). This module starts two
`uvicorn --factory accelerator.api.main:create_application` replicas on one
database, and three more for specific failure modes. They run with
`API_ENVIRONMENT=test`, `API_ENTRA_ISSUER` and `API_ENTRA_JWKS_URI` pointing at
the local signing authority, and no Search or Foundry settings. It checks:

- `/healthz` returns 200. `/readyz` reports the database and the identity
  provider as `ok` and the chat workflow as `not_configured`, so it returns 503.
- Twelve kinds of bad credential each get a 401 with `WWW-Authenticate: Bearer`
  and an `auth_failure` row in `audit_event`. The kinds are: missing, Basic
  scheme, not a JWT, expired, not yet valid, wrong audience, wrong issuer,
  signed by another key, unknown `kid`, no `exp`, HS256 key confusion and
  `alg=none`.
- Wrong or missing app roles get a 403 and an `authorization_failure` row that
  carries the caller's object ID. An Admin then reads those rows back through
  `GET /audit-events`.
- Every response class carries the security headers: 200, 400, 401, 403, 404,
  422, 429 and 503. JSON responses are never cached. CORS allows only
  `API_WEB_ORIGIN`.
- `POST /chat/stream` fails closed on both replicas with a JSON 503 and no event
  stream. Authentication is checked first.
- 80 concurrent requests from one user, split across the two replicas, admit
  exactly `min(count, limit)` per window in total. The `rate_limit_windows`
  table counts every request exactly once. Each 429 carries `Retry-After`. Both
  replicas stay healthy, and another user is unaffected.
- Approvals:
  - A requester cannot decide their own approval (403, audited).
  - An Approver from another scope gets 404.
  - A Reader gets 403.
  - A second Approver can approve or reject. The decision is written to
    `approvals`, `approval_audit_events` and `audit_event`.
  - Repeat decisions get 409.
  - An approval past its expiry gets 409 `approval_expired` and is stored as
    `expired`.
  - Five rounds of an approve and a reject sent at the same moment to the two
    replicas each produce exactly one decision.
- Deadlines. A JWKS endpoint that hangs makes `/readyz` report
  `Check timed out.` within the 3 s readiness deadline, while `/healthz` stays
  responsive. Authenticated calls get a 503 (never a 401) within the 5 s
  identity-provider timeout, and the failed key refresh is not retried on every
  request. The `ExecutionContext` deadline (`API_SCOPE_DEADLINE_SECONDS`) bounds
  Search queries and tool calls only, so it can be exercised only with Azure.
- With a per-client audit cap of 3, eight unauthenticated requests get eight
  401s and exactly three audit rows.
- With an unreachable database, `/readyz` reports the database as failed.
  Unauthenticated requests get 503 `Audit persistence is unavailable.`, and
  authenticated ones get 503 `Scope resolver is unavailable.`.
- Every log line is JSON, has no stack trace and contains no bearer token. Every
  process shuts down cleanly on SIGTERM.

**Ingestion worker** (`test_ingestion_processes.py`). This module starts two
`python -m accelerator.ingestion` processes against Azurite and PostgreSQL with
`INGESTION_SKIP_SEARCH_INDEXING=true`. Documents are uploaded to the incoming
container. Messages follow the contract in `workers/ingestion/README.md`. It
checks:

- Markdown, plain text and a multi-page Flate-compressed PDF are parsed. These
  are the three types on the allow-list. The checks for each document:
  - it is chunked into the same chunk IDs the pipeline computes for the same
    bytes;
  - Markdown section headings are recorded, and a `#` line inside a code fence
    is not a heading;
  - the canonical blob is stored under a flat name;
  - the row has the right hash and ends as `indexing_skipped`.
- Redelivery. Four copies of the same message, taken by both workers, change
  nothing. The row is not rewritten, and dedupe is by hash and version.
- A new version replaces the lineage and removes stale chunk rows. A delete
  message removes the blob, the document row and its chunks.
- Hostile uploads are poisoned on first delivery with `failed(reason)` and no
  retry. They are: oversized, an executable declared as PDF, binary declared as
  text, a 120 MB decompression-bomb PDF, a cyclic page tree, deeply nested PDF
  objects, an empty file and a whitespace-only file. Messages with an
  unsupported content type (`.docx`), unknown fields or bad JSON are poisoned as
  `invalid message` and leave no document state. A `../../` document ID stays a
  single encoded blob name.
- A missing source blob is retried after the 15 s backoff. It succeeds once the
  blob appears. If it never appears, it is poisoned with
  `ResourceNotFoundError after 2 attempt(s)`.

**Web** (`test_web_against_api.py` and `apps/web/tests/e2e-real-api`). This test
runs `next dev`, with its route handlers proxying to the real API. The MSAL
session cache is seeded with a real signed token, which is the only sign-in
shortcut. The page shows `API status: ok` from the real `/healthz`. A chat
message shows `Chat request failed with status 503.` and no answer text. The
next message shows status 429, because the API allows one request per window.
A forged token shows status 401. Afterwards the database confirms two counted
requests for the token's user and scope, and one `auth_failure` row.

### What the real-process suite does not cover

- **Azure AI Search and embeddings.** `INGESTION_SKIP_SEARCH_INDEXING` replaces
  the `ChunkIndex` with one that writes nothing and logs
  `search_indexing_skipped`. Documents end as `indexing_skipped`, never `ready`.
  The flag is refused in production and next to any Search or Foundry setting.
  No embeddings are invented: the repository has no deterministic local
  embedder. The only fixed vector is in the live Search isolation test, which
  queries a real service.
- **Grounded answers, citations and approvals created by the model.** With no
  workflow configured, chat answers 503. The tests create approvals with
  `ApprovalService.create`, the same call the tool-policy middleware makes.
- **Entra ID itself.** The tokens have Entra's shape and are checked by the
  production validator, but a local key signs them. Managed-identity
  authentication to PostgreSQL is covered by the TLS tests in `make check`
  (`TEST_POSTGRES_CA_FILE`), not here.

### Starting the services without Docker Compose

```sh
npx azurite@3.33.0 --inMemoryPersistence --skipApiVersionCheck --loose \
  --blobHost 127.0.0.1 --queueHost 127.0.0.1 --tableHost 127.0.0.1
```

`--skipApiVersionCheck` is required, because the storage SDKs in `uv.lock`
request a newer service version than Azurite 3.33 accepts. Any PostgreSQL 16
server works if its role can create databases.

## Live Azure checks

These need an Azure identity (`az login`, or OIDC in CI) with the roles listed
in `docs/deployment-guide.md`. None of them run in pull-request CI.

| What | Command | Needs |
| --- | --- | --- |
| Cross-scope isolation in Azure AI Search | `TEST_AZURE_SEARCH_ENDPOINT=https://<service>.search.windows.net uv run --all-packages pytest apps/api/src/accelerator/infrastructure/search/tests/test_live_isolation.py` (add `TEST_AZURE_SEARCH_SEMANTIC=1` when semantic ranking is enabled) | Search Service Contributor and Search Index Data Contributor |
| Grounded answers, retrieval, sufficiency, citations and abstention, scored by a judge model | `make eval-full` with the `API_*` Foundry, Search and database settings, `EVALUATION_PRINCIPAL_OBJECT_ID`, `EVALUATION_JUDGE_AZURE_ENDPOINT` and `EVALUATION_JUDGE_AZURE_DEPLOYMENT` exported | A deployed environment; see "Full evaluation" in the deployment guide |
| The deployed web app and API answer | `make deploy-dev STAGE=smoke` | A `deploy-dev` environment |
| Ingestion with real embeddings and indexing | Run the worker with every `INGESTION_SEARCH_*`, `INGESTION_VECTOR_DIMENSIONS` and `INGESTION_FOUNDRY_*` setting and without `INGESTION_SKIP_SEARCH_INDEXING`. Enqueue the same messages as `tests/e2e_local`. Documents must end as `ready` | Search Index Data Contributor and Foundry project access |

**Content Safety.** No code in this repository calls Azure AI Content Safety.
The content-safety step in `docs/spec.md` §6.2 currently relies on the content
filters of the Foundry model deployment, and no test exercises it. Verifying it
needs a deployed environment and a dataset of adversarial prompts run through
`make eval-full`. A dedicated adapter and its tests are still to be built.
