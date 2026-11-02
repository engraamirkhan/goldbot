"""The dashboard's read-through view cache (`GatesCache`), first built for the Progress tab's gate view (ROADMAP W2-3).

The Progress tab and its routes (`/api/gates`, `/api/attribution`, `/api/trades/summary`) were retired on 2026-11-02
(owner: remove the research and feeds screens); the cache stays because every read-only Home and trades view in
api/app.py serves its polls through one.

Design rule this module keeps: one build serves every poll for its TTL ("cached for 60 s", docs/ux/progress-screens.md
Data), so a phone polling every minute and a refocused tab never re-read the whole record twice. Nothing here writes.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any, TypeVar

__all__ = ["GATES_TTL_S", "GatesCache"]

GATES_TTL_S = 60.0                 # "cached for 60 s" (spec Data)

T = TypeVar("T")


class GatesCache:
    """One build served for `ttl_s` seconds (the design's 60 s by default, as Health's own cadence). The build runs
    outside the lock: while one rebuild runs, other callers get the previous value at once instead of queueing behind
    it (only callers before the first value exists build alongside it). A failed build leaves the previous value in
    place."""

    def __init__(self, ttl_s: float = GATES_TTL_S, clock: Callable[[], float] = time.monotonic):
        self._ttl, self._clock = ttl_s, clock
        self._lock = threading.Lock()
        self._at: float | None = None
        self._value: Any = None
        self._building = False

    def get(self, build: Callable[[], T]) -> T:
        with self._lock:
            fresh = self._at is not None and self._clock() - self._at < self._ttl
            if fresh or (self._building and self._at is not None):
                value: T = self._value
                return value
            self._building = True
        try:
            new = build()
            with self._lock:
                self._value, self._at = new, self._clock()
            return new
        finally:
            with self._lock:
                self._building = False
