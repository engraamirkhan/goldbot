"""Append the registry K rows of the demo learning class (ADR 0061 amendment 2026-11-02): one row per `role: learning`
arm of the spec and one per control dedicated to them, each before its first fill. A learning arm's `learning.k_id` is
the config hash of its row (`research.arms.learning_k_id`), so the loader refuses the arm until this has run on the host
whose registry the engine reads (`research.arms.REGISTRY_PATH`, the brain's state/research_registry.jsonl).

Each row is a trial (a look at demo data): it counts in the quarter's budget and in K. The run checks the quarter's
budget under the registry lock and writes nothing when the rows do not fit (the budget is the owner's to raise:
research.trial_budget_overrides). Idempotent: a row already in the registry is not written again.

Every learning pre-registration's rows come from the one spec: docs/research/learning-2026-11-02.md (the gold arms) and
docs/research/learning-mi-2026-11-02.md (the multi-instrument pairs, LB-<key> / LBC-<key>) are written together. A
queued instrument of the multi-instrument doc has no spec row, so no K row until it is registered.

`--budget` prints the quarter's count, its cap and the rows still missing, and whether they fit (no write); run it on
the brain before the write.

Usage: python scripts/record_learning_k_rows.py [--registry state/research_registry.jsonl] [--dry-run | --budget]"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.research import arms as A  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "config" / "exploration_arms.yaml"
AGENT = "learning-class"


def k_rows(spec: Path = SPEC) -> dict[str, dict[str, Any]]:
    """K row config hash -> (config, the arm, why) for every learning arm and every control dedicated to them, from
    the rows alone (no registry read: this is what the registry must hold)."""
    raw = A.load_arms(spec, registry=Path("/nonexistent"))       # stamps refuse without a registry: rows only here
    out: dict[str, dict[str, Any]] = {}
    prereg_of: dict[str, str] = {}
    for a in raw:
        if a.role != "learning":
            continue
        block = A.Learning.model_validate(a.learning)
        config = A.learning_k_config(a.id, A.arm_hash(a), block.prereg)
        out[A.learning_k_id(a.id, A.arm_hash(a), block.prereg)] = {"config": config, "arm": a.id, "kind": "learning"}
        prereg_of.setdefault(block.control, block.prereg)
    for a in raw:
        serves = A.dedicated_control_of(a)
        if not serves or a.id not in prereg_of:
            continue
        prereg = prereg_of[a.id]
        config = A.learning_k_config(a.id, A.arm_hash(a), prereg)
        out[A.learning_k_id(a.id, A.arm_hash(a), prereg)] = {"config": config, "arm": a.id, "kind": "control"}
    return out


def budget_report(registry: Path, spec: Path = SPEC, *, cap: int | None = None,
                  quarter: str | None = None) -> dict[str, Any]:
    """The quarter's trials so far, its cap, the K rows still missing from the registry, and whether they fit
    (`TrialRegistry.check_budget`'s arithmetic, without its lock: a read for the operator, never a licence to write).
    `cap` / `quarter` default to the settings' budget of the wall clock's quarter."""
    from goldbot.research.registry import TrialRegistry, quarter_of
    q = quarter_of() if quarter is None else quarter
    if cap is None:
        from goldbot.config import load_settings
        cap = load_settings().research.budget_for(q)
    have: set[Any] = set()
    used = 0
    if Path(registry).exists():                     # a missing registry is read as empty, never created by a read
        reg = TrialRegistry(registry)
        have = {r.get("config_hash") for r in reg._rows()}
        used = reg.budget_used(q)
    missing = sorted(r["arm"] for h, r in k_rows(spec).items() if h not in have)
    return {"quarter": q, "used": used, "cap": cap, "missing": len(missing), "arms": missing,
            "fits": used + len(missing) <= cap}


def record(registry: Path, spec: Path = SPEC, *, dry_run: bool = False) -> list[str]:
    """Write the missing K rows (under the registry lock, after the quarter's budget check); the arm ids written."""
    from goldbot.config import load_settings
    from goldbot.research.registry import TrialRegistry, quarter_of
    rows = k_rows(spec)
    reg = TrialRegistry(registry)
    with reg.locked():
        have = {r.get("config_hash") for r in reg._rows()}
        missing = {h: r for h, r in rows.items() if h not in have}
        if not missing or dry_run:
            return sorted(r["arm"] for r in missing.values())
        research = load_settings().research
        reg.check_budget(len(missing), research.budget_for(quarter_of()))
        for h, r in sorted(missing.items(), key=lambda kv: kv[1]["arm"]):
            row = reg.record(agent_id=AGENT, family=A.LEARNING_FAMILY, config=r["config"], feature_version="n/a",
                             status="learning", rationale=f"demo learning class {r['kind']} arm {r['arm']}: one K row "
                             "before its first fill (ADR 0061 amendment 2026-11-02); never a qualification",
                             results={"kind": f"learning_{r['kind']}", "arm": r["arm"]})
            if row["config_hash"] != h:          # the k_id the spec names must be the row written
                raise RuntimeError(f"{r['arm']}: K row hash {row['config_hash']} is not {h}")
    return sorted(r["arm"] for r in missing.values())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--registry", default=str(A.REGISTRY_PATH))
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="list the rows that would be written, write nothing")
    mode.add_argument("--budget", action="store_true", help="the quarter's count, cap and missing rows; write nothing")
    args = ap.parse_args()
    if args.budget:
        b = budget_report(Path(args.registry))
        print(f"{b['quarter']}: {b['used']} of {b['cap']} used; {b['missing']} learning K rows missing; "
              + ("they fit" if b["fits"] else "they do NOT fit (the budget is the owner's to raise)"))
        return
    arms = record(Path(args.registry), dry_run=args.dry_run)
    print(("would write" if args.dry_run else "wrote") + f" {len(arms)} K rows: {', '.join(arms) or '-'}")


if __name__ == "__main__":
    main()
