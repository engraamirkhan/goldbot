"""Session and calendar features."""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.data.store import asof_join
from goldbot.features.registry import feature

SESSION_OPENS_UTC = {"asia": 23, "london": 7, "newyork": 12.5}


@feature("session", "calendar")
def f_session(df, ctx):
    idx = pd.DatetimeIndex(df["ts_utc"]).tz_convert("UTC")
    sess = DEFAULT_SESSIONS.session_label(idx)
    out = pd.DataFrame(index=df.index)
    out["session_id"] = pd.Categorical(sess, categories=["asia", "london", "newyork"]).codes
    hour = idx.hour + idx.minute / 60
    for name, h in SESSION_OPENS_UTC.items():
        m = (hour - h) % 24
        out[f"min_since_{name}_open"] = m * 60
    out["dow"] = idx.dayofweek
    out["hour_utc"] = idx.hour
    # US and EU DST flags (shifts the NY/London open relative to UTC)
    out["us_dst"] = (idx.tz_convert("America/New_York").map(lambda t: t.dst().total_seconds() > 0)).astype(int)
    out["eu_dst"] = (idx.tz_convert("Europe/London").map(lambda t: t.dst().total_seconds() > 0)).astype(int)
    return out


@feature("calendar_events", "calendar")
def f_events(df, ctx):
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
    nxt = ev_ts.searchsorted(ts, side="left")
    prv = nxt - 1
    nxt_t = pd.Series(np.where(nxt < len(ev_ts), ev_ts.asi8[np.minimum(nxt, len(ev_ts) - 1)], np.nan), index=df.index)
    prv_t = pd.Series(np.where(prv >= 0, ev_ts.asi8[np.maximum(prv, 0)], np.nan), index=df.index)
    out["min_to_next_tier1"] = (nxt_t - ts.asi8) / 6e10
    out["min_since_last_tier1"] = (ts.asi8 - prv_t) / 6e10
    before, after = ctx.get("blackout_before", 15), ctx.get("blackout_after", 30)
    out["in_blackout"] = ((out["min_to_next_tier1"] <= before) | (out["min_since_last_tier1"] <= after)).astype(int)
    return out


@feature("macro", "macro")
def f_macro(df, ctx):
    """Macro levels and changes joined strictly as-of availability (ctx['macro_wide'])."""
    mw = ctx.get("macro_wide")
    out = pd.DataFrame(index=df.index)
    if mw is None or mw.empty:
        return out
    joined = asof_join(df[["ts_utc"]].copy(), mw)
    for c in mw.columns:
        if c == "available_utc":
            continue
        out[f"macro_{c}"] = joined[c].values
        # 5 and 20 "observations" change, approximated by bar-level diff of the forward-filled series
        out[f"macro_{c}_chg"] = pd.Series(joined[c].values).diff(96 * 5).values
    return out
