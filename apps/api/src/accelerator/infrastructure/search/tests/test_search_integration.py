"""Credential-free integration through the real async Azure Search SDK HTTP pipeline."""

import asyncio
import json
import importlib
from datetime import UTC, datetime, timedelta
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from azure.core.credentials import AccessToken
from azure.core.exceptions import HttpResponseError
from azure.core.pipeline.transport import AsyncHttpResponse, AsyncHttpTransport, HttpRequest
from azure.search.documents.aio import SearchClient
from pydantic import ValidationError

from accelerator.infrastructure.search import (
    AzureSearchRetriever,
    SearchSettings,
    build_index,
    ensure_index,
    open_retriever,
)
from accelerator.retrieval_core.indexing.schema import IndexDefinition
from accelerator.retrieval_core.models import Evidence, RetrievalRequest
from accelerator.security_core.data_boundaries.context import ExecutionContext


class Response(AsyncHttpResponse):
    def __init__(self, request: HttpRequest, payload: dict[str, Any], status: int = 200) -> None:
        super().__init__(request, None)
        self.status_code = status
        self.headers = {"Content-Type": "application/json"}
        self.content_type = "application/json"
        self._body = json.dumps(payload).encode()

    def body(self) -> bytes:
        return self._body


class Embedder:
    def __init__(self, vector: list[float] | None = None) -> None:
        self.vector = [0.1, 0.2, 0.3] if vector is None else vector
        self.calls: list[str] = []

    async def embed(self, query: str) -> list[float]:
        self.calls.append(query)
        return self.vector


def context(*scopes: str) -> ExecutionContext:
    return ExecutionContext(
        correlation_id="integration-test",
        user_id="user",
        roles=frozenset({"reader"}),
        scope_ids=frozenset(scopes),
        deadline_utc=datetime.now(UTC) + timedelta(minutes=1),
    )


def settings(**changes: object) -> SearchSettings:
    return SearchSettings.model_validate(
        {
            "endpoint": "https://search.example.test",
            "index_name": "test-chunks",
            "vector_dimensions": 3,
            "managed_identity_client_id": "test-managed-identity",
            **changes,
        }
    )


def hit(scope: str, chunk: str) -> dict[str, object]:
    return {
        "scope_id": scope,
        "chunk_id": chunk,
        "document_id": "document",
        "document_title": "Policy",
        "version": "v1",
        "text": "untrusted document content",
        "source_uri": "https://documents.example.test/policy",
        "@search.score": 0.04,
        "@search.rerankerScore": 3.5,
    }


def client(transport: AsyncMock) -> SearchClient:
    credential = AsyncMock()
    credential.get_token.return_value = AccessToken("test-token", 4102444800)
    return SearchClient(
        endpoint="https://search.example.test",
        index_name="test-chunks",
        credential=credential,
        transport=transport,
        retry_total=0,
    )


@pytest.mark.asyncio
async def test_hybrid_wire_query_is_scoped_before_vector_and_semantic_ranking() -> None:
    transport = AsyncMock(spec=AsyncHttpTransport)
    requests: list[dict[str, Any]] = []
    corpus = [hit("tenant-a", "chunk-a"), hit("tenant-b", "chunk-b")]

    async def send(request: HttpRequest, **kwargs: object) -> Response:
        assert isinstance(request.body, str | bytes)
        payload = json.loads(request.body)
        requests.append(payload)
        # The test service enforces the exact expected predicate, not a substring approximation.
        assert payload["filter"] == "(search.in(scope_id, 'tenant-a', '|'))"
        return Response(request, {"value": [row for row in corpus if row["scope_id"] == "tenant-a"]})

    transport.send.side_effect = send
    async with client(transport) as sdk:
        adapter = AzureSearchRetriever(sdk, Embedder(), settings())
        result = await adapter.retrieve(RetrievalRequest(query="policy", top_k=2), context("tenant-a"))
    assert [item.chunk_id for item in result] == ["chunk-a"]
    assert all(isinstance(item, Evidence) for item in result)
    assert result[0].score == 0.04
    assert result[0].reranker_score == 3.5
    assert result[0].text == "untrusted document content"
    assert "scope_id" not in result[0].model_dump()
    payload = requests[0]
    assert payload["search"] == "policy"
    assert payload["top"] == 2
    assert payload["queryType"] == "semantic"
    assert payload["semanticConfiguration"] == "chunk-semantic"
    assert payload["vectorFilterMode"] == "preFilter"
    assert payload["semanticErrorHandling"] == "fail"
    assert payload["vectorQueries"] == [
        {"kind": "vector", "vector": [0.1, 0.2, 0.3], "fields": "embedding", "k": 50}
    ]
    assert "scope_id" in payload["select"].split(",")
    assert "embedding" not in payload["select"].split(",")


