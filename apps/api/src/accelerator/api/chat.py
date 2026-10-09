import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Annotated, Literal, Protocol, runtime_checkable

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from accelerator.agent_core.workflows.generation import current_token_budget, current_token_sink
from accelerator.agent_core.workflows.grounded_answer import GroundedAnswerResult
from accelerator.identity.scope_resolver import get_execution_context
from accelerator.security_core.cost_guard import TokenBudget
from accelerator.security_core.data_boundaries.context import ExecutionContext
from accelerator.security_core.tool_policy import ApprovalRequired


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        message = value.strip()
        if not message:
            raise ValueError("Message must not be blank.")
        return message


class TokenEvent(BaseModel):
    text: str


class CitationItem(BaseModel):
    chunk_id: str
    document_title: str
    source_uri: str


class CitationsEvent(BaseModel):
    citations: list[CitationItem]


class ApprovalCard(BaseModel):
    approval_id: str
    tool_name: str
    status: Literal["pending"] = "pending"


class ApprovalEvent(BaseModel):
    approval: ApprovalCard


class AbstentionEvent(BaseModel):
    reason: str
    evidence_ids: list[str]


class DoneEvent(BaseModel):
    pass


@runtime_checkable
class ChatTurnPort(Protocol):
    async def run(
        self, query: str, ctx: ExecutionContext
    ) -> GroundedAnswerResult | ApprovalRequired[BaseModel]: ...


router = APIRouter(prefix="/chat", tags=["chat"])
logger = logging.getLogger(__name__)

WITHDRAWN_REASON = (
    "The generated answer could not be verified against its sources and was withdrawn."
)
TurnResult = GroundedAnswerResult | ApprovalRequired[BaseModel]


def get_chat_turn(request: Request) -> ChatTurnPort:
    workflow = getattr(request.app.state, "chat_turn", None)
    if not isinstance(workflow, ChatTurnPort):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The chat workflow is not configured.",
        )
    return workflow


def get_request_token_budget(request: Request) -> TokenBudget | None:
    """The budget the route's cost-guard dependency (run first) stored for this request."""
    guard = getattr(request.state, "request_cost_guard", None)
    return getattr(guard, "token_budget", None)


def _frame(
    name: Literal["token", "citations", "approval", "abstention", "done"],
    data: BaseModel,
) -> str:
    return f"event: {name}\ndata: {data.model_dump_json()}\n\n"


def _validate_citations(result: GroundedAnswerResult) -> list[CitationItem]:
    citation_sources = result.citation_sources
    source_ids = tuple(source.chunk_id for source in citation_sources)
    if source_ids != result.citations:
        raise ValueError("Workflow citation metadata does not match validated citations.")

    return [
        CitationItem(
            chunk_id=source.chunk_id,
            document_title=source.document_title,
            source_uri=source.source_uri,
        )
        for source in citation_sources
    ]


def _validate_result(result: GroundedAnswerResult) -> list[CitationItem]:
    citations = _validate_citations(result)
    if result.status == "answered":
        if result.answer is None or result.abstention is not None:
            raise ValueError("Answered workflow result has an invalid answer shape.")
        return citations

    if result.status == "abstained":
        if (
            result.answer is not None
            or result.citations
            or result.citation_sources
            or result.abstention is None
        ):
            raise ValueError("Abstained workflow result has an invalid abstention shape.")
        return citations

    raise ValueError(f"Unsupported workflow status: {result.status}")


async def _stream_result(
    result: GroundedAnswerResult | ApprovalRequired[BaseModel],
    citations: list[CitationItem],
) -> AsyncIterator[str]:
    if isinstance(result, ApprovalRequired):
        yield _frame(
            "approval",
            ApprovalEvent(
                approval=ApprovalCard(
                    approval_id=str(result.approval_id),
                    tool_name=result.tool_name,
                )
            ),
        )
        yield _frame("done", DoneEvent())
        return

    if result.status == "abstained":
        if result.abstention is None:
            raise ValueError("Abstained workflow result is missing its structured reason.")
        yield _frame(
            "abstention",
            AbstentionEvent(
                reason=result.abstention.reason,
                evidence_ids=list(result.abstention.evidence_ids),
            ),
        )
    else:
        if result.answer is None:
            raise ValueError("Answered workflow result is missing its answer.")
        for offset in range(0, len(result.answer), 48):
            yield _frame(
                "token", TokenEvent(text=result.answer[offset : offset + 48])
            )
        if citations:
            yield _frame("citations", CitationsEvent(citations=citations))

    yield _frame("done", DoneEvent())


