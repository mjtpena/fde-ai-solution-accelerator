from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from accelerator.api.health import router as health_router
from accelerator.configuration.settings import Settings
from accelerator.identity.authentication import get_current_principal
from accelerator.identity.jwt_validator import EntraTokenValidator


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with httpx.AsyncClient(timeout=5) as client:
        app.state.http_client = client
        yield


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(
        title="FDE AI Solution Accelerator API",
        version="0.1.0",
        lifespan=lifespan,
        dependencies=[Depends(get_current_principal)],
        responses={
            401: {"description": "Missing or invalid bearer token."},
            403: {"description": "Insufficient app role."},
        },
    )
    app.state.settings = settings
    app.state.token_validator = EntraTokenValidator(settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.web_origin],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    return app