@pytest.mark.asyncio
async def test_cross_scope_response_aborts_entire_retrieval_without_partial_evidence() -> None:
    transport = AsyncMock(spec=AsyncHttpTransport)

    async def send(request: HttpRequest, **kwargs: object) -> Response:
        return Response(request, {"value": [hit("tenant-a", "a"), hit("tenant-b", "b")]})

    transport.send.side_effect = send
    async with client(transport) as sdk:
        adapter = AzureSearchRetriever(sdk, Embedder(), settings())
        with pytest.raises(PermissionError, match="outside authorized scopes"):
            await adapter.retrieve(RetrievalRequest(query="policy", top_k=5), context("tenant-a"))


@pytest.mark.asyncio
async def test_all_context_scopes_are_allowed_but_metadata_filter_only_narrows() -> None:
    transport = AsyncMock(spec=AsyncHttpTransport)

    async def send(request: HttpRequest, **kwargs: object) -> Response:
        assert isinstance(request.body, str | bytes)
        payload = json.loads(request.body)
        assert payload["filter"] == (
            "(search.in(scope_id, 'tenant-a|tenant-b', '|')) and (version eq 'v1')"
        )
        return Response(request, {"value": [hit("tenant-a", "a"), hit("tenant-b", "b")]})

    transport.send.side_effect = send
    async with client(transport) as sdk:
        adapter = AzureSearchRetriever(sdk, Embedder(), settings(semantic_ranking=False))
        result = await adapter.retrieve(
            RetrievalRequest(query="policy", top_k=5, filters={"version": "v1"}),
            context("tenant-a", "tenant-b"),
        )
    assert [item.chunk_id for item in result] == ["a", "b"]


@pytest.mark.asyncio
async def test_missing_scope_in_service_response_fails_validation() -> None:
    transport = AsyncMock(spec=AsyncHttpTransport)

    async def send(request: HttpRequest, **kwargs: object) -> Response:
        row = hit("tenant-a", "a")
        del row["scope_id"]
        return Response(request, {"value": [row]})

    transport.send.side_effect = send
    async with client(transport) as sdk:
        adapter = AzureSearchRetriever(sdk, Embedder(), settings())
        with pytest.raises(ValidationError):
            await adapter.retrieve(RetrievalRequest(query="policy", top_k=5), context("tenant-a"))


@pytest.mark.asyncio
async def test_scope_widening_and_empty_context_never_embed_or_call_service() -> None:
    transport = AsyncMock(spec=AsyncHttpTransport)
    embedder = Embedder()
    async with client(transport) as sdk:
        adapter = AzureSearchRetriever(sdk, embedder, settings())
        with pytest.raises(ValueError, match="Unsupported"):
            await adapter.retrieve(
                RetrievalRequest(query="policy", top_k=5, filters={"scope_id": "tenant-b"}),
                context("tenant-a"),
            )
        with pytest.raises(PermissionError):
            await adapter.retrieve(RetrievalRequest(query="policy", top_k=5), context())
    assert embedder.calls == []
    transport.send.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("vector", [[0.1], [float("nan"), 0.2, 0.3], [0.1, float("inf"), 0.3]])
async def test_invalid_embedding_never_calls_search(vector: list[float]) -> None:
    transport = AsyncMock(spec=AsyncHttpTransport)
    async with client(transport) as sdk:
        adapter = AzureSearchRetriever(sdk, Embedder(vector), settings())
        with pytest.raises(ValueError, match="dimensions"):
            await adapter.retrieve(RetrievalRequest(query="policy", top_k=5), context("tenant-a"))
    transport.send.assert_not_called()


