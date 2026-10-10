"""Macro driver features: the real yield, the dollar, gold's implied volatility (GVZ) and equity stress (VIX, S&P 500).

Inputs: ctx["macro"], the long `macro` table (series, value_date, value, available_utc; release `macro-v1`, see
goldbot/data/macro.py). The derived values are computed on each series' own observations and reach the bars only
through `store.asof_join` on available_utc (design: "features join with merge_asof on available_utc only").

Not in research's default feature set (goldbot/research/pipeline.py: OPT_IN_FEATURES): a frame gets these columns
only when macro data is passed, so models trained without macro keep their feature version.

Equity stress (indicator survey rank 6, "short-lived haven bid after equity shocks"; version 2): FRED VIXCLS and SP500,
published by scripts/fred_macro.py under their series ids with the next-business-day availability rule. `macro_vix` is
the VIX level, `macro_vix_chg5` its 5-observation change (about a trading week), `macro_spx_dd20` the S&P 500 close over
its 20-observation high minus 1 (<= 0). Each is computed on the series' first releases and joined like the drivers;
NaN until the release carries the series.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.data.macro import DRIVERS, driver_frames, first_release
from goldbot.data.store import asof_join
from goldbot.features.registry import FeatureCtx, feature

EQUITY_STRESS = ("macro_vix", "macro_vix_chg5", "macro_spx_dd20")


def equity_stress_frames(macro: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """column -> DataFrame(available_utc, column) for the equity-stress columns (module docstring), as driver_frames."""
    vix, spx = first_release(macro, "VIXCLS"), first_release(macro, "SP500")
    v, s = vix["value"].astype(float), spx["value"].astype(float)
    values = {"macro_vix": (vix, v), "macro_vix_chg5": (vix, v.diff(5)),
              "macro_spx_dd20": (spx, s / s.rolling(20, min_periods=20).max() - 1)}
    out: dict[str, pd.DataFrame] = {}
    for col, (src, x) in values.items():
        f = pd.DataFrame({"available_utc": src["available_utc"], col: x.to_numpy()})
        out[col] = f.drop_duplicates("available_utc", keep="last").dropna().reset_index(drop=True)
    return out


# directional for a meta-label model: rising real yields or a rising dollar mean opposite things for a long and a short;
# so do equity stress moves (a VIX jump or an S&P drawdown is the haven-bid case)
@feature("macro_drivers", "macro", version="2",
         signed={"macro_real_yield_chg20": 0.0, "macro_real_yield_z252": 0.0, "macro_dollar_chg20": 0.0,
                 "macro_vix_chg5": 0.0, "macro_spx_dd20": 0.0})
def f_macro_drivers(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    """20-observation change and 1-year z-score of the 10y real yield, 20-observation dollar change, GVZ level and
    its 20-observation change, VIX level and 5-observation change, S&P 500 drawdown from its 20-observation high, as
    known at each bar. NaN columns without macro data (the column set is fixed)."""
    out = pd.DataFrame({c: np.full(len(df), np.nan) for c in (*DRIVERS, *EQUITY_STRESS)}, index=df.index)
    macro = ctx.get("macro")
    if macro is None or len(macro) == 0:
        return out
    bars = pd.DataFrame({"ts_utc": df["ts_utc"].to_numpy(), "_row": np.arange(len(df))})
    for col, frame in {**driver_frames(macro), **equity_stress_frames(macro)}.items():
        if frame.empty:
            continue
        joined = asof_join(bars, frame).sort_values("_row")     # asof_join sorts by time; restore the bar order
        out[col] = joined[col].to_numpy()
    return out
