"""Credential-dependent full evaluation; no fixture answers or reporting API.

Besides the Foundry judge scores, the run measures abstention accuracy against each
row's ``expected_abstain`` and checks that no answer carries a row's ``canary:``
value. ``FullEvaluationGates`` (``evaluations/full-gates.example.yml``) turn those
into absolute release gates: a mean and a pass rate per judge metric, a minimum
abstention accuracy, and zero canary leaks.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat
from pydantic_settings import BaseSettings, SettingsConfigDict

from accelerator.agent_core.workflows.grounded_answer import GroundedAnswerResult
from accelerator.security_core.data_boundaries.context import ExecutionContext

from ..datasets import DatasetRow, load_dataset
from .foundry import FoundryEvaluatorAdapters, MetricName
from .settings import FoundryEvaluatorSettings


class EvaluationWorkflow(Protocol):
    async def run(self, workflow_input: str, ctx: ExecutionContext) -> GroundedAnswerResult: ...


class Judge(Protocol):
    def evaluate_available(
        self, *, query: str, response: str, context: str, expected_answer: str | None
    ) -> dict[MetricName, float]: ...


@dataclass(frozen=True)
class FullEvaluationRuntime:
    """Trusted server composition, never constructed from dataset scope IDs.

    The workflow must be built with capture_evaluation_context=True. Factories
    can use the existing AgentFactory and GroundedAnswerWorkflow public APIs.
    """

    workflow: EvaluationWorkflow
    context: ExecutionContext
    # Releases clients the factory opened; awaited on the run's event loop.
    close: Callable[[], Awaitable[None]] | None = None


class FullEvaluationSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVALUATION_", extra="ignore")

    dataset: Path
    workflow_factory: str = Field(pattern=r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*$")
    # Absolute release gates; without them the run only measures and logs.
    gates: Path | None = None


class RowEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    row_id: str
    status: Literal["answered", "abstained"]
    metrics: dict[MetricName, float]


class FullEvaluationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rows: tuple[RowEvaluation, ...]
    metrics: dict[MetricName, float]
    metric_counts: dict[MetricName, int]
    # Share of rows that abstained exactly when the dataset expected it.
    abstention_accuracy: float
    # Rows whose answer contained one of the row's ``canary:`` values: an injection
    # planted in the corpus was followed. Any entry fails the gates.
    canary_leaks: tuple[str, ...]


Score = Annotated[FiniteFloat, Field(ge=1.0, le=5.0)]
Fraction = Annotated[FiniteFloat, Field(ge=0.0, le=1.0)]


class JudgeMetricGate(BaseModel):
    """A judge metric passes when its mean and its share of passing rows are high enough."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    min_mean: Score
    pass_score: Score
    min_pass_rate: Fraction


