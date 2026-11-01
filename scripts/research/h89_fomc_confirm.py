"""H-89 post-FOMC continuation: the ONE confirm look on 2018-01..2025-09 (frozen rule in
docs/research/prereg-h89-fomc-2026-11-01.md; decision doc docs/research/decision-2026-11-01-next-lines.md line 1).

Bars are Dukascopy 1m bid/ask labelled by bar OPEN (`ts_utc`; the bar is visible at `visible_at` = open + 1 min),
indexed exactly as in the 2010-2017 explore. For each scheduled `fomc_statement` minute t (UTC, from the keyless
US calendar; duplicates on the same minute dropped):

    signal  side = sign(mid_close[t+5m] - mid_close[t-1m])            (known at t+6m wall clock)
    entry   close of bar t+6m at ask (long) / bid (short)              (filled at t+7m wall clock)
    exit    close of bar t+60m at bid (long) / ask (short)
    net     side * (exit - entry) - 0.08 $/oz commission - 0.30 $/oz slippage margin
    skip    side == 0, or fewer than 60 bars in [t-30m, t+60m] (the explore's coverage rule)

PASS only if mean net > 0 with t >= 2 AND >= 5 of the 8 calendar years 2018..2025 have mean net > 0 AND the share of
trades with net > 0 is >= 0.55. Any leg failing retires H-89, final.

Holdout guard: no bar or event at or after 2025-10-01 00:00 UTC is loaded (asserted); the 2026 file is never opened.
Records nothing in any registry. Writes the statistics JSON to --out (not committed).

    uv run python scripts/research/h89_fomc_confirm.py --bars <repo>/raw \
        --calendar ~/tmp/kcal_out/us_calendar.parquet --out ~/tmp/h89/result.json
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

START = pd.Timestamp("2018-01-01", tz="UTC")
HOLDOUT = pd.Timestamp("2025-10-01", tz="UTC")
EXTRA_COST = 0.30  # slippage margin, $/oz
COMMISSION = 0.08  # $/oz round trip, cost of record cor-2026-10-31.2
YEARS = list(range(2018, 2026))
COLS = ["ts_utc", "bid_close", "ask_close"]


def load_bars(root: Path) -> pd.DataFrame:
    parts = [
        pd.read_parquet(root / f"xauusd_1m_dukascopy_{y}.parquet", columns=COLS) for y in range(2017, 2026)
    ]
    bars = pd.concat(parts).drop_duplicates("ts_utc").set_index("ts_utc").sort_index()
    bars = bars.loc[bars.index < HOLDOUT]
    assert bars.index.max() < HOLDOUT, "holdout bar loaded"
    return bars


def load_events(path: Path) -> pd.Series:
    cal = pd.read_parquet(path)
    ev = pd.to_datetime(cal.loc[cal.event_type == "fomc_statement", "event_utc"], utc=True)
    ev = ev.drop_duplicates().sort_values()
    ev = ev[(ev >= START) & (ev + pd.Timedelta("61min") < HOLDOUT)]
    assert (ev < HOLDOUT).all()
    return ev.reset_index(drop=True)


def _at(s: pd.Series, ts: pd.Timestamp) -> float:
    return float(s.asof(ts))  # type: ignore[arg-type]


def trades(bars: pd.DataFrame, events: pd.Series) -> pd.DataFrame:
    mid = (bars.bid_close + bars.ask_close) / 2
    m = pd.Timedelta("1min")
    rows: list[dict[str, object]] = []
    for t in pd.DatetimeIndex(events):
        if len(bars.loc[t - 30 * m : t + 60 * m]) < 60:
            rows.append({"t": t, "skip": "coverage"})
            continue
        pre, p5 = _at(mid, t - m), _at(mid, t + 5 * m)
        side = float(np.sign(p5 - pre))
        if side == 0 or math.isnan(side):
            rows.append({"t": t, "skip": "flat"})
            continue
        a6, b6 = _at(bars.ask_close, t + 6 * m), _at(bars.bid_close, t + 6 * m)
        a60, b60 = _at(bars.ask_close, t + 60 * m), _at(bars.bid_close, t + 60 * m)
        gross = (b60 - a6) if side > 0 else (b6 - a60)
        rows.append(
            {"t": t, "skip": "", "side": side, "first5": p5 - pre, "net": gross - COMMISSION - EXTRA_COST}
        )
    return pd.DataFrame(rows)


def verdict(df: pd.DataFrame) -> dict[str, object]:
    d = df[df.skip == ""].copy()
    n = len(d)
    mean = float(d.net.mean())
    t = mean / (float(d.net.std(ddof=1)) / math.sqrt(n))
    d["y"] = pd.DatetimeIndex(d.t).year
    by_year: dict[int, tuple[int, float]] = {}
    for y in YEARS:
        g = d.loc[d.y == y, "net"]
        if len(g):
            by_year[y] = (len(g), round(float(g.mean()), 3))
    years_pos = sum(1 for _, mu in by_year.values() if mu > 0)
    hit = float((d.net > 0).mean())
    legs = {"mean_t": mean > 0 and t >= 2.0, "years": years_pos >= 5, "hit": hit >= 0.55}
    return {
        "n": n,
        "skipped": df[df.skip != ""].skip.value_counts().to_dict(),
        "mean_net": round(mean, 3),
        "median_net": round(float(d.net.median()), 3),
        "mean_net_drop_top5_informative": round(float(d.net.sort_values().iloc[:-5].mean()), 3),
        "t": round(t, 3),
        "years_positive": years_pos,
        "hit_rate": round(hit, 4),
        "by_year_n_mean": by_year,
        "legs": legs,
        "verdict": "PASS" if all(legs.values()) else "FAIL",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=Path, required=True)
    ap.add_argument("--calendar", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    res = verdict(trades(load_bars(a.bars), load_events(a.calendar)))
    a.out.write_text(json.dumps(res, indent=2, default=str))
    print(json.dumps(res, indent=2, default=str))


if __name__ == "__main__":
    main()
