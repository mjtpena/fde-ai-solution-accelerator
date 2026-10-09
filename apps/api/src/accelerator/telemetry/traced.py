"""Span-emitting wrappers for the grounded-answer workflow's ports.

They record only metadata (counts, decisions, model name, timings); queries,
evidence text, prompts and answers never become span attributes.
"""

from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel

from accelerator.agent_core.workflows.grounded_answer import (
    AnswerGenerator,
    CitationValidator,
    GeneratedAnswer,
    GroundedAnswerResult,
    Retriever,
    SufficiencyChecker,
    SufficiencyDecision,
)
from accelerator.api.chat import ChatTurnPort
from accelerator.observability_core import SpanAttributes, Telemetry
from accelerator.retrieval_core.models import Evidence, RetrievalRequest
from accelerator.security_core.content_safety import (
    ContentSafetyChecker,
    ContentSafetyPolicy,
    ContentSafetyUnavailableError,
    ScreenedDocument,
    ShieldResult,
    TextAnalysis,
)
from accelerator.security_core.data_boundaries.context import ExecutionContext
from accelerator.security_core.prompt_injection import injection_signals
from accelerator.security_core.tool_policy import ApprovalRequired


class TracedRetriever:
    def __init__(
        self,
        inner: Retriever[RetrievalRequest, ExecutionContext, Evidence],
        telemetry: Telemetry,
    ) -> None:
        self._inner = inner
        self._telemetry = telemetry

    async def retrieve(self, req: RetrievalRequest, ctx: ExecutionContext) -> list[Evidence]:
        fields = tuple(sorted(req.filters)) or None
        with self._telemetry.span(
            "retrieval.search", attributes=SpanAttributes(top_k=req.top_k, filter_fields=fields)
        ) as span:
            evidence: list[Evidence] = list(await self._inner.retrieve(req, ctx))
            span.set_attribute("fde.retrieval.result_count", len(evidence))
            # Retrieved text stays untrusted data regardless; this only counts
            # instruction-like documents so poisoning attempts are visible.
            span.set_attribute(
                "fde.retrieval.injection_signal_count",
                sum(1 for item in evidence if injection_signals(item.text)),
            )
            return evidence


class TracedSufficiencyChecker:
    def __init__(self, inner: SufficiencyChecker[Evidence], telemetry: Telemetry) -> None:
        self._inner = inner
        self._telemetry = telemetry

    async def evaluate(self, evidence: Sequence[Evidence]) -> SufficiencyDecision:
        with self._telemetry.span("retrieval.sufficiency") as span:
            decision = await self._inner.evaluate(evidence)
            span.set_attribute(
                "fde.retrieval.decision", "sufficient" if decision.sufficient else "insufficient"
            )
            span.set_attribute("fde.retrieval.result_count", len(decision.evidence_ids))
            return decision


class TracedAnswerGenerator:
    def __init__(
        self, inner: AnswerGenerator[Evidence], telemetry: Telemetry, *, model: str
    ) -> None:
        self._inner = inner
        self._telemetry = telemetry
        self._model = model

    async def generate(self, query: str, evidence: Sequence[Evidence]) -> GeneratedAnswer:
        with self._telemetry.span(
            "gen_ai.chat", attributes=SpanAttributes(model=self._model)
        ) as span:
            generated = await self._inner.generate(query, evidence)
            span.set_attribute("fde.citations.count", len(generated.citations))
            return generated


class TracedCitationValidator:
    def __init__(self, inner: CitationValidator, telemetry: Telemetry) -> None:
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


class TracedContentSafetyChecker:
    """``content_safety.*`` spans: decisions, flagged categories and chunk IDs, never text."""

    def __init__(
        self, inner: ContentSafetyChecker, telemetry: Telemetry, policy: ContentSafetyPolicy
    ) -> None:
        self._inner = inner
        self._telemetry = telemetry
        self._policy = policy

    async def shield_prompt(
        self,
        user_prompt: str,
        documents: Sequence[ScreenedDocument],
        *,
        deadline_utc: datetime | None = None,
    ) -> ShieldResult:
        with self._telemetry.span("content_safety.shield_prompt") as span:
            span.set_attribute("fde.content_safety.stage", "documents" if documents else "prompt")
            span.set_attribute("fde.content_safety.document_count", len(documents))
            try:
                result = await self._inner.shield_prompt(
                    user_prompt, documents, deadline_utc=deadline_utc
                )
            except ContentSafetyUnavailableError as error:
                span.set_attribute("fde.content_safety.decision", "unavailable")
                span.set_attribute("fde.content_safety.error_reason", error.reason)
                raise
            span.set_attribute(
                "fde.content_safety.decision",
                "attack" if result.user_prompt_attack or result.attacked_document_ids else "allow",
            )
            span.set_attribute("fde.content_safety.prompt_attack", result.user_prompt_attack)
            span.set_attribute(
                "fde.content_safety.dropped_chunk_ids", list(result.attacked_document_ids)
            )
            return result

    async def analyze_text(
        self, text: str, *, deadline_utc: datetime | None = None
    ) -> TextAnalysis:
        with self._telemetry.span("content_safety.analyze") as span:
            try:
                analysis = await self._inner.analyze_text(text, deadline_utc=deadline_utc)
                blocked = self._policy.blocked_categories(analysis)
            except ContentSafetyUnavailableError as error:
                span.set_attribute("fde.content_safety.decision", "unavailable")
                span.set_attribute("fde.content_safety.error_reason", error.reason)
                raise
            span.set_attribute("fde.content_safety.decision", "block" if blocked else "allow")
            span.set_attribute(
                "fde.content_safety.blocked_categories", [category.value for category in blocked]
            )
            for category, severity in analysis.severities.items():
                span.set_attribute(f"fde.content_safety.severity.{category.value}", severity)
            return analysis


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
                if result.abstention is not None:
                    span.set_attribute("fde.abstention.code", result.abstention.code)
                if result.screened_out_chunk_ids:
                    span.set_attribute(
                        "fde.content_safety.dropped_chunk_ids", list(result.screened_out_chunk_ids)
                    )
            return result
