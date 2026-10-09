"""Idempotent ingestion orchestration behind storage-specific async ports."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Protocol


class DocumentInput(Protocol):
    @property
    def document_id(self) -> str: ...

    @property
    def scope_id(self) -> str:
        """Server-assigned authorization scope; every indexed chunk carries it."""
        ...

    @property
    def title(self) -> str: ...

    @property
    def source_uri(self) -> str: ...

    @property
    def version(self) -> str | None: ...

    @property
    def effective_date(self) -> date | None: ...


class Chunk(Protocol):
    @property
    def chunk_id(self) -> str: ...

    @property
    def document_id(self) -> str: ...

    @property
    def scope_id(self) -> str:
        """Must equal the document's server-assigned scope; checked before any write."""
        ...

    @property
    def text(self) -> str: ...

    @property
    def version(self) -> str | None: ...

    @property
    def effective_date(self) -> date | None: ...

    @property
    def section_heading(self) -> str | None: ...


class IngestionStatus(StrEnum):
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    # Local runs with INGESTION_SKIP_SEARCH_INDEXING: stored and chunked, not indexed.
    # Never treated as ``ready``, so a worker with indexing re-processes the document.
    INDEXING_SKIPPED = "indexing_skipped"


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    document_id: str
    scope_id: str
    title: str
    source_uri: str
    content_hash: str
    version: str | None
    effective_date: date | None
    status: IngestionStatus
    failure_reason: str | None = None


@dataclass(frozen=True, slots=True)
class IngestionJob:
    document: DocumentInput
    source_content: bytes
    chunks: Sequence[Chunk]


class BlobStore(Protocol):
    async def put(self, document_id: str, content: bytes) -> None: ...

    async def delete(self, document_id: str) -> None: ...


class ChunkIndex(Protocol):
    async def upsert_chunks(self, chunks: Sequence[Chunk]) -> None: ...

    async def delete_chunks(self, document_id: str, chunk_ids: Sequence[str]) -> None: ...

    async def delete_document(self, document_id: str) -> None: ...


