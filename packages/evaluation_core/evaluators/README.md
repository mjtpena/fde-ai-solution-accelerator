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
`make eval-full` invokes `accelerator.evaluation_core.evaluators.full`. Configure
`EVALUATION_DATASET` with a real JSONL dataset path and
`EVALUATION_WORKFLOW_FACTORY` with a trusted local `module:function` factory
returning `FullEvaluationRuntime(workflow, context)`. The factory constructs
the existing `GroundedAnswerWorkflow` with `capture_evaluation_context=True`
and resolves an authorized `ExecutionContext` server-side. Dataset `scope_id`
is never used to grant access. There is no default workflow, fake answer,
fixture metric, or second retrieval.

The runner passes actual answers and captured same-turn evidence to the SDK
off the async event loop. `run_full(rows, runtime, judge)` returns a typed
`FullEvaluationResult` with per-row scores, measured means and sample counts.
Abstentions have no judge scores; completeness is omitted for rows without a
reference answer. Missing capture, malformed workflow results, and workflow or
SDK errors fail the command. No answer/evidence text is logged or returned in
the evaluation result. The runner does not implement deterministic safety gates,
baseline comparison, or report writing owned by #30/#31; measured scores are
not a declaration of safety/threshold acceptance.

A live run needs both judge variables, a deployed model, and an Entra identity
authorized for inference, plus the application's configured workflow/retrieval
dependencies. Offline tests inject the workflow and SDK boundaries; they do
not prove live Azure access. An unconfigured `make eval-full` fails rather than
fabricating successful output. `make eval-smoke` remains owned by #30/#31.
