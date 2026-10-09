"""Load the offline evaluation corpus: Markdown files with a scope manifest.

Parsing and chunking use ``retrieval_core`` exactly as ingestion does. Chunk IDs
are ``<document_id>-<n>`` in heading order so dataset rows can name expected
evidence. Every chunk keeps the scope assigned by the manifest; retrieval filters
on it from the trusted execution context, never from dataset rows. Chunks carry
the manifest's ``version`` and ``effective_date`` like ingested chunks do.
"""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from accelerator.retrieval_core.chunking.chunker import TextChunker
from accelerator.retrieval_core.chunking.contracts import ChunkingConfig
from accelerator.retrieval_core.parsing.markdown import MarkdownParser

# Large enough that each fixture section is exactly one chunk.
CORPUS_CHUNKING = ChunkingConfig(size=1000, overlap=0, heading_aware=True)


class ManifestDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    file: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*\.md$")
    title: str = Field(min_length=1)
    scope_id: str = Field(min_length=1)
    version: str | None = None
    effective_date: date | None = None


class CorpusManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    documents: tuple[ManifestDocument, ...] = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class CorpusChunk:
    chunk_id: str
    document_id: str
    document_title: str
    source_uri: str
    version: str | None
    effective_date: date | None
    scope_id: str
    section_heading: str | None
    text: str


def load_corpus(directory: Path) -> tuple[CorpusChunk, ...]:
    manifest = CorpusManifest.model_validate_json(
        (directory / "manifest.json").read_text(encoding="utf-8")
    )
    if len({document.document_id for document in manifest.documents}) != len(manifest.documents):
        raise ValueError("Corpus document IDs must be unique.")
    chunker = TextChunker(CORPUS_CHUNKING)
    chunks: list[CorpusChunk] = []
    for document in manifest.documents:
        parsed = MarkdownParser().parse((directory / document.file).read_bytes())
        pieces = chunker.chunk(parsed)
        if not pieces:
            raise ValueError(f"Corpus document {document.document_id} has no text.")
        chunks.extend(
            CorpusChunk(
                chunk_id=f"{document.document_id}-{index}",
                document_id=document.document_id,
                document_title=document.title,
                source_uri=f"fixture://corpus/{document.file}",
                version=document.version,
                effective_date=document.effective_date,
                scope_id=document.scope_id,
                section_heading=piece.section_heading,
                text=piece.text,
            )
            for index, piece in enumerate(pieces)
        )
    return tuple(chunks)
