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
    h["visible_at"] = pd.to_datetime(higher_bars["visible_at"].values, utc=True)
    h = h.drop(columns=["ts_utc"]).sort_values("visible_at")
    h = h.rename(columns={c: f"{tf_label}_{c}" for c in h.columns if c != "visible_at"})
    d = decision.sort_values("ts_utc")
    out = pd.merge_asof(d, h, left_on="ts_utc", right_on="visible_at", direction="backward", allow_exact_matches=True)
    return out.drop(columns=["visible_at"])
