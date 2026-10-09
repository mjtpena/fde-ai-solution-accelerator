from collections.abc import Callable, Mapping, Sequence

from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.util.types import AttributeValue

AttributeSanitizer = Callable[
    [Mapping[str, AttributeValue]], Mapping[str, AttributeValue]
]


class SanitizingSpanExporter(SpanExporter):
    """Sanitize copies at the final export boundary, including event attributes."""

    def __init__(self, exporter: SpanExporter, sanitize_attributes: AttributeSanitizer) -> None:
        self._exporter = exporter
        self._sanitize_attributes = sanitize_attributes

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        sanitized = [
            ReadableSpan(
                name=span.name,
                context=span.context,
                parent=span.parent,
                resource=span.resource,
                attributes=self._sanitize_attributes(span.attributes or {}),
                events=[
                    Event(
                        name=event.name,
                        attributes=self._sanitize_attributes(event.attributes or {}),
                        timestamp=event.timestamp,
                    )
                    for event in span.events
                ],
                links=span.links,
                kind=span.kind,
                status=span.status,
                start_time=span.start_time,
                end_time=span.end_time,
                instrumentation_scope=span.instrumentation_scope,
            )
            for span in spans
        ]
        return self._exporter.export(sanitized)

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._exporter.force_flush(timeout_millis)

    def shutdown(self) -> None:
        self._exporter.shutdown()
