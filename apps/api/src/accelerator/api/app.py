from fastapi import FastAPI

from accelerator.api.health import router as health_router
from accelerator.configuration.settings import Settings


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(
        title="FDE AI Solution Accelerator API",
        version="0.1.0",
    )
    app.state.settings = settings
    app.include_router(health_router)
    return app
