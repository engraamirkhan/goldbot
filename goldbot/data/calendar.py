"""Trading calendar for gold CFDs.

Session state is derived from the broker's session table (server-time daily break and the
weekend gap), never from silence in the feed. Defaults match IC Markets / Vantage XAUUSD:
open Monday 01:02 server time, daily break 23:59-01:02, close Friday 23:57.
At runtime the live engine overwrites these from `symbol_info(...).session_deals`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SessionTable:
    server_tz: str = "Europe/Athens"
    daily_break_start: time = time(23, 59)
    daily_break_end: time = time(1, 2)
    week_open_day: int = 0          # Monday (server time), after the break ends
    week_close_day: int = 4         # Friday
    week_close_time: time = time(23, 57)
    # Trading sessions in UTC, used only for features (not for open/closed state)
    sessions_utc: dict[str, tuple[time, time]] = field(
        default_factory=lambda: {
            "asia": (time(23, 0), time(7, 0)),
            "london": (time(7, 0), time(12, 30)),
            "newyork": (time(12, 30), time(21, 0)),
        }
    )

    def is_open(self, utc_ts: pd.DatetimeIndex) -> np.ndarray:
        local = pd.DatetimeIndex(utc_ts).tz_convert(ZoneInfo(self.server_tz))
        dow = local.dayofweek.values
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
