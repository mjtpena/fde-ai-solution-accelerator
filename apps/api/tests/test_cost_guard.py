import asyncio
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import copy
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends
from pydantic import ValidationError
from starlette.types import ASGIApp

from accelerator.api.app import create_app
from accelerator.api.cost_guard import RequestCostGuard
from accelerator.configuration.settings import Settings
from accelerator.identity.authentication import AppRole, Principal, get_current_principal
from accelerator.security_core.cost_guard import (
    CostGuardContext,
    RateLimitExceeded,
    SlidingWindowRateLimiter,
    TokenBudget,
    TokenBudgetExceeded,
    TokenReservation,
)


@dataclass(frozen=True)
class FakeExecutionContext:
    correlation_id: str
    user_id: str
    scope_ids: frozenset[str]


@dataclass(frozen=True)
class AsgiResponse:
    status_code: int
    headers: dict[str, str]
    body: dict[str, object]


def get_asgi_response(app: ASGIApp, path: str) -> AsgiResponse:
    async def send_request() -> AsgiResponse:
        request_sent = False
        messages: list[dict[str, object]] = []
        request_path, _, query = path.partition("?")

        async def receive() -> dict[str, object]:
            nonlocal request_sent
            if request_sent:
                return {"type": "http.disconnect"}
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict[str, object]) -> None:
            messages.append(message)

        await app(
            {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": request_path,
                "raw_path": request_path.encode(),
                "query_string": query.encode(),
                "root_path": "",
                "headers": [],
                "client": ("testclient", 50000),
                "server": ("testserver", 80),
            },
            receive,
            send,
        )

        response_start = next(
            message for message in messages if message["type"] == "http.response.start"
        )
        response_body = b"".join(
            message.get("body", b"")  # type: ignore[arg-type]
            for message in messages
            if message["type"] == "http.response.body"
        )
        headers = {
            name.decode().lower(): value.decode()
            for name, value in response_start["headers"]  # type: ignore[union-attr]
        }
        return AsgiResponse(
            status_code=int(response_start["status"]),
            headers=headers,
            body=json.loads(response_body),
        )

    return asyncio.run(send_request())


class TokenBudgetTests(unittest.TestCase):
    def test_unissued_reservations_cannot_change_counters(self) -> None:
        for operation in ("cancel", "settle"):
            with self.subTest(operation=operation):
                budget = TokenBudget(10)
                issued = budget.reserve(4)
                unissued = TokenReservation(budget, 8)
                with self.assertRaisesRegex(ValueError, "not issued"):
                    if operation == "cancel":
                        unissued.cancel()
                    else:
                        unissued.settle(0)
                self.assertEqual(budget.remaining_tokens, 6)
                self.assertEqual(budget.consumed_tokens, 0)
                issued.settle(4)
                self.assertEqual(budget.remaining_tokens, 6)

    def test_copied_reservation_is_not_an_issued_handle(self) -> None:
        budget = TokenBudget(10)
        issued = budget.reserve(4)
        with self.assertRaisesRegex(ValueError, "not issued"):
            copy(issued).cancel()
        self.assertEqual(budget.remaining_tokens, 6)
        issued.cancel()
        self.assertEqual(budget.remaining_tokens, 10)

    def test_accounting_uses_issued_amount_not_mutated_handle_amount(self) -> None:
        budget = TokenBudget(10)
        issued = budget.reserve(4)
        issued._token_count = 10
        issued.cancel()
        self.assertEqual(budget.remaining_tokens, 10)

    def test_exact_limit_is_allowed_and_overage_fails(self) -> None:
        budget = TokenBudget(10, correlation_id="correlation-1")

        budget.consume(10)

        self.assertEqual(budget.consumed_tokens, 10)
        self.assertEqual(budget.remaining_tokens, 0)
        with self.assertRaises(TokenBudgetExceeded) as error:
            budget.consume(1)
        self.assertEqual(error.exception.requested_tokens, 1)
        self.assertEqual(error.exception.remaining_tokens, 0)
        self.assertEqual(error.exception.correlation_id, "correlation-1")

    def test_reservations_charge_actual_usage_and_release_unused_tokens(self) -> None:
        budget = TokenBudget(10)
        reservation = budget.reserve(8)

        self.assertEqual(budget.remaining_tokens, 2)
        reservation.settle(5)

        self.assertEqual(budget.consumed_tokens, 5)
        self.assertEqual(budget.remaining_tokens, 5)

    def test_reservation_cannot_settle_usage_above_the_total_budget(self) -> None:
        budget = TokenBudget(10)
        reservation = budget.reserve(8)

        with self.assertRaises(TokenBudgetExceeded):
            reservation.settle(11)
        self.assertEqual(budget.remaining_tokens, 2)
        reservation.cancel()
        self.assertEqual(budget.remaining_tokens, 10)

    def test_reservation_cannot_be_settled_twice(self) -> None:
        budget = TokenBudget(10)
        reservation = budget.reserve(4)
        reservation.cancel()

        with self.assertRaises(ValueError):
            reservation.settle(0)

    def test_invalid_token_limits_and_counts_are_rejected(self) -> None:
        for limit in (0, -1, True):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                TokenBudget(limit)

        budget = TokenBudget(10)
        for count in (-1, True, 1.5):
            with self.subTest(count=count), self.assertRaises(ValueError):
                budget.consume(count)  # type: ignore[arg-type]


