"""Phase 0 definition-of-done dry run: synthetic ticks -> bars -> features -> session-open candidates ->
labels -> purged walk-forward -> registry row. Expect ~zero edge on synthetic data; the point is that the
whole path runs green without lookahead. Usage: python scripts/dry_run.py [years]"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from goldbot.data.quality import check_bars, events_frame  # noqa: E402
from goldbot.data.resample import resample_bars, ticks_to_1m  # noqa: E402
from goldbot.data.store import Store  # noqa: E402
from goldbot.data.synthetic import synthetic_ticks  # noqa: E402
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
    b15, b1h, b1d = (resample_bars(b1, tf) for tf in ("15m", "1h", "1d"))
    for tf, b in (("15m", b15), ("1h", b1h), ("1d", b1d)):
        store.append(f"bars_{tf}", b, source="synthetic")
    print(f"15m {len(b15):,}  1h {len(b1h):,}  1d {len(b1d):,}   [{time.time() - t0:.0f}s]")

    spec = SPECIALISTS["session_open"]()
    res = run_specialist(spec, b15, context={"h1": b1h, "d1": b1d}, n_trials=1)
    print(f"candidates: {res.n_candidates}  folds: {res.n_folds}  feature_version: {res.feature_version}")
    for k, v in res.metrics.items():
        print(f"  {k}: {v}")
    if res.importance is not None:
        print("top features:\n", res.importance.head(10).to_string())

    # leakage check on the labelled frame
    m, X = build_decision_frame(b15, {"h1": b1h, "d1": b1d})
    lab = res.oof
    feats = X.drop(columns=["ts_utc"]).iloc[lab["idx"].values].reset_index(drop=True)
    cols = [c for c in feats.columns if feats[c].notna().mean() > 0.8][:40]
    auc = shuffle_test_auc(feats.fillna(0), lab["target_hit"].astype(int), cols)
    print(f"shuffle-test AUC (should be ~0.5): {auc:.3f}")

    reg = TrialRegistry("data_dryrun/registry.jsonl")
    row = reg.record(agent_id=res.agent_id, family=spec.family, config=spec.config, feature_version=res.feature_version,
                     rationale="Phase 0 dry run on synthetic data", results=res.metrics, status="dry_run")
    print(f"registry trial #{row['trial']} written. total {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 3)
