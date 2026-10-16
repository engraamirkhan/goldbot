"""Time handling. Three named days, never confused:

* feature-day  — ends at COMEX settlement 13:30 America/New_York; daily bars and daily features.
* risk-day     — ends 00:00 UTC; loss-cap resets.
* swap-day     — each broker's rollover, read from the terminal at runtime (not here).

and a fourth, never used for a limit: the opportunity day, D-1 22:00 to D 22:00 UTC (ROLLOVER_UTC_H), the day of the
opt-in `day_type` features and of the owner's north-star reports (goldbot/ops/report_days.py). 22:00 UTC falls inside
gold's 17:00-18:00 New York daily break in both US DST regimes, so the Sunday reopen belongs to Monday.

MT5 returns epoch integers that are *server clock* values labelled as UTC. `server_to_utc`
undoes that using the broker's zone resolved per timestamp, so DST is handled and no fixed
offset is ever assumed. A broker's `server_tz` is an IANA name (`Europe/Athens`: UTC+2/+3 on
EU dates) or `NY+7` (UTC+2/+3 on US dates: the "5 pm New York = midnight" clock IC Markets
documents). The two disagree by one hour in the US-EU gap weeks (about three in March, one in
Oct/Nov); `goldbot.ops.server_clock` measures which one the broker really runs.
"""
from __future__ import annotations

import math
from datetime import datetime, time, timedelta, timezone, tzinfo
from typing import Any, Literal
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

UTC = ZoneInfo("UTC")
NEW_YORK = ZoneInfo("America/New_York")
NY_PLUS_7 = "NY+7"
_NY_SHIFT = timedelta(hours=7)


class NewYorkPlus7(tzinfo):
    """Server clock = New York wall clock + 7 h: UTC+2 while New York is on EST, UTC+3 while it is on EDT, switching
    with the US (second Sunday of March, first Sunday of November, at 02:00 New York = 09:00 server time).

    Brokers whose midnight is "5 pm New York" (IC Markets: MT4/MT5 server time "is based on '5 pm New York'") run
    this clock. Derived from America/New_York, so a change to US DST law arrives with the tz database.

    For Python datetimes only: pandas treats any non-IANA tzinfo as a fixed offset (`utcoffset(None)`, which is None
    here so pandas raises instead of silently using one offset all year). Vectorised code uses `server_wall` and
    `server_to_utc`, which handle `NY+7` through America/New_York."""

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        if dt is None:
            return None
        ny = (dt.replace(tzinfo=None) - _NY_SHIFT).replace(tzinfo=NEW_YORK, fold=dt.fold)
        off = ny.utcoffset()
        return None if off is None else off + _NY_SHIFT

    def dst(self, dt: datetime | None) -> timedelta | None:
        if dt is None:
            return None
        ny = (dt.replace(tzinfo=None) - _NY_SHIFT).replace(tzinfo=NEW_YORK, fold=dt.fold)
        return ny.dst()

    def tzname(self, dt: datetime | None) -> str:
        return NY_PLUS_7

    def fromutc(self, dt: datetime) -> datetime:
        ny = dt.replace(tzinfo=timezone.utc).astimezone(NEW_YORK)
        return (ny.replace(tzinfo=None) + _NY_SHIFT).replace(tzinfo=self, fold=ny.fold)

    def __repr__(self) -> str:
        return f"NewYorkPlus7({NY_PLUS_7!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, NewYorkPlus7)

    def __hash__(self) -> int:
        return hash(NY_PLUS_7)


def server_zone(server_tz: str) -> tzinfo:
    """The tzinfo for an account's `server_tz`: `NY+7` or any IANA name. Raises (ValueError / ZoneInfoNotFoundError)
    on anything else, so a typo in accounts.yaml fails at load instead of shifting every timestamp."""
    if server_tz == NY_PLUS_7:
        return NewYorkPlus7()
    return ZoneInfo(server_tz)


def server_wall(utc_ts: pd.DatetimeIndex | pd.Timestamp, server_tz: str) -> pd.DatetimeIndex:
    """Naive server wall-clock values for UTC instants (what the MT5 terminal displays and expects in requests)."""
    idx = pd.DatetimeIndex([utc_ts] if isinstance(utc_ts, pd.Timestamp) else utc_ts)
    if idx.tz is None:      # a naive stamp could already be server time: guessing UTC would shift it silently
        raise ValueError("server_wall needs tz-aware UTC instants")
    if server_tz == NY_PLUS_7:
        return idx.tz_convert(NEW_YORK).tz_localize(None) + pd.Timedelta(_NY_SHIFT)
    return idx.tz_convert(ZoneInfo(server_tz)).tz_localize(None)


