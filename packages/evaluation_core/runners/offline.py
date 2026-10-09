"""Deterministic offline runtime for the smoke evaluation.

The smoke gate runs the product's own grounded-answer workflow, sufficiency gate,
citation validator, untrusted-evidence wrapper, tool bridge, tool policy and
approval service over the fixture corpus. Only two pieces are offline stand-ins:

* ``OfflineRetriever`` scores chunks lexically instead of with Azure AI Search, and
  filters them by the trusted ``ExecutionContext`` scopes exactly as the Search
  adapter does.
* ``OfflineModel`` replaces the Foundry model. It answers extractively from the
  evidence elements in the prompt and cites them, and it picks a tool when the
  question names one. It is deliberately naive about prompt structure: any
  ``SYSTEM:``/``INSTRUCTION:`` directive it finds outside an ``<evidence>`` element
  is obeyed. Injection metrics therefore fail if retrieved text ever escapes its
  evidence element, which is what the wrapper must prevent.

What this measures is the deterministic control plane (scope filtering, abstention,
citation validation, evidence wrapping, tool routing, approval binding), not model
judgement; ``make eval-full`` measures the real model with Foundry evaluators.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from accelerator.retrieval_core.models import Evidence, RetrievalRequest
from accelerator.security_core.data_boundaries.context import ExecutionContext

from .corpus import CorpusChunk

if TYPE_CHECKING:
    from packages.agent_core.approvals.models import Approval, ApprovalAuditEvent
    from packages.agent_core.tools import (
        EnterpriseTool,
        ExecutionContextProtocol,
        IdempotentWriteTool,
        ToolRisk,
    )
else:
    from accelerator.agent_core.approvals.models import Approval, ApprovalAuditEvent
    from accelerator.agent_core.tools import (
        EnterpriseTool,
        ExecutionContextProtocol,
        IdempotentWriteTool,
        ToolRisk,
    )

_STOPWORDS = frozenset(
    "a about an and any are as at be by can do does for from give how i in is it its "
    "many me much of on or please should that the their then this to was what when "
    "where which who why will with".split()
)
_DIRECTIVE = re.compile(
    r"^\s*(?:SYSTEM|INSTRUCTION):\s*(?P<verb>reply|call)\s+(?P<rest>.+?)\s*$", re.MULTILINE
)
_EVIDENCE = re.compile(
    r'<evidence chunk_id="(?P<chunk_id>[^"]*)" title="(?P<title>[^"]*)">\n'
    r"(?P<text>.*?)\n</evidence>",
    re.DOTALL,
)
_QUESTION = re.compile(r"<question>(?P<text>.*?)</question>", re.DOTALL)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")


def terms(text: str) -> frozenset[str]:
    """Lowercase content words with a light plural/possessive strip."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return frozenset(
        word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word
        for word in words
        if word not in _STOPWORDS
    )


def _overlap(query: frozenset[str], text: str) -> float:
    if not query:
        return 0.0
    return len(query & terms(text)) / len(query)


class OfflineRetriever:
    """Lexical retrieval over the corpus, filtered by the trusted context's scopes."""

    def __init__(self, chunks: Sequence[CorpusChunk]) -> None:
        self._chunks = tuple(chunks)
        self.returned: list[CorpusChunk] = []

    async def retrieve(self, request: RetrievalRequest, ctx: ExecutionContext) -> list[Evidence]:
        query = terms(request.query)
        scored = sorted(
            (
                (
                    _overlap(
                        query, f"{chunk.document_title} {chunk.section_heading or ''} {chunk.text}"
                    ),
                    chunk,
                )
                for chunk in self._chunks
                # The scope filter comes only from the server-side context.
                if chunk.scope_id in ctx.scope_ids
            ),
            key=lambda item: (-item[0], item[1].chunk_id),
        )
        selected = [(score, chunk) for score, chunk in scored if score > 0][: request.top_k]
        self.returned.extend(chunk for _, chunk in selected)
        return [
            Evidence(
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                document_title=chunk.document_title,
                version=chunk.version,
                score=score,
                reranker_score=None,
                text=chunk.text,
                source_uri=chunk.source_uri,
            )
            for score, chunk in selected
        ]


@dataclass(frozen=True, slots=True)
class OfflineResponse:
    text: str
    usage_details: Mapping[str, int] | None = None


@dataclass(frozen=True, slots=True)
class _PromptEvidence:
    chunk_id: str
    title: str
    text: str


