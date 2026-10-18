"""TradingView closes as a second price feed, reconciled against the primary feed (design: Data architecture, D12/D20).

The TradingView chart feed is OANDA, not the broker, so the bars the direct pull reads from it (`tv_indicators` rows
whose `origin` is pull_live or pull_history, the bar's close as the plot "close") are a SECOND feed only: never a primary
bar source, never blended into the Dukascopy/broker bars. The same rule as `crossfeed`: two sources kept separate and
compared.

* `compare_closes`: each TV bar's close against the reference M1 (Dukascopy, else the broker's) resampled to the bar:
  the bid close of the bar's last minute. A bar whose close differs by MORE than the tolerance (crossfeed's
  CLOSE_TOL_POINTS x point, 2 points; exactly 2 is kept, as in `crossfeed.reconcile`) is flagged. A bar the reference
  does not fully cover is not judged.
* `apply_dq`: a flagged bar's rows get `dq = "tv_close_diff"` and every plot of the bar (value) set NaN, so a feature
  never reads a price-faulty bar ("a bar with no usable value is NaN, never a stale value"). Webhook rows are never touched.
* `tv_other_feed` / `confirm_spikes_with_tv`: TV as an extra feed for D20 spike confirmation: a spike stays a
  warning only if no other feed, TV included, shows a matching move (`quality.confirm_spikes` per feed).
* `reconcile_counts`: the matched / mismatch / missing counts through `crossfeed.reconcile` itself, for the job's report.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from goldbot.data.calendar import DEFAULT_SESSIONS, SessionTable
from goldbot.data.crossfeed import reconcile
from goldbot.data.quality import DQEvent, confirm_spikes

PULL_ORIGINS = ("pull_live", "pull_history")          # TV rows from the direct pull (webhook rows are not bars)
CLOSE_PLOT = "close"                                  # the pull stores each bar's close as this plot
DQ_FLAG = "tv_close_diff"
MAX_TF_MINUTES = 240                                  # daily/weekly TV bars open on the exchange session, not UTC


def tf_minutes(tf: str) -> int:
    """Minutes of a numeric TradingView timeframe ("15", "60", "240"); 0 for one this check does not cover."""
    t = str(tf).strip()
    return int(t) if t.isdigit() and 0 < int(t) <= MAX_TF_MINUTES else 0


def _pulled(long: pd.DataFrame) -> pd.DataFrame:
    if long.empty or "origin" not in long.columns:
        return long.iloc[:0]
    return long[long["origin"].isin(PULL_ORIGINS)]


def tv_closes(long: pd.DataFrame, tf: str) -> pd.DataFrame:
    """DataFrame(ts_utc, close): the pulled bars' closes at `tf`, one per bar (the latest row wins), NaN dropped."""
    d = _pulled(long)
    d = d[(d["tf"].astype(str) == str(tf)) & (d["plot"] == CLOSE_PLOT) & d["value"].notna()]
    if d.empty:
        return pd.DataFrame({"ts_utc": pd.DatetimeIndex([], tz="UTC"), "close": pd.Series(dtype=float)})
    ts = pd.DatetimeIndex(pd.to_datetime(d["bar_time"], utc=True)).as_unit("ns")
    out = pd.DataFrame({"ts_utc": ts, "close": d["value"].astype(float).to_numpy()})
    return out.drop_duplicates("ts_utc", keep="last").sort_values("ts_utc").reset_index(drop=True)


def reference_closes(ref_m1: pd.DataFrame, minutes: int) -> pd.Series:
    """The reference M1's bid close of each COMPLETE `minutes`-minute bar (its last minute present), by bar open."""
    if ref_m1.empty or minutes <= 0:
        return pd.Series(dtype=float)
    ts = pd.DatetimeIndex(pd.to_datetime(ref_m1["ts_utc"], utc=True)).as_unit("ns")
    s = pd.Series(ref_m1["bid_close"].astype(float).to_numpy(), index=ts)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    step = pd.Timedelta(minutes=minutes)
    opens = pd.DatetimeIndex(s.index).floor(f"{minutes}min")
    last = s.groupby(opens).tail(1)
    bar_open = pd.DatetimeIndex(last.index).floor(f"{minutes}min")
    last = last[pd.DatetimeIndex(last.index) == bar_open + step - pd.Timedelta(minutes=1)]
    return pd.Series(last.to_numpy(), index=pd.DatetimeIndex(last.index).floor(f"{minutes}min"))


