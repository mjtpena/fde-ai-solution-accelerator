"""ASGI entry point: ``uvicorn --factory accelerator.api.main:create_application``.

A factory, so importing this module constructs no credentials, engines or clients.
"""

from fastapi import FastAPI

from accelerator.api.composition import build_application
from accelerator.configuration.settings import get_settings


def create_application() -> FastAPI:
    return build_application(get_settings())
