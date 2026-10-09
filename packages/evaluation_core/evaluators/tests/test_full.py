import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError

from accelerator.security_core.data_boundaries.context import ExecutionContext

from .. import FoundryEvaluatorAdapters, MetricName
from ..full import (
    FullEvaluationRuntime,
    FullEvaluationSettings,
    load_runtime,
    main,
    run_full,
)
from ..settings import FoundryEvaluatorSettings
from ...datasets import DatasetRow

if TYPE_CHECKING:
    from packages.agent_core.workflows.grounded_answer import (
        Abstention,
        CitationSource,
        GroundedAnswerResult,
        GroundedAnswerWorkflow,
        RetrievedEvidenceContext,
    )
else:
    from accelerator.agent_core.workflows.grounded_answer import (
        Abstention,
        CitationSource,
        GroundedAnswerResult,
        GroundedAnswerWorkflow,
        RetrievedEvidenceContext,
    )


def row(row_id: str = "row", expected_answer: str | None = "Reference") -> DatasetRow:
    return DatasetRow(
        id=row_id,
        category="factual",
        query="Question",
        scope_id="untrusted-dataset-scope",
        expected_answer=expected_answer,
        expected_evidence_ids=["chunk"],
        expected_tool=None,
        expected_abstain=False,
        tags=[],
    )


def context() -> ExecutionContext:
    return ExecutionContext(
        correlation_id="evaluation",
        user_id="trusted-user",
        roles=frozenset({"reader"}),
        scope_ids=frozenset({"trusted-scope"}),
        deadline_utc=datetime(2099, 1, 1, tzinfo=UTC),
    )


def answer() -> GroundedAnswerResult:
    return GroundedAnswerResult(
        status="answered",
        answer="Actual workflow answer",
        citations=("chunk",),
        citation_sources=(CitationSource("chunk", "Title", "https://example.invalid"),),
        abstention=None,
        evaluation_context=(RetrievedEvidenceContext("chunk", "Actual same-turn text"),),
    )


class Workflow:
    def __init__(self, result: GroundedAnswerResult | None = None) -> None:
        self.result = result if result is not None else answer()
        self.calls: list[tuple[str, ExecutionContext]] = []

    async def run(self, workflow_input: str, ctx: ExecutionContext) -> GroundedAnswerResult:
        self.calls.append((workflow_input, ctx))
        return self.result


class Judge:
    def __init__(self) -> None:
        self.calls: list[dict[str, str | None]] = []

    def evaluate_available(
        self, *, query: str, response: str, context: str, expected_answer: str | None
    ) -> dict[MetricName, float]:
        self.calls.append(
            dict(query=query, response=response, context=context, expected_answer=expected_answer)
        )
        scores: dict[MetricName, float] = {
            "groundedness": float(len(self.calls) + 2),
            "relevance": 4,
            "retrieval": 5,
        }
        if expected_answer is not None:
            scores["completeness"] = 4
        return scores

    def __enter__(self) -> "Judge":
        return self

    def __exit__(self, *_: object) -> None:
        pass


@pytest.mark.asyncio
async def test_actual_response_context_and_reference_are_aggregated_without_scope_widening() -> (
    None
):
    workflow = Workflow()
    judge = Judge()
    trusted = context()
    result = await run_full(
        [row("one"), row("two", None)], FullEvaluationRuntime(workflow, trusted), judge
    )
    assert workflow.calls == [("Question", trusted), ("Question", trusted)]
    assert judge.calls[0] == {
        "query": "Question",
        "response": "Actual workflow answer",
        "context": "Actual same-turn text",
        "expected_answer": "Reference",
    }
    assert result.metrics["groundedness"] == 3.5
    assert result.metric_counts == {
        "groundedness": 2,
        "relevance": 2,
        "retrieval": 2,
        "completeness": 1,
    }
    assert "completeness" not in result.rows[1].metrics
    assert "Actual workflow answer" not in result.model_dump_json()
    assert "Actual same-turn text" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_abstention_has_no_invented_scores() -> None:
    result = GroundedAnswerResult(
        status="abstained",
        answer=None,
        citations=(),
        citation_sources=(),
        abstention=Abstention("Insufficient evidence", ()),
        evaluation_context=(),
    )
    judge = Judge()
    measured = await run_full([row()], FullEvaluationRuntime(Workflow(result), context()), judge)
    assert measured.metrics == {}
    assert measured.metric_counts == {}
    assert judge.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        replace(answer(), evaluation_context=None),
        replace(answer(), citations=("fabricated",)),
        replace(answer(), citation_sources=()),
        replace(answer(), answer=None),
    ],
)
async def test_invalid_or_missing_capture_fails_before_judge(result: GroundedAnswerResult) -> None:
    judge = Judge()
    with pytest.raises(ValueError):
        await run_full([row()], FullEvaluationRuntime(Workflow(result), context()), judge)
    assert not judge.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [[], [row(), row()]])
