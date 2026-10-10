"""Asia-session drift specialist `asia_drift` (H-04, docs/research/preregistration-2027Q1.md; SoA C1-2): Asian
physical and ETF demand against Western selling, so gold is said to drift up while Asia trades and London is shut.

Rule, exactly as pre-registered: long at 00:00 UTC, flat at 07:00 UTC (before London), stop 1.5 x ATR(1h), every
trading day, 1h decision bars, long only, one position at a time. No target: the trade lives until the stop or the
07:00 time exit (the barrier spec needs a target, so it is set at NO_TARGET_ATR, which 7 hourly bars cannot reach).

Point in time. The decision bar is the 1h bar that closes at 00:00 UTC (23:00-00:00); the labels enter at its close,
which is the open of the first 1h bar at or after 00:00 UTC, on the paying side (ask). ATR(14) is the decision bar's,
from completed bars only, frozen for the trade's life. Nothing after the decision bar's close is read.

Exit. A time barrier in 1h bars, part of the label spec (no exit policy), so the labels, the shadow book and the live
engine count the same bars: entry at 00:00, the time exit at the close of the 7th bar after it (max_bars 6:
triple_barrier exits at the close of bar idx + 1 + max_bars), 07:00 UTC. UTC has no DST, so entry and exit are the
same UTC hours all year; London opens at 08:00 local, which is 08:00 UTC in winter and 07:00 UTC in summer, so the
trade is flat before (winter) or at (summer) the London open.

Market closed. No entry when the market is closed at 00:00 UTC by the session calendar (`data.calendar.
DEFAULT_SESSIONS.is_open`: the weekend and the daily break on the broker's server clock), on the all-day closures of
XAUUSD CFDs (CLOSED_DAYS: 25 December and 1 January, UTC dates), or when the decision bar is missing (no 23:00 UTC bar
in the data: the market did not trade into 00:00). 00:00 UTC is 02:00 (winter) / 03:00 (summer) on the server clock
(Europe/Athens), outside the daily break (23:59-01:02 server), so every weekday from Monday to Friday is a candidate.

No swap. The position is open from 00:00 to 07:00 UTC, 02:00-09:00 or 03:00-10:00 server time, so it never holds
through the server midnight (the rollover): `labels.triple_barrier.rollover_nights` is 0 for every trade
(tests/test_asia_drift.py).

Research only until it passes: `screening` keeps it out of the default population founders, the pooled models, the
research director / label grid / analyst (`ops.jobs.research_families`) and the model stage of `research_pass.py`
(rule-only screen). Research keeps an entry only when its whole 00:00-07:00 UTC window is in the data
(`complete_windows`). The label spec says it has no reachable target (`has_target` False), so the RiskGate refuses its
intents (`time_exit_ev_unsupported`) until the EV of a time-exit rule is implemented (HANDOFF).
"""
from __future__ import annotations

from datetime import time
from typing import Any

import numpy as np
import pandas as pd

from goldbot.config import tf_seconds
from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.data.timeutil import utc_index
from goldbot.features.technical import atr
from goldbot.labels.triple_barrier import BarrierSpec
from goldbot.specialists.base import Specialist, register

ENTRY_UTC = time(0, 0)             # pre-registered: long at 00:00 UTC
EXIT_UTC = time(7, 0)              # pre-registered: flat at 07:00 UTC, before London
CLOSED_DAYS = ((12, 25), (1, 1))   # (month, day) UTC dates XAUUSD CFDs are shut all day: no entry
NO_TARGET_ATR = 50.0               # the rule has no target; 50 x ATR(1h) is out of reach within 7 hourly bars


