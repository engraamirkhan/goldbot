"""Repaint check for TradingView chart-export history (`python -m goldbot.ops.run tv-repaint-check --indicator ...`).

A chart export is redrawn with hindsight: an indicator that repaints shows history values it never showed live, so a
backtest on them is optimistic. The live alerts are the truth (they were recorded at the bar close). For one
indicator x version x timeframe this compares the CSV rows with the alert rows on the bars both have, plot by plot
(exact within `abs_tol`; both empty counts as equal, one empty does not):

* a bar matches when every plot the two sources share agrees;
* the match % is matching bars over overlapping bars;
* `repaint_validated` is set only on the CSV rows of plots the alerts also carried (those were compared), and only when
  the overlap is at least `min_bars` (200) AND the match is at least `agree` (99 %); otherwise (or on a later failing
  re-check) it is cleared. A CSV plot the alerts never carried stays unvalidated, and the history older than the overlap
  is never compared at all: the report says so, and the reader keeps its "unvalidated" label while any plot is unflagged. A new indicator version has its own
  rows, so it starts unvalidated whatever the old one earned.

The same check covers history from the direct pull (`history_origin="pull_history"`, `--origin pull_history`): each
history origin is compared with, and flagged against, the live rows (webhook alerts and `pull_live`) only, never with
the other history (goldbot/data/tv_catalog.py: LIVE_ORIGINS, HISTORY_ORIGINS).

Until then every research result that reads the CSV rows is labelled "unvalidated (repaint risk)"
(goldbot/features/tradingview.py). This is research tooling: it reads and flags `tv_indicators`, nothing else.
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord
from goldbot.data.store import Store
from goldbot.data.tv_catalog import HISTORY_ORIGINS, is_live
from goldbot.data.tv_csv import normalize_tf
from goldbot.features.tradingview import UNVALIDATED, _long_for
from goldbot.webhook.inbox import TABLE, safe_symbol, source_of

MIN_BARS = 200
AGREE = 0.99
ABS_TOL = 1e-4


class RepaintReport(FrozenRecord):
    indicator: str
    version: str
    symbol: str
    tf: str
    csv_bars: int = Field(ge=0)
    overlap_bars: int = Field(ge=0)
    matched_bars: int = Field(ge=0)
    match_pct: float
    per_plot: dict[str, float]          # % of overlapping bars on which this plot matched
    min_bars: int
    agree: float
    abs_tol: float
    validated: bool
    reason: str
    uncompared_plots: list[str] = Field(default_factory=list)     # CSV plots no live alert carried: stay unvalidated

    @property
    def label(self) -> str | None:
        return None if self.validated else UNVALIDATED

    def text(self) -> str:
        plots = ", ".join(f"{p} {v:.1f}%" for p, v in sorted(self.per_plot.items())) or "none shared"
        return "\n".join([
            f"{self.indicator}@{self.version} {self.symbol} {self.tf}: "
            f"{'VALIDATED' if self.validated else 'NOT validated'} (repaint_validated={str(self.validated).lower()})",
            f"  csv bars {self.csv_bars}, overlapping live-alert bars {self.overlap_bars}, matching {self.matched_bars} "
            f"= {self.match_pct:.1f}% (need >= {self.agree * 100:g}% over >= {self.min_bars}, tolerance {self.abs_tol:g})",
            f"  per plot: {plots}",
            f"  {self.reason}",
            *([f"  not compared, left unvalidated (the alerts did not carry them): {', '.join(self.uncompared_plots)}"]
              if self.uncompared_plots else []),
            f"  limit: only the {self.overlap_bars} bars since the alerts began were compared; CSV history older than "
            f"the overlap is unvalidated (it is trusted by extension, not checked)"])


def _plot_name(p: str) -> str:
    return f"plot_{p}" if p.isdigit() else p


def _matrix(d: pd.DataFrame) -> pd.DataFrame:
    """bar_time x plot values of one source (first value per bar and plot)."""
    d = d[d["plot"].map(lambda p: bool(p) and p != "signal")]
    if d.empty:
        return pd.DataFrame()
    d = d.assign(plot=d["plot"].map(_plot_name)).drop_duplicates(["bar_time", "plot"], keep="first")
    return d.pivot(index="bar_time", columns="plot", values="value")


def validate(store: Store, *, indicator: str, version: str, tf: str, symbol: str = "XAUUSD", min_bars: int = MIN_BARS,
             agree: float = AGREE, abs_tol: float = ABS_TOL, apply: bool = True,
             history_origin: str = "csv") -> RepaintReport:
    """Compare the history of `history_origin` (the CSV export, or `pull_history`) with the live rows and (with
    `apply`) set `repaint_validated` on that history's rows."""
    if history_origin not in HISTORY_ORIGINS:
        raise ValueError(f"history_origin must be one of {sorted(HISTORY_ORIGINS)}, not {history_origin!r}")
    tf = normalize_tf(tf)
    long = _long_for(store, indicator, tf, symbol, version)
    base: dict[str, Any] = dict(indicator=indicator, version=version, symbol=symbol, tf=tf, min_bars=min_bars, agree=agree,
                abs_tol=abs_tol)
    if long.empty:
        return RepaintReport(**base, csv_bars=0, overlap_bars=0, matched_bars=0, match_pct=0.0, per_plot={},
                             validated=False, reason="nothing is stored for this indicator and version")
    long = long[(long["tf"].astype(str) == tf) & (~long["intrabar"].astype(bool))]
    origin = long["origin"] if "origin" in long.columns else pd.Series("", index=long.index)
    csv, live = _matrix(long[origin.eq(history_origin)]), _matrix(long[is_live(origin)])
    shared = [p for p in csv.columns if p in live.columns]
    bars = csv.index.intersection(live.index)
    per_plot: dict[str, float] = {}
    matched = 0
    if len(bars) and shared:
        a, b = csv.loc[bars, shared].to_numpy(float), live.loc[bars, shared].to_numpy(float)
        eq = (np.isnan(a) & np.isnan(b)) | (np.abs(a - b) <= abs_tol)
        per_plot = {p: round(float(eq[:, i].mean() * 100), 4) for i, p in enumerate(shared)}
        matched = int(eq.all(axis=1).sum())
    overlap = len(bars) if shared else 0
    pct = round(matched / overlap * 100, 4) if overlap else 0.0
    if overlap == 0:
        validated, reason = False, "no bar has both a CSV row and a live alert yet: nothing to compare"
    elif overlap < min_bars:
        validated, reason = False, (f"only {overlap} overlapping bars, need {min_bars}: keep collecting live alerts and "
                                    f"export the chart again")
    elif matched < overlap * agree:
        validated, reason = False, "history differs from what was live: this indicator repaints; results stay unvalidated"
    else:
        validated, reason = True, "history matches what was live"
    if not csv.shape[0]:
        reason = (f"no {history_origin} rows are stored for this indicator and version: "
                  + ("run tv-import first" if history_origin == "csv" else "pull the history first"))
    uncompared = [p for p in csv.columns if p not in shared]
    if validated and uncompared:
        reason += f"; plots {', '.join(uncompared)} were not in the alerts, so they stay unvalidated"
    if apply and csv.shape[0]:
        _set_flag(store, indicator, version, tf, symbol, set(shared) if validated else set(), history_origin)
    return RepaintReport(**base, csv_bars=int(csv.shape[0]), overlap_bars=overlap, matched_bars=matched,
                         match_pct=pct, per_plot=per_plot, validated=validated, reason=reason,
                         uncompared_plots=uncompared)


