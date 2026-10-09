"""Two real ingestion worker processes against Azurite and PostgreSQL.

The workers run ``python -m accelerator.ingestion`` exactly as the container
image does. Documents are uploaded to the Azurite incoming container and queue
messages are sent in the documented contract (workers/ingestion/README.md). The
only Azure-dependent stage, embedding and Azure AI Search writes, is turned off
with ``INGESTION_SKIP_SEARCH_INDEXING``; successful documents therefore end as
``indexing_skipped``. Everything else - queue handling, the allow-list, parsing,
chunking, the canonical blob, lineage, dedupe, retries and poisoning - is real.
"""

import hashlib
import json
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob.aio import BlobServiceClient
from azure.storage.queue.aio import QueueClient
from e2e_local_harness import (
    FIXTURES,
    WorkerProcess,
    cyclic_page_tree_pdf,
    decompression_bomb_pdf,
    deeply_nested_pdf,
    eventually,
    migrated_database,
    text_pdf,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from accelerator.ingestion.infrastructure.blob_store import blob_name
from accelerator.ingestion.pipeline import IngestionMessage, build_job, chunk_id_for
from accelerator.retrieval_core.chunking.contracts import ChunkingConfig

MAX_BYTES = 256 * 1024
CHUNKING = ChunkingConfig(size=400, overlap=50, heading_aware=True)
SCOPE = "scope-documents"
WAIT = 120.0  # an idle worker polls with backoff of up to 30 s


@dataclass(frozen=True)
class Pipeline:
    connection_string: str
    database_url: str
    queue: str
    poison: str
    incoming: str
    documents: str
    workers: tuple[WorkerProcess, ...]

    def worker_log(self) -> str:
        return "\n".join(worker.log() for worker in self.workers)


@pytest.fixture(scope="module")
def pipeline(
    server_dsn: str, azurite_connection_string: str, process_logs: Path
) -> Iterator[Pipeline]:
    suffix = uuid4().hex[:10]
    names = {
        "queue": f"e2e-ingest-{suffix}",
        "poison": f"e2e-poison-{suffix}",
        "incoming": f"e2e-incoming-{suffix}",
        "documents": f"e2e-documents-{suffix}",
    }
    with migrated_database(server_dsn, process_logs) as database_url:
        settings = {
            "INGESTION_ENVIRONMENT": "test",
            "INGESTION_LOG_LEVEL": "INFO",
            "INGESTION_STORAGE_CONNECTION_STRING": azurite_connection_string,
            "INGESTION_DATABASE_URL": database_url,
            "INGESTION_DATABASE_AUTH_MODE": "password",
            "INGESTION_SKIP_SEARCH_INDEXING": "true",
            "INGESTION_QUEUE_NAME": names["queue"],
            "INGESTION_POISON_QUEUE_NAME": names["poison"],
            "INGESTION_INCOMING_CONTAINER": names["incoming"],
            "INGESTION_DOCUMENTS_CONTAINER": names["documents"],
            "INGESTION_MAX_DOCUMENT_BYTES": str(MAX_BYTES),
            "INGESTION_CHUNK_SIZE": str(CHUNKING.size),
            "INGESTION_CHUNK_OVERLAP": str(CHUNKING.overlap),
            "INGESTION_MAX_ATTEMPTS": "2",
            "INGESTION_VISIBILITY_TIMEOUT_SECONDS": "30",
        }
        workers = tuple(
            WorkerProcess(f"ingestion-worker-{index}", settings, process_logs)
            for index in (1, 2)
        )
        for worker in workers:
            worker.start_and_wait()
        try:
            yield Pipeline(
                azurite_connection_string,
                database_url,
                workers=workers,
                **names,
            )
        finally:
            codes = [worker.stop() for worker in workers]
            logs = "\n".join(worker.log() for worker in workers)
            assert codes == [0, 0], logs[-4000:]
            assert logs.count("worker_stopped") == 2, logs[-4000:]


@pytest.fixture
async def database(pipeline: Pipeline) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(pipeline.database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def storage(pipeline: Pipeline) -> AsyncIterator["Storage"]:
    blobs = BlobServiceClient.from_connection_string(pipeline.connection_string)
    queue = QueueClient.from_connection_string(pipeline.connection_string, pipeline.queue)
    poison = QueueClient.from_connection_string(pipeline.connection_string, pipeline.poison)
    async with blobs, queue, poison:
        yield Storage(pipeline, blobs, queue, poison)


@dataclass
class Storage:
    pipeline: Pipeline
    blobs: BlobServiceClient
    queue: QueueClient
    poison: QueueClient

    async def upload(self, name: str, content: bytes) -> None:
        container = self.blobs.get_container_client(self.pipeline.incoming)
        await container.upload_blob(name, content, overwrite=True)

    async def enqueue(self, message: IngestionMessage | str) -> None:
        body = message if isinstance(message, str) else message.model_dump_json()
        await self.queue.send_message(body)

    async def canonical(self, document_id: str) -> bytes | None:
        blob = self.blobs.get_blob_client(self.pipeline.documents, blob_name(document_id))
        try:
            return await (await blob.download_blob()).readall()
        except ResourceNotFoundError:
            return None

    async def drained(self) -> bool | None:
        properties = await self.queue.get_queue_properties()
        return True if properties.approximate_message_count == 0 else None

    async def poisoned(self) -> list[dict[str, Any]]:
        """Poison-queue wrappers, peeked so they stay for later assertions."""
        return [
            json.loads(message.content)
            for message in await self.poison.peek_messages(max_messages=32)
        ]


def ingest(document_id: str, blob: str, content_type: str, **fields: Any) -> IngestionMessage:
    return IngestionMessage(
        operation="ingest",
        document_id=document_id,
        scope_id=SCOPE,
        title=fields.pop("title", document_id),
        source_uri=fields.pop("source_uri", f"https://documents.example/{document_id}"),
        content_type=content_type,  # type: ignore[arg-type]  # validated by the model
        source_blob=blob,
        **fields,
    )


async def document(engine: AsyncEngine, document_id: str) -> dict[str, Any] | None:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT * FROM documents WHERE document_id = :id"), {"id": document_id}
            )
        ).mappings().one_or_none()
    return dict(row) if row is not None else None


