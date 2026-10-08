import asyncio
import json
from datetime import UTC, datetime, timedelta
import unittest
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError

from accelerator.agent_core.workflows.grounded_answer import (
    Abstention,
    CitationSource,
    GroundedAnswerResult,
    RetrievedEvidenceContext,
)
from accelerator.api.app import create_app
from accelerator.api.chat import ChatRequest, stream_chat
from accelerator.configuration.settings import Settings
from accelerator.domain.audit import AuditRepository
from accelerator.identity.authentication import AppRole, Principal, get_current_principal
from accelerator.identity.scope_resolver import configure_scope_resolver
from accelerator.security_core.data_boundaries.context import ExecutionContext
from accelerator.security_core.tool_policy import ApprovalRequired


class ToolArguments(BaseModel):
    record_id: str


class FakeChatTurn:
    def __init__(
        self, result: GroundedAnswerResult | ApprovalRequired[BaseModel]
    ) -> None:
        self.result = result
        self.query: str | None = None
        self.context: ExecutionContext | None = None
        self.roles_seen: frozenset[str] | None = None

    async def run(
        self, query: str, ctx: ExecutionContext
    ) -> GroundedAnswerResult | ApprovalRequired[BaseModel]:
        self.query = query
        self.context = ctx
        self.roles_seen = ctx.roles
        return self.result


def execution_context() -> ExecutionContext:
    return ExecutionContext(
        correlation_id="a51a8e92-2ae4-4a80-ad1c-3a3dd61a3b9b",
        user_id="user-1",
        roles=frozenset({"Reader"}),
        scope_ids=frozenset({"scope-1"}),
        deadline_utc=datetime.now(UTC) + timedelta(minutes=1),
    )


def make_test_settings() -> Settings:
    return Settings(
        environment="test",
        entra_tenant_id=UUID("00000000-0000-0000-0000-000000000001"),
        entra_audience="api://test",
    )


class InMemoryMemberships:
    async def scope_ids_for(self, user_id: str) -> frozenset[str]:
        return frozenset({"scope-1"}) if user_id == "user-1" else frozenset()


async def collect_stream(response: StreamingResponse) -> str:
    chunks: list[str] = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, bytes):
            chunks.append(chunk.decode())
        else:
            chunks.append(chunk)
    return "".join(chunks)


