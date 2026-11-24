"""Weekly self-retraining of a per-arm meta-label FILTER on the exploration replay labels (owner 2026-11-24;
pre-registration docs/research/prereg-2026-11-24-self-retrain.md, spec `SPEC_VERSION`). aux.db migration 43.

Replaces the two retrains retired on 2026-11-13 (docs/research/ml-review-2026-11-13.md: the specialists' Saturday
LightGBM, shadow AUC 0.44 on 145 trades, and the H-62 meta-labeller, 0 model rows because no candidate carried a
feature snapshot). Since then every gold candidate gets its point-in-time feature vector (research/feature_snapshots.py,
`exploration_feature_snapshots`), so the labels can finally be learnt from.

What one run does, per broker (evidence never pooled across brokers, ADR 0043 amendment):
1. Rows: every gold exploration candidate, taken or not, whose r1 replay label (`label_sim_r`, net of the broker's
   cost model; `label_mid_r` is the gross diagnostic) resolved by the data cutoff (`label_exit_ns` <= cutoff, the last
   Friday 21:00 UTC), not quarantined, not void as posterior evidence, with a strictly point-in-time snapshot
   (`pit` = written, `available_utc_max_ns` <= decision). Rows inside the sealed holdout window are refused (none can
   exist: the log starts 2026-10-25; the guard fails the run if one does). Control arms' rows are the random baseline,
   never a training row.
2. Per strategy arm, one fixed model (no grid): median-imputed, standardised L2 logistic regression (C = `C_REG`) on
   the frozen `FEATURES` (39 market columns, the directional ones signed by the candidate's side) + `side` = 40 inputs,
   y = 1[label_sim_r > 0]. Expanding walk-forward over session days: the last half of the days in `N_BLOCKS`
   contiguous test blocks; a training row must have its label EXITED before the block's first decision and be decided
   at least `EMBARGO` before it. The filter keeps a candidate when p >= tau, tau = the `DROP_SHARE` quantile of the
   training fold's own scores (drops the bottom third), fixed before any test row is seen.
3. Gate (`gate`): ACTIVE only if, out of sample, the kept candidates beat BOTH no-model (every candidate of the arm)
   and the arm's random control (its named control, else the broker's controls of its style, decided in the same OOS
   window), each by at least `MIN_EDGE_R` net R per candidate, with minimum counts, and the day-block bootstrap p of
   the edge over no-model, Holm-adjusted across the arms tested in the run, at most `ALPHA`. Otherwise SHADOW.
4. Demotion: an ACTIVE model is DEMOTED when the run's own walk-forward fails the gate, or when its forward record
   (candidates decided after its activation, scored by the frozen model) shows an edge over no-model below
   `DEMOTE_EDGE_R` on at least `MIN_FORWARD` candidates. A new passer SUPERSEDES the arm's previous active model.
5. Every run writes `self_retrain_runs` (label, feature and weight counts, per-arm OOS metrics and decisions) and each
   fitted model `self_retrain_models` (its coefficients as JSON: no pickle, nothing executable is ever loaded).

Scope and safety: this module reads aux.db and writes only its own two tables. An ACTIVE model may only ever drop
(filter) candidates of a demo strategy arm before the RiskGate; it can never add an order, raise a size, touch a control
or a replay_only arm, a risk limit or a live account. Serving in the engine is a separate, trading-safety-reviewed
change: until it lands, ACTIVE is recorded and reported, and nothing in goldbot/engine reads these tables.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Collection, Mapping, Sequence
from typing import Any, Literal

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.db.connection import Database, now_ns
from goldbot.research.exploration_data import GOLD, read_obs
from goldbot.research.model import check_feature_cap

log = logging.getLogger("goldbot.self_retrain")

# ---------------------------------------------------------------------------------------------- the frozen spec
SPEC_VERSION = "sr-v1"
PREREG = "docs/research/prereg-2026-11-24-self-retrain.md"
REGISTRY_FAMILY = "self_retrain_meta_filter"
# 39 market columns of the snapshot vector (research/feature_snapshots.py, the engine's default frame); cost and
# range-to-cost first (the binding constraint in every ML failure so far: cost per R), then volatility regime, clock,
# trend, location and the daily context. Frozen for the quarter; a change is a new SPEC_VERSION and a new K row
FEATURES: tuple[str, ...] = (
    # cost / range-to-cost (8)
    "em_atr_cost", "em_yz_4_cost", "em_yz_16_cost", "em_yz_48_cost", "spread_atr", "spread_rel_median_48",
    "tick_count_z_48", "tick_vol_ratio_20",
    # volatility regime (8)
    "atr_ratio_14_100", "atr14_pct", "rv_ratio", "vol_ts_yz_20_60", "vol_of_vol_60", "jump_z", "bars_since_jump",
    "vol_tercile",
    # clock (5)
    "hour_utc", "dow", "min_since_london_open", "min_since_newyork_open", "session_id",
    # trend / momentum (8)
    "ret_4", "ret_16", "ret_96", "tsmom_score", "adx14", "er_30", "slope_ema50", "dist_ema50_atr",
    # location (7)
    "bb_z_20", "rsi14", "donchian_pos_20", "dist_vwap48_atr", "sess_high_dist_atr", "sess_low_dist_atr",
    "compression_8_96",
    # daily context (3)
    "d1_atr_ratio_14_100", "d1_slope_ema20", "d1_rv_ratio",
)
# directional columns enter signed by the candidate's side (a long and a short read the same move oppositely); the
# centre is subtracted first so the sign flips around neutral
SIGNED: dict[str, float] = {"ret_4": 0.0, "ret_16": 0.0, "ret_96": 0.0, "tsmom_score": 0.0, "slope_ema50": 0.0,
                            "dist_ema50_atr": 0.0, "bb_z_20": 0.0, "rsi14": 50.0, "donchian_pos_20": 0.5,
                            "dist_vwap48_atr": 0.0, "d1_slope_ema20": 0.0}
INPUTS: tuple[str, ...] = (*FEATURES, "side")
check_feature_cap(list(INPUTS))
C_REG = 0.1                     # L2 strength (sklearn C): strong shrinkage, a few hundred rows an arm
DROP_SHARE = 1.0 / 3.0          # the filter drops the bottom third of the training fold's scores
N_BLOCKS = 4                    # OOS test blocks over the last half of the session days
EMBARGO = pd.Timedelta(days=1)
MIN_TRAIN = 100                 # training rows a fold needs (both classes present)
# the promotion gate (pre-registered; never tuned on the data)
MIN_OOS = 200                   # OOS candidates of the arm
MIN_KEPT = 100                  # OOS candidates the filter kept
MIN_OOS_DAYS = 10               # OOS session days
MIN_CONTROL = 50                # OOS control candidates in the same window
MIN_EDGE_R = 0.05               # kept mean minus no-model mean, and kept mean minus control mean, net R per candidate
ALPHA = 0.10                    # Holm-adjusted one-sided bootstrap p of the edge over no-model, across the run's arms
BOOT = 1000                     # day-block bootstrap draws
SEED = 20261124
# demotion (hysteresis below the promotion edge)
DEMOTE_EDGE_R = 0.0
MIN_FORWARD = 60
# health: the run is weekly (Sunday); older than this, or 0 labels, WARNs
RUN_MAX_AGE = pd.Timedelta(days=8)

Status = Literal["active", "shadow", "demoted", "superseded"]


class LogitModel(FrozenRecord):
    """A fitted filter, fully described by numbers (no pickle): impute, standardise, score, compare with tau."""
    inputs: tuple[str, ...]
    medians: tuple[float, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]
    coef: tuple[float, ...]
    intercept: float
    tau: float

    @property
    def n_weights(self) -> int:
        return len(self.coef) + 1

    def score(self, X: pd.DataFrame) -> np.ndarray:
        m = X.reindex(columns=list(self.inputs)).to_numpy(dtype=float)
        m = np.where(np.isfinite(m), m, np.asarray(self.medians))
        z = (m - np.asarray(self.means)) / np.asarray(self.scales)
        return 1.0 / (1.0 + np.exp(-(z @ np.asarray(self.coef) + self.intercept)))

    def keep(self, X: pd.DataFrame) -> np.ndarray:
        return self.score(X) >= self.tau


def fit_logit(X: pd.DataFrame, y: np.ndarray) -> LogitModel:
    """The fixed model on one training set: column medians (0 for an all-missing column), mean/sd standardisation,
    L2 logistic regression at C_REG; tau = the DROP_SHARE quantile of its own training scores."""
    from sklearn.linear_model import LogisticRegression
    m = X.reindex(columns=list(INPUTS)).to_numpy(dtype=float)
    med = np.nanmedian(np.where(np.isfinite(m), m, np.nan), axis=0) if len(m) else np.zeros(m.shape[1])
    med = np.where(np.isfinite(med), med, 0.0)
    m = np.where(np.isfinite(m), m, med)
    mu, sd = m.mean(axis=0), m.std(axis=0)
    sd = np.where(sd > 1e-12, sd, 1.0)
    clf = LogisticRegression(C=C_REG, max_iter=2000)
    clf.fit((m - mu) / sd, y)
    model = LogitModel(inputs=INPUTS, medians=tuple(map(float, med)), means=tuple(map(float, mu)),
                       scales=tuple(map(float, sd)), coef=tuple(map(float, clf.coef_[0])),
                       intercept=float(clf.intercept_[0]), tau=0.0)
    tau = float(np.quantile(model.score(X), DROP_SHARE))
    return model.model_copy(update={"tau": tau})


# ---------------------------------------------------------------------------------------------- rows
def design(rows: pd.DataFrame) -> pd.DataFrame:
    """The model's 40 inputs from the rows' feature columns and side: the SIGNED columns centred and signed."""
    X = rows.reindex(columns=list(FEATURES)).astype(float).replace([np.inf, -np.inf], np.nan)
    side = rows["side"].astype(float).to_numpy()
    for c, centre in SIGNED.items():
        X[c] = (X[c] - centre) * side
    X["side"] = side
    return X.reset_index(drop=True)


def _snapshots(db: Database) -> pd.DataFrame:
    """Strictly point-in-time snapshot vectors (status ok, pit written), with their availability."""
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT s.candidate_id, s.decision_ns, s.available_utc_max_ns, v.features "
            "FROM exploration_feature_snapshots s JOIN exploration_feature_vectors v ON v.sha256 = s.vector_sha256 "
            "WHERE s.status = 'ok' AND s.pit = 'written'").fetchall()
    if not rows:
        return pd.DataFrame(columns=["candidate_id", "_snap_decision_ns", "_avail_ns", *FEATURES])
    vals = pd.DataFrame([json.loads(f) for *_, f in rows]).reindex(columns=list(FEATURES)).astype(float)
    head = pd.DataFrame([r[:3] for r in rows], columns=["candidate_id", "_snap_decision_ns", "_avail_ns"])
    return pd.concat([head, vals], axis=1)


def load_rows(db: Database, *, broker: str, cutoff: pd.Timestamp, holdout: tuple[pd.Timestamp, pd.Timestamp] | None,
              held_out: Collection[str] = (), instruments: Mapping[str, str] | None = None
              ) -> tuple[pd.DataFrame, dict[str, int]]:
    """(rows, counts): the broker's gold candidates whose r1 label resolved by `cutoff`, with a written-pit snapshot.
    Fails (ValueError) on a holdout row or a snapshot available after its decision: never silently dropped."""
    obs = read_obs(db, broker=broker, as_of=cutoff, evidence="posterior", held_out=held_out, instrument=GOLD,
                   instruments=instruments)
    obs = obs[obs["label_sim_r"].notna()].reset_index(drop=True)
    dec = pd.DatetimeIndex(obs["decision_utc"])
    if holdout is not None and len(obs) and ((dec >= holdout[0]) & (dec < holdout[1])).any():
        raise ValueError("self-retrain: a candidate decided inside the sealed holdout window: refused")
    snaps = _snapshots(db)
    rows = obs.merge(snaps, on="candidate_id", how="inner")
    if len(rows):
        if (rows["_avail_ns"].notna() & (rows["_avail_ns"] > rows["_snap_decision_ns"])).any():
            raise ValueError("self-retrain: a snapshot available after its decision (look-ahead): refused")
        if (pd.to_datetime(rows["label_exit_utc"], utc=True) > cutoff).any():
            raise ValueError("self-retrain: a label exits after the cutoff: refused")
    counts = {"labels": int(len(obs)), "with_features": int(len(rows)),
              "no_snapshot": int(len(obs) - len(rows)), "controls": int(rows["is_control"].sum()) if len(rows) else 0}
    return rows.sort_values(["decision_utc", "candidate_id"]).reset_index(drop=True), counts


# ---------------------------------------------------------------------------------------------- walk-forward
def walk_forward(arm_rows: pd.DataFrame) -> pd.DataFrame:
    """Out-of-sample scores of one arm's candidates (module docstring step 2): columns candidate_id, session_day,
    decision_utc, r (net), kept, p. Empty when too few days or training rows."""
    tab = arm_rows.sort_values(["decision_utc", "candidate_id"]).reset_index(drop=True)
    days = np.array(sorted(set(tab["session_day"].astype(str))))
    out: list[pd.DataFrame] = []
    if len(days) < 2 * N_BLOCKS:
        return pd.DataFrame(columns=["candidate_id", "session_day", "decision_utc", "r", "kept", "p"])
    X_all, y_all = design(tab), (tab["label_sim_r"].to_numpy(dtype=float) > 0).astype(int)
    dec = pd.DatetimeIndex(pd.to_datetime(tab["decision_utc"], utc=True))
    ext = pd.DatetimeIndex(pd.to_datetime(tab["label_exit_utc"], utc=True))
    sd = tab["session_day"].astype(str).to_numpy()
    for block in np.array_split(days[len(days) // 2:], N_BLOCKS):
        test = np.flatnonzero(np.isin(sd, block))
        if len(test) == 0:
            continue
        t0 = dec[test].min()
        train = np.flatnonzero((ext < t0) & (dec < t0 - EMBARGO))
        if len(train) < MIN_TRAIN or len(set(y_all[train].tolist())) < 2:
            continue
        model = fit_logit(X_all.iloc[train], y_all[train])
        p = model.score(X_all.iloc[test])
        out.append(pd.DataFrame({"candidate_id": tab["candidate_id"].to_numpy()[test], "session_day": sd[test],
                                 "decision_utc": dec[test], "r": tab["label_sim_r"].to_numpy(dtype=float)[test],
                                 "kept": p >= model.tau, "p": p}))
    if not out:
        return pd.DataFrame(columns=["candidate_id", "session_day", "decision_utc", "r", "kept", "p"])
    return pd.concat(out, ignore_index=True)


def _auc(y: np.ndarray, p: np.ndarray) -> float | None:
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score(y, p)) if len(set(y.tolist())) == 2 else None


def _by_day(r: np.ndarray, days: np.ndarray) -> dict[str, np.ndarray]:
    return {d: r[days == d] for d in sorted(set(days.tolist()))}


def oos_metrics(oos: pd.DataFrame, control: pd.DataFrame, *, seed: int = SEED) -> dict[str, Any]:
    """The arm's OOS read: counts, mean net R of all (no-model), kept and control candidates, both edges, AUC and the
    one-sided day-block bootstrap p of the edge over no-model (and of the edge over control, both samples resampled)."""
    n, kept = len(oos), int(oos["kept"].sum()) if len(oos) else 0
    res: dict[str, Any] = {"n_oos": n, "n_kept": kept, "n_days": int(oos["session_day"].nunique()) if n else 0,
                           "n_control": int(len(control))}
    if n == 0 or kept == 0:
        return res | {"mean_all": None, "mean_kept": None, "mean_control": None, "edge_vs_nomodel": None,
                      "edge_vs_control": None, "p_nomodel": None, "p_control": None, "auc": None}
    r, k = oos["r"].to_numpy(dtype=float), oos["kept"].to_numpy(dtype=bool)
    days = oos["session_day"].astype(str).to_numpy()
    mean_all, mean_kept = float(r.mean()), float(r[k].mean())
    cr = control["r"].to_numpy(dtype=float) if len(control) else np.zeros(0)
    mean_ctrl = float(cr.mean()) if len(cr) else None
    rng = np.random.default_rng(seed)
    uniq = np.array(sorted(set(days.tolist())))
    idx = {d: np.flatnonzero(days == d) for d in uniq}
    cdays = control["session_day"].astype(str).to_numpy() if len(control) else np.zeros(0, dtype=str)
    cu = np.array(sorted(set(cdays.tolist())))
    cidx = {d: np.flatnonzero(cdays == d) for d in cu}
    e_nm, e_ct = np.empty(BOOT), np.empty(BOOT)
    for b in range(BOOT):
        take = np.concatenate([idx[d] for d in rng.choice(uniq, len(uniq))])
        rk = r[take][k[take]]
        km = rk.mean() if len(rk) else np.nan
        e_nm[b] = km - r[take].mean()
        if len(cu):
            ct = np.concatenate([cidx[d] for d in rng.choice(cu, len(cu))])
            e_ct[b] = km - cr[ct].mean()
        else:
            e_ct[b] = np.nan
    ok_nm, ok_ct = e_nm[np.isfinite(e_nm)], e_ct[np.isfinite(e_ct)]
    return res | {"mean_all": mean_all, "mean_kept": mean_kept, "mean_control": mean_ctrl,
                  "edge_vs_nomodel": mean_kept - mean_all,
                  "edge_vs_control": None if mean_ctrl is None else mean_kept - mean_ctrl,
                  "p_nomodel": float((ok_nm <= 0).mean()) if len(ok_nm) else None,
                  "p_control": float((ok_ct <= 0).mean()) if len(ok_ct) else None,
                  "auc": _auc((r > 0).astype(int), oos["p"].to_numpy(dtype=float))}


def eligible(m: Mapping[str, Any]) -> str | None:
    """Why an arm's OOS read cannot be judged (minimum counts), or None."""
    for key, floor in (("n_oos", MIN_OOS), ("n_kept", MIN_KEPT), ("n_days", MIN_OOS_DAYS),
                       ("n_control", MIN_CONTROL)):
        if int(m.get(key) or 0) < floor:
            return f"{key} {int(m.get(key) or 0)} < {floor}"
    return None


