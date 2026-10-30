"""Autonomous discovery CLI (ADR 0058 phase 1): generate -> log -> evaluate -> null -> report, explore window only.

    python scripts/autodisc.py run --generator combine --synthetic [--limit N] [--reps 100] [--db PATH]
    python scripts/autodisc.py bench --synthetic --specs 10000        # throughput of log + evaluate
    python scripts/autodisc.py report --run-json state/autodisc_run.json
    python scripts/autodisc.py nightly [--state-dir state] [--timeframe 15m|1h|4h]   # ADR 0058 phase 2 job, here
    python scripts/autodisc.py confirm-prereg <spec_hash>        # ADR 0058 phase 3: pre-register a survivor's confirm read
    python scripts/autodisc.py confirm-run --batch YYYY-MM       # read the month's frozen batch once (2018-01 .. 2025-09)
    python scripts/autodisc.py holdout <spec_hash>               # a confirm passer's one holdout read

`--synthetic` runs the pipeline on seeded noise (`nulls.SyntheticProvider`). Without it `run` reads the local 1m bars
(GOLDBOT_RAW_BARS or the repo's gitignored raw/, 2010-2017 only, never committed), builds the real outcome tensor and
masks from `primitives.PrimitiveProvider`; specs naming a leaf the code base cannot compute are dropped before they
are ledgered. `bench` is synthetic only. Every spec is ledgered before it is evaluated (`SearchLedger.require_logged`),
K_search and K_null are printed, the ledger head (seq, digest) goes into the run record so a truncated tail is
detectable, and the report never prints a 2018+ date or year (`assert_no_confirm_numbers`; counts carry thousands
separators so a count of 2,019 cannot read as a year). `nightly` runs the scheduler's `autodisc_nightly` on this
machine (the owner's Mac, where the bars are), whatever research.autodisc.nightly_enabled says.
"""
from __future__ import annotations

import argparse
import json
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
from goldbot.research.autodisc import SearchLedger, nightly, spec_hash  # noqa: E402
from goldbot.research.autodisc.contracts import MaskProvider  # noqa: E402
from goldbot.research.autodisc.gen_combine import existing_family_names, generate  # noqa: E402
from goldbot.research.autodisc.nightly import (  # noqa: E402
    assert_no_confirm_numbers,
    raw_dir,
    run_nightly_job,
)
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
from goldbot.research.autodisc.outcomes import OutcomeTensor  # noqa: E402
from goldbot.research.autodisc.primitives import PrimitiveProvider  # noqa: E402
from goldbot.research.autodisc.spec import StrategySpec  # noqa: E402

DEFAULT_DB = {"real": "state/discovery_real.db", "synthetic": "state/discovery_synthetic.db"}
DEFAULT_REPS = 100      # reported null percentiles need >= 100 reps; 20 (resolution 1/21) is the pass/fail gate only
_DATE_RE = r" built \d{4}-\d\d-\d\d"


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
                  fpr: float, cost_line: str = "") -> str:
    lines = [
        f"autodisc report | generator {generator} | explore window 2010-2017 only",
        f"K_search {res.k_search:,} | K_null {res.k_null:,} (null reps {reps:,})",
        *([f"costs: {re.sub(_DATE_RE, '', cost_line)}"] if cost_line else []),
        f"evaluated {res.evaluated:,}",
        f"passed floors {res.passed_floors:,}",
        f"beat null (>= 95th pct of best-of-search, {res.null_threshold:.3f}) {res.beat_null:,}",
        f"also pass explore checks (excess over drift, 1.5x cost stress, floors) {res.passed_explore_checks:,}",
        f"real best-of-search fitness {res.real_best:.3f} at null percentile {res.real_best_null_percentile:.2f}",
        f"stated false-positive rate {fpr:.3f} (1/(reps+1))",
        f"explore survivors {len(res.survivors):,}: a recorded fact, not evidence of edge",
    ]
    for h, lab in sorted(labels.items()):
        lines.append(f"  {h[:12]}  {lab}")
    lines.append(f"ledger head seq {head[0]:,} digest {head[1]}")
    text = "\n".join(lines) + "\n"
    assert_no_confirm_numbers(text)
    return text


def _need_synthetic(args: argparse.Namespace) -> None:
    if not args.synthetic:
        raise SystemExit("bench runs on seeded noise only: pass --synthetic")


def real_backends(args: argparse.Namespace, timeframe: str) -> tuple[OutcomeTensor, PrimitiveProvider, str]:
    """Explore-window 1m bars -> provider and the outcome tensor of the decision bars it cut (`nightly.real_backends`)."""
    raw = raw_dir(args.raw)
    b = nightly.real_backends(load_settings(), timeframe, raw=raw, cost_table=args.cost_table or "")   # "": no table (priors), as before
    if b is None:
        raise SystemExit(f"no 2010-2017 1m bars under {raw} (set --raw or GOLDBOT_RAW_BARS)")
    return b.tensor, b.provider, b.cost_line


