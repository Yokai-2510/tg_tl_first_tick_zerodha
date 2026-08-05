"""
Client-side rate limiting for Kite's documented caps.

    orders   10/sec, 400/min, 5000/day
    quote     1/sec
    other    10/sec

Enforced locally so we fail fast and visibly instead of collecting broker
429s during the one minute of the day that matters.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class _Bucket:
    """Sliding-window counter."""

    limit: int
    window_s: float
    hits: deque[float] = field(default_factory=deque)

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_s
        while self.hits and self.hits[0] <= cutoff:
            self.hits.popleft()

    def try_acquire(self, now: float) -> bool:
        self._prune(now)
        if len(self.hits) >= self.limit:
            return False
        self.hits.append(now)
        return True

    def retry_after(self, now: float) -> float:
        self._prune(now)
        if len(self.hits) < self.limit:
            return 0.0
        return max(0.0, self.hits[0] + self.window_s - now)

    @property
    def used(self) -> int:
        return len(self.hits)


class RateLimiter:
    """Thread-safe multi-window limiter.

    `acquire` consumes from EVERY applicable window atomically, or from none —
    a partial consume would let a later call slip past a window that had
    already been charged.
    """

    def __init__(
        self,
        *,
        orders_per_sec: int = 10,
        orders_per_min: int = 400,
        orders_per_day: int = 5000,
        quote_per_sec: int = 1,
        other_per_sec: int = 10,
    ) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, list[_Bucket]] = {
            "order": [
                _Bucket(orders_per_sec, 1.0),
                _Bucket(orders_per_min, 60.0),
                _Bucket(orders_per_day, 86_400.0),
            ],
            "quote": [_Bucket(quote_per_sec, 1.0)],
            "other": [_Bucket(other_per_sec, 1.0)],
        }
        self._rejected: dict[str, int] = {k: 0 for k in self._buckets}

    def acquire(self, kind: str = "other", *, timeout: float = 0.0) -> bool:
        """Try to consume one slot, optionally waiting up to `timeout` seconds."""
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            with self._lock:
                buckets = self._buckets.get(kind) or self._buckets["other"]
                now = time.monotonic()
                if all(len(b.hits) < b.limit or b.retry_after(now) == 0.0
                       for b in buckets):
                    if all(b.try_acquire(now) for b in buckets):
                        return True
                wait = max((b.retry_after(now) for b in buckets), default=0.0)

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                with self._lock:
                    self._rejected[kind] = self._rejected.get(kind, 0) + 1
                return False
            time.sleep(min(wait or 0.01, remaining, 0.25))

    def stats(self) -> dict[str, dict[str, int]]:
        with self._lock:
            now = time.monotonic()
            out: dict[str, dict[str, int]] = {}
            for kind, buckets in self._buckets.items():
                for b in buckets:
                    b._prune(now)
                out[kind] = {
                    "rejected": self._rejected.get(kind, 0),
                    **{f"used_{int(b.window_s)}s": b.used for b in buckets},
                    **{f"limit_{int(b.window_s)}s": b.limit for b in buckets},
                }
            return out

    def reset(self) -> None:
        with self._lock:
            for buckets in self._buckets.values():
                for b in buckets:
                    b.hits.clear()
            self._rejected = {k: 0 for k in self._buckets}


def from_config(rate_cfg) -> RateLimiter:
    get = (lambda k, d: getattr(rate_cfg, k, d)) if not isinstance(rate_cfg, dict) \
        else (lambda k, d: rate_cfg.get(k, d))
    return RateLimiter(
        orders_per_sec=get("orders_per_sec", 10),
        orders_per_min=get("per_minute", 400),
        orders_per_day=get("daily_cap", 5000),
        quote_per_sec=get("quote_per_sec", 1),
    )


__all__ = ["RateLimiter", "from_config"]
