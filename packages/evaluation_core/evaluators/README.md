# Foundry evaluator adapters

`FoundryEvaluatorAdapters` wraps the Azure AI Evaluation SDK's groundedness,
relevance, retrieval, and response-completeness evaluators. Configure the judge
with `EVALUATION_JUDGE_AZURE_ENDPOINT` and
`EVALUATION_JUDGE_AZURE_DEPLOYMENT`; credentials use `DefaultAzureCredential`
and no API key is accepted.

```python
from accelerator.evaluation_core.evaluators import (
    FoundryEvaluatorAdapters,
    FoundryEvaluatorSettings,
)

settings = FoundryEvaluatorSettings()
with FoundryEvaluatorAdapters.from_settings(settings) as evaluators:
    scores = evaluators.evaluate(
        query="What does the source say?",
        response="The generated answer.",
        context="The retrieved evidence.",
        expected_answer="The expected answer.",
    )
```

`scores` is a typed mapping with float values under the stable keys
`groundedness`, `relevance`, `retrieval`, and `completeness`. The SDK's 1–5
score range is validated; missing, malformed, or out-of-range scores raise
`ValueError`. Callers should evaluate completeness only for rows with a
non-null expected answer.

These adapters do not load evaluation datasets or generate agent responses.
The full-run target depends on the dataset-loader, agent-factory, and report
APIs being available to the runner.
