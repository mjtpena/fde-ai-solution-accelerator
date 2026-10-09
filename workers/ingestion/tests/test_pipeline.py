from datetime import date

import pytest
from pydantic import ValidationError

from accelerator.ingestion.pipeline import (
    IngestionMessage,
    RejectedDocument,
    build_job,
    chunk_id_for,
)
from accelerator.retrieval_core.chunking.contracts import ChunkingConfig


def message(**overrides: object) -> IngestionMessage:
    values: dict[str, object] = {
        "operation": "ingest",
        "document_id": "policies/retention.md",
        "scope_id": "scope-a",
        "title": "Retention policy",
        "source_uri": "https://documents.example.test/retention",
        "version": "3",
        "effective_date": date(2026, 1, 1),
        "content_type": "text/markdown",
        "source_blob": "incoming/retention.md",
    }
    values.update(overrides)
    return IngestionMessage.model_validate(values)


CHUNKING = ChunkingConfig(size=60, overlap=10, heading_aware=True)


def test_markdown_becomes_scoped_chunks_with_deterministic_ids() -> None:
    content = b"# Retention\nRecords are kept for 30 days.\n\n# Backups\nBackups run nightly."

    first = build_job(message(), content, max_bytes=10_000, chunking=CHUNKING)
    second = build_job(message(), content, max_bytes=10_000, chunking=CHUNKING)

    assert [chunk.chunk_id for chunk in first.chunks] == [c.chunk_id for c in second.chunks]
    assert {chunk.scope_id for chunk in first.chunks} == {"scope-a"}
    assert {chunk.section_heading for chunk in first.chunks} == {"Retention", "Backups"}
    assert all(chunk.version == "3" for chunk in first.chunks)
    assert first.document.scope_id == "scope-a"


def test_chunk_ids_are_search_key_safe_and_version_specific() -> None:
    key = chunk_id_for("a/b c'd", "1", 0)

    assert key.startswith("c-")
    assert key.replace("-", "").isalnum()
    assert key != chunk_id_for("a/b c'd", "2", 0)


@pytest.mark.parametrize(
    ("content", "overrides", "reason"),
    [
        (b"", {}, "empty"),
        (b"x" * 101, {}, "exceeds"),
        (b"\xff\xfe\xfa", {"content_type": "text/plain"}, "could not be parsed"),
        (b"   ", {"content_type": "text/plain"}, "no extractable text"),
    ],
)
def test_invalid_documents_are_rejected_permanently(
    content: bytes, overrides: dict[str, object], reason: str
) -> None:
    with pytest.raises(RejectedDocument, match=reason):
        build_job(message(**overrides), content, max_bytes=100, chunking=CHUNKING)


@pytest.mark.parametrize(
    "overrides",
    [
        {"content_type": "application/x-msdownload"},
        {"scope_id": ""},
        {"unexpected": "field"},
        {"document_id": "bad\nid"},
        {"content_type": None},
        {"source_blob": None},
    ],
)
def test_messages_outside_the_allow_list_do_not_validate(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        message(**overrides)


def test_delete_messages_need_no_source() -> None:
    deletion = message(operation="delete", content_type=None, source_blob=None)

    assert deletion.operation == "delete"
