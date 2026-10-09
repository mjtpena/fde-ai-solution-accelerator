"""Screened release (the default, required in production): no model text reaches the
client before citation validation and content safety have passed.

These tests run the real grounded-answer workflow (generator, citation validator,
output screening) behind the route and inspect the raw SSE bytes.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import Request
from fastapi.responses import StreamingResponse

from accelerator.agent_core.workflows.generation import AgentAnswerGenerator
from accelerator.agent_core.workflows.grounded_answer import (
    REFUSAL_REASONS,
    GroundedAnswerResult,
    GroundedAnswerWorkflow,
)
from accelerator.api.chat import (
    KEEPALIVE_FRAME,
    WITHDRAWN_REASON,
    ChatRequest,
    StreamRelease,
    get_stream_release,
    stream_chat,
)
from accelerator.retrieval_core.citations import (
    CitationValidationError,
    SameTurnCitationValidator,
)
from accelerator.security_core.content_safety import (
    HarmCategory,
    ScreenedDocument,
    ShieldResult,
    TextAnalysis,
)
from accelerator.security_core.data_boundaries.context import ExecutionContext

# Fast enough that a turn which sleeps briefly is still "slow" for the route.
SCREENED = StreamRelease(mode="screened", heartbeat_seconds=0.01)
INCREMENTAL = StreamRelease(mode="incremental", heartbeat_seconds=0.01)
HARMFUL = "HARMFUL-ANSWER-TEXT describes violence in detail."
UNGROUNDED = "UNGROUNDED-ANSWER-TEXT claims something no chunk says."
CLEAN = "Backups run nightly at two in the morning, every day of the week."


def context() -> ExecutionContext:
    return ExecutionContext(
        correlation_id="00000000-0000-0000-0000-0000000005e2",
        user_id="user-1",
        roles=frozenset({"Reader"}),
        scope_ids=frozenset({"scope-a"}),
        deadline_utc=datetime.now(UTC) + timedelta(minutes=1),
    )


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    text: str
    document_title: str = "Ops guide"
    source_uri: str = "https://docs.example.test/ops"


@dataclass(frozen=True)
class Decision:
    sufficient: bool
    reason: str
    evidence_ids: tuple[str, ...]


class Retriever:
    async def retrieve(self, req: str, ctx: ExecutionContext) -> tuple[Chunk, ...]:
        return (Chunk("chunk-1", "Backups run nightly."),)


class AlwaysSufficient:
    async def evaluate(self, evidence: Sequence[Chunk]) -> Decision:
        return Decision(True, "ok", tuple(item.chunk_id for item in evidence))


@dataclass(frozen=True)
class Reply:
    text: str


class ModelAgent:
    """A chat model that streams when asked to and takes ``delay`` seconds either way."""

    def __init__(self, reply: str, *, delay: float = 0.0) -> None:
        self.reply = reply
        self.delay = delay
        self.streamed = False

    def run(
        self,
        messages: str,
        *,
        options: Mapping[str, Any],
        tools: Sequence[Any] | None = None,
        stream: bool = False,
    ) -> Any:
        if stream:
            self.streamed = True
            return _Stream(self.reply, self.delay)
        return self._complete()

    async def _complete(self) -> Reply:
        await asyncio.sleep(self.delay)
        return Reply(self.reply)


class _Stream:
    def __init__(self, reply: str, delay: float) -> None:
        self.words = reply.split(" ")
        self.delay = delay

    async def __aiter__(self) -> AsyncIterator[Reply]:
        for index, word in enumerate(self.words):
            await asyncio.sleep(self.delay / len(self.words))
            yield Reply(word if index == 0 else f" {word}")

    async def get_final_response(self) -> Reply:
        return Reply(" ".join(self.words))


class HarmChecker:
    """Prompt Shields finds nothing; an answer containing HARMFUL is severity 6."""

    def __init__(self) -> None:
        self.analyzed: list[str] = []

    async def shield_prompt(
        self,
        user_prompt: str,
        documents: Sequence[ScreenedDocument],
        *,
        deadline_utc: datetime | None = None,
    ) -> ShieldResult:
        return ShieldResult(user_prompt_attack=False)

    async def analyze_text(
        self, text: str, *, deadline_utc: datetime | None = None
    ) -> TextAnalysis:
        self.analyzed.append(text)
        severity = 6 if "HARMFUL" in text else 0
        return TextAnalysis(dict.fromkeys(HarmCategory, 0) | {HarmCategory.VIOLENCE: severity})


def workflow(
    agent: ModelAgent, checker: HarmChecker
) -> GroundedAnswerWorkflow[str, ExecutionContext, Chunk]:
    return GroundedAnswerWorkflow(
        retriever=Retriever(),
        sufficiency_checker=AlwaysSufficient(),
        answer_generator=AgentAnswerGenerator(agent, max_output_tokens=64),
        citation_validator=SameTurnCitationValidator(),
        retrieval_request_factory=lambda query: query,
        content_safety_checker=checker,
    )


async def raw_body(response: StreamingResponse) -> str:
    chunks: list[str] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, str) else bytes(chunk).decode())
    return "".join(chunks)


def events(raw: str) -> list[tuple[str, dict[str, object]]]:
    """Named events in order; SSE comment frames (keepalives) are skipped."""
    parsed: list[tuple[str, dict[str, object]]] = []
    for frame in raw.split("\n\n"):
        lines = [line for line in frame.split("\n") if line and not line.startswith(":")]
        if not lines:
            continue
        event, data = lines
        parsed.append((event.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return parsed


@pytest.mark.parametrize(
    ("delay", "heartbeat"), [(0.0, 5.0), (0.1, 0.01)], ids=["fast-turn", "slow-turn"]
)
async def test_a_blocked_answer_sends_zero_answer_bytes(delay: float, heartbeat: float) -> None:
    """Fast turns finish before the response starts; slow ones are kept alive first."""
    agent = ModelAgent(f"{HARMFUL} [cite:chunk-1]", delay=delay)
    checker = HarmChecker()
    release = StreamRelease(mode="screened", heartbeat_seconds=heartbeat)

    response = await stream_chat(
        ChatRequest(message="q"), context(), workflow(agent, checker), release=release
    )
    raw = await raw_body(response)

    assert checker.analyzed == [HARMFUL]  # the model did produce it
    assert not agent.streamed  # the model call had no token sink at all
    for fragment in ("HARMFUL", "violence", "event: token"):
        assert fragment not in raw
    assert events(raw) == [
        (
            "abstention",
            {
                "reason": REFUSAL_REASONS["content_safety_output_blocked"],
                "evidence_ids": [],
                "code": "content_safety_output_blocked",
            },
        ),
        ("done", {}),
    ]
    assert (KEEPALIVE_FRAME in raw) is (delay > 0)


async def test_an_answer_failing_citation_validation_sends_zero_answer_bytes() -> None:
    agent = ModelAgent(f"{UNGROUNDED} [cite:fabricated]", delay=0.1)
    checker = HarmChecker()

    response = await stream_chat(
        ChatRequest(message="q"), context(), workflow(agent, checker), release=SCREENED
    )
    raw = await raw_body(response)

    assert checker.analyzed == []  # rejected before output screening
    for fragment in ("UNGROUNDED", "claims", "fabricated", "event: token", "event: citations"):
        assert fragment not in raw
    assert events(raw) == [
        (
            "abstention",
            {"reason": WITHDRAWN_REASON, "evidence_ids": [], "code": "answer_withdrawn"},
        ),
        ("done", {}),
    ]


async def test_a_fast_citation_failure_fails_before_any_response_bytes() -> None:
    agent = ModelAgent(f"{UNGROUNDED} [cite:fabricated]")

    with pytest.raises(CitationValidationError):
        await stream_chat(
            ChatRequest(message="q"), context(), workflow(agent, HarmChecker()), release=SCREENED
        )


async def test_incremental_mode_would_leak_the_blocked_answer() -> None:
    """Why production refuses incremental mode: the same turn leaks its tokens."""
    agent = ModelAgent(f"{HARMFUL} [cite:chunk-1]", delay=0.05)

    response = await stream_chat(
        ChatRequest(message="q"), context(), workflow(agent, HarmChecker()), release=INCREMENTAL
    )
    raw = await raw_body(response)

    assert "HARMFUL" in raw
    assert events(raw)[-2][1]["code"] == "content_safety_output_blocked"


async def test_a_released_answer_follows_screening_in_order() -> None:
    agent = ModelAgent(f"{CLEAN} [cite:chunk-1]", delay=0.1)
    checker = HarmChecker()
    response = await stream_chat(
        ChatRequest(message="q"), context(), workflow(agent, checker), release=SCREENED
    )

    chunks: list[str] = []
    async for chunk in response.body_iterator:
        text = chunk if isinstance(chunk, str) else bytes(chunk).decode()
        if text.startswith("event: token") and not any(
            c.startswith("event: token") for c in chunks
        ):
            # The first answer byte leaves only after the answer was screened.
            assert checker.analyzed == [CLEAN]
        chunks.append(text)
    raw = "".join(chunks)

    assert chunks[0] == KEEPALIVE_FRAME
    names = [name for name, _ in events(raw)]
    assert names[-2:] == ["citations", "done"]
    assert set(names[:-2]) == {"token"} and len(names) > 3  # released in chunks
    released = "".join(str(data["text"]) for name, data in events(raw) if name == "token")
    assert released == CLEAN
    citations = events(raw)[-2][1]["citations"]
    assert citations == [
        {
            "chunk_id": "chunk-1",
            "document_title": "Ops guide",
            "source_uri": "https://docs.example.test/ops",
        }
    ]


class GatedTurn:
    def __init__(self, result: GroundedAnswerResult) -> None:
        self.result = result
        self.release = asyncio.Event()
        self.cancelled = False

    async def run(self, query: str, ctx: ExecutionContext) -> GroundedAnswerResult:
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return self.result


ANSWERED = GroundedAnswerResult(
    status="answered",
    answer=CLEAN,
    citations=(),
    citation_sources=(),
    abstention=None,
)


async def test_keepalives_flow_while_the_turn_is_buffered() -> None:
    turn = GatedTurn(ANSWERED)
    response = await stream_chat(ChatRequest(message="q"), context(), turn, release=SCREENED)
    body = aiter(response.body_iterator)

    assert [await anext(body) for _ in range(3)] == [KEEPALIVE_FRAME] * 3
    turn.release.set()
    rest = [chunk async for chunk in body]

    assert all(isinstance(chunk, str) for chunk in rest)
    assert events("".join(str(chunk) for chunk in rest))[-1] == ("done", {})


async def test_client_disconnect_while_buffering_cancels_the_turn() -> None:
    turn = GatedTurn(ANSWERED)
    response = await stream_chat(ChatRequest(message="q"), context(), turn, release=SCREENED)
    body = aiter(response.body_iterator)
    await anext(body)

    await body.aclose()  # type: ignore[attr-defined]
    for _ in range(3):
        await asyncio.sleep(0)

    assert turn.cancelled


def test_the_route_takes_its_release_mode_from_settings_and_defaults_to_screened() -> None:
    def request(state: SimpleNamespace) -> Request:
        return cast(Request, SimpleNamespace(app=SimpleNamespace(state=state)))

    configured = SimpleNamespace(
        settings=SimpleNamespace(stream_release_mode="incremental", stream_heartbeat_seconds=2.0)
    )

    assert get_stream_release(request(configured)) == StreamRelease("incremental", 2.0)
    assert get_stream_release(request(SimpleNamespace())) == StreamRelease("screened", 5.0)
