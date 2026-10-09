"""Time-series momentum specialist `tsmom` (proposal P4; Moskowitz, Ooi and Pedersen 2012; Hurst, Ooi and Pedersen
2017): trade in the direction of the trailing return.

Signal: the mean of the volatility-scaled trailing log returns over 24, 120 and 480 hours (1, 5 and 20 trading days;
each return divided by the 96-hour standard deviation of hourly returns x sqrt(horizon)), so a value of 1 is a
one-sigma move. Long when it is >= `min_score`, short when <= -`min_score`.

Firing: on a fixed schedule, at most once per `schedule_h` hours (bars that close on a multiple of 4 UTC hours; every
bar on 4h), not on sign changes. A sign change happens a few dozen times a year at these horizons, which would give
far fewer than the screen's 1,000 events in 2010-2025; a schedule samples the signal evenly, and one position at a
time (`labels.one_at_a_time`) keeps the trades from overlapping.

Barriers: target 3.0 x ATR(1h), stop 1.5 x ATR(1h), 48 bars (two days). A 1h ATR on gold is several dollars against a
round trip of well under one dollar, so the target is many times the cost. Holding two days crosses up to two
rollovers (four on a Wednesday triple); the net labels pay swap for each (settings `costs.swap_*`), the gross screen
does not.

Timeframes: 1h (default), 4h or 1d. Horizons are in hours (24 per trading day), converted to bars of the decision
timeframe; on 4h set `max_bars` to 12 for the same two-day horizon (the population's timeframe mutation rescales it
automatically).

Daily option (horizon study, `timeframe: "1d"`): the decision bar is the feature-day (COMEX settlement to settlement,
13:30 New York), with no higher context timeframe (`features.mtf.context_tfs`). Its own defaults
(`timeframe_defaults`): vol-scaled returns over 20, 60 and 120 trading days with 60-day volatility, evaluated at every
settlement (a daily bar is its own schedule), target 3.0 x ATR(daily), stop 1.5 x ATR(daily), 10 bars (two weeks),
one position at a time; walk-forward train 60 / test 12 / step 12 months on an expanding window
(`research.walkforward.WINDOWS["1d"]`). Holding up to ten days pays up to ~14 nights of swap, charged in the net
labels.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from goldbot.config import tf_seconds
from goldbot.features.momentum import tsmom_score
from goldbot.labels.triple_barrier import BarrierSpec
from goldbot.specialists.base import Specialist, register


@register
class TimeSeriesMomentumSpecialist(Specialist):
    family = "tsmom"
    timeframe = "1h"
    timeframes = ("4h", "1d")
    default_config = {
        "lb_fast_h": 24,
        "lb_mid_h": 120,
        "lb_slow_h": 480,
        "vol_window_h": 96,
        "min_score": 0.5,
        "schedule_h": 4,
        "target_atr": 3.0,
        "stop_atr": 1.5,
        "max_bars": 48,
    }
    timeframe_defaults = {
        "1d": {"lb_fast_h": 20 * 24, "lb_mid_h": 60 * 24, "lb_slow_h": 120 * 24, "vol_window_h": 60 * 24,
               "schedule_h": 24, "target_atr": 3.0, "stop_atr": 1.5, "max_bars": 10},
    }
    # declared meta-model inputs, chosen by rationale (the momentum signal at each horizon and how much they agree,
    # the volatility regime, trend quality on the bar and on the 4h/daily bars, stretch and nearby levels); the model
    # adds `side`, <= 40 in all
    model_features = (
        "tsmom_z_24", "tsmom_z_120", "tsmom_z_480", "tsmom_score", "tsmom_agree", "ret_1", "ret_4", "ret_16", "ret_96",
        "atr14_pct", "atr_ratio_14_100", "rv_ratio", "vol_tercile", "parkinson_20", "dist_ema20_atr", "dist_ema50_atr",
        "dist_ema200_atr", "slope_ema50", "slope_ema200", "ribbon_state", "adx14", "donchian_pos_20", "bb_z_20",
        "rsi14", "range_width_96_atr", "structure_state", "dist_res_atr", "dist_sup_atr", "tick_vol_ratio_20",
        "session_id", "dow", "h4_adx14", "h4_slope_ema50", "h4_dist_ema50_atr", "h4_ribbon_state",
        "d1_dist_ema50_atr", "d1_slope_ema50", "d1_adx14", "d1_atr_ratio_14_100",
    )

    @property
    def label_spec(self) -> BarrierSpec:
        c = self.config
        return BarrierSpec(target_atr=c["target_atr"], stop_atr=c["stop_atr"], max_bars=c["max_bars"], name="tsmom")

    def exit_policy(self) -> dict[str, Any]:
        return {"type": "barrier"}

    def _bars(self, hours: float) -> int:
        return max(1, int(round(hours * 3600 / tf_seconds(self.timeframe))))

    def candidates(self, mid_bars: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
        c = self.config
        horizons = tuple(self._bars(c[k]) for k in ("lb_fast_h", "lb_mid_h", "lb_slow_h"))
        score = tsmom_score(mid_bars["close"].reset_index(drop=True), horizons, max(2, self._bars(c["vol_window_h"])))
        # schedule: the bar's close (open + timeframe) falls on a whole multiple of schedule_h UTC hours; a bar as long as
        # the schedule (4h with schedule_h 4, a daily bar closing at settlement) is on it every time
        close_ts = pd.DatetimeIndex(pd.to_datetime(mid_bars["ts_utc"], utc=True)) + pd.Timedelta(seconds=tf_seconds(self.timeframe))
        step = int(c["schedule_h"])
        if tf_seconds(self.timeframe) >= max(step, 1) * 3600:
            on_schedule = np.ones(len(close_ts), dtype=bool)
        else:
            on_schedule = np.asarray((close_ts.minute == 0) & (close_ts.hour % max(step, 1) == 0))
        s = score.to_numpy(dtype=float)
        side = np.where(s >= c["min_score"], 1, np.where(s <= -c["min_score"], -1, 0))
        hit = on_schedule & (side != 0) & np.isfinite(s)
        return pd.DataFrame({"idx": np.flatnonzero(hit), "side": side[hit].astype(int)})
