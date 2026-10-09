# API audit events

## Streaming chat host composition

The host constructs the authenticated application with
`create_app(settings, chat_turn=workflow, scope_repository=memberships)`.
`workflow` implements `accelerator.api.chat.ChatTurnPort`; it can be the existing
`GroundedAnswerWorkflow` composed with the host's retriever, sufficiency checker,
answer generator, citation validator, and retrieval-request factory, or the
tool-policy workflow that returns `ApprovalRequired`. `memberships` implements
`ScopeMembershipRepository` and uses the host-managed database session factory.
The host owns provider configuration and managed-identity engine lifecycle.
Existing `configure_scope_resolver(app, memberships)` remains supported.

`POST /chat/stream` accepts only `message`. Authentication validates the bearer
token before scope resolution; identity and memberships are never supplied by
the browser. It emits validated answer chunks, clickable citation metadata,
structured abstentions, or a pending approval reference. Approval cards do not
execute tools or expose bound arguments. Answer chunks are emitted only after
the workflow completes citation validation, not during unvalidated generation.

The default ASGI entry point, `accelerator.api.main:app`, is built by the
composition root `accelerator.api.composition.build_application(settings)`. It
creates the async SQLAlchemy engine from `API_DATABASE_URL` with explicit pool
limits (`API_DATABASE_POOL_*`), disposes it on shutdown, and wires
`SqlAlchemyScopeMembershipRepository`, `PostgresAuditRepository`, the
request-scoped `ApprovalService` dependency (`get_approval_service`) and the
cost-guard dependency. With `API_DATABASE_AUTH_MODE=managed_identity` every
pooled connection authenticates with a fresh Microsoft Entra token from
`DefaultAzureCredential`; the DSN carries no password. Development and test may
run without a database, in which case persistence-backed routes return 503;
production settings validation refuses to start without one. A route whose
workflow is not configured still returns 503, never a synthetic answer.

When Foundry and Azure AI Search are configured (always, in production), the
composition root builds the grounded-answer workflow from
`accelerator.infrastructure.grounded_answer`: the scope-injecting
`AzureSearchRetriever` with Foundry query embeddings, the threshold
`EvidenceSufficiencyChecker` (`API_SUFFICIENCY_*`, gating on the semantic
reranker score by default), a tool-less Foundry agent created by `AgentFactory`
from `instructions/grounded_answer.md` as the answer generator, and
`SameTurnCitationValidator`. All Azure clients share one `DefaultAzureCredential`
for the user-assigned identity in `AZURE_CLIENT_ID` and are closed on shutdown.

`AuditRecorder` exposes async `auth_failure`, `approval`, and `tool_execution`
hooks. Call approval/tool hooks from trusted API application code with the
authenticated execution context, after the decision or execution result is known.
They do not authorise or execute tools. Events contain only an ID, UTC timestamp,
type, outcome, correlation ID, authenticated actor ID, and an approval ID or
registered tool name. Never pass credentials, arguments, results, document text,
or user-supplied tool names. There is no arbitrary metadata payload.

`PostgresAuditRepository` takes #10's shared async `SessionFactory`; its table is
registered in the shared `Base.metadata`. It has only
`append` and bounded `query` operations. The `0001_audit_event` Alembic migration creates
it; run migrations with the deployment/migration identity. The runtime database identity must not
own the table or schema: grant only SELECT and INSERT on `audit_event`, and no
schema CREATE or trigger-management privileges. Database triggers also reject
UPDATE, DELETE, and TRUNCATE. Runtime code never creates schema or connects using
embedded credentials.

`GET /audit-events` requires the authenticated `Admin` role. It returns `items`
with `limit` (1–100, default 50), `offset` (0–10000, default 0), and optional
`event_type` (`auth_failure`, `approval`, `tool_execution`). Results are ordered by
timestamp and event ID descending. Offset pagination is bounded, not a snapshot:
new inserts can shift subsequent pages. No public write/mutation endpoint exists.

The HTTP middleware records 401 responses before sending headers; request
headers, paths, bodies, and query strings are never stored. Correlation IDs for
unauthenticated requests come from #10's UUID-validating correlation boundary,
and match the response header. Missing IDs are server-generated. SQLAlchemy
failures and unconfigured persistence return 503 with a correlated, structured
error log; raw database errors are never returned or logged. Unexpected errors
propagate rather than returning an apparently successful audit operation.

## Host composition on the M2 stack

The admin query uses `accelerator.identity.scope_resolver.get_execution_context`
directly; neither roles nor identity/scope are taken from audit query input.
The API host must call `accelerator.api.audit.configure_audit(app, session_factory)`
after `create_app`, with the same factory used by the scope resolver. The
application owns the PostgreSQL engine/lifecycle and managed-identity connection,
not the audit adapter. Tests may inject an `AuditRepository` into `create_app`.
Without configuration, a 401 that cannot be recorded becomes 503; this is an
explicit fail-closed audit outage, not a successful unaudited authentication
response. Health and CORS behavior for authenticated requests is unchanged.

Like #10's membership table, schema is provisioned by deployment migrations,
never by runtime repositories. Alembic migrations under
`src/accelerator/migrations` (`make migrate`, or
`python -m accelerator.migrations upgrade head` in a container) provision it; `Base.metadata.create_all` alone does
not install the PostgreSQL immutability triggers. Provisioning database roles and
the managed-identity engine remains the host's responsibility.

SQLAlchemy is declared directly because the adapter imports it for async
inserts/queries. Existing #9 `httpx` is reused. API dev dependency `asyncpg`
declares the PostgreSQL driver required by the explicit integration test; its
version constraint matches #10's shared database runtime dependency.

## Verification

Unit tests: `uv run --all-packages pytest apps/api/tests/test_audit.py`.
Strict API types: `uv run --all-packages mypy --config-file apps/api/pyproject.toml --strict apps/api/src apps/api/tests`.
PostgreSQL verification: apply the schema to a disposable local database, set
`API_AUDIT_TEST_PORT` to its loopback port, then run
`uv run --all-packages pytest apps/api/tests/postgres_audit.py`.
This explicitly selected database test fails if the database is unavailable; it
does not skip or silently replace PostgreSQL with an in-memory database.
It also exercises HTTP admin authorisation and auth-failure persistence, shared
scope resolution, and approval/tool hooks through test-only routes using the same
PostgreSQL session factory.

## Container image

`apps/api/Dockerfile` builds from the repository root
(`docker build -f apps/api/Dockerfile .`). It installs the locked dependencies
with `uv sync --frozen --no-dev` into a virtual environment, copies only that
environment into a digest-pinned `python:3.12-slim` runtime, runs as UID 10001,
and health-checks `GET /healthz` over HTTP. The same image runs migrations
(`python -m accelerator.migrations upgrade head`); `docker compose up` does this
in the one-shot `migrate` service before the API starts.
