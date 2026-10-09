import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import pytest
from azure.core import MatchConditions
from azure.core.exceptions import ResourceNotFoundError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from accelerator.ingestion import IngestionService, IngestionStatus
from accelerator.ingestion.infrastructure.blob_store import (
    AzureBlobStore,
    AzureSourceReader,
    SourceTooLarge,
    blob_name,
)
from accelerator.ingestion.infrastructure.repository import PostgresIngestionRepository
from accelerator.ingestion.infrastructure.search_index import (
    AzureSearchChunkIndex,
    IndexWriteError,
)
from accelerator.ingestion.pipeline import IngestionMessage, build_job
from accelerator.retrieval_core.chunking.contracts import ChunkingConfig


class FakeContainer:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.replacements: dict[str, bytes] = {}

    async def upload_blob(self, name: str, data: bytes, *, overwrite: bool) -> None:
        assert overwrite
        self.blobs[name] = data

    async def delete_blob(self, name: str) -> None:
        if name not in self.blobs:
            raise ResourceNotFoundError("missing")
        del self.blobs[name]

    def get_blob_client(self, name: str) -> "FakeBlob":
        self.last_blob = FakeBlob(self.blobs[name])
        self.last_blob.replacement = self.replacements.get(name)
        return self.last_blob


class FakeBlob:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.downloaded = False
        self.download_kwargs: dict[str, Any] = {}
        self.replacement: bytes | None = None

    async def get_blob_properties(self) -> Any:
        return type("Properties", (), {"size": len(self.data), "etag": "etag-1"})()

    async def download_blob(self, **kwargs: Any) -> Any:
        self.downloaded = True
        self.download_kwargs = kwargs
        # Simulates a service that ignored the ETag condition and served a larger blob.
        data = self.replacement if self.replacement is not None else self.data

        class Downloader:
            async def chunks(self) -> AsyncIterator[bytes]:
                for start in range(0, len(data), 4):
                    yield data[start : start + 4]

        return Downloader()


async def test_blob_store_uses_flat_names_and_idempotent_deletes() -> None:
    container = FakeContainer()
    store = AzureBlobStore(container)  # type: ignore[arg-type]

    await store.put("../escape/doc", b"content")
    await store.delete("../escape/doc")
    await store.delete("../escape/doc")

    assert blob_name("../escape/doc") == "..%2Fescape%2Fdoc"
    assert container.blobs == {}


async def test_source_reader_refuses_oversized_blobs_before_download() -> None:
    container = FakeContainer()
    container.blobs["big"] = b"x" * 11
    reader = AzureSourceReader(container, max_bytes=10)  # type: ignore[arg-type]

    with pytest.raises(SourceTooLarge):
        await reader.read("big")
    assert not container.last_blob.downloaded


async def test_source_reader_binds_the_download_to_the_inspected_etag() -> None:
    container = FakeContainer()
    container.blobs["doc"] = b"0123456789"
    reader = AzureSourceReader(container, max_bytes=10)  # type: ignore[arg-type]

    assert await reader.read("doc") == b"0123456789"
    assert container.last_blob.download_kwargs == {
        "etag": "etag-1",
        "match_condition": MatchConditions.IfNotModified,
    }


async def test_source_reader_caps_a_blob_replaced_after_its_size_check() -> None:
    container = FakeContainer()
    container.blobs["doc"] = b"small"
    container.replacements["doc"] = b"x" * 1_000
    reader = AzureSourceReader(container, max_bytes=10)  # type: ignore[arg-type]

    with pytest.raises(SourceTooLarge):
        await reader.read("doc")


@dataclass
class WriteResult:
    key: str
    succeeded: bool = True


