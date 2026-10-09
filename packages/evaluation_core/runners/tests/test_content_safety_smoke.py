"""The content_safety smoke gate fails when any part of the screening wiring breaks."""

import asyncio
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from accelerator.agent_core.workflows import grounded_answer
from accelerator.security_core.content_safety import (
    ContentSafetyUnavailableError,
    HarmCategory,
    ScreenedDocument,
    ShieldResult,
    TextAnalysis,
)

from ...datasets import load_dataset
from ..corpus import load_corpus
from ..offline import OfflineContentSafetyChecker
from ..smoke import (
    HARD_GATES,
    EvaluationResult,
    GateName,
    RowOutcome,
    measure_smoke,
)

ROOT = Path(__file__).resolve().parents[4]
DATASET = ROOT / "evaluations/example-datasets/smoke.jsonl"
CORPUS = ROOT / "tests/fixtures/retrieval/corpus"
PROMPT_ATTACK_ROWS = {"smoke-injection-003", "smoke-injection-004", "smoke-injection-005"}


def measure() -> tuple[EvaluationResult, tuple[RowOutcome, ...]]:
    return asyncio.run(measure_smoke(load_dataset(DATASET), load_corpus(CORPUS)))


def failed(outcomes: tuple[RowOutcome, ...]) -> set[str]:
    return {o.row_id for o in outcomes if not o.checks[GateName.CONTENT_SAFETY]}


def test_content_safety_is_a_hard_gate_that_passes_on_the_product() -> None:
    result, _ = measure()

    assert GateName.CONTENT_SAFETY in HARD_GATES
    assert result.metrics["content_safety"] == 1.0
    assert "content_safety" not in result.hard_failures


def test_the_dataset_covers_every_content_safety_case() -> None:
    tags = {tag for row in load_dataset(DATASET) for tag in row.tags}

    assert "content-safety:prompt-attack" in tags
    assert "content-safety:harmful-output" in tags
    assert any(tag.startswith("content-safety:poisoned:") for tag in tags)
    assert "content-safety:below-threshold" in tags


def test_a_blind_prompt_shield_fails_the_jailbreak_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    original = OfflineContentSafetyChecker.shield_prompt

    async def blind(self: Any, prompt: str, documents: Any, **kw: Any) -> ShieldResult:
        verdict = await original(self, prompt, documents, **kw)
        return ShieldResult(False, verdict.attacked_document_ids)

    monkeypatch.setattr(OfflineContentSafetyChecker, "shield_prompt", blind)
    result, outcomes = measure()

    assert "content_safety" in result.hard_failures
    assert PROMPT_ATTACK_ROWS <= failed(outcomes)


def test_keeping_flagged_chunks_fails_the_poisoned_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    async def keep_all(
        self: Any, prompt: str, documents: Sequence[ScreenedDocument], **kw: Any
    ) -> ShieldResult:
        return ShieldResult(OfflineContentSafetyChecker.is_attack(prompt), ())

    monkeypatch.setattr(OfflineContentSafetyChecker, "shield_prompt", keep_all)
    result, outcomes = measure()

    assert "content_safety" in result.hard_failures
    assert "smoke-injection-006" in failed(outcomes)


def test_skipping_output_screening_fails_the_harmful_output_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def safe(self: Any, text: str, **kw: Any) -> TextAnalysis:
        return TextAnalysis(dict.fromkeys(HarmCategory, 0))

    monkeypatch.setattr(OfflineContentSafetyChecker, "analyze_text", safe)
    result, outcomes = measure()

    assert "content_safety" in result.hard_failures
    assert failed(outcomes) == {"smoke-safety-001"}


def test_an_over_eager_checker_fails_on_false_positive_refusals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def flag_everything(
        self: Any, prompt: str, documents: Any, *, deadline_utc: datetime | None = None
    ) -> ShieldResult:
        return ShieldResult(True, ())

    monkeypatch.setattr(OfflineContentSafetyChecker, "shield_prompt", flag_everything)
    result, outcomes = measure()

    assert "content_safety" in result.hard_failures
    benign = {"smoke-factual-001", "smoke-safety-002", "smoke-unsupported-001"}
    assert benign <= failed(outcomes)


def test_dropping_benign_chunks_is_a_false_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    async def drop_all(
        self: Any, prompt: str, documents: Sequence[ScreenedDocument], **kw: Any
    ) -> ShieldResult:
        return ShieldResult(False, tuple(d.document_id for d in documents))

    monkeypatch.setattr(OfflineContentSafetyChecker, "shield_prompt", drop_all)
    _, outcomes = measure()

    assert "smoke-factual-001" in failed(outcomes)


def test_an_unavailable_checker_refuses_and_fails_the_gate_without_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def down(self: Any, *args: Any, **kw: Any) -> Any:
        raise ContentSafetyUnavailableError("timeout")

    monkeypatch.setattr(OfflineContentSafetyChecker, "analyze_text", down)
    result, outcomes = measure()

    assert "content_safety" in result.hard_failures
    assert all(outcome.error is None for outcome in outcomes)
    # Fail closed: nothing that reached output screening was answered.
    assert result.metrics["abstention"] < 1.0


def test_an_unscreened_workflow_fails_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    original = grounded_answer.GroundedAnswerWorkflow.__init__

    def unscreened(self: Any, **kwargs: Any) -> None:
        kwargs["content_safety_checker"] = None
        original(self, **kwargs)

    monkeypatch.setattr(grounded_answer.GroundedAnswerWorkflow, "__init__", unscreened)
    result, outcomes = measure()

    assert "content_safety" in result.hard_failures
    assert PROMPT_ATTACK_ROWS | {"smoke-safety-001", "smoke-injection-006"} <= failed(outcomes)
