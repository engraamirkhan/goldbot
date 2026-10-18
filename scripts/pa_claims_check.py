"""Measure three descriptive price-action claims from the owner's course notes on our Dukascopy bars, from the earliest
year to `research.holdout_from` (the 2025 file is read whole and its holdout rows are cut before resampling, so no
holdout bar reaches a number). Descriptive frequencies only: no trade, label, cost or P&L is computed.

A "touch" of an EMA always uses the EMA of the PREVIOUS bar (`ema_touch`): the EMA of bar t is pulled toward bar t's
own close, which lies inside bar t's range, so counting it would inflate every touch rate (quant review 2026-10-18).

1. EMA20 on 1h bars is touched at least once on (claimed) about 98% of trading days: share of UTC days with at least
   `MIN_HOURS` 1h bars on which some bar's high-low range contains the previous bar's EMA20.
2. "About 70% of reversals fail" (continuation is the default). A reversal attempt is a turn AGAINST an established
   trend: in an up-trend (the last two swing highs and the last two swing lows rising) a newly confirmed swing high
   below the last high (a lower high); in a down-trend a higher low. Pivots are classic pivots of `PIVOT_LEN` bars,
   counted from their confirmation bar (known then). The attempt fails when a later close goes beyond the trend's
   last extreme (the higher high) before a close goes beyond the last opposite swing (the higher low), and holds in
   the other order; unresolved within `HORIZON` bars is reported apart. "every pivot" is the turn at each confirmed
   pivot (fails on a close beyond the pivot before a close beyond the last opposite swing), for reference.
   NULL: the same counts on `NULL_RUNS` random paths with the same bars in shuffled order (`shuffled_bars`: each bar's
   open/high/low/close relative to the previous close, permuted, then chained). Reading rule: the claim is supported
   only if the real failed share is above the null's 95th percentile; inside the null's 5-95% range the failure rate
   is what swing geometry gives on any random path, not a property of gold.
3. 15m: after the EMA10-EMA50 gap first exceeds k x ATR(14), the share of events with a bar touching EMA50 within N
   bars, against (a) every bar and (b) every bar matched on distance: the every-bar rate per bin of |close - EMA50| /
   ATR (`MATCH_WIDTH` ATR bins), weighted by the events' bins (`matched_rate`). A gap event starts farther from EMA50,
   so only (b) compares like with like.

  python scripts/fetch_data_release.py --download-only        # raw/xauusd_1m_dukascopy_<year>.parquet
  python scripts/pa_claims_check.py --bars raw --out docs/research/pa-claims-2026-10-18.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.config import load_settings  # noqa: E402
from goldbot.data.release import read_bar_files  # noqa: E402
from goldbot.data.resample import mid, resample_bars  # noqa: E402
from goldbot.data.timeutil import utc_index  # noqa: E402
from goldbot.features.smc_replica import pivot_points  # noqa: E402
from goldbot.features.technical import atr, ema  # noqa: E402
from goldbot.research import h16  # noqa: E402

MIN_HOURS = 12                 # a trading day: at least this many 1h bars on the UTC date
PIVOT_LEN = 5
HORIZON = 500                  # bars a reversal attempt has to resolve
NULL_RUNS = 20
GAP_KS = (1.0, 2.0, 3.0)
GAP_NS = (8, 16, 32)
MATCH_WIDTH = 0.25             # ATR bins of the distance to EMA50 for the matched baseline


def ema_touch(b: pd.DataFrame, n: int) -> np.ndarray:
    """Bar t's high-low range contains EMA(n) of bar t-1 (False before the EMA exists)."""
    e = ema(b["close"], n).to_numpy()
    prev = np.r_[np.nan, e[:-1]]
    with np.errstate(invalid="ignore"):
        return (b["low"].to_numpy() <= prev) & (prev <= b["high"].to_numpy())


