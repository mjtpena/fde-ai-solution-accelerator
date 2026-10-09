from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import ValidationError

from accelerator.observability_core import Telemetry
from accelerator.observability_core.infrastructure import application_insights as insights
from accelerator.observability_core.infrastructure.application_insights import (
    ApplicationInsightsAdapter,
    ApplicationInsightsSettings,
    start_application_insights,
    stop_application_insights,
)


def test_missing_service_name_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FDE_TELEMETRY_SERVICE_NAME", raising=False)
    with pytest.raises(ValidationError):
        ApplicationInsightsSettings()


def test_managed_identity_export_and_batch_flush(monkeypatch: pytest.MonkeyPatch) -> None:
    credential = MagicMock()
    credential_factory = MagicMock(return_value=credential)
    exporter = InMemorySpanExporter()
    exporter_factory = MagicMock(return_value=exporter)
    monkeypatch.setattr(insights, "ManagedIdentityCredential", credential_factory)
    monkeypatch.setattr(insights, "AzureMonitorTraceExporter", exporter_factory)
    adapter = ApplicationInsightsAdapter(
        ApplicationInsightsSettings(service_name="api", managed_identity_client_id="identity-id")
    )
    telemetry = Telemetry.create("api", adapter)
    with telemetry.request(uuid4()):
        with telemetry.span("workflow", name="answer"):
            pass
    assert telemetry.force_flush()
    spans = exporter.get_finished_spans()
    assert {span.name for span in spans} == {"http.request", "workflow.answer"}
    assert all(span.resource.attributes["service.name"] == "api" for span in spans)
    assert all(
        span.instrumentation_scope.name == "fde.observability_core" for span in spans
    )
    credential_factory.assert_called_once_with(client_id="identity-id")
    exporter_factory.assert_called_once_with(credential=credential, disable_offline_storage=True)
    with pytest.raises(RuntimeError, match="only create one exporter"):
        adapter.create_exporter()
    telemetry.shutdown()
    adapter.close()
    adapter.close()
    credential.close.assert_called_once()


def test_initialization_errors_are_not_swallowed_and_credentials_are_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = MagicMock()
    monkeypatch.setattr(insights, "ManagedIdentityCredential", MagicMock(return_value=credential))
    monkeypatch.setattr(
        insights,
        "AzureMonitorTraceExporter",
        MagicMock(side_effect=ValueError("missing destination")),
    )
    adapter = ApplicationInsightsAdapter(ApplicationInsightsSettings(service_name="api"))
    with pytest.raises(ValueError, match="missing destination"):
        Telemetry.create("api", adapter)
    credential.close.assert_called_once()


def test_real_exporter_rejects_missing_destination(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    credential = MagicMock()
    monkeypatch.setattr(insights, "ManagedIdentityCredential", MagicMock(return_value=credential))
    adapter = ApplicationInsightsAdapter(ApplicationInsightsSettings(service_name="api"))
    with pytest.raises(ValueError):
        adapter.create_exporter()
    credential.close.assert_called_once()


async def test_async_lifecycle_closes_exporter_and_managed_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = MagicMock()
    exporter = MagicMock(wraps=InMemorySpanExporter())
    monkeypatch.setattr(insights, "ManagedIdentityCredential", MagicMock(return_value=credential))
    monkeypatch.setattr(insights, "AzureMonitorTraceExporter", MagicMock(return_value=exporter))
    telemetry, adapter = await start_application_insights(
        ApplicationInsightsSettings(service_name="api")
    )
    await stop_application_insights(telemetry, adapter)
    exporter.shutdown.assert_called_once()
    credential.close.assert_called_once()
