"""Fit and record SEQ-1's outcome-free embedding on gold xt-v1 history (goldbot/research/seq1_embed.py, quant ruling
R-c; run record: history_runs.record_seq1_embed -> run_manifest.fit_and_record, ADR 0109).

    python scripts/research/train_seq1_embed.py --tfs 1h --threads 8
    python scripts/research/train_seq1_embed.py --tfs 1h,2h,4h,1d --threads 8 --out ~/tmp/seq1-embed-pooled

Reads NO label value and books no K: the decisions are the xt-v1 label store's decision bars from the outcome-blind
skeleton (`dense_labels.skeleton` of the partitions `learn_labels.xt_keys` resolves: current spec and code hashes,
exit_freeze_sha = EXIT_FREEZE_SHA256), so each embedding row joins one label row of the same store later; the inputs
are the 64 closed bars visible at each decision. Learn 2010-01..2017-12 only: minutes are loaded for the learn years
only (no test_old price enters a window; the first `window` bars of 2010 are warm-up) and the holdout is cut at load
(`feature_store.history_minutes`); a decision whose horizon passes the learn end is not a row.

Outputs (--out, default ~/tmp/seq1-embed-<date>; market-derived, never committed, ADR 0048): data/ (the design .npz the
manifest hashes), manifests/ (the run manifest; rebuild with scripts/research/rebuild_model.py), summary.json:
- an outer fold (train: decisions before --oof-from, purged by `seq1_embed.fold_embed`; test: from --oof-from): the
  pretext loss in fold (fit rows, inner validation rows) and out of fold (test rows whose whole window is after the
  cut), beside the zero-prediction loss of the same rows (the scale a learned pretext has to beat); with
  --seed-stability, the 5 seeds' pairwise linear CKA on the out-of-fold rows (a report, never a selector);
- the recorded fit on every learn row (seed index 0 of seed_list(manifest key)): artifact sha256, manifest id, its
  inner validation loss, wall times.
Threads: torch's intra-op threads = --threads (spec.threads), OMP/MKL set to match; torch is imported before any
goldbot module so its OpenMP runtime comes first (macOS, `seq1.omp_before_torch`).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
GOLD = "XAUUSD"


def _years(s: str) -> list[int]:
    a, _, b = s.partition("-")
    return list(range(int(a), int(b or a) + 1))


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tfs", default="1h", help="comma list of SEQ-1 tfs (1h, 2h, 4h, 1d, 1w); sub-hour is refused")
    ap.add_argument("--years", default="2010-2017", help="learn years only")
    ap.add_argument("--labels", default=str(Path.home() / "tmp" / "dense-labels"))
    ap.add_argument("--raw", default=str(ROOT / "raw"))
    ap.add_argument("--out", default=str(Path.home() / "tmp" / f"seq1-embed-{time.strftime('%Y-%m-%d')}"))
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--oof-from", type=int, default=2016, help="first year of the outer fold's test rows")
    ap.add_argument("--max-epochs", type=int, default=None, help="override the spec's max_epochs (smoke runs only)")
    ap.add_argument("--manifest-key", default=None)
    ap.add_argument("--seed-stability", action="store_true",
                    help="also fit the spec's 5 seeds on the fold's training rows: pairwise linear CKA out of fold")
    return ap


def decisions(labels: Path, tf: str, years: list[int]) -> Any:
    """The xt-v1 store's decision bars of gold `tf` (outcome-blind skeleton) whose horizon ends inside learn."""
    import pandas as pd

    from goldbot.research import dense_labels as D
    from goldbot.research import learn_labels as LL
    from goldbot.research import windows as WN
    keys = LL.xt_keys(labels, instrument=GOLD, tf=tf, years=years)
    missing = [y for y in years if (y, 0) not in keys]
    if missing:
        raise SystemExit(f"refused: no xt-v1 partition of {GOLD} {tf} for {missing} under the current spec and code "
                         "(scripts/research/build_dense_labels.py)")
    sk = D.skeleton(labels, instrument=GOLD, tf=tf, years=years, keys=keys)
    dec = pd.DatetimeIndex(pd.to_datetime(sk["decision_utc"], utc=True).unique()).sort_values()
    lo, hi = WN.SPANS[WN.Window.LEARN]
    end = dec + pd.Timedelta(seconds=D.H_BY_TF[tf])
    return pd.Series(dec[(dec >= lo) & (end <= hi)]), {f"{y}:{p}": k for (y, p), k in sorted(keys.items())}


def bars(minutes: Any, tf: str) -> Any:
    """`tf` bars on the label store's decision grid (dense_labels.decision_bars: phase 0, late-revised bars dropped)."""
    from goldbot.data.resample import BAR_COLUMNS, resample_bars
    from goldbot.research import dense_labels as D
    return resample_bars(D.Minutes(minutes).frame[BAR_COLUMNS], tf, drop_late=True)


def zero_loss(X: Any) -> float | None:
    """The pretext loss of predicting 0 for every target (the scale of the task on these rows)."""
    from goldbot.research import seq1_embed as E
    t, m = E.pretext_targets(X)
    n = float(m.sum())
    return float(((t ** 2) * m[:, :, None]).sum() / n) if n > 0 else None


