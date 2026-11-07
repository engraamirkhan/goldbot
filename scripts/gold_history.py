"""Continuous daily XAUUSD from 1968/1978 to the research cutoff, stitched to our Dukascopy daily. Local only.

Adapted from the owner's Goldhub + Dukascopy script. Ranking (best first; each source adds only dates before the
series' current first date, prices are not level-adjusted and the overlap gap is reported):
  1. Dukascopy daily (raw/daily/bars/xauusd_1d.parquet, 2003-05 on; already local, never re-downloaded)
  2. World Gold Council Goldhub daily USD (LBMA PM) workbook, 1978 on, when present (--goldhub, else the newest
     matching .xlsx in ~/Downloads or raw/). Goldhub terms: personal non-commercial research; never published.
  3. COMEX GC=F continuous futures (Yahoo, 2000-08 on): bridges to the Frankfurt fixing when Goldhub is absent
  4. Frankfurt gold fixing (Bundesbank, DEM/kg, 1968-06..1998-12) / Frankfurt USD fixing -> USD/oz
available_utc: LBMA PM 15:30 London, Frankfurt 17:00 Berlin, GC=F 23:00 UTC, Dukascopy its 21:00 UTC visible_at.
Without Goldhub there is a hole 1999-01..2000-08 (no free daily USD gold source reachable).

Output raw/daily/xauusd_daily_1978_present.parquet: date, open, high, low, close, source, available_utc.

  uv run python scripts/gold_history.py [--goldhub ~/Downloads/<goldhub>.xlsx] [--dukascopy PATH] [--report PATH]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.data.daily import DAILY_ROOT  # noqa: E402
from goldbot.data.long_history import summary  # noqa: E402
from goldbot.data.long_sources import SPECS, build_instrument, find_goldhub, licence_for  # noqa: E402

OUT = DAILY_ROOT / "xauusd_daily_1978_present.parquet"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--goldhub", type=Path, default=None)
    ap.add_argument("--dukascopy", type=Path, default=None, help="Dukascopy daily Parquet/CSV (default: raw/daily/bars)")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--report", type=Path, default=Path.home() / "tmp" / "daily_lane" / "gold_1978.md")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args(argv)
    goldhub = args.goldhub or find_goldhub((Path.home() / "Downloads", DAILY_ROOT, DAILY_ROOT.parent))
    spec = SPECS["XAUUSD"]
    if args.dukascopy is not None:
        spec = spec.model_copy(update={"dukascopy": (str(args.dukascopy.resolve()),)})
    df, res = build_instrument(spec, args.refresh, goldhub)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_suffix(".tmp.parquet")
    df.to_parquet(tmp, index=False)
    tmp.replace(args.out)
    s = summary(df)
    lines = ["# XAUUSD daily long history (local only; never published)\n",
             f"- file: {args.out}", f"- rows: {s['rows']}, {s['first']}..{s['last']}, "
             f"weekday coverage {s['weekday_coverage_pct']}%", f"- Goldhub workbook: {goldhub or 'not found'}"]
    srcs = s["sources"]
    assert isinstance(srcs, dict)
    for k, v in srcs.items():
        lines.append(f"- source {k}: {v[0]}..{v[1]}, {v[2]} rows. Licence: {licence_for(k)}")
    for o in res.overlaps:
        gap = "n/a" if o.median_signed_pct is None else f"{o.median_signed_pct:+.3f}% (median |gap| {o.median_abs_pct:.3f}%)"
        lines.append(f"- overlap {o.lower} vs {o.upper}: {o.n_common} common days, level gap {gap}; "
                     f"{o.rows_added} rows added before {o.cutover}")
    for f in res.failures:
        lines.append(f"- source not used: {f}")
    gaps = s["gaps_5plus_weekdays"]
    assert isinstance(gaps, list)
    lines.append(f"- gaps of 5+ missing weekdays: {len(gaps)}")
    lines += [f"  - {a}..{b}: {n} weekdays" for a, b, n in sorted(gaps, key=lambda g: -g[2])[:10]]
    text = "\n".join(lines) + "\n"
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
