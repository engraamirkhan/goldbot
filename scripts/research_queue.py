"""Emit the continuous research loop's next batch as JSON (docs/plans/continuous-research-loop.md, "Loop grid v1").

    python scripts/research_queue.py --registry registry.jsonl [--limit N] > batch.json

Prints a JSON list of research.yml inputs (specialist, variants, rationale, from_year, to_year, rule_ref=loop), one
trial each, at most `research.loop_trials_per_run` (the loop's own budget, ADR 0029, separate from
`trial_budget_quarter`). stderr reports the grid size per timeframe and what is left of it. Nothing is written and
nothing is a trial. There is no sizing pre-filter: every loop trial runs `--lot-fit` instead.

Exit codes: 0 a batch (possibly shorter than the limit); 3 the grid is exhausted (every member is a trial in the
registry): `[]` is printed and stderr says "grid exhausted", so the workflow reports it to the research director
instead of dispatching nothing silently; 1 an unreadable registry or settings: `[]` and a message, nothing dispatched."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.config import load_settings  # noqa: E402
from goldbot.research.director import build_plan  # noqa: E402
from goldbot.research.generator import batch, generate, grid, grid_sizes, read_registry  # noqa: E402
from goldbot.research.registry import quarter_of  # noqa: E402
from goldbot.specialists import SPECIALISTS  # noqa: E402

TIMEFRAMES = ("15m", "1h", "4h", "1d")
EXHAUSTED = 3


def _sizes(label: str, sizes: dict[str, int]) -> str:
    return f"{label} " + " ".join(f"{tf}={n}" for tf, n in sizes.items())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--registry", default="registry.jsonl")
    ap.add_argument("--limit", type=int, default=None, help="at most this many (default: research.loop_trials_per_run)")
    args = ap.parse_args(argv)
    try:
        settings = load_settings()
        rows = read_registry(args.registry)
        now = pd.Timestamp.now(tz="UTC")
        plan = build_plan(now, sorted(SPECIALISTS), rows, [], [], quarter_budget=settings.research.trial_budget_quarter,
                          monthly_total=settings.research.trial_budget_quarter,
                          trial_budget_per_month=settings.research.trial_budget_per_month,
                          floor=settings.research.director_floor, holdout=settings.research.holdout_window(),
                          retired=settings.research.retired_families,
                          reserved_setting=settings.research.reserved_for(quarter_of(now.to_pydatetime())))
        limit = settings.research.loop_trials_per_run if args.limit is None else args.limit
        everything = grid(settings)
        left = generate(rows, settings, plan)
        jobs = batch(left, limit)
    except (ValueError, OSError, SystemExit) as exc:
        print(f"research_queue: no batch: {exc}", file=sys.stderr)
        print("[]")
        return 1
    print(json.dumps(jobs, indent=1))
    print("research_queue: " + _sizes("grid", grid_sizes(everything, TIMEFRAMES)) + " | "
          + _sizes("left", grid_sizes(left, TIMEFRAMES)), file=sys.stderr)
    if not left:
        print(f"research_queue: grid exhausted: all {len(everything)} members of loop grid v1 are trials in the registry",
              file=sys.stderr)
        return EXHAUSTED
    print(f"research_queue: {len(jobs)} job(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