def holm(p: Mapping[str, float]) -> dict[str, float]:
    """Holm step-down adjusted p values (monotone, capped at 1)."""
    order = sorted(p, key=lambda a: p[a])
    out, run = {}, 0.0
    for i, a in enumerate(order):
        run = max(run, min(1.0, (len(order) - i) * p[a]))
        out[a] = run
    return out


def gate(metrics: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per arm: {"pass": bool, "reason": str, "p_holm": float | None}. Holm runs over the arms meeting the minimum
    counts (the run's family of tests)."""
    judged = {a: m for a, m in metrics.items() if eligible(m) is None}
    adj = holm({a: float(m["p_nomodel"] if m["p_nomodel"] is not None else 1.0) for a, m in judged.items()})
    out: dict[str, dict[str, Any]] = {}
    for arm, m in metrics.items():
        why = eligible(m)
        if why is not None:
            out[arm] = {"pass": False, "reason": f"insufficient: {why}", "p_holm": None}
            continue
        fails = []
        if float(m["edge_vs_nomodel"]) < MIN_EDGE_R:
            fails.append(f"edge vs no-model {m['edge_vs_nomodel']:+.3f} R < {MIN_EDGE_R}")
        if m["edge_vs_control"] is None or float(m["edge_vs_control"]) < MIN_EDGE_R:
            fails.append(f"edge vs control {m['edge_vs_control']} R < {MIN_EDGE_R}")
        if adj[arm] > ALPHA:
            fails.append(f"Holm p {adj[arm]:.3f} > {ALPHA}")
        out[arm] = {"pass": not fails, "reason": "; ".join(fails) or "passed", "p_holm": adj[arm]}
    return out


def control_rows(rows: pd.DataFrame, arm_id: str, *, named: Mapping[str, str], since: pd.Timestamp) -> pd.DataFrame:
    """The arm's random baseline over its OOS window: its named control's candidates when it has one with rows, else
    every control candidate of its style (same broker: `rows` is one broker's)."""
    ctrl = rows[rows["is_control"].astype(bool) & (pd.to_datetime(rows["decision_utc"], utc=True) >= since)]
    mine = ctrl[ctrl["arm_id"] == named[arm_id]] if arm_id in named else ctrl.iloc[0:0]
    if mine.empty:
        style = rows.loc[rows["arm_id"] == arm_id, "style"]
        mine = ctrl[ctrl["style"] == (style.iloc[0] if len(style) else None)]
    return pd.DataFrame({"session_day": mine["session_day"].astype(str).to_numpy(),
                         "r": mine["label_sim_r"].to_numpy(dtype=float)})


# ---------------------------------------------------------------------------------------------- the store
def active_models(db: Database, broker: str) -> dict[str, dict[str, Any]]:
    """The broker's ACTIVE model per arm: version, activation and the model."""
    with db.connection() as conn:
        got = conn.execute("SELECT arm_id, model_version, activation_ns, model FROM self_retrain_models "
                           "WHERE broker = ? AND status = 'active'", (broker,)).fetchall()
    return {str(a): {"model_version": v, "activation_ns": int(t), "model": LogitModel.model_validate_json(m)}
            for a, v, t, m in got}


def forward_read(rows: pd.DataFrame, arm_id: str, active: Mapping[str, Any]) -> dict[str, Any]:
    """The frozen active model on the arm's candidates decided at or after its activation."""
    act = pd.Timestamp(int(active["activation_ns"]), unit="ns", tz="UTC")
    fw = rows[(rows["arm_id"] == arm_id) & (pd.to_datetime(rows["decision_utc"], utc=True) >= act)]
    if fw.empty:
        return {"n": 0, "edge_vs_nomodel": None}
    k = active["model"].keep(design(fw))
    r = fw["label_sim_r"].to_numpy(dtype=float)
    return {"n": int(len(fw)), "n_kept": int(k.sum()),
            "edge_vs_nomodel": float(r[k].mean() - r.mean()) if k.any() else None}


def _set_status(conn: Any, version: str, status: Status, reason: str) -> None:
    conn.execute("UPDATE self_retrain_models SET status = ?, status_reason = ?, status_ns = ? WHERE model_version = ?",
                 (status, reason, now_ns(), version))


def run_broker(db: Database, *, broker: str, slot: pd.Timestamp, cutoff: pd.Timestamp, activation: pd.Timestamp,
               holdout: tuple[pd.Timestamp, pd.Timestamp] | None, serve: bool, named_controls: Mapping[str, str],
               held_out: Collection[str] = (), instruments: Mapping[str, str] | None = None) -> dict[str, Any]:
    """One broker's weekly run (module docstring), recorded in self_retrain_runs and self_retrain_models."""
    run_id = f"{SPEC_VERSION}-{broker}-{cutoff:%Y%m%dT%H%M}"
    rows, counts = load_rows(db, broker=broker, cutoff=cutoff, holdout=holdout, held_out=held_out,
                             instruments=instruments)
    strat = rows[~rows["is_control"].astype(bool)] if len(rows) else rows
    metrics: dict[str, dict[str, Any]] = {}
    oos_of: dict[str, pd.DataFrame] = {}
    for arm_id, arm_rows in (strat.groupby("arm_id") if len(strat) else []):
        oos = walk_forward(arm_rows)
        oos_of[str(arm_id)] = oos
        since = pd.Timestamp(oos["decision_utc"].min()) if len(oos) else cutoff
        metrics[str(arm_id)] = oos_metrics(oos, control_rows(rows, str(arm_id), named=named_controls, since=since))
        metrics[str(arm_id)]["n_rows"] = int(len(arm_rows))
    verdicts = gate(metrics)
    active = active_models(db, broker)
    decisions: dict[str, dict[str, Any]] = {}
    n_weights = 0
    with db.write() as conn:
        for arm_id, arm_rows in (strat.groupby("arm_id") if len(strat) else []):
            arm_id = str(arm_id)
            v = verdicts[arm_id]
            y = (arm_rows["label_sim_r"].to_numpy(dtype=float) > 0).astype(int)
            model = fit_logit(design(arm_rows), y) if len(arm_rows) >= MIN_TRAIN and len(set(y.tolist())) == 2 \
                else None
            status: Status = "active" if (v["pass"] and serve and model is not None) else "shadow"
            reason = v["reason"] if serve or not v["pass"] else "passed, serving switched off"
            prev = active.get(arm_id)
            fwd = forward_read(rows, arm_id, prev) if prev is not None else None
            if prev is not None:
                if status == "active":
                    _set_status(conn, prev["model_version"], "superseded", f"superseded by run {run_id}")
                elif fwd is not None and fwd["n"] >= MIN_FORWARD and (fwd["edge_vs_nomodel"] is None
                                                                       or fwd["edge_vs_nomodel"] < DEMOTE_EDGE_R):
                    _set_status(conn, prev["model_version"], "demoted",
                                f"forward edge {fwd['edge_vs_nomodel']} R < {DEMOTE_EDGE_R} on {fwd['n']}")
                else:
                    _set_status(conn, prev["model_version"], "demoted", f"run {run_id} walk-forward: {v['reason']}")
            if model is not None:
                n_weights += model.n_weights
                conn.execute(
                    "INSERT OR REPLACE INTO self_retrain_models (model_version, run_id, broker, arm_id, spec_version, "
                    "status, status_reason, cutoff_ns, activation_ns, n_train, n_features, n_weights, model, oos, "
                    "recorded_ns, status_ns) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (f"{run_id}-{arm_id}", run_id, broker, arm_id, SPEC_VERSION, status, reason,
                     int(cutoff.value), int(activation.value), int(len(arm_rows)), len(INPUTS), model.n_weights,
                     model.model_dump_json(), json.dumps(metrics[arm_id] | v, default=float), now_ns(), now_ns()))
            decision = status if prev is None or status == "active" else "demoted"
            decisions[arm_id] = {"decision": decision, "reason": reason,
                                 "previous": prev["model_version"] if prev else None, "forward": fwd,
                                 **{k: metrics[arm_id].get(k) for k in ("n_rows", "n_oos", "n_kept", "auc",
                                                                         "edge_vs_nomodel", "edge_vs_control")},
                                 "p_holm": v["p_holm"]}
        n_active = sum(1 for d in decisions.values() if d["decision"] == "active")
        n_demoted = sum(1 for d in decisions.values() if d["decision"] == "demoted")
        summary = {"run_id": run_id, "broker": broker, "labels": counts["labels"],
                   "with_features": counts["with_features"], "no_snapshot": counts["no_snapshot"],
                   "controls": counts["controls"], "features": len(INPUTS), "weights": n_weights,
                   "arms_trained": sum(1 for d in decisions.values() if d.get("n_rows")),
                   "active": n_active, "demoted": n_demoted, "shadow": len(decisions) - n_active - n_demoted}
        conn.execute(
            "INSERT OR REPLACE INTO self_retrain_runs (run_id, broker, spec_version, slot_ns, cutoff_ns, run_ns, status, "
            "n_labels, n_with_features, n_features, n_weights, n_arms, n_active, n_shadow, n_demoted, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, 'ok', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, broker, SPEC_VERSION, int(slot.value), int(cutoff.value), now_ns(), counts["labels"],
             counts["with_features"], len(INPUTS), n_weights, len(decisions), n_active, summary["shadow"], n_demoted,
             json.dumps({"counts": counts, "decisions": decisions}, default=float)))
    return summary