async def chunks(engine: AsyncEngine, document_id: str) -> list[dict[str, Any]]:
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT chunk_id, version, effective_date, section_heading "
                    "FROM document_chunks WHERE document_id = :id"
                ),
                {"id": document_id},
            )
        ).mappings()
        return [dict(row) for row in rows]


async def settled(engine: AsyncEngine, document_id: str, status: str) -> dict[str, Any]:
    async def check() -> dict[str, Any] | None:
        row = await document(engine, document_id)
        return row if row is not None and row["status"] == status else None

    return await eventually(f"{document_id} to be {status}", check, timeout=WAIT)


def sample_pdf() -> bytes:
    return text_pdf(
        [
            [
                "Facilities guide",
                "Meeting rooms are booked through the shared calendar.",
                "A booking longer than four hours needs the facilities team's approval.",
                "Rooms left unused for fifteen minutes are released automatically.",
                "Projectors and screens are checked every Monday morning.",
            ],
            [
                "Visitors",
                "Visitors sign in at reception and wear a badge at all times.",
                "Hosts collect their visitors from reception and escort them out.",
                "Badges are returned at the end of the visit.",
            ],
        ]
    )


DOCUMENTS = {
    "handbook/workspace.md": ("workspace-handbook.md", "text/markdown"),
    "notes/coordination.txt": ("meeting-notes.txt", "text/plain"),
    "guides/facilities.pdf": ("facilities.pdf", "application/pdf"),
}


def source(blob: str) -> bytes:
    return sample_pdf() if blob.endswith(".pdf") else (FIXTURES / blob).read_bytes()


async def test_every_supported_type_is_parsed_chunked_stored_and_recorded(
    pipeline: Pipeline, storage: Storage, database: AsyncEngine
) -> None:
    messages = {}
    for document_id, (blob, content_type) in DOCUMENTS.items():
        await storage.upload(blob, source(blob))
        messages[document_id] = ingest(
            document_id, blob, content_type, version="1", effective_date=date(2026, 1, 1)
        )
        await storage.enqueue(messages[document_id])

    for document_id, (blob, _) in DOCUMENTS.items():
        content = source(blob)
        row = await settled(database, document_id, "indexing_skipped")
        assert row["content_hash"] == hashlib.sha256(content).hexdigest()
        assert row["scope_id"] == SCOPE
        assert row["version"] == "1"
        assert row["effective_date"] == date(2026, 1, 1)
        assert row["failure_reason"] is None
        assert await storage.canonical(document_id) == content

        # The worker's lineage equals the deterministic pipeline on the same bytes.
        expected = build_job(messages[document_id], content, max_bytes=MAX_BYTES, chunking=CHUNKING)
        stored = await chunks(database, document_id)
        assert sorted(chunk["chunk_id"] for chunk in stored) == sorted(
            chunk_id_for(document_id, "1", index) for index in range(len(expected.chunks))
        )
        assert len(stored) >= 2, document_id
        assert all(chunk["version"] == "1" for chunk in stored)

    handbook = await chunks(database, "handbook/workspace.md")
    assert {chunk["section_heading"] for chunk in handbook} == {
        "Workspace handbook",
        "Requesting access",
        "Storing documents",
        "Retention",
        "Getting help",
    }  # the "# ..." line inside the fenced block is not a heading
    notes = await chunks(database, "notes/coordination.txt")
    assert {chunk["section_heading"] for chunk in notes} == {None}

    await eventually("the work queue to drain", storage.drained, timeout=WAIT)
    assert await storage.poisoned() == []
    assert "search_indexing_skipped" in pipeline.worker_log()


