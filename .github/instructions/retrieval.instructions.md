---
applyTo: "**/retrieval*/**,workers/**"
---
# Retrieval rules (Azure AI Search)
- All queries go through `Retriever`; the Azure AI Search adapter ALWAYS injects scope filters from `ExecutionContext`.
- Hybrid (keyword + vector) with semantic ranker by default; parameters configurable.
- Ingestion is idempotent: content hash dedupe, upsert by `chunk_id`, deletion removes blob + index + DB rows.
- Chunks carry `document_id`, `version`, `effective_date`, `section_heading`.
- Return `Evidence` objects with scores; never return raw Search documents to callers.
- Any change to chunking or ranking must include a before/after recall@k from the eval suite in the PR.
