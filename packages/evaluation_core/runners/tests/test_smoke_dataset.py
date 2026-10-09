"""The committed smoke dataset is grounded in the committed fixture corpus.

Every row declares what it tests: one ``class:`` tag, the ``gate:`` tags it is designed
to exercise, and the evidence its attack depends on (``canary:``,
``restricted-evidence:``, ``injected-tool:``, ``approval-followup:``). These tests
keep those declarations true; ``test_red_team.py`` proves each one is earned.
"""

import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from ...datasets import DatasetRow, load_dataset
from ..approval_followups import FollowUp, follow_up_of
from ..corpus import ManifestDocument, load_corpus
from ..smoke import EVALUATION_SCOPES, GateName

ROOT = Path(__file__).resolve().parents[4]
DATASET = ROOT / "evaluations/example-datasets/smoke.jsonl"
CORPUS = ROOT / "tests/fixtures/retrieval/corpus"
CATEGORIES = {"factual", "synthesis", "conflict", "unsupported", "tool_selection", "injection"}
TOOLS = {"document_status", "request_document_review"}
ROWS = load_dataset(DATASET)
CHUNKS = {chunk.chunk_id: chunk for chunk in load_corpus(CORPUS)}

# The floor for each class; the suite may grow but never silently shrink.
MINIMUM_ROWS = {
    "benign": 30,
    "benign-lookalike": 6,
    "conflict": 2,
    "ambiguous": 5,
    "unsupported": 11,
    "tool-use": 5,
    "direct-injection": 12,
    "indirect-injection": 17,
    "cross-scope": 12,
    "fabricated-citation": 9,
    "approval-bypass": 7,
    "content-harm": 2,
}
ADVERSARIAL = {
    "direct-injection",
    "indirect-injection",
    "cross-scope",
    "fabricated-citation",
    "approval-bypass",
}


def values(row: DatasetRow, prefix: str) -> list[str]:
    return [tag.removeprefix(prefix) for tag in row.tags if tag.startswith(prefix)]


def row_class(row: DatasetRow) -> str:
    (name,) = values(row, "class:")
    return name


def test_every_dataset_category_is_covered() -> None:
    assert {row.category for row in ROWS} == CATEGORIES


def test_row_ids_are_unique_and_rows_are_smoke_rows() -> None:
    assert len({row.id for row in ROWS}) == len(ROWS)
    for row in ROWS:
        assert re.fullmatch(r"smoke-[a-z]+-\d{3}", row.id), row.id
        assert "smoke" in row.tags, row.id


def test_every_row_declares_one_class_and_the_gates_it_exercises() -> None:
    gates = {gate.value for gate in GateName}
    for row in ROWS:
        assert len(values(row, "class:")) == 1, row.id
        assert row_class(row) in MINIMUM_ROWS, row.id
        declared = values(row, "gate:")
        assert declared, row.id
        assert set(declared) <= gates, row.id


def test_each_class_keeps_its_minimum_size() -> None:
    counts = Counter(row_class(row) for row in ROWS)
    for name, minimum in MINIMUM_ROWS.items():
        assert counts[name] >= minimum, (name, counts[name])
    assert len(ROWS) >= 119


def test_every_gate_has_rows_designed_for_it() -> None:
    counts = Counter(gate for row in ROWS for gate in values(row, "gate:"))
    assert {gate.value for gate in GateName} <= set(counts)
    hard = (
        "citation_validity",
        "scope_isolation",
        "approval_bypass",
        "injection_followed",
        "content_safety",
    )
    assert all(counts[gate] >= 10 for gate in hard), counts


def test_adversarial_rows_map_to_owasp_risks_and_name_their_technique() -> None:
    risks: set[str] = set()
    for row in ROWS:
        if row_class(row) in ADVERSARIAL:
            assert values(row, "owasp:"), row.id
        if row_class(row) in ADVERSARIAL - {"approval-bypass"}:
            assert values(row, "technique:"), row.id
        risks.update(values(row, "owasp:"))
    assert {"LLM01", "LLM02", "LLM06", "LLM07", "LLM08", "LLM09"} <= risks


def test_rows_claim_only_the_evaluation_scope() -> None:
    # Rows never grant access; the harness context does. A row still names the
    # scope its expected evidence comes from, and that is the evaluated principal's.
    for row in ROWS:
        assert row.scope_id in EVALUATION_SCOPES, row.id


def test_expected_evidence_exists_in_the_rows_scope() -> None:
    for row in ROWS:
        for evidence_id in row.expected_evidence_ids:
            assert evidence_id in CHUNKS, (row.id, evidence_id)
            assert CHUNKS[evidence_id].scope_id == row.scope_id, (row.id, evidence_id)


def test_abstaining_rows_expect_no_evidence_and_answering_rows_do() -> None:
    for row in ROWS:
        assert row.expected_abstain == (row.expected_answer is None), row.id
        assert row.expected_abstain == (not row.expected_evidence_ids), row.id


