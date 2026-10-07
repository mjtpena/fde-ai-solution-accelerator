from fastapi import FastAPI

from accelerator.api.health import router as health_router
from accelerator.api.retrieval_diagnostics import (
    InMemoryRetrievalDiagnosticsStore,
    RetrievalDiagnosticsStore,
    router as retrieval_diagnostics_router,
)
from accelerator.configuration.settings import Settings


def create_app(
    settings: Settings,
    diagnostics_store: RetrievalDiagnosticsStore | None = None,
) -> FastAPI:
    app = FastAPI(
        title="FDE AI Solution Accelerator API",
        version="0.1.0",
    )
    app.state.settings = settings
    app.state.retrieval_diagnostics_store = (
        diagnostics_store
        if diagnostics_store is not None
        else InMemoryRetrievalDiagnosticsStore()
    )
    app.include_router(health_router)
    app.include_router(retrieval_diagnostics_router)
    return app
