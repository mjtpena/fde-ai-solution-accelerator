"""PostgreSQL-backed cost controls shared by every API replica.

In-process counters reset per replica and multiply the effective limit by the
replica count. These adapters keep the request rate limit and the per-session /
per-turn tool-call limits in the database, using single-statement atomic upserts.
"""

import hashlib
import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Table,
    delete,
    func,
    select,
    text,
)
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
    Index("ix_rate_limit_windows_window_start", "window_start"),
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
# Arbitrary constant key for the pg_advisory_xact_lock serializing session admission.
_ADMISSION_LOCK = 0x46444541  # "FDEA"


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
                # First request of a new window for this key: drop every key's windows
                # older than the previous one, so one-off callers do not accumulate.
                await session.execute(
                    delete(rate_limit_windows).where(
                        rate_limit_windows.c.window_start
                        < window_start - timedelta(seconds=self.window_seconds)
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
            session_total: int = (
                await session.execute(self._increment(session_id, SESSION_TOTAL, now))
            ).scalar_one()
            if session_total == 1:
                # A new session: admit it only within the shared capacity. The advisory
                # lock serializes admissions across replicas until this commit.
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"), {"key": _ADMISSION_LOCK}
                )
                tracked = (
                    await session.execute(
                        select(func.count()).where(
                            tool_call_counters.c.turn_id == SESSION_TOTAL
                        )
                    )
                ).scalar_one()
                if tracked > limits.max_tracked_sessions:
                    raise ToolCallLimitExceeded("tracked-session capacity exceeded")
            if session_total > limits.max_calls_per_session:
                raise ToolCallLimitExceeded("per-session tool call limit exceeded")
            turn_total: int = (
                await session.execute(self._increment(session_id, turn_id, now))
            ).scalar_one()
            if turn_total > limits.max_calls_per_turn:
                raise ToolCallLimitExceeded("per-turn tool call limit exceeded")

    async def release_session(self, session_id: str) -> None:
        async with self._sessions() as session, session.begin():
            await session.execute(
                delete(tool_call_counters).where(tool_call_counters.c.session_id == session_id)
            )
