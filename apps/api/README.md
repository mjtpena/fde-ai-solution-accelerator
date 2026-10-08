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

The default ASGI entry point has no host providers: absent workflow or scope
repository returns 503, never a synthetic answer or permissive scope. Configure
audit persistence as described below so authentication failures can be recorded.
`test_configured_app_authenticates_and_streams_without_dependency_overrides`
exercises this composition seam with a signed test JWT, real authentication and
scope resolution, and test-only workflow/repository/JWKS fixtures.

`AuditRecorder` exposes async `auth_failure`, `approval`, and `tool_execution`
hooks. Call approval/tool hooks from trusted API application code with the
authenticated execution context, after the decision or execution result is known.
They do not authorise or execute tools. Events contain only an ID, UTC timestamp,
type, outcome, correlation ID, authenticated actor ID, and an approval ID or
registered tool name. Never pass credentials, arguments, results, document text,
or user-supplied tool names. There is no arbitrary metadata payload.

`PostgresAuditRepository` takes #10's shared async `SessionFactory`; its table is
registered in the shared `Base.metadata`. It has only
`append` and bounded `query` operations. Apply `schema/001_audit_event.sql` once
with the deployment/migration identity. The runtime database identity must not
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
never by runtime repositories. Include `schema/001_audit_event.sql` in the
deployment's one-time migration sequence; `Base.metadata.create_all` alone does
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