async def test_empty_or_duplicate_dataset_fails(rows: list[DatasetRow]) -> None:
    with pytest.raises(ValueError):
        await run_full(rows, FullEvaluationRuntime(Workflow(), context()), Judge())


@pytest.mark.asyncio
async def test_sdk_failure_propagates_without_a_partial_success() -> None:
    class FailingJudge(Judge):
        def evaluate_available(
            self, *, query: str, response: str, context: str, expected_answer: str | None
        ) -> dict[MetricName, float]:
            raise RuntimeError("SDK boundary failed")

    with pytest.raises(RuntimeError, match="SDK boundary failed"):
        await run_full([row()], FullEvaluationRuntime(Workflow(), context()), FailingJudge())


@pytest.mark.asyncio
async def test_workflow_failure_propagates() -> None:
    class FailingWorkflow(Workflow):
        async def run(self, workflow_input: str, ctx: ExecutionContext) -> GroundedAnswerResult:
            raise RuntimeError("workflow boundary failed")

    judge = Judge()
    with pytest.raises(RuntimeError, match="workflow boundary failed"):
        await run_full([row()], FullEvaluationRuntime(FailingWorkflow(), context()), judge)
    assert not judge.calls


@dataclass(frozen=True)
class Evidence:
    chunk_id: str = "chunk"
    text: str = "Actual same-turn text"
    document_title: str = "Title"
    source_uri: str = "https://example.invalid"


@dataclass(frozen=True)
class Decision:
    sufficient: bool = True
    reason: str = "Supported"
    evidence_ids: tuple[str, ...] = ("chunk",)


@dataclass(frozen=True)
class Generated:
    answer: str = "Actual workflow answer"
    citations: tuple[str, ...] = ("chunk",)


@pytest.mark.asyncio
async def test_real_workflow_captures_one_retrieval_and_judge_runs_off_event_loop() -> None:
    retrievals: list[ExecutionContext] = []

    class Retriever:
        async def retrieve(self, req: str, ctx: ExecutionContext) -> tuple[Evidence, ...]:
            assert req == "Question"
            retrievals.append(ctx)
            return (Evidence(),)

    class Checker:
        async def evaluate(self, evidence: Sequence[Evidence]) -> Decision:
            return Decision()

    class Generator:
        async def generate(self, query: str, evidence: Sequence[Evidence]) -> Generated:
            return Generated()

    class Validator:
        def validate(self, citations: Sequence[str], retrieved_chunk_ids: frozenset[str]) -> None:
            assert set(citations) <= retrieved_chunk_ids

    class ThreadJudge(Judge):
        def evaluate_available(
            self, *, query: str, response: str, context: str, expected_answer: str | None
        ) -> dict[MetricName, float]:
            with pytest.raises(RuntimeError, match="no running event loop"):
                asyncio.get_running_loop()
            return super().evaluate_available(
                query=query, response=response, context=context, expected_answer=expected_answer
            )

    workflow = GroundedAnswerWorkflow(
        retriever=Retriever(),
        sufficiency_checker=Checker(),
        answer_generator=Generator(),
        citation_validator=Validator(),
        retrieval_request_factory=lambda query: query,
        capture_evaluation_context=True,
    )
    trusted = context()
    judge = ThreadJudge()
    await run_full([row()], FullEvaluationRuntime(workflow, trusted), judge)
    assert retrievals == [trusted]
    assert judge.calls[0]["context"] == Evidence().text


