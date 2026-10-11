"""Entry windows from the calendar (BACKLOG 17, playbook G-2; BACKLOG 18, G-3), each off until the owner adopts it.

* Reopen (`risk.reopen`): "No entries for 15 min after each daily reopen and 30 min after the weekly open" (the minutes
  are config). The reopen is the latest closed -> open turn of the broker session table (D13; the static table where
  the broker reports none), so it follows the server clock's DST and a broker's own hours.
* Holidays (`risk.holidays`): "Holiday calendar ...: no entries on those days", from a versioned list in config
  (config/holidays_cme_metals.yaml) keyed by broker server date. With the default server clock (Europe/Athens)
  server midnight is 17:00 New York, the CME trading-day roll, so a listed date covers the CME trading day of that
  name; in the weeks when only one side of the Atlantic has changed clocks the roll is an hour off.
* Tier 2 (`risk.blackout.tier2`) uses goldbot.data.econ_calendar.blackout_window on the live calendar (engine).
  The calendar counts as known (`calendar_gap`) only when its newest fetch is under CALENDAR_MAX_AGE old and the
  archive covers through now + CALENDAR_LOOKAHEAD: a Forex Factory week runs Sunday to Saturday, New York time, so the
  archive covers to the end of the week of its latest event (review M2: a row of last week no longer passes for the
  new week at the Sunday open).

These functions only say whether `now` is inside a window; RiskGate turns the engine's flags into entry refusals
(`reopen_window`, `holiday_thin`, `tier2_blackout`, `calendar_unavailable`). Exits never read them.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from pydantic import model_validator

from goldbot.base import FrozenRecord
from goldbot.config import ROOT, ReopenSettings, load_yaml
from goldbot.data.calendar import SessionTable, server_midnight_utc
from goldbot.data.timeutil import server_wall

FF_TZ = ZoneInfo("America/New_York")          # Forex Factory's weeks: Sunday 00:00 to Sunday 00:00, New York
CALENDAR_LOOKAHEAD = pd.Timedelta(hours=24)   # the archive must cover at least this far ahead of now
CALENDAR_MAX_AGE = pd.Timedelta(days=7)       # and its newest fetch be at most this old
# a reopen whose market was also closed this long before it ends a long closure (the weekend, a holiday closure) and
# takes the weekly window; the daily break is about an hour, an early close about five
LONG_CLOSURE = pd.Timedelta(hours=12)


class HolidayList(FrozenRecord):
    """A versioned list of thin-liquidity server dates. Outside [valid_from, valid_through] it knows nothing."""
    version: str
    source: str
    valid_from: date
    valid_through: date
    days: dict[date, str]

    @model_validator(mode="after")
    def _days_inside_validity(self) -> HolidayList:
        bad = sorted(d for d in self.days if not self.valid_from <= d <= self.valid_through)
        if bad:
            raise ValueError(f"holiday list {self.version}: {bad} outside {self.valid_from}..{self.valid_through}")
        return self

    def covers(self, d: date) -> bool:
        return self.valid_from <= d <= self.valid_through

    def thin(self, d: date) -> str | None:
        """The holiday's name when `d` is a listed thin-liquidity day, else None."""
        return self.days.get(d)


def load_holidays(path: str | Path) -> HolidayList:
    """The list at `path` (relative paths from the repo root). Raises on a missing or malformed file; the engine
    turns that into `calendar_unavailable` (fail closed)."""
    p = Path(path)
    return HolidayList.model_validate(load_yaml(p if p.is_absolute() else ROOT / p))


def ff_week_end(ts: pd.Timestamp) -> pd.Timestamp:
    """The end (next Sunday 00:00 New York, as UTC) of the Forex Factory week holding `ts`."""
    local = pd.Timestamp(ts).tz_convert(FF_TZ)
    sunday = local.normalize().tz_localize(None) + pd.Timedelta(days=7 - (local.dayofweek + 1) % 7)
    return pd.Timestamp(sunday).tz_localize(FF_TZ).tz_convert("UTC")


def calendar_gap(events: pd.DataFrame | None, now: pd.Timestamp) -> str | None:
    """Why the archived calendar cannot judge entries at `now` (`calendar_unavailable`), or None when it can: no
    events around now, no fetch time, a newest fetch older than CALENDAR_MAX_AGE (or stamped in the future), or an
    archive that does not cover through now + CALENDAR_LOOKAHEAD."""
    if events is None or events.empty:
        return "no events archived around now"
    if "received_utc" not in events.columns:
        return "archive rows carry no fetch time"
    received = pd.Timestamp(pd.to_datetime(events["received_utc"], utc=True).max())
    if pd.isna(received) or received > now or now - received > CALENDAR_MAX_AGE:
        return f"newest fetch {received} is not within {CALENDAR_MAX_AGE} before now"
    covered = ff_week_end(pd.Timestamp(pd.to_datetime(events["ts_utc"], utc=True).max()))
    if covered < now + CALENDAR_LOOKAHEAD:
        return f"archive covers only until {covered.isoformat()}"
    return None


