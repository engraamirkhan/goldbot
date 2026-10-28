"""Autonomous discovery CLI (ADR 0058 phase 1): generate -> log -> evaluate -> null -> report, explore window only.

    python scripts/autodisc.py run --generator combine --synthetic [--limit N] [--reps 20] [--db PATH]
    python scripts/autodisc.py bench --synthetic --specs 10000        # throughput of log + evaluate
    python scripts/autodisc.py report --run-json state/autodisc_run.json

`--synthetic` runs the pipeline on seeded noise (`nulls.SyntheticProvider`). Without it `run` reads the local 1m bars
(GOLDBOT_RAW_BARS or the repo's gitignored raw/, 2010-2017 only, never committed), builds the real outcome tensor and
masks from `primitives.PrimitiveProvider`; specs naming a leaf the code base cannot compute are dropped before they
are ledgered. `bench` is synthetic only. Every spec is ledgered before it is evaluated (`SearchLedger.require_logged`),
K_search and K_null are printed, the ledger head (seq, digest) goes into the run record so a truncated tail is
detectable, and the report never prints a 2018+ date or year (`assert_no_confirm_numbers`).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.config import load_settings  # noqa: E402
from goldbot.data.release import read_bar_files  # noqa: E402
from goldbot.execution.costs import research_costs  # noqa: E402
from goldbot.features.technical import atr  # noqa: E402
from goldbot.research.autodisc import ExploreFrame, SearchLedger, spec_hash, truncate_to_explore  # noqa: E402
from goldbot.research.autodisc.contracts import MaskProvider  # noqa: E402
from goldbot.research.autodisc.gen_combine import MAX_BARS, existing_family_names, generate  # noqa: E402
from goldbot.research.autodisc.novelty import (  # noqa: E402
    Reference,
    check_novelty,
    daily_series,
    existing_names,
)
from goldbot.research.autodisc.nulls import (  # noqa: E402
    FunnelResult,
    MaskCache,
    SyntheticProvider,
    make_noise_tensor,
    run_funnel,
    search_specs,
)
from goldbot.research.autodisc.outcomes import OutcomeTensor, build_tensor, default_menu  # noqa: E402
from goldbot.research.autodisc.primitives import PrimitiveProvider  # noqa: E402
from goldbot.research.autodisc.spec import StrategySpec  # noqa: E402

DEFAULT_DB = "state/discovery.db"
_CONFIRM_YEAR = re.compile(r"(?<![0-9A-Za-z])20(?:1[89]|[2-9]\d)(?![0-9A-Za-z])")
_CONFIRM_MONTH = re.compile(r"20(?:1[89]|[2-9]\d)-\d\d")


def assert_no_confirm_numbers(text: str) -> None:
    """The report is explore-window only: refuse any 2018+ year or date in it."""
    m = _CONFIRM_YEAR.search(text) or _CONFIRM_MONTH.search(text)
    if m:
        raise ValueError(f"report would contain a confirm/holdout-window date: {m.group(0)!r}")


def _daily(t: OutcomeTensor, mask: np.ndarray, side: np.ndarray, day_id: np.ndarray, n_days: int) -> np.ndarray:
    ev = t.select(mask, side, 0)
    return daily_series(ev.r(), ev.idx, day_id, n_days)


def novelty_labels(t: OutcomeTensor, specs: list[StrategySpec], survivors: list[str], provider: MaskProvider
                   ) -> dict[str, str]:
    """Label each survivor against every family (unfiltered entries, exit 0) and flag variants of existing families."""
    cache = MaskCache(provider)
    names = existing_names()
    tf = specs[0].timeframe
    codes, _ = pd.factorize(t.ts_utc.normalize())
    day_id, n_days = codes.astype(np.int64), int(codes.max()) + 1
    refs: dict[str, Reference] = {}
    for fam in sorted({s.family for s in specs if s.family}):
        mask, side = cache.p.family_entries(fam, tf)
        refs[fam] = Reference(fam, mask, _daily(t, mask, side, day_id, n_days))
    by_hash = {spec_hash(s): s for s in specs}
    out = {}
    for h in survivors:
        s = by_hash[h]
        mask, side = cache.entries(s)
        out[h] = check_novelty(s.family or "", mask, _daily(t, mask, side, day_id, n_days), list(refs.values()),
                               names).label
    return out


def render_report(res: FunnelResult, labels: dict[str, str], head: tuple[int, str], generator: str, reps: int,
                  fpr: float) -> str:
    lines = [
        f"autodisc report | generator {generator} | explore window 2010-2017 only",
        f"K_search {res.k_search} | K_null {res.k_null} (null reps {reps})",
        f"evaluated {res.evaluated}",
        f"passed floors {res.passed_floors}",
        f"beat null (>= 95th pct of best-of-search, {res.null_threshold:.3f}) {res.beat_null}",
        f"also pass explore checks (excess over drift, 1.5x cost stress, floors) {res.passed_explore_checks}",
        f"real best-of-search fitness {res.real_best:.3f} at null percentile {res.real_best_null_percentile:.2f}",
        f"stated false-positive rate {fpr:.3f} (1/(reps+1))",
        f"explore survivors {len(res.survivors)}: a recorded fact, not evidence of edge",
    ]
    for h, lab in sorted(labels.items()):
        lines.append(f"  {h[:12]}  {lab}")
    lines.append(f"ledger head seq {head[0]} digest {head[1]}")
    text = "\n".join(lines) + "\n"
    assert_no_confirm_numbers(text)
    return text


def _need_synthetic(args: argparse.Namespace) -> None:
    if not args.synthetic:
        raise SystemExit("bench runs on seeded noise only: pass --synthetic")


def raw_dir(arg: str | None) -> Path:
    return Path(arg or os.environ.get("GOLDBOT_RAW_BARS") or Path(__file__).resolve().parents[1] / "raw")


def real_backends(args: argparse.Namespace, timeframe: str) -> tuple[OutcomeTensor, PrimitiveProvider]:
    """Explore-window 1m bars -> provider and the outcome tensor of the decision bars it cut."""
    raw = raw_dir(args.raw)
    b1 = truncate_to_explore(read_bar_files(raw, 2010, 2017), "ts_utc")      # 2018+ files are never opened
    if len(b1) == 0:
        raise SystemExit(f"no 2010-2017 1m bars under {raw} (set --raw or GOLDBOT_RAW_BARS)")
    prov = PrimitiveProvider(ExploreFrame.from_frame(b1, "ts_utc"))
    bars = prov.bars(timeframe)
    extra, swap, _ = research_costs(load_settings(), None)
    t = build_tensor(ExploreFrame.from_frame(bars, "ts_utc"), atr(prov.mid_bars(timeframe), 14),
                     default_menu(MAX_BARS[timeframe]), extra_cost_usd=extra, swap=swap)
    return t, prov


def cmd_run(args: argparse.Namespace) -> int:
    if args.generator != "combine":
        raise SystemExit("only --generator combine exists in phase 1")
    tf = args.timeframe
    prov: MaskProvider
    if args.synthetic:
        specs = list(generate(timeframes=(tf,)))
        t = make_noise_tensor(args.bars, seed=args.seed)
        prov = SyntheticProvider(args.bars, seed=args.seed)
    else:
        t, real = real_backends(args, tf)
        fams = tuple(f for f in existing_family_names() if f in real.families(tf))
        every = list(generate(families=fams, timeframes=(tf,)))
        specs = [s for s in every if s.filter is None or real.supports(s.filter)]
        print(f"families {len(fams)} ({', '.join(fams)}); grid {len(every)}; dropped {len(every) - len(specs)} specs "
              "whose filter has no existing feature", file=sys.stderr)
        prov = real
    if args.limit:
        specs = specs[: args.limit]
    ledger = SearchLedger(args.db)
    res = run_funnel(t, specs, prov, ledger, "G1", "1", reps=args.reps, seed=args.seed, min_events=args.min_events or None)
    labels = novelty_labels(t, specs, res.survivors, prov)
    head = ledger.head()
    text = render_report(res, labels, head, "G1 combine", args.reps, 1 / (args.reps + 1))
    sys.stdout.write(text)
    if args.run_json:
        Path(args.run_json).write_text(json.dumps({"result": asdict(res), "labels": labels, "head": list(head),
                                                   "reps": args.reps}))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    d: dict[str, Any] = json.loads(Path(args.run_json).read_text())
    res = FunnelResult(**d["result"])
    sys.stdout.write(render_report(res, d["labels"], (d["head"][0], d["head"][1]), "G1 combine", d["reps"],
                                   1 / (d["reps"] + 1)))
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    """Throughput of ledger-log + evaluate for N specs (no null replications: the null is calibrated per generator
    version, run overnight)."""
    _need_synthetic(args)
    specs = list(generate())[: args.specs]
    t = make_noise_tensor(args.bars, seed=0)
    cache = MaskCache(SyntheticProvider(args.bars))
    ledger = SearchLedger(args.db)
    t0 = time.perf_counter()
    for s in specs:
        ledger.log_spec(s, "G1", "1")
    t1 = time.perf_counter()
    search_specs(t, specs, cache, ledger=ledger, min_events=args.min_events or None)
    t2 = time.perf_counter()
    print(f"{len(specs)} specs on {args.bars} bars: ledger {t1 - t0:.1f}s, evaluate {t2 - t1:.1f}s, "
          f"{(t2 - t0) / len(specs) * 1000:.2f} ms/spec single process")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("run", cmd_run), ("bench", cmd_bench), ("report", cmd_report)):
        p = sub.add_parser(name)
        p.set_defaults(fn=fn)
        p.add_argument("--synthetic", action="store_true", help="seeded-noise stand-in backends")
        p.add_argument("--db", default=DEFAULT_DB)
        p.add_argument("--bars", type=int, default=4000)
        p.add_argument("--seed", type=int, default=0)
        p.add_argument("--min-events", type=int, default=0, help="0 = the evaluator's per-timeframe floor")
        p.add_argument("--run-json", default=None)
        if name == "run":
            p.add_argument("--generator", default="combine")
            p.add_argument("--limit", type=int, default=0)
            p.add_argument("--reps", type=int, default=20)
            p.add_argument("--timeframe", default="15m", choices=["15m", "1h", "4h"])
            p.add_argument("--raw", default=None, help="dir of xauusd_1m_*.parquet (default GOLDBOT_RAW_BARS or raw/)")
        if name == "bench":
            p.add_argument("--specs", type=int, default=10_000)
    args = ap.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
