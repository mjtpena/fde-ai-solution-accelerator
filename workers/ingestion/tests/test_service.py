from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date
import hashlib
import unittest
from typing import Sequence

from accelerator.ingestion import (
    Chunk,
    DeletionError,
    DocumentRecord,
    IngestionJob,
    IngestionService,
    IngestionStatus,
)


@dataclass(frozen=True, slots=True)
class FakeDocument:
    document_id: str
    title: str
    source_uri: str
    scope_id: str = "scope-a"
    version: str | None = None
    effective_date: date | None = None


@dataclass(frozen=True, slots=True)
class FakeChunk:
    chunk_id: str
    document_id: str
    text: str
    version: str | None = "v1"
    scope_id: str = "scope-a"
    effective_date: date | None = None
    section_heading: str | None = None


class MemoryBlobStore:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.put_calls = 0
        self.delete_calls = 0
        self.delete_error: Exception | None = None

    async def put(self, document_id: str, content: bytes) -> None:
        self.put_calls += 1
        self.blobs[document_id] = content

    async def delete(self, document_id: str) -> None:
        self.delete_calls += 1
        if self.delete_error is not None:
            raise self.delete_error
        self.blobs.pop(document_id, None)


class MemoryChunkIndex:
    def __init__(self) -> None:
        self.chunks: dict[str, Chunk] = {}
        self.upsert_calls = 0
        self.deleted_chunk_ids: list[str] = []
        self.deleted_documents: list[str] = []
        self.upsert_error: Exception | None = None

    async def upsert_chunks(self, chunks: Sequence[Chunk]) -> None:
        self.upsert_calls += 1
        await asyncio.sleep(0)
        if self.upsert_error is not None:
            raise self.upsert_error
        for chunk in chunks:
            self.chunks[chunk.chunk_id] = chunk

    async def delete_chunks(self, document_id: str, chunk_ids: Sequence[str]) -> None:
        del document_id
        self.deleted_chunk_ids.extend(chunk_ids)
        for chunk_id in chunk_ids:
            self.chunks.pop(chunk_id, None)

    async def delete_document(self, document_id: str) -> None:
        self.deleted_documents.append(document_id)
        for chunk_id in tuple(self.chunks):
            if self.chunks[chunk_id].document_id == document_id:
                del self.chunks[chunk_id]


class MemoryRepository:
    def __init__(self) -> None:
        self.locks: dict[str, asyncio.Lock] = {}
        self.documents: dict[str, DocumentRecord] = {}
        self.chunks: dict[str, Chunk] = {}
        self.deleted_chunk_ids: list[str] = []
        self.deleted_documents: list[str] = []

    @asynccontextmanager
    async def lock_document(self, document_id: str) -> AsyncIterator[None]:
        async with self.locks.setdefault(document_id, asyncio.Lock()):
            yield

    async def get_document(self, document_id: str) -> DocumentRecord | None:
        await asyncio.sleep(0)  # yield like a real store so interleavings are possible
        return self.documents.get(document_id)

    async def get_chunk_ids(self, document_id: str) -> tuple[str, ...]:
        chunk_ids = tuple(
            chunk_id
            for chunk_id, chunk in self.chunks.items()
            if chunk.document_id == document_id
        )
        await asyncio.sleep(0)
        return chunk_ids

    async def upsert_document(self, document: DocumentRecord) -> None:
        self.documents[document.document_id] = document

    async def upsert_chunks(self, chunks: Sequence[Chunk]) -> None:
        for chunk in chunks:
            self.chunks[chunk.chunk_id] = chunk

    async def delete_chunks(self, document_id: str, chunk_ids: Sequence[str]) -> None:
        del document_id
        self.deleted_chunk_ids.extend(chunk_ids)
        for chunk_id in chunk_ids:
            self.chunks.pop(chunk_id, None)

    async def delete_document(self, document_id: str) -> None:
        self.deleted_documents.append(document_id)
        self.documents.pop(document_id, None)
        for chunk_id in tuple(self.chunks):
            if self.chunks[chunk_id].document_id == document_id:
                del self.chunks[chunk_id]


class IngestionServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.blobs = MemoryBlobStore()
        self.index = MemoryChunkIndex()
        self.repository = MemoryRepository()
        self.service = IngestionService(self.blobs, self.index, self.repository)

    @staticmethod
    def make_job(
        *,
        source_content: bytes = b"document contents",
        version: str | None = "v1",
        chunks: tuple[Chunk, ...] | None = None,
        scope_id: str = "scope-a",
    ) -> IngestionJob:
        return IngestionJob(
            document=FakeDocument(
                document_id="doc-1",
                scope_id=scope_id,
                title="Document",
                source_uri="blob://container/doc-1",
                version=version,
                effective_date=date(2026, 1, 1),
            ),
            source_content=source_content,
            chunks=chunks
            if chunks is not None
            else (
                FakeChunk(
                    chunk_id="chunk-1",
                    document_id="doc-1",
                    text="first chunk",
                    version=version,
                    scope_id=scope_id,
                ),
            ),
        )

    async def test_hash_deduplication_skips_unchanged_ready_document(self) -> None:
        job = self.make_job()
        first = await self.service.ingest(job)
        second = await self.service.ingest(job)

        self.assertEqual(first.content_hash, hashlib.sha256(job.source_content).hexdigest())
        self.assertFalse(first.skipped)
        self.assertTrue(second.skipped)
        self.assertEqual(self.blobs.put_calls, 1)
        self.assertEqual(self.index.upsert_calls, 1)
        self.assertEqual(self.repository.documents["doc-1"].status, IngestionStatus.READY)

    async def test_version_change_reindexes_even_when_content_hash_is_unchanged(self) -> None:
        await self.service.ingest(self.make_job())
        result = await self.service.ingest(self.make_job(version="v2"))

        self.assertFalse(result.skipped)
        self.assertTrue(result.reindexed)
        self.assertEqual(self.index.upsert_calls, 2)
        self.assertEqual(self.repository.documents["doc-1"].version, "v2")
        self.assertEqual(self.index.chunks["chunk-1"].version, "v2")

    async def test_scope_change_reindexes_unchanged_content(self) -> None:
        await self.service.ingest(self.make_job())
        result = await self.service.ingest(self.make_job(scope_id="scope-b"))

        self.assertFalse(result.skipped)
        self.assertEqual(self.index.upsert_calls, 2)
        self.assertEqual(self.repository.documents["doc-1"].scope_id, "scope-b")

    async def test_changed_chunk_set_upserts_by_id_and_removes_stale_chunks(self) -> None:
        await self.service.ingest(
            self.make_job(
                chunks=(
                    FakeChunk(chunk_id="keep", document_id="doc-1", text="old text"),
                    FakeChunk(chunk_id="stale", document_id="doc-1", text="removed text"),
                )
            )
        )
        await self.service.ingest(
            self.make_job(
                source_content=b"changed contents",
                chunks=(FakeChunk(chunk_id="keep", document_id="doc-1", text="new text"),),
            )
        )

        self.assertEqual(set(self.index.chunks), {"keep"})
        self.assertEqual(self.index.chunks["keep"].text, "new text")
        self.assertEqual(self.index.deleted_chunk_ids, ["stale"])
        self.assertEqual(self.repository.deleted_chunk_ids, ["stale"])

    async def test_delete_attempts_blob_index_and_database(self) -> None:
        await self.service.ingest(self.make_job())
        self.blobs.delete_error = RuntimeError("blob unavailable")

        with self.assertRaises(DeletionError) as raised:
            await self.service.delete("doc-1")

        self.assertEqual([store for store, _ in raised.exception.failures], ["blob"])
        self.assertEqual(self.blobs.delete_calls, 1)
        self.assertEqual(self.index.deleted_documents, ["doc-1"])
        self.assertEqual(self.repository.deleted_documents, ["doc-1"])
        self.assertNotIn("doc-1", self.repository.documents)
        self.assertFalse(self.index.chunks)

    async def test_index_failure_persists_a_reason_without_exception_details(self) -> None:
        self.index.upsert_error = RuntimeError("sensitive adapter detail")

        with self.assertRaisesRegex(RuntimeError, "sensitive adapter detail"):
            await self.service.ingest(self.make_job())

        failed_document = self.repository.documents["doc-1"]
        self.assertEqual(failed_document.status, IngestionStatus.FAILED)
        self.assertEqual(failed_document.failure_reason, "index_upsert failed (RuntimeError)")
        self.assertNotIn("sensitive", failed_document.failure_reason or "")

    async def test_chunks_outside_the_document_scope_are_rejected_before_any_write(self) -> None:
        job = self.make_job(
            chunks=(
                FakeChunk(
                    chunk_id="chunk-1", document_id="doc-1", text="x", scope_id="scope-b"
                ),
            )
        )

        with self.assertRaisesRegex(ValueError, "scope"):
            await self.service.ingest(job)

        self.assertEqual(self.repository.documents, {})
        self.assertEqual(self.blobs.put_calls, 0)
        self.assertEqual(self.index.upsert_calls, 0)

    async def test_concurrent_ingests_of_one_document_leave_one_consistent_version(self) -> None:
        def versioned(version: str) -> IngestionJob:
            return self.make_job(
                version=version,
                source_content=version.encode(),
                chunks=tuple(
                    FakeChunk(
                        chunk_id=f"{version}-{index}",
                        document_id="doc-1",
                        text=f"{version} text",
                        version=version,
                    )
                    for index in range(2)
                ),
            )

        await asyncio.gather(*(self.service.ingest(versioned(v)) for v in ("v1", "v2", "v3")))

        winner = self.repository.documents["doc-1"]
        expected = {f"{winner.version}-0", f"{winner.version}-1"}
        self.assertEqual(winner.status, IngestionStatus.READY)
        self.assertEqual(set(self.repository.chunks), expected)
        self.assertEqual(set(self.index.chunks), expected)
        self.assertEqual(self.blobs.blobs["doc-1"], (winner.version or "").encode())


if __name__ == "__main__":
    unittest.main()
