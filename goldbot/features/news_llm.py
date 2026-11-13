"""LLM news features (opt-in, R139): the collector's scored headlines as point-in-time columns for the discovery screen.

Input: ctx["news"], the store's `news` table (the brain's; the Mac holds none). goldbot/data/news_features.py builds
the per-headline fields and the 15-minute availability grid; each bar reads the grid through `store.asof_join` on
`available_utc` at the bar's close (`visible_at`, the bar's start when absent), with a tolerance of one grid step, so a
bar past the grid reads NaN rather than an old value. Columns: news_features.COLUMNS (17).

Forward-only: the collector started in October 2026, so every research bar before it reads NaN; these columns are
screen candidates on the forward window only, never in a live model until a discovery trial passes them (max 40
features per live model). Opt-in like positioning: not imported by goldbot.features, listed in
pipeline.OPT_IN_FEATURES, so the default feature set and its version (what the live engine builds) are unchanged.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.data.news_features import COLUMNS, GRID, news_grid
from goldbot.data.store import asof_join
from goldbot.features.registry import FEATURES, FeatureCtx, feature

NAME = "news_llm"
FAMILY = "news"
VERSION = "1"
# gold-signed (positive = supportive for gold): directional for a meta-label model
SIGNED = {"news_sent_(1h|4h|24h)": 0.0, "news_sent_z_(1h|4h|24h)": 0.0, "news_surprise_4h": 0.0}


def _close(df: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(df["visible_at"] if "visible_at" in df.columns else df["ts_utc"], utc=True)


def f_news_llm(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    """COLUMNS as known at each bar's close; all NaN without ctx["news"] (the column set is fixed)."""
    out = pd.DataFrame({c: np.full(len(df), np.nan) for c in COLUMNS}, index=df.index)
    news = ctx.get("news")
    if news is None or len(news) == 0 or len(df) == 0:
        return out
    close = _close(df)
    grid = news_grid(news, end=close.max())
    if grid.empty:
        return out
    bars = pd.DataFrame({"ts_utc": close.to_numpy(), "_row": np.arange(len(df))})
    joined = asof_join(bars, grid, tolerance=GRID).sort_values("_row")
    for c in COLUMNS:
        out[c] = joined[c].to_numpy(dtype=float)
    return out


def enable() -> str:
    """Register the opt-in `news_llm` feature (idempotent) and return its name."""
    if NAME not in FEATURES:
        feature(NAME, FAMILY, version=VERSION, signed=SIGNED)(f_news_llm)
    return NAME
