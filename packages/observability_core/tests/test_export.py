from collections.abc import Mapping
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.util.types import AttributeValue

from accelerator.observability_core import SanitizingSpanExporter, Telemetry


def redact(attributes: Mapping[str, AttributeValue]) -> Mapping[str, AttributeValue]:
    return {
        key: value.replace("secret", "[REDACTED]") if isinstance(value, str) else value
        for key, value in attributes.items()
    }


def test_sanitizer_runs_on_exported_span_and_event_copies() -> None:
    source = InMemorySpanExporter()
    destination = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(source))
    with provider.get_tracer("test").start_as_current_span("test") as span:
        span.set_attribute("test.value", "secret")
        span.add_event("test.event", {"test.value": "secret", "test.count": 2})
    originals = source.get_finished_spans()
    wrapper = SanitizingSpanExporter(destination, redact)
    assert wrapper.export(originals) == SpanExportResult.SUCCESS
    exported = destination.get_finished_spans()[0]
    assert exported.attributes["test.value"] == "[REDACTED]"
    assert exported.events[0].attributes["test.value"] == "[REDACTED]"
    assert exported.events[0].attributes["test.count"] == 2
    assert originals[0].attributes["test.value"] == "secret"
    assert originals[0].events[0].attributes["test.value"] == "secret"
    assert exported.context == originals[0].context
    assert exported.start_time == originals[0].start_time
    assert exported.end_time == originals[0].end_time
    assert exported.events[0].timestamp == originals[0].events[0].timestamp
    provider.shutdown()


def test_runtime_wires_sanitizer_before_batch_export() -> None:
    exporter = InMemorySpanExporter()
    adapter = MagicMock()
    adapter.create_exporter.return_value = exporter
    adapter.create_sampler.return_value = ALWAYS_ON
    runtime = Telemetry.create("test", adapter, sanitize_attributes=redact)
    with runtime.request(uuid4()) as span:
        span.set_attribute("test.value", "secret")
        span.add_event("test.event", {"test.value": "secret"})
    assert runtime.force_flush()
    result = exporter.get_finished_spans()[0]
    assert result.attributes["test.value"] == "[REDACTED]"
    assert result.events[0].attributes["test.value"] == "[REDACTED]"
    runtime.shutdown()


def test_wrapper_delegates_lifecycle_and_export_failures() -> None:
    exporter = MagicMock()
    exporter.export.return_value = SpanExportResult.FAILURE
    exporter.force_flush.return_value = False
    wrapper = SanitizingSpanExporter(exporter, redact)
    assert wrapper.export([]) == SpanExportResult.FAILURE
    assert not wrapper.force_flush(42)
    exporter.force_flush.assert_called_once_with(42)
    wrapper.shutdown()
    exporter.shutdown.assert_called_once()


def test_sanitizer_failure_never_exports_unsanitized_data() -> None:
    exporter = MagicMock()
    source = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(source))
    with provider.get_tracer("test").start_as_current_span("test"):
        pass

    def fail(attributes: Mapping[str, AttributeValue]) -> Mapping[str, AttributeValue]:
        raise ValueError("sanitization failed")

    wrapper = SanitizingSpanExporter(exporter, fail)
    with pytest.raises(ValueError, match="sanitization failed"):
        wrapper.export(source.get_finished_spans())
    exporter.export.assert_not_called()
    provider.shutdown()
