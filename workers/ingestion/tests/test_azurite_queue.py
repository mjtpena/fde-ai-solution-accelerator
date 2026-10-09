"""Queue and blob behaviour against the Azurite emulator (opt-in).

Set TEST_AZURITE_CONNECTION_STRING (for example ``UseDevelopmentStorage=true``
with Azurite on its default ports; CI starts one). Each test uses its own queues
and container.
"""

import asyncio
import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import uuid4

import pytest
from azure.storage.blob.aio import BlobServiceClient
from azure.storage.queue.aio import QueueClient

from accelerator.ingestion import DocumentRecord
from accelerator.ingestion.consumer import QueueConsumer, RetryPolicy
from accelerator.ingestion.handler import IngestionHandler
from accelerator.ingestion.infrastructure.blob_store import AzureSourceReader
from accelerator.ingestion.pipeline import IngestionMessage
from accelerator.retrieval_core.chunking.contracts import ChunkingConfig

CONNECTION = os.environ.get("TEST_AZURITE_CONNECTION_STRING")
pytestmark = pytest.mark.skipif(
    not CONNECTION, reason="Set TEST_AZURITE_CONNECTION_STRING to run Azurite tests."
)


@dataclass
class Storage:
    queue: QueueClient
    poison: QueueClient
    incoming: AzureSourceReader
    blobs: BlobServiceClient
    container: str


@pytest.fixture
async def storage() -> AsyncIterator[Storage]:
    assert CONNECTION is not None
    suffix = uuid4().hex[:12]
    queue = QueueClient.from_connection_string(CONNECTION, f"ingest-{suffix}")
    poison = QueueClient.from_connection_string(CONNECTION, f"poison-{suffix}")
    blobs = BlobServiceClient.from_connection_string(CONNECTION)
    container = f"incoming-{suffix}"
    async with queue, poison, blobs:
        await queue.create_queue()
        await poison.create_queue()
        await blobs.create_container(container)
        try:
            yield Storage(
                queue,
                poison,
                AzureSourceReader(blobs.get_container_client(container), max_bytes=1000),
                blobs,
                container,
            )
        finally:
            await queue.delete_queue()
            await poison.delete_queue()
            await blobs.delete_container(container)


class RecordingService:
    def __init__(self) -> None:
        self.jobs: list[object] = []

    async def ingest(self, job: object) -> None:
        self.jobs.append(job)

    async def delete(self, document_id: str) -> None:
        raise AssertionError("not used")


class NullRepository:
    def __init__(self) -> None:
        self.failed: list[str] = []

    async def get_document(self, document_id: str) -> None:
        return None

    async def upsert_document(self, document: DocumentRecord) -> None:
        self.failed.append(document.failure_reason or "")


def message(source_blob: str, document_id: str = "doc-1") -> str:
    return IngestionMessage(
        operation="ingest",
        document_id=document_id,
        scope_id="scope-a",
        content_type="text/plain",
        source_blob=source_blob,
    ).model_dump_json()


def build(storage: Storage, service: RecordingService, repository: NullRepository) -> QueueConsumer:
    handler = IngestionHandler(
        service,  # type: ignore[arg-type]
        repository,  # type: ignore[arg-type]
        storage.incoming,
        max_bytes=1000,
        chunking=ChunkingConfig(size=200, overlap=0),
    )
    return QueueConsumer(
        storage.queue,
        storage.poison,
        handler,
        handler.record_failure,
        retry=RetryPolicy(max_attempts=2, base_delay_seconds=1, visibility_timeout_seconds=30),
    )


async def test_message_is_consumed_from_a_real_queue_and_blob(storage: Storage) -> None:
    await storage.blobs.get_container_client(storage.container).upload_blob(
        "doc-1.txt", b"Text from the emulator."
    )
    await storage.queue.send_message(message("doc-1.txt"))
    service, repository = RecordingService(), NullRepository()

    assert await build(storage, service, repository).drain_once() == 1

    assert len(service.jobs) == 1
    assert (await storage.queue.get_queue_properties()).approximate_message_count == 0


async def test_oversized_source_is_poisoned_immediately_with_failed_state(
    storage: Storage,
) -> None:
    await storage.blobs.get_container_client(storage.container).upload_blob(
        "big.txt", b"x" * 2000
    )
    await storage.queue.send_message(message("big.txt", "doc-big"))
    service, repository = RecordingService(), NullRepository()

    await build(storage, service, repository).drain_once()

    poisoned = [m async for m in storage.poison.receive_messages()]
    assert service.jobs == []
    assert repository.failed == ["rejected: source blob exceeds 1000 bytes"]
    assert json.loads(poisoned[0].content)["reason"].startswith("rejected")
    assert (await storage.queue.get_queue_properties()).approximate_message_count == 0


async def test_transient_failure_is_redelivered_then_poisoned(storage: Storage) -> None:
    await storage.queue.send_message(message("missing.txt", "doc-missing"))
    service, repository = RecordingService(), NullRepository()
    consumer = build(storage, service, repository)

    assert await consumer.drain_once() == 1  # blob not found: retry in 1s
    assert await consumer.drain_once() == 0  # still invisible
    await asyncio.sleep(1.5)
    assert await consumer.drain_once() == 1  # second and last attempt: poisoned

    poisoned = [m async for m in storage.poison.receive_messages()]
    assert len(poisoned) == 1
    assert repository.failed == ["ResourceNotFoundError after 2 attempt(s)"]
