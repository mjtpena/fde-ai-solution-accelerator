"""Credential-dependent full evaluation; no fixture answers or reporting API."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from accelerator.security_core.data_boundaries.context import ExecutionContext

from ..datasets import DatasetRow, load_dataset
from .foundry import FoundryEvaluatorAdapters, MetricName
from .settings import FoundryEvaluatorSettings

if TYPE_CHECKING:
    from packages.agent_core.workflows.grounded_answer import GroundedAnswerResult
else:
    from accelerator.agent_core.workflows.grounded_answer import GroundedAnswerResult


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


class FullEvaluationSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVALUATION_", extra="ignore")

    dataset: Path
    workflow_factory: str = Field(pattern=r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*$")


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
        for name, score in scores.items():
            totals[name] = totals.get(name, 0.0) + score
            counts[name] = counts.get(name, 0) + 1
    return FullEvaluationResult(
        rows=tuple(results),
        metrics={name: total / counts[name] for name, total in totals.items()},
        metric_counts=counts,
    )


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
    args = parser.parse_args()
    overrides: dict[str, object] = {}
    if args.dataset is not None:
        overrides["dataset"] = args.dataset
    if args.workflow_factory is not None:
        overrides["workflow_factory"] = args.workflow_factory
    settings = FullEvaluationSettings.model_validate(overrides)
    judge_settings = FoundryEvaluatorSettings.model_validate({})
    rows = load_dataset(settings.dataset)
    runtime = load_runtime(settings.workflow_factory)
    with FoundryEvaluatorAdapters.from_settings(judge_settings) as judge:
        result = asyncio.run(run_full(rows, runtime, judge))
    logging.basicConfig(level=logging.INFO)
    logging.getLogger(__name__).info(
        "Full evaluation measured %d rows; metrics=%s counts=%s correlation_id=%s",
        len(result.rows),
        result.metrics,
        result.metric_counts,
        runtime.context.correlation_id,
    )
