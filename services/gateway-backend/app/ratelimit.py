"""A per-key request limit for the MCP endpoint (sliding window, in memory).

Each API key may make `per_minute` requests in any window of `window` seconds. Requests over the limit are
refused with HTTP 429 and a Retry-After header, and do not extend the window, so a client that backs off
recovers as soon as its oldest request ages out. The counters live in this process: with several gateway
replicas each one counts separately, so the effective limit is per replica.
"""
from __future__ import annotations

import math
import time
from collections import deque


class RateLimiter:
    def __init__(self, per_minute: int, window: float = 60.0, clock=time.monotonic) -> None:
        self.per_minute, self.window, self._clock = per_minute, window, clock
        self._hits: dict[str, deque[float]] = {}
        self._logged: dict[str, float] = {}
        self._checks = 0

    @property
    def enabled(self) -> bool:
        return self.per_minute > 0

    def check(self, key: str) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds). An allowed request is counted."""
        if not self.enabled:
            return True, 0
        now = self._clock()
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= self.window:
            hits.popleft()
        if len(hits) >= self.per_minute:
            return False, max(1, math.ceil(self.window - (now - hits[0])))
        hits.append(now)
        self._checks += 1
        if self._checks % 500 == 0:
            self._sweep(now)
        return True, 0

    def first_rejection(self, key: str) -> bool:
        """True once per window per key, so a burst of refused requests writes one audit row, not thousands."""
        now = self._clock()
        last = self._logged.get(key)
        if last is None or now - last >= self.window:
            self._logged[key] = now
            return True
        return False

    def _sweep(self, now: float) -> None:
        """Forget keys that have been quiet for a whole window, so memory stays bounded."""
        for key in [k for k, h in self._hits.items() if not h or now - h[-1] >= self.window]:
            self._hits.pop(key, None)
        for key in [k for k, t in self._logged.items() if now - t >= self.window]:
            self._logged.pop(key, None)
