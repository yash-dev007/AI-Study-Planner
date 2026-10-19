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

from app.core.errors import RateLimitedError


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: int = 60) -> None:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
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
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(scope: str, limit: int) -> Callable[[Request], None]:
    """Build a dependency that applies a per-user sliding-window limit."""

    def _check(request: Request) -> None:
        identity = getattr(request.state, "user_id", None) or client_identity(request)
        limiter.check(f"{scope}:{identity}", limit)

    return _check
