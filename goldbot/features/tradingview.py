"""TradingView indicator features (opt-in): the values the owner's indicators plot, read from their bar alerts.

Source: the `tv_indicators` dataset (long format, one row per indicator x version x symbol x tf x bar x plot), written
from the webhook inbox (goldbot/webhook/inbox.py). "Every join to bars via asof_join on available_utc": a plot
reaches a bar only once its alert was received AND its bar had closed (`available_utc = max(bar close, received)`),
never on the bar's nominal time. Intrabar alerts are excluded (the bar's final values are the close alert's).

* `tv_features(store, indicator, tf, plots=None)` -> DataFrame(available_utc, <plot>...), one row per bar, the
  research reader: `asof_join(bars, tv_features(...), prefix="tv_")`.
* `f_tradingview(df, ctx)`: the registered feature; ctx["tv_indicators"] is the long table, columns are
  `tv_<indicator>_<tf>_<plot>`; a bar with no alert yet is NaN, never a stale value filled forward past the join.

History from the owner's chart export (goldbot/data/tv_csv.py, `origin="csv"`) sits beside the live alerts. A bar that
has a live alert always uses the alert's values; a bar only the CSV has uses the CSV's, with `available_utc` = bar
close. A chart export can repaint (the history is redrawn with hindsight), so until `tv-repaint-check` has flagged the
indicator x timeframe `repaint_validated`, `tv_features` marks its frame `attrs["tv_label"] = UNVALIDATED` and every
research result built on it must carry that label (`label_for`); the registered feature leaves unvalidated CSV rows
out (plan F11: unverified rows never train) unless ctx["tv_include_unvalidated"] is set.

Opt-in: this module is NOT imported by goldbot.features and registers nothing on import; `enable()` registers the
`tradingview` feature, so the default feature set and its feature version (what the live engine builds) do not change.
TradingView indicators stay filter features until proven (DESIGN promotion table); they count toward the 40-feature cap.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

import numpy as np
import pandas as pd

from goldbot.data.store import Store, asof_join
from goldbot.features.registry import FEATURES, FeatureCtx, feature
from goldbot.webhook.inbox import TABLE, safe_symbol, source_of

NAME = "tradingview"
VERSION = "1"
UNVALIDATED = "unvalidated (repaint risk)"


def _long_for(store: Store, indicator: str, tf: str, symbol: str, version: str | None) -> pd.DataFrame:
    tdir = store.table_dir(TABLE)
    prefix = source_of(indicator, "")
    found = sorted(d.name.removeprefix("source=") for d in tdir.glob("source=*") if d.name.startswith(f"source={prefix}"))
    if version is not None:
        found = [s for s in found if s == source_of(indicator, version)]
    if len(found) > 1:
        raise ValueError(f"{indicator!r} has several versions in the store ({', '.join(found)}); pass version=")
    if not found:
        return pd.DataFrame()
    return store.read(TABLE, source=found[0], symbol=safe_symbol(symbol))


def _is_csv(d: pd.DataFrame) -> pd.Series:
    return d["origin"].eq("csv") if "origin" in d.columns else pd.Series(False, index=d.index)


def _csv_validated(d: pd.DataFrame) -> pd.Series:
    return d["repaint_validated"].fillna(False).astype(bool) if "repaint_validated" in d.columns \
        else pd.Series(False, index=d.index)


def _csv_rows_used(d: pd.DataFrame) -> pd.DataFrame:
    """Drop every CSV row of a bar that has a live alert: the alert wins, whole bar."""
    csv = _is_csv(d)
    if not csv.any():
        return d
    alert_bars = set(d.loc[~csv, "bar_time"])
    return d[~csv | ~d["bar_time"].isin(alert_bars)]


def wide(long: pd.DataFrame, tf: str, plots: Iterable[str] | None = None, *, include_unvalidated: bool = False) -> pd.DataFrame:
    """Long rows of one indicator -> DataFrame(available_utc, <plot>...): bar-close alerts only, one row per bar (the
    first receipt), the plots that were never numeric on any bar dropped, `plots` selecting and ordering columns.
    A bar with a live alert uses only the alert; chart-export (CSV) rows fill the bars without one, and only those
    whose indicator x timeframe passed the repaint check unless `include_unvalidated` (then `attrs["tv_label"]` says
    so when any such row is in the frame)."""
    cols = ["available_utc"]
    if long.empty:
        return pd.DataFrame(columns=cols)
    d = long[(long["tf"].astype(str) == str(tf)) & (~long["intrabar"].astype(bool)) & (long["plot"] != "")]
    if not include_unvalidated:
        d = d[~_is_csv(d) | _csv_validated(d)]
    d = _csv_rows_used(d)
    if d.empty:
        return pd.DataFrame(columns=cols)
    unvalidated = bool((_is_csv(d) & ~_csv_validated(d)).any())
    d = d.assign(available_utc=pd.to_datetime(d["available_utc"], utc=True))
    # a bar's availability is the earliest receipt of any of its plots (they arrive in one alert, so they agree)
    d = d.sort_values("available_utc").drop_duplicates(["bar_time", "plot"], keep="first")
    out = d.pivot(index=["bar_time", "available_utc"], columns="plot", values="value").reset_index().sort_values("available_utc")
    out = out.drop(columns=["bar_time"]).reset_index(drop=True)
    out.columns.name = None
    names = [c for c in out.columns if c != "available_utc"]
    if plots is not None:
        wanted = list(plots)
        for p in wanted:
            if p not in out.columns:
                out[p] = np.nan
        names = wanted
    res = out[["available_utc", *names]]
    if unvalidated:
        res.attrs["tv_label"] = UNVALIDATED
    return res


def tv_features(store: Store, indicator: str, tf: str, plots: Iterable[str] | None = None, *,
                symbol: str = "XAUUSD", version: str | None = None, include_unvalidated: bool = True) -> pd.DataFrame:
    """The indicator's plots at timeframe `tf`, keyed by `available_utc`, ready for `asof_join` (research reader).
    Chart-export rows not yet repaint-validated are included by default and then the frame carries
    `attrs["tv_label"] == UNVALIDATED`: put `label_for(frame)` on every result (`include_unvalidated=False` leaves
    them out)."""
    return wide(_long_for(store, indicator, tf, symbol, version), tf, plots, include_unvalidated=include_unvalidated)


def label_for(frame: pd.DataFrame) -> str | None:
    """The label a research result must carry when it reads `frame` (None: live alerts and validated history only)."""
    label = frame.attrs.get("tv_label")
    return str(label) if label else None


def _slug(x: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", x.lower()).strip("_")


def f_tradingview(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    """`tv_<indicator>_<tf>_<plot>` as known at each bar; no columns without data (nothing is invented)."""
    long = ctx.get("tv_indicators")
    out = pd.DataFrame(index=df.index)
    if long is None or len(long) == 0:
        return out
    bars = pd.DataFrame({"ts_utc": df["ts_utc"].to_numpy(), "_row": np.arange(len(df))})
    for (indicator, tf), grp in long.groupby(["indicator", "tf"]):
        frame = wide(grp, str(tf), include_unvalidated=bool(ctx.get("tv_include_unvalidated", False)))
        if frame.empty:
            continue
        prefix = f"tv_{_slug(str(indicator))}_{_slug(str(tf))}_"
        joined = asof_join(bars, frame, prefix=prefix).sort_values("_row")
        for c in frame.columns:
            if c != "available_utc":
                out[f"{prefix}{c}"] = joined[f"{prefix}{c}"].to_numpy()
    return out


def enable() -> str:
    """Register the `tradingview` feature (idempotent) and return its name, for an explicit feature list."""
    if NAME not in FEATURES:
        feature(NAME, "tradingview", version=VERSION)(f_tradingview)
    return NAME
