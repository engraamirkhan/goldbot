"""Pre-registered event counts of H-48 `sweep_absorb` and H-49 `eq_liquidity_run` (docs/research/prereg-kb-2026-10-18.md
sections 1-2 and 7): does the implemented rule reproduce the counts the pre-registration was written on? Counts only;
no label, return or P&L is computed.

The pre-registration counted 3,349 kept `sweep_absorb` events and 2,079 `eq_liquidity_run` approaches on 15m mid bars
2010-01-01 .. 2025-09-30 with a scratch script that was not committed. This script replaces it and counts with the
specialists' own code, so the number checked is the trial's: `candidates` = the rule's candidates (one per bar,
opposite sides on one bar dropped; before the news blackout and one-at-a-time, which are label mechanics), `raw` = the
events before that (sweep_absorb: absorbed London/NY sweeps in the stop band, one per level; eq_liquidity_run: every
first approach).

Tolerance (pre-registered 2026-10-19, before any outcome, quant review finding 3): each family's `candidates` within
TOLERANCE (3%) of its pre-registered count, else **stop**: that family is not dispatched until a dated deviation note
explains the gap and is reviewed again. Exit status 1 on a stop.

  python scripts/fetch_data_release.py --download-only        # raw/xauusd_1m_dukascopy_<year>.parquet
  python scripts/kb_prereg_counts.py --bars raw [--out-json counts.json]

Bars are cut at `research.holdout_from` before resampling: the holdout is never loaded.
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
from goldbot.data.calendar import DEFAULT_SESSIONS  # noqa: E402
from goldbot.data.release import read_bar_files  # noqa: E402
from goldbot.data.resample import mid, resample_bars  # noqa: E402
from goldbot.features.technical import atr  # noqa: E402
from goldbot.research import h16  # noqa: E402
from goldbot.specialists import eq_liquidity_run as eq  # noqa: E402
from goldbot.specialists import sweep_absorb as sa  # noqa: E402

PRE_REGISTERED = {"sweep_absorb": 3349, "eq_liquidity_run": 2079}
TOLERANCE = 0.03


def _sweep_raw(m: pd.DataFrame) -> int:
    """Absorbed London/NY sweeps whose stop lies in the 0.6-1.0 ATR band, one per level (no per-bar dedup): the
    pre-registration's "kept" definition."""
    ev = sa.sweep_events(m)
    if ev.empty:
        return 0
    idx = ev["idx"].to_numpy(dtype=int)
    a = atr(m, 14).to_numpy(dtype=float)[idx]
    side = ev["side"].to_numpy(dtype=int)
    dist = side * (m["close"].to_numpy(dtype=float)[idx] - (ev["extreme"].to_numpy(dtype=float) - side * sa.STOP_BUFFER_ATR * a))
    ts = pd.DatetimeIndex(pd.to_datetime(m["ts_utc"], utc=True))[idx]
    with np.errstate(invalid="ignore"):
        ok = (np.isin(DEFAULT_SESSIONS.session_label(ts), sa.SESSIONS) & np.isfinite(a)
              & (dist >= sa.MIN_STOP_ATR * a - 1e-9) & (dist <= sa.MAX_STOP_ATR * a + 1e-9))
    return int(ok.sum())


def count(m: pd.DataFrame) -> dict[str, dict[str, int]]:
    """Per family on 15m mid bars: raw (before the one-per-bar rule) and candidates (the trial's own `_scan`)."""
    m = m.reset_index(drop=True)
    return {"sweep_absorb": {"raw": _sweep_raw(m), "candidates": len(sa.SweepAbsorbSpecialist()._scan(m))},
            "eq_liquidity_run": {"raw": len(eq.approaches(m)),
                                 "candidates": len(eq.EqLiquidityRunSpecialist()._scan(m))}}


def check(candidates: dict[str, int]) -> dict[str, dict[str, Any]]:
    """Each family's candidate count against its pre-registered count: deviation and ok (|deviation| <= TOLERANCE)."""
    out = {}
    for fam, want in PRE_REGISTERED.items():
        got = int(candidates[fam])
        dev = (got - want) / want
        out[fam] = {"count": got, "pre_registered": want, "deviation": dev, "ok": bool(abs(dev) <= TOLERANCE + 1e-12)}
    return out


def markdown(counts: dict[str, dict[str, int]], verdict: dict[str, dict[str, Any]]) -> str:
    lines = ["| Family | Raw | Candidates | Pre-registered | Deviation | Reading |", "|---|---:|---:|---:|---:|---|"]
    for fam, v in verdict.items():
        lines.append(f"| {fam} | {counts[fam]['raw']:,} | {v['count']:,} | {v['pre_registered']:,} | "
                     f"{v['deviation']:+.1%} | {'ok' if v['ok'] else 'STOP'} |")
    lines += ["", f"Tolerance {TOLERANCE:.0%} on the candidates (pre-registered 2026-10-19): a STOP family is not "
              "dispatched until a dated deviation note explains the gap and is reviewed."]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--bars", type=Path, required=True, help="folder of xauusd_1m_dukascopy_<year>.parquet")
    ap.add_argument("--out-json", type=Path, default=None)
    args = ap.parse_args()
    holdout_from = load_settings().research.holdout_from
    if holdout_from is None:
        raise SystemExit("research.holdout_from is not set: refusing to run without a holdout cut")
    b1 = h16.cut_before_holdout(read_bar_files(args.bars, 2010, pd.Timestamp(holdout_from).year), holdout_from)
    counts = count(mid(resample_bars(b1, "15m").reset_index(drop=True)))
    verdict = check({f: c["candidates"] for f, c in counts.items()})
    if args.out_json:
        args.out_json.write_text(json.dumps({"counts": counts, "check": verdict, "tolerance": TOLERANCE}, indent=2))
    print(markdown(counts, verdict))
    if not all(v["ok"] for v in verdict.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
