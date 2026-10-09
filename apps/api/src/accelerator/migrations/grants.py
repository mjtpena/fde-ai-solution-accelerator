"""Least-privilege table grants for the runtime database roles.

Migrations run as the schema owner. After an upgrade, the owner grants each runtime
role exactly the privileges its code uses, so neither the API nor the worker can
change the schema, and the append-only audit tables never accept UPDATE or DELETE.
Grants are additive and idempotent; a privilege removed here must be revoked by a
migration.
"""

import re
from collections.abc import Mapping

from sqlalchemy import text
from sqlalchemy.engine import Connection

API_TABLE_PRIVILEGES: Mapping[str, tuple[str, ...]] = {
    "audit_event": ("SELECT", "INSERT"),
    "scope_memberships": ("SELECT",),
    "approvals": ("SELECT", "INSERT", "UPDATE"),
    "approval_audit_events": ("SELECT", "INSERT"),
    "rate_limit_windows": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "tool_call_counters": ("SELECT", "INSERT", "UPDATE", "DELETE"),
}

WORKER_TABLE_PRIVILEGES: Mapping[str, tuple[str, ...]] = {
    "documents": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "document_chunks": ("SELECT", "INSERT", "UPDATE", "DELETE"),
}

# The operator (the PostgreSQL Entra administrator) manages who belongs to which
# scope and reads memberships for the full evaluation; nothing else.
OPERATOR_TABLE_PRIVILEGES: Mapping[str, tuple[str, ...]] = {
    "scope_memberships": ("SELECT", "INSERT", "DELETE"),
}

ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
# Entra principal names (e.g. a group "DB Admins" or "admin@contoso.com") are roles too.
ENTRA_ROLE_NAME = re.compile(r"^[^\x00-\x1f\x7f]{1,63}$")


def quoted_role(name: str, *, entra: bool = False) -> str:
    pattern = ENTRA_ROLE_NAME if entra else ROLE_NAME
    if not pattern.fullmatch(name):
        raise ValueError("Database role names must be valid SQL identifiers.")
    return '"' + name.replace('"', '""') + '"'


def grant_runtime_privileges(
    connection: Connection,
    *,
    api_role: str | None,
    worker_role: str | None,
    operator_role: str | None = None,
) -> None:
    for role, privileges, entra in (
        (api_role, API_TABLE_PRIVILEGES, False),
        (worker_role, WORKER_TABLE_PRIVILEGES, False),
        (operator_role, OPERATOR_TABLE_PRIVILEGES, True),
    ):
        if role is None:
            continue
        grantee = quoted_role(role, entra=entra)
        for table, actions in privileges.items():
            # Identifiers come from the constants above and a validated role name.
            connection.execute(
                text(f"GRANT {', '.join(actions)} ON TABLE {table} TO {grantee}")  # noqa: S608
            )
