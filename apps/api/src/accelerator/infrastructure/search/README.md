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
