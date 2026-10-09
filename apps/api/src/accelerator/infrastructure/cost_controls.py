"""PostgreSQL-backed cost controls shared by every API replica.

In-process counters reset per replica and multiply the effective limit by the
replica count. These adapters keep the request rate limit and the per-session /
per-turn tool-call limits in the database, using single-statement atomic upserts.
"""

import hashlib
import math
from collections.abc import Callable
from typing import Any
from datetime import UTC, datetime, timedelta

from sqlalchemy import Column, DateTime, Integer, String, Table, delete
from sqlalchemy.dialects.postgresql import insert

from accelerator.agent_core.middleware import ToolCallLimitExceeded, ToolCallLimits
from accelerator.security_core.cost_guard import CostGuardContext, RateLimitExceeded
from accelerator.security_core.infrastructure.database import Base, SessionFactory

rate_limit_windows = Table(
    "rate_limit_windows",
    Base.metadata,
    Column("limiter_key", String(64), primary_key=True),
    Column("window_start", DateTime(timezone=True), primary_key=True),
    Column("request_count", Integer, nullable=False),
)

tool_call_counters = Table(
    "tool_call_counters",
    Base.metadata,
    Column("session_id", String(255), primary_key=True),
    # "" holds the session-wide total; any other value is one turn.
    Column("turn_id", String(255), primary_key=True),
    Column("call_count", Integer, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

SESSION_TOTAL = ""


def _limiter_key(context: CostGuardContext) -> str:
    """Fixed-length key for a user and their exact server-resolved scope set."""
    material = "\x1f".join([context.user_id, *sorted(context.scope_ids)])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class PostgresRateLimiter:
    """Fixed-window request limit per user and scope set, shared across replicas."""

    def __init__(
        self,
        sessions: SessionFactory,
        limit: int,
        window_seconds: float,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if limit <= 0 or not math.isfinite(window_seconds) or window_seconds <= 0:
            raise ValueError("Rate limit and window must be positive.")
        self._sessions = sessions
        self.limit = limit
        self.window_seconds = float(window_seconds)
        self._clock = clock

    async def check(self, context: CostGuardContext) -> None:
        if not context.user_id:
            raise ValueError("Execution context user_id must not be empty.")
        now = self._clock()
        window_index = math.floor(now.timestamp() / self.window_seconds)
        window_start = datetime.fromtimestamp(window_index * self.window_seconds, UTC)
        key = _limiter_key(context)
        statement = (
            insert(rate_limit_windows)
            .values(limiter_key=key, window_start=window_start, request_count=1)
            .on_conflict_do_update(
                index_elements=["limiter_key", "window_start"],
                set_={"request_count": rate_limit_windows.c.request_count + 1},
            )
            .returning(rate_limit_windows.c.request_count)
        )
        async with self._sessions() as session, session.begin():
            count = (await session.execute(statement)).scalar_one()
            if count == 1:
                # First request of a new window: drop this key's expired windows.
                await session.execute(
                    delete(rate_limit_windows).where(
                        rate_limit_windows.c.limiter_key == key,
                        rate_limit_windows.c.window_start < window_start,
                    )
                )
        if count > self.limit:
            window_end = window_start + timedelta(seconds=self.window_seconds)
            raise RateLimitExceeded(
                retry_after_seconds=max(0.0, (window_end - now).total_seconds()),
                limit=self.limit,
                window_seconds=self.window_seconds,
            )


class PostgresToolCallCounter:
    """``ToolCallCounter`` whose per-session and per-turn counts survive replicas."""

    def __init__(
        self, sessions: SessionFactory, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self._sessions = sessions
        self._clock = clock

    def _increment(self, session_id: str, turn_id: str, now: datetime) -> Any:
        return (
            insert(tool_call_counters)
            .values(session_id=session_id, turn_id=turn_id, call_count=1, updated_at=now)
            .on_conflict_do_update(
                index_elements=["session_id", "turn_id"],
                set_={"call_count": tool_call_counters.c.call_count + 1, "updated_at": now},
            )
            .returning(tool_call_counters.c.call_count)
        )

    async def consume(self, session_id: str, turn_id: str, limits: ToolCallLimits) -> None:
        if not turn_id:
            raise ValueError("turn_id must not be empty.")
        now = self._clock()
        # Raising inside the transaction rolls both increments back, so a refused
        # call consumes nothing.
        async with self._sessions() as session, session.begin():
            session_total = (
                await session.execute(self._increment(session_id, SESSION_TOTAL, now))
            ).scalar_one()
            if session_total > limits.max_calls_per_session:
                raise ToolCallLimitExceeded("per-session tool call limit exceeded")
            turn_total = (
                await session.execute(self._increment(session_id, turn_id, now))
            ).scalar_one()
            if turn_total > limits.max_calls_per_turn:
                raise ToolCallLimitExceeded("per-turn tool call limit exceeded")

    async def release_session(self, session_id: str) -> None:
        async with self._sessions() as session, session.begin():
            await session.execute(
                delete(tool_call_counters).where(tool_call_counters.c.session_id == session_id)
            )
