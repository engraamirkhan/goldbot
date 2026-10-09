"""Session intraday momentum specialist `intraday_momentum` (proposal P4; Gao, Han, Li and Zhou 2018 found the first
half-hour return predicts the last half-hour on US equity ETFs; for gold this is a hypothesis to test).

Rule: on each trading day, at the middle of the chosen session (`formation_frac` of its length, rounded down to a whole
bar), enter in the direction of the move from the session open to that time; exit at the session close (time barrier)
unless the protective stop or the target is hit first. Sessions are on their local wall clocks with DST handled by the
zone (`goldbot.data.calendar.LOCAL_SESSIONS`): London 08:00-16:30 Europe/London, New York 08:30-16:00
America/New_York. One session per configuration (New York by default; `{"session": "london"}` is the other trial),
so the time barrier is one fixed number of bars. A day without the session's opening bar in the data is skipped.

Defaults on 15m bars: New York decision at 12:15 local (225 minutes formation), then the 15 bars to the 16:00 close;
the protective stop is wide (2.0 x ATR(15m)) so most trades live to the time exit or the target (3.0 x ATR(15m), about
ten times a round trip of a few tenths of a dollar against a 15m ATR of one to several dollars). `min_move_atr` 0
trades every day the opening bar exists: about 250 events a year.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from goldbot.config import tf_seconds
from goldbot.data.calendar import LOCAL_SESSIONS, LocalSession
from goldbot.features.technical import atr
from goldbot.labels.triple_barrier import BarrierSpec
from goldbot.specialists.base import Specialist, register


@register
class IntradayMomentumSpecialist(Specialist):
    family = "intraday_momentum"
    timeframe = "15m"
    default_config = {
        "session": "newyork",
        "formation_frac": 0.5,       # decision after this fraction of the session (rounded down to a whole bar)
        "min_move_atr": 0.0,         # |close - session open| at the decision must be at least this many ATR
        "target_atr": 3.0,
        "stop_atr": 2.0,
        "max_bars": 96,              # cap only: the time barrier is the session close (see label_spec)
    }
    # declared meta-model inputs, chosen by rationale (the size of the session's first-part move and where the day
    # stands in both sessions, short and multi-day momentum, the volatility regime, stretch, flow and the higher
    # timeframes); the model adds `side`, <= 40 in all
    model_features = (
        "im_ny_ret_atr", "im_ldn_ret_atr", "im_ny_min", "im_ldn_min", "tsmom_z_24", "tsmom_z_120", "tsmom_score",
        "ret_1", "ret_4", "ret_16", "ret_96", "atr14_pct", "atr_ratio_14_100", "rv_ratio", "vol_tercile",
        "parkinson_20", "dist_ema20_atr", "dist_ema50_atr", "slope_ema50", "ribbon_state", "adx14", "donchian_pos_20",
        "bb_z_20", "rsi14", "dist_vwap48_atr", "range_width_24_atr", "tick_vol_ratio_20", "tick_count_z_48",
        "spread_atr", "dist_res_atr", "dist_sup_atr", "dow", "us_dst", "h1_adx14", "h1_slope_ema50",
        "h4_slope_ema50", "h4_dist_ema50_atr", "d1_dist_ema50_atr", "d1_slope_ema50",
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if self.config["session"] not in LOCAL_SESSIONS:
            raise ValueError(f"intraday_momentum: unknown session {self.config['session']!r}; known: {sorted(LOCAL_SESSIONS)}")
        if not 0.0 < float(self.config["formation_frac"]) < 1.0:
            raise ValueError("intraday_momentum: formation_frac must be in (0, 1)")
        if self.bars_to_close < 2:
            raise ValueError("intraday_momentum: the decision leaves less than two bars before the session close")

    @property
    def session(self) -> LocalSession:
        return LOCAL_SESSIONS[self.config["session"]]

    @property
    def bar_minutes(self) -> int:
        return tf_seconds(self.timeframe) // 60

    @property
    def decision_minute(self) -> int:
        """Minutes after the session open at which the decision bar closes (a whole number of bars)."""
        bars = int(self.session.length_minutes * float(self.config["formation_frac"]) // self.bar_minutes)
        return max(1, bars) * self.bar_minutes

    @property
    def bars_to_close(self) -> int:
        return (self.session.length_minutes - self.decision_minute) // self.bar_minutes

    @property
    def label_spec(self) -> BarrierSpec:
        c = self.config
        # entry at the decision bar's close; triple_barrier exits at the close of bar idx + 1 + max_bars, which is the
        # session close when max_bars = bars_to_close - 1
        hold = min(int(c["max_bars"]), self.bars_to_close - 1)
        return BarrierSpec(target_atr=c["target_atr"], stop_atr=c["stop_atr"], max_bars=hold, name="intraday_momentum")

    def exit_policy(self) -> dict[str, Any]:
        return {"type": "barrier", "time_exit": f"{self.session.name} session close {self.session.close:%H:%M} {self.session.tz}"}

    def candidates(self, mid_bars: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
        ts = pd.DatetimeIndex(pd.to_datetime(mid_bars["ts_utc"], utc=True))
        since, _ = self.session.clock(ts)
        sopen = self.session.open_price(mid_bars)
        close = mid_bars["close"].to_numpy(dtype=float)
        a = (features["atr14"] if "atr14" in features.columns else atr(mid_bars, 14)).to_numpy(dtype=float)
        decision = since == self.decision_minute - self.bar_minutes          # the bar that closes at the decision time
        move = close - sopen
        side = np.sign(np.nan_to_num(move)).astype(int)
        big = np.abs(move) >= float(self.config["min_move_atr"]) * a
        hit = decision & np.isfinite(move) & np.isfinite(a) & big & (side != 0)
        return pd.DataFrame({"idx": np.flatnonzero(hit), "side": side[hit]})
