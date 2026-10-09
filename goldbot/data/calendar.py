"""Trading calendar for gold CFDs.

Session state is derived from the broker's session table (server-time daily break and the
weekend gap), never from silence in the feed. Defaults match IC Markets / Vantage XAUUSD:
open Monday 01:02 server time, daily break 23:59-01:02, close Friday 23:57.
At runtime the live engine overwrites these from `symbol_info(...).session_deals`.
"""
from __future__ import annotations

from datetime import time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord


class SessionTable(FrozenRecord):
    server_tz: str = "Europe/Athens"
    daily_break_start: time = time(23, 59)
    daily_break_end: time = time(1, 2)
    week_open_day: int = 0          # Monday (server time), after the break ends
    week_close_day: int = 4         # Friday
    week_close_time: time = time(23, 57)
    # Trading sessions in UTC, used only for features (not for open/closed state)
    sessions_utc: dict[str, tuple[time, time]] = Field(
        default_factory=lambda: {
            "asia": (time(23, 0), time(7, 0)),
            "london": (time(7, 0), time(12, 30)),
            "newyork": (time(12, 30), time(21, 0)),
        }
    )

    def is_open(self, utc_ts: pd.DatetimeIndex) -> np.ndarray:
        local = pd.DatetimeIndex(utc_ts).tz_convert(ZoneInfo(self.server_tz))
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

    def session_label(self, utc_ts: pd.DatetimeIndex) -> np.ndarray:
        idx = pd.DatetimeIndex(utc_ts).tz_convert("UTC")
        secs = idx.hour * 3600 + idx.minute * 60
        out = np.full(len(idx), "asia", dtype=object)
        for name, (a, b) in self.sessions_utc.items():
            sa, sb = a.hour * 3600 + a.minute * 60, b.hour * 3600 + b.minute * 60
            mask = (secs >= sa) & (secs < sb) if sa < sb else (secs >= sa) | (secs < sb)
            out[np.asarray(mask)] = name
        return out


DEFAULT_SESSIONS = SessionTable()


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
