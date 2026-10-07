"""SDK-free chunk index definition shared with the Search infrastructure adapter."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

VECTOR_FIELD = "embedding"
VECTOR_PROFILE = "chunk-vectors"
VECTOR_ALGORITHM = "chunk-hnsw"
SEMANTIC_CONFIGURATION = "chunk-semantic"


@dataclass(frozen=True)
class IndexField:
    name: str
    kind: Literal["string", "datetime", "vector"] = "string"
    key: bool = False
    searchable: bool = False
    filterable: bool = False


CHUNK_FIELDS = (
    IndexField("chunk_id", key=True, filterable=True),
    IndexField("document_id", filterable=True),
    IndexField("scope_id", filterable=True),
    IndexField("document_title", searchable=True),
    IndexField("source_uri"),
    IndexField("content_hash", filterable=True),
    IndexField("version", filterable=True),
    IndexField("effective_date", kind="datetime", filterable=True),
    IndexField("section_heading", searchable=True, filterable=True),
    IndexField("text", searchable=True),
    IndexField(VECTOR_FIELD, kind="vector", searchable=True),
)
RESULT_FIELDS = tuple(field.name for field in CHUNK_FIELDS if field.kind != "vector")


class IndexDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,126}[a-z0-9]$")
    vector_dimensions: int = Field(ge=1, le=4096)
