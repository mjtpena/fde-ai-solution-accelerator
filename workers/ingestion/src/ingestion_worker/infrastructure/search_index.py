"""Azure AI Search ``ChunkIndex``: embed chunks and upsert them by ``chunk_id``."""

import math
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any, Protocol

from azure.search.documents.aio import SearchClient


class IndexableChunk(Protocol):
    chunk_id: str
    document_id: str
    scope_id: str
    document_title: str
    source_uri: str
    content_hash: str
    text: str
    version: str | None
    effective_date: date | None
    section_heading: str | None


class EmbeddingVector(Protocol):
    @property
    def vector(self) -> list[float]: ...


class EmbeddingClient(Protocol):
    """The subset of ``agent_framework.foundry.FoundryEmbeddingClient`` used here."""

    async def get_embeddings(
        self, values: Sequence[str], *, options: Any = None
    ) -> Sequence[EmbeddingVector]: ...


class IndexWriteError(RuntimeError):
    pass


def _odata_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _offset(value: date | None) -> str | None:
    if value is None:
        return None
    return datetime(value.year, value.month, value.day, tzinfo=UTC).isoformat()


class AzureSearchChunkIndex:
    def __init__(
        self,
        client: SearchClient,
        embeddings: EmbeddingClient,
        *,
        dimensions: int,
        batch_size: int = 16,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self._client = client
        self._embeddings = embeddings
        self._dimensions = dimensions
        self._batch_size = batch_size

    async def _embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = await self._embeddings.get_embeddings(
            list(texts), options={"dimensions": self._dimensions}
        )
        if len(vectors) != len(texts):
            raise IndexWriteError("embedding service returned an unexpected number of vectors")
        result = [list(item.vector) for item in vectors]
        for vector in result:
            if len(vector) != self._dimensions or not all(math.isfinite(v) for v in vector):
                raise IndexWriteError("embedding does not match the index dimensions")
        return result

    async def upsert_chunks(self, chunks: Sequence[Any]) -> None:
        indexable: Sequence[IndexableChunk] = chunks
        for start in range(0, len(indexable), self._batch_size):
            batch = indexable[start : start + self._batch_size]
            vectors = await self._embed([chunk.text for chunk in batch])
            documents = [
                {
                    "chunk_id": chunk.chunk_id,
                    "document_id": chunk.document_id,
                    "scope_id": chunk.scope_id,
                    "document_title": chunk.document_title,
                    "source_uri": chunk.source_uri,
                    "content_hash": chunk.content_hash,
                    "version": chunk.version,
                    "effective_date": _offset(chunk.effective_date),
                    "section_heading": chunk.section_heading,
                    "text": chunk.text,
                    "embedding": vector,
                }
                for chunk, vector in zip(batch, vectors, strict=True)
            ]
            await self._write(await self._client.merge_or_upload_documents(documents))

    async def delete_chunks(self, document_id: str, chunk_ids: Sequence[str]) -> None:
        del document_id
        for start in range(0, len(chunk_ids), 1000):
            keys = [{"chunk_id": chunk_id} for chunk_id in chunk_ids[start : start + 1000]]
            await self._write(await self._client.delete_documents(keys))

    async def delete_document(self, document_id: str) -> None:
        results = await self._client.search(
            search_text="*",
            filter=f"document_id eq {_odata_literal(document_id)}",
            select=["chunk_id"],
        )
        chunk_ids = [item["chunk_id"] async for item in results]
        if chunk_ids:
            await self.delete_chunks(document_id, chunk_ids)

    @staticmethod
    async def _write(results: Sequence[Any]) -> None:
        failed = [result.key for result in results if not result.succeeded]
        if failed:
            raise IndexWriteError(f"{len(failed)} index write(s) failed")
