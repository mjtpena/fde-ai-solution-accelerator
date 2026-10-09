"""Allow content_safety audit events, with a bounded reason_code.

Revision ID: 0007_content_safety_audit
Revises: 0006_authorization_failure_audit
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_content_safety_audit"
down_revision: str | None = "0006_authorization_failure_audit"
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
    " OR "
    "(event_type = 'authorization_failure' AND outcome = 'denied' "
    "AND approval_id IS NULL AND tool_name IS NULL)"
)
# Refusals are denied; a screening outage that forced a refusal is failed.
_CONTENT_SAFETY = (
    "(event_type = 'content_safety' AND actor_id IS NOT NULL "
    "AND approval_id IS NULL AND tool_name IS NULL AND ("
    "(reason_code IN ('content_safety_prompt_attack', 'content_safety_output_blocked') "
    "AND outcome = 'denied') OR "
    "(reason_code = 'content_safety_unavailable' AND outcome = 'failed')))"
)


def upgrade() -> None:
    op.add_column("audit_event", sa.Column("reason_code", sa.String(64), nullable=True))
    op.drop_constraint("audit_event_valid_fields", "audit_event", type_="check")
    op.create_check_constraint(
        "audit_event_valid_fields",
        "audit_event",
        f"(({_BASE}) AND reason_code IS NULL) OR {_CONTENT_SAFETY}",
    )


def downgrade() -> None:
    """Intentionally a no-op.

    audit_event is append-only, so content_safety rows written since the upgrade
    cannot be removed and would violate the narrower constraint.
    """