@router.post(
    "/stream",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": (
                "Server-sent events. Each frame has an event name and JSON data. "
                "Answered turns emit token frames as the model produces them, then "
                "citations (only after they validate against this turn's retrieval) "
                "and done; insufficient evidence emits abstention and done; policy "
                "handoffs emit approval and done. An abstention after token frames "
                "withdraws the streamed text, and clients must discard it. Clients "
                "must ignore event names they do not recognise."
            ),
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string"},
                    "x-sse-events": {
                        "token": TokenEvent.model_json_schema(),
                        "citations": CitationsEvent.model_json_schema(),
                        "approval": ApprovalEvent.model_json_schema(),
                        "abstention": AbstentionEvent.model_json_schema(),
                        "done": DoneEvent.model_json_schema(),
                    },
                }
            },
        },
        503: {"description": "The chat workflow is not configured."},
    },
)
async def stream_chat(
    payload: ChatRequest,
    context: Annotated[ExecutionContext, Depends(get_execution_context)],
    workflow: Annotated[ChatTurnPort, Depends(get_chat_turn)],
    token_budget: Annotated[TokenBudget | None, Depends(get_request_token_budget)] = None,
) -> StreamingResponse:
    tokens: asyncio.Queue[str] = asyncio.Queue()

    async def sink(text: str) -> None:
        await tokens.put(text)

    async def run_turn() -> TurnResult:
        # Per-turn context: streamed tokens go to the sink, and the cost guard's
        # token budget bounds every model call.
        current_token_sink.set(sink)
        current_token_budget.set(token_budget)
        return await workflow.run(payload.message, context)

    turn = asyncio.create_task(run_turn())
    first_token = await _next_token(tokens, turn)
    if first_token is None:
        # Nothing was streamed: failures and invalid results surface as errors
        # (500, or 429 for an exhausted budget) before any response bytes are sent.
        result = await turn
        citations: list[CitationItem] = (
            [] if isinstance(result, ApprovalRequired) else _validate_result(result)
        )
        frames = _stream_result(result, citations)
    else:
        frames = _stream_live(first_token, tokens, turn, context.correlation_id)

    return StreamingResponse(
        frames,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _next_token(tokens: asyncio.Queue[str], turn: asyncio.Task[TurnResult]) -> str | None:
    """The next streamed token, or ``None`` once the turn has finished and drained."""
    if not tokens.empty():
        return tokens.get_nowait()
    if turn.done():
        return None
    getter = asyncio.ensure_future(tokens.get())
    try:
        await asyncio.wait({getter, turn}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        if not getter.done():
            getter.cancel()
    if getter.done() and not getter.cancelled():
        return getter.result()
    return tokens.get_nowait() if not tokens.empty() else None


async def _stream_live(
    first_token: str,
    tokens: asyncio.Queue[str],
    turn: asyncio.Task[TurnResult],
    correlation_id: str,
) -> AsyncIterator[str]:
    """Forward model tokens as they arrive; emit citations only after validation.

    If the turn fails or its citations do not validate after text was streamed,
    an ``abstention`` frame withdraws the answer; clients must discard the
    streamed text when they receive it.
    """
    try:
        token: str | None = first_token
        while token is not None:
            yield _frame("token", TokenEvent(text=token))
            token = await _next_token(tokens, turn)
        try:
            result = await turn
            citations = (
                [] if isinstance(result, ApprovalRequired) else _validate_result(result)
            )
        except Exception as error:  # the answer is withdrawn, never silently kept
            logger.error(
                "streamed_answer_withdrawn",
                extra={"correlation_id": correlation_id, "exception_type": type(error).__name__},
            )
            yield _frame("abstention", AbstentionEvent(reason=WITHDRAWN_REASON, evidence_ids=[]))
            yield _frame("done", DoneEvent())
            return
        if isinstance(result, ApprovalRequired) or result.status == "abstained":
            async for frame in _stream_result(result, citations):
                yield frame
            return
        if citations:
            yield _frame("citations", CitationsEvent(citations=citations))
        yield _frame("done", DoneEvent())
    finally:
        if not turn.done():
            turn.cancel()
