"""Per-year quality of the local 1m bar files (raw/xauusd_1m_dukascopy_<year>.parquet), to choose the earliest usable
year of the pre-2010 extension (ADR research-windows-w2). Bars only: reads ts_utc, prices, spread and dq_flag; computes
no return, label or strategy outcome, so it may run on every window except the holdout (refused: the 2025 file is cut
at 2025-10-01 and later years are skipped).

Per year: bars, first bar, trading weekdays checked, gap days (check_coverage rule: a weekday under 700 bars, not a
closure), minutes per trading day (median and p10 over non-gap weekdays), the share of in-session bar steps with a gap
> 1 minute and the share of in-session minutes missing (steps of at most MAX_STEP_MIN minutes, so session breaks and
weekends do not count), spread (ask_close - bid_close) p50/p90/p99 in $ and p50 in basis points of the bid, the share
of zero-spread minutes, and the shares flagged bid/ask-imputed, no-volume and partial-month.

Usable (stated before any pre-2010 bar was read, ADR research-windows-w2): gap days <= 2% of checked weekdays, median
minutes per trading day >= 1,000 (2010+ runs about 1,300-1,380), in-session gap>1m share <= 10%, imputed share <= 5%,
spread p50 in bps <= 3x the 2010-2017 median of the same statistic. The earliest usable year is the first year from
which every year up to 2009 passes.

  python scripts/bar_quality_years.py --raw raw --from-year 2003 --to-year 2017 [--report quality.md]
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_coverage import MIN_BARS_PER_DAY, coverage  # noqa: E402

from goldbot.data.timeutil import epoch_ns  # noqa: E402

HOLDOUT_FROM = pd.Timestamp("2025-10-01", tz="UTC")
MAX_STEP_MIN = 60            # a bar-to-bar step longer than this is a session break, not an in-session gap
MINUTE_NS = 60 * 1_000_000_000
COLUMNS = ["ts_utc", "bid_close", "ask_close", "spread_mean", "dq_flag"]

# usability thresholds (frozen with the ADR; see module docstring)
MAX_GAP_DAY_SHARE = 0.02
MIN_MEDIAN_MINUTES = 1_000
MAX_GAP_STEP_SHARE = 0.10
MAX_IMPUTED_SHARE = 0.05
MAX_SPREAD_BPS_RATIO = 3.0


def year_quality(bars: pd.DataFrame, year: int) -> dict[str, object]:
    """Quality statistics of one year's bars (columns COLUMNS). Bars only, no outcome."""
    ts = pd.DatetimeIndex(pd.to_datetime(bars["ts_utc"], utc=True))
    keep = np.asarray(ts < HOLDOUT_FROM)
    b, ts = bars.loc[keep].reset_index(drop=True), ts[keep]
    out: dict[str, object] = {"year": year, "bars": len(b)}
    if not len(b):
        return out
    first = max(dt.date(year, 1, 1), ts.min().date())
    last = min(dt.date(year, 12, 31), (HOLDOUT_FROM - pd.Timedelta(days=1)).date(), ts.max().date())
    rep = coverage(b[["ts_utc"]], first, last, MIN_BARS_PER_DAY)
    ok = rep[rep["status"] != "closure"]["bars"]     # gap days count: a thin year must not hide behind its full days
    ns = np.sort(epoch_ns(ts))
    step = np.diff(ns) // MINUTE_NS
    in_session = step[(step >= 1) & (step <= MAX_STEP_MIN)]
    missing = int((in_session - 1).sum())
    spread = (b["ask_close"] - b["bid_close"]).astype(float)
    flags = b["dq_flag"].fillna("").astype(str) if "dq_flag" in b else pd.Series("", index=b.index)
    out.update({
        "first_bar": ts.min().strftime("%Y-%m-%d %H:%M"),
        "weekdays": len(rep),
        "gap_days": int((rep["status"] == "gap").sum()),
        "closure_days": int((rep["status"] == "closure").sum()),
        "min_per_day_p50": float(ok.median()) if len(ok) else float("nan"),
        "min_per_day_p10": float(ok.quantile(0.10)) if len(ok) else float("nan"),
        "gap_step_share": float((in_session > 1).mean()) if len(in_session) else float("nan"),
        "missing_min_share": missing / (len(in_session) + missing) if len(in_session) else float("nan"),
        "spread_p50": float(spread.quantile(0.50)),
        "spread_p90": float(spread.quantile(0.90)),
        "spread_p99": float(spread.quantile(0.99)),
        "spread_bps_p50": float((spread / b["bid_close"].astype(float) * 1e4).median()),
        "zero_spread_share": float((spread <= 0).mean()),
        "imputed_share": float(flags.str.contains("_imputed").mean()),
        "no_volume_share": float(flags.str.contains("no_volume").mean()),
        "partial_share": float(flags.str.contains("partial_month").mean()),
    })
    return out


