"""Small in-process rate limiter.

For a single-instance MVP this is sufficient. In a multi-instance deployment,
swap the storage for Redis while keeping the same dependency interface.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

from fastapi import Request

from app.core.config import settings
from app.core.errors import RateLimitedError


class SlidingWindowLimiter:
    def __init__(self, max_keys: int = 10_000) -> None:
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        # Bounds memory: a stream of unique identities must not grow the map
        # without limit.
        self._max_keys = max(1, max_keys)

    def _evict(self, cutoff: float) -> None:
        """Drop expired buckets, then the oldest ones if the map is still full."""

        stale = [key for key, bucket in self._events.items() if not bucket or bucket[-1] <= cutoff]
        for key in stale:
            del self._events[key]
        while len(self._events) >= self._max_keys:
            del self._events[next(iter(self._events))]

    def check(self, key: str, limit: int, window_seconds: int = 60) -> None:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            if key not in self._events and len(self._events) >= self._max_keys:
                self._evict(cutoff)
            bucket = self._events.setdefault(key, deque())
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = max(1, int(window_seconds - (now - bucket[0])))
                raise RateLimitedError(
                    "Too many requests. Please slow down and try again shortly.",
                    details={"retry_after_seconds": retry_after},
                )
            bucket.append(now)

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


limiter = SlidingWindowLimiter()


def client_identity(request: Request) -> str:
    """Best-effort client key for throttling.

    ``X-Forwarded-For`` is attacker-controlled unless a trusted proxy sets it,
    so it is only consulted when ``TRUST_FORWARDED_FOR`` is enabled. The
    rightmost entry is the hop appended by the nearest proxy.
    """

    if settings.trust_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.rsplit(",", 1)[-1].strip() or "unknown"
    return request.client.host if request.client else "unknown"


def rate_limit(scope: str, limit: int) -> Callable[[Request], None]:
    """Build a dependency that applies a per-user sliding-window limit."""

    def _check(request: Request) -> None:
        identity = getattr(request.state, "user_id", None) or client_identity(request)
        limiter.check(f"{scope}:{identity}", limit)

    return _check