class RateLimitTests(unittest.TestCase):
    def test_concurrent_requests_sample_clock_inside_the_counter_lock(self) -> None:
        timestamps = iter(range(100, 120))

        def clock() -> float:
            self.assertTrue(limiter._lock.locked())
            return float(next(timestamps))

        limiter = SlidingWindowRateLimiter(20, 60, clock=clock)
        context = FakeExecutionContext("correlation-1", "user-1", frozenset({"scope-1"}))
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(limiter.check, [context] * 20))

        requests = limiter._requests[("user-1", ("scope-1",))]
        self.assertEqual(list(requests), list(range(100, 120)))
        limiter._clock = lambda: 159.0
        with self.assertRaises(RateLimitExceeded) as error:
            limiter.check(context)
        self.assertEqual(error.exception.retry_after_seconds, 1)
        limiter._clock = lambda: 160.0
        limiter.check(context)

    def test_sliding_window_boundary_and_retry_after(self) -> None:
        now = [100.0]
        limiter = SlidingWindowRateLimiter(2, 10, clock=lambda: now[0])
        context = FakeExecutionContext("correlation-1", "user-1", frozenset({"scope-1"}))

        limiter.check(context)
        limiter.check(context)
        with self.assertRaises(RateLimitExceeded) as error:
            limiter.check(context)
        self.assertEqual(error.exception.retry_after_seconds, 10)

        now[0] = 110.0
        limiter.check(context)

    def test_distinct_execution_context_scope_has_an_independent_bucket(self) -> None:
        limiter = SlidingWindowRateLimiter(1, 60, clock=lambda: 100.0)
        first_context = FakeExecutionContext("correlation-1", "user-1", frozenset({"scope-1"}))
        second_context = FakeExecutionContext("correlation-2", "user-1", frozenset({"scope-2"}))

        limiter.check(first_context)
        limiter.check(second_context)

        with self.assertRaises(RateLimitExceeded):
            limiter.check(first_context)


class CostGuardApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = FakeExecutionContext(
            correlation_id="correlation-1",
            user_id="user-1",
            scope_ids=frozenset({"scope-1"}),
        )
        self.context_resolutions = 0

        async def get_execution_context() -> CostGuardContext:
            self.context_resolutions += 1
            return self.context

        self.app = create_app(
            Settings(
                environment="test",
                entra_tenant_id="00000000-0000-0000-0000-000000000001",
                entra_audience="api://test",
                request_token_budget=4,
                request_rate_limit=1,
                request_rate_window_seconds=60,
            ),
            get_execution_context=get_execution_context,
        )
        async def authenticated_principal() -> Principal:
            return Principal(subject="user-1", roles=frozenset({AppRole.READER}))

        self.app.dependency_overrides[get_current_principal] = authenticated_principal
        request_cost_guard = self.app.state.request_cost_guard

        @self.app.get("/protected")
        async def protected(
            context: Annotated[CostGuardContext, Depends(get_execution_context)],
            cost_guard: Annotated[RequestCostGuard, Depends(request_cost_guard)],
            repeated_guard: Annotated[
                RequestCostGuard,
                Depends(request_cost_guard, use_cache=False),
            ],
        ) -> dict[str, str | bool]:
            return {
                "correlation_id": context.correlation_id,
                "user_id": context.user_id,
                "guard_correlation_id": cost_guard.correlation_id,
                "same_guard": cost_guard is repeated_guard,
            }

        @self.app.get("/over-budget")
        async def over_budget(
            cost_guard: Annotated[RequestCostGuard, Depends(request_cost_guard)],
        ) -> dict[str, bool]:
            cost_guard.token_budget.consume(5)
            return {"accepted": True}

    def test_rate_limit_uses_execution_context_and_returns_retry_after(self) -> None:
        first = get_asgi_response(self.app, "/protected?user_id=attacker&scope_id=other")
        second = get_asgi_response(
            self.app, "/protected?user_id=another-user&scope_id=another-scope"
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.body["user_id"], "user-1")
        self.assertEqual(first.body["guard_correlation_id"], "correlation-1")
        self.assertTrue(first.body["same_guard"])
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.headers["retry-after"], "60")
        detail = second.body["detail"]
        self.assertIsInstance(detail, dict)
        self.assertEqual(detail["code"], "rate_limit_exceeded")
        self.assertEqual(self.context_resolutions, 2)

    def test_token_budget_overage_returns_explicit_429(self) -> None:
        response = get_asgi_response(self.app, "/over-budget")

        self.assertEqual(response.status_code, 429)
        detail = response.body["detail"]
        self.assertIsInstance(detail, dict)
        self.assertEqual(detail["code"], "token_budget_exceeded")
        self.assertEqual(detail["correlation_id"], "correlation-1")

    def test_token_budget_settings_must_be_positive(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(environment="test", request_token_budget=0)

        with self.assertRaises(ValidationError):
            Settings(environment="test", request_rate_limit=0)


if __name__ == "__main__":
    unittest.main()


class CostGuardOnProductRoutesTests(unittest.TestCase):
    def make_app(self) -> Any:
        from accelerator.agent_core.workflows.grounded_answer import (
            Abstention,
            GroundedAnswerResult,
        )

        class AbstainingChat:
            async def run(self, query: str, ctx: Any) -> GroundedAnswerResult:
                return GroundedAnswerResult(
                    status="abstained",
                    answer=None,
                    citations=(),
                    citation_sources=(),
                    abstention=Abstention(reason="No evidence.", evidence_ids=()),
                )

        from datetime import UTC, datetime, timedelta

        from accelerator.security_core.data_boundaries.context import ExecutionContext

        context = ExecutionContext(
            correlation_id="correlation-1",
            user_id="user-1",
            roles=frozenset({"Reader", "Contributor"}),
            scope_ids=frozenset({"scope-1"}),
            deadline_utc=datetime.now(UTC) + timedelta(minutes=1),
        )

        async def get_execution_context() -> ExecutionContext:
            return context

        from accelerator.identity import scope_resolver

        app = create_app(
            Settings(
                environment="test",
                entra_tenant_id="00000000-0000-0000-0000-000000000001",
                entra_audience="api://test",
                request_rate_limit=1,
            ),
            chat_turn=AbstainingChat(),
        )

        async def authenticated_principal() -> Principal:
            return Principal(subject="user-1", roles=frozenset({AppRole.READER}))

        app.dependency_overrides[get_current_principal] = authenticated_principal
        app.dependency_overrides[scope_resolver.get_execution_context] = get_execution_context
        return app

    def test_chat_stream_is_rate_limited(self) -> None:
        from fastapi.testclient import TestClient

        with TestClient(self.make_app()) as client:
            first = client.post("/chat/stream", json={"message": "Question"})
            second = client.post("/chat/stream", json={"message": "Question"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.json()["detail"]["code"], "rate_limit_exceeded")

    def test_diagnostics_route_shares_the_rate_limit(self) -> None:
        from fastapi.testclient import TestClient

        with TestClient(self.make_app()) as client:
            first = client.post("/chat/stream", json={"message": "Question"})
            diagnostics = client.get("/diagnostics/retrieval/correlation-1")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(diagnostics.status_code, 429)

    def test_openapi_declares_429_on_guarded_routes_only(self) -> None:
        paths = self.make_app().openapi()["paths"]

        self.assertIn("429", paths["/chat/stream"]["post"]["responses"])
        self.assertNotIn("429", paths["/audit-events"]["get"]["responses"])


class TokenBudgetOnChatTests(unittest.TestCase):
    def test_chat_model_calls_draw_from_the_request_token_budget(self) -> None:
        from datetime import UTC, datetime, timedelta

        from fastapi.testclient import TestClient

        from accelerator.agent_core.workflows.generation import current_token_budget
        from accelerator.identity import scope_resolver
        from accelerator.security_core.data_boundaries.context import ExecutionContext

        class BudgetedChat:
            async def run(self, query: str, ctx: Any) -> Any:
                budget = current_token_budget.get()
                assert budget is not None
                budget.reserve(budget.limit + 1)  # a model call larger than the budget

        context = ExecutionContext(
            correlation_id="correlation-1",
            user_id="user-1",
            roles=frozenset({"Reader"}),
            scope_ids=frozenset({"scope-1"}),
            deadline_utc=datetime.now(UTC) + timedelta(minutes=1),
        )
        app = create_app(
            Settings(
                environment="test",
                entra_tenant_id="00000000-0000-0000-0000-000000000001",
                entra_audience="api://test",
                request_token_budget=100,
            ),
            chat_turn=BudgetedChat(),
        )

        async def principal() -> Principal:
            return Principal(subject="user-1", roles=frozenset({AppRole.READER}))

        async def trusted() -> ExecutionContext:
            return context

        app.dependency_overrides[get_current_principal] = principal
        app.dependency_overrides[scope_resolver.get_execution_context] = trusted
        with TestClient(app) as client:
            response = client.post("/chat/stream", json={"message": "Question"})

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["detail"]["code"], "token_budget_exceeded")
