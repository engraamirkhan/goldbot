"""`tv_plots` (opt-in, v1): the catalogued plots of TradingView studies as known at each bar.

Input: ctx["tv_catalog"] (list[PlotSpec], goldbot/data/tv_catalog.py: kind, side, feature id per plot) and
ctx["tv_indicators"] (the long `tv_indicators` table: one row per indicator x version x tf x bar x plot). One column
per catalogued plot, named by its feature id `tv_{study}_{meta_hash}_{tf}_{plot_id}`:

* level -> (close - level) / close (signed distance of our close from the line); oscillator and state -> the value;
  event -> 1 when the mark is on the bar, 0 when the bar arrived without it.
* "Every join to bars via asof_join on available_utc": a TradingView bar reaches a row only from its `available_utc`
  (bar close, or the alert's receipt), never 1 ns before.
* "A missed alert sets the feature to NaN rather than a stale value": the joined TradingView bar must have closed less
  than one TradingView bar plus MAX_LAG before the row (one bar of delivery latency is normal; two is a missing bar).
  Older is NaN, never carried forward. A weekend or session gap is NaN until the first new bar.
* "Unverified rows never train" (plan F11): live rows (webhook alert, `pull_live`) always count; history rows (`csv`,
  `pull_history`) only when the repaint check flagged that plot `repaint_validated`; any other origin never. A live row
  wins over history on the same bar. Rows that carry a `meta_hash` must carry this study's (another input set is a
  different feature).
* "Training data has about 1% of alerts randomly blanked so the model has seen the NaN path before production"
  (DESIGN, TradingView signals): with ctx["tv_training"] set, BLANK_RATE of each study's bars are blanked, all of its
  plots at once, chosen by a hash of (study, meta hash, tf, bar time, ctx["tv_blank_seed"]) so a rerun or another
  window blanks the same bars. Live scoring never sets it.
* At most MAX_TV_FEATURES columns (goldbot.research.model.check_feature_cap also refuses a model with more, or with
  more than 40 inputs in all).

Opt-in: not imported by goldbot.features; `enable()` registers it, so the default feature set does not change.
"""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from goldbot.data.store import asof_join
from goldbot.data.timeutil import epoch_ns, utc_index
from goldbot.data.tv_catalog import PlotSpec, is_history, is_live
from goldbot.features.registry import FEATURES, FeatureCtx, feature
from goldbot.research.model import MAX_TV_FEATURES, check_feature_cap
from goldbot.webhook.app import tf_seconds

NAME = "tv_plots"
VERSION = "1"
BLANK_RATE = 0.01
MAX_LAG = pd.Timedelta(seconds=60)    # delivery latency a bar may take beyond its own close before it counts as missing


def check_model_columns(names: list[str]) -> None:
    """A model's inputs with TradingView columns: at most MAX_TV_FEATURES of them and 40 in all."""
    check_feature_cap(names)


def _plot_name(p: object) -> str:
    s = str(p)
    return f"plot_{s}" if s.isdigit() else s


def _eligible(long: pd.DataFrame, spec: PlotSpec) -> pd.DataFrame:
    """The study's rows that may be used: this indicator, version, timeframe and input set; bar close only; live, or
    repaint-validated history."""
    d = long[(long["indicator"].astype(str) == spec.study) & (long["version"].astype(str) == spec.version)
             & (long["tf"].astype(str) == spec.tf) & ~long["intrabar"].fillna(False).astype(bool)]
    if "meta_hash" in d.columns:
        h = d["meta_hash"].fillna("").astype(str)
        d = d[h.eq("") | h.eq(spec.meta_hash)]
    origin = d["origin"] if "origin" in d.columns else pd.Series("", index=d.index)
    validated = d["repaint_validated"].fillna(False).astype(bool) if "repaint_validated" in d.columns \
        else pd.Series(False, index=d.index)
    live = is_live(origin)
    d = d[live | (is_history(origin) & validated)].assign(_live=live)
    return d.assign(plot=d["plot"].map(_plot_name))


