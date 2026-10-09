"""Args-bound approvals and their transition log.

Revision ID: 0003_approvals
Revises: 0002_scope_memberships
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_approvals"
down_revision: str | None = "0002_scope_memberships"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approvals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tool_name", sa.String(255), nullable=False),
        sa.Column("args_hash", sa.String(64), nullable=False),
        sa.Column("scope_id", sa.String(255), nullable=False),
        sa.Column("requested_by", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("decided_by", sa.String(255), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlation_id", sa.String(255), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'executed', 'expired')",
            name="approvals_valid_status",
        ),
    )
    op.create_index("ix_approvals_scope_status", "approvals", ["scope_id", "status"])
    op.create_table(
        "approval_audit_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("approval_id", sa.Uuid(), sa.ForeignKey("approvals.id"), nullable=False),
        sa.Column("transition", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlation_id", sa.String(255), nullable=False),
    )
    op.create_index(
        "ix_approval_audit_events_approval", "approval_audit_events", ["approval_id"]
    )


def downgrade() -> None:
    op.drop_table("approval_audit_events")
    op.drop_table("approvals")