async def test_redelivery_is_idempotent_and_unchanged_content_is_deduplicated(
    pipeline: Pipeline, storage: Storage, database: AsyncEngine
) -> None:
    document_id = "handbook/workspace.md"
    message = ingest(
        document_id,
        "workspace-handbook.md",
        "text/markdown",
        version="1",
        effective_date=date(2026, 1, 1),
    )
    before = await settled(database, document_id, "indexing_skipped")
    lineage = sorted(chunk["chunk_id"] for chunk in await chunks(database, document_id))

    # At-least-once delivery: the same message arrives several times, and the two
    # workers can pick copies up at the same moment.
    for _ in range(4):
        await storage.enqueue(message)
    await eventually("redelivered copies to be consumed", storage.drained, timeout=WAIT)

    after = await document(database, document_id)
    assert after == before  # same hash and version: skipped, not even re-marked
    assert sorted(chunk["chunk_id"] for chunk in await chunks(database, document_id)) == lineage
    assert await storage.poisoned() == []


async def test_a_new_version_replaces_the_lineage_and_drops_stale_chunks(
    storage: Storage, database: AsyncEngine
) -> None:
    document_id = "handbook/workspace.md"
    old = {chunk["chunk_id"] for chunk in await chunks(database, document_id)}
    revised = b"# Workspace handbook\n\nThe handbook moved to the intranet. Ask an owner.\n"
    await storage.upload("workspace-handbook-v2.md", revised)
    await storage.enqueue(
        ingest(document_id, "workspace-handbook-v2.md", "text/markdown", version="2")
    )

    async def replaced() -> dict[str, Any] | None:
        row = await document(database, document_id)
        return row if row is not None and row["version"] == "2" else None

    row = await eventually("version 2", replaced, timeout=WAIT)
    current = await chunks(database, document_id)

    assert row["status"] == "indexing_skipped"
    assert row["content_hash"] == hashlib.sha256(revised).hexdigest()
    assert [chunk["chunk_id"] for chunk in current] == [chunk_id_for(document_id, "2", 0)]
    assert old.isdisjoint(chunk["chunk_id"] for chunk in current)
    assert await storage.canonical(document_id) == revised


