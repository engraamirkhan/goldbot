"""Longest free daily history per instrument, stitched to our Dukascopy daily bars. Local research only.

Sources, ranking and licences: goldbot/data/long_sources.py; row contract and stitch: goldbot/data/long_history.py.
Downloads are cached in raw/daily/_long_cache/ (re-runs are offline and idempotent; --refresh re-downloads).
Writes raw/daily/long/<key>_daily_long.parquet (XAUUSD also to raw/daily/xauusd_daily_1978_present.parquet),
raw/daily/long/manifest.json (sha256, sources, licences, overlap gaps) and a markdown inventory. Everything lands in
the git-ignored raw/; never commit or publish it (ADR 0048). Nothing at or after the research cutoff is kept.

  uv run python scripts/long_daily_history.py [--instruments XAUUSD XAGUSD ...] [--goldhub PATH] [--refresh]
      [--inventory ~/tmp/daily_lane/inventory.md]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.data.daily import DAILY_ROOT  # noqa: E402
from goldbot.data.long_history import summary  # noqa: E402
from goldbot.data.long_sources import (  # noqa: E402
    LONG_ROOT,
    PRIORITY,
    SPECS,
    BuildResult,
    build_instrument,
    find_goldhub,
    licence_for,
)

GOLD_OUT = DAILY_ROOT / "xauusd_daily_1978_present.parquet"
GOLDHUB_DIRS = (Path.home() / "Downloads", DAILY_ROOT, DAILY_ROOT.parent)


def write_parquet(df: pd.DataFrame, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.parquet")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.2f}%"


def inventory_rows(key: str, df: pd.DataFrame, res: BuildResult) -> list[str]:
    s = summary(df)
    srcs = s["sources"]
    assert isinstance(srcs, dict)
    gaps = s["gaps_5plus_weekdays"]
    assert isinstance(gaps, list)
    big = sorted(gaps, key=lambda g: -g[2])[:3]
    gap_txt = "; ".join(f"{a}..{b} ({n}wd)" for a, b, n in big) or "none"
    ov = "; ".join(f"{o.lower} vs {o.upper}: n={o.n_common}, med|d|={_pct(o.median_abs_pct)}, "
                   f"med d={_pct(o.median_signed_pct)}, +{o.rows_added} rows before {o.cutover}"
                   for o in res.overlaps) or "single source"
    src_txt = "<br>".join(f"{k} {v[0]}..{v[1]} ({v[2]})" for k, v in srcs.items())
    fail = ("<br>FAILED: " + "; ".join(res.failures)) if res.failures else ""
    return [f"| {key} | {s['first']} | {s['last']} | {s['rows']} | {s['weekday_coverage_pct']}% | {src_txt} | "
            f"{gap_txt} ({len(gaps)} runs of 5+ weekdays) | {ov}{fail} |"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--instruments", nargs="*", default=None, help="default: all, priority instruments first")
    ap.add_argument("--goldhub", type=Path, default=None, help="WGC Goldhub prices .xlsx (default: search ~/Downloads)")
    ap.add_argument("--refresh", action="store_true", help="re-download cached sources")
    ap.add_argument("--inventory", type=Path, default=Path.home() / "tmp" / "daily_lane" / "inventory.md")
    args = ap.parse_args(argv)
    keys = args.instruments or [*PRIORITY, *(k for k in SPECS if k not in PRIORITY)]
    goldhub = args.goldhub or find_goldhub(GOLDHUB_DIRS)
    manifest_path = LONG_ROOT / "manifest.json"
    manifest: dict[str, object] = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    lines = ["| instrument | first | last | rows | weekday cover | sources (range, rows) | largest gaps | "
             "overlap level gap at each cut-over |", "|---|---|---|---|---|---|---|---|"]
    rc = 0
    for key in keys:
        spec = SPECS[key]
        try:
            df, res = build_instrument(spec, args.refresh, goldhub)
        except Exception as e:  # noqa: BLE001 - report and continue with the next instrument
            print(f"{key}: FAILED {type(e).__name__}: {e}", flush=True)
            lines.append(f"| {key} | - | - | 0 | - | - | - | FAILED: {type(e).__name__}: {e} |")
            rc = 1
            continue
        if df.empty:
            lines.append(f"| {key} | - | - | 0 | - | - | - | no source available: {'; '.join(res.failures)} |")
            continue
        out = LONG_ROOT / f"{key.lower()}_daily_long.parquet"
        sha = write_parquet(df, out)
        if key == "XAUUSD":
            write_parquet(df, GOLD_OUT)
        manifest[key] = {"file": str(out.relative_to(DAILY_ROOT)), "sha256": sha, "note": spec.note,
                         "summary": summary(df), "overlaps": [o.model_dump() for o in res.overlaps],
                         "failures": list(res.failures),
                         "licences": {str(src): licence_for(str(src)) for src in df["source"].unique()},
                         "goldhub_file": str(goldhub) if key == "XAUUSD" and goldhub else None}
        lines += inventory_rows(key, df, res)
        print(f"{key}: {len(df):,} rows {df['date'].min():%Y-%m-%d}..{df['date'].max():%Y-%m-%d} "
              f"sources={list(df['source'].unique())} failures={len(res.failures)}", flush=True)
    LONG_ROOT.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=1, default=str))
    args.inventory.parent.mkdir(parents=True, exist_ok=True)
    args.inventory.write_text("# Long daily history inventory (local only, never published)\n\n"
                              "Overlap gap: median of (older source / series it hands over to - 1) on common dates; "
                              "med|d| is the median absolute gap. Prices are not level-adjusted.\n\n"
                              + "\n".join(lines) + "\n")
    print(f"inventory -> {args.inventory}; manifest -> {manifest_path}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