def cmd_run(args: argparse.Namespace) -> int:
    if args.generator != "combine":
        raise SystemExit("only --generator combine exists in phase 1")
    tf = args.timeframe
    prov: MaskProvider
    cost_line, dropped = "", 0
    if args.synthetic:
        specs = list(generate(timeframes=(tf,)))
        t = make_noise_tensor(args.bars, seed=args.seed)
        prov = SyntheticProvider(args.bars, seed=args.seed)
    else:
        t, real, cost_line = real_backends(args, tf)
        fams = tuple(f for f in existing_family_names() if f in real.families(tf))
        every = list(generate(families=fams, timeframes=(tf,)))
        specs = [s for s in every if s.filter is None or real.supports(s.filter)]
        dropped = len(every) - len(specs)
        print(f"families {len(fams)} ({', '.join(fams)}); grid {len(every)}; dropped {dropped} specs "
              "whose filter has no existing feature", file=sys.stderr)
        prov = real
    if args.limit:
        specs = specs[: args.limit]
    mode = "synthetic" if args.synthetic else "real"
    ledger = SearchLedger(args.db or DEFAULT_DB[mode], mode=mode)
    res = run_funnel(t, specs, prov, ledger, "G1", "1", reps=args.reps, seed=args.seed, min_events=args.min_events or None)
    labels = novelty_labels(t, specs, res.survivors, prov)
    head = ledger.head()
    text = render_report(res, labels, head, "G1 combine", args.reps, 1 / (args.reps + 1), cost_line)
    sys.stdout.write(text)
    if args.run_json:
        # dropped_unsupported: grid specs never ledgered (their filter has no existing feature); not part of K
        Path(args.run_json).write_text(json.dumps({"result": asdict(res), "labels": labels, "head": list(head),
                                                   "reps": args.reps, "cost_line": cost_line,
                                                   "dropped_unsupported": dropped}))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    d: dict[str, Any] = json.loads(Path(args.run_json).read_text())
    res = FunnelResult(**d["result"])
    sys.stdout.write(render_report(res, d["labels"], (d["head"][0], d["head"][1]), "G1 combine", d["reps"],
                                   1 / (d["reps"] + 1), d.get("cost_line", "")))
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    """Throughput of ledger-log + evaluate for N specs (no null replications: the null is calibrated per generator
    version, run overnight)."""
    _need_synthetic(args)
    specs = list(generate())[: args.specs]
    t = make_noise_tensor(args.bars, seed=0)
    cache = MaskCache(SyntheticProvider(args.bars))
    ledger = SearchLedger(args.db or DEFAULT_DB["synthetic"], mode="synthetic")
    t0 = time.perf_counter()
    for s in specs:
        ledger.log_spec(s, "G1", "1")
    t1 = time.perf_counter()
    search_specs(t, specs, cache, ledger=ledger, min_events=args.min_events or None)
    t2 = time.perf_counter()
    print(f"{len(specs)} specs on {args.bars} bars: ledger {t1 - t0:.1f}s, evaluate {t2 - t1:.1f}s, "
          f"{(t2 - t0) / len(specs) * 1000:.2f} ms/spec single process")
    return 0


def cmd_nightly(args: argparse.Namespace) -> int:
    """The scheduler's autodisc_nightly on this machine (enabled for this run only; settings.yaml is not changed).
    `--timeframe` overrides research.autodisc.timeframe for this run (the Mac's launchd job runs 15m, 1h, 4h in turn)."""
    s = load_settings()
    upd: dict[str, Any] = {"nightly_enabled": True}
    if args.timeframe:
        upd["timeframe"] = args.timeframe
    on = s.model_copy(update={"research": s.research.model_copy(update={
        "autodisc": s.research.autodisc.model_copy(update=upd)})})
    out = run_nightly_job(on, Path(args.state_dir), pd.Timestamp.now(tz="UTC"))
    print(out.get("health") or out.get("skipped"))
    return 0


def _stage_args(args: argparse.Namespace) -> tuple[Any, Any, Any]:
    from goldbot.research.autodisc.stages import StageRefused
    from goldbot.research.registry import TrialRegistry
    s = load_settings()
    if not Path(args.db).is_file():          # never create an empty ledger here: stage reads run where the search ran
        raise StageRefused(f"no search ledger at {args.db}: stage reads run on the host that searched (the Mac)")
    return s, SearchLedger(args.db, mode="real"), TrialRegistry(args.registry or s.research.registry)


