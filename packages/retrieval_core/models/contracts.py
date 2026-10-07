from datetime import date
from typing import Any

from pydantic import BaseModel, Field


class Document(BaseModel):
    document_id: str
    title: str
    source_uri: str
    content_hash: str
    version: str | None = None
    effective_date: date | None = None


class Chunk(BaseModel):
    chunk_id: str
    document_id: str
    version: str | None = None
    effective_date: date | None = None
    section_heading: str | None = None
    text: str


class Evidence(BaseModel):
    chunk_id: str
    document_id: str
    document_title: str
    version: str | None
    score: float
    reranker_score: float | None
    text: str
    source_uri: str


class RetrievalRequest(BaseModel):
    query: str
    top_k: int = Field(5, ge=1, le=20)
    filters: dict[str, Any] = Field(default_factory=dict)