@pytest.mark.asyncio
async def test_service_errors_propagate_instead_of_returning_empty_evidence() -> None:
    transport = AsyncMock(spec=AsyncHttpTransport)

    async def send(request: HttpRequest, **kwargs: object) -> Response:
        return Response(request, {"error": {"code": "unavailable", "message": "failed"}}, 503)

    transport.send.side_effect = send
    async with client(transport) as sdk:
        adapter = AzureSearchRetriever(sdk, Embedder(), settings())
        with pytest.raises(HttpResponseError):
            await adapter.retrieve(RetrievalRequest(query="policy", top_k=5), context("tenant-a"))


def test_index_schema_has_lineage_filterable_scope_and_matching_ranker_vector_profile() -> None:
    index = build_index(IndexDefinition(name="test-chunks", vector_dimensions=3))
    fields = {field.name: field for field in index.fields}
    assert fields["chunk_id"].key
    assert fields["scope_id"].filterable
    assert fields["document_id"].filterable
    assert fields["embedding"].vector_search_dimensions == 3
    assert fields["embedding"].hidden
    assert fields["embedding"].vector_search_profile_name == "chunk-vectors"
    assert fields["text"].searchable
    assert {"content_hash", "version", "effective_date", "section_heading"} <= fields.keys()
    assert index.vector_search is not None
    assert index.vector_search.profiles is not None
    assert index.vector_search.profiles[0].name == "chunk-vectors"
    assert index.semantic_search is not None
    assert index.semantic_search.configurations is not None
    assert index.semantic_search.configurations[0].name == "chunk-semantic"


def test_missing_settings_fail_fast() -> None:
    with pytest.raises(ValidationError):
        SearchSettings.model_validate({})


def test_api_namespace_composes_retrieval_and_security_workspace_packages() -> None:
    api = importlib.import_module("accelerator.infrastructure.search")
    retrieval = importlib.import_module("accelerator.retrieval_core.search")
    security = importlib.import_module("accelerator.security_core.data_boundaries.context")
    assert api.AzureSearchRetriever is AzureSearchRetriever
    assert retrieval.Retriever is not None
    assert security.ExecutionContext is ExecutionContext


def test_insecure_endpoint_fails_fast() -> None:
    with pytest.raises(ValidationError):
        settings(endpoint="http://search.example.test")


@pytest.mark.asyncio
async def test_managed_identity_factory_owns_and_closes_resources() -> None:
    credential = AsyncMock()
    sdk = AsyncMock()
    with (
        patch(
            "accelerator.infrastructure.search.adapter.ManagedIdentityCredential"
        ) as credential_factory,
        patch("accelerator.infrastructure.search.adapter.SearchClient") as client_factory,
    ):
        credential_factory.return_value.__aenter__.return_value = credential
        client_factory.return_value.__aenter__.return_value = sdk
        async with open_retriever(settings(), Embedder()) as adapter:
            assert isinstance(adapter, AzureSearchRetriever)
        credential_factory.assert_called_once_with(client_id="test-managed-identity")
        client_factory.assert_called_once_with(
            endpoint="https://search.example.test/",
            index_name="test-chunks",
            credential=credential,
        )
        client_factory.return_value.__aexit__.assert_awaited_once()
        credential_factory.return_value.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_index_provisioning_uses_injected_client_and_propagates_errors() -> None:
    index_client = AsyncMock()
    definition = IndexDefinition(name="test-chunks", vector_dimensions=3)
    await ensure_index(index_client, definition)
    provisioned = index_client.create_or_update_index.call_args.args[0]
    assert provisioned.serialize() == build_index(definition).serialize()
    index_client.create_or_update_index.side_effect = HttpResponseError(message="index failure")
    with pytest.raises(HttpResponseError, match="index failure"):
        await ensure_index(index_client, definition)