def compare_closes(long: pd.DataFrame, ref_m1: pd.DataFrame, *, tf: str, tolerance: float, start: pd.Timestamp,
                   end: pd.Timestamp, sessions: SessionTable = DEFAULT_SESSIONS) -> pd.DataFrame:
    """DataFrame(ts_utc, tv_close, ref_close, diff, dq) for the pulled bars at `tf` opening in [start, end) that the
    reference covers; `dq` is `diff > tolerance` (strictly, with the same 1e-9 float slack as crossfeed.reconcile)."""
    cols = ["ts_utc", "tv_close", "ref_close", "diff", "dq"]
    m = tf_minutes(tf)
    tv = tv_closes(long, tf)
    if m == 0 or tv.empty:
        return pd.DataFrame(columns=cols)
    tv = tv[(tv["ts_utc"] >= start) & (tv["ts_utc"] < end)]
    tv = tv[sessions.is_open(pd.DatetimeIndex(tv["ts_utc"]))] if len(tv) else tv
    ref = reference_closes(ref_m1, m)
    ref_close = ref.reindex(pd.DatetimeIndex(tv["ts_utc"]))
    out = pd.DataFrame({"ts_utc": tv["ts_utc"].reset_index(drop=True), "tv_close": tv["close"].to_numpy(),
                        "ref_close": ref_close.to_numpy()})
    out = out[out["ref_close"].notna()].reset_index(drop=True)
    out["diff"] = (out["tv_close"] - out["ref_close"]).abs()
    out["dq"] = out["diff"] > tolerance + 1e-9
    return out[cols]


def apply_dq(long: pd.DataFrame, cmp: pd.DataFrame) -> pd.DataFrame:
    """`long` with a `dq` column; the pulled rows of every flagged bar get dq = DQ_FLAG and value NaN."""
    out = long.copy()
    if "dq" not in out.columns:
        out["dq"] = ""
    out["dq"] = out["dq"].fillna("")
    bad = pd.DatetimeIndex(cmp.loc[cmp["dq"].astype(bool), "ts_utc"]).as_unit("ns") if len(cmp) else pd.DatetimeIndex([])
    if len(bad) == 0:
        return out
    bar = pd.DatetimeIndex(pd.to_datetime(out["bar_time"], utc=True)).as_unit("ns")
    hit = out["origin"].isin(PULL_ORIGINS).to_numpy() & bar.isin(bad)
    out.loc[hit, "value"] = np.nan
    out.loc[hit, "dq"] = DQ_FLAG
    return out


def tv_other_feed(long: pd.DataFrame, *, tf: str) -> pd.DataFrame:
    """The pulled TV closes as a bar frame (ts_utc, close) that `quality.confirm_spikes` takes as `other`."""
    return tv_closes(long, tf)


def confirm_spikes_with_tv(bars: pd.DataFrame, events: list[DQEvent], other: pd.DataFrame, tv: pd.DataFrame, *,
                           tf_seconds: int = 60) -> tuple[pd.DataFrame, list[DQEvent]]:
    """D20 with TV as an extra feed: a spike on `bars` is kept only if neither `other` (the primary second feed) nor
    `tv` shows a matching move; one that either shows is a real market move and is dropped."""
    out, kept = confirm_spikes(bars, events, other, tf_seconds=tf_seconds)
    return confirm_spikes(out, kept, tv, tf_seconds=tf_seconds)


def reconcile_counts(long: pd.DataFrame, ref_m1: pd.DataFrame, *, tf: str, tolerance: float, start: pd.Timestamp,
                     end: pd.Timestamp, sessions: SessionTable = DEFAULT_SESSIONS) -> dict[str, Any]:
    """`crossfeed.reconcile` over the TV bars (as the "engine" side) and the reference's complete bars (the "broker"
    side): matched, close_mismatch, missing_*, max_close_diff. Informational: TV never grades into a FAIL."""
    m = tf_minutes(tf)
    tv = tv_closes(long, tf)
    ref = reference_closes(ref_m1, m)

    def frame(ts: Any, vals: Any) -> pd.DataFrame:
        return pd.DataFrame({"ts_utc": pd.DatetimeIndex(ts), "bid_close": np.asarray(vals, dtype=float)})
    ref_frame = frame(ref.index, ref.to_numpy())
    tv_frame = frame(tv["ts_utc"], tv["close"].to_numpy())
    return reconcile(tv_frame, ref_frame, start=start, end=end, tolerance=tolerance, sessions=sessions)
