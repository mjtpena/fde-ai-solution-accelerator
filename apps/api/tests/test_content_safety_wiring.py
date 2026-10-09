"""Content safety wiring in the API: composition, traced spans and refusal audit."""

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from azure.core.credentials import AccessToken
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import BaseModel, ValidationError

from accelerator.agent_core.workflows.grounded_answer import (
    REFUSAL_REASONS,
    Abstention,
    GroundedAnswerResult,
)
from accelerator.api.composition import build_application
from accelerator.api.refusal_audit import AuditedRefusalsChatTurn
from accelerator.application.audit import AuditRecorder
from accelerator.configuration.settings import Settings
from accelerator.domain.audit import AuditEvent, AuditPage, EventOutcome, EventType
from accelerator.infrastructure.content_safety import AzureContentSafetyChecker
from accelerator.infrastructure.grounded_answer import build_content_safety_checker
from accelerator.security_core.content_safety import (
    ContentSafetyPolicy,
    ContentSafetyUnavailableError,
    HarmCategory,
    ScreenedDocument,
    ShieldResult,
    TextAnalysis,
)
from accelerator.security_core.data_boundaries.context import ExecutionContext
from accelerator.security_core.tool_policy import ApprovalRequired
from accelerator.telemetry.traced import TracedChatTurn, TracedContentSafetyChecker
from accelerator.telemetry.tracing import build_telemetry

TENANT = "00000000-0000-0000-0000-000000000001"


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "development",
        "entra_tenant_id": TENANT,
        "entra_audience": "api://test",
        "foundry_project_endpoint": "https://foundry.example.test/api/projects/p",
        "foundry_model_deployment": "chat-model",
        "foundry_embedding_deployment": "embedding-model",
        "search_endpoint": "https://search.example.test",
        "search_index_name": "chunks",
        "search_vector_dimensions": 3,
    }
    values.update(overrides)
    return Settings.model_validate(values)


class FakeCredential:
    async def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        return AccessToken("entra-token", 4_102_444_800)

    async def close(self) -> None:
        return None


def context() -> ExecutionContext:
    return ExecutionContext(
        correlation_id="00000000-0000-0000-0000-00000000c5a1",
        user_id="user-1",
        roles=frozenset({"Reader"}),
        scope_ids=frozenset({"scope-a"}),
        deadline_utc=datetime.now(UTC) + timedelta(minutes=1),
    )


def test_configured_endpoint_wires_the_adapter_and_closes_it_with_the_app() -> None:
    app = build_application(
        settings(content_safety_endpoint="https://safety.example.test"),
        credential=FakeCredential(),  # type: ignore[arg-type]
    )

    workflow = app.state.chat_turn._workflow._inner._inner
    assert isinstance(workflow._content_safety, TracedContentSafetyChecker)
    assert isinstance(workflow._content_safety._inner, AzureContentSafetyChecker)
    # Telemetry, Search, embeddings, Foundry chat and the Content Safety client.
    assert len(app.state.shutdown_callbacks) == 5


def test_development_without_an_endpoint_runs_unscreened(caplog: pytest.LogCaptureFixture) -> None:
    shutdown: list[Any] = []

    checker = build_content_safety_checker(settings(), FakeCredential(), shutdown)  # type: ignore[arg-type]

    assert checker is None and shutdown == []
    assert "content_safety_unscreened" in caplog.text


def test_composition_refuses_to_run_production_unscreened() -> None:
    production = settings(content_safety_endpoint="https://safety.example.test")
    # Bypass validation to prove the composition root is a second line of defence.
    unscreened = production.model_copy(
        update={"environment": "production", "content_safety_enabled": False}
    )

    with pytest.raises(ValueError, match="Production requires Azure AI Content Safety"):
        build_content_safety_checker(unscreened, FakeCredential(), [])  # type: ignore[arg-type]


