"""Breakout specialist (design table: 15m/1h; implemented on 1h bars).

Trigger: the previous `range_bars` (>= 8) 1h bars form a range narrower than `max_range_atr` (1.2) x ATR, and this
bar closes outside it with tick volume above `min_tick_ratio` (1.5) x its 20-bar median. Barriers 2.0 / 1.0 x
ATR(1h), 24 h. The design's live exit (half at 1.0 ATR, rest trailed at 1.0 ATR; stop inside the range) is the exit
policy; labels use the barriers.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from goldbot.labels.triple_barrier import BarrierSpec
from goldbot.specialists.base import Specialist, register


@register
class BreakoutSpecialist(Specialist):
    family = "breakout"
    timeframe = "1h"
    default_config = {
        "range_bars": 8,
        "max_range_atr": 1.2,
        "min_tick_ratio": 1.5,
        "target_atr": 2.0,
        "stop_atr": 1.0,
        "max_bars": 24,
    }

    @property
    def label_spec(self) -> BarrierSpec:
        c = self.config
        return BarrierSpec(target_atr=c["target_atr"], stop_atr=c["stop_atr"], max_bars=c["max_bars"], name="breakout")

    def exit_policy(self) -> dict[str, Any]:
        return {"type": "scale_out_trail", "first_target_atr": 1.0, "first_fraction": 0.5, "trail_atr": 1.0,
                "stop": "inside_range"}

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
