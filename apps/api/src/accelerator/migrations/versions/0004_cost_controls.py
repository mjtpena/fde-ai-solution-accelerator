"""Shared rate-limit windows and tool-call counters.

Revision ID: 0004_cost_controls
Revises: 0003_approvals
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_cost_controls"
down_revision: str | None = "0003_approvals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_windows",
        sa.Column("limiter_key", sa.String(64), primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("request_count", sa.Integer(), nullable=False),
    )
    op.create_table(
        "tool_call_counters",
        sa.Column("session_id", sa.String(255), primary_key=True),
        sa.Column("turn_id", sa.String(255), primary_key=True),
        sa.Column("call_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("tool_call_counters")
    op.drop_table("rate_limit_windows")