def test_class_specific_expectations() -> None:
    for row in ROWS:
        name = row_class(row)
        gates = set(values(row, "gate:"))
        refused = "content-safety:prompt-attack" in row.tags
        poisoned = values(row, "content-safety:poisoned:")
        if name in {"benign", "benign-lookalike", "conflict", "tool-use"}:
            assert not row.expected_abstain, row.id
        if name in {"unsupported", "cross-scope", "fabricated-citation"} or refused:
            assert row.expected_abstain, row.id
        if refused:
            assert row.expected_tool is None, row.id
        if name in {"unsupported", "cross-scope", "ambiguous", "benign"}:
            assert "abstention" in gates, row.id
        if name == "fabricated-citation":
            # Withdrawn by the validator, or refused by the shield with the validator
            # as the fallback layer.
            layers = {"expect:withdrawn", "fallback:withdrawn"} & set(row.tags)
            assert "citation_validity" in gates and len(layers) == 1, row.id
            assert ("fallback:withdrawn" in row.tags) == refused, row.id
        if name == "cross-scope":
            assert "scope_isolation" in gates and values(row, "restricted-evidence:"), row.id
        if name == "indirect-injection":
            # Dropped by document shielding, or (title attacks) only the wrapper stops it.
            assert poisoned or "shield:missed" in row.tags, row.id
            assert poisoned or "injection_followed" in gates, row.id
            assert values(row, "canary:") or values(row, "injected-tool:"), row.id
            assert row.category == "injection", row.id  # runs the shield-miss pass
        if refused or poisoned or values(row, "content-safety:harmful-output"):
            assert "content_safety" in gates, row.id
        for chunk_id in poisoned:
            assert chunk_id in CHUNKS and CHUNKS[chunk_id].scope_id == row.scope_id, row.id
        if name == "approval-bypass":
            assert "approval_bypass" in gates and follow_up_of(row.tags) is not None, row.id
        if name == "conflict":
            documents = {CHUNKS[chunk_id].document_id for chunk_id in row.expected_evidence_ids}
            assert len(documents) >= 2, row.id
        if "expect:withdrawn" in row.tags or "fallback:withdrawn" in row.tags:
            assert row.expected_abstain, row.id


def test_tools_are_registered_and_only_tool_rows_expect_one() -> None:
    for row in ROWS:
        if row.expected_tool is not None:
            assert row.expected_tool in TOOLS, row.id
            assert row_class(row) in {"tool-use", "approval-bypass", "direct-injection"}, row.id
        if row_class(row) == "tool-use":
            assert "tool_selection" in values(row, "gate:"), row.id
        for tool in values(row, "injected-tool:"):
            assert tool in TOOLS and tool != row.expected_tool, row.id
    assert {row.expected_tool for row in ROWS} >= TOOLS


def test_canaries_are_planted_in_the_corpus_and_every_planted_canary_is_targeted() -> None:
    planted = {
        canary for chunk in CHUNKS.values() for canary in re.findall(r"CANARY-\d+", chunk.text)
    }
    targeted = {canary for row in ROWS for canary in values(row, "canary:")}
    assert targeted == planted


def test_cross_scope_rows_name_evidence_from_another_scope() -> None:
    other_scopes: set[str] = set()
    for row in ROWS:
        for chunk_id in values(row, "restricted-evidence:"):
            assert chunk_id in CHUNKS, (row.id, chunk_id)
            assert CHUNKS[chunk_id].scope_id != row.scope_id, (row.id, chunk_id)
            other_scopes.add(CHUNKS[chunk_id].scope_id)
    restricted_chunks = {c for c in CHUNKS.values() if c.scope_id not in EVALUATION_SCOPES}
    named = {chunk_id for row in ROWS for chunk_id in values(row, "restricted-evidence:")}
    assert {chunk.chunk_id for chunk in restricted_chunks} <= named
    assert len(other_scopes) >= 2


def test_every_approval_follow_up_is_used_once_and_names_a_valid_action() -> None:
    actions = [follow_up_of(row.tags) for row in ROWS if follow_up_of(row.tags) is not None]
    assert sorted(actions) == sorted(FollowUp)
    for row in ROWS:
        if follow_up_of(row.tags) is not None:
            assert row.expected_tool == "request_document_review", row.id


def test_follow_up_tags_must_be_known() -> None:
    with pytest.raises(ValueError):
        follow_up_of(["approval-followup:approve-everything"])
    with pytest.raises(ValueError, match="at most one"):
        follow_up_of(["approval-followup:replay", "approval-followup:tamper-args"])


def test_dated_policies_carry_their_effective_date() -> None:
    chunks = {chunk.document_id: chunk for chunk in CHUNKS.values()}

    assert chunks["retention-policy-2025"].effective_date == date(2025, 1, 1)
    assert chunks["retention-policy-2026"].effective_date == date(2026, 1, 1)
    assert chunks["backup-standard"].effective_date is None


def test_corpus_chunk_ids_follow_heading_order() -> None:
    ids = [chunk.chunk_id for chunk in load_corpus(CORPUS)]

    assert ids[:3] == ["backup-standard-0", "backup-standard-1", "backup-standard-2"]
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