@pytest.mark.parametrize("dimensions", [0, 1, 4097])
def test_invalid_vector_dimensions_fail_before_index_or_client_creation(dimensions: int) -> None:
    with pytest.raises(ValidationError):
        settings(vector_dimensions=dimensions)
    with pytest.raises(ValidationError):
        IndexDefinition(name="test-chunks", vector_dimensions=dimensions)


@pytest.mark.parametrize("dimensions", [2, 4096])
def test_vector_dimension_service_boundaries_are_accepted(dimensions: int) -> None:
    assert settings(vector_dimensions=dimensions).vector_dimensions == dimensions
    assert IndexDefinition(name="test-chunks", vector_dimensions=dimensions).vector_dimensions == (
        dimensions
    )


@pytest.mark.asyncio
async def test_expired_context_never_embeds_or_calls_search() -> None:
    transport = AsyncMock(spec=AsyncHttpTransport)
    embedder = Embedder()
    ctx = context("tenant-a").model_copy(
        update={"deadline_utc": datetime.now(UTC) - timedelta(seconds=1)}
    )
    async with client(transport) as sdk:
        with pytest.raises(TimeoutError, match="deadline"):
            await AzureSearchRetriever(sdk, embedder, settings()).retrieve(
                RetrievalRequest(query="policy"), ctx
            )
    assert embedder.calls == []
    transport.send.assert_not_called()


@pytest.mark.asyncio
async def test_deadline_cancels_embedding_without_search_io() -> None:
    transport = AsyncMock(spec=AsyncHttpTransport)
    embedder = AsyncMock()
    cancelled = asyncio.Event()

    async def embed(query: str) -> list[float]:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        return [0.1, 0.2, 0.3]

    embedder.embed.side_effect = embed
    ctx = context("tenant-a").model_copy(
        update={"deadline_utc": datetime.now(UTC) + timedelta(milliseconds=50)}
    )
    async with client(transport) as sdk:
        with pytest.raises(TimeoutError):
            await AzureSearchRetriever(sdk, embedder, settings()).retrieve(
                RetrievalRequest(query="policy"), ctx
            )
    assert cancelled.is_set()
    transport.send.assert_not_called()


@pytest.mark.asyncio
async def test_deadline_cancels_real_sdk_http_call() -> None:
    transport = AsyncMock(spec=AsyncHttpTransport)
    cancelled = asyncio.Event()

    async def send(request: HttpRequest, **kwargs: object) -> Response:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        return Response(request, {"value": []})

    transport.send.side_effect = send
    ctx = context("tenant-a").model_copy(
        update={"deadline_utc": datetime.now(UTC) + timedelta(milliseconds=50)}
    )
    async with client(transport) as sdk:
        with pytest.raises(TimeoutError):
            await AzureSearchRetriever(sdk, Embedder(), settings()).retrieve(
                RetrievalRequest(query="policy"), ctx
            )
    assert cancelled.is_set()
    transport.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_deadline_covers_paging_without_returning_partial_evidence() -> None:
    sdk = AsyncMock(spec=SearchClient)
    cancelled = asyncio.Event()

    async def rows() -> AsyncIterator[dict[str, object]]:
        row = hit("tenant-a", "first")
        row["@search.reranker_score"] = row.pop("@search.rerankerScore")
        yield row
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    sdk.search.return_value = rows()
    ctx = context("tenant-a").model_copy(
        update={"deadline_utc": datetime.now(UTC) + timedelta(milliseconds=50)}
    )
    with pytest.raises(TimeoutError):
        await AzureSearchRetriever(sdk, Embedder(), settings()).retrieve(
            RetrievalRequest(query="policy"), ctx
        )
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_oversized_scope_filter_never_embeds_or_calls_search() -> None:
    transport = AsyncMock(spec=AsyncHttpTransport)
    embedder = Embedder()
    async with client(transport) as sdk:
        with pytest.raises(ValueError, match="64 KiB"):
            await AzureSearchRetriever(sdk, embedder, settings()).retrieve(
                RetrievalRequest(query="policy"), context("a" * 65536)
            )
    assert embedder.calls == []
    transport.send.assert_not_called()
