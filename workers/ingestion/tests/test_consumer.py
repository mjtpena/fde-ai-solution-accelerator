import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pytest

from accelerator.ingestion import DocumentRecord, IngestionStatus
from accelerator.ingestion.consumer import QueueConsumer, RetryPolicy
from accelerator.ingestion.handler import IngestionHandler
from accelerator.ingestion.pipeline import IngestionMessage, RejectedDocument
from accelerator.retrieval_core.chunking.contracts import ChunkingConfig


@dataclass
class Message:
    content: str
    dequeue_count: int = 1
    id: str = "m-1"
    pop_receipt: str = "receipt"


@dataclass
class FakeQueue:
    pending: list[Message] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    delayed: list[tuple[str, int]] = field(default_factory=list)
    sent: list[str] = field(default_factory=list)

    def receive_messages(self, **kwargs: Any) -> AsyncIterator[Message]:
        batch, self.pending = self.pending, []

        async def messages() -> AsyncIterator[Message]:
            for message in batch:
                yield message

        return messages()

    async def delete_message(self, message: Message, pop_receipt: str | None = None) -> None:
        assert pop_receipt == message.pop_receipt
        self.deleted.append(message.id)

    async def update_message(
        self, message: Message, pop_receipt: str | None = None, *, visibility_timeout: int | None = None
    ) -> None:
        assert visibility_timeout is not None
        self.delayed.append((message.id, visibility_timeout))

    async def send_message(self, content: Any) -> None:
        self.sent.append(content)


INGEST = json.dumps(
    {
        "operation": "ingest",
        "document_id": "doc-1",
        "scope_id": "scope-a",
        "title": "Doc",
        "content_type": "text/plain",
        "source_blob": "doc-1.txt",
    }
)


class Recorder:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.handled: list[IngestionMessage] = []
        self.failures: list[tuple[str, str]] = []

    async def handle(self, message: IngestionMessage) -> None:
        self.handled.append(message)
        if self.error is not None:
            raise self.error

    async def record_failure(self, message: IngestionMessage, reason: str) -> None:
        self.failures.append((message.document_id, reason))


def consumer(recorder: Recorder, queue: FakeQueue, poison: FakeQueue) -> QueueConsumer:
    return QueueConsumer(
        queue,
        poison,
        recorder.handle,
        recorder.record_failure,
        retry=RetryPolicy(max_attempts=3, base_delay_seconds=10, max_delay_seconds=25),
    )


async def test_handled_message_is_deleted() -> None:
    queue, poison, recorder = FakeQueue([Message(INGEST)]), FakeQueue(), Recorder()

    assert await consumer(recorder, queue, poison).drain_once() == 1

    assert queue.deleted == ["m-1"]
    assert recorder.handled[0].scope_id == "scope-a"
    assert poison.sent == []


async def test_transient_failures_back_off_exponentially_then_poison_with_failed_state() -> None:
    poison, recorder = FakeQueue(), Recorder(ConnectionError("search unavailable"))
    queue = FakeQueue([Message(INGEST, 1), Message(INGEST, 2, id="m-2"), Message(INGEST, 3, id="m-3")])

    await consumer(recorder, queue, poison).drain_once()

    assert queue.delayed == [("m-1", 10), ("m-2", 20)]
    assert queue.deleted == ["m-3"]
    assert recorder.failures == [("doc-1", "ConnectionError after 3 attempt(s)")]
    assert json.loads(poison.sent[0])["message_id"] == "m-3"


async def test_rejected_document_is_poisoned_immediately() -> None:
    queue, poison = FakeQueue([Message(INGEST)]), FakeQueue()
    recorder = Recorder(RejectedDocument("source document exceeds 10 bytes"))

    await consumer(recorder, queue, poison).drain_once()

    assert queue.delayed == []
    assert queue.deleted == ["m-1"]
    assert recorder.failures == [("doc-1", "rejected: source document exceeds 10 bytes")]
    assert len(poison.sent) == 1


@pytest.mark.parametrize("content", ["not json", json.dumps({"operation": "ingest"})])
async def test_invalid_messages_are_poisoned_without_touching_documents(content: str) -> None:
    queue, poison, recorder = FakeQueue([Message(content)]), FakeQueue(), Recorder()

    await consumer(recorder, queue, poison).drain_once()

    assert recorder.handled == []
    assert recorder.failures == []
    assert queue.deleted == ["m-1"]
    assert json.loads(poison.sent[0])["reason"] == "invalid message"


async def test_run_stops_promptly_when_idle() -> None:
    stop = asyncio.Event()
    task = asyncio.create_task(consumer(Recorder(), FakeQueue(), FakeQueue()).run(stop))
    await asyncio.sleep(0.01)
    stop.set()

    await asyncio.wait_for(task, timeout=1)


class FakeService:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.jobs: list[Any] = []

    async def delete(self, document_id: str) -> None:
        self.deleted.append(document_id)

    async def ingest(self, job: Any) -> None:
        self.jobs.append(job)


class FakeRepository:
    def __init__(self, existing: DocumentRecord | None = None) -> None:
        self.existing = existing
        self.saved: list[DocumentRecord] = []

    async def get_document(self, document_id: str) -> DocumentRecord | None:
        return self.existing

    async def upsert_document(self, document: DocumentRecord) -> None:
        self.saved.append(document)


class Sources:
    async def read(self, name: str) -> bytes:
        assert name == "doc-1.txt"
        return b"Some text worth indexing."


def handler(service: FakeService, repository: FakeRepository) -> IngestionHandler:
    return IngestionHandler(
        service,  # type: ignore[arg-type]
        repository,  # type: ignore[arg-type]
        Sources(),
        max_bytes=1000,
        chunking=ChunkingConfig(size=100, overlap=0),
    )


async def test_handler_ingests_from_the_source_blob_and_deletes_on_request() -> None:
    service = FakeService()
    run = handler(service, FakeRepository())

    await run(IngestionMessage.model_validate_json(INGEST))
    await run(IngestionMessage(operation="delete", document_id="doc-1", scope_id="scope-a"))

    assert service.jobs[0].document.scope_id == "scope-a"
    assert service.deleted == ["doc-1"]


async def test_failure_record_keeps_known_document_fields() -> None:
    existing = DocumentRecord(
        document_id="doc-1",
        scope_id="scope-a",
        title="Original",
        source_uri="https://d.test/1",
        content_hash="a" * 64,
        version="1",
        effective_date=date(2026, 1, 1),
        status=IngestionStatus.READY,
    )
    repository = FakeRepository(existing)

    await handler(FakeService(), repository).record_failure(
        IngestionMessage(operation="ingest", document_id="doc-1", scope_id="scope-a"), "poisoned"
    )

    [saved] = repository.saved
    assert saved.status is IngestionStatus.FAILED
    assert saved.failure_reason == "poisoned"
    assert saved.title == "Original"
    assert saved.content_hash == "a" * 64
