"""Sprint-3000 batch screen CLI (goldbot/research/sprint3000/engine.py; rules config/research/sprint3000_rules.yaml).

    python scripts/sprint3000_screen.py --validate                                  # catalogue check only, no bars read
    python scripts/sprint3000_screen.py --window explore --out ~/tmp/s3k/explore.json
    python scripts/sprint3000_screen.py --window confirm --passers ~/tmp/s3k/explore.json --out ~/tmp/s3k/confirm.json
    python scripts/sprint3000_screen.py --window test_old --passers ~/tmp/s3k/confirm.json --out ~/tmp/s3k/test_old.json
    python scripts/sprint3000_screen.py --window learn_old --out ~/tmp/s3k/learn_old.json
    python scripts/sprint3000_screen.py --validate --batch levels                   # the key-level batch, no bars read

`--batch levels` runs the 379 key-level specs only (amendment C1 ruling e), each window's tape starting
LEVEL_WARMUP_DAYS before its first decision; every passer report and one-time read is per batch.

Confirm reads explore passers and the family ensembles frozen in the explore report only; test_old reads confirm
passers only (of a report on the same catalogue). Confirm's BH-FDR m is the singles plus families sent, fixed here
before any confirm bar is read. Nothing at or after 2025-10-01 is
ever read. The output holds summary statistics only (no trade row); keep it out of the repository's data paths."""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from goldbot.research import level_vocab as LV  # noqa: E402
from goldbot.research.registry import TrialRegistry  # noqa: E402
from goldbot.research.sprint3000 import engine as E  # noqa: E402
from goldbot.research.sprint3000 import trials as T  # noqa: E402
from goldbot.research.sprint3000 import vocab as V  # noqa: E402
from goldbot.research.sprint3000 import vocab_levels as VL  # noqa: E402
from goldbot.research.sprint3000.market import WINDOWS, Market  # noqa: E402

CATALOGUE = ROOT / "config" / "research" / "sprint3000_catalogue.yaml"


def default_raw() -> Path:
    """The repo's raw/ (a worktree under .claude/worktrees reads the main checkout's)."""
    for p in (ROOT / "raw", ROOT.parents[2] / "raw" if len(ROOT.parents) > 2 else ROOT / "raw"):
        if p.exists():
            return p
    return ROOT / "raw"


