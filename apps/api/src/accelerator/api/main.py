"""ASGI entry point: ``uvicorn --factory accelerator.api.main:create_application``.

A factory, so importing this module constructs no credentials, engines or clients.
Logging is configured before the composition root (and the Azure SDKs it imports)
is loaded, so warnings raised while those modules import are structured too.
"""

from fastapi import FastAPI

from accelerator.configuration.settings import get_settings
from accelerator.telemetry.logging import configure_logging


def create_application() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    from accelerator.api.composition import build_application

    return build_application(settings)