def server_date(sessions: SessionTable, now: pd.Timestamp) -> date:
    """The broker server calendar date of `now` (DST per the server zone's rules)."""
    return server_wall(pd.DatetimeIndex([pd.Timestamp(now)]), sessions.server_tz)[0].date()


def _open(sessions: SessionTable, ts: list[pd.Timestamp]) -> np.ndarray:
    return np.asarray(sessions.is_open(pd.DatetimeIndex(ts)), dtype=bool)


def last_reopen(sessions: SessionTable, now: pd.Timestamp, horizon: pd.Timedelta) -> tuple[pd.Timestamp, bool] | None:
    """(the instant r in [now - horizon, now] from which the market has been open through `now` after being closed
    just before r, whether it ended a long closure) or None: closed at `now`, or open throughout the horizon.
    Scanned on whole seconds (session boundaries are whole seconds): minute steps back, then bisection to the second."""
    # Known edge (review L4): at a broker table's `covers_from`, `is_open` switches from the static rule to the broker's
    # windows, so where the two disagree (e.g. the static break still running when the broker's session has already
    # begun) the switch itself looks like a reopen and refuses entries for up to the reopen window. Rare (once per
    # table refresh, only where the tables differ) and it only refuses entries, so it is accepted rather than special-cased.
    base = pd.Timestamp(now).floor("s")
    steps = int(horizon // pd.Timedelta(minutes=1)) + 1
    grid = [base - pd.Timedelta(minutes=k) for k in range(steps + 1)]
    is_open = _open(sessions, grid)
    if not is_open[0]:
        return None
    closed = np.flatnonzero(~is_open)
    if not len(closed):
        return None
    lo, hi = grid[closed[0]], grid[closed[0] - 1]               # closed at lo, open at hi
    while hi - lo > pd.Timedelta(seconds=1):
        mid = lo + ((hi - lo) // 2).floor("s")
        if _open(sessions, [mid])[0]:
            hi = mid
        else:
            lo = mid
    if pd.Timestamp(now) - hi > horizon:
        return None
    return hi, not bool(_open(sessions, [hi - LONG_CLOSURE])[0])


def reopen_window(sessions: SessionTable, now: pd.Timestamp, cfg: ReopenSettings) -> bool:
    """Inside [r, r + after_daily_reopen_min] of a daily reopen r, or [r, r + after_monday_open_min] of the weekly
    open (any reopen after a LONG_CLOSURE), both ends included as in the news blackout."""
    daily = pd.Timedelta(minutes=cfg.after_daily_reopen_min)
    weekly = pd.Timedelta(minutes=cfg.after_monday_open_min)
    hit = last_reopen(sessions, now, max(daily, weekly))
    if hit is None:
        return False
    r, long_closure = hit
    return pd.Timestamp(now) - r <= (weekly if long_closure else daily)


def reopen_window_end(sessions: SessionTable, now: pd.Timestamp, cfg: ReopenSettings) -> pd.Timestamp | None:
    """When the reopen window holding `now` ends (the same arithmetic as `reopen_window`), None when outside one.
    For display only: the flag is `reopen_window`."""
    daily = pd.Timedelta(minutes=cfg.after_daily_reopen_min)
    weekly = pd.Timedelta(minutes=cfg.after_monday_open_min)
    hit = last_reopen(sessions, now, max(daily, weekly))
    if hit is None:
        return None
    end = hit[0] + (weekly if hit[1] else daily)
    return end if end >= pd.Timestamp(now) else None


def holiday_end(hl: HolidayList, sessions: SessionTable, now: pd.Timestamp) -> tuple[str, pd.Timestamp] | None:
    """(today's holiday name, server midnight of the first unlisted server day after it, as UTC), None when today is
    not listed. A run of listed days is followed for at most two weeks. For display only: the flag is `holiday_thin`."""
    today = server_date(sessions, now)
    name = hl.thin(today)
    if name is None:
        return None
    day = today + timedelta(days=1)
    while hl.thin(day) is not None and day - today < timedelta(days=14):
        day += timedelta(days=1)
    return name, server_midnight_utc(day, sessions.server_tz)
