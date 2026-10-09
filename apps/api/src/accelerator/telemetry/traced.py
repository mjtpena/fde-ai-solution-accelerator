"""Span-emitting wrappers for the grounded-answer workflow's ports.

They record only metadata (counts, decisions, model name, timings); queries,
evidence text, prompts and answers never become span attributes.
"""

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

from accelerator.agent_core.workflows.grounded_answer import GroundedAnswerResult
from accelerator.api.chat import ChatTurnPort
from accelerator.observability_core import SpanAttributes, Telemetry
from accelerator.retrieval_core.models import Evidence, RetrievalRequest
from accelerator.security_core.data_boundaries.context import ExecutionContext
from accelerator.security_core.tool_policy import ApprovalRequired


class TracedRetriever:
    def __init__(self, inner: Any, telemetry: Telemetry) -> None:
        self._inner = inner
        self._telemetry = telemetry

    async def retrieve(self, req: RetrievalRequest, ctx: ExecutionContext) -> list[Evidence]:
        fields = tuple(sorted(req.filters)) or None
        with self._telemetry.span(
            "retrieval.search", attributes=SpanAttributes(top_k=req.top_k, filter_fields=fields)
        ) as span:
            evidence: list[Evidence] = list(await self._inner.retrieve(req, ctx))
            span.set_attribute("fde.retrieval.result_count", len(evidence))
            return evidence


class TracedSufficiencyChecker:
    def __init__(self, inner: Any, telemetry: Telemetry) -> None:
        self._inner = inner
        self._telemetry = telemetry

    async def evaluate(self, evidence: Sequence[Any]) -> Any:
        with self._telemetry.span("retrieval.sufficiency") as span:
            decision = await self._inner.evaluate(evidence)
            span.set_attribute(
                "fde.retrieval.decision", "sufficient" if decision.sufficient else "insufficient"
            )
            span.set_attribute("fde.retrieval.result_count", len(decision.evidence_ids))
            return decision


class TracedAnswerGenerator:
    def __init__(self, inner: Any, telemetry: Telemetry, *, model: str) -> None:
        self._inner = inner
        self._telemetry = telemetry
        self._model = model

    async def generate(self, query: str, evidence: Sequence[Any]) -> Any:
        with self._telemetry.span(
            "gen_ai.chat", attributes=SpanAttributes(model=self._model)
        ) as span:
            generated = await self._inner.generate(query, evidence)
            span.set_attribute("fde.citations.count", len(generated.citations))
            return generated


class TracedCitationValidator:
    def __init__(self, inner: Any, telemetry: Telemetry) -> None:
        self._inner = inner
        self._telemetry = telemetry

    def validate(self, citations: Sequence[str], retrieved_chunk_ids: frozenset[str]) -> None:
        with self._telemetry.span(
            "citations.validate", attributes=SpanAttributes(citation_count=len(citations))
        ) as span:
            try:
                self._inner.validate(citations, retrieved_chunk_ids)
            except ValueError:
                span.set_attribute("fde.citations.valid", False)
                raise
            span.set_attribute("fde.citations.valid", True)


class TracedChatTurn:
    def __init__(self, inner: ChatTurnPort, telemetry: Telemetry, *, name: str) -> None:
        self._inner = inner
        self._telemetry = telemetry
        self._name = name

    async def run(
        self, query: str, ctx: ExecutionContext
    ) -> GroundedAnswerResult | ApprovalRequired[BaseModel]:
        with self._telemetry.span("workflow", name=self._name) as span:
            result = await self._inner.run(query, ctx)
            if isinstance(result, ApprovalRequired):
                span.set_attribute("fde.outcome", "denied")
            else:
                span.set_attribute(
                    "fde.outcome", "success" if result.status == "answered" else "abstained"
                )
            return result
