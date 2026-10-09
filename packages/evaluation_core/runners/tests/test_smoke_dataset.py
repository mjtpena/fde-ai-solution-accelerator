"""The committed smoke dataset is grounded in the committed fixture corpus."""

import json
from pathlib import Path

import pytest

from ...datasets import load_dataset
from ..corpus import ManifestDocument, load_corpus

ROOT = Path(__file__).resolve().parents[4]
DATASET = ROOT / "evaluations/example-datasets/smoke.jsonl"
CORPUS = ROOT / "tests/fixtures/retrieval/corpus"
CATEGORIES = {"factual", "synthesis", "conflict", "unsupported", "tool_selection", "injection"}


def test_every_dataset_category_is_covered() -> None:
    assert {row.category for row in load_dataset(DATASET)} == CATEGORIES


def test_expected_evidence_exists_in_the_rows_scope() -> None:
    chunks = {chunk.chunk_id: chunk for chunk in load_corpus(CORPUS)}
    for row in load_dataset(DATASET):
        for evidence_id in row.expected_evidence_ids:
            assert evidence_id in chunks, (row.id, evidence_id)
            assert chunks[evidence_id].scope_id == row.scope_id, (row.id, evidence_id)


def test_abstaining_rows_expect_no_evidence_and_answering_rows_do() -> None:
    for row in load_dataset(DATASET):
        assert row.expected_abstain == (row.expected_answer is None), row.id
        assert row.expected_abstain == (not row.expected_evidence_ids), row.id


def test_tool_rows_name_a_tool_and_injection_rows_name_a_canary() -> None:
    corpus_text = " ".join(chunk.text for chunk in load_corpus(CORPUS))
    for row in load_dataset(DATASET):
        assert (row.expected_tool is not None) == (row.category == "tool_selection"), row.id
        if row.category == "injection":
            canaries = [
                tag.removeprefix("canary:") for tag in row.tags if tag.startswith("canary:")
            ]
            assert canaries, row.id
            assert all(canary in corpus_text for canary in canaries), row.id


def test_an_unsupported_row_is_answerable_only_from_another_scope() -> None:
    chunks = load_corpus(CORPUS)
    isolation_rows = [row for row in load_dataset(DATASET) if "scope-isolation" in row.tags]

    assert isolation_rows and all(row.expected_abstain for row in isolation_rows)
    assert {chunk.scope_id for chunk in chunks} > {row.scope_id for row in isolation_rows}


def test_corpus_chunk_ids_follow_heading_order() -> None:
    ids = [chunk.chunk_id for chunk in load_corpus(CORPUS)]

    assert ids[:2] == ["backup-standard-0", "backup-standard-1"]
    assert len(ids) == len(set(ids))


def test_manifest_rejects_unsafe_names_and_duplicate_documents(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ManifestDocument.model_validate(
            {"document_id": "d", "file": "../escape.md", "title": "t", "scope_id": "s"}
        )
    document = {"document_id": "d", "file": "d.md", "title": "t", "scope_id": "s"}
    (tmp_path / "manifest.json").write_text(
        json.dumps({"documents": [document, document]}), encoding="utf-8"
    )
    (tmp_path / "d.md").write_text("## A\nText.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unique"):
        load_corpus(tmp_path)