def ema20_touch_days(b1h: pd.DataFrame) -> dict[str, Any]:
    """Share of trading days (UTC dates with >= MIN_HOURS bars) with a 1h bar touching the previous bar's EMA20."""
    e = ema(b1h["close"], 20).to_numpy()
    ok = ~np.isnan(np.r_[np.nan, e[:-1]])
    day = utc_index(b1h["ts_utc"]).normalize()
    g = pd.DataFrame({"day": day, "touch": ema_touch(b1h, 20), "ok": ok})
    g = g[g["ok"]].groupby("day").agg(bars=("touch", "size"), touched=("touch", "any"))
    g = g[g["bars"] >= MIN_HOURS]
    by_year = g.groupby(pd.DatetimeIndex(g.index).year)["touched"].mean()
    return {"days": len(g), "share": float(g["touched"].mean()) if len(g) else float("nan"),
            "min_year": float(by_year.min()) if len(by_year) else float("nan"),
            "max_year": float(by_year.max()) if len(by_year) else float("nan")}


def _first(mask: np.ndarray, horizon: int) -> int:
    return int(np.argmax(mask)) if mask.any() else horizon + 1


def reversal_attempts(b: pd.DataFrame, pivot_len: int = PIVOT_LEN, horizon: int = HORIZON) -> dict[str, Any]:
    """Reversal attempts against the trend and turns at every pivot, and how they resolve (module docstring, 2)."""
    h, lo, c = (b[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    ph, pl = pivot_points(h, lo, pivot_len)
    events = sorted([(int(t), 1, ph[t]) for t in np.flatnonzero(~np.isnan(ph))] +
                    [(int(t), -1, pl[t]) for t in np.flatnonzero(~np.isnan(pl))])
    highs: list[float] = []
    lows: list[float] = []
    res: dict[str, dict[str, Any]] = {k: {"failed": 0, "held": 0, "open": 0} for k in ("counter_trend", "all")}

    def resolve(key: str, t: int, fail_up: bool, fail_at: float, hold_at: float) -> None:
        seg = c[t + 1:t + 1 + horizon]
        fb = _first(seg > fail_at if fail_up else seg < fail_at, horizon)
        bb = _first(seg < hold_at if fail_up else seg > hold_at, horizon)
        res[key]["open" if fb > horizon and bb > horizon else "failed" if fb < bb else "held"] += 1

    for t, kind, v in events:
        if len(highs) >= 2 and len(lows) >= 2:
            up = highs[-1] > highs[-2] and lows[-1] > lows[-2]
            down = highs[-1] < highs[-2] and lows[-1] < lows[-2]
            if kind == 1 and up and v < highs[-1]:            # lower high in an up-trend
                resolve("counter_trend", t, True, highs[-1], lows[-1])
            elif kind == -1 and down and v > lows[-1]:        # higher low in a down-trend
                resolve("counter_trend", t, False, lows[-1], highs[-1])
            other = lows[-1] if kind == 1 else highs[-1]
            resolve("all", t, kind == 1, v, other)
        (highs if kind == 1 else lows).append(v)
    for r in res.values():
        done = r["failed"] + r["held"]
        r["failed_share"] = r["failed"] / done if done else float("nan")
    return res


def shuffled_bars(b: pd.DataFrame, seed: int) -> pd.DataFrame:
    """The bars in a random order: each bar's OHLC relative to the previous close, permuted and chained from the
    first close (same bar shapes and returns, no serial structure)."""
    px = b[["open", "high", "low", "close"]].to_numpy(dtype=float)
    rel = px[1:] - px[:-1, 3:4]
    rel = rel[np.random.default_rng(seed).permutation(len(rel))]
    closes = px[0, 3] + np.cumsum(rel[:, 3])
    prev = np.r_[px[0, 3], closes[:-1]]
    out = b.copy()
    out.loc[out.index[1:], ["open", "high", "low", "close"]] = rel + prev[:, None]
    return out


def reversal_null(b: pd.DataFrame, n: int = NULL_RUNS, pivot_len: int = PIVOT_LEN,
                  horizon: int = HORIZON) -> dict[str, Any]:
    """Failed shares of `reversal_attempts` on n shuffled paths: the shares, their mean and 5th/95th percentiles."""
    runs = [reversal_attempts(shuffled_bars(b, seed), pivot_len, horizon) for seed in range(n)]
    out: dict[str, Any] = {}
    for key in ("counter_trend", "all"):
        s = np.array([r[key]["failed_share"] for r in runs], dtype=float)
        out[key] = {"shares": s.tolist(), "mean": float(np.nanmean(s)), "p05": float(np.nanpercentile(s, 5)),
                    "p95": float(np.nanpercentile(s, 95))}
    return out


def matched_rate(dist_events: np.ndarray, dist_all: np.ndarray, hit_all: np.ndarray, width: float) -> float:
    """The every-bar hit rate per distance bin (width `width`), averaged with the events' bin counts as weights
    (bins with no every-bar row are left out)."""
    bins_all = np.floor(dist_all / width).astype(np.int64)
    rate = pd.Series(hit_all.astype(float)).groupby(bins_all).mean()
    ev = pd.Series(np.floor(dist_events / width).astype(np.int64)).value_counts()
    ev = ev[ev.index.isin(rate.index)]
    return float((rate[ev.index] * ev).sum() / ev.sum()) if len(ev) else float("nan")


def gap_fill(b15: pd.DataFrame, ks: tuple[float, ...] = GAP_KS, ns: tuple[int, ...] = GAP_NS,
             width: float = MATCH_WIDTH) -> dict[str, Any]:
    """Claim 3: EMA50 touch within N bars after the EMA10-EMA50 gap first exceeds k x ATR, against every bar and every
    bar matched on the distance to EMA50."""
    c = b15["close"]
    e10, e50 = ema(c, 10).to_numpy(), ema(c, 50).to_numpy()
    a = atr(b15, 14).to_numpy()
    touch = ema_touch(b15, 50)
    n = len(c)
    idx = np.where(touch, np.arange(n), n)
    next_touch = np.minimum.accumulate(idx[::-1])[::-1]
    after = next_touch[np.minimum(np.arange(n) + 1, n - 1)] - np.arange(n)        # bars to the next touch after i
    gap = np.abs(e10 - e50) / a
    dist = np.abs(c.to_numpy() - e50) / a
    valid = ~np.isnan(gap) & ~np.isnan(dist)
    out: dict[str, Any] = {"bars": int(valid.sum()), "baseline": {}, "events": {}}
    for N in ns:
        ok = valid & (np.arange(n) + N < n)
        out["baseline"][N] = float((after[ok] <= N).mean())
    prev = np.r_[np.nan, gap[:-1]]
    for k in ks:
        ev = valid & (gap > k) & (prev <= k)
        out["events"][k] = {"count": int(ev.sum())}
        for N in ns:
            ok = valid & (np.arange(n) + N < n)
            e_ok = ev & ok
            out["events"][k][N] = float((after[e_ok] <= N).mean()) if e_ok.any() else float("nan")
            out["events"][k][f"matched_{N}"] = matched_rate(dist[e_ok], dist[ok], after[ok] <= N, width)
    return out


def run(b1: pd.DataFrame, holdout_from: Any, null_runs: int = NULL_RUNS) -> dict[str, Any]:
    b1 = h16.cut_before_holdout(b1, holdout_from)
    bars = {tf: mid(resample_bars(b1, tf).reset_index(drop=True)) for tf in ("15m", "1h", "4h")}
    t = utc_index(bars["15m"]["ts_utc"])
    return {"first_bar": str(t[0]), "last_bar": str(t[-1]), "holdout_from": str(holdout_from),
            "bars": {k: len(v) for k, v in bars.items()}, "null_runs": null_runs,
            "ema20_1h": ema20_touch_days(bars["1h"]),
            "reversals": {tf: reversal_attempts(bars[tf]) for tf in ("1h", "4h")},
            "reversal_null": {tf: reversal_null(bars[tf], null_runs) for tf in ("1h", "4h")},
            "gap_fill_15m": gap_fill(bars["15m"])}


def markdown(r: dict[str, Any]) -> str:
    e = r["ema20_1h"]
    g = r["gap_fill_15m"]
    lines = [f"Dukascopy mid bars {r['first_bar'][:10]} .. {r['last_bar'][:10]} (holdout from {r['holdout_from']} not "
             f"loaded; 15m {r['bars']['15m']:,}, 1h {r['bars']['1h']:,}, 4h {r['bars']['4h']:,} bars). Descriptive "
             "frequencies only; no trade or P&L. Definitions: scripts/pa_claims_check.py. A touch uses the previous "
             "bar's EMA.", "",
             "## 1. EMA20 (1h) touched at least once per trading day", "",
             "| Trading days | Share touched | Lowest year | Highest year |", "|---:|---:|---:|---:|",
             f"| {e['days']:,} | {e['share']:.1%} | {e['min_year']:.1%} | {e['max_year']:.1%} |", "",
             "## 2. Reversal attempts", "",
             f"Reading rule: the claim is supported only if the real failed share is above the null's 95th percentile "
             f"({r['null_runs']} shuffled-bar paths).", "",
             "| Timeframe | Set | Failed | Held | Unresolved | Failed share | Null mean | Null 5-95% |",
             "|---|---|---:|---:|---:|---:|---:|---:|"]
    for tf, rr in r["reversals"].items():
        for k, label in (("counter_trend", "against the trend"), ("all", "every pivot")):
            x, nl = rr[k], r["reversal_null"][tf][k]
            lines.append(f"| {tf} | {label} | {x['failed']:,} | {x['held']:,} | {x['open']:,} | "
                         f"{x['failed_share']:.1%} | {nl['mean']:.1%} | {nl['p05']:.1%}-{nl['p95']:.1%} |")
    ns = sorted(g["baseline"], key=int)
    lines += ["", "## 3. EMA10 to EMA50 after a wide gap (15m)", "",
              "Share touching EMA50 within N bars. Matched: every-bar rate at the same distance from EMA50.", "",
              "| Row | Events | " + " | ".join(f"N={n}" for n in ns) + " | " + " | ".join(f"N={n} matched" for n in ns)
              + " |", "|---|---:|" + "---:|" * (2 * len(ns)),
              f"| every bar | {g['bars']:,} | " + " | ".join(f"{g['baseline'][n]:.1%}" for n in ns) + " | "
              + " | ".join("" for _ in ns) + " |"]
    for k, x in g["events"].items():
        lines.append(f"| gap first > {k} x ATR | {x['count']:,} | " + " | ".join(f"{x[n]:.1%}" for n in ns) + " | "
                     + " | ".join(f"{x[f'matched_{n}']:.1%}" for n in ns) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--bars", type=Path, required=True, help="folder of xauusd_1m_dukascopy_<year>.parquet")
    ap.add_argument("--from-year", type=int, default=2003)
    ap.add_argument("--null-runs", type=int, default=NULL_RUNS)
    ap.add_argument("--out", type=Path, default=None, help="markdown output")
    ap.add_argument("--out-json", type=Path, default=None)
    args = ap.parse_args()
    holdout_from = load_settings().research.holdout_from
    if holdout_from is None:
        raise SystemExit("research.holdout_from is not set: refusing to measure without a holdout cut")
    b1 = read_bar_files(args.bars, args.from_year, pd.Timestamp(holdout_from).year)
    r = run(b1, holdout_from, args.null_runs)
    text = "# Price-action course claims, measured (2026-10-18)\n\n" + markdown(r)
    if args.out:
        args.out.write_text(text)
    if args.out_json:
        args.out_json.write_text(json.dumps(r, indent=2, default=str))
    print(text)


if __name__ == "__main__":
    main()
