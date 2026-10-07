from importlib import import_module
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from accelerator.security_core.redaction import redact_attributes, redact_sensitive_data

REDACTED = "[" + "REDACTED" + "]"


def test_redaction_hook_removes_sensitive_fixtures_from_exported_spans() -> None:
    try:
        telemetry = import_module("accelerator.observability_core").Telemetry
    except AttributeError:
        pytest.skip("The exported-span sanitizer hook requires issue #26's observability package")

    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.sdk.trace.sampling import ALWAYS_ON

    exporter = InMemorySpanExporter()
    adapter = MagicMock()
    adapter.create_exporter.return_value = exporter
    adapter.create_sampler.return_value = ALWAYS_ON
    runtime = telemetry.create("redaction-test", adapter, sanitize_attributes=redact_attributes)

    bearer_prefix = "Bear" + "er "
    prompt = (
        "Contact alice@example.com with api_key=api-key-fixture and "
        f"{bearer_prefix}bearer-token-fixture"
    )
    event_content = "owner=bob@example.org token=event-token-fixture"
    authorization_value = "Bear" + "er " + "header-fixture"

    with runtime.request(uuid4()) as span:
        span.set_attribute("test.prompt", prompt)
        span.set_attribute("test.header", f"Authorization: {authorization_value}")
        span.set_attribute("api_key", "attribute-key-fixture")
        span.add_event(
            "test.input",
            {
                "test.content": event_content,
                "http.request.header.authorization": f"{bearer_prefix}event-bearer-fixture",
            },
        )

    assert runtime.force_flush()
    exported_spans = exporter.get_finished_spans()
    assert len(exported_spans) == 1
    exported = exported_spans[0]
    assert exported.attributes is not None
    assert exported.events[0].attributes is not None
    exported_content = repr((exported.attributes, exported.events))

    for fixture in (
        "alice@example.com",
        "api-key-fixture",
        "bearer-token-fixture",
        "header-fixture",
        "attribute-key-fixture",
        "bob@example.org",
        "event-token-fixture",
        "event-bearer-fixture",
    ):
        assert fixture not in exported_content

    assert exported.attributes["test.prompt"] == redact_sensitive_data(prompt)
    assert exported.attributes["test.header"] == "Authorization: " + REDACTED
    assert exported.attributes["api_key"] == "[REDACTED]"
    assert exported.events[0].attributes["test.content"] == redact_sensitive_data(event_content)
    assert exported.events[0].attributes["http.request.header.authorization"] == REDACTED
    runtime.shutdown()
