"""Ingestion state and chunk lineage (no document bodies).

Revision ID: 0005_ingestion
Revises: 0004_cost_controls
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_ingestion"
down_revision: str | None = "0004_cost_controls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("document_id", sa.String(255), primary_key=True),
        sa.Column("scope_id", sa.String(255), nullable=False),
        sa.Column("title", sa.String(1024), nullable=False),
        sa.Column("source_uri", sa.String(2048), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("version", sa.String(255), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("failure_reason", sa.String(512), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_documents_scope_status", "documents", ["scope_id", "status"])
    op.create_table(
        "document_chunks",
        sa.Column("chunk_id", sa.String(64), primary_key=True),
        sa.Column(
            "document_id",
            sa.String(255),
            sa.ForeignKey("documents.document_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.String(255), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("section_heading", sa.String(1024), nullable=True),
    )
    op.create_index("ix_document_chunks_document", "document_chunks", ["document_id"])


def downgrade() -> None:
    op.drop_table("document_chunks")
    op.drop_table("documents")