def _stage_reader(args: argparse.Namespace, settings: Any, stage: Any) -> Any:
    """Per timeframe, built once: the stage window's tensor and provider (`confirm.stage_backends`) and its reader."""
    from goldbot.research.autodisc import confirm as C
    built: dict[str, Any] = {}

    def read(spec: StrategySpec) -> Any:
        if spec.timeframe not in built:
            t, prov, cost = C.stage_backends(settings, stage, spec.timeframe, raw=raw_dir(args.raw),
                                             cost_table=args.cost_table)
            built[spec.timeframe] = C.read_on(t, prov, cost)
        return built[spec.timeframe](spec)
    return read


def _stage_cmd(fn: Any) -> int:
    from goldbot.research.autodisc.stages import StageRefused
    from goldbot.research.registry import TrialBudgetExceeded
    try:
        out = fn()
    except (StageRefused, TrialBudgetExceeded) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(out, indent=1, default=str))
    return 0


def cmd_confirm_prereg(args: argparse.Namespace) -> int:
    from goldbot.research.autodisc import confirm as C

    def go() -> Any:
        _, ledger, reg = _stage_args(args)
        return C.preregister_confirm(reg, ledger, args.spec_hash)
    return _stage_cmd(go)


def cmd_confirm_run(args: argparse.Namespace) -> int:
    from goldbot.research.autodisc import confirm as C
    from goldbot.research.registry import quarter_of

    def go() -> Any:
        s, ledger, reg = _stage_args(args)
        q = quarter_of()
        cost = C.stage_costs(s, args.cost_table)[2]
        return [v.model_dump() for v in C.run_confirm_batch(
            reg, ledger, args.batch, _stage_reader(args, s, "confirm"), cap=s.research.budget_for(q),
            reserved=s.research.reserved_for(q), cost_line=cost)]
    return _stage_cmd(go)


def cmd_holdout(args: argparse.Namespace) -> int:
    from goldbot.research.autodisc import confirm as C
    from goldbot.research.registry import quarter_of

    def go() -> Any:
        s, ledger, reg = _stage_args(args)
        q = quarter_of()
        return C.holdout_read(reg, ledger, args.spec_hash, _stage_reader(args, s, "holdout"),
                              cap=s.research.budget_for(q), reserved=s.research.reserved_for(q),
                              cost_line=C.stage_costs(s, args.cost_table)[2]).model_dump()
    return _stage_cmd(go)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    night = sub.add_parser("nightly")
    night.set_defaults(fn=cmd_nightly)
    night.add_argument("--state-dir", default="state")
    night.add_argument("--timeframe", default=None, choices=["15m", "1h", "4h"],
                       help="decision timeframe of this run (default: research.autodisc.timeframe)")
    for name, fn in (("confirm-prereg", cmd_confirm_prereg), ("confirm-run", cmd_confirm_run), ("holdout", cmd_holdout)):
        p = sub.add_parser(name)
        p.set_defaults(fn=fn)
        p.add_argument("--db", default=DEFAULT_DB["real"], help="the real search ledger")
        p.add_argument("--registry", default=None, help="trial registry (default research.registry)")
        if name == "confirm-run":
            p.add_argument("--batch", required=True, help="YYYY-MM: the month's pre-registered batch")
        else:
            p.add_argument("spec_hash")
        if name != "confirm-prereg":
            p.add_argument("--raw", default=None, help="dir of xauusd_1m_*.parquet (default GOLDBOT_RAW_BARS or raw/)")
            p.add_argument("--cost-table", default=None, help="measured cost table JSON; default research.autodisc")
    for name, fn in (("run", cmd_run), ("bench", cmd_bench), ("report", cmd_report)):
        p = sub.add_parser(name)
        p.set_defaults(fn=fn)
        p.add_argument("--synthetic", action="store_true", help="seeded-noise stand-in backends")
        p.add_argument("--db", default=None, help="ledger path (default per mode: state/discovery_real.db or _synthetic.db)")
        p.add_argument("--bars", type=int, default=4000)
        p.add_argument("--seed", type=int, default=0)
        p.add_argument("--min-events", type=int, default=0, help="0 = the evaluator's per-timeframe floor")
        p.add_argument("--run-json", default=None)
        if name == "run":
            p.add_argument("--generator", default="combine")
            p.add_argument("--limit", type=int, default=0)
            p.add_argument("--reps", type=int, default=DEFAULT_REPS)
            p.add_argument("--timeframe", default="15m", choices=["15m", "1h", "4h"])
            p.add_argument("--cost-table", default=None, help="measured cost table JSON (costs_measured.json); default: priors")
            p.add_argument("--raw", default=None, help="dir of xauusd_1m_*.parquet (default GOLDBOT_RAW_BARS or raw/)")
        if name == "bench":
            p.add_argument("--specs", type=int, default=10_000)
    args = ap.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