def hostile_uploads() -> dict[str, tuple[str, str, bytes, str]]:
    """Document id: (blob name, content type, content, expected failure reason)."""
    return {
        "hostile/oversized.txt": (
            "oversized.txt",
            "text/plain",
            b"0123456789abcdef" * (MAX_BYTES // 16 + 1),
            f"rejected: source blob exceeds {MAX_BYTES} bytes",
        ),
        "hostile/executable-as-pdf.pdf": (
            "executable-as-pdf.pdf",
            "application/pdf",
            b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff" + bytes(range(256)) * 4,
            "rejected: source document could not be parsed (PdfStreamError)",
        ),
        "hostile/binary-as-text.txt": (
            "binary-as-text.txt",
            "text/plain",
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(range(128, 256)),
            "rejected: source document could not be parsed (UnicodeDecodeError)",
        ),
        "hostile/decompression-bomb.pdf": (
            "decompression-bomb.pdf",
            "application/pdf",
            decompression_bomb_pdf(),
            "rejected: source document could not be parsed (LimitReachedError)",
        ),
        "hostile/cyclic-pages.pdf": (
            "cyclic-pages.pdf",
            "application/pdf",
            cyclic_page_tree_pdf(),
            "rejected: source document could not be parsed (PdfReadError)",
        ),
        "hostile/deeply-nested.pdf": (
            "deeply-nested.pdf",
            "application/pdf",
            deeply_nested_pdf(),
            "rejected: source document could not be parsed (PdfReadError)",
        ),
        "hostile/empty.md": (
            "empty.md",
            "text/markdown",
            b"",
            "rejected: source document is empty",
        ),
        "hostile/blank.txt": (
            "blank.txt",
            "text/plain",
            b" \n\t \n",
            "rejected: source document has no extractable text",
        ),
    }


async def test_hostile_uploads_are_poisoned_at_once_with_a_failed_state(
    pipeline: Pipeline, storage: Storage, database: AsyncEngine
) -> None:
    hostile = hostile_uploads()
    for document_id, (blob, content_type, content, _) in hostile.items():
        assert len(content) <= MAX_BYTES or document_id == "hostile/oversized.txt"
        await storage.upload(blob, content)
        await storage.enqueue(ingest(document_id, blob, content_type))
    unsupported = json.dumps(
        {
            "operation": "ingest",
            "document_id": "hostile/report.docx",
            "scope_id": SCOPE,
            "content_type": "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document",
            "source_blob": "report.docx",
        }
    )
    widened = json.dumps(
        {
            "operation": "ingest",
            "document_id": "hostile/extra-field.md",
            "scope_id": SCOPE,
            "content_type": "text/markdown",
            "source_blob": "empty.md",
            "scope_ids": ["every-scope"],
        }
    )
    for invalid in (unsupported, widened, "not json at all"):
        await storage.enqueue(invalid)

    for document_id, (_, _, _, reason) in hostile.items():
        row = await settled(database, document_id, "failed")
        assert row["failure_reason"] == reason, document_id
        assert await chunks(database, document_id) == []
        assert await storage.canonical(document_id) is None
    await eventually("the work queue to drain", storage.drained, timeout=WAIT)

    poisoned = await storage.poisoned()
    reasons = sorted(entry["reason"] for entry in poisoned)
    assert reasons == sorted(
        [reason for _, _, _, reason in hostile.values()] + ["invalid message"] * 3
    )
    # Invalid messages never create document state; permanent rejections are not retried.
    for document_id in ("hostile/report.docx", "hostile/extra-field.md"):
        assert await document(database, document_id) is None
    assert "ingestion_retry_scheduled" not in pipeline.worker_log()
    for worker in pipeline.workers:
        assert worker.running  # nothing above crashed a worker


async def test_traversal_style_document_ids_stay_flat_blob_names(
    storage: Storage, database: AsyncEngine
) -> None:
    document_id = "../../outside/escape.md"
    content = b"# Escape attempt\n\nThis document id tries to leave its container path.\n"
    await storage.upload("escape.md", content)
    await storage.enqueue(ingest(document_id, "escape.md", "text/markdown"))

    await settled(database, document_id, "indexing_skipped")
    container = storage.blobs.get_container_client(storage.pipeline.documents)
    names = [blob.name async for blob in container.list_blobs()]

    assert "..%2F..%2Foutside%2Fescape.md" in names
    assert not any("/" in name for name in names)


async def test_transient_failures_back_off_then_recover_or_poison(
    pipeline: Pipeline, storage: Storage, database: AsyncEngine
) -> None:
    content = b"# Late arrival\n\nThe upload finished after the message was queued.\n"
    retries_before = pipeline.worker_log().count("ingestion_retry_scheduled")
    await storage.enqueue(ingest("transient/late.md", "late.md", "text/markdown"))
    await storage.enqueue(ingest("transient/never.md", "never.md", "text/markdown"))

    async def both_retried() -> bool | None:
        count = pipeline.worker_log().count("ingestion_retry_scheduled") - retries_before
        return True if count >= 2 else None

    await eventually("first attempts to fail", both_retried, timeout=WAIT, interval=0.2)
    first_failure = time.monotonic()
    await storage.upload("late.md", content)  # the blob appears during the backoff

    recovered = await settled(database, "transient/late.md", "indexing_skipped")
    recovered_after = time.monotonic() - first_failure
    poisoned = await settled(database, "transient/never.md", "failed")

    assert recovered["content_hash"] == hashlib.sha256(content).hexdigest()
    assert recovered_after >= 13  # redelivered only after the 15 s first backoff
    assert poisoned["failure_reason"] == "ResourceNotFoundError after 2 attempt(s)"
    await eventually("the work queue to drain", storage.drained, timeout=WAIT)
    reasons = [entry["reason"] for entry in await storage.poisoned()]
    assert "ResourceNotFoundError after 2 attempt(s)" in reasons


async def test_delete_removes_the_blob_and_every_database_row(
    storage: Storage, database: AsyncEngine
) -> None:
    document_id = "notes/coordination.txt"
    assert await document(database, document_id) is not None

    await storage.enqueue(
        IngestionMessage(operation="delete", document_id=document_id, scope_id=SCOPE)
    )

    async def deleted() -> bool | None:
        return True if await document(database, document_id) is None else None

    await eventually("the deletion", deleted, timeout=WAIT)
    assert await chunks(database, document_id) == []
    assert await storage.canonical(document_id) is None
