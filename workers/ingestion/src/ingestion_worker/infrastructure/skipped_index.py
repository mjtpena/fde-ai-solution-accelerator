"""``ChunkIndex`` for INGESTION_SKIP_SEARCH_INDEXING: embeds and writes nothing.

It exists so the rest of the pipeline (queue, blob storage, parsing, chunking,
PostgreSQL lineage and state) can run end to end against emulators without Azure
AI Search or a Foundry embedding deployment. It invents no embeddings, and every
skipped call is logged so the gap is never silent. Settings validation refuses it
in production and next to any Search or Foundry setting.
"""

import logging
from collections.abc import Sequence
from typing import Any

logger = logging.getLogger("ingestion_worker")


class SkippedSearchIndex:
    async def upsert_chunks(self, chunks: Sequence[Any]) -> None:
        logger.warning(
            "search_indexing_skipped", extra={"operation": "upsert", "chunk_count": len(chunks)}
        )

    async def delete_chunks(self, document_id: str, chunk_ids: Sequence[str]) -> None:
        del document_id
        logger.warning(
            "search_indexing_skipped",
            extra={"operation": "delete_chunks", "chunk_count": len(chunk_ids)},
        )

    async def delete_document(self, document_id: str) -> None:
        del document_id
        logger.warning("search_indexing_skipped", extra={"operation": "delete_document"})
