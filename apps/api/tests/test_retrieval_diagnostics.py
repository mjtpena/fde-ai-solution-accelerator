import asyncio
from datetime import UTC, datetime
import unittest
from uuid import UUID

from fastapi import HTTPException
from fastapi.testclient import TestClient
from httpx import Response

from accelerator.api.app import create_app
from accelerator.api.retrieval_diagnostics import (
    InMemoryRetrievalDiagnosticsStore,
    RetrievalDiagnosticRecord,
    RetrievalDiagnosticResult,
    RetrievalDiagnosticsResponse,
    get_retrieval_diagnostics,
    require_contributor,
)
from accelerator.configuration.settings import Settings
from accelerator.identity.authentication import get_current_principal
from accelerator.identity.scope_resolver import get_execution_context
from accelerator.security_core.data_boundaries.context import ExecutionContext


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

    def settings(self, diagnostics_include_content: bool = False) -> Settings:
        return Settings(
            environment="test",
            entra_tenant_id=UUID("00000000-0000-0000-0000-000000000001"),
            entra_audience="api://test",
            diagnostics_include_content=diagnostics_include_content,
        )

    def get_response(self, context: ExecutionContext) -> Response:
        app = create_app(self.settings(), self.store)
        app.dependency_overrides[get_execution_context] = lambda: context
        app.dependency_overrides[get_current_principal] = lambda: None

        with TestClient(app) as client:
            response = client.get("/diagnostics/retrieval/trace-1")
        if not isinstance(response, Response):
            raise AssertionError("Expected an HTTP response")
        return response

    def test_requires_contributor_role(self) -> None:
        reader = self.contributor.model_copy(update={"roles": frozenset({"Reader"})})

        with self.assertRaises(HTTPException) as raised:
            require_contributor(reader)

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(self.get_response(reader).status_code, 403)

    def test_accepts_normalized_contributor_role(self) -> None:
        contributor = self.contributor.model_copy(update={"roles": frozenset({"CONTRIBUTOR"})})

        self.assertEqual(require_contributor(contributor), contributor)

    def test_response_includes_filters_results_and_scores_but_redacts_content(self) -> None:
        response = asyncio.run(
            get_retrieval_diagnostics(
                "trace-1",
                self.contributor,
                self.store,
                self.settings(),
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

    def test_http_response_contains_diagnostics_with_redacted_content(self) -> None:
        response = self.get_response(self.contributor)
        body = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["correlation_id"], "trace-1")
        self.assertEqual(body["query"], "[REDACTED]")
        self.assertEqual(body["filters"], {"scope_id": "scope-a"})
        self.assertEqual(body["results"][0]["score"], 0.9)
        self.assertEqual(body["results"][0]["reranker_score"], 0.8)
        self.assertEqual(body["results"][0]["text"], "[REDACTED]")

    def test_content_is_returned_only_when_enabled_by_settings(self) -> None:
        response = asyncio.run(
            get_retrieval_diagnostics(
                "trace-1",
                self.contributor,
                self.store,
                self.settings(diagnostics_include_content=True),
            )
        )

        self.assertEqual(response.query, "sensitive query")
        self.assertEqual(response.results[0].text, "sensitive document text")

    def test_diagnostics_are_not_disclosed_outside_execution_scope(self) -> None:
        other_scope = self.contributor.model_copy(update={"scope_ids": frozenset({"scope-b"})})

        with self.assertRaises(HTTPException) as raised:
            asyncio.run(
                get_retrieval_diagnostics(
                    "trace-1", other_scope, self.store, self.settings()
                )
            )

        self.assertEqual(raised.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
