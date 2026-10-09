"""Build the queue consumer from settings and own every client's lifetime."""

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from agent_framework.foundry import FoundryEmbeddingClient
from azure.identity.aio import DefaultAzureCredential
from azure.search.documents.aio import SearchClient
from azure.storage.blob.aio import BlobServiceClient
from azure.storage.queue.aio import QueueClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from accelerator.retrieval_core.chunking.contracts import ChunkingConfig

from .consumer import QueueConsumer, RetryPolicy
from .handler import IngestionHandler
from .infrastructure.blob_store import AzureBlobStore, AzureSourceReader
from .infrastructure.database import create_engine
from .infrastructure.repository import PostgresIngestionRepository
from .infrastructure.search_index import AzureSearchChunkIndex
from .service import IngestionService
from .settings import WorkerSettings

logger = logging.getLogger("ingestion_worker")


@asynccontextmanager
async def compose_consumer(settings: WorkerSettings) -> AsyncIterator[QueueConsumer | None]:
    """Yield a ready consumer, or ``None`` outside production when indexing is unconfigured."""
    if not settings.indexing_configured:
        logger.warning("ingestion_disabled", extra={"reason": "indexing settings incomplete"})
        yield None
        return
    if (
        settings.database_url is None
        or settings.search_endpoint is None
        or settings.search_index_name is None
        or settings.vector_dimensions is None
        or settings.foundry_project_endpoint is None
    ):
        raise ValueError("indexing_configured implies these settings are present")

    async with AsyncExitStack() as stack:
        credential = await stack.enter_async_context(
            DefaultAzureCredential(managed_identity_client_id=settings.managed_identity_client_id)
        )
        if settings.storage_connection_string is not None:
            secret = settings.storage_connection_string.get_secret_value()
            blobs = BlobServiceClient.from_connection_string(secret)
            queue = QueueClient.from_connection_string(secret, settings.queue_name)
            poison = QueueClient.from_connection_string(secret, settings.poison_queue_name)
        else:
            if settings.blob_account_url is None or settings.queue_account_url is None:
                raise ValueError("indexing_configured implies both account URLs are present")
            blobs = BlobServiceClient(str(settings.blob_account_url), credential=credential)
            queue = QueueClient(
                str(settings.queue_account_url), settings.queue_name, credential=credential
            )
            poison = QueueClient(
                str(settings.queue_account_url), settings.poison_queue_name, credential=credential
            )
        for client in (blobs, queue, poison):
            await stack.enter_async_context(client)
        if settings.environment != "production":
            # Bicep provisions these in Azure; the emulator starts empty.
            await _ensure_local_resources(blobs, queue, poison, settings)

        engine = create_engine(
            str(settings.database_url),
            managed_identity=settings.database_auth_mode == "managed_identity",
            credential=credential,
            tls_ca_file=settings.database_tls_ca_file,
        )
        stack.push_async_callback(engine.dispose)
        search = await stack.enter_async_context(
            SearchClient(str(settings.search_endpoint), settings.search_index_name, credential)
        )
        embeddings = FoundryEmbeddingClient(
            project_endpoint=str(settings.foundry_project_endpoint),
            model=settings.foundry_embedding_deployment,
            credential=credential,
        )
        stack.push_async_callback(embeddings.close)

        repository = PostgresIngestionRepository(async_sessionmaker(engine, expire_on_commit=False))
        service = IngestionService(
            AzureBlobStore(blobs.get_container_client(settings.documents_container)),
            AzureSearchChunkIndex(
                search,
                embeddings,
                dimensions=settings.vector_dimensions,
                batch_size=settings.embedding_batch_size,
            ),
            repository,
        )
        handler = IngestionHandler(
            service,
            repository,
            AzureSourceReader(
                blobs.get_container_client(settings.incoming_container),
                max_bytes=settings.max_document_bytes,
            ),
            max_bytes=settings.max_document_bytes,
            chunking=ChunkingConfig(
                size=settings.chunk_size, overlap=settings.chunk_overlap, heading_aware=True
            ),
        )
        yield QueueConsumer(
            queue,
            poison,
            handler,
            handler.record_failure,
            retry=RetryPolicy(
                max_attempts=settings.max_attempts,
                visibility_timeout_seconds=settings.visibility_timeout_seconds,
            ),
        )


async def _ensure_local_resources(
    blobs: BlobServiceClient, queue: QueueClient, poison: QueueClient, settings: WorkerSettings
) -> None:
    from azure.core.exceptions import ResourceExistsError

    for container in (settings.incoming_container, settings.documents_container):
        try:
            await blobs.create_container(container)
        except ResourceExistsError:
            pass
    for client in (queue, poison):
        try:
            await client.create_queue()
        except ResourceExistsError:
            pass
