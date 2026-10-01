"""Data-quality checks run on every ingest batch.

Warnings commit with a `dq_flag`; errors quarantine the batch. The trading loop never opens a
position on a bar carrying an error flag.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from goldbot.data.calendar import DEFAULT_SESSIONS, SessionTable


@dataclass
class DQEvent:
    ts_utc: pd.Timestamp
    check: str
    severity: str  # "warning" | "error"
    detail: str


def check_bars(bars: pd.DataFrame, *, tf_seconds: int = 60, sessions: SessionTable = DEFAULT_SESSIONS,
               spike_sigma: float = 12.0, max_gap_bars: int = 3) -> tuple[pd.DataFrame, list[DQEvent]]:
    events: list[DQEvent] = []
    b = bars.sort_values("ts_utc").reset_index(drop=True).copy()
    b["dq_flag"] = ""
    if b.empty:
        return b, events
    ts = pd.DatetimeIndex(b["ts_utc"])

    dup = b["ts_utc"].duplicated(keep="last")
    if dup.any():
        events.append(DQEvent(ts[dup.values][0], "duplicate_ts", "error", f"{int(dup.sum())} duplicate bar timestamps"))
        b.loc[dup, "dq_flag"] = "error:duplicate"

    bad_spread = (b["ask_close"] < b["bid_close"]) | (b["spread_mean"] < 0)
    if bad_spread.any():
        events.append(DQEvent(ts[bad_spread.values][0], "bid_gt_ask", "error", f"{int(bad_spread.sum())} bars with bid>ask"))
        b.loc[bad_spread, "dq_flag"] = "error:bid_gt_ask"

    mono = np.diff(ts.asi8) <= 0
    if mono.any():
        events.append(DQEvent(ts[1:][mono][0], "non_monotonic", "error", "timestamps not increasing"))

    # gaps inside open sessions
    gap = np.diff(ts.asi8) / 1e9
    big = gap > tf_seconds * max_gap_bars
    if big.any():
        for i in np.flatnonzero(big):
            # only a problem if the market was open throughout
            probe = pd.date_range(ts[i], ts[i + 1], freq=f"{tf_seconds}s", inclusive="neither")
            if len(probe) and sessions.is_open(probe).all():
                events.append(DQEvent(ts[i], "gap_in_session", "warning", f"{int(gap[i] // tf_seconds)} missing bars after {ts[i]}"))
                b.loc[i, "dq_flag"] = "warning:gap"

    # spikes: |log return| > k sigma of trailing 60 bars
    mid = (b["bid_close"] + b["ask_close"]) / 2
    r = np.log(mid).diff()
    sigma = r.shift(1).rolling(60, min_periods=20).std()  # trailing, excluding the bar under test
    spike = (r.abs() > spike_sigma * sigma) & sigma.notna()
    if spike.any():
        for i in np.flatnonzero(spike.values):
            events.append(DQEvent(ts[i], "spike", "warning", f"return {r.iloc[i]:.4f} vs sigma {sigma.iloc[i]:.5f}"))
            b.loc[i, "dq_flag"] = (b.loc[i, "dq_flag"] + ";" if b.loc[i, "dq_flag"] else "") + "warning:spike"

    return b, events


def stale_feed(last_tick_utc: pd.Timestamp, now_utc: pd.Timestamp, *, limit_seconds: int = 90,
               sessions: SessionTable = DEFAULT_SESSIONS) -> bool:
    if not sessions.is_open(pd.DatetimeIndex([now_utc]))[0]:
        return False
    return (now_utc - last_tick_utc).total_seconds() > limit_seconds


def events_frame(events: list[DQEvent]) -> pd.DataFrame:
    if not events:
        return pd.DataFrame(columns=["ts_utc", "check", "severity", "detail"])
    return pd.DataFrame([e.__dict__ for e in events])
