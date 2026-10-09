"""Allow authorization_failure (403) audit events.

Revision ID: 0006_authorization_failure_audit
Revises: 0005_ingestion
Create Date: 2026-10-09
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_authorization_failure_audit"
down_revision: str | None = "0005_ingestion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TOOL_NAME = "tool_name ~ '^[A-Za-z0-9_.-]{1,128}$'"
_BASE = (
    "(event_type = 'auth_failure' AND outcome = 'failed' "
    "AND actor_id IS NULL AND approval_id IS NULL AND tool_name IS NULL) OR "
    "(event_type = 'approval' AND outcome IN ('approved', 'denied') "
    "AND actor_id IS NOT NULL AND approval_id IS NOT NULL AND tool_name IS NULL) OR "
    "(event_type = 'tool_execution' AND outcome IN ('succeeded', 'failed') "
    f"AND actor_id IS NOT NULL AND approval_id IS NULL AND tool_name IS NOT NULL AND {_TOOL_NAME})"
)
_AUTHORIZATION = (
    "(event_type = 'authorization_failure' AND outcome = 'denied' "
    "AND approval_id IS NULL AND tool_name IS NULL)"
)


def upgrade() -> None:
    op.drop_constraint("audit_event_valid_fields", "audit_event", type_="check")
    op.create_check_constraint(
        "audit_event_valid_fields", "audit_event", f"{_BASE} OR {_AUTHORIZATION}"
    )


def downgrade() -> None:
    """Intentionally a no-op.

    audit_event is append-only, so authorization_failure rows written since the
    upgrade cannot be removed and would violate the narrower constraint.
    """
