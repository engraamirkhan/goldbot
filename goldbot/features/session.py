"""Session and calendar features."""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.data.store import asof_join
from goldbot.data.timeutil import epoch_ns
from goldbot.features.columns import Columns
from goldbot.features.registry import FeatureCtx, feature

SESSION_OPENS_UTC = {"asia": 23, "london": 7, "newyork": 12.5}


@feature("session", "calendar")
def f_session(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    idx = pd.DatetimeIndex(df["ts_utc"]).tz_convert("UTC")
    sess = DEFAULT_SESSIONS.session_label(idx)
    out = Columns(df.index)
    out["session_id"] = pd.Categorical(sess, categories=["asia", "london", "newyork"]).codes
    hour = idx.hour + idx.minute / 60
    for name, h in SESSION_OPENS_UTC.items():
        m = (hour - h) % 24
        out[f"min_since_{name}_open"] = m * 60
    out["dow"] = idx.dayofweek
    out["hour_utc"] = idx.hour
    # US and EU DST flags (shifts the NY/London open relative to UTC)
    out["us_dst"] = _dst_flag(idx, "America/New_York")
    out["eu_dst"] = _dst_flag(idx, "Europe/London")
    return out.frame()


def _dst_on(hours: np.ndarray, tz: str) -> np.ndarray:
    """tz.dst() > 0 at each UTC hour start (int64 hour numbers since the epoch)."""
    at = pd.DatetimeIndex(pd.to_datetime(hours * 3_600_000_000_000, unit="ns", utc=True)).tz_convert(tz)
    return np.array([t.dst().total_seconds() > 0 for t in at], dtype=np.bool_)   # type: ignore[union-attr]


def _dst_flag(idx: pd.DatetimeIndex, tz: str) -> np.ndarray:
    """1 where `tz` observes daylight saving at the instant, else 0 (int64). New York and London switch on whole
    UTC hours, at most once a day, so the flag is constant within a UTC hour, and within a UTC day whose start and
    end agree: tz.dst() is evaluated at each distinct day's two ends and hourly only on a switch day, then spread
    back, instead of once per bar."""
    ns = epoch_ns(idx)
    hours, inv = np.unique(ns // 3_600_000_000_000, return_inverse=True)
    days = np.unique(hours // 24)
    start, end = _dst_on(days * 24, tz), _dst_on(days * 24 + 24, tz)
    flag = start[np.searchsorted(days, hours // 24)]
    switch = np.isin(hours // 24, days[start != end])
    if switch.any():
        flag[switch] = _dst_on(hours[switch], tz)
    return flag[inv.reshape(-1)].astype(int)


@feature("calendar_events", "calendar")
def f_events(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    """Minutes to the next tier-1 event and since the last, from ctx['events'] (ts_utc, tier, name).

    Events are scheduled, so the schedule is known in advance: joining on the *scheduled* time is
    legitimate for 'minutes to next'. The *outcome* (surprise) is only visible via available_utc.
    """
    out = pd.DataFrame(index=df.index)
    ev = ctx.get("events")
    if ev is None or len(ev) == 0:
        out["min_to_next_tier1"] = np.nan
        out["min_since_last_tier1"] = np.nan
        out["in_blackout"] = 0
        return out
    ev = ev[ev["tier"] == 1].sort_values("ts_utc")
    ts = pd.DatetimeIndex(df["ts_utc"]).tz_convert("UTC")
    ev_ts = pd.DatetimeIndex(ev["ts_utc"]).tz_convert("UTC")
    ts_ns, ev_ns = epoch_ns(ts), epoch_ns(ev_ts)
    nxt = np.searchsorted(ev_ns, ts_ns, side="left")
    prv = nxt - 1
    nxt_t = pd.Series(np.where(nxt < len(ev_ts), ev_ns[np.minimum(nxt, len(ev_ts) - 1)], np.nan), index=df.index)
    prv_t = pd.Series(np.where(prv >= 0, ev_ns[np.maximum(prv, 0)], np.nan), index=df.index)
    out["min_to_next_tier1"] = (nxt_t - ts_ns) / 6e10
    out["min_since_last_tier1"] = (ts_ns - prv_t) / 6e10
    before, after = ctx.get("blackout_before", 15), ctx.get("blackout_after", 30)
    out["in_blackout"] = ((out["min_to_next_tier1"] <= before) | (out["min_since_last_tier1"] <= after)).astype(int)
    return out


@feature("macro", "macro")
def f_macro(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    """Macro levels and changes joined strictly as-of availability (ctx['macro_wide'])."""
    mw = ctx.get("macro_wide")
    out = pd.DataFrame(index=df.index)
    if mw is None or mw.empty:
        return out
    joined = asof_join(df[["ts_utc"]].copy(), mw)
    for c in mw.columns:
        if c == "available_utc":
            continue
        out[f"macro_{c}"] = joined[c].to_numpy()
        # 5 and 20 "observations" change, approximated by bar-level diff of the forward-filled series
        out[f"macro_{c}_chg"] = pd.Series(joined[c].to_numpy()).diff(96 * 5).to_numpy()
    return out
