# Security boundaries

`data_boundaries.context.ExecutionContext` is an immutable, server-created Pydantic
contract. Its `scope_ids` must come from `ScopeMembershipRepository.scope_ids_for`
using the validated Entra `oid`, never from request bodies, queries, or model output.
An unknown user has an empty scope set; database errors propagate instead of
masquerading as an authorization result.

`infrastructure.memberships.ScopeMembership` maps an Entra object ID to a generic
scope ID. The composite primary key prevents duplicate memberships.
`SqlAlchemyScopeMembershipRepository` accepts the shared async `SessionFactory`
from `infrastructure.database.create_session_factory(engine)`. Provision the table
through deployment migrations; repositories never create schemas on requests.
The application owns the PostgreSQL async engine and its lifecycle, authenticates
with managed identity, and injects the session factory. No database credentials
are stored by this package.

Tests use an in-memory SQLite async adapter to exercise the same SQLAlchemy queries
without credentials. PostgreSQL uses the `asyncpg` driver.

## API integration

The API host calls
`accelerator.identity.scope_resolver.configure_scope_resolver(app, repository)`
after `create_app`, injecting the shared repository. No engine or credential is
created at import time. Context-dependent endpoints declare
`context: Annotated[ExecutionContext, Depends(get_execution_context)]`, importing
`get_execution_context` from `accelerator.identity.scope_resolver`.
There is no production endpoint exposing membership lists.

The dependency uses the authenticated principal's `object_id` as `user_id`
and for the membership lookup. Token scope claims, body/query scope values,
and body/query identity values are ignored. Roles are mapped from the validated
principal; even `Admin` does not bypass membership checks. A missing `oid`
returns 403. Authentication retains #9's 401 behavior. Missing repository
configuration or a SQLAlchemy error returns 503 with a correlated structured
error log. The shared repository itself propagates database errors.

`create_app` installs correlation middleware: `X-Correlation-ID` must be a UUID,
or is generated if absent. Invalid values return 400. The same ID is stored in
`request.state.correlation_id`, the execution context, structured resolver logs,
and the response header (including authentication/authorization error responses).
Successful resolution also stores `request.state.execution_context`. Session ID
is unset; it is not accepted from request input. The UTC deadline starts before
the membership query and defaults to 30 seconds, configurable through
`ScopeResolverSettings` / `API_SCOPE_DEADLINE_SECONDS`.
