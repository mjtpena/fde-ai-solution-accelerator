import asyncio
from datetime import UTC, datetime, timedelta
import unittest
from uuid import UUID

from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError

from accelerator.agent_core.workflows.grounded_answer import (
    Abstention,
    CitationSource,
    GroundedAnswerResult,
)
from accelerator.api.app import create_app
from accelerator.api.chat import ChatRequest, stream_chat
from accelerator.configuration.settings import Settings
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


async def collect_stream(response: StreamingResponse) -> str:
    chunks: list[str] = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, bytes):
            chunks.append(chunk.decode())
        else:
            chunks.append(chunk)
    return "".join(chunks)


class ChatEndpointTests(unittest.TestCase):
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
        app = create_app(Settings(environment="test"))
        operation = app.openapi()["paths"]["/chat/stream"]["post"]

        self.assertIn("text/event-stream", operation["responses"]["200"]["content"])


if __name__ == "__main__":
    unittest.main()