@register
class AsiaDriftSpecialist(Specialist):
    family = "asia_drift"
    timeframe = "1h"
    screening = True
    default_config = {
        "stop_atr": 1.5,
        "target_atr": NO_TARGET_ATR,   # no target (module docstring)
        "max_bars": 6,                 # cap only: the time barrier is 07:00 UTC (see label_spec)
    }
    # declared meta-model inputs: a minimal list (H-04 is a rule-only screen first; a model is pre-registered only after
    # the rule passes): recent moves into the Asian open, the volatility regime, the cost of the trade, the weekday and
    # the daily trend; the model adds `side`
    model_features = (
        "ret_1", "ret_4", "ret_16", "ret_96", "atr14_pct", "atr_ratio_14_100", "rv_ratio", "spread_atr", "dow",
        "us_dst", "d1_dist_ema50_atr", "d1_slope_ema50",
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if not float(self.config["stop_atr"]) > 0 or not float(self.config["target_atr"]) > 0:
            raise ValueError("asia_drift: stop_atr and target_atr must be positive")
        if not 1 <= int(self.config["max_bars"]) <= self.hold_bars - 1:
            raise ValueError(f"asia_drift: max_bars must be 1..{self.hold_bars - 1}: the time exit is at "
                             f"{EXIT_UTC:%H:%M} UTC at the latest")

    @property
    def hold_bars(self) -> int:
        """Decision bars from the entry (ENTRY_UTC) to the time exit (EXIT_UTC): 7."""
        minutes = (EXIT_UTC.hour * 60 + EXIT_UTC.minute) - (ENTRY_UTC.hour * 60 + ENTRY_UTC.minute)
        return minutes * 60 // tf_seconds(self.timeframe)

    @property
    def label_spec(self) -> BarrierSpec:
        # entry at the close of the decision bar (00:00); triple_barrier's time exit is the close of bar
        # idx + 1 + max_bars, so max_bars = hold_bars - 1 exits at the close of the bar ending 07:00 (a longer hold is
        # refused by the constructor). No reachable target: has_target False (RiskGate refuses the barrier EV)
        c = self.config
        return BarrierSpec(target_atr=float(c["target_atr"]), stop_atr=float(c["stop_atr"]), max_bars=int(c["max_bars"]),
                           name="asia_drift", has_target=False)

    def complete_windows(self, bars_dec: pd.DataFrame, cands: pd.DataFrame) -> pd.DataFrame:
        """Pre-registered sample rule (quant review M3): keep an entry only when the bar that closes its time barrier
        (the 7th after the decision bar for the default) closes exactly 7 hours after the entry, so the 1h bars from
        00:00 to 07:00 UTC are all there. A data gap or an unlisted closure (e.g. Good Friday) would otherwise carry a
        bar-counted exit past 07:00 UTC. Bar timestamps only: no price is read."""
        if cands.empty:
            return cands
        step = pd.Timedelta(seconds=tf_seconds(self.timeframe))
        ts = utc_index(bars_dec["ts_utc"])
        close = utc_index(bars_dec["visible_at"]) if "visible_at" in bars_dec else ts + step
        n_hold = int(self.config["max_bars"]) + 1
        idx = cands["idx"].to_numpy(dtype=int)
        end = idx + n_hold
        ok = end < len(close)
        good = np.zeros(len(idx), dtype=bool)
        good[ok] = np.asarray(close[end[ok]] - close[idx[ok]] == n_hold * step)
        return cands[good].reset_index(drop=True)

    def exit_policy(self) -> dict[str, Any]:
        return {"type": "barrier", "time_exit": f"{EXIT_UTC:%H:%M} UTC", "target": "none"}  # label_spec.has_target

    def candidates(self, mid_bars: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
        ts = utc_index(mid_bars["ts_utc"])
        step = pd.Timedelta(seconds=tf_seconds(self.timeframe))
        close = utc_index(mid_bars["visible_at"]) if "visible_at" in mid_bars else ts + step
        at_entry = np.asarray((close.hour == ENTRY_UTC.hour) & (close.minute == ENTRY_UTC.minute)
                              & (close.second == 0) & (close - ts == step))
        closed_day = np.zeros(len(ts), dtype=bool)
        for month, day in CLOSED_DAYS:
            closed_day |= np.asarray((close.month == month) & (close.day == day))
        is_open = DEFAULT_SESSIONS.is_open(close) if len(close) else np.zeros(0, dtype=bool)
        a = (features["atr14"] if "atr14" in features.columns else atr(mid_bars, 14)).to_numpy(dtype=float)
        hit = at_entry & is_open & ~closed_day & np.isfinite(a) & (a > 0)
        return pd.DataFrame({"idx": np.flatnonzero(hit), "side": np.ones(int(hit.sum()), dtype=int)})