def _wide(d: pd.DataFrame) -> pd.DataFrame:
    """bar_time, available_utc and one column per plot; a bar with any live row uses its live rows only."""
    live_bars = set(d.loc[d["_live"], "bar_time"])
    d = d[d["_live"] | ~d["bar_time"].isin(live_bars)]
    d = d.assign(available_utc=utc_index(d["available_utc"]), bar_time=utc_index(d["bar_time"]))
    d = d.sort_values("available_utc").drop_duplicates(["bar_time", "plot"], keep="first")
    vals = d.pivot(index="bar_time", columns="plot", values="value")
    avail = d.groupby("bar_time")["available_utc"].max()          # the bar is complete when its last plot is known
    out = vals.assign(available_utc=avail).reset_index()
    out.columns.name = None
    return out


def _blanked(bar_time: pd.Series, key: str, seed: int) -> np.ndarray:
    salt = int(hashlib.sha256(f"{key}|{seed}".encode()).hexdigest()[:15], 16)
    ns = epoch_ns(pd.DatetimeIndex(bar_time)).astype(np.uint64) ^ np.uint64(salt)
    u = pd.util.hash_array(ns).astype(np.float64) / float(2**64)
    return np.asarray(u < BLANK_RATE)


def f_tv_plots(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    """One column per catalogued plot (feature id), point in time; NaN where the bar is missing, late or unverified."""
    specs: list[PlotSpec] = list(ctx.get("tv_catalog") or [])
    if len(specs) > MAX_TV_FEATURES:
        raise ValueError(f"at most {MAX_TV_FEATURES} TradingView columns per model; the catalogue selects {len(specs)}")
    ids = [s.feature_id for s in specs]
    if len(set(ids)) != len(ids):
        raise ValueError("the catalogue selects a feature id twice")
    out = pd.DataFrame({i: np.full(len(df), np.nan) for i in ids}, index=df.index)
    long = ctx.get("tv_indicators")
    if not specs or long is None or len(long) == 0:
        return out
    t = utc_index(df["ts_utc"])
    bars = pd.DataFrame({"ts_utc": t, "_row": np.arange(len(df))})
    close = pd.to_numeric(df["close"], errors="coerce").to_numpy(dtype=float) if "close" in df.columns \
        else np.full(len(df), np.nan)
    training = bool(ctx.get("tv_training", False))
    seed = int(ctx.get("tv_blank_seed", 0))
    for spec in specs:
        d = _eligible(long, spec)
        d = d[d["plot"] == spec.plot_id]
        if d.empty:
            continue
        frame = _wide(d)
        if training:
            key = f"{spec.study}|{spec.meta_hash}|{spec.tf}"
            frame = frame[~_blanked(frame["bar_time"], key, seed)]
        joined = asof_join(bars, frame.rename(columns={"bar_time": "bar"}), prefix="_tv_").sort_values("_row")
        width = pd.Timedelta(seconds=tf_seconds(spec.tf))
        bar_close = utc_index(joined["_tv_bar"]) + width
        fresh = np.asarray((t - bar_close) < width + MAX_LAG) & joined["_tv_bar"].notna().to_numpy()
        v = pd.to_numeric(joined[f"_tv_{spec.plot_id}"], errors="coerce").to_numpy(dtype=float)
        if spec.kind == "event":
            v = (np.isfinite(v) & (v != 0)).astype(float)
        elif spec.kind == "level":
            v = (close - v) / close
        out[spec.feature_id] = np.where(fresh, v, np.nan)
    return out


def enable() -> str:
    """Register the `tv_plots` feature (idempotent) and return its name, for an explicit feature list."""
    if NAME not in FEATURES:
        feature(NAME, "tradingview", version=VERSION)(f_tv_plots)
    return NAME
