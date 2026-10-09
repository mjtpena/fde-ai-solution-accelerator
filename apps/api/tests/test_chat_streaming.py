"""Token frames leave the API while the model is still generating."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from accelerator.agent_core.workflows.generation import current_token_sink
from accelerator.agent_core.workflows.grounded_answer import CitationSource, GroundedAnswerResult
from accelerator.api.chat import WITHDRAWN_REASON, ChatRequest, stream_chat
from accelerator.retrieval_core.citations import CitationValidationError, CitationValidationResult
from accelerator.security_core.data_boundaries.context import ExecutionContext
from accelerator.security_core.tool_policy import ApprovalRequired


def context() -> ExecutionContext:
    return ExecutionContext(
        correlation_id="00000000-0000-0000-0000-0000000005e1",
        user_id="user-1",
        roles=frozenset({"Reader"}),
        scope_ids=frozenset({"scope-a"}),
        deadline_utc=datetime.now(UTC) + timedelta(minutes=1),
    )


ANSWERED = GroundedAnswerResult(
    status="answered",
    answer="Backups run nightly.",
    citations=("chunk-1",),
    citation_sources=(
        CitationSource(chunk_id="chunk-1", document_title="Ops", source_uri="https://d.test/1"),
    ),
    abstention=None,
)


class GatedStreamingTurn:
    """Streams one token, then waits until the test has received it."""

    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.release = asyncio.Event()
        self.finished = False
        self.cancelled = False

    async def run(self, query: str, ctx: ExecutionContext) -> object:
        sink = current_token_sink.get()
        assert sink is not None
        await sink("Backups ")
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        await sink("run nightly.")
        self.finished = True
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def parse(frame: str) -> tuple[str, dict[str, object]]:
    event, data = frame.strip().split("\n")
    return event.removeprefix("event: "), json.loads(data.removeprefix("data: "))


async def frames(response: StreamingResponse) -> AsyncIterator[tuple[str, dict[str, object]]]:
    async for chunk in response.body_iterator:
        yield parse(chunk if isinstance(chunk, str) else bytes(chunk).decode())


async def test_first_token_is_sent_before_generation_finishes() -> None:
    turn = GatedStreamingTurn(ANSWERED)
    response = await stream_chat(ChatRequest(message="When do backups run?"), context(), turn)
    received = frames(response)

    first = await anext(received)
    assert first == ("token", {"text": "Backups "})
    assert not turn.finished
    turn.release.set()
    rest = [frame async for frame in received]

    assert rest[0] == ("token", {"text": "run nightly."})
    assert rest[1][0] == "citations"
    assert rest[1][1]["citations"][0]["chunk_id"] == "chunk-1"  # type: ignore[index]
    assert rest[2] == ("done", {})


async def test_invalid_citations_after_streaming_withdraw_the_answer() -> None:
    failure = CitationValidationError(
        CitationValidationResult(
            valid=False,
            cited_chunk_ids=("fake",),
            unknown_chunk_ids=("fake",),
            retrieved_chunk_count=1,
        )
    )
    turn = GatedStreamingTurn(failure)
    turn.release.set()

    response = await stream_chat(ChatRequest(message="q"), context(), turn)
    events = [frame async for frame in frames(response)]

    assert [name for name, _ in events] == ["token", "token", "abstention", "done"]
    assert events[2][1] == {"reason": WITHDRAWN_REASON, "evidence_ids": []}
    assert all(name != "citations" for name, _ in events)


class Args(BaseModel):
    record: str


async def test_approval_after_streamed_text_is_still_delivered() -> None:
    approval = ApprovalRequired[Args](
        approval_id="6f1c2b8e-3f3a-4d5e-8a9b-0c1d2e3f4a5b",  # type: ignore[arg-type]
        tool_name="update_record",
        arguments=Args(record="r"),
        args_hash="0" * 64,
        scope_id="scope-a",
        requested_by="user-1",
        correlation_id="c",
    )
    turn = GatedStreamingTurn(approval)
    turn.release.set()

    response = await stream_chat(ChatRequest(message="q"), context(), turn)
    events = [name for name, _ in [frame async for frame in frames(response)]]

    assert events == ["token", "token", "approval", "done"]


async def test_client_disconnect_cancels_generation() -> None:
    turn = GatedStreamingTurn(ANSWERED)
    response = await stream_chat(ChatRequest(message="q"), context(), turn)
    received = frames(response)
    await anext(received)

    await received.aclose()  # type: ignore[attr-defined]
    await response.body_iterator.aclose()  # type: ignore[attr-defined]
    for _ in range(3):
        await asyncio.sleep(0)

    assert turn.cancelled
    assert not turn.finished
