"""Macro driver features: the real yield, the dollar and gold's implied volatility (GVZ).

Inputs: ctx["macro"], the long `macro` table (series, value_date, value, available_utc; release `macro-v1`, see
goldbot/data/macro.py). The derived values are computed on each series' own observations and reach the bars only
through `store.asof_join` on available_utc (design: "features join with merge_asof on available_utc only").

Not in research's default feature set (goldbot/research/pipeline.py: OPT_IN_FEATURES): a frame gets these columns
only when macro data is passed, so models trained without macro keep their feature version.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.data.macro import DRIVERS, driver_frames
from goldbot.data.store import asof_join
from goldbot.features.registry import FeatureCtx, feature


# directional for a meta-label model: rising real yields or a rising dollar mean opposite things for a long and a short
@feature("macro_drivers", "macro", signed={"macro_real_yield_chg20": 0.0, "macro_real_yield_z252": 0.0,
                                           "macro_dollar_chg20": 0.0})
def f_macro_drivers(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    """20-observation change and 1-year z-score of the 10y real yield, 20-observation dollar change, GVZ level and
    its 20-observation change, as known at each bar. NaN columns without macro data (the column set is fixed)."""
    out = pd.DataFrame({c: np.full(len(df), np.nan) for c in DRIVERS}, index=df.index)
    macro = ctx.get("macro")
    if macro is None or len(macro) == 0:
        return out
    bars = pd.DataFrame({"ts_utc": df["ts_utc"].to_numpy(), "_row": np.arange(len(df))})
    for col, frame in driver_frames(macro).items():
        if frame.empty:
            continue
        joined = asof_join(bars, frame).sort_values("_row")     # asof_join sorts by time; restore the bar order
        out[col] = joined[col].to_numpy()
    return out
