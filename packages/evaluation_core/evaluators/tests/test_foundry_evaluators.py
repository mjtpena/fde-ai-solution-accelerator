from collections.abc import Callable, Mapping

import pytest
from azure.core.credentials import AccessToken, TokenCredential
from pydantic import HttpUrl

from .. import (
    FoundryEvaluatorAdapters,
    FoundryEvaluatorFactories,
    FoundryEvaluatorSettings,
)


class FakeCredential:
    def get_token(self, *scopes: str, **kwargs: object) -> AccessToken:
        return AccessToken("test-token", 3_600)

    def close(self) -> None:
        pass


class FakeEvaluator:
    def __init__(
        self,
        result: Mapping[str, object],
        model_config: Mapping[str, str],
        credential: TokenCredential,
    ) -> None:
        self.result = result
        self.model_config = model_config
        self.credential = credential
        self.calls: list[dict[str, str]] = []

    def __call__(self, **kwargs: str) -> Mapping[str, object]:
        self.calls.append(kwargs)
        return self.result


def _factories(
    created: dict[str, FakeEvaluator],
    results: Mapping[str, Mapping[str, object]],
) -> FoundryEvaluatorFactories:
    def build(
        name: str,
    ) -> Callable[..., FakeEvaluator]:
        def factory(
            *,
            model_config: Mapping[str, str],
            credential: TokenCredential,
        ) -> FakeEvaluator:
            evaluator = FakeEvaluator(results[name], model_config, credential)
            created[name] = evaluator
            return evaluator

        return factory

    return {
        "groundedness": build("groundedness"),
        "relevance": build("relevance"),
        "retrieval": build("retrieval"),
        "completeness": build("completeness"),
    }


def test_adapters_build_sdk_evaluators_and_normalize_scores() -> None:
    credential = FakeCredential()
    created: dict[str, FakeEvaluator] = {}
    results: dict[str, Mapping[str, object]] = {
        "groundedness": {"groundedness": 4},
        "relevance": {"gpt_relevance": 3.5},
        "retrieval": {"retrieval_score": 5},
        "completeness": {"response_completeness": 4},
    }
    settings = FoundryEvaluatorSettings(
        azure_endpoint=HttpUrl("https://example.services.ai.azure.com"),
        azure_deployment="judge-deployment",
    )

    adapters = FoundryEvaluatorAdapters.from_settings(
        settings,
        credential=credential,
        factories=_factories(created, results),
    )
    scores = adapters.evaluate(
        query="What does the source say?",
        response="A grounded response.",
        context="Evidence from the retrieved source.",
        expected_answer="The expected response.",
    )

    assert scores == {
        "groundedness": 4.0,
        "relevance": 3.5,
        "retrieval": 5.0,
        "completeness": 4.0,
    }
    assert set(created) == {"groundedness", "relevance", "retrieval", "completeness"}
    assert all(
        evaluator.model_config
        == {
            "azure_endpoint": "https://example.services.ai.azure.com/",
            "azure_deployment": "judge-deployment",
        }
        for evaluator in created.values()
    )
    assert all(evaluator.credential is credential for evaluator in created.values())
    assert created["groundedness"].calls == [
        {
            "query": "What does the source say?",
            "response": "A grounded response.",
            "context": "Evidence from the retrieved source.",
        }
    ]
    assert created["relevance"].calls == [
        {"query": "What does the source say?", "response": "A grounded response."}
    ]
    assert created["retrieval"].calls == [
        {"query": "What does the source say?", "context": "Evidence from the retrieved source."}
    ]
    assert created["completeness"].calls == [
        {"response": "A grounded response.", "ground_truth": "The expected response."}
    ]


def test_settings_are_loaded_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "EVALUATION_JUDGE_AZURE_ENDPOINT",
        "https://judge.services.ai.azure.com",
    )
    monkeypatch.setenv("EVALUATION_JUDGE_AZURE_DEPLOYMENT", "configured-judge")

    settings = FoundryEvaluatorSettings.model_validate({})

    assert str(settings.azure_endpoint) == "https://judge.services.ai.azure.com/"
    assert settings.azure_deployment == "configured-judge"


def test_missing_reference_omits_completeness_sdk_call() -> None:
    created: dict[str, FakeEvaluator] = {}
    adapters = FoundryEvaluatorAdapters.from_settings(
        FoundryEvaluatorSettings(
            azure_endpoint=HttpUrl("https://judge.example.invalid"), azure_deployment="judge"
        ),
        credential=FakeCredential(),
        factories=_factories(
            created,
            {
                "groundedness": {"groundedness": 4},
                "relevance": {"relevance": 4},
                "retrieval": {"retrieval": 4},
                "completeness": {"completeness": 4},
            },
        ),
    )
    scores = adapters.evaluate_available(
        query="Question", response="Answer", context="Evidence", expected_answer=None
    )
    assert "completeness" not in scores
    assert not created["completeness"].calls


@pytest.mark.parametrize(
    "result",
    [
        {},
        {"groundedness": "4"},
        {"groundedness": True},
        {"groundedness": 0},
        {"groundedness": 6},
        {"groundedness": float("nan")},
    ],
)
def test_invalid_foundry_scores_raise(result: Mapping[str, object]) -> None:
    credential = FakeCredential()
    created: dict[str, FakeEvaluator] = {}
    results = {
        "groundedness": result,
        "relevance": {"relevance": 3},
        "retrieval": {"retrieval": 3},
        "completeness": {"completeness": 3},
    }
    adapters = FoundryEvaluatorAdapters.from_settings(
        FoundryEvaluatorSettings(
            azure_endpoint=HttpUrl("https://example.services.ai.azure.com"),
            azure_deployment="judge-deployment",
        ),
        credential=credential,
        factories=_factories(created, results),
    )

    with pytest.raises(ValueError, match="groundedness"):
        adapters.evaluate(
            query="Question",
            response="Response",
            context="Context",
            expected_answer="Reference",
        )
