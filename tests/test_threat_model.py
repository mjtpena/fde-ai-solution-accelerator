"""Consistency checks for the machine-readable threat model in ``threat-model/``."""

import ast
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
# A generated project (marked by ACCELERATOR_VERSION) does not receive the
# accelerator's infrastructure, deployment workflows or generator; it owns its threat
# model and refreshes these references when it adds its own.
GENERATED_PROJECT = (ROOT / "ACCELERATOR_VERSION").exists()
NOT_GENERATED = (
    "infrastructure/",
    "scripts/new_project.py",
    ".github/workflows/deploy-dev.yml",
    ".github/workflows/bicep-validation.yml",
)
THREAT_MODEL = ROOT / "threat-model"
SMOKE_RUNNER = ROOT / "packages" / "evaluation_core" / "runners" / "smoke.py"

THREAT_ID = re.compile(r"T-\d{3}")
CONTROL_ID = re.compile(r"C-\d{3}")
STRIDE = {
    "spoofing",
    "tampering",
    "repudiation",
    "information_disclosure",
    "denial_of_service",
    "elevation_of_privilege",
}
RATINGS = {"low", "medium", "high"}
CONTROL_TYPES = {"preventive", "detective", "corrective"}
STATUSES = {"implemented", "partial", "planned"}
THREAT_KEYS = {
    "id", "title", "description", "stride", "assets", "likelihood", "impact", "controls"
}
CONTROL_KEYS = {"id", "title", "description", "type", "status", "implemented_in", "verified_by"}
OPTIONAL_CONTROL_KEYS = {"gates", "notes"}


def _load(name: str, key: str) -> list[dict[str, Any]]:
    document = yaml.safe_load((THREAT_MODEL / name).read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    entries = document[key]
    assert isinstance(entries, list)
    assert entries
    return entries


def _gate_names() -> set[str]:
    """Read ``GateName`` values without importing the evaluation runtime."""
    tree = ast.parse(SMOKE_RUNNER.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "GateName":
            return {
                statement.value.value
                for statement in node.body
                if isinstance(statement, ast.Assign)
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            }
    raise AssertionError("GateName not found in the smoke runner")


THREATS = _load("threats.yml", "threats")
CONTROLS = _load("controls.yml", "controls")
CONTROLS_BY_ID = {control["id"]: control for control in CONTROLS}


@pytest.mark.parametrize(
    ("entries", "pattern"), [(THREATS, THREAT_ID), (CONTROLS, CONTROL_ID)], ids=["T", "C"]
)
def test_ids_are_unique_and_well_formed(
    entries: list[dict[str, Any]], pattern: re.Pattern[str]
) -> None:
    ids = [entry["id"] for entry in entries]
    assert all(pattern.fullmatch(value) for value in ids), ids
    duplicates = [value for value, count in Counter(ids).items() if count > 1]
    assert not duplicates


@pytest.mark.parametrize("threat", THREATS, ids=lambda threat: threat["id"])
def test_threat_entries_follow_the_schema(threat: dict[str, Any]) -> None:
    assert set(threat) == THREAT_KEYS
    assert threat["title"].strip()
    assert threat["description"].strip()
    assert threat["stride"] in STRIDE
    assert threat["likelihood"] in RATINGS
    assert threat["impact"] in RATINGS
    assert threat["assets"]
    assert all(isinstance(asset, str) and asset.strip() for asset in threat["assets"])


@pytest.mark.parametrize("threat", THREATS, ids=lambda threat: threat["id"])
def test_every_threat_references_existing_controls(threat: dict[str, Any]) -> None:
    controls = threat["controls"]
    assert controls
    assert len(controls) == len(set(controls))
    missing = [control for control in controls if control not in CONTROLS_BY_ID]
    assert not missing


def test_every_control_mitigates_some_threat() -> None:
    referenced = {control for threat in THREATS for control in threat["controls"]}
    assert set(CONTROLS_BY_ID) - referenced == set()


@pytest.mark.parametrize("control", CONTROLS, ids=lambda control: control["id"])
def test_control_entries_follow_the_schema(control: dict[str, Any]) -> None:
    keys = set(control)
    assert CONTROL_KEYS <= keys
    assert keys <= CONTROL_KEYS | OPTIONAL_CONTROL_KEYS
    assert control["title"].strip()
    assert control["description"].strip()
    assert control["type"] in CONTROL_TYPES
    assert control["status"] in STATUSES
    if control["status"] != "implemented":
        assert control.get("notes", "").strip(), "partial/planned controls need notes"
    if control["status"] != "planned":
        assert control["implemented_in"], "implemented/partial controls need code"
        assert control["verified_by"] or control.get("gates"), "controls need verification"


@pytest.mark.parametrize("control", CONTROLS, ids=lambda control: control["id"])
@pytest.mark.parametrize("field", ["implemented_in", "verified_by"])
def test_control_paths_exist(control: dict[str, Any], field: str) -> None:
    paths = control[field]
    assert isinstance(paths, list)
    assert len(paths) == len(set(paths))
    for path in paths:
        assert isinstance(path, str)
        assert not Path(path).is_absolute(), path
        resolved = (ROOT / path).resolve()
        assert resolved.is_relative_to(ROOT), path
        # The generator downgrades controls whose implementation it did not copy, so
        # only a control that no longer claims to be implemented may cite them.
        if (
            GENERATED_PROJECT
            and control["status"] != "implemented"
            and path.startswith(NOT_GENERATED)
        ):
            continue
        assert resolved.exists(), f"{control['id']} {field}: {path} does not exist"


@pytest.mark.parametrize("control", CONTROLS, ids=lambda control: control["id"])
def test_controls_in_force_have_code_in_this_repository(control: dict[str, Any]) -> None:
    if control["status"] != "planned":
        assert any((ROOT / path).exists() for path in control["implemented_in"]), (
            f"{control['id']} is {control['status']} but none of its code is here"
        )


@pytest.mark.parametrize("control", CONTROLS, ids=lambda control: control["id"])
def test_control_gates_are_smoke_gate_names(control: dict[str, Any]) -> None:
    gates = control.get("gates", [])
    assert isinstance(gates, list)
    assert set(gates) <= _gate_names(), gates
