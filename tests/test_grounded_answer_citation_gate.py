"""A fabricated chunk ID must fail the whole response, end to end.

Uses the production sufficiency checker and citation validator with a scripted
retriever and generator, then drives the same workflow through POST /chat/stream.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest

from accelerator.agent_core.workflows.grounded_answer import GroundedAnswerWorkflow
from accelerator.api.app import create_app
from accelerator.configuration.settings import Settings
from accelerator.identity.authentication import AppRole, Principal, get_current_principal
from accelerator.identity.scope_resolver import get_execution_context
from accelerator.retrieval_core.citations import (
    CitationValidationError,
    SameTurnCitationValidator,
)
from accelerator.retrieval_core.models import Evidence, RetrievalRequest
from accelerator.retrieval_core.sufficiency import (
    EvidenceSufficiencyChecker,
    SufficiencyPolicy,
)
from accelerator.security_core.data_boundaries.context import ExecutionContext


def context() -> ExecutionContext:
    return ExecutionContext(
        correlation_id="00000000-0000-0000-0000-00000000c001",
        user_id="user-1",
        roles=frozenset({"Reader"}),
        scope_ids=frozenset({"scope-a"}),
        deadline_utc=datetime.now(UTC) + timedelta(minutes=1),
    )


EVIDENCE = (
    Evidence(
        chunk_id="chunk-1",
        document_id="doc-1",
        document_title="Guide",
        version="1",
        score=0.9,
        reranker_score=3.1,
        text="The guide says retention is 30 days.",
        source_uri="https://docs.example.test/guide",
    ),
)


class ScriptedRetriever:
    async def retrieve(self, req: RetrievalRequest, ctx: ExecutionContext) -> list[Evidence]:
        assert ctx.scope_ids == frozenset({"scope-a"})
        return list(EVIDENCE)


@dataclass(frozen=True)
class Generated:
    answer: str
    citations: tuple[str, ...]


class ScriptedGenerator:
    def __init__(self, citations: tuple[str, ...]) -> None:
        self.citations = citations

    async def generate(self, query: str, evidence: Sequence[Evidence]) -> Generated:
        return Generated("Retention is 30 days.", self.citations)


def workflow(
    citations: tuple[str, ...],
) -> GroundedAnswerWorkflow[RetrievalRequest, ExecutionContext, Evidence]:
    return GroundedAnswerWorkflow(
        retriever=ScriptedRetriever(),
        sufficiency_checker=EvidenceSufficiencyChecker(
            SufficiencyPolicy(minimum_score=0.5, minimum_evidence_count=1)
        ),
        answer_generator=ScriptedGenerator(citations),
        citation_validator=SameTurnCitationValidator(),
        retrieval_request_factory=lambda query: RetrievalRequest(query=query),
    )


async def test_valid_same_turn_citation_is_answered() -> None:
    result = await workflow(("chunk-1",)).run("How long is retention?", context())

    assert result.status == "answered"
    assert result.citations == ("chunk-1",)


@pytest.mark.parametrize("citations", [("fabricated-chunk",), ("chunk-1", "fabricated-chunk")])
async def test_fake_chunk_id_fails_the_workflow(citations: tuple[str, ...]) -> None:
    with pytest.raises(CitationValidationError) as raised:
        await workflow(citations).run("How long is retention?", context())

    assert raised.value.result.unknown_chunk_ids == ("fabricated-chunk",)


async def test_fake_chunk_id_fails_the_http_response_without_streaming_text() -> None:
    app = create_app(
        Settings(
            environment="test",
            entra_tenant_id=UUID("00000000-0000-0000-0000-000000000001"),
            entra_audience="api://test",
        ),
        chat_turn=workflow(("fabricated-chunk",)),
    )

    async def reader() -> Principal:
        return Principal(subject="user-1", object_id="user-1", roles=frozenset({AppRole.READER}))

    async def trusted_context() -> ExecutionContext:
        return context()

    app.dependency_overrides[get_current_principal] = reader
    app.dependency_overrides[get_execution_context] = trusted_context
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post("/chat/stream", json={"message": "How long is retention?"})

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error."}
    assert "Retention is 30 days" not in response.text
    assert "event: token" not in response.text
