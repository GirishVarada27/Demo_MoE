"""Resource limits for the HTTP API: per-applicant rate limiting and a
bounded session store. Both keep their state in process memory (the API
runs as a single worker) and read their limits from env vars:

  MOE_RATE_LIMIT_PER_MINUTE   sustained chat turns per applicant (default 30)
  MOE_RATE_LIMIT_BURST        turns allowed back to back (default 15)
  MOE_SESSION_IDLE_SECONDS    idle time before a session expires (default 1800)
  MOE_MAX_SESSIONS_PER_APPLICANT  oldest is evicted beyond this (default 5)
  MOE_MAX_SESSIONS            whole store; least recently used evicted (default 10000)
  MOE_MAX_TURNS_PER_SESSION   then the client must start a new session (default 50)
"""

from __future__ import annotations

import math
import os
import threading
import time
from collections import OrderedDict
from typing import Callable, Generic, TypeVar

S = TypeVar("S")


def _env_number(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


class RateLimiter:
    """Token bucket per key: `burst` requests at once, refilled at `per_minute`."""

    def __init__(self, per_minute: float, burst: float, clock: Callable[[], float] = time.monotonic):
        self.rate = per_minute / 60.0
        self.burst = burst
        self.clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last refill time)
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "RateLimiter":
        return cls(_env_number("MOE_RATE_LIMIT_PER_MINUTE", 30), _env_number("MOE_RATE_LIMIT_BURST", 15))

    def acquire(self, key: str) -> float | None:
        """Take one token. Returns None if allowed, else seconds until one is available."""
        now = self.clock()
        with self._lock:
            tokens, last = self._buckets.get(key, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            if tokens >= 1:
                self._buckets[key] = (tokens - 1, now)
                return None
            self._buckets[key] = (tokens, now)
            return math.ceil((1 - tokens) / self.rate) if self.rate > 0 else 60.0


class SessionStore(Generic[S]):
    """Sessions keyed by id, each with an owner. Bounded by idle expiry, a
    per-owner cap (evicts that owner's least recently used session) and a
    global cap (evicts the least recently used session overall)."""

    def __init__(self, idle_seconds: float, per_owner: int, total: int, clock: Callable[[], float] = time.monotonic):
        self.idle_seconds, self.per_owner, self.total, self.clock = idle_seconds, per_owner, total, clock
        self._items: OrderedDict[str, tuple[str, S, float]] = OrderedDict()  # id -> (owner, session, last used); LRU order
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "SessionStore":
        return cls(_env_number("MOE_SESSION_IDLE_SECONDS", 1800), int(_env_number("MOE_MAX_SESSIONS_PER_APPLICANT", 5)),
                   int(_env_number("MOE_MAX_SESSIONS", 10000)))

    def _expire(self, now: float) -> None:
        stale = [sid for sid, (_, _, used) in self._items.items() if now - used > self.idle_seconds]
        for sid in stale:
            del self._items[sid]

    def add(self, session_id: str, owner: str, session: S) -> None:
        now = self.clock()
        with self._lock:
            self._expire(now)
            owned = [sid for sid, (o, _, _) in self._items.items() if o == owner]  # LRU first
            for sid in owned[: max(0, len(owned) - self.per_owner + 1)]:
                del self._items[sid]
            while len(self._items) >= self.total:
                self._items.popitem(last=False)
            self._items[session_id] = (owner, session, now)

    def get(self, session_id: str, owner: str) -> S | None:
        """The session if it exists, hasn't expired and belongs to `owner`."""
        now = self.clock()
        with self._lock:
            item = self._items.get(session_id)
            if item is None or now - item[2] > self.idle_seconds:
                self._items.pop(session_id, None)
                return None
            if item[0] != owner:
                return None
            self._items[session_id] = (item[0], item[1], now)
            self._items.move_to_end(session_id)
            return item[1]

    def remove(self, session_id: str, owner: str) -> bool:
        with self._lock:
            item = self._items.get(session_id)
            if item is None or item[0] != owner:
                return False
            del self._items[session_id]
            return True

    def __len__(self) -> int:
        return len(self._items)
