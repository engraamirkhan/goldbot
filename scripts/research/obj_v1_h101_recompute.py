"""Recompute H-101 (zoo-v1) Stage L under obj-v1 (ML lifecycle plan 2026-11-25, N1 acceptance and N2 retro).

Descriptive only, 0 K: the 2012-17 Stage L rows were read on 2026-11-25 (seen); nothing here selects, gates or
re-judges H-101, whose verdict (NULL) stands under its own signed objective. The run:
1. reads the Stage L OOF rows (`learn_oof.parquet`) and keeps `model_zoo.judged` (the 104,367 judged rows);
2. per judged block y, the decision-time cost(x) (research/decision_cost.py) with per-arm priors from the learn rows
   labelled before 1 January y; k from entry / risk_usd when the cache carries them, else backed out of the realised
   leg exactly (stop flag r_mid <= −1 incl. gapped stops, nights held, signed swap rate);
3. per zoo candidate, the obj-v1 read: S, its day-block lower bound, kept mean, the four-part S decomposition, 2x
   cost stress and the gate legs (zoo-v1 has no control leg, so no candidate can pass: expected);
4. the baseline ladder (N2 + A23) walk-forward by judged block, the keeps pooled and read once; the best rung with
   and without the in-sample B8.

The "model" on the ladder is the best of the 8 by S on these same rows: an in-sample choice that favours the model
(ADR 0102). Market data, labels and outputs stay local (--cache, default ~/tmp/history-replay/model-zoo): never
committed. Expected: S of every zoo candidate about −0.021 R (B5 −0.0207).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from goldbot.data import holdout_guard  # noqa: E402
from goldbot.research import baselines as B  # noqa: E402
from goldbot.research import decision_cost as C  # noqa: E402
from goldbot.research import model_zoo as Z  # noqa: E402
from goldbot.research import objective as O  # noqa: E402

FEATURE_COLS = ["session_id", "em_atr_cost", "side"]
LABEL_COLS = ["candidate_id", "arm", "decision_utc", "label_exit_utc", "max_hold_s", "session_day", "r", "r_mid"]


def _block_start(y: int) -> pd.Timestamp:
    return pd.Timestamp(f"{int(y)}-01-01", tz="UTC")


def _labelled_before(d: pd.DataFrame, start: pd.Timestamp) -> pd.DataFrame:
    return d[pd.to_datetime(d["label_exit_utc"], utc=True) < start]


def frame(cache: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(judged rows with `cost` = cost(x) under their block's priors, all learn rows with k) in the obj-v1 shape."""
    path = cache / "learn_rows.parquet"
    extra = [c for c in ("entry", "risk_usd", "barrier_hit") if c in pq.read_schema(path).names]
    feats = pd.read_parquet(path, columns=[*LABEL_COLS, *FEATURE_COLS, *extra]).drop_duplicates("candidate_id")
    holdout_guard.refuse_labels(feats, what="obj-v1 H-101 recompute (learn rows)")
    feats["k"] = C.row_k(feats)
    j = Z.judged(pd.read_parquet(cache / "learn_oof.parquet"))
    holdout_guard.refuse_labels(j, what="obj-v1 H-101 recompute (Stage L OOF rows)")
    j = j.merge(feats[["candidate_id", "k", *FEATURE_COLS, *extra]], on="candidate_id", how="left",
                validate="one_to_one")
    cost = np.full(len(j), np.nan)
    for y in sorted(j["block"].unique()):
        m = (j["block"] == y).to_numpy()
        start = _block_start(y)
        pri = C.priors(_labelled_before(feats, start), before=start)
        cost[m] = C.cost_x(j[m], pri, k=j.loc[m, "k"].to_numpy(float))

    def shape(d: pd.DataFrame) -> pd.DataFrame:
        return d.assign(net=d["r"], gross=d["r_mid"], day=d["session_day"], t0=d["decision_utc"],
                        t1=d["label_exit_utc"]).reset_index(drop=True)

    return shape(j.assign(cost=cost)), shape(feats)


def read_dict(rd: O.ObjectiveRead) -> dict[str, Any]:
    keys = ("n", "n_days", "n_kept", "abstain", "keep_share", "mean_net", "kept_mean", "s", "s_lo", "s_stress",
            "decomposition", "quarantined")
    d = rd.model_dump()
    return {k: d[k] for k in keys}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache", type=Path, default=Path.home() / "tmp/history-replay/model-zoo")
    ap.add_argument("--boot", type=int, default=O.BOOT)
    a = ap.parse_args(argv)
    j, learn = frame(a.cache)
    net, gross, day = j["net"].to_numpy(float), j["gross"].to_numpy(float), j["day"].to_numpy()
    cx = j["cost"].to_numpy(float)
    grouped = j[["arm"]].assign(cx=cx, g=O.clip_r(gross), real=gross - net).groupby("arm")[["cx", "g", "real"]]
    dm = grouped.transform(lambda x: x - x.mean())
    out: dict[str, Any] = {"objective_id": O.OBJECTIVE_ID, "n_judged": len(j), "zoo": {},
                           "within_arm_corr_cost_x_gross": float(np.corrcoef(dm["cx"], dm["g"])[0, 1]),
                           "within_arm_corr_realised_cost_gross": float(np.corrcoef(dm["real"], dm["g"])[0, 1])}
    for c in Z.CANDIDATES:
        rd = O.read(net, gross, j[f"keep_{c}"].to_numpy(bool), day, cost_x=cx, boot=a.boot)
        out["zoo"][c] = {**read_dict(rd), "gate_fails": list(O.gate(rd).reasons)}
    best = max(Z.CANDIDATES, key=lambda c: out["zoo"][c]["s"])
    model_keep = j[f"keep_{best}"].to_numpy(bool)
    pooled: dict[str, list[np.ndarray]] = {}
    notes: dict[str, str] = {}
    order: list[np.ndarray] = []
    for y in sorted(j["block"].unique()):
        m = (j["block"] == y).to_numpy()
        start = _block_start(y)
        lr = learn[(learn["t0"] < start) & (learn["t1"] < start)].copy()
        lr["cost"] = C.cost_x(lr, C.priors(lr, before=start), k=lr["k"].to_numpy(float))
        keeps, n = B.ladder_keeps(j[m], lr, model_keep=model_keep[m], cells=("arm", "session_id"),
                                  rule_cols=("cost", "em_atr_cost"))
        notes.update({k: f"{v} (block {y})" if k == "B4" else v for k, v in n.items()})
        order.append(np.flatnonzero(m))
        for k, v in keeps.items():
            pooled.setdefault(k, []).append(v)
    idx = np.concatenate(order)
    rows = j.iloc[idx].reset_index(drop=True)
    lad = B.read_ladder(rows, {k: np.concatenate(v) for k, v in pooled.items()}, model_keep=model_keep[idx],
                        notes=notes)
    bar, bar_s = B.best_bar(lad)
    bar_t, bar_t_s = B.best_bar(lad, include_b8=False)
    model = O.read(rows["net"].to_numpy(float), rows["gross"].to_numpy(float), model_keep[idx],
                   rows["day"].to_numpy(), cost_x=rows["cost"].to_numpy(float), boot=0)
    out["ladder"] = {"model": best, "rungs": {k: v.model_dump() for k, v in lad.items()}, "best_bar": bar,
                     "best_bar_s": bar_s, "best_tradeable": bar_t, "best_tradeable_s": bar_t_s, "model_s": model.s,
                     "model_beats_ladder": B.beats_ladder(model, lad)}
    print(json.dumps(out, indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