def main(argv: list[str] | None = None) -> int:
    a = parser().parse_args(argv)
    for v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[v] = str(a.threads)
    import numpy as np
    import pandas as pd
    import torch  # noqa: F401  first: its OpenMP runtime before sklearn/lightgbm's (seq1.omp_before_torch)

    from goldbot.data import htf_bars as H
    from goldbot.research import dense_labels as D
    from goldbot.research import feature_store as F
    from goldbot.research import history_runs as HR
    from goldbot.research import seq1 as S
    from goldbot.research import seq1_embed as E
    from goldbot.research import windows as WN

    t0 = time.perf_counter()
    tfs = [t.strip() for t in a.tfs.split(",") if t.strip()]
    for tf in tfs:
        S.judged_tf(tf)                                   # sub-hour and unknown tfs refused (R11)
    years = _years(a.years)
    lo, hi = WN.SPANS[WN.Window.LEARN]
    if not all(lo.year <= y < hi.year for y in years):
        raise SystemExit(f"refused: the embedding fits learn {lo.year}..{hi.year - 1} only, not {years}")
    out = Path(a.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    m = F.history_minutes(GOLD, years[0], years[-1], Path(a.raw))   # holdout cut at load; learn years only
    dec, keys, bb = {}, {}, {}
    for tf in tfs:
        dec[(GOLD, tf)], keys[tf] = decisions(Path(a.labels), tf, years)
        bb[(GOLD, tf)] = bars(m, tf)
    base = E.SPEC.base.model_copy(update={"threads": a.threads, **({"max_epochs": a.max_epochs}
                                                                    if a.max_epochs else {})})
    key = a.manifest_key or f"seq1-embed:{GOLD}:{'+'.join(tfs)}:{years[0]}-{years[-1]}:xt-v1"
    spec = HR.embed_spec(key, E.SPEC.model_copy(update={"base": base}))
    data = E.rows(dec, bb, spec)
    t_design = time.perf_counter() - t0
    dv = hashlib.sha256(json.dumps({"label_spec": D.spec_hash(), "label_code": D.code_hash(),
                                    "exit_freeze": D.EXIT_FREEZE_SHA256, "xt_keys": keys,
                                    "minutes_sha256": H.minutes_sha(m)}, sort_keys=True).encode()).hexdigest()

    # the outer fold: purged train before --oof-from, test from it
    d = pd.DatetimeIndex(pd.to_datetime(data.keys["decision_utc"], utc=True))
    cut = pd.Timestamp(f"{a.oof_from}-01-01", tz="UTC")
    tr, te = np.asarray(d < cut), np.asarray(d >= cut)
    # the out-of-fold read: test rows whose whole window is after the cut (a window reaching back into the training
    # span holds bars the fit used as pretext targets)
    te_read = te & np.asarray(pd.DatetimeIndex(pd.to_datetime(data.keys["window_from_utc"], utc=True)) >= cut)
    t1 = time.perf_counter()
    art_f, kept, _ = E.fold_embed(data, tr, te, spec=spec)
    fit_rows, val_rows = S.inner_split(data.take(kept), spec.base)
    fold = {"train_rows": int(tr.sum()), "kept_after_purge": int(kept.sum()), "test_rows": int(te.sum()),
            "test_rows_read": int(te_read.sum()), "embargo": str(E.fold_embargo(data, spec)),
            "artifact_sha256": art_f.sha256, "best_epoch": art_f.best_epoch,
            "loss_in_fold_fit_rows": E.pretext_loss(art_f, fit_rows.X),
            "loss_in_fold_validation": art_f.val_loss,
            "loss_out_of_fold": E.pretext_loss(art_f, data.X[te_read]),
            "zero_loss_in_fold_fit_rows": zero_loss(fit_rows.X), "zero_loss_in_fold_validation": zero_loss(val_rows.X),
            "zero_loss_out_of_fold": zero_loss(data.X[te_read]),
            "wall_s": round(time.perf_counter() - t1, 1)}
    print(json.dumps({"fold": fold}, indent=1), flush=True)
    if a.seed_stability:                 # a report, never a selector: the served embedding stays seed index 0
        t3 = time.perf_counter()
        st = E.seed_stability(data.take(kept), data.X[te_read], spec)
        fold["seed_stability"] = {**st.model_dump(mode="json"), "wall_s": round(time.perf_counter() - t3, 1)}
        print(json.dumps({"seed_stability": fold["seed_stability"]}, indent=1), flush=True)

    t2 = time.perf_counter()
    art, man = HR.record_seq1_embed(data, manifest_key=key, data_root=out / "data", data_version=dv,
                                    root=out / "manifests", spec=spec, repo=ROOT,
                                    results={"outer_fold": fold, "tfs": tfs, "rows": len(data)})
    full = {"artifact_sha256": art.sha256, "manifest_id": man.manifest_id, "best_epoch": art.best_epoch,
            "n_train": art.n_train, "n_val": art.n_val, "loss_validation": art.val_loss,
            "zero_loss_validation": zero_loss(S.inner_split(data, spec.base)[1].X), "seed": art.seed,
            "code_sha": man.code_sha, "code_dirty": man.code_dirty, "wall_s": round(time.perf_counter() - t2, 1)}
    summary = {"manifest_key": key, "instrument": GOLD, "tfs": tfs, "years": [years[0], years[-1]],
               "rows": len(data), "rows_by_tf": {t: int((data.keys["tf"] == t).sum()) for t in tfs},
               "data_version": dv, "xt_keys": keys, "threads": a.threads, "spec_id": spec.spec_id,
               "max_epochs": spec.base.max_epochs, "design_s": round(t_design, 1), "fold": fold, "full": full,
               "wall_s": round(time.perf_counter() - t0, 1), "labels_read": 0, "k_booked": 0}
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print(json.dumps({"full": full, "wall_s": summary["wall_s"]}, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