class StubChecker:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    async def shield_prompt(
        self,
        user_prompt: str,
        documents: Sequence[ScreenedDocument],
        *,
        deadline_utc: datetime | None = None,
    ) -> ShieldResult:
        if self.fail:
            raise ContentSafetyUnavailableError("timeout")
        return ShieldResult(
            user_prompt_attack=False,
            attacked_document_ids=tuple(d.document_id for d in documents if "x" in d.text),
        )

    async def analyze_text(
        self, text: str, *, deadline_utc: datetime | None = None
    ) -> TextAnalysis:
        if self.fail:
            raise ContentSafetyUnavailableError("http_503")
        return TextAnalysis(dict.fromkeys(HarmCategory, 0) | {HarmCategory.VIOLENCE: 6})


async def test_traced_checker_records_decisions_ids_and_severities_but_no_text() -> None:
    exporter = InMemorySpanExporter()
    telemetry, shutdown = build_telemetry(settings(), exporter=exporter)
    traced = TracedContentSafetyChecker(StubChecker(), telemetry, ContentSafetyPolicy())

    await traced.shield_prompt("SECRET-PROMPT", [])
    await traced.shield_prompt(
        "SECRET-PROMPT",
        [ScreenedDocument("chunk-1", "fine"), ScreenedDocument("chunk-2", "SECRET-x")],
    )
    await traced.analyze_text("SECRET-ANSWER")
    telemetry.force_flush()
    spans = exporter.get_finished_spans()
    await shutdown()

    prompt, documents, analyze = spans
    assert prompt.name == documents.name == "content_safety.shield_prompt"
    assert prompt.attributes is not None and documents.attributes is not None
    assert prompt.attributes["fde.content_safety.stage"] == "prompt"
    assert prompt.attributes["fde.content_safety.decision"] == "allow"
    assert documents.attributes["fde.content_safety.stage"] == "documents"
    assert documents.attributes["fde.content_safety.decision"] == "attack"
    assert documents.attributes["fde.content_safety.document_count"] == 2
    assert documents.attributes["fde.content_safety.dropped_chunk_ids"] == ("chunk-2",)
    assert "fde.duration_ms" in documents.attributes
    assert analyze.name == "content_safety.analyze"
    assert analyze.attributes is not None
    assert analyze.attributes["fde.content_safety.decision"] == "block"
    assert analyze.attributes["fde.content_safety.blocked_categories"] == ("Violence",)
    assert analyze.attributes["fde.content_safety.severity.Violence"] == 6
    exported = json.dumps([dict(span.attributes or {}) for span in spans], default=str)
    assert "SECRET" not in exported


async def test_traced_checker_marks_unavailable_and_reraises() -> None:
    exporter = InMemorySpanExporter()
    telemetry, shutdown = build_telemetry(settings(), exporter=exporter)
    traced = TracedContentSafetyChecker(StubChecker(fail=True), telemetry, ContentSafetyPolicy())

    with pytest.raises(ContentSafetyUnavailableError):
        await traced.analyze_text("a")
    telemetry.force_flush()
    [span] = exporter.get_finished_spans()
    await shutdown()

    assert span.attributes is not None
    assert span.attributes["fde.content_safety.decision"] == "unavailable"
    assert span.attributes["fde.content_safety.error_reason"] == "http_503"
    assert span.attributes["error.type"] == "ContentSafetyUnavailableError"


class MemoryRepository:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def append(self, event: AuditEvent) -> None:
        self.events.append(event)

    async def query(
        self, *, limit: int, offset: int, event_type: EventType | None = None
    ) -> AuditPage:
        return AuditPage(items=tuple(self.events))


class FixedTurn:
    def __init__(self, result: GroundedAnswerResult) -> None:
        self.result = result

    async def run(
        self, query: str, ctx: ExecutionContext
    ) -> GroundedAnswerResult | ApprovalRequired[BaseModel]:
        return self.result


def refusal(code: str) -> GroundedAnswerResult:
    return GroundedAnswerResult(
        status="abstained",
        answer=None,
        citations=(),
        citation_sources=(),
        abstention=Abstention(reason=REFUSAL_REASONS[code], evidence_ids=(), code=code),  # type: ignore[arg-type]
        screened_out_chunk_ids=("chunk-9",),
    )


