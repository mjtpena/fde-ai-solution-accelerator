"""Adapters for Microsoft Foundry's Azure AI Evaluation SDK."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol, TypedDict, cast

from azure.core.credentials import TokenCredential

from .settings import FoundryEvaluatorSettings


class FoundryEvaluationMetrics(TypedDict):
    """Normalized scalar scores emitted by the four Foundry evaluators."""

    groundedness: float
    relevance: float
    retrieval: float
    completeness: float


class _SdkEvaluator(Protocol):
    def __call__(self, **kwargs: str) -> Mapping[str, object]: ...


class _CloseableCredential(TokenCredential, Protocol):
    def close(self) -> None: ...


EvaluatorFactory = Callable[..., _SdkEvaluator]

MetricName = Literal["groundedness", "relevance", "retrieval", "completeness"]


class FoundryEvaluatorFactories(TypedDict):
    """Factory injection boundary, primarily for tests that mock the SDK."""

    groundedness: EvaluatorFactory
    relevance: EvaluatorFactory
    retrieval: EvaluatorFactory
    completeness: EvaluatorFactory


@dataclass
class FoundryEvaluatorAdapters:
    """Construct and normalize the Foundry groundedness, relevance, retrieval,
    and response-completeness evaluators.

    Azure AI Evaluation's built-in evaluators are synchronous callables. This
    adapter keeps their SDK-specific input/output conventions behind one
    boundary and never accepts API keys or other secret model configuration.
    """

    _groundedness: _SdkEvaluator = field(repr=False)
    _relevance: _SdkEvaluator = field(repr=False)
    _retrieval: _SdkEvaluator = field(repr=False)
    _completeness: _SdkEvaluator = field(repr=False)
    _owned_credential: _CloseableCredential | None = field(default=None, repr=False)

    @classmethod
    def from_settings(
        cls,
        settings: FoundryEvaluatorSettings,
        credential: TokenCredential | None = None,
        *,
        factories: FoundryEvaluatorFactories | None = None,
    ) -> FoundryEvaluatorAdapters:
        """Create evaluators using a configured Azure deployment and Entra ID.

        A ``DefaultAzureCredential`` is created only when this factory is
        explicitly called and is owned/closed by the returned adapter.
        """
        owned_credential: _CloseableCredential | None = None
        if credential is None:
            from azure.identity import DefaultAzureCredential

            owned_credential = DefaultAzureCredential()
            credential = owned_credential

        evaluator_factories = factories
        if evaluator_factories is None:
            from azure.ai.evaluation import (
                GroundednessEvaluator,
                RelevanceEvaluator,
                ResponseCompletenessEvaluator,
                RetrievalEvaluator,
            )

            evaluator_factories = cast(
                FoundryEvaluatorFactories,
                {
                    "groundedness": GroundednessEvaluator,
                    "relevance": RelevanceEvaluator,
                    "retrieval": RetrievalEvaluator,
                    "completeness": ResponseCompletenessEvaluator,
                },
            )

        model_config = {
            "azure_endpoint": str(settings.azure_endpoint),
            "azure_deployment": settings.azure_deployment,
        }
        return cls(
            _groundedness=evaluator_factories["groundedness"](
                model_config=model_config,
                credential=credential,
            ),
            _relevance=evaluator_factories["relevance"](
                model_config=model_config,
                credential=credential,
            ),
            _retrieval=evaluator_factories["retrieval"](
                model_config=model_config,
                credential=credential,
            ),
            _completeness=evaluator_factories["completeness"](
                model_config=model_config,
                credential=credential,
            ),
            _owned_credential=owned_credential,
        )

    def evaluate(
        self,
        *,
        query: str,
        response: str,
        context: str,
        expected_answer: str,
    ) -> FoundryEvaluationMetrics:
        """Evaluate a generated response against the retrieved context/reference.

        ``expected_answer`` is passed to the SDK's ``ground_truth`` parameter.
        The caller should invoke this method only for rows with an expected
        answer, because completeness is not defined without one.
        """
        scores = self.evaluate_available(
            query=query, response=response, context=context, expected_answer=expected_answer
        )
        return {
            "groundedness": scores["groundedness"],
            "relevance": scores["relevance"],
            "retrieval": scores["retrieval"],
            "completeness": scores["completeness"],
        }

    def evaluate_available(
        self,
        *,
        query: str,
        response: str,
        context: str,
        expected_answer: str | None,
    ) -> dict[MetricName, float]:
        """Omit completeness when the dataset supplies no reference answer."""
        scores: dict[MetricName, float] = {
            "groundedness": _score(
                self._groundedness(
                    query=query,
                    response=response,
                    context=context,
                ),
                ("groundedness",),
            ),
            "relevance": _score(
                self._relevance(
                    query=query,
                    response=response,
                ),
                ("relevance",),
            ),
            "retrieval": _score(
                self._retrieval(
                    query=query,
                    context=context,
                ),
                ("retrieval",),
            ),
        }
        if expected_answer is not None:
            scores["completeness"] = _score(
                self._completeness(
                    response=response,
                    ground_truth=expected_answer,
                ),
                ("response_completeness", "completeness"),
            )
        return scores

    def close(self) -> None:
        """Close the credential only when this adapter created it."""
        if self._owned_credential is not None:
            self._owned_credential.close()

    def __enter__(self) -> FoundryEvaluatorAdapters:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _score(result: Mapping[str, object], metric_names: tuple[str, ...]) -> float:
    candidate_keys = tuple(
        key
        for metric_name in metric_names
        for key in (
            metric_name,
            f"{metric_name}_score",
            f"gpt_{metric_name}",
            f"gpt_{metric_name}_score",
        )
    )
    for key in candidate_keys:
        value = result.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            score = float(value)
            if math.isfinite(score) and 1.0 <= score <= 5.0:
                return score
            raise ValueError(f"Foundry {metric_names[0]} score must be between 1 and 5.")

    raise ValueError(
        f"Foundry {metric_names[0]} result did not include a numeric score "
        f"under any of {candidate_keys!r}."
    )
