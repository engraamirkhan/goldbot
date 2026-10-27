"""Arm exits that run under the engine's Friday weekend cut (ARM-SPEC docs/research/arms-2026-10-25.md 8.14 (2) for
WFRI, 8.15 for PDC4 and DOND). The cut is a safety rule and is never loosened for an arm: the arm's exit changes.

The engine's weekend rule (`Engine._weekend_rule`) runs at `EngineConfig.weekend_cut` 21:30 server time (`server_tz`
Europe/Athens): "losers are closed and winners' stops moved to lock in half the open profit (never loosened)", and no
new entry is sent after it. `exploration.crosses_friday` refuses every entry whose maximum hold reaches the cut, unless
the arm's rule declares the cut part of its own exit (`arm_rules.follows_friday_cut`); such an entry may hold across
the cut but never into the weekend (the server's Saturday 00:00).

8.15 (PDC4 4h, DOND 1d): "the time exit is the earlier of the frozen hold and the end of the week: the last close of
the arm's timeframe at least WEEK_END_SLACK before the server's Saturday 00:00 (follows_friday_cut; no position is held
into the weekend). Losers close at the cut, winners hold under the cut's tightened stop to that time exit. No entry
after the cut or when no close of the timeframe is left in the week." `capped` applies it to the rule's frame (the
history run and live read the same frame), with each candidate's `cut_at` (UTC); `walk_cut` replays the cut on the 1m
mid path (history), as the engine's weekend rule does live; `time_exit_at` is the engine's wall-clock time exit for
these arms (`exploration.exits`), so a hold counted in decision bars cannot run past the week across the daily breaks.
PDC1 (1h) keeps its frozen hold and the plain weekend refusal."""
from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from goldbot.config import tf_seconds
from goldbot.data.timeutil import server_wall, utc_index

CUT_TZ, CUT = "Europe/Athens", (21, 30)     # = EngineConfig.server_tz / weekend_cut (a test holds them equal)
CUT_KEEP = 0.5                              # runner._weekend_rule: a winner's stop locks in half the open profit
# the time exit is sent at least this long before the server's Saturday 00:00 (the week closes 23:57 server); the
# engine sends an order within `exploration.stale_s` (60 s at 15m and above) of its decision close
WEEK_END_SLACK = pd.Timedelta(minutes=15)
# (rule, timeframe) whose hold is capped at the week's end under the cut (8.15); WFRI's `friday_long` declares the cut
# for every timeframe (`arm_rules_clock_box.FOLLOWS_FRIDAY_CUT`)
CUT_CAPPED = frozenset({("pdc_break", "4h"), ("donchian_break", "1d")})


def week_bounds(close: pd.DatetimeIndex) -> tuple[pd.DatetimeIndex, pd.DatetimeIndex]:
    """Per decision close (UTC): the Friday cut of its server week and the server's following Saturday 00:00, both UTC
    (`exploration.crosses_friday`'s clock: a close on Saturday has its own day as the week's end, already passed)."""
    srv = server_wall(close, CUT_TZ)
    day = srv.normalize()
    ahead = pd.to_timedelta((5 - srv.dayofweek.to_numpy()) % 7, unit="D")
    zone = ZoneInfo(CUT_TZ)
    saturday = (day + ahead).tz_localize(zone).tz_convert("UTC")
    cut = (day + ahead - pd.Timedelta(days=1) + pd.Timedelta(hours=CUT[0], minutes=CUT[1])).tz_localize(zone)
    return cut.tz_convert("UTC"), saturday


def week_hold(close: pd.DatetimeIndex, hold: np.ndarray, tf: str) -> np.ndarray:
    """8.15 per decision close: min(frozen hold, whole bars of `tf` whose close is at least WEEK_END_SLACK before the
    server's Saturday 00:00); 0 (no entry) at or after the week's cut or with no such bar left."""
    if not len(close):
        return np.zeros(0, dtype=int)
    cut, saturday = week_bounds(close)
    left = (saturday - WEEK_END_SLACK - close) // pd.Timedelta(seconds=tf_seconds(tf))
    room = np.maximum(np.asarray(left, dtype=np.int64), 0)
    return np.where(np.asarray(close >= cut), 0, np.minimum(np.asarray(hold, dtype=np.int64), room)).astype(int)


def capped(frame: pd.DataFrame, close_ts: pd.DatetimeIndex, tf: str) -> pd.DataFrame:
    """The rule's candidates under 8.15: `max_bars` from the week-capped hold, the week's `cut_at` (UTC), and the
    candidates the engine would refuse (after the cut, no bar left in the week) dropped."""
    if frame.empty:
        return frame.assign(cut_at=pd.Series(dtype="datetime64[ns, UTC]"))
    close = close_ts[frame["idx"].to_numpy(dtype=int)]
    hold = week_hold(close, frame["max_bars"].to_numpy(dtype=int) + 1, tf)
    cut, _ = week_bounds(close)
    out = frame.assign(max_bars=hold - 1, cut_at=cut)
    return out[hold > 0].reset_index(drop=True)


def walk_cut(path: pd.DataFrame, entry: float, side: int, risk: float, cut_at: pd.Timestamp, exit_at: pd.Timestamp,
             target_r: float) -> dict[str, Any]:
    """One trade on its 1m mid path from the entry minute (`path`: ts_utc, open, high, low, close) under the engine's
    Friday weekend cut: at the open of the first minute at or after `cut_at` a loser (side x (price - entry) <= 0) is
    closed there (`weekend_loser`) and a winner's stop becomes the tighter of its stop and round(entry + 0.5 x (price -
    entry), 2); then stop first in a minute (filled at the stop, or at the minute's open when it gapped through), the
    target (`target_r` R), the time exit at the close of the minute ending at `exit_at`."""
    ts = utc_index(path["ts_utc"])
    o, h, lo, c = (path[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    stop, cut_done = entry - side * risk, False
    target = entry + side * target_r * risk
    for j in range(len(path)):
        if not cut_done and ts[j] >= cut_at:
            cut_done = True
            if side * (o[j] - entry) <= 0:
                return {"exit": o[j], "kind": "weekend_loser", "at": ts[j], "stop": stop}
            locked = round(entry + CUT_KEEP * (o[j] - entry), 2)
            stop = max(stop, locked) if side > 0 else min(stop, locked)
        adv, fav = (lo[j], h[j]) if side > 0 else (h[j], lo[j])
        if side * (adv - stop) <= 0:
            return {"exit": stop if side * (o[j] - stop) > 0 else o[j], "kind": "stop", "at": ts[j], "stop": stop}
        if side * (fav - target) >= 0:
            return {"exit": target, "kind": "target", "at": ts[j], "stop": stop}
        if ts[j] + pd.Timedelta(minutes=1) >= exit_at:
            return {"exit": c[j], "kind": "time", "at": ts[j] + pd.Timedelta(minutes=1), "stop": stop}
    return {"exit": c[-1] if len(c) else entry, "kind": "open", "at": ts[-1] if len(ts) else None, "stop": stop}


def time_exit_at(signal_close: pd.Timestamp | None, hold_s: float) -> pd.Timestamp | None:
    """The wall-clock time exit of an entry under the cut: its signal close plus its (week-capped) hold, the history
    run's close of bar i + hold within one week; None when the signal close is unknown (the bar count still exits)."""
    return None if signal_close is None else signal_close + pd.Timedelta(seconds=hold_s)
