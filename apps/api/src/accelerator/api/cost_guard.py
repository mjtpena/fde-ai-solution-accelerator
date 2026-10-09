from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import inspect
import math
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.responses import JSONResponse
from starlette.requests import Request

from accelerator.configuration.settings import Settings
from accelerator.security_core.cost_guard import (
    CostGuardContext,
    RateLimitExceeded,
    RateLimiter,
    SlidingWindowRateLimiter,
    TokenBudget,
    TokenBudgetExceeded,
)


ContextDependency = Callable[..., CostGuardContext | Awaitable[CostGuardContext]]


@dataclass(frozen=True, slots=True)
class RequestCostGuard:
    correlation_id: str
    token_budget: TokenBudget


def create_cost_guard_dependency(
    settings: Settings,
    get_execution_context: ContextDependency,
    *,
    rate_limiter: RateLimiter | None = None,
) -> Callable[..., Awaitable[RequestCostGuard]]:
    limiter = (
        rate_limiter
        if rate_limiter is not None
        else SlidingWindowRateLimiter(
            settings.request_rate_limit,
            settings.request_rate_window_seconds,
        )
    )

    async def guard_request_cost(
        request: Request,
        context: Annotated[CostGuardContext, Depends(get_execution_context)],
    ) -> RequestCostGuard:
        existing_guard = getattr(request.state, "request_cost_guard", None)
        if isinstance(existing_guard, RequestCostGuard):
            return existing_guard
        try:
            pending = limiter.check(context)
            if inspect.isawaitable(pending):
                await pending
        except RateLimitExceeded as error:
            retry_after = max(1, math.ceil(error.retry_after_seconds))
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "rate_limit_exceeded",
                    "correlation_id": context.correlation_id,
                    "retry_after_seconds": error.retry_after_seconds,
                },
                headers={"Retry-After": str(retry_after)},
            ) from error
        request_cost_guard = RequestCostGuard(
            correlation_id=context.correlation_id,
            token_budget=TokenBudget(
                settings.request_token_budget,
                correlation_id=context.correlation_id,
            ),
        )
        request.state.request_cost_guard = request_cost_guard
        return request_cost_guard

    return guard_request_cost


async def handle_token_budget_exceeded(
    request: Request,
    error: TokenBudgetExceeded,
) -> JSONResponse:
    del request
    return JSONResponse(
        status_code=429,
        content={
            "detail": {
                "code": "token_budget_exceeded",
                "correlation_id": error.correlation_id,
                "budget_limit": error.budget_limit,
                "requested_tokens": error.requested_tokens,
                "remaining_tokens": error.remaining_tokens,
            }
        },
    )
