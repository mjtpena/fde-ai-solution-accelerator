import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from .contracts import Chunk, Document, Evidence, RetrievalRequest

CONTRACTS_PATH = Path(__file__).parents[3] / "contracts"


@pytest.mark.parametrize(
    ("model", "schema_name"),
    [
        (Document, "document.schema.json"),
        (Chunk, "chunk.schema.json"),
        (Evidence, "evidence.schema.json"),
        (RetrievalRequest, "retrieval_request.schema.json"),
    ],
)
def test_exported_schema_matches_model(model: type[Any], schema_name: str) -> None:
    exported_schema = json.loads((CONTRACTS_PATH / schema_name).read_text(encoding="utf-8"))

    assert exported_schema == model.model_json_schema()


def test_document_and_chunk_contracts_support_optional_metadata() -> None:
    document = Document(
        document_id="doc-1",
        title="Reference",
        source_uri="https://example.test/reference",
        content_hash="sha256:abc",
    )
    chunk = Chunk(chunk_id="chunk-1", document_id=document.document_id, text="Untrusted source text")

    assert document.effective_date is None
    assert chunk.version is None
    assert chunk.text == "Untrusted source text"


def test_contracts_parse_effective_dates_as_dates() -> None:
    document = Document.model_validate(
        {
            "document_id": "doc-1",
            "title": "Reference",
            "source_uri": "https://example.test/reference",
            "content_hash": "sha256:abc",
            "effective_date": "2026-01-02",
        }
    )
    chunk = Chunk.model_validate(
        {
            "chunk_id": "chunk-1",
            "document_id": document.document_id,
            "effective_date": "2026-01-02",
            "text": "Text",
        }
    )

    assert document.effective_date == date(2026, 1, 2)
    assert chunk.effective_date == date(2026, 1, 2)


def test_evidence_requires_nullable_fields_to_be_present() -> None:
    with pytest.raises(ValidationError):
        Evidence.model_validate(
            {
                "chunk_id": "chunk-1",
                "document_id": "doc-1",
                "document_title": "Reference",
                "score": 0.9,
                "text": "Evidence text",
                "source_uri": "https://example.test/reference",
            }
        )


@pytest.mark.parametrize("top_k", [0, 21])
def test_retrieval_request_rejects_top_k_outside_bounds(top_k: int) -> None:
    with pytest.raises(ValidationError):
        RetrievalRequest.model_validate({"query": "query", "top_k": top_k})


def test_retrieval_request_uses_independent_filter_defaults() -> None:
    first = RetrievalRequest.model_validate({"query": "first"})
    second = RetrievalRequest.model_validate({"query": "second"})

    first.filters["category"] = "reference"

    assert second.filters == {}
