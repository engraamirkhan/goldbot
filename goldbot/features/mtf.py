"""Multi-timeframe merge with the visibility rule: a higher-timeframe bar is usable on a decision bar
opening at t only if higher_open + TF <= t, i.e. `visible_at <= t`.

Implemented with merge_asof on `visible_at`; tested so that no 1h feature at 10:15 contains the
10:00-11:00 bar.
"""
from __future__ import annotations

import pandas as pd


def merge_higher_tf(decision: pd.DataFrame, higher_feats: pd.DataFrame, higher_bars: pd.DataFrame,
                    tf_label: str) -> pd.DataFrame:
    """decision: frame with ts_utc. higher_feats: features aligned to higher_bars (same index) with ts_utc.
    higher_bars must carry visible_at. Returns decision with `{tf_label}_` prefixed higher features."""
    h = higher_feats.copy()
    h["visible_at"] = pd.DatetimeIndex(pd.to_datetime(higher_bars["visible_at"], utc=True))
    h = h.drop(columns=["ts_utc"]).sort_values("visible_at")
    h = h.rename(columns={c: f"{tf_label}_{c}" for c in h.columns if c != "visible_at"})
    d = decision.sort_values("ts_utc")
    out = pd.merge_asof(d, h, left_on="ts_utc", right_on="visible_at", direction="backward", allow_exact_matches=True)
    return out.drop(columns=["visible_at"])


TF_LABEL = {"1h": "h1", "4h": "h4", "1d": "d1", "1w": "w1"}
CONTEXT_TFS = ("1h", "4h", "1d")


def context_tfs(decision_tf: str) -> list[str]:
    """The context timeframes merged into a decision frame: every one of CONTEXT_TFS longer than the decision bar
    (a 15m agent sees h1/h4/d1, a 1h agent h4/d1). Research, retrains and the engine all use this one rule, so a
    model is always served the columns it was trained on."""
    from goldbot.config import tf_seconds
    return [tf for tf in CONTEXT_TFS if tf_seconds(tf) > tf_seconds(decision_tf)]
