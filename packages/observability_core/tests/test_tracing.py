import asyncio
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from asgiref.testing import ApplicationCommunicator
from asgiref.typing import ASGIReceiveCallable, ASGISendCallable, Scope
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode
from pydantic import ValidationError

from accelerator.observability_core import SpanAttributes, Telemetry, TracingMiddleware


@pytest.fixture
def runtime() -> Iterator[tuple[Telemetry, InMemorySpanExporter]]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry = Telemetry(provider)
    yield telemetry, exporter
    telemetry.shutdown()


def test_spec_trace_tree_and_metadata(runtime: tuple[Telemetry, InMemorySpanExporter]) -> None:
    telemetry, exporter = runtime
    correlation_id = uuid4()
    with telemetry.request(correlation_id, method="POST"):
        with telemetry.span("authz.resolve_scope"):
            pass
        with telemetry.span("workflow", name="answer"):
            with telemetry.span(
                "retrieval.search",
                attributes=SpanAttributes(
                    filter_fields=("scope_id",), top_k=5, result_count=2
                ),
            ):
                pass
            with telemetry.span(
                "retrieval.sufficiency",
                attributes=SpanAttributes(decision="sufficient", reason_code="enough_evidence"),
            ):
                pass
            with telemetry.span(
                "gen_ai.chat",
                attributes=SpanAttributes(model="gpt-4.1", input_tokens=20, output_tokens=10),
            ):
                pass
            with telemetry.span(
                "tool", name="lookup", attributes=SpanAttributes(risk="read", outcome="success")
            ):
                pass
            with telemetry.span("approval", name="approved"):
                pass
            with telemetry.span(
                "citations.validate", attributes=SpanAttributes(citation_count=2, valid=True)
            ):
                pass
        with telemetry.span("response"):
            pass
    spans = {span.name: span for span in exporter.get_finished_spans()}
    expected_parents = {
        "authz.resolve_scope": "http.request",
        "workflow.answer": "http.request",
        "retrieval.search": "workflow.answer",
        "retrieval.sufficiency": "workflow.answer",
        "gen_ai.chat": "workflow.answer",
        "tool.lookup": "workflow.answer",
        "approval.approved": "workflow.answer",
        "citations.validate": "workflow.answer",
        "response": "http.request",
    }
    assert set(spans) == {"http.request", *expected_parents}
    root = spans["http.request"]
    assert root.kind == SpanKind.SERVER
    assert root.parent is None
    for name, parent_name in expected_parents.items():
        child, parent = spans[name], spans[parent_name]
        assert child.parent is not None
        assert child.parent.span_id == parent.context.span_id
        assert child.context.trace_id == root.context.trace_id
    for span in spans.values():
        assert span.attributes["fde.correlation_id"] == str(correlation_id)
        assert span.attributes["fde.duration_ms"] >= 0
    assert spans["retrieval.search"].attributes["fde.retrieval.filter_fields"] == ("scope_id",)
    assert spans["gen_ai.chat"].attributes["gen_ai.usage.input_tokens"] == 20
    assert spans["gen_ai.chat"].attributes["gen_ai.usage.output_tokens"] == 10
    assert spans["gen_ai.chat"].attributes["gen_ai.operation.name"] == "chat"


def test_errors_propagate_without_exporting_content(
    runtime: tuple[Telemetry, InMemorySpanExporter],
) -> None:
    telemetry, exporter = runtime
    secret = "private prompt and retrieved document"
    with pytest.raises(ValueError, match=secret):
        with telemetry.request(uuid4()):
            with telemetry.span("gen_ai.chat"):
                raise ValueError(secret)
    for span in exporter.get_finished_spans():
        assert span.status.status_code == StatusCode.ERROR
        assert span.status.description is None
        assert span.attributes["error.type"] == "ValueError"
        assert not span.events
        assert secret not in str(span.attributes)
    assert not trace.get_current_span().is_recording()


