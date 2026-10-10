"""Session-open specialist (built first: fixed trigger times, most trades per month, exercises the
daily-feature path).

Trigger: at London open (07:00 UTC winter / 06:00 UTC summer -> we use the session table's London
open in local time) and New York open (13:30 UK), when the first two 15m bars of the session close in
a common direction and the Asian range is narrower than `asia_range_max_atr_d` x daily ATR.
Target 1.5 ATR(15m), stop 1.0 ATR, time limit 16 bars, hard flat 1 h before the next session.
"""
from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from goldbot.labels.exit_policy import ExitPolicy
from goldbot.labels.triple_barrier import BarrierSpec
from goldbot.specialists.base import Specialist, register


@register
class SessionOpenSpecialist(Specialist):
    family = "session_open"
    timeframe = "15m"
    default_config = {
        "london_open_local": "08:00",      # Europe/London wall clock
        "newyork_open_local": "08:30",     # America/New_York wall clock (13:30 UK in winter)
        "confirm_bars": 2,
        "asia_range_max_atr_d": 0.8,
        "target_atr": 1.5,
        "stop_atr": 1.0,
        "max_bars": 16,
        "min_body_pct": 0.3,
    }
    # ~90 candidates a year: a rolling 24-month window holds fewer than the 200 training rows a fold needs and a 3-month
    # test fold far fewer than the gate's 60, so train on everything before each fold and test 6 months (proposal P5)
    walkforward = {"expanding": True, "test_months": 6, "step_months": 6}
    # declared meta-model inputs, chosen by rationale (strength of the opening move, the overnight setting, volatility regime and higher-timeframe trend); the model adds `side`, <= 40 in all
    model_features = (
        "ret_1", "ret_4", "ret_16", "ret_96", "atr14_pct", "atr_ratio_14_100", "rv_ratio", "vol_tercile",
        "parkinson_20", "dist_ema20_atr", "dist_ema50_atr", "dist_ema200_atr", "slope_ema50", "ribbon_state", "adx14",
        "donchian_pos_20", "bb_z_20", "rsi14", "range_width_24_atr", "range_width_96_atr", "compression_8_96",
        "tick_vol_ratio_20", "spread_atr", "tick_count_z_48", "gap_atr", "after_break", "body_pct", "session_id",
        "min_since_london_open", "dow", "us_dst", "h1_adx14", "h1_slope_ema50", "h4_dist_ema50_atr", "h4_slope_ema50",
        "h4_ribbon_state", "d1_dist_ema50_atr", "d1_slope_ema50", "d1_atr_ratio_14_100",
    )

    @property
    def label_spec(self) -> BarrierSpec:
        c = self.config
        return BarrierSpec(target_atr=c["target_atr"], stop_atr=c["stop_atr"], max_bars=c["max_bars"], name="session_open")

    @property
    def exit_spec(self) -> ExitPolicy:
        """Design: hard flat 1 h before the next session (Asia 08:00 Tokyo = 23:00 UTC, London and New York at this
        specialist's own opens), run by the labels, the shadow book and the live engine."""
        c = self.config
        return ExitPolicy(flat_before_min=60, session_opens=(("Asia/Tokyo", "08:00"), ("Europe/London", c["london_open_local"]),
                                                             ("America/New_York", c["newyork_open_local"])))

    def exit_policy(self) -> dict[str, Any]:
        return {"type": "barrier", **self.exit_spec.model_dump(exclude_defaults=True)}

    def _open_mask(self, ts: pd.DatetimeIndex) -> np.ndarray:
        """True on the bar that is `confirm_bars` bars after a session open (i.e. the decision bar)."""
        k = self.config["confirm_bars"]
        mask = np.zeros(len(ts), dtype=bool)
        for tz, key in (("Europe/London", "london_open_local"), ("America/New_York", "newyork_open_local")):
            local = ts.tz_convert(ZoneInfo(tz))
            hh, mm = (int(x) for x in self.config[key].split(":"))
            open_min = hh * 60 + mm
            cur = local.hour * 60 + local.minute
            # decision bar opens k bars after the open: the k-th 15m bar's close == open + k*15 => bar opening at open+(k-1)*15
            mask |= np.asarray(cur == open_min + (k - 1) * 15)
        return mask

    def candidates(self, mid_bars: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
        ts = pd.DatetimeIndex(mid_bars["ts_utc"]).tz_convert("UTC")
        k = self.config["confirm_bars"]
        body = mid_bars["close"] - mid_bars["open"]
        rng = (mid_bars["high"] - mid_bars["low"]).replace(0, np.nan)
        body_pct = (body / rng).abs()
        sign = pd.Series(np.sign(body), index=mid_bars.index)
        # all k bars same sign and decent bodies
        same = pd.Series(True, index=mid_bars.index)
        strong = pd.Series(True, index=mid_bars.index)
        for i in range(k):
            same &= sign.shift(i) == sign
            strong &= body_pct.shift(i) >= self.config["min_body_pct"]
        decision = pd.Series(self._open_mask(ts), index=mid_bars.index)
        # Asian range filter: range of the last 32 bars (8h) before London vs daily ATR (from features if present)
        asia_rng = (mid_bars["high"].rolling(32).max() - mid_bars["low"].rolling(32).min()).shift(k)
        atr_d = features["d1_atr14"] if "d1_atr14" in features.columns else features.get("atr14", pd.Series(np.nan, index=mid_bars.index)) * 8
        quiet = asia_rng < self.config["asia_range_max_atr_d"] * atr_d
        quiet = quiet.fillna(True)  # no daily context yet -> do not block (feature may be absent in early bars)
        hit = decision & same & strong & quiet & (sign != 0)
        out = pd.DataFrame({"idx": np.flatnonzero(hit.to_numpy()), "side": sign[hit].astype(int).to_numpy()})
        out["ts_utc"] = ts[hit.to_numpy()]
        return out
