"""Cross-scope isolation against a real Azure AI Search service (opt-in).

Set TEST_AZURE_SEARCH_ENDPOINT to an HTTPS Search endpoint the current Azure
identity can manage (Search Service Contributor + Search Index Data
Contributor). The test creates a throwaway index, indexes one document in each
of two scopes, proves a caller in one scope never sees the other scope's
document even when the query matches it, and deletes the index. Set
TEST_AZURE_SEARCH_SEMANTIC=1 when the service has semantic ranking enabled.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from azure.identity.aio import DefaultAzureCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.indexes.aio import SearchIndexClient

from accelerator.infrastructure.search import AzureSearchRetriever, SearchSettings, ensure_index
from accelerator.retrieval_core.indexing.schema import IndexDefinition
from accelerator.retrieval_core.models import RetrievalRequest
from accelerator.security_core.data_boundaries.context import ExecutionContext

ENDPOINT = os.environ.get("TEST_AZURE_SEARCH_ENDPOINT")
SEMANTIC = os.environ.get("TEST_AZURE_SEARCH_SEMANTIC") == "1"
DIMENSIONS = 3

pytestmark = pytest.mark.skipif(
    not ENDPOINT, reason="Set TEST_AZURE_SEARCH_ENDPOINT to run live Azure AI Search tests."
)


class FixedEmbedder:
    async def embed(self, query: str) -> list[float]:
        return [0.5, 0.5, 0.5]


def context(*scopes: str) -> ExecutionContext:
    return ExecutionContext(
        correlation_id=str(uuid4()),
        user_id="live-isolation-test",
        roles=frozenset({"Reader"}),
        scope_ids=frozenset(scopes),
        deadline_utc=datetime.now(UTC) + timedelta(minutes=2),
    )


def document(scope: str, chunk: str) -> dict[str, object]:
    return {
        "chunk_id": chunk,
        "document_id": f"document-{scope}",
        "scope_id": scope,
        "document_title": "Quarterly retention policy",
        "source_uri": f"https://documents.example.test/{scope}",
        "content_hash": chunk,
        "version": "1",
        "section_heading": "Retention",
        "text": f"Quarterly retention for {scope} is ninety days.",
        "embedding": [0.5, 0.5, 0.5],
    }


@pytest.fixture
async def isolated_index() -> AsyncIterator[tuple[SearchClient, SearchSettings]]:
    assert ENDPOINT is not None
    name = f"isolation-test-{uuid4().hex[:12]}"
    async with DefaultAzureCredential() as credential:
        async with SearchIndexClient(ENDPOINT, credential) as indexes:
            await ensure_index(indexes, IndexDefinition(name=name, vector_dimensions=DIMENSIONS))
            try:
                async with SearchClient(ENDPOINT, name, credential) as client:
                    await client.upload_documents(
                        [document("scope-a", "chunk-a"), document("scope-b", "chunk-b")]
                    )
                    for _ in range(30):
                        if await client.get_document_count() == 2:
                            break
                        await asyncio.sleep(1)
                    else:
                        # Without the other scope's document the test proves nothing.
                        pytest.fail("Both isolation documents were not indexed within 30 s.")
                    settings = SearchSettings.model_validate(
                        {
                            "endpoint": ENDPOINT,
                            "index_name": name,
                            "vector_dimensions": DIMENSIONS,
                            "managed_identity_client_id": "unused-by-direct-client",
                            "semantic_ranking": SEMANTIC,
                        }
                    )
                    yield client, settings
            finally:
                await indexes.delete_index(name)


async def test_request_for_another_scopes_data_returns_nothing_from_it(
    isolated_index: tuple[SearchClient, SearchSettings],
) -> None:
    client, settings = isolated_index
    retriever = AzureSearchRetriever(client, FixedEmbedder(), settings)
    query = RetrievalRequest(query="quarterly retention scope-b ninety days", top_k=10)

    scope_a = await retriever.retrieve(query, context("scope-a"))
    outsider = await retriever.retrieve(query, context("scope-c"))

    assert [item.chunk_id for item in scope_a] == ["chunk-a"]
    assert outsider == []


async def test_caller_filters_cannot_widen_scope(
    isolated_index: tuple[SearchClient, SearchSettings],
) -> None:
    client, settings = isolated_index
    retriever = AzureSearchRetriever(client, FixedEmbedder(), settings)

    with pytest.raises(ValueError, match="Unsupported retrieval filter"):
        await retriever.retrieve(
            RetrievalRequest(query="retention", filters={"scope_id": "scope-b"}),
            context("scope-a"),
        )
    narrowed = await retriever.retrieve(
        RetrievalRequest(query="retention", filters={"document_id": "document-scope-b"}),
        context("scope-a"),
    )
    assert narrowed == []
