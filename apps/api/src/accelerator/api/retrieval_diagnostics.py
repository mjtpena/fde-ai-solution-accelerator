from typing import Annotated, Protocol, runtime_checkable

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel

from accelerator.configuration.settings import Settings
from accelerator.identity.scope_resolver import get_execution_context
from accelerator.security_core.data_boundaries.context import ExecutionContext

REDACTED = "[REDACTED]"


class RetrievalDiagnosticResult(BaseModel):
    chunk_id: str
    document_id: str
    document_title: str
    score: float
    reranker_score: float | None
    text: str
    source_uri: str


class RetrievalDiagnosticRecord(BaseModel):
    correlation_id: str
    scope_ids: frozenset[str]
    query: str
    filters: dict[str, object]
    results: list[RetrievalDiagnosticResult]


class RetrievalDiagnosticsResponse(BaseModel):
    correlation_id: str
    query: str
    filters: dict[str, object]
    results: list[RetrievalDiagnosticResult]


@runtime_checkable
class RetrievalDiagnosticsStore(Protocol):
    async def get(self, correlation_id: str) -> RetrievalDiagnosticRecord | None: ...

    async def save(self, record: RetrievalDiagnosticRecord) -> None: ...


class InMemoryRetrievalDiagnosticsStore:
    def __init__(self) -> None:
        self._records: dict[str, RetrievalDiagnosticRecord] = {}

    async def get(self, correlation_id: str) -> RetrievalDiagnosticRecord | None:
        return self._records.get(correlation_id)

    async def save(self, record: RetrievalDiagnosticRecord) -> None:
        self._records[record.correlation_id] = record


def require_contributor(
    context: Annotated[ExecutionContext, Depends(get_execution_context)],
) -> ExecutionContext:
    if not any(
        str(getattr(role, "value", role)).casefold() == "contributor"
        for role in context.roles
    ):
        raise HTTPException(status_code=403, detail="Contributor role required")
    return context


def get_diagnostics_store(request: Request) -> RetrievalDiagnosticsStore:
    store = getattr(request.app.state, "retrieval_diagnostics_store", None)
    if not isinstance(store, RetrievalDiagnosticsStore):
        raise HTTPException(status_code=503, detail="Retrieval diagnostics unavailable")
    return store


def get_api_settings(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    if not isinstance(settings, Settings):
        raise HTTPException(status_code=503, detail="API settings unavailable")
    return settings


router = APIRouter(prefix="/diagnostics/retrieval", tags=["retrieval diagnostics"])


@router.get("/{correlation_id}", response_model=RetrievalDiagnosticsResponse)
async def get_retrieval_diagnostics(
    correlation_id: Annotated[str, Path(min_length=1, max_length=128)],
    context: Annotated[ExecutionContext, Depends(require_contributor)],
    store: Annotated[RetrievalDiagnosticsStore, Depends(get_diagnostics_store)],
    settings: Annotated[Settings, Depends(get_api_settings)],
) -> RetrievalDiagnosticsResponse:
    record = await store.get(correlation_id)
    if record is None or not record.scope_ids or not record.scope_ids.issubset(context.scope_ids):
        raise HTTPException(status_code=404, detail="Retrieval diagnostics not found")

    query = record.query
    results = record.results
    if not settings.diagnostics_include_content:
        query = REDACTED
        results = [result.model_copy(update={"text": REDACTED}) for result in results]

    return RetrievalDiagnosticsResponse(
        correlation_id=record.correlation_id,
        query=query,
        filters=record.filters,
        results=results,
    )
