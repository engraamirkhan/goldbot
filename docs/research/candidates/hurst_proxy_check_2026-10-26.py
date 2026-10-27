"""Is slice F's Hurst tercile (F-1, X1) a trend-length or volatility proxy? Research director, 2026-10-26.

Discovery half 2010-01-01..2017-12-31 only (0 trials; looks are logged in time-structure-2026-10-26.md). 15m mid bars
from data-v1. Events: first 15m close outside BB(20, 2) from inside (one of F-1's three components), signed 4-bar
forward return from the next bar's open, in ATR15 of the signal bar (slice F's convention). Regime variables at the
signal bar, all trailing:
* hurst_128      goldbot.features.survey.rolling_hurst (the feature F used), tercile of its trailing 250-bar rank;
* er_128         Kaufman efficiency ratio |c_t - c_{t-128}| / sum |dc| over 128 bars (trend length / straightness);
* trend_z        signed 128-bar log return / (sd(1-bar) * sqrt(128)), and `with_run` = sign(break) == sign(trend_z);
* atr_pct        ATR15 / its trailing 250-bar median (volatility level).
Outputs: Spearman correlations of hurst_128 with the proxies; F's Hurst split; the same split by er_128 and by |trend_z|;
and the double sort (Hurst tercile within er_128 terciles) that shows whether Hurst carries anything beyond the proxy.

Usage: uv run python docs/research/candidates/hurst_proxy_check_2026-10-26.py --raw <data-v1 dir>
"""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

from goldbot.features.survey import rolling_hurst

_here = Path(__file__).with_name("time_structure_2026-10-26.py")
_spec = importlib.util.spec_from_file_location("ts", _here)
ts = importlib.util.module_from_spec(_spec)          # type: ignore[arg-type]
_spec.loader.exec_module(ts)                          # type: ignore[union-attr]


def trailing_rank_tercile(x: pd.Series, n: int = 250) -> pd.Series:
    r = x.rolling(n, min_periods=n).rank(pct=True)
    return pd.Series(np.select([r <= 1 / 3, r > 2 / 3], [0, 2], 1), index=x.index).where(r.notna())


def day_t(v: pd.Series, day: pd.Series) -> tuple[float, float, int]:
    s = v.groupby(day).sum()
    c = v.groupby(day).size()
    g = len(s)
    mu = v.mean()
    resid = (s - c * mu)
    se = np.sqrt((resid ** 2).sum() * g / (g - 1)) / len(v)
    return float(mu), float(mu / se), int(len(v))


