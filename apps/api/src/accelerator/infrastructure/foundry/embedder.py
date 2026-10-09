"""Query embeddings from a Foundry embedding deployment."""

from collections.abc import Sequence
from typing import Any, Protocol


class EmbeddingVector(Protocol):
    @property
    def vector(self) -> list[float]: ...


class EmbeddingClient(Protocol):
    """The subset of ``agent_framework.foundry.FoundryEmbeddingClient`` used here."""

    async def get_embeddings(
        self, values: Sequence[str], *, options: Any = None
    ) -> Sequence[EmbeddingVector]: ...


class FoundryQueryEmbedder:
    """``QueryEmbedder`` that requests vectors of exactly the index's dimension."""

    def __init__(self, client: EmbeddingClient, *, dimensions: int) -> None:
        self._client = client
        self._dimensions = dimensions

    async def embed(self, query: str) -> list[float]:
        embeddings = await self._client.get_embeddings(
            [query], options={"dimensions": self._dimensions}
        )
        if len(embeddings) != 1:
            raise ValueError("Embedding service returned an unexpected number of vectors.")
        return list(embeddings[0].vector)
