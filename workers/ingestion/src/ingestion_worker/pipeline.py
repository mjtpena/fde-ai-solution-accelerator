"""Turn a queued ingestion request and its source bytes into an ``IngestionJob``.

Validation (type and size allow-list), parsing and chunking happen here, without
any storage SDK. Chunk IDs are deterministic so re-ingesting the same document
version upserts the same index keys.
"""

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from accelerator.retrieval_core.chunking.chunker import TextChunker
from accelerator.retrieval_core.chunking.contracts import ChunkingConfig
from accelerator.retrieval_core.parsing.contracts import DocumentParser
from accelerator.retrieval_core.parsing.markdown import MarkdownParser
from accelerator.retrieval_core.parsing.pdf import PdfParser
from accelerator.retrieval_core.parsing.text import TextParser

from .service import IngestionJob

ContentType = Literal["text/plain", "text/markdown", "application/pdf"]

PARSERS: Mapping[str, Callable[[], DocumentParser]] = {
    "text/plain": TextParser,
    "text/markdown": MarkdownParser,
    "application/pdf": PdfParser,
}

Identifier = Field(min_length=1, max_length=255, pattern=r"^[^\x00-\x1f]+$")


class IngestionMessage(BaseModel):
    """A queue message. Written only by trusted server code that resolved ``scope_id``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: Literal["ingest", "delete"]
    document_id: str = Identifier
    scope_id: str = Identifier
    title: str = Field(default="", max_length=1024)
    source_uri: str = Field(default="", max_length=2048)
    version: str | None = Field(default=None, max_length=255)
    effective_date: date | None = None
    content_type: ContentType | None = None
    # Blob name in the incoming container holding the uploaded source document.
    source_blob: str | None = Field(default=None, min_length=1, max_length=1024)

    @model_validator(mode="after")
    def require_a_source_to_ingest(self) -> "IngestionMessage":
        if self.operation == "ingest" and (self.content_type is None or self.source_blob is None):
            raise ValueError("ingest messages require content_type and source_blob")
        return self


class RejectedDocument(ValueError):
    """Permanent: retrying the same message cannot succeed."""


@dataclass(frozen=True, slots=True)
class Document:
    document_id: str
    scope_id: str
    title: str
    source_uri: str
    version: str | None
    effective_date: date | None


@dataclass(frozen=True, slots=True)
class IndexedChunk:
    chunk_id: str
    document_id: str
    scope_id: str
    document_title: str
    source_uri: str
    content_hash: str
    text: str
    version: str | None
    effective_date: date | None
    section_heading: str | None


def chunk_id_for(document_id: str, version: str | None, index: int) -> str:
    """Stable, Azure-Search-safe key for the ``index``-th chunk of a document version."""
    material = "\x1f".join((document_id, version or "", str(index)))
    return "c-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:40]


def build_job(
    message: IngestionMessage,
    content: bytes,
    *,
    max_bytes: int,
    chunking: ChunkingConfig,
) -> IngestionJob:
    if message.operation != "ingest" or message.content_type is None:
        raise RejectedDocument("only ingest messages with a content type build a job")
    if not content:
        raise RejectedDocument("source document is empty")
    if len(content) > max_bytes:
        raise RejectedDocument(f"source document exceeds {max_bytes} bytes")
    try:
        parsed = PARSERS[message.content_type]().parse(content)
    except Exception as error:
        # Parsing is a pure function of bytes the worker already holds, so any parser
        # failure is permanent. Parsers raise their own types (pypdf raises
        # PdfReadError and LimitReachedError, neither a ValueError); treating those
        # as transient would retry a malformed or hostile upload with backoff.
        raise RejectedDocument(
            f"source document could not be parsed ({type(error).__name__})"
        ) from error
    pieces = TextChunker(chunking).chunk(parsed)
    if not pieces:
        raise RejectedDocument("source document has no extractable text")
    content_hash = hashlib.sha256(content).hexdigest()
    document = Document(
        document_id=message.document_id,
        scope_id=message.scope_id,
        title=message.title or message.document_id,
        source_uri=message.source_uri,
        version=message.version,
        effective_date=message.effective_date,
    )
    chunks = tuple(
        IndexedChunk(
            chunk_id=chunk_id_for(document.document_id, document.version, index),
            document_id=document.document_id,
            scope_id=document.scope_id,
            document_title=document.title,
            source_uri=document.source_uri,
            content_hash=content_hash,
            text=piece.text,
            version=document.version,
            effective_date=document.effective_date,
            section_heading=piece.section_heading,
        )
        for index, piece in enumerate(pieces)
    )
    return IngestionJob(document=document, source_content=content, chunks=chunks)
