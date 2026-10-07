from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from accelerator.api.audit import AuthFailureAuditMiddleware, router as audit_router
from accelerator.api.health import router as health_router
from accelerator.configuration.settings import Settings
from accelerator.domain.audit import AuditRepository
from accelerator.identity.authentication import get_current_principal
from accelerator.identity.jwt_validator import EntraTokenValidator
from accelerator.identity.scope_resolver import install_scope_boundary


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with httpx.AsyncClient(timeout=5) as client:
        app.state.http_client = client
        yield


def create_app(settings: Settings, *, audit_repository: AuditRepository | None = None) -> FastAPI:
    app = FastAPI(
        title="FDE AI Solution Accelerator API",
        version="0.1.0",
        lifespan=lifespan,
        dependencies=[Depends(get_current_principal)],
        responses={
            401: {"description": "Missing or invalid bearer token."},
            403: {"description": "Insufficient app role."},
            503: {"description": "Identity, scope, or audit persistence is unavailable."},
        },
    )
    app.state.settings = settings
    app.state.token_validator = EntraTokenValidator(settings)
    app.state.audit_repository = audit_repository
    app.add_middleware(AuthFailureAuditMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.web_origin],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    app.include_router(audit_router)
    install_scope_boundary(app)
    return app
