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
from goldbot.research.registry import TrialRegistry  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def record(registry: Path, priors: dict[str, Any], run_id: str, arms_file: Path, costs_file: Path) -> dict[str, Any]:
    arms = [a for a in A.load_arms(arms_file) if a.status == "run"]
    costs = A.load_costs(costs_file)
    config = {"arms": {a.id: a.config_hash for a in arms}, "blocks": [f"{s}:{e}" for s, e in A.blocks()],
              "round_trip_usd": costs.round_trip_usd, "overshoot_scenarios_usd": list(costs.overshoot_scenarios_usd),
              "holdout_from": str(A.HOLDOUT_FROM.date()), "run_id": run_id}
    return TrialRegistry(registry).record(
        agent_id="arms-matrix", family="arms_matrix", config=config, feature_version="n/a",
        rationale="H-60 historical exploration arms, ARM-SPEC 2026-10-25: 0-trial look, no verdict, no promotion",
        results={"kind": "historical_arms_matrix", "n_arms": len(arms), "priors": priors})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", required=True)
    ap.add_argument("--priors", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--arms-file", default=str(ROOT / "config" / "exploration_arms.yaml"))
    ap.add_argument("--costs", default=str(ROOT / "config" / "arm_costs.yaml"))
    args = ap.parse_args()
    row = record(Path(args.registry), json.loads(Path(args.priors).read_text()), args.run_id,
                 Path(args.arms_file), Path(args.costs))
    print(f"registry row {row['trial']} written for run {args.run_id}")


if __name__ == "__main__":
    main()
