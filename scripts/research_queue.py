"""Emit the continuous research loop's next batch as JSON (docs/plans/continuous-research-loop.md, phase 1).

    python scripts/research_queue.py --registry registry.jsonl --bars raw [--limit N] > batch.json

Prints a JSON list of research.yml inputs (specialist, variants, rationale, from_year, to_year), one trial each, at
most `research.loop_trials_per_run` (ADR 0029) (the loop's own budget, separate from `trial_budget_quarter`). ATR for the sizing
pre-filter comes from the last bars before the held-out year (`--bars`), or is given as `--atr '{"15m": 8.0, ...}'`
($/oz per timeframe). Each job carries rule_ref=loop. stderr reports the ATR used and the candidates surviving the pre-filters per timeframe. Nothing is written and nothing is a trial. An unreadable registry or missing ATR prints `[]` and
exits non-zero: the scheduled workflow must then dispatch nothing."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.config import load_settings  # noqa: E402
from goldbot.data.release import read_bar_files  # noqa: E402
from goldbot.data.resample import mid, resample_bars  # noqa: E402
from goldbot.features.technical import atr as atr_series  # noqa: E402
from goldbot.research.director import build_plan  # noqa: E402
from goldbot.research.generator import (  # noqa: E402
    FROM_YEAR,
    batch,
    generate,
    read_registry,
    survivors_by_timeframe,
)
from goldbot.research.min_lot import ATR_BARS  # noqa: E402
from goldbot.research.registry import quarter_of  # noqa: E402
from goldbot.specialists import SPECIALISTS  # noqa: E402

TIMEFRAMES = ("15m", "1h", "4h", "1d")


def atr_from_bars(folder: Path, cut: pd.Timestamp | None) -> dict[str, float]:
    """Wilder ATR(14) of the last bar of each timeframe before `cut` (the held-out year's start), mid prices."""
    cut_year = (cut.year if cut is not None else pd.Timestamp.now(tz="UTC").year)
    b1 = read_bar_files(folder, max(FROM_YEAR, cut_year - 1), cut_year)
    if cut is not None:
        b1 = b1[pd.to_datetime(b1["ts_utc"], utc=True) < cut]
    out: dict[str, float] = {}
    for tf in TIMEFRAMES:
        m = mid(resample_bars(b1, tf)).reset_index(drop=True)
        if len(m) > ATR_BARS:
            v = float(atr_series(m, ATR_BARS).iloc[-1])
            if v == v and v > 0:
                out[tf] = v
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--registry", default="registry.jsonl")
    ap.add_argument("--bars", default="", help="folder of the yearly 1m files, for the ATR")
    ap.add_argument("--atr", default="", help='JSON {"15m": usd, ...} instead of --bars')
    ap.add_argument("--limit", type=int, default=None, help="at most this many (default: research.loop_trials_per_run)")
    args = ap.parse_args(argv)
    try:
        settings = load_settings()
        rows = read_registry(args.registry)
        if args.atr:
            atr = {str(k): float(v) for k, v in json.loads(args.atr).items()}
        elif args.bars:
            atr = atr_from_bars(Path(args.bars), (settings.research.holdout_window() or (None,))[0])
        else:
            raise ValueError("give --bars or --atr")
        now = pd.Timestamp.now(tz="UTC")
        plan = build_plan(now, sorted(SPECIALISTS), rows, [], [], quarter_budget=settings.research.trial_budget_quarter,
                          monthly_total=settings.research.trial_budget_quarter,
                          trial_budget_per_month=settings.research.trial_budget_per_month,
                          floor=settings.research.director_floor, holdout=settings.research.holdout_window(),
                          retired=settings.research.retired_families,
                          reserved_setting=settings.research.reserved_for(quarter_of(now.to_pydatetime())))
        limit = settings.research.loop_trials_per_run if args.limit is None else args.limit
        cands = generate(rows, settings, plan, atr)
        jobs = batch(cands, limit)
        counts = survivors_by_timeframe(cands, TIMEFRAMES)
        print("research_queue: ATR $/oz " + " ".join(f"{tf}={atr.get(tf, float('nan')):.2f}" for tf in TIMEFRAMES)
              + " | candidates surviving the pre-filters " + " ".join(f"{tf}={n}" for tf, n in counts.items()),
              file=sys.stderr)
    except (ValueError, OSError, FileNotFoundError, SystemExit) as exc:
        print(f"research_queue: no batch: {exc}", file=sys.stderr)
        print("[]")
        return 1
    print(json.dumps(jobs, indent=1))
    print(f"research_queue: {len(jobs)} job(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