class ChatEndpointTests(unittest.TestCase):
    def test_rejects_citation_metadata_mismatch_before_streaming(self) -> None:
        workflow = FakeChatTurn(
            GroundedAnswerResult(
                status="answered",
                answer="Answer must not be streamed.",
                citations=("chunk-1",),
                citation_sources=(
                    CitationSource(
                        chunk_id="chunk-other",
                        document_title="Other guide",
                        source_uri="https://docs.example/other",
                    ),
                ),
                abstention=None,
            )
        )
        with self.assertRaisesRegex(ValueError, "does not match validated citations"):
            asyncio.run(
                stream_chat(ChatRequest(message="Question"), execution_context(), workflow)
            )

    def test_request_rejects_caller_supplied_scope(self) -> None:
        with self.assertRaises(ValidationError):
            ChatRequest(message="What is supported?", scope_ids=["scope-2"])

    def test_streams_answer_and_same_turn_citations(self) -> None:
        context = execution_context()
        workflow = FakeChatTurn(
            GroundedAnswerResult(
                status="answered",
                answer="The guide supports this answer.",
                citations=("chunk-1",),
                citation_sources=(
                    CitationSource(
                        chunk_id="chunk-1",
                        document_title="Guide",
                        source_uri="https://docs.example/guide",
                    ),
                ),
                abstention=None,
                evaluation_context=(
                    RetrievedEvidenceContext(
                        chunk_id="chunk-1",
                        text="Untrusted retrieved text must not appear in SSE.",
                    ),
                ),
            )
        )

        response = asyncio.run(
            stream_chat(
                ChatRequest(message="  What does the guide say?  "),
                context,
                workflow,
            )
        )
        body = asyncio.run(collect_stream(response))

        self.assertEqual(response.media_type, "text/event-stream")
        self.assertIn('event: token\ndata: {"text":"The guide supports this answer."}', body)
        self.assertIn('"chunk_id":"chunk-1"', body)
        self.assertIn('"document_title":"Guide"', body)
        self.assertNotIn("Untrusted retrieved text", body)
        self.assertNotIn("evaluation_context", body)
        self.assertTrue(body.endswith("event: done\ndata: {}\n\n"))
        self.assertEqual(workflow.query, "What does the guide say?")
        self.assertIs(workflow.context, context)
        self.assertEqual(workflow.roles_seen, frozenset({"Reader"}))

    def test_streams_abstention_without_answer_tokens(self) -> None:
        workflow = FakeChatTurn(
            GroundedAnswerResult(
                status="abstained",
                answer=None,
                citations=(),
                citation_sources=(),
                abstention=Abstention(
                    reason="Retrieved evidence is insufficient.",
                    evidence_ids=("chunk-2",),
                ),
                evaluation_context=(
                    RetrievedEvidenceContext(
                        chunk_id="chunk-2",
                        text="Insufficient retrieved text must not appear in SSE.",
                    ),
                ),
            )
        )

        response = asyncio.run(
            stream_chat(ChatRequest(message="Unsupported question"), execution_context(), workflow)
        )
        body = asyncio.run(collect_stream(response))

        self.assertNotIn("event: token", body)
        self.assertIn("event: abstention", body)
        self.assertIn('"reason":"Retrieved evidence is insufficient."', body)
        self.assertIn('"evidence_ids":["chunk-2"]', body)
        self.assertNotIn("Insufficient retrieved text", body)
        self.assertNotIn("evaluation_context", body)

    def test_streams_policy_approval_without_exposing_bound_arguments(self) -> None:
        context = execution_context()
        workflow = FakeChatTurn(
            ApprovalRequired[ToolArguments](
                approval_id=UUID("805f3e22-69fc-4cf8-8374-1a2f04ff7849"),
                tool_name="write_record",
                arguments=ToolArguments(record_id="record-1"),
                args_hash="server-generated-args-hash",
                scope_id="scope-1",
                requested_by="user-1",
                correlation_id=context.correlation_id,
            )
        )

        response = asyncio.run(
            stream_chat(ChatRequest(message="Update this record"), context, workflow)
        )
        body = asyncio.run(collect_stream(response))

        self.assertIn("event: approval", body)
        self.assertIn('"approval_id":"805f3e22-69fc-4cf8-8374-1a2f04ff7849"', body)
        self.assertIn('"tool_name":"write_record"', body)
        self.assertNotIn("record-1", body)
        self.assertNotIn("server-generated-args-hash", body)
        self.assertNotIn('"scope_id"', body)
        self.assertIs(workflow.context, context)


class ChatOpenApiTests(unittest.TestCase):
    def test_chat_stream_is_documented_as_server_sent_events(self) -> None:
        app = create_app(make_test_settings())
        operation = app.openapi()["paths"]["/chat/stream"]["post"]

        response = operation["responses"]["200"]
        self.assertEqual(
            response["content"]["text/event-stream"]["schema"], {"type": "string"}
        )
        self.assertEqual(
            set(response["content"]["text/event-stream"]["x-sse-events"]),
            {"token", "citations", "approval", "abstention", "done"},
        )
        request_schema = app.openapi()["components"]["schemas"]["ChatRequest"]
        self.assertFalse(request_schema["additionalProperties"])
        self.assertEqual(set(request_schema["properties"]), {"message"})


