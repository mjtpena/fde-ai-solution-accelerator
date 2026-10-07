from collections.abc import AsyncIterator
from typing import Annotated, Literal, Protocol, runtime_checkable

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from accelerator.agent_core.workflows.grounded_answer import GroundedAnswerResult
from accelerator.identity.scope_resolver import get_execution_context
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


def get_chat_turn(request: Request) -> ChatTurnPort:
    workflow = getattr(request.app.state, "chat_turn", None)
    if not isinstance(workflow, ChatTurnPort):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The chat workflow is not configured.",
        )
    return workflow


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
                "Answered turns emit token frames followed by citations and done; "
                "insufficient evidence emits abstention and done; policy handoffs "
                "emit approval and done."
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
) -> StreamingResponse:
    result = await workflow.run(payload.message, context)
    if isinstance(result, ApprovalRequired):
        citations: list[CitationItem] = []
    else:
        citations = _validate_result(result)

    return StreamingResponse(
        _stream_result(result, citations),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
