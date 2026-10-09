import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter
from typing import Literal, Protocol, get_args
from uuid import UUID

from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import Sampler
from opentelemetry.trace import Span, SpanKind, StatusCode, set_span_in_context
from opentelemetry.util.types import AttributeValue

from .attributes import SpanAttributes
from .export import AttributeSanitizer, SanitizingSpanExporter

Operation = Literal[
    "authz.resolve_scope",
    "workflow",
    "retrieval.search",
    "retrieval.sufficiency",
    "gen_ai.chat",
    "tool",
    "approval",
    "citations.validate",
    "response",
]
_OPERATIONS = frozenset(get_args(Operation))
_CORRELATION_ID: ContextVar[str | None] = ContextVar("fde_correlation_id", default=None)
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", re.ASCII)


class TraceExportAdapter(Protocol):
    def create_exporter(self) -> SpanExporter: ...

    def create_sampler(self) -> Sampler: ...


class Telemetry:
    """An injected runtime, not a process-global provider or import-time client."""

    def __init__(self, provider: TracerProvider) -> None:
        self.provider = provider
        self._tracer = provider.get_tracer("fde.observability_core", "0.1.0")

    @classmethod
    def create(
        cls,
        service_name: str,
        adapter: TraceExportAdapter,
        *,
        sanitize_attributes: AttributeSanitizer | None = None,
    ) -> "Telemetry":
        if not _IDENTIFIER.fullmatch(service_name):
            raise ValueError("service_name must be a bounded metadata identifier")
        provider = TracerProvider(
            resource=Resource({"service.name": service_name}),
            sampler=adapter.create_sampler(),
            shutdown_on_exit=False,
        )
        exporter = adapter.create_exporter()
        if sanitize_attributes is not None:
            exporter = SanitizingSpanExporter(exporter, sanitize_attributes)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        return cls(provider)

    @contextmanager
    def request(
        self,
        correlation_id: UUID,
        *,
        parent: Context | None = None,
        method: str | None = None,
    ) -> Iterator[Span]:
        token = _CORRELATION_ID.set(str(correlation_id))
        attributes: dict[str, AttributeValue] = {}
        if method is not None:
            attributes["http.request.method"] = (
                method if method in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
                else "_OTHER"
            )
        try:
            with self._span("http.request", attributes, SpanKind.SERVER, parent) as span:
                yield span
        finally:
            _CORRELATION_ID.reset(token)

    @contextmanager
    def span(
        self,
        operation: Operation,
        *,
        name: str | None = None,
        attributes: SpanAttributes | None = None,
    ) -> Iterator[Span]:
        if operation not in _OPERATIONS:
            raise ValueError("Unsupported trace operation")
        if operation in {"workflow", "tool", "approval"}:
            if name is None or not _IDENTIFIER.fullmatch(name):
                raise ValueError("Named spans require a bounded metadata identifier")
            span_name = f"{operation}.{name}"
        else:
            if name is not None:
                raise ValueError("This operation does not accept a name")
            span_name = operation
        values = attributes.to_otel() if attributes is not None else {}
        if operation == "gen_ai.chat":
            values["gen_ai.operation.name"] = "chat"
        kind = (
            SpanKind.CLIENT
            if operation in {"retrieval.search", "gen_ai.chat"}
            else SpanKind.INTERNAL
        )
        with self._span(span_name, values, kind) as span:
            yield span

    @contextmanager
    def response(self, request: Span) -> Iterator[Span]:
        with self._span(
            "response", {}, SpanKind.INTERNAL, set_span_in_context(request), current=False
        ) as span:
            yield span

    @contextmanager
    def _span(
        self,
        name: str,
        attributes: dict[str, AttributeValue],
        kind: SpanKind,
        parent: Context | None = None,
        *,
        current: bool = True,
    ) -> Iterator[Span]:
        correlation_id = _CORRELATION_ID.get()
        if correlation_id is not None:
            attributes["fde.correlation_id"] = correlation_id
        started = perf_counter()
        manager = (
            self._tracer.start_as_current_span(
                name,
                context=parent,
                kind=kind,
                attributes=attributes,
                record_exception=False,
                set_status_on_exception=False,
            )
            if current
            else self._tracer.start_span(
                name,
                context=parent,
                kind=kind,
                attributes=attributes,
                record_exception=False,
                set_status_on_exception=False,
            )
        )
        with manager as span:
            try:
                yield span
            except BaseException as error:
                # Exception messages and stack traces can contain prompts or retrieved text.
                span.set_attribute("error.type", type(error).__name__)
                span.set_status(StatusCode.ERROR)
                raise
            finally:
                span.set_attribute("fde.duration_ms", (perf_counter() - started) * 1000)

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self.provider.force_flush(timeout_millis)

    def shutdown(self) -> None:
        self.provider.shutdown()
