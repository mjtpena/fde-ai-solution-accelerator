# Ingestion worker

`python -m accelerator.ingestion` consumes an Azure Storage queue (Azurite
locally) and runs `IngestionService` for each message:

```text
message → validate → read source blob (size-checked) → parse → chunk
→ store canonical blob → upsert chunk lineage (PostgreSQL)
→ embed + upsert chunks (Azure AI Search, keyed by chunk_id)
→ remove stale chunks → status = ready | failed(reason)
```

## Message contract

JSON text, produced only by trusted server code that has already resolved
`scope_id` for the uploader. Unknown fields are rejected.

```json
{
  "operation": "ingest",
  "document_id": "policies/retention.md",
  "scope_id": "scope-a",
  "title": "Retention policy",
  "source_uri": "https://documents.example/retention",
  "version": "3",
  "effective_date": "2026-01-01",
  "content_type": "text/markdown",
  "source_blob": "retention.md"
}
```

`content_type` is one of `text/plain`, `text/markdown`, `application/pdf`.
`source_blob` names a blob in the incoming container. `{"operation": "delete",
"document_id": ..., "scope_id": ...}` removes the blob, index entries and
database rows.

## Retries and poison messages

A message is deleted only after it is handled, or after it has been moved to the
poison queue with the document recorded as `failed(reason)`. Transient failures
(service errors, missing blobs) make the message invisible for
`15 s × 2^(attempt-1)` (capped at 15 minutes) and it is redelivered, up to
`INGESTION_MAX_ATTEMPTS` (default 5). Invalid messages and rejected documents
(type, size, unparseable or empty) are poisoned immediately. Messages stay
invisible for `INGESTION_VISIBILITY_TIMEOUT_SECONDS` (default 300) while being
processed; set it above the longest expected processing time for one document.

## Configuration

`INGESTION_*` settings (see `settings.py`). Production requires managed identity
for storage, PostgreSQL (`INGESTION_DATABASE_AUTH_MODE=managed_identity`), Search
and Foundry. Outside production, a storage connection string is accepted for
Azurite, and the worker creates its queues and containers. When the indexing
settings are incomplete, the worker logs `ingestion_disabled` and idles, still
healthy, so `docker compose up` works without Azure.

### Running without Azure AI Search (local and CI only)

`INGESTION_SKIP_SEARCH_INDEXING=true` runs every stage except embedding and the
Search writes: queue handling, the size and type allow-list, parsing, chunking,
the canonical blob, chunk lineage and document state in PostgreSQL. It needs
only storage (Azurite) and `INGESTION_DATABASE_URL`. Successful documents end as
`indexing_skipped`, never `ready`, and only `indexing_skipped` dedupes a rerun in
this mode, so a worker with Search configured re-processes them. Each skipped
index call logs `search_indexing_skipped`. Settings validation refuses the flag
in production and next to any Search or Foundry setting. No embeddings are
invented. The real-process suite in `tests/e2e_local` uses it; see
`docs/testing-strategy.md`.
