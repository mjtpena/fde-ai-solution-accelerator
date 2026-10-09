"""Append-only audit_event table (formerly schema/001_audit_event.sql).

Idempotent so databases provisioned with the old SQL file (which have the table
and triggers but no alembic_version) adopt Alembic by running ``upgrade head``.

Revision ID: 0001_audit_event
Revises:
Create Date: 2026-10-09
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001_audit_event"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_event (
            event_id uuid PRIMARY KEY,
            occurred_at timestamptz NOT NULL,
            event_type varchar(32) NOT NULL,
            outcome varchar(16) NOT NULL,
            correlation_id varchar(128) NOT NULL CHECK (length(correlation_id) > 0),
            actor_id varchar(128) CHECK (length(actor_id) > 0),
            approval_id uuid,
            tool_name varchar(128),
            CONSTRAINT audit_event_valid_fields CHECK (
                (event_type = 'auth_failure' AND outcome = 'failed'
                    AND actor_id IS NULL AND approval_id IS NULL AND tool_name IS NULL)
                OR
                (event_type = 'approval' AND outcome IN ('approved', 'denied')
                    AND actor_id IS NOT NULL AND approval_id IS NOT NULL
                    AND tool_name IS NULL)
                OR
                (event_type = 'tool_execution' AND outcome IN ('succeeded', 'failed')
                    AND actor_id IS NOT NULL AND approval_id IS NULL
                    AND tool_name IS NOT NULL
                    AND tool_name ~ '^[A-Za-z0-9_.-]{1,128}$')
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_event_order "
        "ON audit_event (occurred_at DESC, event_id DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_event_type_order "
        "ON audit_event (event_type, occurred_at DESC, event_id DESC)"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_audit_event_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_event is append-only' USING ERRCODE = '42501';
        END;
        $$
        """
    )
    op.execute(
        "CREATE OR REPLACE TRIGGER audit_event_append_only BEFORE UPDATE OR DELETE ON audit_event "
        "FOR EACH STATEMENT EXECUTE FUNCTION reject_audit_event_mutation()"
    )
    op.execute(
        "CREATE OR REPLACE TRIGGER audit_event_no_truncate BEFORE TRUNCATE ON audit_event "
        "FOR EACH STATEMENT EXECUTE FUNCTION reject_audit_event_mutation()"
    )


def downgrade() -> None:
    op.execute("DROP TABLE audit_event")
    op.execute("DROP FUNCTION reject_audit_event_mutation()")
