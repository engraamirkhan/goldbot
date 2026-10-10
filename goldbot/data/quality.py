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


def _mid_series(bars: pd.DataFrame) -> pd.Series:
    """Mid close per minute stamp (bid/ask closes; a bid-only frame such as the broker's own M1 uses its close)."""
    if bars.empty:
        return pd.Series(dtype=float)
    ts = pd.DatetimeIndex(pd.to_datetime(bars["ts_utc"], utc=True)).as_unit("ns")
    if "bid_close" in bars.columns and "ask_close" in bars.columns:
        mid = (bars["bid_close"].astype(float) + bars["ask_close"].astype(float)) / 2
    else:
        mid = bars["close"].astype(float)
    s = pd.Series(mid.to_numpy(), index=ts)
    return s[~s.index.duplicated(keep="last")].sort_index()


SPIKE_MATCH_FRACTION = 0.5     # the other feed's move must be at least this share of the spike, in the same direction
SPIKE_MATCH_MINUTES = 1        # ... within this many minutes of the spike's minute


def spike_matched(r_spike: float, other_mid: pd.Series, ts: pd.Timestamp, *, window_minutes: int = SPIKE_MATCH_MINUTES,
                  match_frac: float = SPIKE_MATCH_FRACTION, tf_seconds: int = 60) -> bool:
    """True when the other feed shows a matching move: a log mid return in the same direction of at least
    `match_frac` x |r_spike|, either in one minute within +-window_minutes of `ts` or over the whole window
    (close of ts - window - 1 to close of ts + window, so a move the other feed spread over two minutes counts).
    Minutes the other feed lacks count as no move."""
    if other_mid.empty or not np.isfinite(r_spike) or r_spike == 0:
        return False
    step = pd.Timedelta(seconds=tf_seconds)
    t = pd.Timestamp(ts).tz_convert("UTC") if pd.Timestamp(ts).tzinfo else pd.Timestamp(ts, tz="UTC")
    grid = pd.date_range(t - (window_minutes + 1) * step, t + window_minutes * step, freq=step).as_unit("ns")
    m = other_mid.reindex(grid)
    r = np.log(m.to_numpy(dtype=float))
    moves = list(np.diff(r)) + [r[-1] - r[0]]
    need = match_frac * abs(r_spike)
    return any(np.isfinite(x) and np.sign(x) == np.sign(r_spike) and abs(x) >= need for x in moves)


def confirm_spikes(bars: pd.DataFrame, events: list[DQEvent], other: pd.DataFrame, *,
                   window_minutes: int = SPIKE_MATCH_MINUTES, match_frac: float = SPIKE_MATCH_FRACTION,
                   tf_seconds: int = 60) -> tuple[pd.DataFrame, list[DQEvent]]:
    """Design (Data quality, D20): a spike is ">12 sigma of trailing hour with no match on the other feed". Where both
    feeds are available, a spike check_bars flagged on `bars` stays a warning only if `other` shows no matching move
    (`spike_matched`) in the same minute +-window_minutes; a matched spike is a real market move, so its event is
    dropped and `warning:spike` is removed from the bar's dq_flag. Every other event and flag is returned unchanged.
    With one feed only, call check_bars alone (its behaviour does not change)."""
    spikes = [e for e in events if e.check == "spike"]
    if not spikes or bars.empty:
        return bars, list(events)
    mid = _mid_series(bars)
    r = np.log(mid).diff()
    other_mid = _mid_series(other)
    out = bars.copy()
    stamps = pd.DatetimeIndex(pd.to_datetime(out["ts_utc"], utc=True)).as_unit("ns")
    kept: list[DQEvent] = []
    for e in events:
        if e.check != "spike":
            kept.append(e)
            continue
        t = pd.Timestamp(e.ts_utc).tz_convert("UTC")
        rs = float(r.get(t.as_unit("ns"), np.nan))
        if not spike_matched(rs, other_mid, t, window_minutes=window_minutes, match_frac=match_frac,
                             tf_seconds=tf_seconds):
            kept.append(e)
            continue
        for i in np.flatnonzero(stamps == t):
            flags = [f for f in str(out.at[int(i), "dq_flag"]).split(";") if f and f != "warning:spike"]
            out.at[int(i), "dq_flag"] = ";".join(flags)
    return out, kept
