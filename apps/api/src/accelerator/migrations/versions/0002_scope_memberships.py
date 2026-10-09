"""Server-side scope memberships.

Revision ID: 0002_scope_memberships
Revises: 0001_audit_event
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_scope_memberships"
down_revision: str | None = "0001_audit_event"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scope_memberships",
        sa.Column("object_id", sa.String(36), primary_key=True),
        sa.Column("scope_id", sa.String(255), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("scope_memberships")
