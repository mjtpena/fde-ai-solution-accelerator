# ADR-0003: Call Azure AI Search directly with a context-injected scope filter

**Status:** Accepted
**Date:** 2026-10-09

## Context

Grounded answers (spec §6.2) depend on retrieval that is **correct**, **isolated by
scope**, and **traceable** to a citation. The threats in spec §7 include cross-scope
data leakage and hallucinated citations. The controls for both sit in retrieval:
mandatory search filters, and validation of citations against the evidence retrieved
in the same turn.

There are three broad ways to give the model documents:

1. Foundry's built-in knowledge or file-search tools, where the platform owns chunking,
   indexing and the query.
2. A retrieval tool the agent chooses to call, with arguments the model writes.
3. A workflow step that queries a search index the accelerator owns, through the SDK.

The first two make it hard to prove that a scope filter was always applied, to see
which query and filter ran, and to reproduce chunk IDs for evaluation (spec §5.6
`expected_evidence_ids`).

## Decision

Retrieval is a **mandatory workflow step** that calls **Azure AI Search directly**
through `azure-search-documents`. It is not a tool the agent may skip or
parameterise.

- **Query.** `AzureSearchRetriever`
  (`apps/api/src/accelerator/infrastructure/search/adapter.py`) implements the
  `Retriever` protocol. Each query combines keyword text with a `VectorizedQuery`
  (`vector_filter_mode="preFilter"`). By default it adds semantic ranking
  (`query_type="semantic"`, `semantic_error_mode="fail"`), so a semantic outage fails
  the request instead of silently downgrading ranking. Semantic ranking and the
  number of vector candidates are operator settings, not request parameters.
- **Scope filter from `ExecutionContext` only.** `build_query`
  (`packages/retrieval_core/search/query.py`) builds a single `search.in` predicate
  from `ctx.scope_ids`. Empty scopes fail before any embedding or search call. Request
  filters accept only string equality on `document_id`, `version`, `content_hash` and
  `section_heading`. These are ANDed with the scope predicate. Unknown keys, scope
  keys and raw OData are rejected. After the query, any hit outside the context's
  scopes aborts the whole retrieval with `PermissionError`.
- **Schema as code.** `packages/retrieval_core/indexing/schema.py` defines the chunk
  index without SDK types. Its fields are `chunk_id` as key, filterable `scope_id`,
  `document_id` and `version`, searchable `text` and `document_title`, an HNSW vector
  profile and a semantic configuration. `index.py` maps it to the SDK, and
  `provision.py` creates or updates it with the deployment identity (Search Service
  Contributor). It runs in the `index` stage of `infrastructure/scripts/deploy-dev.sh`.
- **Deterministic ingestion, no agent** (spec §6.1). The worker (`workers/ingestion`)
  works as follows:
  - It validates type and size against an allow-list (text, Markdown, PDF), then
    parses and chunks the document with `retrieval_core`.
  - Chunk IDs are deterministic, so ingesting the same version again upserts the same
    keys.
  - It hashes content to skip unchanged documents, holds a per-document lock, embeds
    in batches with the Foundry embedding deployment, and upserts by `chunk_id`.
  - It deletes stale chunks when a document is re-indexed. `scope_id` is set by
    trusted server code on the queue message and checked on every chunk before any
    write.
- **Evidence sufficiency before generation.** `EvidenceSufficiencyChecker`
  (`packages/retrieval_core/sufficiency/`) applies a minimum score and minimum count on
  a configured score field. The semantic `reranker_score` is comparable across
  queries, but fused hybrid scores are not. Evidence without that score never
  qualifies.
- Identities: the API has **Search Index Data Reader**, and the worker has **Search
  Index Data Contributor**. Local (key) auth is disabled on the service
  (`infrastructure/README.md`).

## Consequences

Positive:

- Scope isolation is enforced in code that the model cannot influence. It is tested
  with a controlled HTTP transport (`apps/api/src/accelerator/infrastructure/search/tests/`)
  and by the offline smoke gate's `scope_isolation` metric (ADR-0005).
- Each `Evidence` carries `chunk_id`, `document_id`, `version`, scores and
  `source_uri`. Citation validation and recall@k use the same IDs ingestion wrote.
- Retrieval is observable. `TracedRetriever` records `top_k`, the requested filter
  field names and the result count on the `retrieval.search` span
  (`apps/api/src/accelerator/telemetry/traced.py`, spec §8).

Negative / trade-offs:

- The accelerator owns parsing, chunking, embedding, index schema, re-index and
  delete. Foundry's managed file search would provide these.
- Query and ingestion must use the same embedding model and dimensions. A mismatch is
  a configuration error, and index changes need a migration path.
- The model cannot decide to search again with a better query. Multi-hop retrieval
  would need an explicit workflow change.
- Ranking quality is not yet measured. Before/after recall@k for ranking changes
  needs a shared indexed corpus and a live semantic-enabled service (see the
  "Ranking evaluation gate" section of the search README). The spec §6.1 "retrieval
  smoke query" after indexing is not implemented in the worker.

## Alternatives considered

- **Foundry knowledge / file search tools.** Less code to own, but scope filtering,
  chunk identity and query parameters are hidden behind the platform. Cross-scope
  isolation could not be proven at the filter level.
- **An agent-called retrieval tool.** The model would choose whether and how to
  search, and could try to supply filters. Spec §5.4 requires the scope filter to be
  injected whatever the caller or model supplies. A mandatory step makes retrieval
  unconditional.
- **LlamaIndex / LangChain retriever wrappers.** They add a layer over the same SDK
  with no benefit for the baseline (spec §2), and they are forbidden by repository
  rules.
- **Vector-only or keyword-only search.** Simpler, but hybrid search with semantic
  reranking gives better recall on mixed queries. It also provides the bounded
  reranker score that the sufficiency gate can threshold.

## References

- `docs/spec.md` §2, §5.4, §5.5, §6.1, §6.2, §7
- `apps/api/src/accelerator/infrastructure/search/README.md`, `adapter.py`, `index.py`,
  `provision.py`
- `packages/retrieval_core/search/query.py`
- `packages/retrieval_core/indexing/schema.py`
- `packages/retrieval_core/sufficiency/checker.py`, `policy.py`
- `workers/ingestion/src/ingestion_worker/pipeline.py`, `service.py`,
  `infrastructure/search_index.py`
- `apps/api/src/accelerator/infrastructure/grounded_answer.py`
