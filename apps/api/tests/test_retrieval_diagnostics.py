import asyncio
from datetime import UTC, datetime
import unittest

from fastapi import HTTPException, Request

from accelerator.api.app import create_app
from accelerator.api.retrieval_diagnostics import (
    InMemoryRetrievalDiagnosticsStore,
    RetrievalDiagnosticRecord,
    RetrievalDiagnosticResult,
    RetrievalDiagnosticsResponse,
    get_execution_context,
    get_retrieval_diagnostics,
    require_contributor,
)
from accelerator.configuration.settings import Settings
from accelerator.identity.execution_context import ExecutionContext


class RetrievalDiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryRetrievalDiagnosticsStore()
        self.record = RetrievalDiagnosticRecord(
            correlation_id="trace-1",
            scope_ids=frozenset({"scope-a"}),
            query="sensitive query",
            filters={"scope_id": "scope-a"},
            results=[
                RetrievalDiagnosticResult(
                    chunk_id="chunk-1",
                    document_id="document-1",
                    document_title="Example",
                    score=0.9,
                    reranker_score=0.8,
                    text="sensitive document text",
                    source_uri="https://example.test/document-1",
                )
            ],
        )
        asyncio.run(self.store.save(self.record))
        self.contributor = ExecutionContext(
            correlation_id="request-1",
            user_id="user-1",
            roles=frozenset({"Contributor"}),
            scope_ids=frozenset({"scope-a"}),
            deadline_utc=datetime.now(UTC),
        )

    def test_requires_authenticated_execution_context(self) -> None:
        request = Request({"type": "http", "app": create_app(Settings(environment="test"))})

        with self.assertRaises(HTTPException) as raised:
            get_execution_context(request)

        self.assertEqual(raised.exception.status_code, 401)

    def test_requires_contributor_role(self) -> None:
        reader = self.contributor.model_copy(update={"roles": frozenset({"Reader"})})

        with self.assertRaises(HTTPException) as raised:
            require_contributor(reader)

        self.assertEqual(raised.exception.status_code, 403)

    def test_accepts_normalized_contributor_role(self) -> None:
        contributor = self.contributor.model_copy(update={"roles": frozenset({"CONTRIBUTOR"})})

        self.assertEqual(require_contributor(contributor), contributor)

    def test_response_includes_filters_results_and_scores_but_redacts_content(self) -> None:
        response = asyncio.run(
            get_retrieval_diagnostics(
                "trace-1",
                self.contributor,
                self.store,
                Settings(environment="test"),
            )
        )

        self.assertEqual(
            response,
            RetrievalDiagnosticsResponse(
                correlation_id="trace-1",
                query="[REDACTED]",
                filters={"scope_id": "scope-a"},
                results=[
                    self.record.results[0].model_copy(update={"text": "[REDACTED]"})
                ],
            ),
        )
        self.assertEqual(response.results[0].score, 0.9)
        self.assertEqual(response.results[0].reranker_score, 0.8)

    def test_content_is_returned_only_when_enabled_by_settings(self) -> None:
        response = asyncio.run(
            get_retrieval_diagnostics(
                "trace-1",
                self.contributor,
                self.store,
                Settings(environment="test", diagnostics_include_content=True),
            )
        )

        self.assertEqual(response.query, "sensitive query")
        self.assertEqual(response.results[0].text, "sensitive document text")

    def test_diagnostics_are_not_disclosed_outside_execution_scope(self) -> None:
        other_scope = self.contributor.model_copy(update={"scope_ids": frozenset({"scope-b"})})

        with self.assertRaises(HTTPException) as raised:
            asyncio.run(
                get_retrieval_diagnostics(
                    "trace-1", other_scope, self.store, Settings(environment="test")
                )
            )

        self.assertEqual(raised.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