def test_settings_require_explicit_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "EVALUATION_DATASET",
        "EVALUATION_WORKFLOW_FACTORY",
        "EVALUATION_JUDGE_AZURE_ENDPOINT",
        "EVALUATION_JUDGE_AZURE_DEPLOYMENT",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValidationError):
        FullEvaluationSettings.model_validate({})
    with pytest.raises(ValidationError):
        FoundryEvaluatorSettings.model_validate({})
    with pytest.raises(ValidationError):
        FullEvaluationSettings.model_validate(
            {"dataset": "rows.jsonl", "workflow_factory": "../bad"}
        )


def test_runtime_factory_must_return_typed_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.full_test_factory", lambda: object(), raising=False)
    with pytest.raises(TypeError, match="FullEvaluationRuntime"):
        load_runtime("builtins:full_test_factory")


def test_cli_loads_dataset_and_invokes_real_runner_with_offline_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    dataset = tmp_path / "rows.jsonl"
    dataset.write_text(json.dumps(row().model_dump()) + "\n", encoding="utf-8")
    workflow = Workflow()
    judge = Judge()
    monkeypatch.setenv("EVALUATION_DATASET", str(dataset))
    monkeypatch.setenv("EVALUATION_WORKFLOW_FACTORY", "builtins:full_test_factory")
    monkeypatch.setenv("EVALUATION_JUDGE_AZURE_ENDPOINT", "https://judge.example.invalid")
    monkeypatch.setenv("EVALUATION_JUDGE_AZURE_DEPLOYMENT", "judge")
    monkeypatch.setattr(
        "builtins.full_test_factory",
        lambda: FullEvaluationRuntime(workflow, context()),
        raising=False,
    )
    monkeypatch.setattr(FoundryEvaluatorAdapters, "from_settings", lambda settings: judge)
    monkeypatch.setattr(sys, "argv", ["eval-full"])
    with caplog.at_level("INFO"):
        main()
    assert len(workflow.calls) == len(judge.calls) == 1
    assert "Full evaluation measured 1 rows" in caplog.text
    assert "Actual same-turn text" not in caplog.text
    assert "Actual workflow answer" not in caplog.text


def test_make_target_invokes_full_runner() -> None:
    makefile = Path(__file__).resolve().parents[4] / "Makefile"
    assert (
        "eval-full:\n\tuv run --all-packages python -m accelerator.evaluation_core.evaluators"
    ) in makefile.read_text()