@pytest.mark.parametrize(
    "attributes",
    [
        {"prompt": "secret"},
        {"response": "secret"},
        {"filters": {"scope_id": "secret"}},
        {"reason_code": "free form document text"},
        {"top_k": 0},
        {"input_tokens": -1},
        {"output_tokens": "10"},
        {"risk": "unrestricted"},
    ],
)
def test_unsafe_or_invalid_attributes_are_rejected(attributes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        SpanAttributes.model_validate(attributes)


def test_dynamic_names_are_validated(runtime: tuple[Telemetry, InMemorySpanExporter]) -> None:
    telemetry, _ = runtime
    with pytest.raises(ValueError):
        with telemetry.span("workflow", name="private user request"):
            pass
    with pytest.raises(ValueError):
        with telemetry.span("tool"):
            pass
    with pytest.raises(ValueError):
        with telemetry.span("response", name="anything"):
            pass


async def test_concurrent_requests_do_not_share_trace_or_correlation_context(
    runtime: tuple[Telemetry, InMemorySpanExporter],
) -> None:
    telemetry, exporter = runtime
    ids = [uuid4(), uuid4()]

    async def request(correlation_id: UUID) -> None:
        with telemetry.request(correlation_id):
            await asyncio.sleep(0)
            with telemetry.span("workflow", name="answer"):
                await asyncio.sleep(0)

    await asyncio.gather(*(request(value) for value in ids))
    spans = exporter.get_finished_spans()
    for value in ids:
        group = [span for span in spans if span.attributes["fde.correlation_id"] == str(value)]
        assert len(group) == 2
        assert len({span.context.trace_id for span in group}) == 1
    assert len({span.context.trace_id for span in spans}) == 2
    with telemetry.span("response"):
        pass
    assert "fde.correlation_id" not in exporter.get_finished_spans()[-1].attributes


@pytest.mark.parametrize("traceparent", [
    b"00-12345678901234567890123456789012-1234567890123456-01",
    b"not-a-traceparent",
])
async def test_http_propagation_streaming_and_privacy(
    runtime: tuple[Telemetry, InMemorySpanExporter], traceparent: bytes,
) -> None:
    telemetry, exporter = runtime

    async def app(scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable) -> None:
        assert scope["type"] == "http"
        UUID(scope["state"]["correlation_id"])
        with telemetry.span("authz.resolve_scope"):
            pass
        with telemetry.span("workflow", name="answer"):
            with telemetry.span("gen_ai.chat"):
                pass
            # Even a response started inside a workflow remains a child of http.request.
            await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"private answer", "more_body": True})
        await send({"type": "http.response.body", "body": b"private continuation"})

    communicator = ApplicationCommunicator(
        TracingMiddleware(app, telemetry),
        {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"traceparent", traceparent),
                (b"authorization", b"secret"),
                (b"baggage", b"prompt=private"),
                (b"x-correlation-id", b"private"),
            ],
            "path": "/private-document",
            "query_string": b"prompt=private",
        },
    )
    start = await communicator.receive_output()
    UUID(dict(start["headers"])[b"x-correlation-id"].decode("ascii"))
    await communicator.receive_output()
    await communicator.receive_output()
    await communicator.wait()
    spans = exporter.get_finished_spans()
    root = next(span for span in spans if span.name == "http.request")
    responses = [span for span in spans if span.name == "response"]
    assert len(responses) == 1
    assert responses[0].parent.span_id == root.context.span_id
    if traceparent.startswith(b"00-"):
        assert root.context.trace_id == int("12345678901234567890123456789012", 16)
        assert root.parent.span_id == int("1234567890123456", 16)
    else:
        assert root.parent is None
    for span in spans:
        assert "private" not in str(span.attributes)
        assert "secret" not in str(span.attributes)
        assert span.context.trace_id == root.context.trace_id


async def test_http_server_error_and_passthrough(
    runtime: tuple[Telemetry, InMemorySpanExporter],
) -> None:
    telemetry, exporter = runtime

    async def app(scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable) -> None:
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 503, "headers": []})
            await send({"type": "http.response.body", "body": b""})
        else:
            await send({"type": "lifespan.startup.complete"})

    http = ApplicationCommunicator(
        TracingMiddleware(app, telemetry), {"type": "http", "method": "GET", "headers": []}
    )
    await http.receive_output()
    await http.receive_output()
    await http.wait()
    root = next(span for span in exporter.get_finished_spans() if span.name == "http.request")
    assert root.status.status_code == StatusCode.ERROR
    assert root.attributes["http.response.status_code"] == 503
    before = len(exporter.get_finished_spans())
    lifespan = ApplicationCommunicator(TracingMiddleware(app, telemetry), {"type": "lifespan"})
    assert (await lifespan.receive_output())["type"] == "lifespan.startup.complete"
    await lifespan.wait()
    assert len(exporter.get_finished_spans()) == before


async def test_streaming_failure_propagates_without_recording_exception_content(
    runtime: tuple[Telemetry, InMemorySpanExporter],
) -> None:
    telemetry, exporter = runtime

    async def app(scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise ValueError("private model output")

    communicator = ApplicationCommunicator(
        TracingMiddleware(app, telemetry), {"type": "http", "method": "POST", "headers": []}
    )
    await communicator.receive_output()
    with pytest.raises(ValueError, match="private model output"):
        await communicator.wait()
    spans = exporter.get_finished_spans()
    assert {span.name for span in spans} == {"http.request", "response"}
    for span in spans:
        assert not span.events
        assert span.status.status_code == StatusCode.ERROR
        assert span.status.description is None
        assert "private" not in str(span.attributes)


async def test_pre_send_failure_still_exports_response_child(
    runtime: tuple[Telemetry, InMemorySpanExporter],
) -> None:
    telemetry, exporter = runtime

    async def app(scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable) -> None:
        raise ValueError("private model output before response headers")

    communicator = ApplicationCommunicator(
        TracingMiddleware(app, telemetry), {"type": "http", "method": "POST", "headers": []}
    )
    with pytest.raises(ValueError, match="private model output before response headers"):
        await communicator.wait()

    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert set(spans) == {"http.request", "response"}
    request, response = spans["http.request"], spans["response"]
    assert response.parent.span_id == request.context.span_id
    assert response.context.trace_id == request.context.trace_id
    for span in spans.values():
        assert span.status.status_code == StatusCode.ERROR
        assert span.status.description is None
        assert span.attributes["error.type"] == "ValueError"
        assert not span.events
        assert "private" not in str(span.attributes)