class IngestionRepository(Protocol):
    def lock_document(self, document_id: str) -> AbstractAsyncContextManager[None]:
        """Serialize every ingest and delete of one document across workers."""
        ...

    async def get_document(self, document_id: str) -> DocumentRecord | None: ...

    async def get_chunk_ids(self, document_id: str) -> Sequence[str]: ...

    async def upsert_document(self, document: DocumentRecord) -> None: ...

    async def upsert_chunks(self, chunks: Sequence[Chunk]) -> None: ...

    async def delete_chunks(self, document_id: str, chunk_ids: Sequence[str]) -> None: ...

    async def delete_document(self, document_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class IngestionResult:
    content_hash: str
    skipped: bool
    reindexed: bool


class DeletionError(RuntimeError):
    def __init__(self, failures: tuple[tuple[str, Exception], ...]) -> None:
        self.failures = failures
        details = ", ".join(
            f"{store} ({type(error).__name__})" for store, error in failures
        )
        super().__init__(f"Document deletion failed in: {details}")


class IngestionService:
    def __init__(
        self,
        blob_store: BlobStore,
        chunk_index: ChunkIndex,
        repository: IngestionRepository,
        *,
        completed_status: IngestionStatus = IngestionStatus.READY,
    ) -> None:
        if completed_status not in (IngestionStatus.READY, IngestionStatus.INDEXING_SKIPPED):
            raise ValueError("completed_status must be ready or indexing_skipped")
        self._blob_store = blob_store
        self._chunk_index = chunk_index
        self._repository = repository
        # The state a successful run ends in, and the only state that dedupes a rerun.
        self._completed_status = completed_status

    async def ingest(self, job: IngestionJob) -> IngestionResult:
        async with self._repository.lock_document(job.document.document_id):
            return await self._ingest_locked(job)

    async def _ingest_locked(self, job: IngestionJob) -> IngestionResult:
        content_hash = hashlib.sha256(job.source_content).hexdigest()
        # An inconsistent job is rejected before any write, including the failed state.
        chunks = self._validate_and_version_chunks(job)
        stage = "document_lookup"
        try:
            existing = await self._repository.get_document(job.document.document_id)
            if (
                existing is not None
                and existing.status is self._completed_status
                and existing.content_hash == content_hash
                and existing.version == job.document.version
                and existing.scope_id == job.document.scope_id
            ):
                return IngestionResult(content_hash, skipped=True, reindexed=False)

            reindexed = existing is not None and existing.version != job.document.version
            stage = "chunk_lookup"
            previous_chunk_ids = await self._repository.get_chunk_ids(
                job.document.document_id
            )

            stage = "processing_state"
            await self._repository.upsert_document(
                self._record(job, content_hash, IngestionStatus.PROCESSING)
            )
            stage = "blob_write"
            await self._blob_store.put(job.document.document_id, job.source_content)
            stage = "database_chunk_upsert"
            await self._repository.upsert_chunks(chunks)
            stage = "index_upsert"
            await self._chunk_index.upsert_chunks(chunks)

            current_chunk_ids = {chunk.chunk_id for chunk in chunks}
            stale_chunk_ids = tuple(
                chunk_id for chunk_id in previous_chunk_ids if chunk_id not in current_chunk_ids
            )
            if stale_chunk_ids:
                stage = "stale_index_delete"
                await self._chunk_index.delete_chunks(
                    job.document.document_id, stale_chunk_ids
                )
                stage = "stale_database_delete"
                await self._repository.delete_chunks(
                    job.document.document_id, stale_chunk_ids
                )

            stage = "ready_state"
            await self._repository.upsert_document(
                self._record(job, content_hash, self._completed_status)
            )
            return IngestionResult(content_hash, skipped=False, reindexed=reindexed)
        except Exception as error:
            failure_reason = f"{stage} failed ({type(error).__name__})"
            try:
                await self._repository.upsert_document(
                    self._record(
                        job,
                        content_hash,
                        IngestionStatus.FAILED,
                        failure_reason=failure_reason,
                    )
                )
            except Exception as state_error:
                raise ExceptionGroup(  # noqa: B904 - the group carries both errors
                    "Ingestion failed and its failed state could not be persisted",
                    [error, state_error],
                )
            raise

    async def delete(self, document_id: str) -> None:
        async with self._repository.lock_document(document_id):
            await self._delete_locked(document_id)

    async def _delete_locked(self, document_id: str) -> None:
        failures: list[tuple[str, Exception]] = []
        for store, delete in (
            ("blob", self._blob_store.delete),
            ("index", self._chunk_index.delete_document),
            ("database", self._repository.delete_document),
        ):
            try:
                await delete(document_id)
            except Exception as error:
                failures.append((store, error))
        if failures:
            raise DeletionError(tuple(failures))

    @staticmethod
    def _validate_and_version_chunks(job: IngestionJob) -> tuple[Chunk, ...]:
        chunk_ids: set[str] = set()
        validated_chunks: list[Chunk] = []
        for chunk in job.chunks:
            if chunk.document_id != job.document.document_id:
                raise ValueError("All chunks must belong to the ingested document")
            if chunk.scope_id != job.document.scope_id:
                raise ValueError("Chunk scopes must match the document scope")
            if chunk.chunk_id in chunk_ids:
                raise ValueError("Chunk IDs must be unique within an ingestion job")
            if chunk.version != job.document.version:
                raise ValueError("Chunk versions must match the document version")
            chunk_ids.add(chunk.chunk_id)
            validated_chunks.append(chunk)
        return tuple(validated_chunks)

    @staticmethod
    def _record(
        job: IngestionJob,
        content_hash: str,
        status: IngestionStatus,
        failure_reason: str | None = None,
    ) -> DocumentRecord:
        return DocumentRecord(
            document_id=job.document.document_id,
            scope_id=job.document.scope_id,
            title=job.document.title,
            source_uri=job.document.source_uri,
            content_hash=content_hash,
            version=job.document.version,
            effective_date=job.document.effective_date,
            status=status,
            failure_reason=failure_reason,
        )