def verdicts(rows: list[dict[str, object]], ref_years: range = range(2010, 2018)) -> tuple[pd.DataFrame, int | None]:
    """The table with a `usable` column and the reasons a year fails, and the earliest usable pre-2010 year (the first
    year from which every year up to 2009 passes; None if 2009 itself fails or no pre-2010 year is present)."""
    t = pd.DataFrame(rows).set_index("year").sort_index()
    ref = t.loc[[y for y in ref_years if y in t.index], "spread_bps_p50"] if "spread_bps_p50" in t else pd.Series()
    ref_bps = float(ref.median()) if len(ref) else float("nan")
    why = []
    for _, r in t.iterrows():
        bad = []
        if not r.get("bars"):
            bad.append("no bars")
        else:
            if r["gap_days"] > MAX_GAP_DAY_SHARE * r["weekdays"]:
                bad.append("gap days")
            if not r["min_per_day_p50"] >= MIN_MEDIAN_MINUTES:
                bad.append("minutes/day")
            if not r["gap_step_share"] <= MAX_GAP_STEP_SHARE:
                bad.append("gap>1m")
            if r["imputed_share"] > MAX_IMPUTED_SHARE:
                bad.append("imputed")
            if np.isfinite(ref_bps) and not r["spread_bps_p50"] <= MAX_SPREAD_BPS_RATIO * ref_bps:
                bad.append("spread")
        why.append(", ".join(bad))
    t["fails"] = why
    t["usable"] = t["fails"] == ""
    pre = [y for y in t.index if y < 2010]
    earliest = None
    for y in sorted(pre, reverse=True):
        if not t.loc[y, "usable"]:
            break
        earliest = int(y)
    if earliest is not None and any(y not in t.index for y in range(earliest, 2010)):
        earliest = None
    return t, earliest


def render(t: pd.DataFrame, earliest: int | None) -> str:
    def f(v: object, spec: str) -> str:
        return "-" if v is None or (isinstance(v, float) and not np.isfinite(v)) else format(v, spec)
    head = ("| year | bars | first bar | gap days / weekdays | min/day p50 (p10) | gap>1m steps | missing min | "
            "spread $ p50/p90/p99 | spread bps p50 | zero spread | bid/ask imputed | no volume | usable |")
    lines = [head, "|" + "---|" * 13]
    for y, r in t.iterrows():
        if not r.get("bars"):
            lines.append(f"| {y} | 0 | - | - | - | - | - | - | - | - | - | - | no ({r['fails']}) |")
            continue
        lines.append(
            f"| {y} | {int(r['bars']):,} | {r['first_bar']} | {int(r['gap_days'])} / {int(r['weekdays'])} | "
            f"{f(r['min_per_day_p50'], ',.0f')} ({f(r['min_per_day_p10'], ',.0f')}) | {f(r['gap_step_share'], '.1%')} | "
            f"{f(r['missing_min_share'], '.1%')} | {f(r['spread_p50'], '.2f')}/{f(r['spread_p90'], '.2f')}/"
            f"{f(r['spread_p99'], '.2f')} | {f(r['spread_bps_p50'], '.2f')} | {f(r['zero_spread_share'], '.1%')} | "
            f"{f(r['imputed_share'], '.1%')} | {f(r['no_volume_share'], '.1%')} | "
            f"{'yes' if r['usable'] else 'no (' + r['fails'] + ')'} |")
    lines += ["", f"earliest usable pre-2010 year: {earliest if earliest is not None else 'none'}"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--raw", default="raw")
    ap.add_argument("--from-year", type=int, default=2003)
    ap.add_argument("--to-year", type=int, default=2017)
    ap.add_argument("--report", default=None)
    args = ap.parse_args(argv)
    if args.to_year > HOLDOUT_FROM.year:
        ap.error(f"--to-year after {HOLDOUT_FROM.year} would read the holdout")
    rows: list[dict[str, object]] = []
    for y in range(args.from_year, args.to_year + 1):
        p = Path(args.raw) / f"xauusd_1m_dukascopy_{y}.parquet"
        if not p.exists():
            rows.append({"year": y, "bars": 0})
            continue
        bars = pd.read_parquet(p, columns=COLUMNS)
        rows.append(year_quality(bars, y))
        print(f"{y}: {rows[-1]['bars']:,} bars", flush=True)
    t, earliest = verdicts(rows)
    text = render(t, earliest)
    if args.report:
        Path(args.report).write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
