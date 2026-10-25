"""Per-arm priors from the matrix's per-block trade files: OOS 2019-2025 trades only, prior = 0.5 x their mean net R,
weight min(50, n / 100) trades (goldbot.research.arms.build_prior), for the net R at overshoot 0 and at $0.70.
Usage: python scripts/aggregate_arm_priors.py --in arm_out --out priors.json"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.research import arms as A  # noqa: E402


def aggregate(folder: Path) -> dict[str, dict[str, Any]]:
    per_arm: dict[str, list[pd.DataFrame]] = {}
    for f in sorted(folder.rglob("*.parquet")):
        per_arm.setdefault(f.stem.split("__")[0], []).append(pd.read_parquet(f))
    out = {}
    for arm, frames in per_arm.items():
        t = pd.concat(frames, ignore_index=True)
        A.assert_no_holdout_rows(t)
        out[arm] = {"overshoot_0": A.build_prior(t, "net_r"), "overshoot_0.70": A.build_prior(t, "net_r_os"),
                    "n_total": int(len(t))}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", default="arm_out")
    ap.add_argument("--out", default="priors.json")
    args = ap.parse_args()
    priors = aggregate(Path(args.src))
    Path(args.out).write_text(json.dumps(priors, indent=2, sort_keys=True))
    for arm, p in sorted(priors.items()):
        a, b = p["overshoot_0"], p["overshoot_0.70"]
        print(f"{arm}: n_oos={a['n_oos']} prior_r={a['mean_r']:.4f} (os 0.70: {b['mean_r']:.4f}) weight={a['weight']:.2f}")


if __name__ == "__main__":
    main()
