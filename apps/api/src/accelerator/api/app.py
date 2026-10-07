from fastapi import FastAPI

from accelerator.api.cost_guard import (
    ContextDependency,
    create_cost_guard_dependency,
    handle_token_budget_exceeded,
)
from accelerator.api.health import router as health_router
from accelerator.configuration.settings import Settings
from accelerator.security_core.cost_guard import RateLimiter, TokenBudgetExceeded


def create_app(
    settings: Settings,
    *,
    get_execution_context: ContextDependency | None = None,
    rate_limiter: RateLimiter | None = None,
) -> FastAPI:
    app = FastAPI(
        title="FDE AI Solution Accelerator API",
        version="0.1.0",
    )
    app.state.settings = settings
    app.add_exception_handler(TokenBudgetExceeded, handle_token_budget_exceeded)
    if get_execution_context is not None:
        app.state.request_cost_guard = create_cost_guard_dependency(
            settings,
            get_execution_context,
            rate_limiter=rate_limiter,
        )
    app.include_router(health_router)
    return app