def test_make_eval_full_executes_offline_sdk_boundaries(tmp_path: Path) -> None:
    """The subprocess is a test fixture, never a shipped offline full-eval mode."""
    dataset = tmp_path / "rows.jsonl"
    dataset.write_text(json.dumps(row().model_dump()) + "\n", encoding="utf-8")
    (tmp_path / "offline_composition.py").write_text(
        """
from datetime import UTC, datetime
from accelerator.agent_core.workflows.grounded_answer import (
    CitationSource, GroundedAnswerResult, RetrievedEvidenceContext,
)
from accelerator.evaluation_core.evaluators.full import FullEvaluationRuntime
from accelerator.evaluation_core.evaluators.foundry import FoundryEvaluatorAdapters
from accelerator.security_core.data_boundaries.context import ExecutionContext

class Credential:
    def get_token(self, *args, **kwargs):
        raise AssertionError("Offline test must not request a real token")

class Workflow:
    async def run(self, query, ctx):
        assert query == "Question"
        assert ctx.scope_ids == frozenset({"trusted-scope"})
        return GroundedAnswerResult(
            status="answered", answer="Actual workflow answer",
            citations=("chunk",),
            citation_sources=(CitationSource("chunk", "Title", "https://example.invalid"),),
            abstention=None,
            evaluation_context=(RetrievedEvidenceContext("chunk", "Actual same-turn text"),),
        )

def compose():
    original = FoundryEvaluatorAdapters.from_settings
    def sdk_factory(name):
        def build(**kwargs):
            assert kwargs["model_config"]["azure_deployment"] == "offline-test-judge"
            def evaluate(**inputs):
                if name == "groundedness":
                    assert inputs["response"] == "Actual workflow answer"
                    assert inputs["context"] == "Actual same-turn text"
                return {name: 4}
            return evaluate
        return build
    factories = {name: sdk_factory(name) for name in (
        "groundedness", "relevance", "retrieval", "completeness"
    )}
    FoundryEvaluatorAdapters.from_settings = classmethod(
        lambda cls, settings: original(
            settings, credential=Credential(), factories=factories
        )
    )
    return FullEvaluationRuntime(Workflow(), ExecutionContext(
        correlation_id="offline-test", user_id="trusted-user",
        roles=frozenset({"reader"}), scope_ids=frozenset({"trusted-scope"}),
        deadline_utc=datetime(2099, 1, 1, tzinfo=UTC),
    ))
""",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(tmp_path) + os.pathsep + env.get("PYTHONPATH", ""),
            "EVALUATION_DATASET": str(dataset),
            "EVALUATION_WORKFLOW_FACTORY": "offline_composition:compose",
            "EVALUATION_JUDGE_AZURE_ENDPOINT": "https://offline-test.example.invalid",
            "EVALUATION_JUDGE_AZURE_DEPLOYMENT": "offline-test-judge",
        }
    )
    result = subprocess.run(
        ["make", "eval-full"],
        cwd=Path(__file__).resolve().parents[4],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Full evaluation measured 1 rows" in result.stderr
    assert "'completeness': 4.0" in result.stderr
    assert "Actual same-turn text" not in result.stderr
    assert "Actual workflow answer" not in result.stderr


class FailingWorkflow:
    async def run(self, workflow_input: str, ctx: ExecutionContext) -> GroundedAnswerResult:
        raise RuntimeError("search unavailable")


@pytest.mark.parametrize("workflow", [Workflow(), FailingWorkflow()])
def test_cli_closes_the_runtime_on_the_run_loop_even_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workflow: Any
) -> None:
    dataset = tmp_path / "rows.jsonl"
    dataset.write_text(json.dumps(row().model_dump()) + "\n", encoding="utf-8")
    closed: list[bool] = []

    async def close() -> None:
        asyncio.get_running_loop()  # awaited inside the evaluation's event loop
        closed.append(True)

    monkeypatch.setenv("EVALUATION_DATASET", str(dataset))
    monkeypatch.setenv("EVALUATION_WORKFLOW_FACTORY", "builtins:full_test_factory")
    monkeypatch.setenv("EVALUATION_JUDGE_AZURE_ENDPOINT", "https://judge.example.invalid")
    monkeypatch.setenv("EVALUATION_JUDGE_AZURE_DEPLOYMENT", "judge")
    monkeypatch.setattr(
        "builtins.full_test_factory",
        lambda: FullEvaluationRuntime(workflow, context(), close=close),
        raising=False,
    )
    monkeypatch.setattr(FoundryEvaluatorAdapters, "from_settings", lambda settings: Judge())
    monkeypatch.setattr(sys, "argv", ["eval-full"])
    if isinstance(workflow, FailingWorkflow):
        with pytest.raises(RuntimeError, match="search unavailable"):
            main()
    else:
        main()
    assert closed == [True]


def test_make_eval_full_defaults_to_the_api_composition() -> None:
    makefile = (Path(__file__).resolve().parents[4] / "Makefile").read_text()
    assert (
        "EVALUATION_WORKFLOW_FACTORY ?= "
        "accelerator.infrastructure.evaluation:create_full_evaluation_runtime"
    ) in makefile
    assert "export EVALUATION_WORKFLOW_FACTORY EVALUATION_DATASET" in makefile