@pytest.mark.parametrize(
    ("code", "outcome"),
    [
        ("content_safety_prompt_attack", EventOutcome.DENIED),
        ("content_safety_output_blocked", EventOutcome.DENIED),
        ("content_safety_unavailable", EventOutcome.FAILED),
    ],
)
async def test_every_content_safety_refusal_is_audited(code: str, outcome: EventOutcome) -> None:
    repository = MemoryRepository()
    turn = AuditedRefusalsChatTurn(FixedTurn(refusal(code)), AuditRecorder(repository))

    result = await turn.run("SECRET-PROMPT", context())

    assert result == refusal(code)
    [event] = repository.events
    assert event.event_type == EventType.CONTENT_SAFETY
    assert event.outcome == outcome
    assert event.reason_code == code
    assert event.actor_id == "user-1"
    assert event.correlation_id == context().correlation_id
    assert "SECRET" not in event.model_dump_json()


async def test_answers_and_evidence_abstentions_are_not_audited() -> None:
    repository = MemoryRepository()
    abstained = GroundedAnswerResult(
        status="abstained",
        answer=None,
        citations=(),
        citation_sources=(),
        abstention=Abstention(reason="Not enough evidence.", evidence_ids=()),
    )

    await AuditedRefusalsChatTurn(FixedTurn(abstained), AuditRecorder(repository)).run(
        "q", context()
    )

    assert repository.events == []


async def test_a_failed_audit_write_fails_the_turn() -> None:
    class BrokenRepository(MemoryRepository):
        async def append(self, event: AuditEvent) -> None:
            raise RuntimeError("database down")

    turn = AuditedRefusalsChatTurn(
        FixedTurn(refusal("content_safety_prompt_attack")), AuditRecorder(BrokenRepository())
    )

    with pytest.raises(RuntimeError, match="database down"):
        await turn.run("q", context())


async def test_workflow_span_records_the_abstention_code_and_dropped_chunks() -> None:
    exporter = InMemorySpanExporter()
    telemetry, shutdown = build_telemetry(settings(), exporter=exporter)

    await TracedChatTurn(
        FixedTurn(refusal("content_safety_output_blocked")), telemetry, name="grounded_answer"
    ).run("q", context())
    telemetry.force_flush()
    [span] = exporter.get_finished_spans()
    await shutdown()

    assert span.attributes is not None
    assert span.attributes["fde.abstention.code"] == "content_safety_output_blocked"
    assert span.attributes["fde.content_safety.dropped_chunk_ids"] == ("chunk-9",)


@pytest.mark.parametrize(
    "fields",
    [
        {"reason_code": None},
        {"outcome": EventOutcome.FAILED},
        {"actor_id": None},
        {"tool_name": "document_status"},
        {"approval_id": UUID("6f1c2b8e-3f3a-4d5e-8a9b-0c1d2e3f4a5b")},
    ],
)
def test_content_safety_audit_events_have_a_strict_shape(fields: dict[str, Any]) -> None:
    values: dict[str, Any] = {
        "event_type": EventType.CONTENT_SAFETY,
        "outcome": EventOutcome.DENIED,
        "correlation_id": "c",
        "actor_id": "user-1",
        "reason_code": "content_safety_prompt_attack",
    }
    AuditEvent.model_validate(values)

    with pytest.raises(ValidationError):
        AuditEvent.model_validate(values | fields)


def test_other_event_types_cannot_carry_a_reason_code() -> None:
    with pytest.raises(ValidationError):
        AuditEvent(
            event_type=EventType.TOOL_EXECUTION,
            outcome=EventOutcome.SUCCEEDED,
            correlation_id="c",
            actor_id="user-1",
            tool_name="document_status",
            reason_code="content_safety_prompt_attack",
        )
    with pytest.raises(ValidationError):
        AuditEvent(
            event_type=EventType.CONTENT_SAFETY,
            outcome=EventOutcome.DENIED,
            correlation_id="c",
            actor_id="user-1",
            reason_code="free text",  # type: ignore[arg-type]
        )
