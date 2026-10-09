# Azure AI Search adapter

`AzureSearchRetriever` implements `accelerator.retrieval_core.search.Retriever`.
Inject a `QueryEmbedder` using the same embedding model and dimensions as ingestion.
Use `open_retriever(settings, embedder)` in the application lifespan to create and
close the asynchronous Search client and its user-assigned managed identity.
No clients are constructed at import time and no keys are accepted by this factory.

Required settings (environment prefix `AZURE_SEARCH_`): `ENDPOINT`, `INDEX_NAME`,
`VECTOR_DIMENSIONS`, `MANAGED_IDENTITY_CLIENT_ID`. The identity needs Search Index
Data Reader for retrieval. Index provisioning is separate: `build_index` converts
the SDK-free `IndexDefinition` into an Azure SDK index, and `ensure_index` accepts
an injected async token-authenticated `SearchIndexClient` with Search Service
Contributor privileges.

Provision or update the index from the schema-as-code definition with the
deployment identity:

```text
AZURE_SEARCH_ENDPOINT=https://<service>.search.windows.net \
AZURE_SEARCH_INDEX_NAME=chunks AZURE_SEARCH_VECTOR_DIMENSIONS=1536 \
uv run --all-packages python -m accelerator.infrastructure.search.provision
```

It uses `DefaultAzureCredential` (workload identity in CI, a developer login
locally) and never an admin key.

The default query combines keyword and vector search with semantic ranking,
50 vector candidates, and `preFilter`. `SEMANTIC_RANKING` and `VECTOR_CANDIDATES`
are operator settings, not request parameters. Semantic service errors fail the
request rather than silently downgrading ranking.

Authorization is always derived from `ExecutionContext.scope_ids`. Empty scopes
fail before embedding or Search I/O. Request filters accept only string equality
for `document_id`, `version`, `content_hash`, and `section_heading`; unknown keys
(including scope filters and raw OData) raise `ValueError`. Metadata filters are
ANDed with the scope predicate and literals are OData-escaped. Results are
validated and any out-of-scope hit aborts the whole retrieval, including already
read hits. Callers receive only `Evidence`; its text remains untrusted data.

Scope membership uses a single `search.in` clause rather than an unbounded OR tree.
The scope representation has a 64 KiB UTF-8 safety budget, including escaping and
syntax; oversized lists fail explicitly and are never truncated. A delimiter
absent from every scope is selected from `|,;~^`; unrepresentable IDs fail closed.
Vector dimensions must be 2 through 4096 in both settings and index definitions.

The context's UTC deadline is checked before embedding and enforced by one async
timeout through embedding, SDK calls/retries, and result paging. Expired contexts
perform no I/O; timeouts cancel ongoing work and never return partial evidence.
No caller-supplied timeout can widen the context's remaining budget.

Credential-free integration tests exercise the real SDK request serialization,
async HTTP pipeline, and evidence mapping against a controlled transport with
two scopes, plus a deliberately noncompliant response. They do **not** establish
live Azure service behavior or measure recall. Run:

```text
uv run --all-packages pytest packages/retrieval_core/search/tests apps/api/src/accelerator/infrastructure/search/tests
```

Dependencies: #12 supplies `Evidence` and `RetrievalRequest` from
`accelerator.retrieval_core.models`; #10 supplies
`accelerator.security_core.data_boundaries.context.ExecutionContext`.

The workspace config uses setuptools strict editable mode for `fde-retrieval-core`.
This exposes its mapped namespace as a real package tree so strict mypy can resolve
the typed shared contracts without suppressing import errors. After adding new
retrieval modules, rerun `uv sync --all-packages --reinstall-package fde-retrieval-core`
to refresh that editable tree.

The API `accelerator` package extends its namespace to include workspace packages.
Strict type checks run over every workspace package by its installed import name
(`accelerator.*`, see `[tool.mypy] packages` in the root `pyproject.toml`), so the
adapter, retrieval and their package-local tests are each checked once, under the
names callers use. `make check` and pre-commit run it without excluding any errors.

The security-sensitive search test directories are included in root pytest
`testpaths`, so standard `make check` and pre-commit collect them automatically.

## Ranking evaluation gate

Before/after recall@k is required for ranking changes and remains unmeasured.
`make eval-smoke` currently exits successfully with an M5 placeholder message; it
does not run retrieval evaluation. `contracts/evaluation/valid-example.jsonl`
contains synthetic schema-validation rows, not a retrieval corpus: there is no
source chunk or embedding for `synthetic-chunk-1`. The evaluation runner,
evaluators, and baseline packages are empty.

Valid evidence requires a shared indexed corpus with ground-truth chunk IDs,
fixed queries/scopes and k, a baseline/candidate retrieval runner, and operational
semantic-enabled Azure AI Search with matching embeddings and managed-identity
access. The controlled-transport tests prescribe responses and cannot measure
ranking quality. Until these prerequisites are supplied, the ranking acceptance
gate is blocked; passing unit tests or the placeholder does not satisfy it.
