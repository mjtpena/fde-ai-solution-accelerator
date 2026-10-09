# Evaluation datasets

`dataset.schema.json` defines one row of a UTF-8 JSONL dataset per spec section
5.6. All nine fields are required, including the nullable `expected_answer` and
`expected_tool`. Categories are enumerated; unknown fields and type coercion are
not accepted. Empty evidence and tag arrays are valid.

```python
from accelerator.evaluation_core.datasets import load_dataset

rows = load_dataset("contracts/evaluation/valid-example.jsonl")
```

The synchronous loader returns typed `DatasetRow` objects in file order. Any
invalid row, malformed JSON, blank line or invalid UTF-8 raises
`DatasetValidationError` with the path and one-based line number. No partial
dataset is returned. Filesystem errors propagate to the caller. Dataset
`scope_id` is an evaluation expectation, not authorization: runners must obtain
runtime scope from trusted `ExecutionContext`, never copy it into execution
context from a dataset.

`make check` runs the loader tests, verifies the committed schema exactly matches
the model's generated schema, and loads every `*.jsonl` under `evaluations/` and
`contracts/evaluation/`. Invalid checked-in rows therefore fail pull-request CI.

## Tags the runners read

Tags are free-form, but the evaluation runners give these prefixes meaning:

| Tag | Read by | Meaning |
| --- | --- | --- |
| `canary:<text>` | smoke, full | the answer must not contain `<text>` (an injection payload planted in the corpus) |
| `approval-followup:<action>` | smoke | after the turn, abuse the approval it created (`approve-execute`, `tamper-args`, `replay`, `self-approve`, `skip-approval`, `rejected`, `cross-scope`) |
| `class:`, `gate:`, `technique:`, `owasp:`, `restricted-evidence:`, `injected-tool:`, `expect:withdrawn` | dataset tests | what the row tests and the evidence its attack depends on; see `packages/evaluation_core/runners/README.md` |
