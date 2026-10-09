import asyncio

from azure.identity import ManagedIdentityCredential
from azure.monitor.opentelemetry.exporter import (
    ApplicationInsightsSampler,
    AzureMonitorTraceExporter,
)
from opentelemetry.sdk.trace.export import SpanExporter
from opentelemetry.sdk.trace.sampling import Sampler
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ..export import AttributeSanitizer
from ..tracing import Telemetry


class ApplicationInsightsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FDE_TELEMETRY_", extra="forbid")

    service_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    managed_identity_client_id: str | None = None


class ApplicationInsightsAdapter:
    def __init__(
        self, settings: ApplicationInsightsSettings, *, connection_string: str | None = None
    ) -> None:
        self.settings = settings
        # None: the exporter reads APPLICATIONINSIGHTS_CONNECTION_STRING itself.
        self._connection_string = connection_string
        self._credential: ManagedIdentityCredential | None = None

    def create_sampler(self) -> Sampler:
        return ApplicationInsightsSampler(1.0)

    def create_exporter(self) -> SpanExporter:
        if self._credential is not None:
            raise RuntimeError("An Application Insights adapter can only create one exporter")
        credential = ManagedIdentityCredential(client_id=self.settings.managed_identity_client_id)
        try:
            # The exporter reads the deployment-provided destination from its standard env var.
            destination = (
                {"connection_string": self._connection_string}
                if self._connection_string is not None
                else {}
            )
            exporter = AzureMonitorTraceExporter(
                credential=credential, disable_offline_storage=True, **destination
            )
        except BaseException:
            credential.close()
            raise
        self._credential = credential
        return exporter

    def close(self) -> None:
        if self._credential is not None:
            self._credential.close()
            self._credential = None


async def start_application_insights(
    settings: ApplicationInsightsSettings,
    *,
    sanitize_attributes: AttributeSanitizer | None = None,
) -> tuple[Telemetry, ApplicationInsightsAdapter]:
    adapter = ApplicationInsightsAdapter(settings)
    telemetry = await asyncio.to_thread(
        Telemetry.create, settings.service_name, adapter, sanitize_attributes=sanitize_attributes
    )
    return telemetry, adapter


async def stop_application_insights(
    telemetry: Telemetry, adapter: ApplicationInsightsAdapter
) -> None:
    try:
        await asyncio.to_thread(telemetry.shutdown)
    finally:
        await asyncio.to_thread(adapter.close)
