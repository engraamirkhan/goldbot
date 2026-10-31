"""Trading calendar for gold CFDs.

Session state is derived from the broker's session table (server-time daily break and the
weekend gap), never from silence in the feed. Defaults match IC Markets / Vantage XAUUSD:
open Monday 01:02 server time, daily break 23:59-01:02, close Friday 23:57.

Runtime override (TRACEABILITY D13): the broker's `trading_sessions(symbol)` lists its sessions for SESSION_DAYS
server-calendar days, each resolved to UTC for its own date (DST per date); `SessionTable.from_broker` turns that into
the table the engine uses, and `BrokerSessions` refreshes it once per server day. Inside the reported days only the
reported sessions are open, so a shortened session (early close) or a missing day (holiday) blocks entries. An
unreadable or implausible list (error, empty, malformed, no closed time in a week) falls back to the static defaults
with a logged reason, never to "always open". The MetaTrader5 Python package exposes no session schedule
(`symbol_info().session_deals` is the number of deals in the current session, not its hours), so the MT5 adapter
reports the broker's documented hours, i.e. these defaults (goldbot/execution/mt5_adapter.py).
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from pydantic import Field, model_validator

from goldbot.base import FrozenRecord, load_record
from goldbot.data.cme_holidays import cme_metals_closed
from goldbot.data.timeutil import epoch_ns, server_to_utc, server_wall

log = logging.getLogger("goldbot.calendar")

SESSION_DAYS = 7                      # a broker's session list covers this many server days from its first date
DAY_S = 86400
ServerSession = tuple[int, int, int]  # (server weekday 0 = Monday, start, end) in s from server midnight; end <= 86400


class TradingSession(FrozenRecord):
    """One trading session the broker reports for one server-calendar day, resolved to UTC for that date. A weekday
    and a UTC time alone are ambiguous across a DST change, so the server date the times were resolved for travels
    with them. The UTC end may be earlier in the day than the start (the session crosses UTC midnight)."""
    weekday: int = Field(ge=0, le=6)  # server weekday, 0 = Monday
    start_utc: time
    end_utc: time
    server_date: date

    def interval(self, server_tz: str) -> tuple[pd.Timestamp, pd.Timestamp]:
        """[start, end) as UTC instants: the start is the first instant at or after the server date's midnight with
        that UTC time of day, the end the first one after the start."""
        start = _at_or_after(self.start_utc, server_midnight_utc(self.server_date, server_tz), strict=False)
        return start, _at_or_after(self.end_utc, start, strict=True)


def server_midnight_utc(d: date, server_tz: str) -> pd.Timestamp:
    return pd.Timestamp(server_to_utc(pd.DatetimeIndex([pd.Timestamp(d)]), server_tz)[0])


def _at_or_after(t: time, ref: pd.Timestamp, *, strict: bool) -> pd.Timestamp:
    c = pd.Timestamp(datetime.combine(ref.date(), t), tz="UTC")
    while c < ref or (strict and c == ref):
        c += pd.Timedelta(days=1)
    return c


def resolve_server_sessions(template: Sequence[ServerSession], server_tz: str, first_date: date,
                            days: int = SESSION_DAYS) -> list[TradingSession]:
    """A weekly server-time session template (the shape of MT5's SymbolInfoSessionTrade) as TradingSessions for
    `days` server dates from `first_date`, each converted with `server_to_utc` for its own date. A session whose start
    or end the zone cannot resolve (a DST overlap) is dropped, i.e. closed, with a warning."""
    out: list[TradingSession] = []
    for k in range(days):
        d = first_date + timedelta(days=k)
        midnight = datetime.combine(d, time())
        for wd, a, b in template:
            if wd != d.weekday():
                continue
            if not 0 <= a < b <= DAY_S:
                raise ValueError(f"session {wd}: {a}..{b} s is not inside one server day")
            s, e = server_to_utc(pd.DatetimeIndex([midnight + timedelta(seconds=a), midnight + timedelta(seconds=b)]),
                                 server_tz)
            if pd.isna(s) or pd.isna(e):
                log.warning("session %s %s..%s s on %s cannot be resolved in %s: treated as closed", wd, a, b, d,
                            server_tz)
                continue
            out.append(TradingSession(weekday=wd, start_utc=s.time(), end_utc=e.time(), server_date=d))
    return out


DAY_NS = DAY_S * 1_000_000_000
_OFFSETS = (-2, -1, 0, 1)     # trading dates around a stamp's UTC date that can hold its window or its latest open


class SessionWindow(FrozenRecord):
    """A trading session on local exchange clocks: from `open` on `open_tz`'s wall clock to `close` on `close_tz`'s,
    both on the same local calendar date (the session's trading date). Design rule (playbook D-1): session
    boundaries are defined in local exchange time and converted per date, so London follows Europe/London and New
    York follows America/New_York (they switch on different dates in March and October/November) while Tokyo, which
    has no DST, stays fixed in UTC. The conversion uses only the date and the zone's rules, never other bars."""
    open_tz: str
    open: time
    close_tz: str
    close: time

    def bounds_ns(self, days: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(open, close) in UTC epoch ns for each trading date given as days since 1970-01-01."""
        o, c = _local_ns(days, self.open_tz, self.open), _local_ns(days, self.close_tz, self.close)
        if len(days) and not ((c > o) & (c - o < DAY_NS)).all():
            raise ValueError(f"session window {self.open_tz} {self.open} -> {self.close_tz} {self.close} is not a "
                             "positive span shorter than a day on every date")
        return o, c

    def covers(self, utc_ts: pd.DatetimeIndex, *, include_close: bool = False) -> np.ndarray:
        """True where a stamp lies in an instance of this window, [open, close) or [open, close] with
        `include_close`, each instance resolved on its own trading date from the zones' rules alone (playbook D-1),
        so the window's UTC hours move with each city's own DST switch and a stamp depends on nothing but itself."""
        ns = epoch_ns(pd.DatetimeIndex(utc_ts))
        out = np.zeros(len(ns), dtype=bool)
        if not len(ns):
            return out
        day, first, days = _day_grid(ns)
        o, c = self.bounds_ns(days)
        for off in _OFFSETS:
            j = day + off - first
            out |= (ns >= o[j]) & ((ns <= c[j]) if include_close else (ns < c[j]))
        return out


def _local_ns(days: np.ndarray, tz: str, t: time) -> np.ndarray:
    """UTC epoch ns of wall-clock `t` in `tz` on each local date (days since the epoch). A wall time the zone skips
    moves forward and a repeated one takes standard time; the default session times are never in a switch hour."""
    tod = ((t.hour * 60 + t.minute) * 60 + t.second) * 1_000_000_000
    naive = pd.DatetimeIndex(pd.to_datetime(days.astype(np.int64) * DAY_NS + tod, unit="ns"))
    local = naive.tz_localize(ZoneInfo(tz), ambiguous=np.zeros(len(naive), dtype=bool), nonexistent="shift_forward")
    return epoch_ns(local)


def _day_grid(ns: np.ndarray) -> tuple[np.ndarray, int, np.ndarray]:
    """(UTC day number per stamp, first day of a grid covering every stamp's day plus `_OFFSETS`, that grid)."""
    day = ns // DAY_NS
    first = int(day.min()) + min(_OFFSETS)
    return day, first, np.arange(first, int(day.max()) + max(_OFFSETS) + 1, dtype=np.int64)


DEFAULT_SESSION_WINDOWS: dict[str, SessionWindow] = {
    # Asia: Tokyo 08:00 (23:00 UTC all year) to the London open; London: LSE open 08:00 to the 08:30 New York data
    # slot; New York: 08:30 to the 17:00 New York COMEX daily close. On summer time (BST, EDT) this is the old fixed UTC
    # table (07:00, 12:30, 21:00); on its own zone's winter time each boundary is an hour later in UTC.
    "asia": SessionWindow(open_tz="Asia/Tokyo", open=time(8, 0), close_tz="Europe/London", close=time(8, 0)),
    "london": SessionWindow(open_tz="Europe/London", open=time(8, 0), close_tz="America/New_York", close=time(8, 30)),
    "newyork": SessionWindow(open_tz="America/New_York", open=time(8, 30), close_tz="America/New_York",
                             close=time(17, 0)),
}


class PauseWindow(FrozenRecord):
    """A broker's own pause on an always-open symbol (ADR 0062), in UTC: [start, end) on `weekday` (0 = Monday), or on
    every day when None. Default none: Vantage documents no pause for XAUUSD247; one it announces goes here."""
    weekday: int | None = Field(None, ge=0, le=6)
    start: time
    end: time

    @model_validator(mode="after")
    def _ordered(self) -> PauseWindow:
        if not self.start < self.end:
            raise ValueError("a pause window ends after it starts, inside one UTC day")
        return self


class WeekendWindow(FrozenRecord):
    """ADR 0062: a weekend_only account's entry window in UTC, from `open_day open` to `flat_day flat` (default Friday
    21:00 to Sunday 21:00: no entries Monday 00:00 .. Friday 21:00, and flat before the Monday reconvergence with the
    main session). At `flat` every position of the account is closed, winners too; no hold may reach it."""
    open_day: int = Field(4, ge=0, le=6)
    open: time = time(21, 0)
    flat_day: int = Field(6, ge=0, le=6)
    flat: time = time(21, 0)

    @model_validator(mode="after")
    def _not_empty(self) -> WeekendWindow:
        if self._at(self.open_day, self.open) == self._at(self.flat_day, self.flat):
            raise ValueError("the weekend window opens and goes flat at the same time")
        return self

    @staticmethod
    def _at(day: int, t: time) -> int:
        return day * 1440 + t.hour * 60 + t.minute

    @staticmethod
    def _week_min(ts: pd.Timestamp) -> float:
        u = pd.Timestamp(ts).tz_convert("UTC")
        return u.weekday() * 1440 + u.hour * 60 + u.minute + u.second / 60

    def inside(self, ts: pd.Timestamp) -> bool:
        """True while entries are allowed: [open, flat) of the week, wrapping over Monday 00:00 when it must."""
        m, a, b = self._week_min(ts), self._at(self.open_day, self.open), self._at(self.flat_day, self.flat)
        return bool(a <= m < b) if a < b else bool(m >= a or m < b)

    def _cut_of_week(self, ts: pd.Timestamp) -> pd.Timestamp:
        u = pd.Timestamp(ts).tz_convert("UTC")
        monday = u.normalize() - pd.Timedelta(days=u.weekday())
        return monday + pd.Timedelta(minutes=self._at(self.flat_day, self.flat))

    def next_cut(self, ts: pd.Timestamp) -> pd.Timestamp:
        """The first flat instant strictly after `ts`."""
        cut = self._cut_of_week(ts)
        return cut if cut > ts else cut + pd.Timedelta(days=7)

    def last_cut(self, ts: pd.Timestamp) -> pd.Timestamp:
        """The latest flat instant at or before `ts`."""
        cut = self._cut_of_week(ts)
        return cut if cut <= ts else cut - pd.Timedelta(days=7)


class _SessionSource(Protocol):
    def trading_sessions(self, symbol: str) -> list[TradingSession]: ...


class SessionTable(FrozenRecord):
    server_tz: str = "Europe/Athens"
    daily_break_start: time = time(23, 59)
    daily_break_end: time = time(1, 2)
    week_open_day: int = 0          # Monday (server time), after the break ends
    week_close_day: int = 4         # Friday
    week_close_time: time = time(23, 57)
    # Trading sessions on local exchange clocks, used for features, session labels and the cost tables by session
    # (not for open/closed state). Each runs until the next one opens; London and New York follow their own DST.
    sessions_local: dict[str, SessionWindow] = Field(default_factory=lambda: dict(DEFAULT_SESSION_WINDOWS))
    # Runtime override from the broker (D13): sorted [start, end) UTC windows; inside [covers_from, covers_until)
    # only these are open, outside it the static rule above applies. None = static table only.
    broker_windows: tuple[tuple[pd.Timestamp, pd.Timestamp], ...] | None = None
    covers_from: pd.Timestamp | None = None
    covers_until: pd.Timestamp | None = None
    source: str = "static"            # static | broker | static (fallback) | always_open
    note: str = ""                    # why the fallback was taken
    # ADR 0062: a 24/7 symbol (Vantage XAUUSD247). Open every minute but the broker's own `pauses`: no daily break, no
    # weekend, no CME holiday, and the broker's session list is never read (the MT5 adapter reports the weekday gold
    # hours, which would close it). Off on every other account: their table is unchanged
    always_open: bool = False
    pauses: tuple[PauseWindow, ...] = ()

    def server_sessions(self) -> list[ServerSession]:
        """The static rule as a weekly server-time session template (the broker's documented hours): from the end of
        the daily break to its start, Monday to Thursday, and until the week close on Friday."""
        def sec(t: time) -> int:
            return t.hour * 3600 + t.minute * 60 + t.second
        return [(d, sec(self.daily_break_end),
                 sec(self.week_close_time if d == self.week_close_day else self.daily_break_start))
                for d in range(self.week_open_day, self.week_close_day + 1)]

    @classmethod
    def from_broker(cls, broker: _SessionSource, symbol: str, now: pd.Timestamp,
                    fallback: SessionTable | None = None) -> SessionTable:
        """The runtime table from the broker's session list (D13). An error, an empty or malformed list, or one with
        no closed time in a whole week falls back to `fallback` (the static defaults) with the reason logged and kept
        in `note`: the market is never assumed always open."""
        base = fallback if fallback is not None else DEFAULT_SESSIONS
        if base.always_open:
            return base.model_copy(update={"broker_windows": None, "covers_from": None, "covers_until": None,
                                           "source": "always_open", "note": ""})
        try:
            reported: Any = broker.trading_sessions(symbol)
            why = _implausible(reported, base.server_tz)
        except Exception as exc:                       # a terminal fault is a reason to fall back, not to crash
            why = f"trading_sessions failed: {type(exc).__name__}: {exc}"
        if why:
            log.warning("%s sessions at %s from the static table: %s", symbol, now, why)
            return base.model_copy(update={"broker_windows": None, "covers_from": None, "covers_until": None,
                                           "source": "static (fallback)", "note": why})
        first = min(s.server_date for s in reported)
        return base.model_copy(update={
            "broker_windows": tuple(sorted(s.interval(base.server_tz) for s in reported)),
            "covers_from": server_midnight_utc(first, base.server_tz),
            "covers_until": server_midnight_utc(first + timedelta(days=SESSION_DAYS), base.server_tz),
            "source": "broker", "note": ""})

    def entry_block(self, now: pd.Timestamp) -> str | None:
        """Why no entry may open at `now` (the market is closed by this table), or None when it is open."""
        if self.is_open(pd.DatetimeIndex([now]))[0]:
            return None
        return f"market closed ({self.source} session table)"

    def is_open(self, utc_ts: pd.DatetimeIndex) -> np.ndarray:
        static = self._static_open(utc_ts)
        if self.broker_windows is None or self.covers_from is None or self.covers_until is None:
            return static
        ns = epoch_ns(pd.DatetimeIndex(utc_ts).tz_convert("UTC"))
        covered = (ns >= self.covers_from.value) & (ns < self.covers_until.value)
        starts = np.array([a.value for a, _ in self.broker_windows], dtype=np.int64)
        ends = np.array([b.value for _, b in self.broker_windows], dtype=np.int64)
        if not len(starts):
            return np.where(covered, False, static)
        k = np.searchsorted(starts, ns, side="right") - 1       # last window starting at or before each stamp
        inside = (k >= 0) & (ns < ends[np.clip(k, 0, None)])
        return np.where(covered, inside, static)

    def expected_open(self, utc_ts: pd.DatetimeIndex) -> np.ndarray:
        """Minutes a complete feed has bars for: `is_open` less the CME metals holiday closures (goldbot/data/
        cme_holidays.py). For judging missing data only (the engine's history gap guard); `is_open` itself, which
        filters release, resampled and research bars, does not change."""
        if self.always_open:
            return np.asarray(self.is_open(utc_ts), dtype=bool)
        return np.asarray(self.is_open(utc_ts), dtype=bool) & ~cme_metals_closed(pd.DatetimeIndex(utc_ts))

    def _paused(self, utc_ts: pd.DatetimeIndex) -> np.ndarray:
        """Inside one of the broker's own pauses (UTC), for an always-open table."""
        u = pd.DatetimeIndex(utc_ts).tz_convert("UTC")
        out = np.zeros(len(u), dtype=bool)
        if not self.pauses or not len(u):
            return out
        tsec = (u.hour * 3600 + u.minute * 60 + u.second).to_numpy()
        dow = u.dayofweek.to_numpy()
        for p in self.pauses:
            a, b = p.start.hour * 3600 + p.start.minute * 60, p.end.hour * 3600 + p.end.minute * 60
            out |= (tsec >= a) & (tsec < b) & ((dow == p.weekday) if p.weekday is not None else True)
        return out

    def _static_open(self, utc_ts: pd.DatetimeIndex) -> np.ndarray:
        if self.always_open:
            return ~self._paused(utc_ts)
        local = server_wall(pd.DatetimeIndex(utc_ts), self.server_tz)
        dow = local.dayofweek.to_numpy()
        t = local.time
        tsec = np.array([x.hour * 3600 + x.minute * 60 + x.second for x in t])
        bs = self.daily_break_start.hour * 3600 + self.daily_break_start.minute * 60
        be = self.daily_break_end.hour * 3600 + self.daily_break_end.minute * 60
        in_break = (tsec >= bs) | (tsec < be)
        weekend = (dow == 5) | (dow == 6)
        fri_closed = (dow == self.week_close_day) & (
            tsec >= self.week_close_time.hour * 3600 + self.week_close_time.minute * 60
        )
        return ~(in_break | weekend | fri_closed)

    def session_windows(self, utc_ts: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray]:
        """(index into `sessions_local` of the session holding each stamp, -1 outside every window; the UTC start of
        that session instance in epoch ns, -1 outside). Windows are [open, close), resolved per trading date from the
        zones' rules alone, so a stamp's session depends only on its own date, never on other bars."""
        ns = epoch_ns(pd.DatetimeIndex(utc_ts))
        code = np.full(len(ns), -1, dtype=np.int64)
        start = np.full(len(ns), -1, dtype=np.int64)
        if not len(ns):
            return code, start
        day, first, days = _day_grid(ns)
        for k, w in enumerate(self.sessions_local.values()):
            o, c = w.bounds_ns(days)
            for off in _OFFSETS:
                j = day + off - first
                inside = (ns >= o[j]) & (ns < c[j])
                code[inside], start[inside] = k, o[j][inside]
        return code, start

    def last_open_ns(self, utc_ts: pd.DatetimeIndex, name: str) -> np.ndarray:
        """UTC epoch ns of the latest open of session `name` at or before each stamp. An open exists on every calendar
        date, weekends included, so consecutive opens are 23, 24 or 25 h apart around a DST switch."""
        ns = epoch_ns(pd.DatetimeIndex(utc_ts))
        if not len(ns):
            return np.zeros(0, dtype=np.int64)
        day, first, days = _day_grid(ns)
        o, _ = self.sessions_local[name].bounds_ns(days)
        best = np.full(len(ns), np.iinfo(np.int64).min, dtype=np.int64)
        for off in _OFFSETS:
            cand = o[day + off - first]
            best = np.where(cand <= ns, np.maximum(best, cand), best)
        return best

    def session_label(self, utc_ts: pd.DatetimeIndex) -> np.ndarray:
        """Session name per stamp. Between the New York close and the Asia open (the thin hours around the daily
        break) a stamp is labelled "asia", the session whose liquidity and costs it shares."""
        code, _ = self.session_windows(utc_ts)
        names = np.array(list(self.sessions_local), dtype=object)
        out = np.full(len(code), "asia", dtype=object)
        out[code >= 0] = names[code[code >= 0]]
        return out


DEFAULT_SESSIONS = SessionTable()


def _implausible(reported: Any, server_tz: str) -> str | None:
    """Why a broker's session list cannot be used, or None."""
    if not isinstance(reported, list) or not reported:
        return "the broker reported no sessions"
    if not all(isinstance(s, TradingSession) for s in reported):
        return "the broker's session list is malformed"
    first = min(s.server_date for s in reported)
    if any((s.server_date - first).days >= SESSION_DAYS for s in reported):
        return f"the broker's session list spans more than {SESSION_DAYS} days"
    longest = pd.Timedelta(0)
    start, end = sorted(s.interval(server_tz) for s in reported)[0]
    for a, b in sorted(s.interval(server_tz) for s in reported)[1:]:     # merge touching or overlapping windows
        if a > end:
            longest, start = max(longest, end - start), a
        end = max(end, b)
    if max(longest, end - start) >= pd.Timedelta(days=SESSION_DAYS - 1):     # gold always closes for the weekend
        return "the broker's session list has no closed time in a whole week"
    return None


class BrokerSessions:
    """The engine's session table (D13): read from the broker once per server day, the static table whenever the read
    fails (re-tried after `retry_after`). `entry_block(now)` is the engine hook: a reason to block entries, or None."""

    def __init__(self, broker: _SessionSource, symbol: str, *, fallback: SessionTable | None = None,
                 retry_after: pd.Timedelta = pd.Timedelta(minutes=30)) -> None:
        self.broker, self.symbol, self.retry_after = broker, symbol, retry_after
        self.fallback = fallback if fallback is not None else DEFAULT_SESSIONS
        self._table: SessionTable | None = None
        self._day: date | None = None
        self._read_at: pd.Timestamp | None = None

    def table(self, now: pd.Timestamp) -> SessionTable:
        day = server_wall(pd.Timestamp(now), self.fallback.server_tz)[0].date()
        retry = (self._table is not None and self._table.source != "broker" and self._read_at is not None
                 and now - self._read_at >= self.retry_after)
        if self._table is None or day != self._day or retry:
            self._table = SessionTable.from_broker(self.broker, self.symbol, now, self.fallback)
            self._day, self._read_at = day, now
        return self._table

    def entry_block(self, now: pd.Timestamp) -> str | None:
        return self.table(now).entry_block(now)


def session_table_cache(t: SessionTable) -> dict[str, Any]:
    """The parts of a session table another process needs (engine state -> health): zone, source and the broker's
    windows as ISO strings; the static rule's fields stay at their defaults."""
    def iso(x: pd.Timestamp | None) -> str | None:
        return None if x is None else pd.Timestamp(x).isoformat()
    # ADR 0062: the two keys only on an always-open table, so every other account's state file is unchanged
    lane = {"always_open": True, "pauses": [p.model_dump(mode="json") for p in t.pauses]} if t.always_open else {}
    return {"server_tz": t.server_tz, "source": t.source, "note": t.note, **lane,
            "covers_from": iso(t.covers_from), "covers_until": iso(t.covers_until),
            "broker_windows": None if t.broker_windows is None else [[iso(a), iso(b)] for a, b in t.broker_windows]}


def session_table_from_cache(d: Any) -> SessionTable | None:
    """Inverse of `session_table_cache`; None when `d` is missing or malformed (the caller uses the static table)."""
    try:
        win = d.get("broker_windows")
        base = SessionTable(server_tz=str(d["server_tz"]), always_open=bool(d.get("always_open", False)),
                            pauses=tuple(load_record(PauseWindow, p, source="engine state sessions.pauses")
                                         for p in d.get("pauses") or ()))
        if base.always_open:
            return base.model_copy(update={"source": "always_open"})
        if win is None:
            return base
        return base.model_copy(update={
            "broker_windows": tuple((pd.Timestamp(a), pd.Timestamp(b)) for a, b in win),
            "covers_from": pd.Timestamp(d["covers_from"]), "covers_until": pd.Timestamp(d["covers_until"]),
            "source": str(d.get("source", "broker")), "note": str(d.get("note", ""))})
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


class LocalSession(FrozenRecord):
    """A trading session on a local wall clock (DST follows the zone): New York 08:30-16:00 America/New_York is
    13:30-21:00 UTC in winter and 12:30-20:00 UTC in summer. Used by rules that act at a fixed local time."""
    name: str
    tz: str
    open: time
    close: time

    @property
    def length_minutes(self) -> int:
        return (self.close.hour * 60 + self.close.minute) - (self.open.hour * 60 + self.open.minute)

    def clock(self, utc_ts: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray]:
        """(minutes from the session open to each bar's start, NaN outside [open, close) and on weekends; the local
        date as yyyymmdd) for bars starting at `utc_ts`."""
        local = pd.DatetimeIndex(utc_ts).tz_convert(ZoneInfo(self.tz))
        since = self._from_open(local)
        inside = (since >= 0) & (since < self.length_minutes) & np.asarray(local.dayofweek < 5)
        day = np.asarray(local.year * 10000 + local.month * 100 + local.day, dtype=np.int64)
        return np.where(inside, since, np.nan), day

    def _from_open(self, local: pd.DatetimeIndex) -> np.ndarray:
        return np.asarray(local.hour * 60 + local.minute, dtype=float) - (self.open.hour * 60 + self.open.minute)

    def minutes_from_open(self, utc_ts: pd.DatetimeIndex) -> np.ndarray:
        """Local minutes of the day minus the open's (negative before today's open), defined on every bar."""
        return self._from_open(pd.DatetimeIndex(utc_ts).tz_convert(ZoneInfo(self.tz)))

    def last_open_price(self, bars: pd.DataFrame) -> np.ndarray:
        """The open of the most recent bar that started exactly at a session open (today's after the open, the previous
        trading day's before it), carried forward; NaN before the first one. Uses only bars up to the current one."""
        since, _ = self.clock(pd.DatetimeIndex(pd.to_datetime(bars["ts_utc"], utc=True)))
        at_open = np.where(since == 0, bars["open"].to_numpy(dtype=float), np.nan)
        return pd.Series(at_open).ffill().to_numpy()

    def open_price(self, bars: pd.DataFrame) -> np.ndarray:
        """On every in-session bar: the open of the bar that started exactly at that day's session open (NaN outside
        the session and on days whose opening bar is missing). Uses only bars up to the current one."""
        since, day = self.clock(pd.DatetimeIndex(pd.to_datetime(bars["ts_utc"], utc=True)))
        inside = ~np.isnan(since)
        out = np.full(len(bars), np.nan)
        if not inside.any():
            return out
        frame = pd.DataFrame({"day": day[inside], "since": since[inside],
                              "open": bars["open"].to_numpy(dtype=float)[inside]})
        first = frame.groupby("day", sort=False)[["since", "open"]].transform("first")
        ok = first["since"].to_numpy() == 0
        out[np.flatnonzero(inside)[ok]] = first["open"].to_numpy()[ok]
        return out


LOCAL_SESSIONS: dict[str, LocalSession] = {
    # London: LSE hours (both London gold auctions fall inside); New York: from the 08:30 US data releases to the
    # equity close
    "london": LocalSession(name="london", tz="Europe/London", open=time(8, 0), close=time(16, 30)),
    "newyork": LocalSession(name="newyork", tz="America/New_York", open=time(8, 30), close=time(16, 0)),
}
