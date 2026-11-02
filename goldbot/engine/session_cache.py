"""Non-gold books' broker session tables: cached for 24 h and read one at a time (incident 2026-11-02).

docs/incidents/2026-11-02-multi-instrument.md: the icm-probe engine read `trading_sessions` for 19 books one after
another before its loop started. Through the bridge each is a multi-day M1 history read (mt5_adapter
`trading_sessions`, OBSERVE_DAYS) that holds the terminal's one worker for seconds on a CPU-starved box; the engine
gave up after 5 s per book but the bridge kept working, so 34 s of start-up and every other call queued behind it.

The rules:

* `CachedSessions` is a book's session source (`SessionRefresher` reads it on its own thread). A table read from the
  broker is saved to state/instruments.db (`instrument_sessions`) and reused for SESSION_CACHE_TTL while it still
  covers the time asked, by this process and by a restart. Only a "broker" table is cached; a failed read gives the
  static profile (`SessionTable.from_broker`'s fallback) and is tried again after RETRY_AFTER, never in a loop.
* `cached(now)` reads the file only, never the broker: the engine applies it at start, so a book restarted within
  24 h opens on its real hours and one never read opens on its static profile (books.py sets it at construction).
* `SessionStagger` starts the books' reads after the loop runs: at most one in flight, the next on a later pass, and
  only a book that is `due` (no fresh table, not backing off).
* The cache is a convenience, not state: an unreadable or unwritable file is logged and costs one read per book.
  Entries on a book still answer to its session profile (`books.set_sessions`, E3); exits never read this.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

import pandas as pd

from goldbot.data.calendar import SessionTable, session_table_cache, session_table_from_cache
from goldbot.engine.instruments_db import InstrumentsDb, InstrumentsDbError

log = logging.getLogger("goldbot.engine.sessions")

SESSION_CACHE_TTL = pd.Timedelta(hours=24)
RETRY_AFTER = pd.Timedelta(minutes=30)
# Trading-safety review of #319, F4: only a bridge from protocol v11 on reads hours from complete days (PR #317); a
# table an older bridge built (today's unfinished day as a whole one) is used for the moment but never cached, and a
# cached row without this marker, or below it, is ignored
SESSIONS_MIN_PROTOCOL = 11
PROTOCOL_KEY = "bridge_protocol"
# F6: a read still running after this many read timeouts no longer holds the stagger (a hung call never returns)
STUCK_TIMEOUTS = 2.0


class CachedSessions:
    """One non-gold book's session source: the broker's table, read at most once per SESSION_CACHE_TTL."""

    def __init__(self, broker: Any, symbol: str, *, account_id: str, db: InstrumentsDb, fallback: SessionTable,
                 protocol: Callable[[], int | None] = lambda: None, ttl: pd.Timedelta = SESSION_CACHE_TTL,
                 retry_after: pd.Timedelta = RETRY_AFTER) -> None:
        """`protocol`: the bridge protocol the read goes through (None: unknown), asked at each save."""
        self.broker, self.symbol, self.account_id, self.db = broker, symbol, account_id, db
        self.protocol = protocol
        self.fallback, self.ttl, self.retry_after = fallback, ttl, retry_after
        self._table: SessionTable | None = None
        self._read_at: pd.Timestamp | None = None
        self._failed: tuple[pd.Timestamp, SessionTable] | None = None

    def _fresh(self, table: SessionTable | None, read_at: pd.Timestamp | None, now: pd.Timestamp) -> bool:
        return (table is not None and read_at is not None and table.source == "broker"
                and table.covers_from is not None and table.covers_until is not None
                and now - read_at < self.ttl and table.covers_from <= now < table.covers_until)

    def cached(self, now: pd.Timestamp) -> SessionTable | None:
        """A broker table read within the TTL that still covers `now` (memory, else the file), or None. No broker
        call; a cache that cannot be read is None."""
        if self._fresh(self._table, self._read_at, now):
            return self._table
        try:
            row = self.db.load_sessions(self.account_id, self.symbol)
        except InstrumentsDbError as exc:
            log.warning("%s: session cache unreadable (%s); reading from the broker", self.symbol, exc)
            return None
        if row is None:
            return None
        read_at, body = row
        marker = body.get(PROTOCOL_KEY)
        if not isinstance(marker, int) or isinstance(marker, bool) or marker < SESSIONS_MIN_PROTOCOL:
            return None                                   # F4: written by an older bridge or release
        t = session_table_from_cache(body)
        # a cache written under another profile or zone (the instrument was re-profiled) is not this book's
        if t is None or t.profile != self.fallback.profile or t.server_tz != self.fallback.server_tz \
                or not self._fresh(t, read_at, now):
            return None
        self._table, self._read_at = t, read_at
        return t

    def due(self, now: pd.Timestamp) -> bool:
        """Whether a read should start: no fresh table in memory and not backing off after a failure. No I/O."""
        if self._fresh(self._table, self._read_at, now):
            return False
        return self._failed is None or now - self._failed[0] >= self.retry_after

    def table(self, now: pd.Timestamp) -> SessionTable:
        """The table to use at `now` (on the refresher's thread): the cache when fresh, else one broker read."""
        if (t := self.cached(now)) is not None:
            return t
        if self._failed is not None and now - self._failed[0] < self.retry_after:
            return self._failed[1]
        t = SessionTable.from_broker(self.broker, self.symbol, now, self.fallback)
        if t.source != "broker":
            self._failed = (now, t)
            return t
        self._failed = None
        try:
            proto = self.protocol()
        except Exception:                                  # noqa: BLE001 - unknown is "do not cache"
            proto = None
        if proto is None or proto < SESSIONS_MIN_PROTOCOL:
            log.info("%s: session table from bridge protocol %s used, not cached (needs v%d)", self.symbol, proto,
                     SESSIONS_MIN_PROTOCOL)
            return t                                       # F4: kept in no cache, read again next time it is due
        self._table, self._read_at = t, now
        try:
            self.db.save_sessions(self.account_id, self.symbol, now, {**session_table_cache(t), PROTOCOL_KEY: proto})
        except InstrumentsDbError as exc:
            log.warning("%s: session table not cached (%s); it is read again after a restart", self.symbol, exc)
        return t


class SessionStagger:
    """The books' session reads after the loop starts, one at a time: `step(now)` on every pass starts the next due
    book's read when none is running and returns that book (None when nothing started). `items`: (book, refresher)
    in book order; each refresher's `source` is the book's CachedSessions."""

    def __init__(self, items: list[tuple[Any, Any]], *, read_timeout_s: float = 10.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.items = items
        self._next = 0
        self.stuck_after_s = STUCK_TIMEOUTS * read_timeout_s
        self.clock = clock
        self._started: dict[int, float] = {}
        self._stuck: set[int] = set()

    def _in_flight(self) -> bool:
        """A read is running and not yet past `stuck_after_s` (F6: a hung one is logged once and no longer counts)."""
        busy = False
        for k, (book, r) in enumerate(self.items):
            if not r.running():
                self._started.pop(k, None)
                self._stuck.discard(k)
                continue
            ran = self.clock() - self._started.get(k, self.clock())
            if ran <= self.stuck_after_s:
                busy = True
            elif k not in self._stuck:
                self._stuck.add(k)
                log.warning("%s: session read still running after %.0f s (over %.0f s); the next books' reads go "
                            "ahead, this one keeps its table", book, ran, self.stuck_after_s)
        return busy

    def step(self, now: pd.Timestamp) -> Any | None:
        if not self.items or self._in_flight():
            return None
        n = len(self.items)
        for k in range(n):
            book, r = self.items[(self._next + k) % n]
            due = getattr(r.source, "due", None)
            if callable(due) and not due(now):
                continue
            if r.start(now):
                self._started[(self._next + k) % n] = self.clock()
                self._next = (self._next + k + 1) % n
                return book
        return None
