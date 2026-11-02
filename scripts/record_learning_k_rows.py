"""Append the registry K rows of the demo learning class (ADR 0061 amendment 2026-11-02): one row per `role: learning`
arm of the spec and one per control dedicated to them, each before its first fill. A learning arm's `learning.k_id` is
the config hash of its row (`research.arms.learning_k_id`), so the loader refuses the arm until this has run on the host
whose registry the engine reads (`research.arms.REGISTRY_PATH`, the brain's state/research_registry.jsonl).

Each row is a trial (a look at demo data): it counts in the quarter's budget and in K. The run checks the quarter's
budget under the registry lock and writes nothing when the rows do not fit (the budget is the owner's to raise:
research.trial_budget_overrides). Idempotent: a row already in the registry is not written again.

Usage: python scripts/record_learning_k_rows.py [--registry state/research_registry.jsonl] [--dry-run]"""
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
    ap.add_argument("--dry-run", action="store_true", help="list the rows that would be written, write nothing")
    args = ap.parse_args()
    arms = record(Path(args.registry), dry_run=args.dry_run)
    print(("would write" if args.dry_run else "wrote") + f" {len(arms)} K rows: {', '.join(arms) or '-'}")


if __name__ == "__main__":
    main()
