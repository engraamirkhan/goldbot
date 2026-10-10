"""Data-quality checks run on every ingest batch.

Warnings commit with a `dq_flag`; error rows are quarantined (`quarantine`: the store writers put them in the
`bars_quarantine` table, never in the bar tables, and the health check alerts on error events). The trading loop never
opens a position on a bar carrying an error flag.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.base import Record
from goldbot.data.calendar import DEFAULT_SESSIONS, SessionTable
from goldbot.data.timeutil import epoch_ns


class DQEvent(Record):
    ts_utc: pd.Timestamp
    check: str
    severity: str  # "warning" | "error"
    detail: str


def check_bars(bars: pd.DataFrame, *, tf_seconds: int = 60, sessions: SessionTable = DEFAULT_SESSIONS,
               spike_sigma: float = 12.0, max_gap_bars: int = 3) -> tuple[pd.DataFrame, list[DQEvent]]:
    events: list[DQEvent] = []
    # arrival order is checked before sorting, which would otherwise hide an out-of-order batch
    arrived = pd.DatetimeIndex(pd.to_datetime(bars["ts_utc"], utc=True)) if len(bars) else pd.DatetimeIndex([])
    b = bars.sort_values("ts_utc").reset_index(drop=True).copy()
    b["dq_flag"] = ""
    if b.empty:
        return b, events
    ts = pd.DatetimeIndex(b["ts_utc"])

    dup = b["ts_utc"].duplicated(keep="last")
    if dup.any():
        events.append(DQEvent(ts_utc=ts[dup.to_numpy()][0], check="duplicate_ts", severity="error", detail=f"{int(dup.sum())} duplicate bar timestamps"))
        b.loc[dup, "dq_flag"] = "error:duplicate"

    bad_spread = (b["ask_close"] < b["bid_close"]) | (b["spread_mean"] < 0)
    if bad_spread.any():
        events.append(DQEvent(ts_utc=ts[bad_spread.to_numpy()][0], check="bid_gt_ask", severity="error", detail=f"{int(bad_spread.sum())} bars with bid>ask"))
        b.loc[bad_spread, "dq_flag"] = "error:bid_gt_ask"

    mono = np.diff(epoch_ns(arrived)) < 0          # equal stamps are reported as duplicates above
    if mono.any():
        events.append(DQEvent(ts_utc=arrived[1:][mono][0], check="non_monotonic", severity="error",
                              detail=f"{int(mono.sum())} bars arrived out of time order"))

    # gaps inside open sessions
    gap = np.diff(epoch_ns(ts)) / 1e9
    big = gap > tf_seconds * max_gap_bars
    if big.any():
        for i in (int(j) for j in np.flatnonzero(big)):
            # only a problem if the market was open throughout
            probe = pd.date_range(ts[i], ts[i + 1], freq=f"{tf_seconds}s", inclusive="neither")
            if len(probe) and sessions.is_open(probe).all():
                events.append(DQEvent(ts_utc=ts[i], check="gap_in_session", severity="warning", detail=f"{int(gap[i] // tf_seconds)} missing bars after {ts[i]}"))
                b.at[i, "dq_flag"] = "warning:gap"

    # spikes: |log return| > k sigma of trailing 60 bars
    mid = (b["bid_close"] + b["ask_close"]) / 2
    r = np.log(mid).diff()
    sigma = r.shift(1).rolling(60, min_periods=20).std()  # trailing, excluding the bar under test
    spike = (r.abs() > spike_sigma * sigma) & sigma.notna()
    if spike.any():
        for i in (int(j) for j in np.flatnonzero(spike.to_numpy())):
            events.append(DQEvent(ts_utc=ts[i], check="spike", severity="warning", detail=f"return {r.iloc[i]:.4f} vs sigma {sigma.iloc[i]:.5f}"))
            prev = str(b.at[i, "dq_flag"])
            b.at[i, "dq_flag"] = (prev + ";" if prev else "") + "warning:spike"

    return b, events


def quarantine(bars: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split check_bars output into (clean, quarantined): rows whose dq_flag carries an error (duplicate stamp,
    bid > ask) are kept out of the bar tables; warnings stay with their flag. A duplicate stamp keeps its last copy
    clean (check_bars flags the earlier ones), so a re-delivered minute is not lost."""
    if bars.empty or "dq_flag" not in bars.columns:
        return bars, bars.iloc[0:0]
    err = bars["dq_flag"].fillna("").astype(str).str.contains("error:")
    return bars[~err].reset_index(drop=True), bars[err].reset_index(drop=True)


def bar_errors(bars: pd.DataFrame) -> list[DQEvent]:
    """The error-severity events of check_bars (duplicate_ts, bid_gt_ask, non_monotonic, in that order, same
    timestamps and details) without the warning checks and the flagged copy: the engine checks every batch of newly
    completed minutes, a few rows, where check_bars' fixed pandas cost dominated."""
    if not len(bars):
        return []
    arrived = pd.DatetimeIndex(pd.to_datetime(bars["ts_utc"], utc=True))
    order = np.argsort(epoch_ns(arrived), kind="stable")
    ts = arrived[order]
    ns = epoch_ns(ts)
    events: list[DQEvent] = []
    dup = np.zeros(len(ns), dtype=bool)
    dup[:-1] = ns[:-1] == ns[1:]                  # sorted: a stamp seen again later (duplicated(keep="last"))
    if dup.any():
        events.append(DQEvent(ts_utc=ts[dup][0], check="duplicate_ts", severity="error", detail=f"{int(dup.sum())} duplicate bar timestamps"))
    ask, bid = bars["ask_close"].to_numpy()[order], bars["bid_close"].to_numpy()[order]
    bad = (ask < bid) | (bars["spread_mean"].to_numpy()[order] < 0)
    if bad.any():
        events.append(DQEvent(ts_utc=ts[bad][0], check="bid_gt_ask", severity="error", detail=f"{int(bad.sum())} bars with bid>ask"))
    mono = np.diff(epoch_ns(arrived)) < 0
    if mono.any():
        events.append(DQEvent(ts_utc=arrived[1:][mono][0], check="non_monotonic", severity="error",
                              detail=f"{int(mono.sum())} bars arrived out of time order"))
    return events


def stale_feed(last_tick_utc: pd.Timestamp, now_utc: pd.Timestamp, *, limit_seconds: int = 90,
               sessions: SessionTable = DEFAULT_SESSIONS) -> bool:
    if not sessions.is_open(pd.DatetimeIndex([now_utc]))[0]:
        return False
    return (now_utc - last_tick_utc).total_seconds() > limit_seconds


def events_frame(events: list[DQEvent]) -> pd.DataFrame:
    if not events:
        return pd.DataFrame(columns=["ts_utc", "check", "severity", "detail"])
    return pd.DataFrame([e.model_dump() for e in events])
