"""PostgreSQL ``IngestionRepository``: document state and chunk lineage, no bodies."""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    MetaData,
    String,
    Table,
    Text,
    delete,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..service import Chunk, DocumentRecord, IngestionStatus

metadata = MetaData()

documents = Table(
    "documents",
    metadata,
    Column("document_id", String(255), primary_key=True),
    Column("scope_id", String(255), nullable=False),
    Column("title", String(1024), nullable=False),
    Column("source_uri", String(2048), nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("version", String(255)),
    Column("effective_date", Date),
    Column("status", String(16), nullable=False),
    Column("failure_reason", String(512)),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Index("ix_documents_scope_status", "scope_id", "status"),
)

document_chunks = Table(
    "document_chunks",
    metadata,
    Column("chunk_id", String(64), primary_key=True),
    Column(
        "document_id",
        String(255),
        ForeignKey("documents.document_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("version", String(255)),
    Column("effective_date", Date),
    Column("section_heading", Text),
    Index("ix_document_chunks_document", "document_id"),
)


class PostgresIngestionRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    @asynccontextmanager
    async def lock_document(self, document_id: str) -> AsyncIterator[None]:
        """Hold a transaction-scoped advisory lock on the document for the whole workflow.

        The lock lives on its own connection, so it is released on commit, rollback or
        a dropped connection, and two workers can never interleave one document.
        """
        key = func.hashtextextended("ingestion.document:" + document_id, 0)
        async with self._sessions() as session, session.begin():
            await session.execute(select(func.pg_advisory_xact_lock(key)))
            yield

    async def get_document(self, document_id: str) -> DocumentRecord | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(documents).where(documents.c.document_id == document_id)
                )
            ).mappings().one_or_none()
        if row is None:
            return None
        return DocumentRecord(
            document_id=row["document_id"],
            scope_id=row["scope_id"],
            title=row["title"],
            source_uri=row["source_uri"],
            content_hash=row["content_hash"],
            version=row["version"],
            effective_date=row["effective_date"],
            status=IngestionStatus(row["status"]),
            failure_reason=row["failure_reason"],
        )

    async def get_chunk_ids(self, document_id: str) -> Sequence[str]:
        async with self._sessions() as session:
            return tuple(
                await session.scalars(
                    select(document_chunks.c.chunk_id)
                    .where(document_chunks.c.document_id == document_id)
                    .order_by(document_chunks.c.chunk_id)
                )
            )

    async def upsert_document(self, document: DocumentRecord) -> None:
        values: dict[str, Any] = {
            "document_id": document.document_id,
            "scope_id": document.scope_id,
            "title": document.title,
            "source_uri": document.source_uri,
            "content_hash": document.content_hash,
            "version": document.version,
            "effective_date": document.effective_date,
            "status": document.status.value,
            "failure_reason": document.failure_reason,
            "updated_at": datetime.now(UTC),
        }
        statement = insert(documents).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=["document_id"],
            set_={key: statement.excluded[key] for key in values if key != "document_id"},
        )
        async with self._sessions() as session, session.begin():
            await session.execute(statement)

    async def upsert_chunks(self, chunks: Sequence[Chunk]) -> None:
        if not chunks:
            return
        rows: list[dict[str, str | date | None]] = [
            {
                "chunk_id": chunk.chunk_id,
                "document_id": chunk.document_id,
                "version": chunk.version,
                "effective_date": chunk.effective_date,
                "section_heading": chunk.section_heading,
            }
            for chunk in chunks
        ]
        statement = insert(document_chunks).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=["chunk_id"],
            set_={
                key: statement.excluded[key]
                for key in ("document_id", "version", "effective_date", "section_heading")
            },
        )
        async with self._sessions() as session, session.begin():
            await session.execute(statement)

    async def delete_chunks(self, document_id: str, chunk_ids: Sequence[str]) -> None:
        async with self._sessions() as session, session.begin():
            await session.execute(
                delete(document_chunks).where(
                    document_chunks.c.document_id == document_id,
                    document_chunks.c.chunk_id.in_(list(chunk_ids)),
                )
            )

    async def delete_document(self, document_id: str) -> None:
        async with self._sessions() as session, session.begin():
            await session.execute(delete(documents).where(documents.c.document_id == document_id))