def server_utc_offset(server_tz: str, at_utc: pd.Timestamp) -> timedelta:
    """The server clock's offset from UTC at instant `at_utc` (e.g. +2 h or +3 h)."""
    at = pd.Timestamp(at_utc)
    at = at.tz_localize(UTC) if at.tzinfo is None else at.tz_convert(UTC)
    return (server_wall(at, server_tz)[0] - at.tz_localize(None)).to_pytimedelta()


def epoch_ns(ts: pd.DatetimeIndex | pd.Series | pd.Index) -> np.ndarray:
    """Epoch nanoseconds as int64, whatever unit the timestamps carry.

    pandas 3 keeps the inferred resolution (s/ms/us/ns) instead of always using ns, so raw `.asi8` or
    `.view("i8")` silently changes scale with the data source. Always go through this helper."""
    return pd.DatetimeIndex(ts).as_unit("ns").to_numpy(dtype=np.int64)


def utc_index(col: pd.Series) -> pd.DatetimeIndex:
    """`pd.DatetimeIndex(pd.to_datetime(col, utc=True))`, skipping the conversion when the column already is UTC
    datetimes (to_datetime then returns the same values and dtype, after an object-level cache check per call)."""
    dt = col.dtype
    if isinstance(dt, pd.DatetimeTZDtype) and dt.tz is timezone.utc:
        return pd.DatetimeIndex(col)
    return pd.DatetimeIndex(pd.to_datetime(col, utc=True))


def server_to_utc(server_ts: pd.Series | pd.DatetimeIndex, server_tz: str) -> pd.DatetimeIndex:
    """Convert naive MT5 'server time' stamps to tz-aware UTC.

    MT5 hands back e.g. 2025-03-10 09:00 meaning 09:00 *Athens* time. Localising to the server
    zone and converting to UTC applies the correct DST offset for each individual timestamp.
    `NY+7` is localised as New York wall clock + 7 h (pandas cannot localise a custom tzinfo per element).
    """
    idx = pd.DatetimeIndex(server_ts)
    if idx.tz is not None:
        idx = idx.tz_convert(None)
    # ambiguous/nonexistent: brokers freeze the clock across DST; shift_forward is the safe choice
    if server_tz == NY_PLUS_7:
        return (idx - pd.Timedelta(_NY_SHIFT)).tz_localize(NEW_YORK, ambiguous="NaT",
                                                           nonexistent="shift_forward").tz_convert(UTC)
    return idx.tz_localize(ZoneInfo(server_tz), ambiguous="NaT", nonexistent="shift_forward").tz_convert(UTC)


def utc_to_server(utc_ts: pd.DatetimeIndex, server_tz: str) -> pd.DatetimeIndex:
    """Tz-aware server time for an IANA `server_tz`. pandas treats a custom tzinfo as a fixed offset, so `NY+7` is
    refused here rather than converted wrongly in the gap weeks: use `server_wall` (naive) for any server_tz."""
    if server_tz == NY_PLUS_7:
        raise ValueError("utc_to_server cannot represent NY+7 in pandas; use server_wall")
    return pd.DatetimeIndex(utc_ts).tz_convert(ZoneInfo(server_tz))


def feature_day(utc_ts: pd.DatetimeIndex, tz: str = "America/New_York", anchor: str = "13:30") -> pd.DatetimeIndex:
    """Assign each UTC timestamp to its feature-day.

    A feature-day labelled D runs from D-1 13:30 NY to D 13:30 NY (exclusive). Returned as a naive
    date index (midnight) so it can be used as a group key; the *close* of feature-day D is at
    D 13:30 NY, which `feature_day_close` gives in UTC for visibility checks.
    """
    # work on naive *local wall-clock* values so "+1 day" is a calendar day, not 24 absolute hours
    # (a Timedelta of 24h across a US DST switch would mislabel the day).
    local = pd.DatetimeIndex(utc_ts).tz_convert(ZoneInfo(tz)).tz_localize(None)
    hh, mm = (int(x) for x in anchor.split(":"))
    day = local.normalize()
    cutoff = day + pd.Timedelta(hours=hh, minutes=mm)
    return pd.DatetimeIndex(day.where(local < cutoff, day + pd.Timedelta(days=1)))