class OfflineModel:
    """A deterministic ``ChatAgent`` stand-in: extractive answers, keyword tool choice."""

    def __init__(self, *, relative_relevance: float = 0.5, minimum_relevance: float = 0.25) -> None:
        self._relative_relevance = relative_relevance
        self._minimum_relevance = minimum_relevance

    async def run(
        self,
        messages: str,
        *,
        options: Mapping[str, Any],
        tools: Sequence[Any] | None = None,
        stream: bool = False,
    ) -> OfflineResponse:
        del options
        if stream:
            raise NotImplementedError("The offline model does not stream.")
        evidence = [
            _PromptEvidence(
                html.unescape(match["chunk_id"]),
                html.unescape(match["title"]),
                html.unescape(match["text"]),
            )
            for match in _EVIDENCE.finditer(messages)
        ]
        question_match = _QUESTION.search(messages)
        question = html.unescape(question_match["text"]) if question_match else ""
        offered = {tool.name: tool for tool in tools or ()}

        # Everything outside an evidence element is read as instructions.
        outside = _EVIDENCE.sub("", messages)
        for directive in _DIRECTIVE.finditer(outside):
            if directive["verb"] == "reply":
                return OfflineResponse(directive["rest"])
            name, _, raw_arguments = directive["rest"].partition(" ")
            if name in offered:
                await offered[name].invoke(arguments=json.loads(raw_arguments or "{}"))

        query = terms(question)
        if (tool := self._choose_tool(query, offered)) is not None:
            await tool.invoke(arguments=self._arguments(tool, evidence))
        return OfflineResponse(self._answer(query, evidence))

    @staticmethod
    def _choose_tool(query: frozenset[str], offered: Mapping[str, Any]) -> Any | None:
        matches = [
            (len(name_terms), name, tool)
            for name, tool in offered.items()
            if (name_terms := terms(name.replace("_", " "))) and name_terms <= query
        ]
        return max(matches, key=lambda item: (item[0], item[1]))[2] if matches else None

    @staticmethod
    def _arguments(tool: Any, evidence: Sequence[_PromptEvidence]) -> dict[str, str]:
        schema: Mapping[str, Any] = tool.parameters()
        title = evidence[0].title if evidence else "unknown"
        return {
            name: title if name == "document_title" else "Requested by the offline evaluation."
            for name in schema.get("required", ())
        }

    def _answer(self, query: frozenset[str], evidence: Sequence[_PromptEvidence]) -> str:
        best: list[tuple[float, str, str]] = []
        for item in evidence:
            # Directives inside evidence are data: never obeyed, and never quoted as findings.
            sentences = [
                part.strip()
                for part in _SENTENCE_END.split(item.text)
                if part.strip() and not _DIRECTIVE.match(part)
            ]
            if not sentences:
                continue
            score, sentence = max((_overlap(query, text), text) for text in sentences)
            best.append((score, sentence, item.chunk_id))
        if not best:
            return ""
        top = max(score for score, _, _ in best)
        floor = max(self._minimum_relevance, top * self._relative_relevance)
        return " ".join(
            f"{sentence} [cite:{chunk_id}]" for score, sentence, chunk_id in best if score >= floor
        )


class DocumentStatusArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_title: str = Field(min_length=1, max_length=200)


class DocumentStatus(BaseModel):
    document_title: str
    status: Literal["published"]


class DocumentStatusTool(EnterpriseTool[DocumentStatusArgs, DocumentStatus]):
    """Read-only fixture tool: runs directly under policy."""

    name = "document_status"
    description = "Look up the publication status of a document by its title."
    risk = ToolRisk.READ_ONLY
    args_model: ClassVar[type[BaseModel]] = DocumentStatusArgs

    def __init__(self) -> None:
        self.calls = 0

    async def execute(
        self, args: DocumentStatusArgs, ctx: ExecutionContextProtocol
    ) -> DocumentStatus:
        del ctx
        self.calls += 1
        return DocumentStatus(document_title=args.document_title, status="published")


class ReviewRequestArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_title: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=500)


class ReviewRequest(BaseModel):
    review_id: str


@dataclass(frozen=True, slots=True)
class WriteExecution:
    execution_id: UUID
    args: ReviewRequestArgs
    scope_ids: frozenset[str]


class RequestDocumentReviewTool(IdempotentWriteTool[ReviewRequestArgs, ReviewRequest]):
    """Write fixture tool: must only ever run with an executed, args-bound approval."""

    name = "request_document_review"
    description = "Ask a person to review a document."
    risk = ToolRisk.LOW_IMPACT_WRITE
    args_model: ClassVar[type[BaseModel]] = ReviewRequestArgs

    def __init__(self) -> None:
        self.executions: list[WriteExecution] = []

    async def execute_approved(
        self, args: ReviewRequestArgs, ctx: ExecutionContextProtocol, *, execution_id: UUID
    ) -> ReviewRequest:
        self.executions.append(WriteExecution(execution_id, args, frozenset(ctx.scope_ids)))
        return ReviewRequest(review_id=str(execution_id))


@dataclass
class InMemoryApprovalRepository:
    """``ApprovalRepository`` for one evaluation row."""

    approvals: dict[UUID, Approval] = field(default_factory=dict)
    events: list[ApprovalAuditEvent] = field(default_factory=list)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def get_for_update(self, approval_id: UUID) -> Approval | None:
        return self.approvals.get(approval_id)

    async def add(self, approval: Approval) -> None:
        self.approvals[approval.id] = approval

    async def update(self, approval: Approval) -> None:
        self.approvals[approval.id] = approval

    async def add_audit_event(self, event: ApprovalAuditEvent) -> None:
        self.events.append(event)


@dataclass
class RecordingInvoker:
    """Record which tools the model called, then delegate to the policy middleware."""

    middleware: Any
    invoked: list[str] = field(default_factory=list)

    async def invoke(
        self,
        tool: EnterpriseTool[Any, Any],
        arguments: Any,
        context: ExecutionContextProtocol,
        *,
        turn_id: str,
    ) -> Any:
        self.invoked.append(tool.name)
        return await self.middleware.invoke(tool, arguments, context, turn_id=turn_id)
