"""Order-flow proxy features (opt-in, R139): tick-count imbalance, signed runs, spread dynamics and our fill slippage.

Inputs: ctx["bars_1m"] (the store's 1m bars: Dukascopy for research; a 1m decision frame is used itself when absent)
and ctx["fills"] (the store's `fills` table, optional). goldbot/data/orderflow.py computes the values per minute and
per fill; each bar reads them through `store.asof_join` on `available_utc` at the bar's close (`visible_at`, the bar's
start when absent). Tolerances: one 1m value older than BAR_TOLERANCE (a data gap) reads NaN, a fill older than
FILL_TOLERANCE too. MFE/MAE are not built (no excursion fields in `fills`; see the data module).

Opt-in (pipeline.OPT_IN_FEATURES): the default feature set and its version are unchanged; candidates for the discovery
screen only, never a live model's input until a trial passes them (max 40 features per live model). At 15m the H-14
gate still applies to any survivor: expected move >= 3x the round trip.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.data.orderflow import COLUMNS as BAR_COLUMNS
from goldbot.data.orderflow import FILL_COLUMNS, fill_slippage_frame, orderflow_frame
from goldbot.data.store import asof_join
from goldbot.features.registry import FEATURES, FeatureCtx, feature

NAME = "orderflow"
FAMILY = "orderflow"
VERSION = "1"
COLUMNS: tuple[str, ...] = (*BAR_COLUMNS, *FILL_COLUMNS)
BAR_TOLERANCE = pd.Timedelta(minutes=5)
FILL_TOLERANCE = pd.Timedelta(days=7)
SIGNED = {"of_imb_(15m|60m)": 0.0, "of_run": 0.0}


def _close(df: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(df["visible_at"] if "visible_at" in df.columns else df["ts_utc"], utc=True)


def _is_1m(df: pd.DataFrame) -> bool:
    if "visible_at" not in df.columns or len(df) == 0:
        return False
    span = pd.to_datetime(df["visible_at"], utc=True) - pd.to_datetime(df["ts_utc"], utc=True)
    return bool(span.median() == pd.Timedelta(minutes=1))


def _join(bars: pd.DataFrame, frame: pd.DataFrame, cols: tuple[str, ...], tol: pd.Timedelta) -> dict[str, np.ndarray]:
    joined = asof_join(bars, frame, tolerance=tol).sort_values("_row")
    return {c: joined[c].to_numpy(dtype=float) for c in cols}


def f_orderflow(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    """COLUMNS as known at each bar's close; NaN where an input is absent (the column set is fixed)."""
    out = pd.DataFrame({c: np.full(len(df), np.nan) for c in COLUMNS}, index=df.index)
    if len(df) == 0:
        return out
    bars = pd.DataFrame({"ts_utc": _close(df).to_numpy(), "_row": np.arange(len(df))})
    m1 = ctx.get("bars_1m")
    if m1 is None and _is_1m(df):
        m1 = df
    if m1 is not None and len(m1):
        flow = orderflow_frame(m1)
        for c, v in _join(bars, flow, BAR_COLUMNS, BAR_TOLERANCE).items():
            out[c] = v
    fills = ctx.get("fills")
    if fills is not None and len(fills):
        slip = fill_slippage_frame(fills)
        if not slip.empty:
            for c, v in _join(bars, slip, FILL_COLUMNS, FILL_TOLERANCE).items():
                out[c] = v
    return out


def enable() -> str:
    """Register the opt-in `orderflow` feature (idempotent) and return its name."""
    if NAME not in FEATURES:
        feature(NAME, FAMILY, version=VERSION, signed=SIGNED)(f_orderflow)
    return NAME