def feature_day_close(day: pd.DatetimeIndex, tz: str = "America/New_York", anchor: str = "13:30") -> pd.DatetimeIndex:
    """UTC instant at which feature-day `day` becomes visible (its settlement close)."""
    hh, mm = (int(x) for x in anchor.split(":"))
    local = pd.DatetimeIndex(day) + pd.Timedelta(hours=hh, minutes=mm)
    return local.tz_localize(ZoneInfo(tz), ambiguous="NaT", nonexistent="shift_forward").tz_convert(UTC)


def risk_day(utc_ts: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(utc_ts).tz_convert(UTC).normalize().tz_localize(None)


ROLLOVER_UTC_H = 22   # the opportunity day rolls at 22:00 UTC, inside the daily break in summer and winter


def opportunity_day(ts: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Label (naive date) of the opportunity day holding each UTC instant: [D-1 22:00, D 22:00) UTC is day D."""
    idx = pd.DatetimeIndex(ts).tz_convert(UTC)
    return pd.DatetimeIndex((idx + pd.Timedelta(hours=24 - ROLLOVER_UTC_H)).normalize().tz_localize(None))


def floor_tf(utc_ts: pd.DatetimeIndex, tf_sec: int) -> pd.DatetimeIndex:
    """Floor to timeframe boundary (left label). Weekly floors to Sunday 00:00 UTC-aligned weeks via 7d."""
    idx = pd.DatetimeIndex(utc_ts)
    if tf_sec == 7 * 86400:
        # weeks start Sunday 00:00 UTC (gold opens Sunday evening)
        dow = (idx.dayofweek + 1) % 7  # Sunday -> 0
        return (idx.normalize() - pd.to_timedelta(dow, unit="D"))
    ns = np.int64(tf_sec) * 1_000_000_000
    vals = epoch_ns(idx.tz_convert(UTC))
    return pd.DatetimeIndex(pd.to_datetime((vals // ns) * ns, unit="ns", utc=True))


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


# A heartbeat stamped this far ahead of the reader's clock is a clock fault, not a fresh report. Writers and readers of
# the safety heartbeats share one box (the state directory), so the only honest skew is an NTP step; a few seconds
# covers it. Health passes use a wider tolerance (goldbot.ops.checks._base.HEALTH_CLOCK_AHEAD_S): their `now` is taken
# once at the start of a pass that can run for many seconds.
CLOCK_AHEAD_TOLERANCE_S = 5.0

HeartbeatProblem = Literal["invalid", "future", "stale"]


def heartbeat_problem(ts: Any, now: float, max_age_s: float, *,
                      ahead_s: float = CLOCK_AHEAD_TOLERANCE_S) -> HeartbeatProblem | None:
    """Why a heartbeat stamped `ts` (epoch seconds) cannot be called alive at `now`, None when it can. Fail closed
    both ways (found 2026-10-16): `invalid` when `ts` is not a finite number (a string, a bool, null, NaN or
    infinity: NaN compared false with every limit and so read as fresh); `future` when it is more than `ahead_s`
    ahead of `now` (clock skew, a bad write, a backup restored from a box with a fast clock: `now - ts` is then
    negative and a dead writer would look fresh forever); `stale` when it is more than `max_age_s` old."""
    if isinstance(ts, bool) or not isinstance(ts, (int, float)) or not math.isfinite(ts):
        return "invalid"
    if ts - now > ahead_s:
        return "future"
    if now - ts > max_age_s:
        return "stale"
    return None


__all__ = [
    "UTC",
    "NY_PLUS_7",
    "NewYorkPlus7",
    "server_zone",
    "server_wall",
    "server_utc_offset",
    "server_to_utc",
    "utc_to_server",
    "feature_day",
    "feature_day_close",
    "risk_day",
    "ROLLOVER_UTC_H",
    "opportunity_day",
    "floor_tf",
    "now_utc",
    "CLOCK_AHEAD_TOLERANCE_S",
    "HeartbeatProblem",
    "heartbeat_problem",
    "time",
    "timedelta",
]
