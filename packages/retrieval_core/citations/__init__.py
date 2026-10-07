from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol


class CitationValidationSpan(Protocol):
    def set_attribute(self, key: str, value: bool | int) -> None: ...


@dataclass(frozen=True, slots=True)
class CitationValidationResult:
    valid: bool
    cited_chunk_ids: tuple[str, ...]
    unknown_chunk_ids: tuple[str, ...]
    retrieved_chunk_count: int


class CitationValidationError(ValueError):
    def __init__(self, result: CitationValidationResult) -> None:
        self.result = result
        super().__init__(
            "Citation validation failed: one or more cited chunk IDs were not "
            "retrieved in this turn."
        )


def validate_citations(
    cited_chunk_ids: Iterable[str],
    retrieved_chunk_ids: Iterable[str],
    *,
    span: CitationValidationSpan | None = None,
) -> CitationValidationResult:
    """Reject citations that do not reference chunks retrieved in this turn."""
    cited_ids = tuple(cited_chunk_ids)
    retrieved_ids = set(retrieved_chunk_ids)
    unknown_ids = tuple(chunk_id for chunk_id in cited_ids if chunk_id not in retrieved_ids)
    result = CitationValidationResult(
        valid=not unknown_ids,
        cited_chunk_ids=cited_ids,
        unknown_chunk_ids=unknown_ids,
        retrieved_chunk_count=len(retrieved_ids),
    )

    if span is not None:
        span.set_attribute("citations.validation.valid", result.valid)
        span.set_attribute("citations.validation.cited_count", len(cited_ids))
        span.set_attribute("citations.validation.unknown_count", len(unknown_ids))
        span.set_attribute("citations.validation.retrieved_count", len(retrieved_ids))

    if unknown_ids:
        raise CitationValidationError(result)
    return result
