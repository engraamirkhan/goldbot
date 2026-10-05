"""Phase 0 definition-of-done dry run: synthetic ticks -> bars -> features -> session-open candidates ->
labels -> purged walk-forward -> registry row. Expect ~zero edge on synthetic data; the point is that the
whole path runs green without lookahead. Usage: python scripts/dry_run.py [years]"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from goldbot.data.quality import check_bars  # noqa: E402
from goldbot.data.resample import resample_bars, ticks_to_1m  # noqa: E402
from goldbot.data.store import Store  # noqa: E402
from goldbot.data.synthetic import synthetic_ticks  # noqa: E402
from goldbot.features.mtf import TF_LABEL, context_tfs  # noqa: E402
from goldbot.research.model import shuffle_test_auc  # noqa: E402
from goldbot.research.pipeline import build_decision_frame, run_specialist  # noqa: E402
from goldbot.research.registry import TrialRegistry  # noqa: E402
from goldbot.specialists import SPECIALISTS  # noqa: E402


def main(years: int = 3) -> None:
    t0 = time.time()
    end = pd.Timestamp("2025-10-01")
    start = end - pd.DateOffset(years=years)
    print(f"generating synthetic ticks {start.date()} -> {end.date()} ...")
    ticks = synthetic_ticks(str(start.date()), str(end.date()), ticks_per_minute=1, seed=11)
    b1 = ticks_to_1m(ticks)
    b1, dq = check_bars(b1)
    print(f"1m bars: {len(b1):,}  dq events: {len(dq)}")
    store = Store("data_dryrun")
    store.append("bars_1m", b1, source="synthetic")
    bars = {tf: resample_bars(b1, tf) for tf in ("15m", *context_tfs("15m"))}
    for tf, b in bars.items():
        store.append(f"bars_{tf}", b, source="synthetic")
    print("  ".join(f"{tf} {len(b):,}" for tf, b in bars.items()) + f"   [{time.time() - t0:.0f}s]")
    b15 = bars["15m"]
    context = {TF_LABEL[tf]: bars[tf] for tf in context_tfs("15m")}

    spec = SPECIALISTS["session_open"]()
    res = run_specialist(spec, b15, context=context, n_trials=1)
    print(f"candidates: {res.n_candidates}  folds: {res.n_folds}  feature_version: {res.feature_version}")
    for k, v in res.metrics.items():
        print(f"  {k}: {v}")
    if res.importance is not None:
        print("top features:\n", res.importance.head(10).to_string())

    # leakage check on the labelled frame
    m, X = build_decision_frame(b15, context)
    lab = res.oof
    feats = X.drop(columns=["ts_utc"]).iloc[lab["idx"].to_numpy()].reset_index(drop=True)
    cols = [c for c in feats.columns if feats[c].notna().mean() > 0.8][:40]
    auc = shuffle_test_auc(feats.fillna(0), lab["target_hit"].astype(int), cols)
    print(f"shuffle-test AUC (should be ~0.5): {auc:.3f}")

    reg = TrialRegistry("data_dryrun/registry.jsonl")
    row = reg.record(agent_id=res.agent_id, family=spec.family, config=spec.config, feature_version=res.feature_version,
                     rationale="Phase 0 dry run on synthetic data", results=res.metrics, status="dry_run")
    print(f"registry trial #{row['trial']} written. total {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 3)
