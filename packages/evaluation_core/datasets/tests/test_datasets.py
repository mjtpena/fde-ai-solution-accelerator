import json
from pathlib import Path

import pytest

from .. import DatasetRow, DatasetValidationError, load_dataset


ROOT = Path(__file__).resolve().parents[4]
CATEGORIES = ("factual", "synthesis", "conflict", "unsupported", "tool_selection", "injection")


def valid_row() -> dict[str, object]:
    return {
        "id": "q-0001",
        "category": "factual",
        "query": "What does the source say?",
        "scope_id": "scope-1",
        "expected_answer": "A supported answer",
        "expected_evidence_ids": ["chunk-1"],
        "expected_tool": None,
        "expected_abstain": False,
        "tags": ["smoke"],
    }


def write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_committed_schema_matches_loader_contract() -> None:
    schema = json.loads((ROOT / "contracts/evaluation/dataset.schema.json").read_text("utf-8"))
    assert schema.pop("$schema") == "https://json-schema.org/draft/2020-12/schema"
    assert schema == DatasetRow.model_json_schema()


@pytest.mark.parametrize("category", CATEGORIES)
def test_loads_all_categories_with_typed_fields(tmp_path: Path, category: str) -> None:
    row = valid_row()
    row["category"] = category
    path = tmp_path / "dataset.jsonl"
    write_rows(path, [row])
    loaded = load_dataset(path)
    assert len(loaded) == 1
    assert isinstance(loaded[0], DatasetRow)
    assert loaded[0].model_dump() == row


def test_loads_nullable_expectations_and_empty_arrays(tmp_path: Path) -> None:
    row = valid_row()
    row.update(
        expected_answer=None, expected_tool="lookup", expected_evidence_ids=[],
        expected_abstain=True, tags=[],
    )
    path = tmp_path / "dataset.jsonl"
    write_rows(path, [row])
    assert load_dataset(str(path))[0].model_dump() == row


@pytest.mark.parametrize("field", tuple(valid_row()))
def test_rejects_missing_fields_including_nullable_fields(tmp_path: Path, field: str) -> None:
    row = valid_row()
    del row[field]
    path = tmp_path / "dataset.jsonl"
    write_rows(path, [row])
    with pytest.raises(DatasetValidationError, match=f"{field}: Field required"):
        load_dataset(path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", 1), ("query", None), ("scope_id", 42), ("category", "unknown"),
        ("expected_answer", False), ("expected_tool", 1), ("expected_abstain", "false"),
        ("expected_abstain", 0), ("expected_evidence_ids", "chunk-1"),
        ("expected_evidence_ids", [1]), ("tags", None), ("tags", [True]),
        ("unexpected", "extra"),
    ],
)
def test_rejects_invalid_fields_without_coercion(
    tmp_path: Path, field: str, value: object
) -> None:
    row = valid_row()
    row[field] = value
    path = tmp_path / "dataset.jsonl"
    write_rows(path, [row])
    with pytest.raises(DatasetValidationError, match=field):
        load_dataset(path)


@pytest.mark.parametrize("invalid_line", [b"{", b"\n", b"\xff", b"null", b"[]", b'"row"'])
def test_invalid_later_row_fails_whole_dataset_with_location(
    tmp_path: Path, invalid_line: bytes
) -> None:
    path = tmp_path / "dataset.jsonl"
    path.write_bytes((json.dumps(valid_row()) + "\n").encode() + invalid_line)
    with pytest.raises(DatasetValidationError) as caught:
        load_dataset(path)
    assert caught.value.path == path
    assert caught.value.line_number == 2
    assert str(caught.value).startswith(f"{path}:2:")
    assert caught.value.__cause__ is not None


def test_preserves_order_and_accepts_final_line_without_newline(tmp_path: Path) -> None:
    first, second = valid_row(), valid_row()
    second["id"] = "q-0002"
    path = tmp_path / "dataset.jsonl"
    path.write_text(json.dumps(first) + "\n" + json.dumps(second), encoding="utf-8")
    assert [row.id for row in load_dataset(path)] == ["q-0001", "q-0002"]


def test_missing_dataset_propagates_io_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_dataset(tmp_path / "missing.jsonl")


def test_repository_datasets_are_schema_valid() -> None:
    for directory in (ROOT / "evaluations", ROOT / "contracts/evaluation"):
        for path in sorted(directory.rglob("*.jsonl")):
            load_dataset(path)
