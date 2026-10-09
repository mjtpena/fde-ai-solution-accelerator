from __future__ import annotations

from collections import deque
from collections.abc import Awaitable, Callable
import math
from threading import Lock
from time import monotonic
from typing import Protocol


class CostGuardContext(Protocol):
    correlation_id: str
    user_id: str
    scope_ids: frozenset[str]


class RateLimitExceeded(Exception):
    def __init__(
        self,
        *,
        retry_after_seconds: float,
        limit: int,
        window_seconds: float,
    ) -> None:
        super().__init__("The request rate limit has been exceeded.")
        self.retry_after_seconds = retry_after_seconds
        self.limit = limit
        self.window_seconds = window_seconds


class RateLimiter(Protocol):
    """Raise ``RateLimitExceeded`` when the caller is over its limit.

    May be synchronous (in-process) or return an awaitable (shared stores such as
    PostgreSQL, required when more than one API replica runs).
    """

    def check(self, context: CostGuardContext) -> None | Awaitable[None]: ...


class SlidingWindowRateLimiter:
    def __init__(
        self,
        limit: int,
        window_seconds: float,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("Rate limit must be a positive integer.")
        if (
            isinstance(window_seconds, bool)
            or not isinstance(window_seconds, (int, float))
            or not math.isfinite(window_seconds)
            or window_seconds <= 0
        ):
            raise ValueError("Rate limit window must be a finite positive number.")
        self.limit = limit
        self.window_seconds = float(window_seconds)
        self._clock = clock
        self._requests: dict[tuple[str, tuple[str, ...]], deque[float]] = {}
        self._lock = Lock()
        self._next_cleanup_at = 0.0

    def check(self, context: CostGuardContext) -> None:
        if not context.user_id:
            raise ValueError("Execution context user_id must not be empty.")
        if any(not scope_id for scope_id in context.scope_ids):
            raise ValueError("Execution context scope_ids must not contain empty values.")

        key = (context.user_id, tuple(sorted(context.scope_ids)))
        with self._lock:
            now = self._clock()
            if not math.isfinite(now):
                raise ValueError("Rate limiter clock must return a finite timestamp.")
            cutoff = now - self.window_seconds
            if now >= self._next_cleanup_at:
                for tracked_key, tracked_requests in tuple(self._requests.items()):
                    while tracked_requests and tracked_requests[0] <= cutoff:
                        tracked_requests.popleft()
                    if not tracked_requests:
                        del self._requests[tracked_key]
                self._next_cleanup_at = now + self.window_seconds

            requests = self._requests.setdefault(key, deque())
            while requests and requests[0] <= cutoff:
                requests.popleft()

            if len(requests) >= self.limit:
                retry_after = max(0.0, requests[0] + self.window_seconds - now)
                raise RateLimitExceeded(
                    retry_after_seconds=retry_after,
                    limit=self.limit,
                    window_seconds=self.window_seconds,
                )

            requests.append(now)
