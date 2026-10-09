"""Shared cost controls on a migrated PostgreSQL database."""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from accelerator.agent_core.middleware import ToolCallLimitExceeded, ToolCallLimits
from accelerator.infrastructure.cost_controls import PostgresRateLimiter, PostgresToolCallCounter
from accelerator.security_core.cost_guard import RateLimitExceeded
from accelerator.security_core.infrastructure.database import create_session_factory


@dataclass
class Caller:
    correlation_id: str = "c"
    user_id: str = "user-1"
    scope_ids: frozenset[str] = field(default_factory=lambda: frozenset({"scope-a"}))


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 9, 12, 0, 5, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


async def test_rate_limit_is_shared_between_limiter_instances(migrated_database_url: str) -> None:
    engine = create_async_engine(migrated_database_url)
    sessions = create_session_factory(engine)
    clock = Clock()
    # Two instances stand in for two API replicas.
    replica_a = PostgresRateLimiter(sessions, 2, 60, clock=clock)
    replica_b = PostgresRateLimiter(sessions, 2, 60, clock=clock)
    try:
        await replica_a.check(Caller())
        await replica_b.check(Caller())
        with pytest.raises(RateLimitExceeded) as raised:
            await replica_a.check(Caller())
        assert raised.value.retry_after_seconds == pytest.approx(55)

        # Different scope sets and users are limited independently.
        await replica_b.check(Caller(scope_ids=frozenset({"scope-b"})))
        await replica_b.check(Caller(user_id="user-2"))

        clock.now += timedelta(seconds=60)
        await replica_a.check(Caller())
    finally:
        await engine.dispose()


async def test_concurrent_requests_never_exceed_the_limit(migrated_database_url: str) -> None:
    engine = create_async_engine(migrated_database_url, pool_size=10)
    limiter = PostgresRateLimiter(create_session_factory(engine), 5, 60)

    async def attempt() -> bool:
        try:
            await limiter.check(Caller())
        except RateLimitExceeded:
            return False
        return True

    try:
        results = await asyncio.gather(*(attempt() for _ in range(20)))
    finally:
        await engine.dispose()

    assert sum(results) == 5


async def test_tool_call_limits_hold_across_counters_and_refusals_consume_nothing(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    sessions = create_session_factory(engine)
    limits = ToolCallLimits(max_calls_per_turn=2, max_calls_per_session=3)
    replica_a = PostgresToolCallCounter(sessions)
    replica_b = PostgresToolCallCounter(sessions)
    try:
        await replica_a.consume("session-1", "turn-1", limits)
        await replica_b.consume("session-1", "turn-1", limits)
        with pytest.raises(ToolCallLimitExceeded, match="per-turn"):
            await replica_a.consume("session-1", "turn-1", limits)
        # The refused call above did not count against the session.
        await replica_b.consume("session-1", "turn-2", limits)
        with pytest.raises(ToolCallLimitExceeded, match="per-session"):
            await replica_a.consume("session-1", "turn-3", limits)

        await replica_a.release_session("session-1")
        await replica_b.consume("session-1", "turn-4", limits)
    finally:
        await engine.dispose()


async def test_expired_windows_of_other_callers_are_pruned(migrated_database_url: str) -> None:
    from sqlalchemy import text

    engine = create_async_engine(migrated_database_url)
    clock = Clock()
    limiter = PostgresRateLimiter(create_session_factory(engine), 5, 60, clock=clock)
    try:
        await limiter.check(Caller(user_id="one-off-caller"))
        clock.now += timedelta(seconds=180)
        await limiter.check(Caller(user_id="regular-caller"))
        async with engine.connect() as connection:
            remaining = (
                await connection.execute(text("SELECT count(*) FROM rate_limit_windows"))
            ).scalar_one()
    finally:
        await engine.dispose()

    assert remaining == 1


async def test_tracked_session_capacity_is_shared_and_released(migrated_database_url: str) -> None:
    engine = create_async_engine(migrated_database_url)
    counter = PostgresToolCallCounter(create_session_factory(engine))
    limits = ToolCallLimits(max_tracked_sessions=2)
    try:
        await counter.consume("s-1", "t", limits)
        await counter.consume("s-2", "t", limits)
        with pytest.raises(ToolCallLimitExceeded, match="capacity"):
            await counter.consume("s-3", "t", limits)
        await counter.consume("s-1", "t2", limits)  # existing sessions keep working
        await counter.release_session("s-1")
        await counter.consume("s-3", "t", limits)
    finally:
        await engine.dispose()
