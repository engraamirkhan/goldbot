"""Recompute H-101 (zoo-v1) Stage L under obj-v1 (ML lifecycle plan 2026-11-25, N1 acceptance and N2 retro).

Descriptive only, 0 K: the 2012-17 Stage L rows were read on 2026-11-25 (seen); nothing here selects, gates or
re-judges H-101, whose verdict (NULL) stands under its own signed objective. The run:
1. reads the Stage L OOF rows (`learn_oof.parquet`) and keeps `model_zoo.judged` (the 104,367 judged rows);
2. per zoo candidate, the obj-v1 read: S, its day-block lower bound, kept mean, the S decomposition, 2x cost stress
   and the gate legs (no control leg exists for zoo-v1, so no candidate can pass: expected);
3. the baseline ladder (N2 + A23) walk-forward by judged block: for block year y the learn rows are the H-101 learn
   rows decided and labelled before 1 January y; the keeps of all blocks are pooled and read once.

Cost. The cost CHARGED is the replay's realised leg r_mid − r (W2 bps: RT_BP of the entry, OVERSHOOT_BP more on a stop
exit, swap). That leg is NOT known at decision (the overshoot is charged only on stop exits), and a baseline or keep
rule fed with it leaks the outcome (a "cost <= x" rule on it scores S ≈ +0.054 R on these rows). R-I's cost(x) must be
known at decision, so the `cost` column given to the keep rules is rebuilt from decision-time quantities:
k = entry · 1e-4 / risk is backed out per row (stop exit: r_mid = −1 at mid, cost = k (RT + OVERSHOOT); otherwise
cost = k · RT, swap ignored), and cost(x) = k · (RT_BP + OVERSHOOT_BP · p_stop), with p_stop the stop share of the
rows labelled before 2012-01-01 (before every judged row).
Market data, labels and outputs stay local (--cache, default ~/tmp/history-replay/model-zoo): never committed.
Expected: S of every zoo candidate about −0.021 R (best, B5: −0.0207).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from goldbot.data import holdout_guard  # noqa: E402
from goldbot.research import baselines as B  # noqa: E402
from goldbot.research import history_replay as H  # noqa: E402
from goldbot.research import model_zoo as Z  # noqa: E402
from goldbot.research import objective as O  # noqa: E402

FEATURE_COLS = ("session_id", "em_atr_cost")
FIRST_JUDGED = pd.Timestamp("2012-01-01", tz="UTC")


def _is_stop(r_mid: pd.Series) -> np.ndarray:
    return np.isclose(r_mid.to_numpy(float), -1.0, atol=1e-9)


def decision_cost(d: pd.DataFrame, p_stop: float) -> np.ndarray:
    """cost(x) known at decision: k (RT_BP + OVERSHOOT_BP · p_stop), k backed out of the realised W2 leg."""
    realised = (d["r_mid"] - d["r"]).to_numpy(float)
    k = np.where(_is_stop(d["r_mid"]), realised / (H.RT_BP + H.OVERSHOOT_BP), realised / H.RT_BP)
    return k * (H.RT_BP + H.OVERSHOOT_BP * p_stop)


def frame(cache: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(judged rows, all learn rows) in the obj-v1 row shape."""
    feats = pd.read_parquet(cache / "learn_rows.parquet",
                            columns=["candidate_id", "arm", "decision_utc", "label_exit_utc", "session_day", "r",
                                     "r_mid", "max_hold_s", *FEATURE_COLS]).drop_duplicates("candidate_id")
    holdout_guard.refuse_labels(feats, what="obj-v1 H-101 recompute (learn rows)")

    early = feats[feats["label_exit_utc"] < FIRST_JUDGED]
    p_stop = float(_is_stop(early["r_mid"]).mean())

    def shape(d: pd.DataFrame) -> pd.DataFrame:
        return d.assign(net=d["r"], gross=d["r_mid"], cost=decision_cost(d, p_stop), day=d["session_day"],
                        t0=d["decision_utc"], t1=d["label_exit_utc"]).reset_index(drop=True)

    j = Z.judged(pd.read_parquet(cache / "learn_oof.parquet"))
    holdout_guard.refuse_labels(j, what="obj-v1 H-101 recompute (Stage L OOF rows)")
    j = j.merge(feats[["candidate_id", *FEATURE_COLS]], on="candidate_id", how="left", validate="one_to_one")
    return shape(j), shape(feats)


def read_dict(rd: O.ObjectiveRead) -> dict[str, Any]:
    return {k: rd.model_dump()[k] for k in ("n", "n_days", "n_kept", "abstain", "keep_share", "mean_net",
                                            "kept_mean", "s", "s_lo", "s_stress", "decomposition", "quarantined")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache", type=Path, default=Path.home() / "tmp/history-replay/model-zoo")
    ap.add_argument("--boot", type=int, default=2_000)
    a = ap.parse_args(argv)
    j, learn = frame(a.cache)
    net, gross, day = j["net"].to_numpy(float), j["gross"].to_numpy(float), j["day"].to_numpy()
    out: dict[str, Any] = {"objective_id": O.OBJECTIVE_ID, "n_judged": len(j), "zoo": {}}
    for c in Z.CANDIDATES:
        rd = O.read(net, gross, j[f"keep_{c}"].to_numpy(bool), day, boot=a.boot)
        out["zoo"][c] = {**read_dict(rd), "gate_fails": list(O.gate(rd, control_kept_mean=None).reasons)}
    best = max(Z.CANDIDATES, key=lambda c: out["zoo"][c]["s"])
    model_keep = j[f"keep_{best}"].to_numpy(bool)
    pooled: dict[str, list[np.ndarray]] = {}
    notes: dict[str, str] = {}
    order: list[np.ndarray] = []
    for y in sorted(j["block"].unique()):
        m = (j["block"] == y).to_numpy()
        start = pd.Timestamp(f"{int(y)}-01-01", tz="UTC")
        lr = learn[(learn["t0"] < start) & (learn["t1"] < start)]
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
    model = O.read(rows["net"].to_numpy(float), rows["gross"].to_numpy(float), model_keep[idx],
                   rows["day"].to_numpy(), boot=0)
    out["ladder"] = {"model": best, "rungs": {k: v.model_dump() for k, v in lad.items()}, "best_bar": bar,
                     "best_bar_s": bar_s, "model_s": model.s, "model_beats_ladder": B.beats_ladder(model, lad)}
    print(json.dumps(out, indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
