"""Build the API's tracer runtime from settings.

With an Application Insights connection string, spans export through
``SanitizingSpanExporter`` with ``security_core`` redaction composed in, so every
attribute and event is scrubbed at the final export boundary. Without one
(development and test), spans are created but not exported.
"""

import asyncio
from collections.abc import Awaitable, Callable

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_ON, Sampler

from accelerator.configuration.settings import Settings
from accelerator.observability_core import Telemetry
from accelerator.observability_core.infrastructure.application_insights import (
    ApplicationInsightsAdapter,
    ApplicationInsightsSettings,
)
from accelerator.security_core.redaction import redact_attributes

SERVICE_NAME = "fde-accelerator-api"


class ExporterAdapter:
    """Wrap an existing exporter (tests, alternative backends) as a trace adapter."""

    def __init__(self, exporter: SpanExporter, sampler: Sampler = ALWAYS_ON) -> None:
        self._exporter = exporter
        self._sampler = sampler

    def create_exporter(self) -> SpanExporter:
        return self._exporter

    def create_sampler(self) -> Sampler:
        return self._sampler


def build_telemetry(
    settings: Settings, *, exporter: SpanExporter | None = None
) -> tuple[Telemetry, Callable[[], Awaitable[None]]]:
    """Return the runtime and an async shutdown that flushes and closes it."""
    if exporter is not None:
        telemetry = Telemetry.create(
            SERVICE_NAME, ExporterAdapter(exporter), sanitize_attributes=redact_attributes
        )
        close: Callable[[], None] = lambda: None  # noqa: E731
    elif settings.applicationinsights_connection_string is not None:
        adapter = ApplicationInsightsAdapter(
            ApplicationInsightsSettings(
                service_name=SERVICE_NAME,
                managed_identity_client_id=settings.managed_identity_client_id,
            ),
            connection_string=settings.applicationinsights_connection_string.get_secret_value(),
        )
        telemetry = Telemetry.create(
            SERVICE_NAME, adapter, sanitize_attributes=redact_attributes
        )
        close = adapter.close
    else:
        telemetry = Telemetry(TracerProvider(shutdown_on_exit=False))
        close = lambda: None  # noqa: E731

    async def shutdown() -> None:
        try:
            await asyncio.to_thread(telemetry.shutdown)
        finally:
            await asyncio.to_thread(close)

    return telemetry, shutdown
