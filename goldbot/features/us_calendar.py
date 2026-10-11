"""US event proximity, point in time (opt-in): tier-1 and tier-2 scheduled releases from release `calendar-v1`.

Input: ctx["us_calendar"], the rows of release `calendar-v1` (goldbot/data/us_calendar.py). Each tier's knowledge
timeline (`known_timeline`: what the schedule said at every instant it changed) reaches the bars only through
`store.asof_join` on available_utc, so a bar sees an event only once its schedule row was available, and a
cancellation only from the download that saw it.

Features and columns (minutes are floats):
* `tier1_events`: `min_to_next_tier1_pit`, `min_since_last_tier1_pit`, `tier1_count_today` (CPI, NFP, PCE, FOMC
  statement and press conference). The `_pit` suffix keeps the minute columns apart from live-feed columns;
* `tier2_events` (BACKLOG 18): `min_to_next_tier2_pit`, `min_since_last_tier2_pit`, `tier2_count_today`.
Minutes are capped at HORIZON_MIN (the calendar's SCHEDULE_LEAD, 7 days): history only knows a schedule that far
ahead, so live data, which knows months ahead, is capped the same way. `*_count_today` counts the tier's events on the
bar's New York calendar day known at the bar. All columns are NaN before the calendar's first row and without
calendar data (the column set is fixed). They are unsigned: proximity means the same for a long and a short.

Opt-in: this module is NOT imported by goldbot.features and registers nothing on import. `enable()` registers both
features, so DEFAULT_FEATURE_NAMES and its feature version do not change.
"""
from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

from goldbot.data.store import asof_join
from goldbot.data.timeutil import epoch_ns
from goldbot.data.us_calendar import SCHEDULE_LEAD, known_timeline
from goldbot.features.registry import FEATURES, FeatureCtx, feature

TIER1, TIER2 = "tier1_events", "tier2_events"
NAMES = (TIER1, TIER2)
TIER1_COLUMNS = ("min_to_next_tier1_pit", "min_since_last_tier1_pit", "tier1_count_today")
TIER2_COLUMNS = ("min_to_next_tier2_pit", "min_since_last_tier2_pit", "tier2_count_today")
COLUMNS = (*TIER1_COLUMNS, *TIER2_COLUMNS)
VERSION = "1"
HORIZON_MIN = SCHEDULE_LEAD / pd.Timedelta(minutes=1)


def proximity(df: pd.DataFrame, cal: pd.DataFrame | None, tier: int, columns: tuple[str, str, str]) -> pd.DataFrame:
    """The three columns for one tier as known at each bar (see the module docstring)."""
    out = pd.DataFrame({c: np.full(len(df), np.nan) for c in columns}, index=df.index)
    if cal is None or len(cal) == 0:
        return out
    tl = known_timeline(cal, tier)
    if tl.empty:
        return out
    tl["_known"] = 1.0
    bars = pd.DataFrame({"ts_utc": pd.DatetimeIndex(pd.to_datetime(df["ts_utc"], utc=True)).as_unit("ns"),
                         "_row": np.arange(len(df))})
    j = asof_join(bars, tl).sort_values("_row")        # asof_join sorts by time; restore the bar order
    t = epoch_ns(pd.DatetimeIndex(j["ts_utc"]))
    covered = j["_known"].notna().to_numpy()

    def minutes(a: pd.Series, sign: int) -> np.ndarray:
        idx = pd.DatetimeIndex(pd.to_datetime(a, utc=True))
        have = ~np.asarray(idx.isna(), dtype=bool)
        m = np.full(len(idx), HORIZON_MIN)
        if have.any():
            m[have] = np.minimum(sign * (epoch_ns(idx[have]) - t[have]) / 6e10, HORIZON_MIN)
        return np.where(covered, m, np.nan)

    out[columns[0]] = minutes(j["next_event_utc"], 1)
    out[columns[1]] = minutes(j["last_event_utc"], -1)
    out[columns[2]] = np.where(covered, j["count_today"].to_numpy(dtype=float), np.nan)
    return out


def f_tier1_events(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    return proximity(df, ctx.get("us_calendar"), 1, TIER1_COLUMNS)


def f_tier2_events(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    return proximity(df, ctx.get("us_calendar"), 2, TIER2_COLUMNS)


_FNS: dict[str, Callable[[pd.DataFrame, FeatureCtx], pd.DataFrame]] = {TIER1: f_tier1_events, TIER2: f_tier2_events}


def enable() -> list[str]:
    """Register `tier1_events` and `tier2_events` (idempotent) and return their names, for an explicit feature list."""
    for name, fn in _FNS.items():
        if name not in FEATURES:
            feature(name, "calendar", version=VERSION)(fn)
    return list(NAMES)
