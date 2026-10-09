"""Hybrid query planning; caller filters can only narrow context-derived scope."""

from dataclasses import dataclass
from typing import Protocol

from accelerator.retrieval_core.models import Evidence, RetrievalRequest
from accelerator.security_core.data_boundaries.context import ExecutionContext

MAX_SCOPE_FILTER_BYTES = 65536


class Retriever(Protocol):
    async def retrieve(self, req: RetrievalRequest, ctx: ExecutionContext) -> list[Evidence]: ...


class QueryEmbedder(Protocol):
    async def embed(self, query: str) -> list[float]: ...


@dataclass(frozen=True)
class HybridQuery:
    text: str
    top_k: int
    filter: str


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def build_query(req: RetrievalRequest, ctx: ExecutionContext) -> HybridQuery:
    if not ctx.scope_ids:
        raise PermissionError("Retrieval requires at least one authorized scope")
    if not req.query.strip() or req.query.strip() == "*":
        raise ValueError("Hybrid semantic retrieval requires a nonempty text query")
    scopes = sorted(ctx.scope_ids)
    if any(not scope for scope in scopes):
        raise ValueError("Authorized scope identifiers must not be empty")
    if sum(len(scope.encode("utf-8")) + scope.count("'") + 1 for scope in scopes) > (
        MAX_SCOPE_FILTER_BYTES - 32
    ):
        raise ValueError("Authorized scope filter exceeds the 64 KiB safety limit")
    delimiter = next(
        (char for char in "|,;~^" if all(char not in scope for scope in scopes)), None
    )
    if delimiter is None:
        raise ValueError("Authorized scope identifiers have no safe search.in delimiter")
    scope_filter = f"search.in(scope_id, {_literal(delimiter.join(scopes))}, '{delimiter}')"
    predicates = [f"({scope_filter})"]
    allowed_filters = {"document_id", "version", "content_hash", "section_heading"}
    for name, value in sorted(req.filters.items()):
        if name not in allowed_filters:
            raise ValueError(f"Unsupported retrieval filter: {name}")
        if not isinstance(value, str):
            raise ValueError(f"Retrieval filter {name} requires a string")
        predicates.append(f"({name} eq {_literal(value)})")
    return HybridQuery(text=req.query, top_k=req.top_k, filter=" and ".join(predicates))
