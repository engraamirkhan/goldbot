"""Trend specialist (design table: 1h primary, 15m execution).

Trigger on a 1h close: the close is on the EMA-50 side that matches the 4h EMA-50 slope, ADX(14) > 20, price pulled
back to within 0.5 ATR of the EMA in the previous `pullback_bars` bars, and this bar closes back in the trend
direction. Barriers 2.5 / 1.25 x ATR(1h), 48 h, with the design's trail (1.5 ATR once 1.25 ATR in profit) as the exit
policy, run identically by the labels, the shadow book and the live engine (labels.exit_policy).
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from goldbot.labels.exit_policy import ExitPolicy
from goldbot.labels.triple_barrier import BarrierSpec
from goldbot.specialists.base import Specialist, register


@register
class TrendSpecialist(Specialist):
    family = "trend"
    timeframe = "1h"
    default_config = {
        "adx_min": 20.0,
        "pullback_atr": 0.5,
        "pullback_bars": 3,
        "target_atr": 2.5,
        "stop_atr": 1.25,
        "max_bars": 48,
    }
    # declared meta-model inputs, chosen by rationale (trend quality on the decision bar and the 4h/daily bars, pullback depth, structure and momentum); the model adds `side`, <= 40 in all
    model_features = (
        "dist_ema20_atr", "dist_ema50_atr", "dist_ema200_atr", "slope_ema20", "slope_ema50", "slope_ema200",
        "ribbon_state", "ribbon_width_atr", "bars_since_ribbon_flip", "adx14", "adx14_bucket", "donchian_pos_20",
        "ret_1", "ret_4", "ret_16", "ret_96", "atr14_pct", "atr_ratio_14_100", "rv_ratio", "vol_tercile", "rsi14",
        "bb_z_20", "mfi12", "structure_state", "dist_last_swing_high_atr", "dist_last_swing_low_atr", "dist_res_atr",
        "dist_sup_atr", "tick_vol_ratio_20", "session_id", "dow", "h4_adx14", "h4_dist_ema50_atr", "h4_slope_ema50",
        "h4_ribbon_state", "h4_vol_tercile", "d1_dist_ema50_atr", "d1_slope_ema50", "d1_adx14",
    )

    @property
    def label_spec(self) -> BarrierSpec:
        c = self.config
        return BarrierSpec(target_atr=c["target_atr"], stop_atr=c["stop_atr"], max_bars=c["max_bars"], name="trend")

    @property
    def exit_spec(self) -> ExitPolicy:
        return ExitPolicy(trail_atr=1.5, trail_after_atr=1.25)      # design: trail at 1.5 ATR once 1.25 ATR in profit

    def exit_policy(self) -> dict[str, Any]:
        return {"type": "trail", **self.exit_spec.model_dump(exclude_defaults=True)}

    def candidates(self, mid_bars: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
        c = self.config
        idx = mid_bars.index
        need = ("h4_slope_ema50", "dist_ema50_atr", "adx14", "ret_1")
        if any(k not in features.columns for k in need):
            return pd.DataFrame({"idx": pd.Series(dtype=int), "side": pd.Series(dtype=int)})
        side = pd.Series(np.sign(features["h4_slope_ema50"].to_numpy(dtype=float)), index=idx)
        dist = features["dist_ema50_atr"].astype(float)
        on_side = np.sign(dist) == side
        trending = features["adx14"].astype(float) > c["adx_min"]
        near = dist.abs().shift(1).rolling(int(c["pullback_bars"]), min_periods=1).min() <= c["pullback_atr"]
        resumes = np.sign(features["ret_1"].astype(float)) == side
        hit = on_side & trending & near & resumes & (side != 0)
        hit = hit.fillna(False).astype(bool)
        return pd.DataFrame({"idx": np.flatnonzero(hit.to_numpy()), "side": side[hit].astype(int).to_numpy()})
