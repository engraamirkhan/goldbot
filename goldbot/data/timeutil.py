"""Time handling. Three named days, never confused:

* feature-day  — ends at COMEX settlement 13:30 America/New_York; daily bars and daily features.
* risk-day     — ends 00:00 UTC; loss-cap resets.
* swap-day     — each broker's rollover, read from the terminal at runtime (not here).

MT5 returns epoch integers that are *server clock* values labelled as UTC. `server_to_utc`
undoes that using the broker's IANA zone resolved per timestamp, so EU DST is handled
and no fixed offset is ever assumed.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

UTC = ZoneInfo("UTC")


def server_to_utc(server_ts: pd.Series | pd.DatetimeIndex, server_tz: str) -> pd.DatetimeIndex:
    """Convert naive MT5 'server time' stamps to tz-aware UTC.

    MT5 hands back e.g. 2025-03-10 09:00 meaning 09:00 *Athens* time. Localising to the server
    zone and converting to UTC applies the correct DST offset for each individual timestamp.
    """
    idx = pd.DatetimeIndex(server_ts)
    if idx.tz is not None:
        idx = idx.tz_convert(None)
    # ambiguous/nonexistent: brokers freeze the clock across DST; shift_forward is the safe choice
    return idx.tz_localize(ZoneInfo(server_tz), ambiguous="NaT", nonexistent="shift_forward").tz_convert(UTC)


def utc_to_server(utc_ts: pd.DatetimeIndex, server_tz: str) -> pd.DatetimeIndex:
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


def floor_tf(utc_ts: pd.DatetimeIndex, tf_sec: int) -> pd.DatetimeIndex:
    """Floor to timeframe boundary (left label). Weekly floors to Sunday 00:00 UTC-aligned weeks via 7d."""
    idx = pd.DatetimeIndex(utc_ts)
    if tf_sec == 7 * 86400:
        # weeks start Sunday 00:00 UTC (gold opens Sunday evening)
        dow = (idx.dayofweek + 1) % 7  # Sunday -> 0
        return (idx.normalize() - pd.to_timedelta(dow, unit="D"))
    ns = np.int64(tf_sec) * 1_000_000_000
    vals = idx.tz_convert(UTC).as_unit("ns").asi8
    return pd.DatetimeIndex(pd.to_datetime((vals // ns) * ns, unit="ns", utc=True))


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


__all__ = [
    "UTC",
    "server_to_utc",
    "utc_to_server",
    "feature_day",
    "feature_day_close",
    "risk_day",
    "floor_tf",
    "now_utc",
    "time",
    "timedelta",
]