def validate(cat: V.Catalogue, batch: V.Batch = "main") -> None:
    rules = E.load_rules()
    pend = Counter(t for s in cat.specs for t in s.pending)
    print(f"{cat.path}: {len(cat.specs)} specs, version {cat.version!r}, sha256 {cat.sha256[:12]}")
    print(f"  batches {dict(Counter(s.batch for s in cat.specs))}, pending (no module registered) "
          f"{sum(bool(s.pending) for s in cat.specs)} {dict(pend)}")
    print(f"  null controls {sum(E.is_null_control(s, rules) for s in cat.specs)}")
    print(f"  by tf {dict(Counter(s.tf for s in cat.specs))}")
    specs = [s for s in cat.specs if s.batch == batch]
    print(f"  batch {batch}: {len(specs)} specs, pending {sum(bool(s.pending) for s in specs)}, null controls "
          f"{sum(E.is_null_control(s, rules) for s in specs)}, families {len({s.family for s in specs})}")
    if batch == "levels":
        print(f"  level types {dict(Counter(t for s in specs for t in s.level_types))}")
        for tf in V.TIMEFRAMES:
            views = LV.filters_for([s.model_dump(mode="json") for s in specs if s.tf == tf])
            names = ", ".join(f"{f.min_strength_pct:g}/{f.min_tf or 'any'}" for f in views)
            if views:
                print(f"  {tf}: {sum(s.tf == tf for s in specs)} specs, zone views {names}")
        print(f"  warm-up {VL.LEVEL_WARMUP_DAYS} days before each window's first decision")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--catalogue", type=Path, default=CATALOGUE)
    ap.add_argument("--raw", type=Path, default=None)
    ap.add_argument("--window", choices=sorted(WINDOWS), default="explore")
    ap.add_argument("--passers", type=Path, help="the explore report (confirm) or the confirm report (test_old)")
    ap.add_argument("--ids", nargs="*", help="a subset of catalogue ids (confirm/test_old: passers only)")
    ap.add_argument("--limit", type=int, help="the first N evaluable specs by rank (engine timing)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--k", type=int, default=None,
                    help="program trial count for the DSR (default: the registry at run time, else the rules file)")
    ap.add_argument("--registry", type=Path, default=ROOT / "state" / "research_registry.jsonl")
    ap.add_argument("--preregister", action="store_true",
                    help="write the catalogue's pre-registration rows (no trial, no budget slot) and stop")
    ap.add_argument("--ledger", type=Path, default=ROOT / "state" / "sprint3000_reads.jsonl",
                    help="confirm/test_old read markers (one read per window and catalogue)")
    ap.add_argument("--out", type=Path, default=Path.home() / "tmp" / "sprint3000" / "explore.json")
    ap.add_argument("--validate", action="store_true", help="load and check the catalogue, read no bars")
    ap.add_argument("--batch", choices=V.BATCHES, default="main",
                    help="main, or the separate key-level batch (amendment C1 ruling e: its own ensembles, BH family "
                         "and reads; its tape starts LEVEL_WARMUP_DAYS before the window's first decision)")
    a = ap.parse_args(argv)
    batch: V.Batch = a.batch
    cat = V.load_catalogue(a.catalogue)
    if a.validate:
        validate(cat, batch)
        return 0
    if a.preregister:
        n = T.preregister_catalogue(TrialRegistry(a.registry), cat)
        print(f"pre-registered {n} rows in {a.registry} ({len(T.planned_rows(cat))} planned; not trials, no budget)")
        return 0
    one_shot = a.window in ("confirm", "test_old")
    if one_shot and (a.ids or a.limit):               # quant review H3: a one-shot window reads its whole plan
        raise SystemExit(f"{a.window} reads its whole plan once: --ids and --limit are refused")
    report = json.loads(a.passers.read_text()) if a.passers else None
    full = E.plan(cat, report, a.window, batch=batch)  # confirm's m: every passer and family sent, fixed before bars
    p = E.plan(cat, report, a.window, a.ids, batch) if a.ids else full
    ids, singles = p.run_ids, p.singles
    if a.limit:
        ids = singles = [s.id for s in cat.specs if s.id in set(ids) and not s.pending and s.batch == batch][: a.limit]
    if not ids:
        print(f"no ids to run on {a.window}")
        return 0
    if a.k is not None:
        k, k_source = a.k, "cli --k"
    elif a.registry.exists():
        k, k_source = T.program_k(TrialRegistry(a.registry), cat)
    else:
        k, k_source = E.load_rules().dsr.k_program, f"rules (no registry at {a.registry} on this host)"
    phash = ""
    if one_shot:
        if E.engine_provenance()[2]:
            raise SystemExit(f"{a.window} refuses a dirty engine: commit the engine, rules and costs first")
        phash = E.plan_hash(full, a.window, cat, batch)
        E.claim_read(a.ledger, a.window, cat, phash, batch)  # before any bar of the window is read
    t0 = time.monotonic()
    win = WINDOWS[a.window] if batch == "main" else VL.level_window(WINDOWS[a.window])
    mk = Market.load(a.raw or default_raw(), win)
    t_load = time.monotonic() - t0
    if mk.years_missing:
        print(f"no data on disk for {list(mk.years_missing)}")
    rep = E.run_screen(cat, mk, workers=a.workers, k=k, cache_dir=Path.home() / "tmp" / "sprint3000", ids=ids,
                       m=full.m, singles=singles, ensembles=p.ensembles if report is not None else None,
                       k_source=k_source, plan_hash=phash, batch=batch)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(rep.model_dump_json(indent=1))
    ev = max(rep.n_evaluated, 1)
    print(f"{a.window}: status {rep.status}{' ENGINE AUDIT REQUIRED' if rep.audit_required else ''}; "
          f"{rep.n_specs} specs ({rep.n_evaluated} evaluated, {rep.n_not_evaluable} not_evaluable), "
          f"{rep.n_passed} passed; nulls {rep.nulls.passed}/{rep.nulls.evaluated}; K {rep.k}, K_eff {rep.k_eff}; "
          f"ensembles {len(rep.ensembles)} ({sum(e.passed for e in rep.ensembles)} passed)")
    print(f"load {t_load:.1f}s, screen {rep.elapsed_s:.1f}s on {a.workers} workers "
          f"({rep.elapsed_s * a.workers / ev:.2f} worker-s per spec); wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
