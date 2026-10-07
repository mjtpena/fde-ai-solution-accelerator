from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from accelerator.api.health import router as health_router
from accelerator.api.retrieval_diagnostics import (
    InMemoryRetrievalDiagnosticsStore,
    RetrievalDiagnosticsStore,
    router as retrieval_diagnostics_router,
)
from accelerator.configuration.settings import Settings
from accelerator.identity.authentication import AppRole, require_any_role
from accelerator.identity.jwt_validator import EntraTokenValidator
from accelerator.identity.scope_resolver import install_scope_boundary


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with httpx.AsyncClient(timeout=5) as client:
        app.state.http_client = client
        yield


def create_app(
    settings: Settings,
    diagnostics_store: RetrievalDiagnosticsStore | None = None,
) -> FastAPI:
    app = FastAPI(
        title="FDE AI Solution Accelerator API",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
        dependencies=[Depends(require_any_role(*AppRole))],
        responses={
            401: {"description": "Missing or invalid bearer token."},
            403: {"description": "Insufficient app role."},
        },
    )
    app.state.settings = settings
    app.state.retrieval_diagnostics_store = (
        diagnostics_store
        if diagnostics_store is not None
        else InMemoryRetrievalDiagnosticsStore()
    )
    app.state.token_validator = EntraTokenValidator(settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.web_origin],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    app.include_router(retrieval_diagnostics_router)
    install_scope_boundary(app)
    return app
