"""Mean-reversion specialist (design table: 15m).

Trigger: the close is beyond 2 sigma of the 20-bar band, RSI(14) beyond 25/75 in the same direction, and 1h realised
volatility in its bottom two terciles; trade back towards the mean. Barriers 1.0 / 1.5 x ATR(15m), 12 bars, barrier exit.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from goldbot.labels.triple_barrier import BarrierSpec
from goldbot.specialists.base import Specialist, register


@register
class MeanReversionSpecialist(Specialist):
    family = "mean_reversion"
    timeframe = "15m"
    timeframes = ("1h",)
    default_config = {
        "band_z": 2.0,
        "rsi_low": 25.0,
        "rsi_high": 75.0,
        "max_vol_tercile": 1,     # 0 low, 1 mid, 2 high (1h realised vol over 400 bars)
        "target_atr": 1.0,
        "stop_atr": 1.5,
        "max_bars": 12,
    }
    # declared meta-model inputs, chosen by rationale (how stretched, how fast, how calm, and what the higher timeframes say about fading the move); the model adds `side`, <= 40 in all
    model_features = (
        "bb_z_20", "bb_pctb_20", "rsi14", "rsi14_extreme", "dist_vwap48_atr", "mfi12", "mfi_slope", "ret_1", "ret_4",
        "ret_16", "ret_96", "atr14_pct", "atr_ratio_14_100", "rv_ratio", "vol_tercile", "dist_ema20_atr",
        "dist_ema50_atr", "slope_ema20", "adx14", "donchian_pos_20", "spread_atr", "tick_vol_ratio_20",
        "tick_count_z_48", "dist_res_atr", "dist_sup_atr", "levels_within_1atr", "body_pct", "consec_same_dir",
        "min_since_london_open", "min_since_newyork_open", "dow", "h1_adx14", "h1_dist_ema50_atr", "h1_vol_tercile",
        "h4_slope_ema50", "h4_dist_ema50_atr", "h4_ribbon_state", "d1_dist_ema50_atr", "d1_slope_ema50",
    )

    @property
    def label_spec(self) -> BarrierSpec:
        c = self.config
        return BarrierSpec(target_atr=c["target_atr"], stop_atr=c["stop_atr"], max_bars=c["max_bars"], name="mean_reversion")

    def exit_policy(self) -> dict[str, Any]:
        return {"type": "barrier"}

    def candidates(self, mid_bars: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
        c = self.config
        # calm-market filter: the 1h volatility tercile from context on 15m bars, the bar's own on 1h bars
        vol = "h1_vol_tercile" if "h1_vol_tercile" in features.columns else "vol_tercile"
        need = ("bb_z_20", "rsi14", vol)
        if any(k not in features.columns for k in need):
            return pd.DataFrame({"idx": pd.Series(dtype=int), "side": pd.Series(dtype=int)})
        z = features["bb_z_20"].astype(float)
        rsi = features["rsi14"].astype(float)
        calm = features[vol].astype(float) <= c["max_vol_tercile"]
        short = (z > c["band_z"]) & (rsi > c["rsi_high"])
        long_ = (z < -c["band_z"]) & (rsi < c["rsi_low"])
        side = pd.Series(np.where(long_, 1, np.where(short, -1, 0)), index=features.index)
        hit = ((side != 0) & calm).fillna(False).astype(bool)
        return pd.DataFrame({"idx": np.flatnonzero(hit.to_numpy()), "side": side[hit].astype(int).to_numpy()})
