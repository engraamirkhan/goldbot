"""Breakout specialist (design table: 15m/1h; implemented on 1h bars).

Trigger: the previous `range_bars` (>= 8) 1h bars form a range narrower than `max_range_atr` (2.0) x ATR(14) (8 bars
of a random walk span about 3 ATR, so this is a compressed range), and this bar closes outside it with tick volume above `min_tick_ratio` (1.5) x its 20-bar median. Barriers 2.0 / 1.0 x
ATR(1h), 24 h. The design's exit (half at 1.0 ATR, rest trailed at 1.0 ATR) is the exit policy, run identically by
the labels, the shadow book and the live engine (labels.exit_policy); the 2.0 ATR target stays as the cap on the rest.
The design's "stop inside range" is not implemented: the stop is the ATR barrier.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from goldbot.labels.exit_policy import ExitPolicy
from goldbot.labels.triple_barrier import BarrierSpec
from goldbot.specialists.base import Specialist, register


@register
class BreakoutSpecialist(Specialist):
    family = "breakout"
    timeframe = "1h"
    timeframes = ("15m",)
    default_config = {
        "range_bars": 8,
        "max_range_atr": 2.0,
        "min_tick_ratio": 1.5,
        "target_atr": 2.0,
        "stop_atr": 1.0,
        "max_bars": 24,
    }
    # declared meta-model inputs, chosen by rationale (the range being broken, the volume and spread of the break, the candle, nearby levels and trend context); the model adds `side`, <= 40 in all
    model_features = (
        "range_width_8_atr", "range_width_24_atr", "range_width_96_atr", "dist_high_8_atr", "dist_low_8_atr",
        "dist_high_24_atr", "dist_low_24_atr", "compression_8_96", "tick_vol_ratio_20", "tick_count_z_48",
        "spread_atr", "spread_rel_median_48", "atr14_pct", "atr_ratio_14_100", "rv_ratio", "vol_tercile",
        "parkinson_20", "adx14", "dist_ema50_atr", "slope_ema50", "ribbon_state", "ret_1", "ret_4", "ret_16",
        "body_pct", "upper_wick_pct", "lower_wick_pct", "dist_res_atr", "dist_sup_atr", "levels_within_1atr",
        "session_id", "h4_adx14", "h4_dist_ema50_atr", "h4_slope_ema50", "h4_ribbon_state", "h4_vol_tercile",
        "d1_dist_ema50_atr", "d1_slope_ema50", "d1_atr_ratio_14_100",
    )

    @property
    def label_spec(self) -> BarrierSpec:
        c = self.config
        return BarrierSpec(target_atr=c["target_atr"], stop_atr=c["stop_atr"], max_bars=c["max_bars"], name="breakout")

    @property
    def exit_spec(self) -> ExitPolicy:
        # design: half at 1.0 ATR, rest trailed at 1.0 ATR (the trail arms where the half is taken)
        return ExitPolicy(scale_atr=1.0, scale_fraction=0.5, trail_atr=1.0, trail_after_atr=1.0)

    def exit_policy(self) -> dict[str, Any]:
        return {"type": "scale_out_trail", **self.exit_spec.model_dump(exclude_defaults=True)}

    def candidates(self, mid_bars: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
        c = self.config
        if "atr14" not in features.columns or "tick_vol_ratio_20" not in features.columns:
            return pd.DataFrame({"idx": pd.Series(dtype=int), "side": pd.Series(dtype=int)})
        n = int(c["range_bars"])
        hi = mid_bars["high"].rolling(n, min_periods=n).max().shift(1)    # the range before this bar
        lo = mid_bars["low"].rolling(n, min_periods=n).min().shift(1)
        atr = features["atr14"].astype(float).shift(1)
        tight = (hi - lo) < c["max_range_atr"] * atr
        close = mid_bars["close"]
        side = pd.Series(np.where(close > hi, 1, np.where(close < lo, -1, 0)), index=mid_bars.index)
        loud = features["tick_vol_ratio_20"].astype(float) > c["min_tick_ratio"]
        hit = (tight & (side != 0) & loud).fillna(False).astype(bool)
        return pd.DataFrame({"idx": np.flatnonzero(hit.to_numpy()), "side": side[hit].astype(int).to_numpy()})
