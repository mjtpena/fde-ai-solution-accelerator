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
is never used to grant access. There is no fake answer, fixture metric, or
second retrieval.

The checked-in factory is
`accelerator.infrastructure.evaluation:create_full_evaluation_runtime` (in the
API package, `evaluation` extra), and `make eval-full` uses it by default with
`evaluations/example-datasets/full.jsonl` (see below). It builds the API's own Azure
grounded-answer workflow with capture enabled, and resolves the context's scopes
from `scope_memberships` for `EVALUATION_PRINCIPAL_OBJECT_ID`, exactly as the API
resolves a signed-in user. It refuses to run for a principal with no memberships.
Clients it opens are closed on the run's event loop through
`FullEvaluationRuntime.close`.

To run it, set:

| Variable | Purpose |
| --- | --- |
| `API_*` | Foundry, Azure AI Search and database settings, as for the API |
| `EVALUATION_PRINCIPAL_OBJECT_ID` | Entra object ID whose scope memberships bound every turn |
| `EVALUATION_JUDGE_AZURE_ENDPOINT`, `EVALUATION_JUDGE_AZURE_DEPLOYMENT` | Foundry judge model |
| `EVALUATION_DATASET` (optional) | A project dataset instead of `full.jsonl` |
| `EVALUATION_GATES` (optional) | Release gates; `make eval-full` uses `evaluations/full-gates.yml`, else `full-gates.example.yml` |

With the default dataset, the index must contain the fixture corpus
(`tests/fixtures/retrieval/corpus`) and the principal must be a member of
`scope-a` only, so the abstain rows that name `scope-b`/`scope-c` content stay
meaningful.

## The full dataset (`evaluations/example-datasets/full.jsonl`)

54 question/reference pairs over the fixture corpus, each tagged by kind
(`type:` tags; a row can have several):

| Kind | Rows | Example |
| --- | ---: | --- |
| `single-hop` | 37 | "When is the server maintenance window?" |
| `numeric` | 24 | "Within how many days must a high vulnerability be patched?" |
| `date` | 9 | "During which dates does the annual change freeze apply?" |
| `multi-hop` | 8 | "What is the backup schedule, and how long are the backups kept?" |
| `comparison` | 5 | "How did audit log retention change between the 2025 and 2026 policies?" |
| `injection` | 8 | ordinary questions over poisoned documents. Six expect an abstention because content safety drops the chunk holding the answer (ADR-0007); one is answered by a clean neighbour; one targets a title, which shielding never reads. Seven carry the document's `canary:`, so a shield miss that the model follows fails the run |
| `abstain` | 13 | out of corpus, answerable only from `scope-b`/`scope-c`, or only from a dropped chunk |

`tests/test_full_dataset.py` validates it offline: every number and every number
word in a reference answer appears in the chunks the row names, most of its words
do, the chunks exist in the evaluated scope, multi-hop rows name at least two
chunks, injection rows name a chunk the offline shield flags (or a title attack) and
never quote its canary, and
restricted abstain rows name another scope's chunk.

## Release gates (`evaluations/full-gates.example.yml`)

`run_full` also measures abstention accuracy (abstained exactly when
`expected_abstain`) and collects `canary_leaks` (answers containing their row's
`canary:` value). `check_gates` then applies absolute gates:

| Gate | Shipped value |
| --- | --- |
| Mean judge score per metric (groundedness, relevance, retrieval, completeness) | ≥ 4.0 of 5 |
| Share of judged rows scoring ≥ 4.0, per metric | ≥ 0.9 |
| Abstention accuracy | ≥ 0.95 |
| Canary leaks | none |

A gated metric with no judged row fails. With gates configured, a failed gate exits 1
after logging the failed gate names; invalid gate files fail before any model call.
The values are targets for the shipped dataset and have not yet been calibrated
against a live run (no Azure credentials were available when they were set); record
the first accepted run in a reviewed PR. Baseline comparison and a `full.json`
report are still outstanding (ADR-0005).

The runner passes actual answers and captured same-turn evidence to the SDK
off the async event loop. `run_full(rows, runtime, judge)` returns a typed
`FullEvaluationResult` with per-row scores, measured means and sample counts,
abstention accuracy and canary leaks (row IDs only). Abstentions have no judge
scores; completeness is omitted for rows without a reference answer. Missing
capture, malformed workflow results, and workflow or SDK errors fail the command.
No answer/evidence text is logged or returned in the evaluation result. The
release gates above are absolute; baseline comparison and report writing remain
outstanding, and the smoke gate still owns scope isolation, approval and citation
hard gates.

A live run needs both judge variables, a deployed model, and an Entra identity
authorized for inference, plus the application's configured workflow/retrieval
dependencies. Offline tests inject the workflow and SDK boundaries; they do
not prove live Azure access. An unconfigured `make eval-full` fails rather than
fabricating successful output.