def record_failure(db: Database, *, broker: str, slot: pd.Timestamp, cutoff: pd.Timestamp, status: str,
                   error: str) -> None:
    """A run that could not train (error, or switched off): a row all the same, so health sees it."""
    with db.write() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO self_retrain_runs (run_id, broker, spec_version, slot_ns, cutoff_ns, run_ns, status, "
            "n_labels, n_with_features, n_features, n_weights, n_arms, n_active, n_shadow, n_demoted, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?, 0, 0, 0, 0, 0, ?)",
            (f"{SPEC_VERSION}-{broker}-{cutoff:%Y%m%dT%H%M}", broker, SPEC_VERSION, int(slot.value), int(cutoff.value),
             now_ns(), status, len(INPUTS), json.dumps({"error": error[:500]})))


def demote_all(db: Database, reason: str) -> int:
    """Every ACTIVE model demoted (the job switched off, or serving off): nothing stays active unwatched."""
    with db.write() as conn:
        return int(conn.execute("UPDATE self_retrain_models SET status = 'demoted', status_reason = ?, status_ns = ? "
                                "WHERE status = 'active'", (reason, now_ns())).rowcount)


def latest_runs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """The newest run's rows (one per broker); [] before any run or before the table exists (health reads it on a
    read-only connection)."""
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'self_retrain_runs'").fetchone() \
            is None:
        return []
    r = conn.execute("SELECT MAX(slot_ns) FROM self_retrain_runs").fetchone()
    if r is None or r[0] is None:
        return []
    cols = ["run_id", "broker", "status", "slot_ns", "run_ns", "n_labels", "n_with_features", "n_features",
            "n_weights", "n_arms", "n_active", "n_shadow", "n_demoted", "detail"]
    got = conn.execute(f"SELECT {', '.join(cols)} FROM self_retrain_runs WHERE slot_ns = ?", (r[0],)).fetchall()
    return [dict(zip(cols, g, strict=True)) for g in got]