@pytest.mark.asyncio
async def test_http_requires_authentication_before_running_chat() -> None:
    app = create_app(
        make_test_settings(),
        audit_repository=AsyncMock(spec=AuditRepository),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/chat/stream", json={"message": "Question"})
    assert response.status_code == 401
    assert response.headers["X-Correlation-ID"]


@pytest.mark.asyncio
async def test_http_resolves_context_and_rejects_scope_input() -> None:
    workflow = FakeChatTurn(
        GroundedAnswerResult(
            status="abstained",
            answer=None,
            citations=(),
            citation_sources=(),
            abstention=Abstention(reason="Insufficient evidence.", evidence_ids=()),
        )
    )
    app = create_app(make_test_settings(), chat_turn=workflow)

    async def principal() -> Principal:
        return Principal(
            subject="subject", object_id="user-1", roles=frozenset({AppRole.READER})
        )

    app.dependency_overrides[get_current_principal] = principal
    configure_scope_resolver(app, InMemoryMemberships())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        invalid = await client.post(
            "/chat/stream", json={"message": "Question", "scope_ids": ["scope-private"]}
        )
        assert invalid.status_code == 422
        assert workflow.context is None
        response = await client.post("/chat/stream", json={"message": "Question"})
    assert response.status_code == 200
    assert "event: abstention" in response.text
    assert workflow.context is not None
    assert workflow.context.user_id == "user-1"
    assert workflow.context.scope_ids == frozenset({"scope-1"})
    assert workflow.context.roles == frozenset({"Reader"})
    assert workflow.context.correlation_id == response.headers["X-Correlation-ID"]


@pytest.mark.asyncio
async def test_http_fails_closed_without_host_scope_repository() -> None:
    app = create_app(make_test_settings())

    async def principal() -> Principal:
        return Principal(
            subject="subject", object_id="user-1", roles=frozenset({AppRole.READER})
        )

    app.dependency_overrides[get_current_principal] = principal
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/chat/stream", json={"message": "Question"})
    assert response.status_code == 503
    assert response.headers["X-Correlation-ID"]


@pytest.mark.asyncio
async def test_configured_app_authenticates_and_streams_without_dependency_overrides() -> None:
    settings = make_test_settings()
    workflow = FakeChatTurn(
        GroundedAnswerResult(
            status="answered",
            answer="Configured grounded answer.",
            citations=("chunk-1",),
            citation_sources=(
                CitationSource(
                    chunk_id="chunk-1",
                    document_title="Guide",
                    source_uri="https://docs.example/guide",
                ),
            ),
            abstention=None,
        )
    )
    app = create_app(
        settings, chat_turn=workflow, scope_repository=InMemoryMemberships()
    )
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": "chat-test-key", "use": "sig", "alg": "RS256"})
    token = jwt.encode(
        {
            "iss": f"https://login.microsoftonline.com/{settings.entra_tenant_id}/v2.0",
            "aud": settings.entra_audience,
            "sub": "subject",
            "oid": "user-1",
            "roles": ["Reader"],
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "chat-test-key"},
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"keys": [jwk]})
        )
    ) as identity_client:
        app.state.http_client = identity_client
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/chat/stream",
                json={"message": "Question"},
                headers={"Authorization": f"Bearer {token}"},
            )

    assert not app.dependency_overrides
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "Configured grounded answer." in response.text
    assert '"chunk_id":"chunk-1"' in response.text
    assert response.text.endswith("event: done\ndata: {}\n\n")
    assert workflow.context is not None
    assert workflow.context.user_id == "user-1"
    assert workflow.context.scope_ids == frozenset({"scope-1"})
    assert workflow.context.roles == frozenset({"Reader"})
    assert workflow.context.correlation_id == response.headers["X-Correlation-ID"]


@pytest.mark.asyncio
async def test_http_fails_closed_without_host_chat_workflow() -> None:
    app = create_app(make_test_settings())

    async def principal() -> Principal:
        return Principal(
            subject="subject", object_id="user-1", roles=frozenset({AppRole.READER})
        )

    app.dependency_overrides[get_current_principal] = principal
    configure_scope_resolver(app, InMemoryMemberships())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/chat/stream", json={"message": "Question"})
    assert response.status_code == 503
    assert response.json() == {"detail": "The chat workflow is not configured."}
    assert response.headers["X-Correlation-ID"]


if __name__ == "__main__":
    unittest.main()
