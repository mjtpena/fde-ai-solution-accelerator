"""The full-evaluation dataset is grounded in the fixture corpus and ready to run.

It cannot run here (it needs Azure), so these checks stand in for the first live run:
every reference answer is supported by the chunks it names, in the evaluated scope,
and the gates it will be judged against load and are as strict as documented.
"""

import re
from collections import Counter
from pathlib import Path

from ...datasets import load_dataset
from ...runners.corpus import load_corpus
from ...runners.offline import OfflineContentSafetyChecker, terms
from ...runners.smoke import EVALUATION_SCOPES
from ..full import load_gates

ROOT = Path(__file__).resolve().parents[4]
DATASET = ROOT / "evaluations/example-datasets/full.jsonl"
SMOKE = ROOT / "evaluations/example-datasets/smoke.jsonl"
GATES = ROOT / "evaluations/full-gates.example.yml"
CORPUS = ROOT / "tests/fixtures/retrieval/corpus"
ROWS = load_dataset(DATASET)
CHUNKS = {chunk.chunk_id: chunk for chunk in load_corpus(CORPUS)}
KINDS = {"single-hop", "multi-hop", "numeric", "date", "comparison", "abstain", "injection"}
NUMBER = re.compile(r"\d+(?::\d+)?")
NUMBER_WORDS = {"one", "two", "three", "four", "five", "twice", "once"}


def kinds(tags: list[str]) -> set[str]:
    return {tag.removeprefix("type:") for tag in tags if tag.startswith("type:")}


def test_dataset_size_and_mix() -> None:
    assert len(ROWS) >= 40
    counts = Counter(kind for row in ROWS for kind in kinds(row.tags))
    assert counts["single-hop"] >= 25
    assert counts["multi-hop"] >= 5
    assert counts["numeric"] >= 20
    assert counts["date"] >= 6
    assert counts["comparison"] >= 4
    assert counts["abstain"] >= 7
    assert counts["injection"] >= 6


def test_ids_are_unique_and_distinct_from_the_smoke_suite() -> None:
    ids = [row.id for row in ROWS]
    assert len(ids) == len(set(ids))
    assert not set(ids) & {row.id for row in load_dataset(SMOKE)}
    for row in ROWS:
        assert re.fullmatch(r"full-[a-z]+-\d{3}", row.id), row.id
        assert "full" in row.tags and kinds(row.tags) and kinds(row.tags) <= KINDS, row.id
        assert row.expected_tool is None, row.id


def test_rows_are_in_the_evaluated_scope_and_name_existing_chunks() -> None:
    for row in ROWS:
        assert row.scope_id in EVALUATION_SCOPES, row.id
        for chunk_id in row.expected_evidence_ids:
            assert chunk_id in CHUNKS, (row.id, chunk_id)
            assert CHUNKS[chunk_id].scope_id == row.scope_id, (row.id, chunk_id)


def test_abstaining_rows_have_no_reference_and_answering_rows_do() -> None:
    for row in ROWS:
        abstains = "abstain" in kinds(row.tags)
        assert row.expected_abstain is abstains, row.id
        assert (row.expected_answer is None) is abstains, row.id
        assert (not row.expected_evidence_ids) is abstains, row.id
        if row.category == "unsupported":
            assert abstains, row.id


def unsupported_parts(answer: str, evidence_ids: list[str]) -> set[str]:
    """Numbers and words of ``answer`` that the named chunks do not support."""
    evidence = " ".join(
        f"{CHUNKS[chunk_id].document_title} {CHUNKS[chunk_id].text}" for chunk_id in evidence_ids
    )
    # Every figure, time and date comes from the evidence...
    missing = {number for number in NUMBER.findall(answer) if number not in evidence}
    missing |= (NUMBER_WORDS & terms(answer)) - terms(evidence)
    # ...and the answer is mostly the evidence's own words.
    answer_terms = terms(answer)
    unsupported = answer_terms - terms(evidence)
    if len(unsupported) / len(answer_terms) > 0.4:
        missing |= unsupported
    return missing


def test_every_reference_answer_is_supported_by_the_chunks_it_names() -> None:
    for row in ROWS:
        if row.expected_answer is not None:
            assert not unsupported_parts(row.expected_answer, row.expected_evidence_ids), row.id


def test_the_support_check_rejects_unsupported_answers() -> None:
    assert unsupported_parts("Incremental backups run every 6 hours.", ["backup-standard-0"])
    assert unsupported_parts("Incremental backups run every two hours.", ["backup-standard-0"])
    assert unsupported_parts(
        "Snapshots are replicated to a secondary region weekly.", ["backup-standard-0"]
    )
    assert not unsupported_parts("Incremental backups run every four hours.", ["backup-standard-0"])


def test_multi_hop_and_comparison_rows_need_more_than_one_fact() -> None:
    for row in ROWS:
        if "multi-hop" in kinds(row.tags):
            assert len(row.expected_evidence_ids) >= 2, row.id
        if "comparison" in kinds(row.tags):
            assert len(NUMBER.findall(row.expected_answer or "")) >= 2 or (
                len(row.expected_evidence_ids) >= 2
            ), row.id


def test_injection_rows_target_a_poisoned_document_without_quoting_its_canary() -> None:
    """A row naming a poisoned chunk expects content safety to drop it (ADR-0007): it
    abstains unless a clean chunk answers. Without one, the attack is in a title, which
    shielding never reads, and the row must still be answered."""
    for row in ROWS:
        if "injection" not in kinds(row.tags):
            continue
        dropped = [
            t.removeprefix("content-safety:poisoned:")
            for t in row.tags
            if t.startswith("content-safety:poisoned:")
        ]
        for chunk_id in dropped:
            assert OfflineContentSafetyChecker.is_attack(CHUNKS[chunk_id].text), row.id
            assert chunk_id not in row.expected_evidence_ids, row.id
        if not dropped:
            titles = " ".join(CHUNKS[c].document_title for c in row.expected_evidence_ids)
            assert "</evidence>" in titles and not row.expected_abstain, row.id
        documents = {CHUNKS[c].document_id for c in dropped}
        text = " ".join(c.text for c in CHUNKS.values() if c.document_id in documents)
        for tag in row.tags:
            if tag.startswith("canary:"):
                canary = tag.removeprefix("canary:")
                assert canary in text, row.id
                assert canary not in (row.expected_answer or ""), row.id


def test_restricted_abstain_rows_name_another_scopes_chunk() -> None:
    restricted = [
        row for row in ROWS if any(t.startswith("restricted-evidence:") for t in row.tags)
    ]
    assert len(restricted) >= 3
    for row in restricted:
        assert row.expected_abstain, row.id
        for tag in row.tags:
            if tag.startswith("restricted-evidence:"):
                chunk = CHUNKS[tag.removeprefix("restricted-evidence:")]
                assert chunk.scope_id not in EVALUATION_SCOPES, row.id


def test_shipped_gates_are_strict() -> None:
    gates = load_gates(GATES)

    assert set(gates.metrics) == {"groundedness", "relevance", "retrieval", "completeness"}
    for gate in gates.metrics.values():
        assert gate.min_mean >= 4.0
        assert gate.pass_score >= 4.0
        assert gate.min_pass_rate >= 0.9
    assert gates.min_abstention_accuracy >= 0.95
