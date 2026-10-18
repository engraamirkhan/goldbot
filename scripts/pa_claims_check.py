"""Measure three descriptive price-action claims from the owner's course notes on our Dukascopy bars, from the earliest
year to `research.holdout_from` (the 2025 file is read whole and its holdout rows are cut before resampling, so no
holdout bar reaches a number). Descriptive frequencies only: no trade, label, cost or P&L is computed.

1. EMA20 on 1h bars is touched at least once on (claimed) about 98% of trading days: share of UTC days with at least
   `MIN_HOURS` 1h bars on which some bar's high-low range contains that bar's EMA20.
2. "About 70% of reversals fail": at each confirmed swing pivot (pivot_len 5, stamped `pivot_len` bars after the
   extreme, so the count starts when the turn is known) that turned against an established trend (a higher high after
   a higher low for an up-trend; mirrored), the attempt fails when a later close goes beyond the pivot before a close
   goes beyond the opposite swing it would need to break (the last higher low); resolved within `HORIZON` bars or not.
   Reported for every pivot too (no trend filter).
3. 15m: when the EMA10-EMA50 gap first exceeds k x ATR(14), how often a bar touches EMA50 within N bars, next to the
   same rate from every bar (the baseline).

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
GAP_KS = (1.0, 2.0, 3.0)
GAP_NS = (8, 16, 32)


def ema20_touch_days(b1h: pd.DataFrame) -> dict[str, Any]:
    """Share of trading days (UTC dates with >= MIN_HOURS bars) with a 1h bar whose range contains its EMA20."""
    e = ema(b1h["close"], 20).to_numpy()
    touch = (b1h["low"].to_numpy() <= e) & (e <= b1h["high"].to_numpy())
    day = utc_index(b1h["ts_utc"]).normalize()
    g = pd.DataFrame({"day": day, "touch": touch, "ok": ~np.isnan(e)})
    g = g[g["ok"]].groupby("day").agg(bars=("touch", "size"), touched=("touch", "any"))
    g = g[g["bars"] >= MIN_HOURS]
    by_year = g.groupby(pd.DatetimeIndex(g.index).year)["touched"].mean()
    return {"days": len(g), "share": float(g["touched"].mean()) if len(g) else float("nan"),
            "min_year": float(by_year.min()) if len(by_year) else float("nan"),
            "max_year": float(by_year.max()) if len(by_year) else float("nan")}


def reversal_attempts(b: pd.DataFrame, pivot_len: int = PIVOT_LEN, horizon: int = HORIZON) -> dict[str, Any]:
    """Counter-trend swing turns and how they resolve (see the module docstring, claim 2)."""
    h, lo, c = (b[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    ph, pl = pivot_points(h, lo, pivot_len)
    events = sorted([(t, 1, ph[t]) for t in np.flatnonzero(~np.isnan(ph))] +
                    [(t, -1, pl[t]) for t in np.flatnonzero(~np.isnan(pl))])
    highs: list[float] = []
    lows: list[float] = []
    res: dict[str, dict[str, int]] = {k: {"failed": 0, "held": 0, "open": 0} for k in ("counter_trend", "all")}
    for t, kind, v in events:
        (highs if kind == 1 else lows).append(v)
        if len(highs) < 2 or len(lows) < 2:
            continue
        # the opposite swing the turn must break: last low for a top, last high for a bottom
        other = lows[-1] if kind == 1 else highs[-1]
        trend = (kind == 1 and highs[-1] > highs[-2] and lows[-1] > lows[-2]) or \
                (kind == -1 and lows[-1] < lows[-2] and highs[-1] < highs[-2])
        seg = c[t + 1:t + 1 + horizon]
        beyond = seg > v if kind == 1 else seg < v
        broke = seg < other if kind == 1 else seg > other
        fb = int(np.argmax(beyond)) if beyond.any() else horizon + 1
        bb = int(np.argmax(broke)) if broke.any() else horizon + 1
        out = "open" if fb > horizon and bb > horizon else "failed" if fb < bb else "held"
        res["all"][out] += 1
        if trend:
            res["counter_trend"][out] += 1
    for r in res.values():
        done = r["failed"] + r["held"]
        r["failed_share"] = r["failed"] / done if done else float("nan")      # type: ignore[assignment]
    return res


def gap_fill(b15: pd.DataFrame, ks: tuple[float, ...] = GAP_KS, ns: tuple[int, ...] = GAP_NS) -> dict[str, Any]:
    """Claim 3: EMA50 touch within N bars after the EMA10-EMA50 gap first exceeds k x ATR, and from every bar."""
    c = b15["close"]
    e10, e50 = ema(c, 10).to_numpy(), ema(c, 50).to_numpy()
    a = atr(b15, 14).to_numpy()
    h, lo = b15["high"].to_numpy(), b15["low"].to_numpy()
    touch = (lo <= e50) & (e50 <= h)
    n = len(c)
    # first touch at or after each bar (n when none): next_touch[i] = min j >= i with touch[j]
    idx = np.where(touch, np.arange(n), n)
    next_touch = np.minimum.accumulate(idx[::-1])[::-1]
    gap = np.abs(e10 - e50) / a
    valid = ~np.isnan(gap)
    out: dict[str, Any] = {"bars": int(valid.sum()), "baseline": {}, "events": {}}
    after = next_touch[np.minimum(np.arange(n) + 1, n - 1)] - np.arange(n)        # bars to the next touch after i
    for N in ns:
        ok = valid & (np.arange(n) + N < n)
        out["baseline"][N] = float((after[ok] <= N).mean())
    for k in ks:
        prev = np.r_[np.nan, gap[:-1]]
        ev = valid & (gap > k) & (prev <= k)
        out["events"][k] = {"count": int(ev.sum())}
        for N in ns:
            ok = ev & (np.arange(n) + N < n)
            out["events"][k][N] = float((after[ok] <= N).mean()) if ok.any() else float("nan")
    return out


def run(b1: pd.DataFrame, holdout_from: Any) -> dict[str, Any]:
    b1 = h16.cut_before_holdout(b1, holdout_from)
    bars = {tf: mid(resample_bars(b1, tf).reset_index(drop=True)) for tf in ("15m", "1h", "4h")}
    t = utc_index(bars["15m"]["ts_utc"])
    return {"first_bar": str(t[0]), "last_bar": str(t[-1]), "holdout_from": str(holdout_from),
            "bars": {k: len(v) for k, v in bars.items()},
            "ema20_1h": ema20_touch_days(bars["1h"]),
            "reversals": {tf: reversal_attempts(bars[tf]) for tf in ("1h", "4h")},
            "gap_fill_15m": gap_fill(bars["15m"])}


def markdown(r: dict[str, Any]) -> str:
    e = r["ema20_1h"]
    g = r["gap_fill_15m"]
    lines = [f"Dukascopy mid bars {r['first_bar'][:10]} .. {r['last_bar'][:10]} (holdout from {r['holdout_from']} not "
             f"loaded; 15m {r['bars']['15m']:,}, 1h {r['bars']['1h']:,}, 4h {r['bars']['4h']:,} bars). Descriptive "
             "frequencies only; no trade or P&L. Definitions: scripts/pa_claims_check.py.", "",
             "## 1. EMA20 (1h) touched at least once per trading day", "",
             "| Trading days | Share touched | Lowest year | Highest year |", "|---:|---:|---:|---:|",
             f"| {e['days']:,} | {e['share']:.1%} | {e['min_year']:.1%} | {e['max_year']:.1%} |", "",
             "## 2. Reversal attempts at swing points", "",
             "| Timeframe | Set | Failed | Held | Unresolved | Failed share |", "|---|---|---:|---:|---:|---:|"]
    for tf, rr in r["reversals"].items():
        for k in ("counter_trend", "all"):
            x = rr[k]
            lines.append(f"| {tf} | {k.replace('_', '-')} | {x['failed']:,} | {x['held']:,} | {x['open']:,} | "
                         f"{x['failed_share']:.1%} |")
    ns = sorted(g["baseline"], key=int)
    lines += ["", "## 3. EMA10 to EMA50 after a wide gap (15m)", "",
              "| Gap first > k x ATR | Events | " + " | ".join(f"EMA50 touched within {n} bars" for n in ns) + " |",
              "|---|---:|" + "---:|" * len(ns),
              "| every bar (baseline) | " + f"{g['bars']:,} | " + " | ".join(f"{g['baseline'][n]:.1%}" for n in ns) + " |"]
    for k, x in g["events"].items():
        lines.append(f"| {k} | {x['count']:,} | " + " | ".join(f"{x[n]:.1%}" for n in ns) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--bars", type=Path, required=True, help="folder of xauusd_1m_dukascopy_<year>.parquet")
    ap.add_argument("--from-year", type=int, default=2003)
    ap.add_argument("--out", type=Path, default=None, help="markdown output")
    ap.add_argument("--out-json", type=Path, default=None)
    args = ap.parse_args()
    holdout_from = load_settings().research.holdout_from
    if holdout_from is None:
        raise SystemExit("research.holdout_from is not set: refusing to measure without a holdout cut")
    b1 = read_bar_files(args.bars, args.from_year, pd.Timestamp(holdout_from).year)
    r = run(b1, holdout_from)
    text = "# Price-action course claims, measured (2026-10-18)\n\n" + markdown(r)
    if args.out:
        args.out.write_text(text)
    if args.out_json:
        args.out_json.write_text(json.dumps(r, indent=2, default=str))
    print(text)


if __name__ == "__main__":
    main()