def more_proxies(raw: Path) -> None:
    """Second pass: short-scale proxies (variance ratio VR(8) over 128 bars, 16-bar |trend z|, the signal bar's own
    |return| / ATR, distance of the close beyond the band / ATR). Same events and terciles."""
    m1 = ts.load(raw)
    m1 = m1[m1.index < ts.DISC_END]
    b = ts.resample(m1, "15min")
    atr = ts.wilder_atr(b)
    c = b["close"]
    lr = np.log(c).diff()
    b["hurst"] = rolling_hurst(lr.to_numpy())
    v1 = lr.rolling(128).var()
    b["vr8"] = np.log(c).diff(8).rolling(128).var() / (8 * v1)
    b["tz16"] = (np.log(c / c.shift(16)) / (lr.rolling(128).std() * 4)).abs()
    b["bar"] = (c - b["open"]).abs() / atr
    mid, sd = c.rolling(20).mean(), c.rolling(20).std()
    up, dn = mid + 2 * sd, mid - 2 * sd
    b["beyond"] = np.maximum(c - up, dn - c) / atr
    side = pd.Series(np.where((c > up) & (c.shift() <= up.shift()), 1,
                              np.where((c < dn) & (c.shift() >= dn.shift()), -1, 0)), index=b.index)
    fwd = (c.shift(-4) - b["open"].shift(-1)) / atr
    for k in ("hurst", "vr8", "tz16", "bar", "beyond"):
        b[f"{k}_t"] = trailing_rank_tercile(b[k])
    e = b.assign(side=side, fwd=side * fwd).loc[side != 0].dropna(
        subset=["fwd", "hurst_t", "vr8_t", "tz16_t", "bar_t", "beyond_t"])
    e["day"] = ts.trading_day(e.index)
    print("events", len(e))
    print("Spearman hurst vs", {k: round(float(e[["hurst", k]].corr(method="spearman").iloc[0, 1]), 3)
                                for k in ("vr8", "tz16", "bar", "beyond")})
    for k in ("vr8_t", "tz16_t", "bar_t", "beyond_t"):
        print(k, {int(g): tuple(round(x, 3) for x in day_t(v["fwd"], v["day"])) for g, v in e.groupby(k)})
    print("hurst high minus low within vr8 terciles:",
          {int(g): round(v[v.hurst_t == 2]["fwd"].mean() - v[v.hurst_t == 0]["fwd"].mean(), 3)
           for g, v in e.groupby("vr8_t")})
    print("hurst low minus high by year:",
          {int(y): round(v[v.hurst_t == 0]["fwd"].mean() - v[v.hurst_t == 2]["fwd"].mean(), 3)
           for y, v in e.groupby(e.index.year)})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--more", action="store_true", help="the second pass (short-scale proxies)")
    a = ap.parse_args()
    if a.more:
        more_proxies(a.raw)
        return
    m1 = ts.load(a.raw)
    m1 = m1[m1.index < ts.DISC_END]
    b = ts.resample(m1, "15min")
    atr = ts.wilder_atr(b)
    c = b["close"]
    lr = np.log(c).diff()
    b["hurst"] = rolling_hurst(lr.to_numpy())
    b["er"] = (c - c.shift(128)).abs() / c.diff().abs().rolling(128).sum()
    b["tz"] = np.log(c / c.shift(128)) / (lr.rolling(128).std() * np.sqrt(128))
    b["atr_pct"] = atr / atr.rolling(250).median()
    mid = c.rolling(20).mean()
    sd = c.rolling(20).std()
    up, dn = mid + 2 * sd, mid - 2 * sd
    side = pd.Series(np.where((c > up) & (c.shift() <= up.shift()), 1,
                              np.where((c < dn) & (c.shift() >= dn.shift()), -1, 0)), index=b.index)
    fwd = (c.shift(-4) - b["open"].shift(-1)) / atr          # next open -> close 4 bars on
    e = b.assign(side=side, fwd=side * fwd).loc[side != 0].dropna(subset=["fwd", "hurst", "er", "tz", "atr_pct"])
    for k in ("hurst", "er", "atr_pct"):
        b[f"{k}_t"] = trailing_rank_tercile(b[k])
    b["abs_tz_t"] = trailing_rank_tercile(b["tz"].abs())
    e = e.join(b[["hurst_t", "er_t", "atr_pct_t", "abs_tz_t"]]).dropna()
    e["with_run"] = (np.sign(e["tz"]) == e["side"]).astype(int)
    e["day"] = ts.trading_day(e.index)
    print("events", len(e))
    print("Spearman hurst vs er, |tz|, atr_pct:",
          {k: round(float(e[["hurst", k]].corr(method="spearman").iloc[0, 1]), 3) for k in ("er", "atr_pct")},
          round(float(pd.concat([e["hurst"], e["tz"].abs()], axis=1).corr(method="spearman").iloc[0, 1]), 3))
    print("share of breaks with the 128-bar run, by hurst tercile:", e.groupby("hurst_t")["with_run"].mean().round(3).to_dict())
    for k in ("hurst_t", "er_t", "abs_tz_t", "atr_pct_t"):
        print(k, {int(g): tuple(round(x, 3) for x in day_t(v["fwd"], v["day"])) for g, v in e.groupby(k)})
    print("double sort: mean fwd (n) by er tercile x hurst tercile")
    tab = e.groupby(["er_t", "hurst_t"])["fwd"].agg(["mean", "size"]).round(3)
    print(tab.to_string())
    print("within-er-tercile hurst high minus low:")
    for g, v in e.groupby("er_t"):
        hi, lo = v[v.hurst_t == 2]["fwd"], v[v.hurst_t == 0]["fwd"]
        print(int(g), round(hi.mean() - lo.mean(), 3), len(hi), len(lo))
    print("with-run vs against-run, by hurst tercile (mean fwd, n):")
    print(e.groupby(["hurst_t", "with_run"])["fwd"].agg(["mean", "size"]).round(3).to_string())


if __name__ == "__main__":
    main()
