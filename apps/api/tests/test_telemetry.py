import json
import logging
import sys
import warnings
from uuid import UUID, uuid4

import httpx
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from accelerator.api.composition import build_application
from accelerator.configuration.settings import Settings
from accelerator.telemetry.logging import JsonFormatter, configure_logging
from accelerator.telemetry.tracing import build_telemetry


def settings() -> Settings:
    return Settings(
        environment="test",
        entra_tenant_id=UUID("00000000-0000-0000-0000-000000000001"),
        entra_audience="api://test",
    )


async def test_exported_attributes_pass_through_security_core_redaction() -> None:
    exporter = InMemorySpanExporter()
    telemetry, shutdown = build_telemetry(settings(), exporter=exporter)
    with telemetry.request(uuid4()) as span:
        span.set_attribute("debug.note", "client_secret=SEEDED-SECRET-ATTRIBUTE")
        span.add_event("debug", {"token": "SEEDED-SECRET-EVENT"})
    telemetry.force_flush()

    exported = json.dumps(
        [
            [dict(s.attributes or {}), [dict(e.attributes or {}) for e in s.events]]
            for s in exporter.get_finished_spans()
        ]
    )
    await shutdown()

    assert "SEEDED-SECRET" not in exported
    assert "REDACTED" in exported


async def test_trace_and_response_share_the_client_supplied_correlation_id() -> None:
    exporter = InMemorySpanExporter()
    app = build_application(settings(), span_exporter=exporter)
    correlation_id = "6b2f8e5a-4c1d-4e9a-9f3b-1a2b3c4d5e6f"

    # Leaving the lifespan flushes and stops the span processor this app started.
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        response = await client.get("/healthz", headers={"X-Correlation-ID": correlation_id})

    assert response.headers.get_list("x-correlation-id") == [correlation_id]
    [request_span] = [s for s in exporter.get_finished_spans() if s.name == "http.request"]
    assert request_span.attributes is not None
    assert request_span.attributes["fde.correlation_id"] == correlation_id


def record(**extra: object) -> logging.LogRecord:
    entry = logging.LogRecord(
        "accelerator.test", logging.INFO, __file__, 1, "event_name", None, None
    )
    for key, value in extra.items():
        setattr(entry, key, value)
    return entry


def test_json_logs_keep_extra_fields_and_redact_secrets() -> None:
    line = JsonFormatter().format(
        record(correlation_id="c-1", reason="password=SEEDED-SECRET-LOG", attempt=2)
    )
    entry = json.loads(line)

    assert entry["event"] == "event_name"
    assert entry["level"] == "INFO"
    assert entry["correlation_id"] == "c-1"
    assert entry["attempt"] == 2
    assert "SEEDED-SECRET-LOG" not in line


def test_json_logs_never_include_exception_messages() -> None:
    try:
        raise ValueError("prompt text with api_key=SEEDED-SECRET-EXCEPTION")
    except ValueError:
        entry = logging.LogRecord(
            "accelerator.test", logging.ERROR, __file__, 1, "failed", None, sys.exc_info()
        )

    line = JsonFormatter().format(entry)

    assert json.loads(line)["exception_type"] == "ValueError"
    assert "SEEDED-SECRET-EXCEPTION" not in line
    assert "prompt text" not in line


def test_python_warnings_are_logged_as_structured_json(capsys: pytest.CaptureFixture[str]) -> None:
    """A dependency's import-time warning must not put an unstructured line on stderr."""
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    # Other tests (Alembic's fileConfig) disable existing loggers and may already have
    # captured warnings; start from a fresh process's state.
    warnings_logger = logging.getLogger("py.warnings")
    saved_disabled = warnings_logger.disabled
    warnings_logger.disabled = False
    logging.captureWarnings(False)
    try:
        configure_logging("INFO")
        with warnings.catch_warnings():
            warnings.simplefilter("always")
            warnings.warn("invalid escape sequence", SyntaxWarning, stacklevel=1)
        lines = [line for line in capsys.readouterr().err.splitlines() if line.strip()]
    finally:
        logging.captureWarnings(False)
        warnings_logger.disabled = saved_disabled
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)

    assert lines
    entries = [json.loads(line) for line in lines]
    assert any(
        entry["logger"] == "py.warnings" and "SyntaxWarning" in entry["event"] for entry in entries
    )