class FakeSearchClient:
    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}
        self.fail_keys: set[str] = set()

    async def merge_or_upload_documents(self, documents: list[dict[str, Any]]) -> list[WriteResult]:
        for document in documents:
            self.documents[document["chunk_id"]] = document
        return [WriteResult(d["chunk_id"], d["chunk_id"] not in self.fail_keys) for d in documents]

    async def delete_documents(self, keys: list[dict[str, str]]) -> list[WriteResult]:
        for key in keys:
            self.documents.pop(key["chunk_id"], None)
        return [WriteResult(key["chunk_id"]) for key in keys]

    async def search(self, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        document_id = kwargs["filter"].split("eq ")[1].strip("'")
        matches = [
            {"chunk_id": key}
            for key, doc in self.documents.items()
            if doc["document_id"] == document_id
        ]

        async def results() -> AsyncIterator[dict[str, Any]]:
            for match in matches:
                yield match

        return results()


class FakeEmbeddings:
    def __init__(self, dimensions: int = 3) -> None:
        self.dimensions = dimensions
        self.batches: list[int] = []

    async def get_embeddings(self, values: Sequence[str], *, options: Any = None) -> list[Any]:
        assert options == {"dimensions": 3}
        self.batches.append(len(values))
        return [type("E", (), {"vector": [0.1] * self.dimensions})() for _ in values]


def job(
    document_id: str = "doc-1",
    text: bytes = b"First part. " * 20,
    *,
    version: str = "1",
    content_type: str = "text/plain",
    chunking: ChunkingConfig = ChunkingConfig(size=50, overlap=5),
) -> Any:
    return build_job(
        IngestionMessage(
            operation="ingest",
            document_id=document_id,
            scope_id="scope-a",
            title="Doc",
            source_uri="https://d.test/doc",
            version=version,
            effective_date=date(2026, 1, 1),
            content_type=content_type,
            source_blob="incoming/doc",
        ),
        text,
        max_bytes=10_000,
        chunking=chunking,
    )


async def test_index_embeds_in_batches_and_writes_scoped_documents() -> None:
    client, embeddings = FakeSearchClient(), FakeEmbeddings()
    index = AzureSearchChunkIndex(client, embeddings, dimensions=3, batch_size=2)  # type: ignore[arg-type]
    chunks = job().chunks

    await index.upsert_chunks(chunks)

    assert sum(embeddings.batches) == len(chunks)
    assert max(embeddings.batches) == 2
    stored = client.documents[chunks[0].chunk_id]
    assert stored["scope_id"] == "scope-a"
    assert stored["embedding"] == [0.1, 0.1, 0.1]
    assert stored["effective_date"] == "2026-01-01T00:00:00+00:00"


async def test_index_rejects_wrong_dimensions_and_partial_write_failures() -> None:
    client = FakeSearchClient()
    with pytest.raises(IndexWriteError, match="dimensions"):
        await AzureSearchChunkIndex(
            client, FakeEmbeddings(dimensions=4), dimensions=3  # type: ignore[arg-type]
        ).upsert_chunks(job().chunks)

    chunks = job().chunks
    client.fail_keys = {chunks[0].chunk_id}
    with pytest.raises(IndexWriteError, match="1 index write"):
        await AzureSearchChunkIndex(client, FakeEmbeddings(), dimensions=3).upsert_chunks(chunks)  # type: ignore[arg-type]


async def test_index_deletes_every_chunk_of_a_document() -> None:
    client = FakeSearchClient()
    index = AzureSearchChunkIndex(client, FakeEmbeddings(), dimensions=3)  # type: ignore[arg-type]
    await index.upsert_chunks(job("doc-1").chunks)
    await index.upsert_chunks(job("doc-2").chunks)

    await index.delete_document("doc-1")

    assert {doc["document_id"] for doc in client.documents.values()} == {"doc-2"}


async def test_postgres_repository_round_trips_state_lineage_and_deletes(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    repository = PostgresIngestionRepository(async_sessionmaker(engine))
    container = FakeContainer()
    index = AzureSearchChunkIndex(FakeSearchClient(), FakeEmbeddings(), dimensions=3)  # type: ignore[arg-type]
    service = IngestionService(AzureBlobStore(container), index, repository)  # type: ignore[arg-type]
    try:
        first = job()
        await service.ingest(first)
        record = await repository.get_document("doc-1")
        assert record is not None
        assert record.status is IngestionStatus.READY
        assert record.scope_id == "scope-a"
        assert set(await repository.get_chunk_ids("doc-1")) == {c.chunk_id for c in first.chunks}
        assert (await service.ingest(first)).skipped

        shorter = job(text=b"Short.")
        await service.ingest(shorter)
        assert set(await repository.get_chunk_ids("doc-1")) == {c.chunk_id for c in shorter.chunks}

        await service.delete("doc-1")
        assert await repository.get_document("doc-1") is None
        assert await repository.get_chunk_ids("doc-1") == ()
        assert container.blobs == {}
    finally:
        await engine.dispose()


async def test_postgres_repository_serializes_concurrent_ingests_of_one_document(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    repository = PostgresIngestionRepository(async_sessionmaker(engine))
    container = FakeContainer()
    client = FakeSearchClient()
    index = AzureSearchChunkIndex(client, FakeEmbeddings(), dimensions=3)  # type: ignore[arg-type]
    service = IngestionService(AzureBlobStore(container), index, repository)  # type: ignore[arg-type]
    jobs = {v: job(text=f"Version {v} text. ".encode() * 10, version=v) for v in "123"}
    try:
        await asyncio.gather(*(service.ingest(item) for item in jobs.values()))

        record = await repository.get_document("doc-1")
        assert record is not None and record.version is not None
        expected = {chunk.chunk_id for chunk in jobs[record.version].chunks}
        assert record.status is IngestionStatus.READY
        assert set(await repository.get_chunk_ids("doc-1")) == expected
        assert set(client.documents) == expected
        assert container.blobs[blob_name("doc-1")] == jobs[record.version].source_content
    finally:
        await engine.dispose()


async def test_postgres_repository_stores_headings_longer_than_a_bounded_column(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    repository = PostgresIngestionRepository(async_sessionmaker(engine))
    index = AzureSearchChunkIndex(FakeSearchClient(), FakeEmbeddings(), dimensions=3)  # type: ignore[arg-type]
    service = IngestionService(AzureBlobStore(FakeContainer()), index, repository)  # type: ignore[arg-type]
    long_heading = "H" * 5_000
    try:
        ingested = job(
            text=f"# {long_heading}\nBody text.".encode(),
            content_type="text/markdown",
            chunking=ChunkingConfig(size=50, overlap=5, heading_aware=True),
        )
        assert ingested.chunks[0].section_heading == long_heading

        await service.ingest(ingested)

        record = await repository.get_document("doc-1")
        assert record is not None and record.status is IngestionStatus.READY
    finally:
        await engine.dispose()