def _set_flag(store: Store, indicator: str, version: str, tf: str, symbol: str, validated_plots: set[str],
              history_origin: str = "csv") -> None:
    source, sym = source_of(indicator, version), safe_symbol(symbol)
    df = store.read(TABLE, source=source, symbol=sym)
    rows = df[(df["origin"].eq(history_origin)) & (df["tf"].astype(str) == tf)].copy()
    flag = rows["plot"].map(_plot_name).isin(validated_plots)
    if rows.empty or bool(rows["repaint_validated"].fillna(False).astype(bool).eq(flag).all()):
        return
    rows["repaint_validated"] = flag
    # same key (origin is part of it), later write wins: only the flag changes; the alert rows are not touched
    store.append(TABLE, rows.drop(columns=["source", "symbol", "year", "month"], errors="ignore"), source=source, symbol=sym)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="run.py tv-repaint-check", description="Compare a TradingView chart export with "
                                 "the live alerts on the same bars; flag it repaint_validated above the threshold.")
    ap.add_argument("--indicator", required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--tf", required=True)
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--min-bars", type=int, default=MIN_BARS)
    ap.add_argument("--agree", type=float, default=AGREE, help="share of overlapping bars that must match (0.99)")
    ap.add_argument("--abs-tol", type=float, default=ABS_TOL)
    ap.add_argument("--origin", default="csv", choices=sorted(HISTORY_ORIGINS),
                    help="which history to check: the chart export (csv) or the direct pull (pull_history)")
    ap.add_argument("--report-only", action="store_true", help="do not change repaint_validated")
    ap.add_argument("--data-root", default=None, help="store root (default: settings data_root)")
    a = ap.parse_args(argv)
    if a.data_root is None:
        from goldbot.config import load_settings
        a.data_root = load_settings().data_root
    try:
        rep = validate(Store(a.data_root), indicator=a.indicator, version=a.version, tf=a.tf, symbol=a.symbol,
                       min_bars=a.min_bars, agree=a.agree, abs_tol=a.abs_tol, apply=not a.report_only,
                       history_origin=a.origin)
    except ValueError as exc:
        print(f"tv-repaint-check: {exc}", file=sys.stderr)
        return 2
    print(rep.text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