class FullEvaluationGates(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    metrics: dict[MetricName, JudgeMetricGate] = Field(min_length=1)
    min_abstention_accuracy: Fraction


def load_gates(path: Path) -> FullEvaluationGates:
    return FullEvaluationGates.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def check_gates(result: FullEvaluationResult, gates: FullEvaluationGates) -> tuple[str, ...]:
    """Names of the failed gates; empty when the run passes.

    A gated metric with no judged row fails: a run where everything abstained must
    not pass the quality gates by measuring nothing.
    """
    failures: list[str] = []
    for name, gate in sorted(gates.metrics.items()):
        scores = [row.metrics[name] for row in result.rows if name in row.metrics]
        if not scores:
            failures.append(f"{name}.unmeasured")
            continue
        if sum(scores) / len(scores) < gate.min_mean:
            failures.append(f"{name}.mean")
        if sum(score >= gate.pass_score for score in scores) / len(scores) < gate.min_pass_rate:
            failures.append(f"{name}.pass_rate")
    if result.abstention_accuracy < gates.min_abstention_accuracy:
        failures.append("abstention_accuracy")
    if result.canary_leaks:
        failures.append("canary_leak")
    return tuple(failures)


async def run_full(
    rows: Sequence[DatasetRow], runtime: FullEvaluationRuntime, judge: Judge
) -> FullEvaluationResult:
    """Measure real workflow outputs and propagate any workflow/SDK failure."""
    if not rows:
        raise ValueError("Full evaluation requires a nonempty dataset.")
    if len({row.id for row in rows}) != len(rows):
        raise ValueError("Full evaluation requires unique dataset row IDs.")
    results: list[RowEvaluation] = []
    totals: dict[MetricName, float] = {}
    counts: dict[MetricName, int] = {}
    abstained_as_expected = 0
    canary_leaks: list[str] = []
    for row in rows:
        result = await runtime.workflow.run(row.query, runtime.context)
        evidence = result.evaluation_context
        if evidence is None:
            raise ValueError("Workflow must enable same-turn evaluation context capture.")
        if len({item.chunk_id for item in evidence}) != len(evidence):
            raise ValueError("Same-turn evidence contains duplicate chunk IDs.")
        if result.status == "abstained":
            if (
                result.answer is not None
                or result.citations
                or result.citation_sources
                or result.abstention is None
            ):
                raise ValueError("Invalid abstained workflow result.")
            scores: dict[MetricName, float] = {}
        elif result.status == "answered":
            if result.answer is None or result.abstention is not None or not evidence:
                raise ValueError("Answered workflow result requires an answer and evidence.")
            if not set(result.citations) <= {item.chunk_id for item in evidence}:
                raise ValueError("Citations must identify same-turn evidence.")
            if tuple(source.chunk_id for source in result.citation_sources) != result.citations:
                raise ValueError("Citation sources must match workflow citations.")
            scores = await asyncio.to_thread(
                judge.evaluate_available,
                query=row.query,
                response=result.answer,
                context="\n\n".join(item.text for item in evidence),
                expected_answer=row.expected_answer,
            )
        else:
            raise ValueError("Invalid workflow result status.")
        results.append(RowEvaluation(row_id=row.id, status=result.status, metrics=scores))
        abstained_as_expected += (result.status == "abstained") == row.expected_abstain
        canaries = [tag.removeprefix("canary:") for tag in row.tags if tag.startswith("canary:")]
        if any(canary and canary in (result.answer or "") for canary in canaries):
            canary_leaks.append(row.id)
        for name, score in scores.items():
            totals[name] = totals.get(name, 0.0) + score
            counts[name] = counts.get(name, 0) + 1
    return FullEvaluationResult(
        rows=tuple(results),
        metrics={name: total / counts[name] for name, total in totals.items()},
        metric_counts=counts,
        abstention_accuracy=abstained_as_expected / len(rows),
        canary_leaks=tuple(canary_leaks),
    )


async def _run_and_close(
    rows: Sequence[DatasetRow], runtime: FullEvaluationRuntime, judge: Judge
) -> FullEvaluationResult:
    try:
        return await run_full(rows, runtime, judge)
    finally:
        if runtime.close is not None:
            await runtime.close()


def load_runtime(factory_path: str) -> FullEvaluationRuntime:
    """Load only an explicitly configured trusted local composition factory."""
    module_name, function_name = factory_path.split(":")
    factory: Callable[[], object] = getattr(importlib.import_module(module_name), function_name)
    runtime = factory()
    if not isinstance(runtime, FullEvaluationRuntime):
        raise TypeError("Workflow factory must return FullEvaluationRuntime.")
    return runtime


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--workflow-factory")
    parser.add_argument("--gates", type=Path)
    args = parser.parse_args()
    overrides: dict[str, object] = {}
    if args.dataset is not None:
        overrides["dataset"] = args.dataset
    if args.workflow_factory is not None:
        overrides["workflow_factory"] = args.workflow_factory
    if args.gates is not None:
        overrides["gates"] = args.gates
    settings = FullEvaluationSettings.model_validate(overrides)
    # Invalid gates are a configuration error before any model or judge call.
    gates = load_gates(settings.gates) if settings.gates is not None else None
    judge_settings = FoundryEvaluatorSettings.model_validate({})
    rows = load_dataset(settings.dataset)
    runtime = load_runtime(settings.workflow_factory)
    try:
        adapters = FoundryEvaluatorAdapters.from_settings(judge_settings)
    except BaseException:
        # The runtime already holds clients; release them if the judge cannot start.
        if runtime.close is not None:
            close = runtime.close

            async def release() -> None:
                await close()

            asyncio.run(release())
        raise
    with adapters as judge:
        result = asyncio.run(_run_and_close(rows, runtime, judge))
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    logger.info(
        "Full evaluation measured %d rows; metrics=%s counts=%s abstention_accuracy=%s "
        "canary_leaks=%s correlation_id=%s",
        len(result.rows),
        result.metrics,
        result.metric_counts,
        result.abstention_accuracy,
        list(result.canary_leaks),
        runtime.context.correlation_id,
    )
    if gates is None:
        return
    failures = check_gates(result, gates)
    logger.info(
        "Full evaluation gates %s; failed=%s correlation_id=%s",
        "failed" if failures else "passed",
        list(failures),
        runtime.context.correlation_id,
    )
    if failures:
        raise SystemExit(1)
