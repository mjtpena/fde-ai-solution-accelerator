import unittest

from accelerator.retrieval_core.citations import (  # type: ignore[import-untyped]
    CitationValidationError,
    CitationValidationResult,
    validate_citations,
)


class RecordingSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, bool | int] = {}

    def set_attribute(self, key: str, value: bool | int) -> None:
        self.attributes[key] = value


class CitationValidationTests(unittest.TestCase):
    def test_validates_multiple_citations_from_the_current_turn(self) -> None:
        span = RecordingSpan()

        result = validate_citations(
            ("chunk-1", "chunk-2"),
            ("chunk-1", "chunk-2", "chunk-3"),
            span=span,
        )

        self.assertEqual(
            result,
            CitationValidationResult(
                valid=True,
                cited_chunk_ids=("chunk-1", "chunk-2"),
                unknown_chunk_ids=(),
                retrieved_chunk_count=3,
            ),
        )
        self.assertEqual(span.attributes["citations.validation.valid"], True)
        self.assertEqual(span.attributes["citations.validation.cited_count"], 2)
        self.assertEqual(span.attributes["citations.validation.unknown_count"], 0)
        self.assertEqual(span.attributes["citations.validation.retrieved_count"], 3)

    def test_rejects_unknown_citation_and_records_invalid_trace(self) -> None:
        span = RecordingSpan()

        with self.assertRaises(CitationValidationError) as raised:
            validate_citations(("chunk-1", "fabricated"), ("chunk-1",), span=span)

        self.assertFalse(raised.exception.result.valid)
        self.assertEqual(raised.exception.result.unknown_chunk_ids, ("fabricated",))
        self.assertEqual(span.attributes["citations.validation.valid"], False)
        self.assertEqual(span.attributes["citations.validation.unknown_count"], 1)

    def test_accepts_empty_citations_and_traces_success(self) -> None:
        span = RecordingSpan()

        result = validate_citations((), ("chunk-1",), span=span)

        self.assertTrue(result.valid)
        self.assertEqual(result.cited_chunk_ids, ())
        self.assertEqual(span.attributes["citations.validation.valid"], True)
        self.assertEqual(span.attributes["citations.validation.cited_count"], 0)

    def test_checks_every_citation_when_one_of_many_is_unknown(self) -> None:
        with self.assertRaises(CitationValidationError) as raised:
            validate_citations(
                ("valid-1", "unknown-1", "valid-2", "unknown-2"),
                ("valid-1", "valid-2"),
            )

        self.assertEqual(
            raised.exception.result.unknown_chunk_ids,
            ("unknown-1", "unknown-2"),
        )


if __name__ == "__main__":
    unittest.main()
