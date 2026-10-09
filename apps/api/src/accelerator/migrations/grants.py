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

ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def quoted_role(name: str) -> str:
    if not ROLE_NAME.fullmatch(name):
        raise ValueError("Database role names must be lowercase SQL identifiers.")
    return f'"{name}"'


def grant_runtime_privileges(
    connection: Connection, *, api_role: str | None, worker_role: str | None
) -> None:
    for role, privileges in (
        (api_role, API_TABLE_PRIVILEGES),
        (worker_role, WORKER_TABLE_PRIVILEGES),
    ):
        if role is None:
            continue
        grantee = quoted_role(role)
        for table, actions in privileges.items():
            # Identifiers come from the constants above and a validated role name.
            connection.execute(
                text(f"GRANT {', '.join(actions)} ON TABLE {table} TO {grantee}")  # noqa: S608
            )
