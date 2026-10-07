import logging
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from azure.identity.aio import ManagedIdentityCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import VectorizedQuery
from pydantic import BaseModel, ConfigDict, Field

from accelerator.infrastructure.search.settings import SearchSettings
from accelerator.retrieval_core.indexing.schema import (
    RESULT_FIELDS,
    SEMANTIC_CONFIGURATION,
    VECTOR_FIELD,
)
from accelerator.retrieval_core.models import Evidence, RetrievalRequest
from accelerator.retrieval_core.search import QueryEmbedder, Retriever, build_query
from accelerator.security_core.data_boundaries.context import ExecutionContext

logger = logging.getLogger(__name__)


class SearchHit(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)

    scope_id: str
    chunk_id: str
    document_id: str
    document_title: str
    version: str | None = None
    text: str
    source_uri: str
    score: float = Field(alias="@search.score")
    reranker_score: float | None = Field(default=None, alias="@search.reranker_score")

    def evidence(self) -> Evidence:
        return Evidence(**self.model_dump(exclude={"scope_id"}))


class AzureSearchRetriever:
    def __init__(
        self, client: SearchClient, embedder: QueryEmbedder, settings: SearchSettings
    ) -> None:
        self._client = client
        self._embedder = embedder
        self._settings = settings

    async def retrieve(self, req: RetrievalRequest, ctx: ExecutionContext) -> list[Evidence]:
        query = build_query(req, ctx)
        vector = await self._embedder.embed(query.text)
        if len(vector) != self._settings.vector_dimensions or not all(
            math.isfinite(value) for value in vector
        ):
            raise ValueError("Query embedding must match index dimensions and contain finite values")
        results = await self._client.search(
            search_text=query.text,
            filter=query.filter,
            top=query.top_k,
            select=list(RESULT_FIELDS),
            search_fields=["text", "document_title", "section_heading"],
            vector_queries=[
                VectorizedQuery(
                    vector=vector,
                    fields=VECTOR_FIELD,
                    k_nearest_neighbors=self._settings.vector_candidates,
                )
            ],
            vector_filter_mode="preFilter",
            query_type="semantic" if self._settings.semantic_ranking else "simple",
            semantic_configuration_name=(
                SEMANTIC_CONFIGURATION if self._settings.semantic_ranking else None
            ),
            semantic_error_mode="fail",
        )
        evidence = []
        async for raw in results:
            hit = SearchHit.model_validate(raw)
            if hit.scope_id not in ctx.scope_ids:
                logger.error(
                    "search_scope_violation",
                    extra={"correlation_id": ctx.correlation_id, "event": "search_scope_violation"},
                )
                raise PermissionError("Search returned evidence outside authorized scopes")
            evidence.append(hit.evidence())
        return evidence


@asynccontextmanager
async def open_retriever(
    settings: SearchSettings, embedder: QueryEmbedder
) -> AsyncIterator[Retriever]:
    """Own and close the managed identity and async SDK client per application lifespan."""
    async with ManagedIdentityCredential(
        client_id=settings.managed_identity_client_id
    ) as credential:
        async with SearchClient(
            endpoint=str(settings.endpoint),
            index_name=settings.index_name,
            credential=credential,
        ) as client:
            yield AzureSearchRetriever(client, embedder, settings)