def health(runs: Sequence[Mapping[str, Any]], now: pd.Timestamp, *, enabled: bool) -> tuple[str, str]:
    """(status, reason) for the `self_retrain` health check: WARN when switched off, when no run in RUN_MAX_AGE, when
    the newest run failed or trained on 0 labels (learning must never be silently off). Counts, never schedules."""
    if not enabled:
        return "warn", "self-retrain is switched off (research.self_retrain.enabled): nothing learns from the labels"
    if not runs:
        return "warn", "no self-retrain run recorded yet (weekly, Sunday; job self_retrain)"
    last = pd.Timestamp(max(int(r["run_ns"]) for r in runs), unit="ns", tz="UTC")
    labels = sum(int(r["n_labels"] or 0) for r in runs)
    feats = sum(int(r["n_with_features"] or 0) for r in runs)
    weights = sum(int(r["n_weights"] or 0) for r in runs)
    active = sum(int(r["n_active"] or 0) for r in runs)
    arms = sum(int(r["n_arms"] or 0) for r in runs)
    bad = [f"{r['broker']}:{r['status']}" for r in runs if r["status"] != "ok"]
    msg = (f"last run {last:%Y-%m-%d %H:%M} UTC: {labels} labels, {feats} with features, {arms} arms, {weights} "
           f"weights, {active} active")
    if now - last > RUN_MAX_AGE:
        return "warn", f"no self-retrain run for {(now - last).days} d (> {RUN_MAX_AGE.days} d); {msg}"
    if bad:
        return "warn", f"self-retrain run failed ({', '.join(bad)}); {msg}"
    if labels == 0 or feats == 0:
        return "warn", f"self-retrain trained on 0 labels with features; {msg}"
    return "ok", msg
