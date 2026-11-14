"""Order-flow proxy features (opt-in, R139): quote-update-weighted return scores, signed runs, spread dynamics.

Input: ctx["bars_1m"] (the store's 1m bars: Dukascopy for research; a 1m decision frame is used itself when absent).
goldbot/data/orderflow.py computes the values per minute; each bar reads them through `store.asof_join` on
`available_utc` at the bar's close (`visible_at`, the bar's start when absent). A 1m value older than BAR_TOLERANCE
(a data gap) reads NaN. No fill columns and no MFE/MAE (see the data module; BACKLOG 214).

Dukascopy's tick_count counts the bid side's quote updates, not signed trades, so `of_vw_retz_*` and `of_vw_run` are
quote-update-weighted return scores, not order-flow imbalance (quant review 2026-11-13). SCREEN_NOTE is the bar a
survivor of the discovery screen must clear.

Opt-in (pipeline.OPT_IN_FEATURES): the default feature set and its version are unchanged; candidates for the discovery
screen only, never a live model's input until a trial passes them (max 40 features per live model). At 15m the H-14
gate still applies to any survivor: expected move >= 3x the round trip.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.data.orderflow import COLUMNS, orderflow_frame
from goldbot.data.store import asof_join
from goldbot.features.registry import FEATURES, FeatureCtx, feature

NAME = "orderflow"
FAMILY = "orderflow"
VERSION = "3"                    # 2: columns renamed, fill slippage dropped (quant review); 3: of_quote_imb_* (213)
BAR_TOLERANCE = pd.Timedelta(minutes=5)
SIGNED = {"of_vw_retz_(15m|60m)": 0.0, "of_vw_run": 0.0}
SCREEN_NOTE = ("tick_count is Dukascopy's bid-side quote updates, not signed trades: of_vw_retz_* and of_vw_run are "
               "quote-update-weighted return scores; a survivor must beat the plain return and momentum features "
               "(ret_*, momentum family) in the same screen, or it is read as a restated return")


def _close(df: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(df["visible_at"] if "visible_at" in df.columns else df["ts_utc"], utc=True)


def _is_1m(df: pd.DataFrame) -> bool:
    if "visible_at" not in df.columns or len(df) == 0:
        return False
    span = pd.to_datetime(df["visible_at"], utc=True) - pd.to_datetime(df["ts_utc"], utc=True)
    return bool(span.median() == pd.Timedelta(minutes=1))


def f_orderflow(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    """COLUMNS as known at each bar's close; NaN without 1m input (the column set is fixed)."""
    out = pd.DataFrame({c: np.full(len(df), np.nan) for c in COLUMNS}, index=df.index)
    if len(df) == 0:
        return out
    m1 = ctx.get("bars_1m")
    if m1 is None and _is_1m(df):
        m1 = df
    if m1 is None or len(m1) == 0:
        return out
    bars = pd.DataFrame({"ts_utc": _close(df).to_numpy(), "_row": np.arange(len(df))})
    joined = asof_join(bars, orderflow_frame(m1), tolerance=BAR_TOLERANCE).sort_values("_row")
    for c in COLUMNS:
        out[c] = joined[c].to_numpy(dtype=float)
    return out


def enable() -> str:
    """Register the opt-in `orderflow` feature (idempotent) and return its name."""
    if NAME not in FEATURES:
        feature(NAME, FAMILY, version=VERSION, signed=SIGNED)(f_orderflow)
    return NAME
