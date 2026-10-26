"""Append ONE registry K row for one run of the historical arms matrix (a look at the 2015-2025-09 data: it counts).
Usage: python scripts/record_arms_k_row.py --registry registry.jsonl --priors priors.json --run-id <id>
The row carries the arm config hashes, the blocks, the cost model and the per-arm priors; the workflow saves the registry
afterwards under the shared lock (scripts/registry_release.sh)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.research import arms as A  # noqa: E402
from goldbot.research.registry import DISCOVERY, TrialRegistry, row_ref  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FAMILY = "arms_matrix"
H60_K_EFF = 24      # the frozen prereg's arm count (prereg-2026-10-25-high-volume.md: K +24 once), not len(arms)


def _h60_rows(registry: Path) -> list[dict[str, Any]]:
    return [r for r in TrialRegistry(registry)._rows() if r.get("family") == FAMILY]


def _already_charged(rows: list[dict[str, Any]]) -> bool:
    return any(r.get("status") == DISCOVERY and ((r.get("results") or {}).get("discovery") or {}).get("k_eff", 0) > 0
               for r in rows)


def record(registry: Path, priors: dict[str, Any], run_id: str, arms_file: Path, costs_file: Path) -> dict[str, Any]:
    """One registry row per run. The first charges H-60's K_eff = 24 once (a discovery-status row, which is what
    `n_trials_effective` sums); a rerun is still one look (1 trial) but charges k_eff 0."""
    arms = [a for a in A.load_arms(arms_file) if a.status == "run"]
    costs = A.load_costs(costs_file)
    config = {"arms": {a.id: a.config_hash for a in arms}, "blocks": [f"{s}:{e}" for s, e in A.blocks()],
              "round_trip_usd": costs.round_trip_usd, "overshoot_scenarios_usd": list(costs.overshoot_scenarios_usd),
              "holdout_from": str(A.HOLDOUT_FROM.date()), "run_id": run_id}
    k_eff = 0 if _already_charged(_h60_rows(registry)) else H60_K_EFF
    return TrialRegistry(registry).record(
        agent_id="arms-matrix", family=FAMILY, config=config, feature_version="n/a", status=DISCOVERY,
        rationale="H-60 historical exploration arms, ARM-SPEC 2026-10-25: no verdict, no promotion; K_eff 24 once",
        results={"kind": "historical_arms_matrix", "n_arms": len(arms), "priors": priors,
                 "discovery": {"k_eff": k_eff}})


def correct(registry: Path) -> dict[str, Any] | None:
    """Append-only correction for runs recorded before the fix (status "evaluated", +1 each, no k_eff): one discovery row
    naming them whose own trial count plus k_eff brings H-60's total to exactly 24. None when H-60 is already charged."""
    rows = _h60_rows(registry)
    if _already_charged(rows):
        return None
    wrong = [r for r in rows if r.get("status") != DISCOVERY]
    k_eff = max(H60_K_EFF - len(wrong) - 1, 0)
    return TrialRegistry(registry).record(
        agent_id="arms-matrix", family=FAMILY, config={"correction": True, "corrects": [r.get("config", {}).get("run_id") for r in wrong]},
        feature_version="n/a", status=DISCOVERY,
        rationale="correction: H-60's K was charged +1 per run as evaluated rows; this row brings the program to K +24 once",
        results={"kind": "historical_arms_matrix_correction", "corrects": [row_ref(r) for r in wrong],
                 "discovery": {"k_eff": k_eff}})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", required=True)
    ap.add_argument("--priors")
    ap.add_argument("--run-id")
    ap.add_argument("--arms-file", default=str(ROOT / "config" / "exploration_arms.yaml"))
    ap.add_argument("--costs", default=str(ROOT / "config" / "arm_costs.yaml"))
    ap.add_argument("--correct", action="store_true", help="append the one-off K correction row (idempotent)")
    args = ap.parse_args()
    if args.correct:
        r = correct(Path(args.registry))
        print("already charged" if r is None else f"correction row {r['trial']} written")
        return
    if not args.priors or not args.run_id:
        ap.error("--priors and --run-id are required")
    row = record(Path(args.registry), json.loads(Path(args.priors).read_text()), args.run_id,
                 Path(args.arms_file), Path(args.costs))
    print(f"registry row {row['trial']} written for run {args.run_id}")


if __name__ == "__main__":
    main()
