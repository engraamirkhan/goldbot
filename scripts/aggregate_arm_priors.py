"""Per-arm priors from the matrix's per-block trade files: OOS 2019-2025 trades only, weight halved and capped at 50
trades (goldbot.research.arms.build_prior). Usage: python scripts/aggregate_arm_priors.py --in arm_out --out priors.json"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.research import arms as A  # noqa: E402


def aggregate(folder: Path) -> dict[str, dict[str, float]]:
    per_arm: dict[str, list[pd.DataFrame]] = {}
    for f in sorted(folder.rglob("*.parquet")):
        per_arm.setdefault(f.stem.split("__")[0], []).append(pd.read_parquet(f))
    out = {}
    for arm, frames in per_arm.items():
        t = pd.concat(frames, ignore_index=True)
        A.assert_no_holdout_rows(t)
        out[arm] = {**A.build_prior(t), "n_total": int(len(t))}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", default="arm_out")
    ap.add_argument("--out", default="priors.json")
    args = ap.parse_args()
    priors = aggregate(Path(args.src))
    Path(args.out).write_text(json.dumps(priors, indent=2, sort_keys=True))
    for arm, p in sorted(priors.items()):
        print(f"{arm}: n_oos={p['n_oos']} mean_r={p['mean_r']:.4f} weight={p['weight']:.1f}")


if __name__ == "__main__":
    main()
