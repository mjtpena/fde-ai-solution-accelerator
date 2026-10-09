"""Handle one validated ingestion message with ``IngestionService``."""

from typing import Protocol

from accelerator.retrieval_core.chunking.contracts import ChunkingConfig

from .pipeline import IngestionMessage, RejectedDocument, build_job
from .service import DocumentRecord, IngestionRepository, IngestionService, IngestionStatus


class SourceReader(Protocol):
    async def read(self, name: str) -> bytes: ...


class IngestionHandler:
    def __init__(
        self,
        service: IngestionService,
        repository: IngestionRepository,
        sources: SourceReader,
        *,
        max_bytes: int,
        chunking: ChunkingConfig,
    ) -> None:
        self._service = service
        self._repository = repository
        self._sources = sources
        self._max_bytes = max_bytes
        self._chunking = chunking

    async def __call__(self, message: IngestionMessage) -> None:
        if message.operation == "delete":
            await self._service.delete(message.document_id)
            return
        if message.source_blob is None:
            raise RejectedDocument("ingest messages need a source_blob")
        content = await self._sources.read(message.source_blob)
        job = build_job(message, content, max_bytes=self._max_bytes, chunking=self._chunking)
        await self._service.ingest(job)

    async def record_failure(self, message: IngestionMessage, reason: str) -> None:
        """Persist ``failed(reason)`` so the document's final state is visible."""
        existing = await self._repository.get_document(message.document_id)
        await self._repository.upsert_document(
            DocumentRecord(
                document_id=message.document_id,
                scope_id=message.scope_id,
                title=message.title or (existing.title if existing else message.document_id),
                source_uri=message.source_uri or (existing.source_uri if existing else ""),
                content_hash=existing.content_hash if existing else "",
                version=message.version,
                effective_date=message.effective_date,
                status=IngestionStatus.FAILED,
                failure_reason=reason,
            )
        )
