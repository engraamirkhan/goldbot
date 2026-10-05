"""Trend specialist (design table: 1h primary, 15m execution).

Trigger on a 1h close: the close is on the EMA-50 side that matches the 4h EMA-50 slope, ADX(14) > 20, price pulled
back to within 0.5 ATR of the EMA in the previous `pullback_bars` bars, and this bar closes back in the trend
direction. Barriers 2.5 / 1.25 x ATR(1h), 48 h. The design's trail (1.5 ATR once 1.25 ATR in profit) is the live
exit policy; labels use the barriers, which is the conservative bound on what the trail can do.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

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

    @property
    def label_spec(self) -> BarrierSpec:
        c = self.config
        return BarrierSpec(target_atr=c["target_atr"], stop_atr=c["stop_atr"], max_bars=c["max_bars"], name="trend")

    def exit_policy(self) -> dict[str, Any]:
        return {"type": "trail", "trail_atr": 1.5, "activate_after_atr": 1.25}

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
