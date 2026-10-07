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
