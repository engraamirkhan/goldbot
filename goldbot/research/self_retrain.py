"""Weekly self-retraining of a per-arm meta-label FILTER on the exploration replay labels (owner 2026-11-24;
pre-registration docs/research/prereg-2026-11-24-self-retrain.md, spec `SPEC_VERSION` sr-v2). aux.db migration 43.

Replaces the two retrains retired on 2026-11-13 (docs/research/ml-review-2026-11-13.md: the specialists' Saturday
LightGBM, shadow AUC 0.44 on 145 trades, and the H-62 meta-labeller, 0 model rows because no candidate carried a
feature snapshot). Since then every gold candidate gets its feature vector at its decision
(research/feature_snapshots.py, `exploration_feature_snapshots`).

One weekly run (the job `self_retrain` in goldbot/ops/jobs.py drives it):
1. `compute_broker` per broker (evidence never pooled across brokers, ADR 0043 amendment). Rows: every gold exploration
   candidate, taken or not, whose replay **r2** label (`exploration_replay`, measured legs, never a credit; r1 is read
   alongside as `label_r1`) resolved by the data cutoff (the last Friday 21:00 UTC) AND whose full horizon
   (decision + MAX_SEND_DELAY + max_hold_s) ended by then (a quick stop is never kept while its open twins are not:
   no truncation bias); not quarantined, not void as posterior evidence; with a snapshot available at its decision:
   `pit` written, or `pit` nominal decided on/after NOMINAL_FROM (card section 2: every minute of the last day before
   such a decision carries its write time and the daily reconcile had settled older minutes). Holdout rows fail the
   run. Arms retired in the spec and replay_only strategy rows are excluded (card section 2). Control arms are the
   random baseline, never training rows.
2. Per strategy arm, one fixed model (no grid): median-imputed, standardised L2 logistic regression (C = `C_REG`) on
   the frozen `FEATURES` (39 market columns, the directional ones signed by side) + `side` = 40 inputs,
   y = 1[r2 net R > 0]. Expanding walk-forward over session days (last half in `N_BLOCKS` blocks); a training row's
   whole horizon, its label exit, and its decision + EMBARGO all lie before the block's first decision. Keep when
   p >= tau, tau = the `DROP_SHARE` quantile of the training fold's own scores.
3. `decide` once per RUN over every (broker, arm): judged pairs (eligible pairs of the PRENAMED retired arms, at most
   MAX_JUDGED, ranked by candidate count, label-blind; a pre-named arm's OOS rows are only those decided after its
   retired_day, its earlier rows train only) are Holm-adjusted together on p = max(p vs no-model, p vs control) (intersection-union).
   A pass needs both edges >= MIN_EDGE_R, the gross edge (label_mid_r) >= 0 and Holm p <= ALPHA. ACTIVE needs a pass
   in TWO consecutive runs, and the first passer's frozen forward edge >= 0 once it has MIN_CONFIRM forward rows.
4. Demotion is read first: a previous ACTIVE model whose forward edge is missing or below DEMOTE_EDGE_R on at least
   MIN_FORWARD rows is demoted and this run's model of the arm is forced to shadow. An ACTIVE model is otherwise
   superseded by a new active one or demoted (gate failed, or no rows this run).
5. `apply` writes `self_retrain_models` (coefficients as JSON, never a pickle) and `self_retrain_runs`; the job writes
   the K rows BEFORE it (one per judged pair); if they cannot be written nothing becomes active (fail closed).

Scope and safety: reads aux.db, writes only its own two tables. The judged arms are retired, so ACTIVE means a
RE-ACTIVATION CANDIDATE: nothing trades; the run records it (`reactivation_candidate`), health and the job output name
it, and re-activation as a demo learning arm goes through the existing learning-class route with trading-safety review
(out of scope here). Nothing in goldbot/engine reads these tables.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
import yaml

from goldbot.base import FrozenRecord, UtcTimestamp, load_record_json
from goldbot.data.timeutil import epoch_ns
from goldbot.db.connection import Database, now_ns
from goldbot.research.exploration_data import GOLD, REPLAY_V1, REPLAY_V2, VOID_INSIDE_SPREAD, read_obs
from goldbot.research.model import check_feature_cap

log = logging.getLogger("goldbot.self_retrain")

# ---------------------------------------------------------------------------------------------- the frozen spec
SPEC_VERSION = "sr-v2"
PREREG = "docs/research/prereg-2026-11-24-self-retrain.md"
REGISTRY_FAMILY = "self_retrain_meta_filter"
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
SIGNED: dict[str, float] = {"ret_4": 0.0, "ret_16": 0.0, "ret_96": 0.0, "tsmom_score": 0.0, "slope_ema50": 0.0,
                            "dist_ema50_atr": 0.0, "bb_z_20": 0.0, "rsi14": 50.0, "donchian_pos_20": 0.5,
                            "dist_vwap48_atr": 0.0, "d1_slope_ema20": 0.0}
INPUTS: tuple[str, ...] = (*FEATURES, "side")
check_feature_cap(list(INPUTS))
C_REG = 0.1
DROP_SHARE = 1.0 / 3.0
N_BLOCKS = 4
EMBARGO = pd.Timedelta(days=1)
MIN_TRAIN = 100
# a candidate's horizon: decision + the longest send delay (arms.send_delay_s is at most 7 s; 60 s is the bound) + hold
MAX_SEND_DELAY = pd.Timedelta(seconds=60)
# nominal-pit snapshots are admitted from this decision time (minute write stamps began 2026-11-13; one day later the
# whole last day before a decision is stamped, and older minutes were settled by the daily broker-M1 reconcile)
NOMINAL_FROM = pd.Timestamp("2026-11-15", tz="UTC")
# the promotion gate (pre-registered; never tuned on the data)
MIN_OOS = 200
MIN_KEPT = 100
MIN_OOS_DAYS = 10
MIN_CONTROL = 50
MIN_EDGE_R = 0.05
ALPHA = 0.10
BOOT = 10_000
SEED = 20261124
MAX_JUDGED = 10                 # judged (broker, arm) pairs a run at most (K = pairs judged)
# PD + research-director 2026-11-24 (no live strategy arm is left after #424): the ONLY judged arms, each "retired arm
# + learned filter" a new hypothesis (card section 2). The 10 of PR #435's replay_only gold arms with the most
# candidates (G1A dropped: the fewest); RBR1H / RBR4H are excluded (STOPped by #432). Judged on any broker, at most
# MAX_JUDGED (broker, arm) pairs a run by candidate count
PRENAMED: tuple[str, ...] = ("RBR15", "Z3D", "Z1P", "Z9D", "Z10D", "S1A", "S1B", "F1A", "Z2P", "Z5P")
# a pre-named arm recorded under a new id after its retirement (PR #435): RB15's row is hard-stopped by its end_utc
# 2026-11-22 and never records again; its replay_only successor RBR15 (twin RBRC15) does. The old arm's rows are the new
# arm's HISTORY: training rows only, never out of sample (every OOS row is decided after the last history row)
HISTORY_OF: dict[str, str] = {"RB15": "RBR15"}
STOP_MATCH = 0.10               # a matched-stop control's median stop within 10 % of the arm's (same timeframe)
MIN_CONFIRM = 30                # the first passer's forward rows from which its forward edge must be >= 0
# demotion reads the FROZEN active model's forward record (rows after its activation), not a refit: below 0 R on
# MIN_FORWARD rows it goes. Lower than the OOS promotion edge on purpose (a forward record is shorter and noisier)
DEMOTE_EDGE_R = 0.0
MIN_FORWARD = 60
RUN_MAX_AGE = pd.Timedelta(days=8)

Status = Literal["active", "shadow", "demoted", "superseded"]

# the nine decision timeframes every run reports a cell for (label unlock 2026-11-25), whether or not a row exists
CELL_TFS: tuple[str, ...] = ("1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w")
# what a non-judged arm's read may carry under quant Q2 (counts, never an outcome)
COUNT_KEYS: tuple[str, ...] = ("n_oos", "n_kept", "n_days", "n_control", "n_rows", "dup_bar_share")
# the funnel of one (broker, class, timeframe) cell: counts only, never a label value
FUNNEL: tuple[str, ...] = ("labels", "capped_day", "open_horizon", "no_snapshot", "nominal_not_audited_clean",
                           "with_features", "nominal", "controls")
CELL_KEYS: tuple[str, ...] = ("spec", "broker", "class", "tf", "candidates", *FUNNEL, "usable")


class Spec(FrozenRecord):
    """The parts of the frozen spec that differ between versions. sr-v2 is the signed card; sr-v3 is
    docs/research/prereg-amendment-2026-11-25-sr-v3-snapshot-admission.md (not judged before its card is signed)."""
    version: str
    nominal_from: UtcTimestamp
    # brokers whose nominal snapshots decided before `nominal_from` are admitted (the minute-revision audit passed
    # for them), each still only when its last day was all live minutes (snapshot_audit.live_feed_audit)
    nominal_early_brokers: tuple[str, ...] = ()
    oos_judged_only: bool = False        # quant Q2: OOS outcomes are computed only for the run's judged pairs
    horizon_embargo: bool = False        # embargo = max(EMBARGO, the arm's longest horizon)
    # quant H2 of #451: a nominal row that read a reference (rank-3) minute older than 24 h in the stamped era is out
    exclude_rank3_beyond_24h: bool = False


SR_V2 = Spec(version=SPEC_VERSION, nominal_from=NOMINAL_FROM)
# the audit result fills nominal_early_brokers (prereg amendment section 6); () = sr-v2's admission for every broker
SR_V3 = Spec(version="sr-v3", nominal_from=NOMINAL_FROM, nominal_early_brokers=(), oos_judged_only=True,
             horizon_embargo=True, exclude_rank3_beyond_24h=True)


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
    """The fixed model on one training set; tau = the DROP_SHARE quantile of its own training scores."""
    from sklearn.linear_model import LogisticRegression
    m = X.reindex(columns=list(INPUTS)).to_numpy(dtype=float)
    finite = np.where(np.isfinite(m), m, np.nan)
    med = np.array([np.nanmedian(c) if np.isfinite(c).any() else 0.0 for c in finite.T])
    m = np.where(np.isfinite(m), m, med)
    mu, sd = m.mean(axis=0), m.std(axis=0)
    sd = np.where(sd > 1e-12, sd, 1.0)
    clf = LogisticRegression(C=C_REG, max_iter=2000)
    clf.fit((m - mu) / sd, y)
    model = LogitModel(inputs=INPUTS, medians=tuple(map(float, med)), means=tuple(map(float, mu)),
                       scales=tuple(map(float, sd)), coef=tuple(map(float, clf.coef_[0])),
                       intercept=float(clf.intercept_[0]), tau=0.0)
    return model.model_copy(update={"tau": float(np.quantile(model.score(X), DROP_SHARE))})


# ---------------------------------------------------------------------------------------------- arm rules
def arm_rules(paths: Sequence[Path] | None = None) -> dict[str, dict[str, Any]]:
    """Each spec row's run policy: retired_day, control_mode and role (ARM-SPEC, then discovered arms; an ARM-SPEC
    row wins an id both name, as in the engine)."""
    from goldbot.research import arms as arm_spec
    if paths is None:
        paths = [arm_spec.REPO_ROOT / "config" / "spec_arms.yaml", arm_spec.REPO_ROOT / "config" / "exploration_arms.yaml"]
    out: dict[str, dict[str, Any]] = {}
    for p in paths:
        if not Path(p).exists():
            continue
        for r in (yaml.safe_load(Path(p).read_text()) or {}).get("arms", []):
            if isinstance(r, dict) and "id" in r:
                out[str(r["id"])] = {"retired_day": r.get("retired_day"), "control_mode": r.get("control_mode",
                                     "broker"), "role": r.get("role", "strategy")}
    return out


def strategy_excluded(arm_id: str, rules: Mapping[str, Mapping[str, Any]]) -> bool:
    """A strategy arm retired in the spec, or a replay_only row that is not a control: never trained, never judged."""
    r = rules.get(arm_id)
    if r is None:
        return False
    return r.get("retired_day") is not None or (r.get("control_mode") == "replay_only" and r.get("role") != "control")


def baseline_rows(rows: pd.DataFrame, rules: Mapping[str, Mapping[str, Any]], named: Collection[str]) -> pd.Series:
    """Control rows usable as the style-fallback baseline: not decided on/after its arm's retired_day, not a
    replay_only non-control row; a named control always stays."""
    def ok(arm: str, day: str) -> bool:
        if arm in named:
            return True
        r = rules.get(arm) or {}
        rd = r.get("retired_day")
        if rd is not None and day >= str(rd):
            return False
        return not (r.get("control_mode") == "replay_only" and r.get("role") != "control")
    days = pd.to_datetime(rows["decision_utc"], utc=True).dt.strftime("%Y-%m-%d")
    return pd.Series([ok(str(a), d) for a, d in zip(rows["arm_id"], days, strict=True)], index=rows.index, dtype=bool)


# ---------------------------------------------------------------------------------------------- rows
def design(rows: pd.DataFrame) -> pd.DataFrame:
    """The model's 40 inputs from the rows' feature columns and side: the SIGNED columns centred and signed."""
    X = rows.reindex(columns=list(FEATURES)).astype(float).replace([np.inf, -np.inf], np.nan)
    side = rows["side"].astype(float).to_numpy()
    for c, centre in SIGNED.items():
        X[c] = (X[c] - centre) * side
    X["side"] = side
    return X.reset_index(drop=True)


def horizon_end(rows: pd.DataFrame) -> pd.DatetimeIndex:
    """decision + MAX_SEND_DELAY + max_hold_s: when the candidate's label is resolved whatever its path."""
    dec = pd.DatetimeIndex(pd.to_datetime(rows["decision_utc"], utc=True))
    return dec + MAX_SEND_DELAY + pd.to_timedelta(rows["max_hold_s"].fillna(0).astype(float).to_numpy(), unit="s")


def _tf_counts(tf: pd.Series) -> dict[str, int]:
    return {str(k): int(v) for k, v in tf.value_counts().items()}


def _snapshots(db: Database, nominal_from: pd.Timestamp | None = NOMINAL_FROM) -> pd.DataFrame:
    """Every ok snapshot that may be admitted: `pit` written, or nominal decided on/after `nominal_from` (None: any)."""
    since = np.iinfo(np.int64).min if nominal_from is None else int(nominal_from.value)
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT s.candidate_id, s.decision_ns, s.available_utc_max_ns, s.pit, v.features "
            "FROM exploration_feature_snapshots s JOIN exploration_feature_vectors v ON v.sha256 = s.vector_sha256 "
            "WHERE s.status = 'ok' AND (s.pit = 'written' OR (s.pit = 'nominal' AND s.decision_ns >= ?))",
            (since,)).fetchall()
    if not rows:
        return pd.DataFrame(columns=["candidate_id", "_snap_decision_ns", "_avail_ns", "_pit", *FEATURES])
    vals = pd.DataFrame([json.loads(f) for *_, f in rows]).reindex(columns=list(FEATURES)).astype(float)
    head = pd.DataFrame([r[:4] for r in rows], columns=["candidate_id", "_snap_decision_ns", "_avail_ns", "_pit"])
    return pd.concat([head, vals], axis=1)


def load_rows(db: Database, *, broker: str, cutoff: pd.Timestamp, holdout: tuple[pd.Timestamp, pd.Timestamp] | None,
              held_out: Collection[str] = (), instruments: Mapping[str, str] | None = None,
              nominal_ok: Collection[str] = (), capped: Collection[tuple[str, str]] = (), spec: Spec = SR_V2,
              rank3_beyond_24h: Collection[str] = ()) -> tuple[pd.DataFrame, dict[str, Any]]:
    """(rows, counts): the broker's gold candidates with an r2 label and a full horizon by `cutoff` and an admitted
    snapshot (module docstring step 1). A nominal snapshot is admitted only when the audit found it clean
    (`nominal_ok`, research/snapshot_audit.py; quant S5). Every row of an (account, UTC day) the engine journalled
    `exploration_replay_cap` is dropped: that day's replay record is truncated (`capped`; quant S1). A holdout row or a
    snapshot after its decision fails (ValueError). `spec` sr-v3 also admits a passed broker's nominal snapshots
    decided before its `nominal_from` (when in `nominal_ok`). counts["by_tf"]: the same funnel per timeframe."""
    obs = read_obs(db, broker=broker, as_of=cutoff, evidence="posterior", held_out=held_out, instrument=GOLD,
                   instruments=instruments, replay_version=REPLAY_V2)
    obs = obs[obs["label_sim_r"].notna()].reset_index(drop=True)
    dec = pd.DatetimeIndex(obs["decision_utc"])
    if holdout is not None and len(obs) and ((dec >= holdout[0]) & (dec < holdout[1])).any():
        raise ValueError("self-retrain: a candidate decided inside the sealed holdout window: refused")
    n_labels = len(obs)
    tf_all = _tf_counts(obs["tf"].astype(str)) if len(obs) else {}
    cap = {(str(a), str(d)) for a, d in capped}
    if cap and len(obs):
        days = pd.to_datetime(obs["decision_utc"], utc=True).dt.strftime("%Y-%m-%d")
        hit = np.array([(str(a), d) in cap for a, d in zip(obs["account_id"], days, strict=True)], dtype=bool)
    else:
        hit = np.zeros(len(obs), dtype=bool)
    tf_cap = _tf_counts(obs.loc[hit, "tf"].astype(str)) if hit.any() else {}
    obs = obs[~hit].reset_index(drop=True)
    open_h = horizon_end(obs) > cutoff if len(obs) else np.zeros(0, dtype=bool)
    tf_open = _tf_counts(obs.loc[np.asarray(open_h), "tf"].astype(str)) if len(obs) else {}
    obs = obs[~np.asarray(open_h)].reset_index(drop=True)
    r1 = read_obs(db, broker=broker, as_of=cutoff, evidence="posterior", held_out=held_out, instrument=GOLD,
                  instruments=instruments, replay_version=REPLAY_V1)[["candidate_id", "label_sim_r"]]
    obs = obs.merge(r1.rename(columns={"label_sim_r": "label_r1"}), on="candidate_id", how="left")
    early = broker in spec.nominal_early_brokers
    rows = obs.merge(_snapshots(db, None if early else spec.nominal_from), on="candidate_id", how="inner")
    n_snap = len(rows)
    tf_snap = _tf_counts(rows["tf"].astype(str)) if len(rows) else {}
    ok = set(nominal_ok)
    rows = rows[(rows["_pit"] == "written") | rows["candidate_id"].isin(ok)].reset_index(drop=True)
    r3 = rows["candidate_id"].isin(set(rank3_beyond_24h)) & (rows["_pit"] == "nominal") if len(rows) else \
        pd.Series(dtype=bool)
    n_rank3 = int(r3.sum()) if len(rows) else 0
    if spec.exclude_rank3_beyond_24h and n_rank3:
        rows = rows[~r3.to_numpy()].reset_index(drop=True)
    if len(rows) and (rows["_avail_ns"].notna() & (rows["_avail_ns"] > rows["_snap_decision_ns"])).any():
        raise ValueError("self-retrain: a snapshot available after its decision (look-ahead): refused")
    with db.connection() as conn:      # prereg amendment (quant review of #442): never labelled, counted here
        inside = int(conn.execute(
            "SELECT COUNT(*) FROM exploration_label_void v JOIN exploration_obs o ON o.candidate_id = v.candidate_id "
            "WHERE v.replay_version = ? AND v.reason = ? AND o.broker = ? AND o.decision_ns <= ?",
            (REPLAY_V2, VOID_INSIDE_SPREAD, broker, int(epoch_ns(pd.DatetimeIndex([cutoff]))[0]))).fetchone()[0])
    counts: dict[str, Any] = {"labels": int(n_labels), "stop_inside_spread": inside, "capped_day": int(hit.sum()),
              "open_horizon": int(np.asarray(open_h).sum()),
              "with_features": int(len(rows)), "no_snapshot": int(len(obs) - n_snap),
              "nominal_not_audited_clean": int(n_snap - len(rows)),
              "nominal": int((rows["_pit"] == "nominal").sum()) if len(rows) else 0,
              "controls": int(rows["is_control"].sum()) if len(rows) else 0,
              "nominal_rank3_beyond_24h": n_rank3}
    tf_rows = rows["tf"].astype(str) if len(rows) else pd.Series(dtype=str)
    by_tf: dict[str, dict[str, int]] = {}
    for tf in sorted(set(tf_all) | set(CELL_TFS)):
        n_l, n_c, n_o = int(tf_all.get(tf, 0)), int(tf_cap.get(tf, 0)), int(tf_open.get(tf, 0))
        n_s = int(tf_snap.get(tf, 0))
        mine = rows[(tf_rows == tf).to_numpy()] if len(rows) else rows
        by_tf[tf] = {"labels": n_l, "capped_day": n_c, "open_horizon": n_o, "no_snapshot": n_l - n_c - n_o - n_s,
                     "nominal_not_audited_clean": n_s - len(mine), "with_features": len(mine),
                     "nominal": int((mine["_pit"] == "nominal").sum()) if len(mine) else 0,
                     "controls": int(mine["is_control"].sum()) if len(mine) else 0}
    counts["by_tf"] = by_tf
    return rows.sort_values(["decision_utc", "candidate_id"]).reset_index(drop=True), counts


CAP_ACTION = "exploration_replay_cap"        # engine/exploration.py `_replay_room` (PR #435), once a UTC day per book
CAP_FROM = pd.Timestamp("2026-11-24", tz="UTC")   # the cap exists from PR #435 (2026-11-24): no earlier day can carry it


def capped_days(store: Any, accounts: Collection[str], *, start: pd.Timestamp, end: pd.Timestamp
                ) -> set[tuple[str, str]]:
    """(account, UTC day) pairs whose engine journalled CAP_ACTION in [start, end). Raises when an account's journal
    cannot be read (fail closed: a truncated day must never be read as complete)."""
    out: set[tuple[str, str]] = set()
    for account in sorted(set(accounts)):
        d = store.read("decisions", source=account, symbol=None, start=start, end=end)
        if d is None:
            raise ValueError(f"self-retrain: {account} decisions journal unreadable")
        if d.empty or "action" not in d.columns:
            continue
        for _, r in d[d["action"].astype(str) == CAP_ACTION].iterrows():
            try:
                day = json.loads(str(r.get("detail") or "{}")).get("day")
            except ValueError:
                day = None
            out.add((account, str(day) if day else pd.Timestamp(r["ts_utc"]).tz_convert("UTC").strftime("%Y-%m-%d")))
    return out


# ---------------------------------------------------------------------------------------------- walk-forward
_OOS_COLS = ["candidate_id", "block", "session_day", "decision_utc", "r", "r_mid", "r1", "kept", "p"]


def embargo_of(arm_rows: pd.DataFrame, spec: Spec) -> pd.Timedelta:
    """The training embargo of one arm: EMBARGO (sr-v2), or the larger of it and the arm's longest horizon (decision
    + MAX_SEND_DELAY + max_hold_s; sr-v3 `horizon_embargo`): a multi-day hold's neighbours never train its test."""
    if not spec.horizon_embargo or arm_rows.empty:
        return EMBARGO
    longest = pd.Timedelta(seconds=float(arm_rows["max_hold_s"].fillna(0).astype(float).max())) + MAX_SEND_DELAY
    return max(EMBARGO, longest)


def walk_forward(arm_rows: pd.DataFrame, *, oos_from: pd.Timestamp | None = None,
                 embargo: pd.Timedelta = EMBARGO) -> pd.DataFrame:
    """Out-of-sample scores of one arm's candidates (module docstring step 2). `oos_from` (a pre-named retired arm:
    the day after its retired_day): only rows decided from then are ever test rows, split over all their session days;
    earlier rows (already read before the retirement) are training rows only."""
    tab = arm_rows.sort_values(["decision_utc", "candidate_id"]).reset_index(drop=True)
    days = np.array(sorted(set(tab["session_day"].astype(str))))
    post = np.ones(len(tab), dtype=bool)
    if oos_from is not None:
        post = (pd.to_datetime(tab["decision_utc"], utc=True) >= oos_from).to_numpy()
        test_days = np.array(sorted(set(tab.loc[post, "session_day"].astype(str))))
        if len(test_days) < N_BLOCKS:
            return pd.DataFrame(columns=_OOS_COLS)
    elif len(days) < 2 * N_BLOCKS:
        return pd.DataFrame(columns=_OOS_COLS)
    else:
        test_days = days[len(days) // 2:]
    X_all, y_all = design(tab), (tab["label_sim_r"].to_numpy(dtype=float) > 0).astype(int)
    dec = pd.DatetimeIndex(pd.to_datetime(tab["decision_utc"], utc=True))
    ext = pd.DatetimeIndex(pd.to_datetime(tab["label_exit_utc"], utc=True))
    hor = horizon_end(tab)
    sd = tab["session_day"].astype(str).to_numpy()
    r1 = tab["label_r1"].to_numpy(dtype=float) if "label_r1" in tab else np.full(len(tab), np.nan)
    out: list[pd.DataFrame] = []
    for b, block in enumerate(np.array_split(test_days, N_BLOCKS)):
        test = np.flatnonzero(np.isin(sd, block) & post)
        if len(test) == 0:
            continue
        t0 = dec[test].min()
        train = np.flatnonzero((ext < t0) & (hor < t0) & (dec < t0 - embargo))
        if len(train) < MIN_TRAIN or len(set(y_all[train].tolist())) < 2:
            continue
        model = fit_logit(X_all.iloc[train], y_all[train])
        p = model.score(X_all.iloc[test])
        out.append(pd.DataFrame({"candidate_id": tab["candidate_id"].to_numpy()[test], "block": b,
                                 "session_day": sd[test], "decision_utc": dec[test],
                                 "r": tab["label_sim_r"].to_numpy(dtype=float)[test],
                                 "r_mid": tab["label_mid_r"].to_numpy(dtype=float)[test], "r1": r1[test],
                                 "kept": p >= model.tau, "p": p}))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=_OOS_COLS)


def _auc(y: np.ndarray, p: np.ndarray) -> float | None:
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score(y, p)) if len(set(y.tolist())) == 2 else None


def _p_value(edges: np.ndarray) -> float:
    """One-sided bootstrap p: (1 + #draws <= 0) / (1 + B); a draw with no kept row (NaN) counts against."""
    return float((1 + int((~(edges > 0)).sum())) / (1 + len(edges)))


def _day_sums(days: np.ndarray, *cols: np.ndarray) -> tuple[np.ndarray, list[np.ndarray]]:
    uniq, inv = np.unique(days, return_inverse=True)
    return uniq, [np.bincount(inv, weights=c, minlength=len(uniq)) for c in cols]


def _mean(a: np.ndarray, mask: np.ndarray) -> float | None:
    m = mask & np.isfinite(a)
    return float(a[m].mean()) if m.any() else None


def oos_metrics(oos: pd.DataFrame, control: pd.DataFrame, *, seed: int = SEED, boot: int = BOOT) -> dict[str, Any]:
    """The arm's OOS read (r2 net R unless named): counts, means, both edges, the gross (label_mid_r) and r1 edges, AUC,
    and the day-block bootstrap p of each edge (arm days and control days resampled independently)."""
    n, kept = len(oos), int(oos["kept"].sum()) if len(oos) else 0
    res: dict[str, Any] = {"n_oos": n, "n_kept": kept, "n_days": int(oos["session_day"].nunique()) if n else 0,
                           "n_control": int(len(control))}
    empty = {"mean_all": None, "mean_kept": None, "mean_control": None, "edge_vs_nomodel": None,
             "edge_vs_control": None, "edge_gross": None, "edge_r1": None, "p_nomodel": None, "p_control": None,
             "auc": None, "boot_no_kept": None}
    if n == 0 or kept == 0:
        return res | empty
    r, k = oos["r"].to_numpy(dtype=float), oos["kept"].to_numpy(dtype=bool)
    rm, r1 = oos["r_mid"].to_numpy(dtype=float), oos["r1"].to_numpy(dtype=float)
    every = np.ones(n, dtype=bool)
    mean_all, mean_kept = float(r.mean()), float(r[k].mean())
    cr = control["r"].to_numpy(dtype=float) if len(control) else np.zeros(0)
    mean_ctrl = float(cr.mean()) if len(cr) else None
    k1, a1 = _mean(r1, k), _mean(r1, every)
    gk, ga = _mean(rm, k), _mean(rm, every)
    rng = np.random.default_rng(seed)
    uniq, (s_all, n_all, s_k, n_k) = _day_sums(oos["session_day"].astype(str).to_numpy(), r, every.astype(float),
                                                r * k, k.astype(float))
    W = rng.multinomial(len(uniq), np.full(len(uniq), 1.0 / len(uniq)), size=boot).astype(float)
    nk = W @ n_k
    with np.errstate(invalid="ignore", divide="ignore"):
        km = (W @ s_k) / nk
        e_nm = km - (W @ s_all) / (W @ n_all)
        if len(cr):
            cu, (cs, cn) = _day_sums(control["session_day"].astype(str).to_numpy(), cr, np.ones(len(cr)))
            Wc = rng.multinomial(len(cu), np.full(len(cu), 1.0 / len(cu)), size=boot).astype(float)
            e_ct = km - (Wc @ cs) / (Wc @ cn)
        else:
            e_ct = np.full(boot, np.nan)
    return res | {"mean_all": mean_all, "mean_kept": mean_kept, "mean_control": mean_ctrl,
                  "edge_vs_nomodel": mean_kept - mean_all,
                  "edge_vs_control": None if mean_ctrl is None else mean_kept - mean_ctrl,
                  "edge_gross": None if gk is None or ga is None else gk - ga,
                  "edge_r1": None if k1 is None or a1 is None else k1 - a1,
                  "p_nomodel": _p_value(e_nm), "p_control": _p_value(e_ct) if len(cr) else None,
                  "auc": _auc((r > 0).astype(int), oos["p"].to_numpy(dtype=float)),
                  "boot_no_kept": int((nk == 0).sum())}


def dup_bar_share(arm_rows: pd.DataFrame) -> float | None:
    """Share of an arm's rows whose (tf, decision minute) the arm also decided on another account of the broker
    (icm-demo and icm-probe read one live feed: quant M2 of #451, reported, 0 K). None without rows."""
    if arm_rows.empty or "account_id" not in arm_rows:
        return None
    bar = pd.to_datetime(arm_rows["decision_utc"], utc=True).dt.floor("1min")
    key = arm_rows["tf"].astype(str) + "|" + bar.astype(str)
    accounts = arm_rows.groupby(key)["account_id"].transform("nunique")
    return float((accounts > 1).mean())


def count_metrics(oos: pd.DataFrame, control: pd.DataFrame) -> dict[str, Any]:
    """The label-blind part of an arm's OOS read (quant Q2): row, kept, day and control counts, no outcome."""
    n = len(oos)
    return {"n_oos": n, "n_kept": int(oos["kept"].sum()) if n else 0,
            "n_days": int(oos["session_day"].nunique()) if n else 0, "n_control": int(len(control))}


def eligible(m: Mapping[str, Any]) -> str | None:
    """Why an arm's OOS read cannot be judged (minimum counts), or None."""
    for key, floor in (("n_oos", MIN_OOS), ("n_kept", MIN_KEPT), ("n_days", MIN_OOS_DAYS),
                       ("n_control", MIN_CONTROL)):
        if int(m.get(key) or 0) < floor:
            return f"{key} {int(m.get(key) or 0)} < {floor}"
    return None


def holm(p: Mapping[Any, float]) -> dict[Any, float]:
    """Holm step-down adjusted p values (monotone, capped at 1)."""
    order = sorted(p, key=lambda a: (p[a], str(a)))
    out, run = {}, 0.0
    for i, a in enumerate(order):
        run = max(run, min(1.0, (len(order) - i) * p[a]))
        out[a] = run
    return out


def control_rows(rows: pd.DataFrame, arm_id: str, *, named: Mapping[str, str], since: pd.Timestamp,
                 rules: Mapping[str, Mapping[str, Any]] | None = None) -> pd.DataFrame:
    """The arm's random baseline over its OOS window: its named control's candidates when it has rows, else the
    MATCHED-STOP controls (same timeframe, median stop within STOP_MATCH of the arm's; quant Bias 3: never a
    small-stop style fallback) admitted by `baseline_rows`; none: no baseline (insufficient). Same broker: `rows` is
    one broker's."""
    ctrl = rows[rows["is_control"].astype(bool) & (pd.to_datetime(rows["decision_utc"], utc=True) >= since)]
    mine = ctrl[ctrl["arm_id"] == named[arm_id]] if arm_id in named else ctrl.iloc[0:0]
    if mine.empty and len(ctrl):
        arm = rows[rows["arm_id"] == arm_id]
        stop = float(arm["stop_dist"].median()) if len(arm) else float("nan")
        tf = arm["tf"].iloc[0] if len(arm) else None
        med = ctrl.groupby("arm_id")["stop_dist"].median()
        same_tf = set(ctrl.loc[ctrl["tf"] == tf, "arm_id"].astype(str))
        matched = [str(a) for a, m in med.items() if str(a) in same_tf and np.isfinite(stop) and stop > 0
                   and abs(float(m) - stop) <= STOP_MATCH * stop]
        mine = ctrl[ctrl["arm_id"].astype(str).isin(matched)]
        if len(mine):
            mine = mine[baseline_rows(mine, rules or {}, set(named.values())).to_numpy()]
    return pd.DataFrame({"session_day": mine["session_day"].astype(str).to_numpy(),
                         "r": mine["label_sim_r"].to_numpy(dtype=float)})


def forward_read(rows: pd.DataFrame, arm_id: str, model: LogitModel, activation_ns: int) -> dict[str, Any]:
    """A frozen model on the arm's candidates decided at or after `activation_ns` (horizon-complete rows only)."""
    act = pd.Timestamp(int(activation_ns), unit="ns", tz="UTC")
    fw = rows[(rows["arm_id"] == arm_id) & (pd.to_datetime(rows["decision_utc"], utc=True) >= act)]
    if fw.empty:
        return {"n": 0, "n_kept": 0, "edge_vs_nomodel": None}
    k = model.keep(design(fw))
    r = fw["label_sim_r"].to_numpy(dtype=float)
    return {"n": int(len(fw)), "n_kept": int(k.sum()),
            "edge_vs_nomodel": float(r[k].mean() - r.mean()) if k.any() else None}


# ---------------------------------------------------------------------------------------------- the store
def _model(text: str) -> LogitModel:
    return load_record_json(LogitModel, text, source="aux.db self_retrain_models")


def active_models(db: Database, broker: str) -> dict[str, dict[str, Any]]:
    with db.connection() as conn:
        got = conn.execute("SELECT arm_id, model_version, activation_ns, model FROM self_retrain_models "
                           "WHERE broker = ? AND status = 'active'", (broker,)).fetchall()
    return {str(a): {"model_version": v, "activation_ns": int(t), "model": _model(m)} for a, v, t, m in got}


def previous_passes(db: Database, broker: str, cutoff: pd.Timestamp) -> dict[str, dict[str, Any]]:
    """The arms whose model passed the gate in the broker's ok run of the week before (cutoff exactly 7 days
    earlier: a missed week breaks "two consecutive passes")."""
    with db.connection() as conn:
        r = conn.execute("SELECT run_id FROM self_retrain_runs WHERE broker = ? AND status = 'ok' AND cutoff_ns = ? "
                         "ORDER BY run_ns DESC LIMIT 1",
                         (broker, int((cutoff - pd.Timedelta(days=7)).value))).fetchone()
        if r is None:
            return {}
        got = conn.execute("SELECT arm_id, model_version, activation_ns, cutoff_ns, model FROM self_retrain_models "
                           "WHERE run_id = ? AND gate_pass = 1", (r[0],)).fetchall()
    return {str(a): {"model_version": v, "activation_ns": int(t), "cutoff_ns": int(c), "model": _model(m)}
            for a, v, t, c, m in got}


def read_arms(rows: pd.DataFrame, *, named_controls: Mapping[str, str], rules: Mapping[str, Mapping[str, Any]],
              cutoff: pd.Timestamp, active: Mapping[str, Mapping[str, Any]] | None = None,
              passes: Mapping[str, Mapping[str, Any]] | None = None, boot: int = BOOT,
              spec: Spec = SR_V2) -> dict[str, dict[str, Any]]:
    """Per eligible strategy arm: OOS metrics, the final model fitted on all its rows, the forward read of its active
    model (demotion) and of its previous-run passer (confirmation). Pure: no store. sr-v3 (`oos_judged_only`): the
    metrics are counts only (`count_metrics`); `complete_judged` reads the outcomes of the run's judged pairs."""
    if rows.empty:
        return {}
    strat = rows[~rows["is_control"].astype(bool)]
    hist = strat["arm_id"].astype(str).isin(HISTORY_OF)
    last_hist: dict[str, pd.Timestamp] = {}
    if hist.any():
        strat = strat.copy()
        strat.loc[hist, "arm_id"] = strat.loc[hist, "arm_id"].astype(str).map(HISTORY_OF)
        for new, d in strat[hist].groupby("arm_id")["decision_utc"]:
            last_hist[str(new)] = pd.Timestamp(d.max()) + pd.Timedelta(microseconds=1)
    strat = strat[[str(a) in PRENAMED or not strategy_excluded(str(a), rules) for a in strat["arm_id"]]]
    out: dict[str, dict[str, Any]] = {}
    for arm_id, ar in strat.groupby("arm_id"):
        arm = str(arm_id)
        rd = (rules.get(arm) or {}).get("retired_day") if arm in PRENAMED else None
        cuts = [c for c in (None if rd is None else pd.Timestamp(str(rd), tz="UTC") + pd.Timedelta(days=1),
                            last_hist.get(arm)) if c is not None]
        oos = walk_forward(ar, oos_from=max(cuts) if cuts else None, embargo=embargo_of(ar, spec))
        since = pd.Timestamp(oos["decision_utc"].min()) if len(oos) else cutoff
        ctrl = control_rows(rows, arm, named=named_controls, since=since, rules=rules)
        m = count_metrics(oos, ctrl) if spec.oos_judged_only else oos_metrics(oos, ctrl, boot=boot)
        m["n_rows"] = int(len(ar))
        m["dup_bar_share"] = dup_bar_share(ar)
        y = (ar["label_sim_r"].to_numpy(dtype=float) > 0).astype(int)
        model = fit_logit(design(ar), y) if len(ar) >= MIN_TRAIN and len(set(y.tolist())) == 2 else None
        act = (active or {}).get(arm)
        first = (passes or {}).get(arm)
        out[arm] = {"metrics": m, "model": model,
                    "active": act, "forward": forward_read(rows, arm, act["model"], act["activation_ns"]) if act
                    else None,
                    # code review L3: from the first passer's DATA cutoff, not its (late, for a catch-up) activation
                    "first_pass": first, "confirm": forward_read(rows, arm, first["model"],
                                                                 first.get("cutoff_ns", first["activation_ns"]))
                    if first else None}
        if spec.oos_judged_only:
            out[arm] |= {"_oos": oos, "_control": ctrl, "_boot": boot}
    return out


def complete_judged(reads: Sequence[dict[str, Any]]) -> list[tuple[str, str]]:
    """sr-v3 (quant Q2): the run's judged pairs (label-blind, `judged_pairs`), then the OOS outcomes of those pairs
    only. Every other arm keeps its counts. Returns the judged pairs."""
    judged = judged_pairs(reads)
    for rd in reads:
        for a, v in rd["arms"].items():
            if (str(rd["broker"]), a) in judged and "_oos" in v:
                v["metrics"] = oos_metrics(v["_oos"], v["_control"], boot=int(v["_boot"])) | {
                    k: v["metrics"][k] for k in ("n_rows", "dup_bar_share")}
    return judged


def compute_broker(db: Database, *, broker: str, cutoff: pd.Timestamp,
                   holdout: tuple[pd.Timestamp, pd.Timestamp] | None, named_controls: Mapping[str, str],
                   rules: Mapping[str, Mapping[str, Any]], held_out: Collection[str] = (),
                   instruments: Mapping[str, str] | None = None, nominal_ok: Collection[str] = (),
                   capped: Collection[tuple[str, str]] = (), spec: Spec = SR_V2,
                   rank3_beyond_24h: Collection[str] = ()) -> dict[str, Any]:
    """One broker's reads (nothing written)."""
    rows, counts = load_rows(db, broker=broker, cutoff=cutoff, holdout=holdout, held_out=held_out,
                             instruments=instruments, nominal_ok=nominal_ok, capped=capped, spec=spec,
                             rank3_beyond_24h=rank3_beyond_24h)
    active = active_models(db, broker)
    arms = read_arms(rows, named_controls=named_controls, rules=rules, cutoff=cutoff, active=active,
                     passes=previous_passes(db, broker, cutoff), spec=spec)
    return {"broker": broker, "counts": counts, "arms": arms, "active": active}


def cells(db: Database, *, broker: str, by_tf: Mapping[str, Mapping[str, int]], spec: Spec = SR_V2,
          since: pd.Timestamp | None = None, instruments: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Usable-row counts per (broker, asset class, timeframe), every class seen and all CELL_TFS (an empty cell is an
    explicit 0, never absent): candidates recorded (since `since`), and for gold the run's admission funnel `by_tf`
    (`load_rows`). Another class has candidates but no admitted row (labels, costs and snapshots are gold-only).
    Counts only: no label value is read."""
    from goldbot.instruments import UNKNOWN_CLASS, registry
    from goldbot.research import arms as arm_spec
    inst = arm_spec.arm_instruments() if instruments is None else instruments
    reg = registry()

    def klass(name: str | None) -> str:
        spec_ = reg.instruments.get(str(name)) if name is not None else None
        return str(spec_.asset_class) if spec_ is not None else UNKNOWN_CLASS
    with db.connection() as conn:
        got = conn.execute("SELECT arm_id, tf, COUNT(*) FROM exploration_obs WHERE broker = ? AND decision_ns >= ? "
                           "GROUP BY arm_id, tf", (broker, np.iinfo(np.int64).min if since is None
                                                    else int(pd.Timestamp(since).value))).fetchall()
    gold_class = klass(GOLD)
    seen: dict[tuple[str, str], int] = {}
    for arm, tf, n in got:
        name = inst.get(str(arm))
        key = (gold_class if name == GOLD else klass(name), str(tf))
        seen[key] = seen.get(key, 0) + int(n)
    classes = sorted({c for c, _ in seen} | {gold_class})
    out: list[dict[str, Any]] = []
    for c in classes:
        for tf in sorted(set(CELL_TFS) | {t for k, t in seen if k == c}, key=lambda t: (t not in CELL_TFS, t)):
            f = dict(by_tf.get(tf, {})) if c == gold_class else {}
            row = {"spec": spec.version, "broker": broker, "class": c, "tf": tf, "candidates": seen.get((c, tf), 0),
                   **{k: int(f.get(k, 0)) for k in FUNNEL}}
            row["usable"] = row["with_features"]
            out.append(row)
    return out


def judged_pairs(reads: Sequence[Mapping[str, Any]]) -> list[tuple[str, str]]:
    """The run's judged (broker, arm) pairs: eligible pairs of a PRENAMED arm, at most MAX_JUDGED, most candidates
    first (label-blind ranking; ties by broker then arm)."""
    ok = [(str(rd["broker"]), a, int(v["metrics"].get("n_rows") or 0)) for rd in reads
          for a, v in rd["arms"].items() if eligible(v["metrics"]) is None and v["model"] is not None]
    ok = [t for t in ok if t[1] in PRENAMED]
    return [(b, a) for b, a, _ in sorted(ok, key=lambda t: (-t[2], t[0], t[1]))[:MAX_JUDGED]]


def decide(reads: Sequence[Mapping[str, Any]], *, serve: bool) -> dict[tuple[str, str], dict[str, Any]]:
    """Every (broker, arm) of the run: gate, confirmation and demotion (module docstring steps 3-4)."""
    judged = judged_pairs(reads)
    pj: dict[tuple[str, str], float] = {}
    for rd in reads:
        for a, v in rd["arms"].items():
            if (rd["broker"], a) in judged:
                m = v["metrics"]
                pj[(rd["broker"], a)] = max(float(m["p_nomodel"] if m["p_nomodel"] is not None else 1.0),
                                            float(m["p_control"] if m["p_control"] is not None else 1.0))
    adj = holm(pj)
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for rd in reads:
        for a, v in rd["arms"].items():
            key = (str(rd["broker"]), a)
            m = v["metrics"]
            why = eligible(m)
            fails: list[str] = []
            if why is not None:
                fails.append(f"insufficient: {why}")
            elif key not in adj:
                fails.append(f"not judged: not a pre-named arm, or outside the run's {MAX_JUDGED} judged pairs")
            else:
                if float(m["edge_vs_nomodel"]) < MIN_EDGE_R:
                    fails.append(f"edge vs no-model {m['edge_vs_nomodel']:+.3f} R < {MIN_EDGE_R}")
                if m["edge_vs_control"] is None or float(m["edge_vs_control"]) < MIN_EDGE_R:
                    fails.append(f"edge vs control {m['edge_vs_control']} R < {MIN_EDGE_R}")
                if m["mean_kept"] is None or float(m["mean_kept"]) <= 0:
                    fails.append(f"kept mean {m['mean_kept']} R <= 0")
                if m["edge_gross"] is None or float(m["edge_gross"]) < 0:
                    fails.append(f"gross edge {m['edge_gross']} R < 0")
                if adj[key] > ALPHA:
                    fails.append(f"Holm p {adj[key]:.4f} > {ALPHA}")
            passed = not fails and v["model"] is not None
            fwd, conf = v["forward"], v["confirm"]
            decayed = fwd is not None and fwd["n"] >= MIN_FORWARD and (fwd["edge_vs_nomodel"] is None
                                                                       or fwd["edge_vs_nomodel"] < DEMOTE_EDGE_R)
            confirmed = v["first_pass"] is not None and (conf is None or conf["n"] < MIN_CONFIRM or (
                conf["edge_vs_nomodel"] is not None and conf["edge_vs_nomodel"] >= 0))
            if decayed:
                status, reason = "shadow", f"forced shadow: active model's forward edge {fwd['edge_vs_nomodel']} R " \
                                           f"< {DEMOTE_EDGE_R} on {fwd['n']}"
            elif passed and not serve:
                status, reason = "shadow", "passed, serving switched off"
            elif passed and confirmed:
                status, reason = "active", "passed in two consecutive runs"
            elif passed:
                status, reason = "shadow", ("passed; awaiting a second consecutive pass" if v["first_pass"] is None
                                            else f"passed; first passer's forward edge {conf and conf['edge_vs_nomodel']}"
                                                 " R < 0")
            else:
                status, reason = "shadow", "; ".join(fails) or "no model"
            prev = None
            if v["active"] is not None:
                prev = ("superseded", f"superseded ({key[1]} active again)") if status == "active" else (
                    "demoted", reason if decayed else f"walk-forward this run: {reason}")
            out[key] = {"status": status, "reason": reason, "gate_pass": passed, "judged": key in adj,
                        "p_holm": adj.get(key), "previous": prev}
    return out


def _set_status(conn: Any, version: str, status: Status, reason: str) -> None:
    conn.execute("UPDATE self_retrain_models SET status = ?, status_reason = ?, status_ns = ? WHERE model_version = ?",
                 (status, reason, now_ns(), version))


def apply(db: Database, read: Mapping[str, Any], decisions: Mapping[tuple[str, str], Mapping[str, Any]], *,
          slot: pd.Timestamp, cutoff: pd.Timestamp, activation: pd.Timestamp, k_ok: bool,
          hold: str | None = None) -> dict[str, Any]:
    """Write one broker's models, status changes and run row. `k_ok` False, or a `hold` reason (a broker read of the
    run failed, so its Holm family is incomplete): nothing becomes active (fail closed)."""
    broker = str(read["broker"])
    run_id = f"{SPEC_VERSION}-{broker}-{cutoff:%Y%m%dT%H%M}"
    counts = read["counts"]
    detail: dict[str, Any] = {}
    n_weights = 0
    with db.write() as conn:
        for arm, v in read["arms"].items():
            d = dict(decisions[(broker, arm)])
            if d["status"] == "active" and (not k_ok or hold):
                why = "K rows not written" if not k_ok else str(hold)
                d["status"], d["reason"] = "shadow", f"passed; {why}: fail closed"
                if d["previous"] is not None:
                    d["previous"] = ("demoted", f"{why}: fail closed")
            if v["active"] is not None and d["previous"] is not None:
                _set_status(conn, v["active"]["model_version"], d["previous"][0], d["previous"][1])
            model: LogitModel | None = v["model"]
            if model is not None:
                n_weights += model.n_weights
                conn.execute(
                    "INSERT OR REPLACE INTO self_retrain_models (model_version, run_id, broker, arm_id, spec_version, "
                    "status, status_reason, gate_pass, cutoff_ns, activation_ns, n_train, n_features, n_weights, "
                    "model, oos, recorded_ns, status_ns) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (f"{run_id}-{arm}", run_id, broker, arm, SPEC_VERSION, d["status"], d["reason"],
                     int(bool(d["gate_pass"])), int(cutoff.value), int(activation.value),
                     int(v["metrics"]["n_rows"]), len(INPUTS), model.n_weights, model.model_dump_json(),
                     json.dumps(v["metrics"] | {k: d[k] for k in ("judged", "p_holm")}, default=float), now_ns(),
                     now_ns()))
            detail[arm] = {"decision": d["status"], "reason": d["reason"], "judged": d["judged"],
                           "reactivation_candidate": d["status"] == "active",
                           "p_holm": d["p_holm"], "previous": d["previous"], "forward": v["forward"],
                           "confirm": v["confirm"],
                           **{k: v["metrics"].get(k) for k in ("n_rows", "dup_bar_share", "n_oos", "n_kept", "auc",
                                                                "edge_vs_nomodel",
                                                                "edge_vs_control", "edge_gross", "edge_r1",
                                                                "p_nomodel", "p_control", "boot_no_kept")}}
        # an active model whose arm had no rows this run is demoted: it is never left active unjudged
        for arm, act in read["active"].items():
            if arm not in read["arms"]:
                _set_status(conn, act["model_version"], "demoted", f"no rows in run {run_id}")
                detail[arm] = {"decision": "demoted", "reason": "no rows in run", "previous": act["model_version"]}
        n_active = int(conn.execute("SELECT COUNT(*) FROM self_retrain_models WHERE broker = ? AND status = 'active'",
                                    (broker,)).fetchone()[0])
        n_demoted = sum(1 for d in detail.values() if (d.get("previous") or ("",))[0] == "demoted"
                        or d["decision"] == "demoted")
        n_shadow = sum(1 for d in detail.values() if d["decision"] == "shadow")
        conn.execute(
            "INSERT OR REPLACE INTO self_retrain_runs (run_id, broker, spec_version, slot_ns, cutoff_ns, run_ns, status, "
            "n_labels, n_with_features, n_features, n_weights, n_arms, n_active, n_shadow, n_demoted, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, 'ok', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, broker, SPEC_VERSION, int(slot.value), int(cutoff.value), now_ns(), counts["labels"],
             counts["with_features"], len(INPUTS), n_weights, len(read["arms"]), n_active, n_shadow, n_demoted,
             json.dumps({"counts": counts, "decisions": detail, "k_ok": k_ok,
                         **{k: read[k] for k in ("cells", "cells_sr_v3") if k in read}}, default=float)))
    return {"run_id": run_id, "broker": broker, **counts, "features": len(INPUTS), "weights": n_weights,
            "arms_trained": len(read["arms"]), "active": n_active, "demoted": n_demoted, "shadow": n_shadow}


def run_broker(db: Database, *, broker: str, slot: pd.Timestamp, cutoff: pd.Timestamp, activation: pd.Timestamp,
               holdout: tuple[pd.Timestamp, pd.Timestamp] | None, serve: bool, named_controls: Mapping[str, str],
               rules: Mapping[str, Mapping[str, Any]] | None = None, held_out: Collection[str] = (),
               instruments: Mapping[str, str] | None = None) -> dict[str, Any]:
    """A one-broker run end to end (tests and the CLI; the job runs `compute_broker` per broker, one `decide`, the K
    rows, then `apply`)."""
    read = compute_broker(db, broker=broker, cutoff=cutoff, holdout=holdout, named_controls=named_controls,
                          rules=rules or {}, held_out=held_out, instruments=instruments)
    return apply(db, read, decide([read], serve=serve), slot=slot, cutoff=cutoff, activation=activation, k_ok=True)


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


def demote_broker(db: Database, broker: str, reason: str) -> int:
    """Every ACTIVE model of one broker demoted (its read failed this run: code review L1, fail closed)."""
    with db.write() as conn:
        return int(conn.execute("UPDATE self_retrain_models SET status = 'demoted', status_reason = ?, status_ns = ? "
                                "WHERE status = 'active' AND broker = ?", (reason, now_ns(), broker)).rowcount)


def demote_all(db: Database, reason: str) -> int:
    """Every ACTIVE model demoted (the job switched off, or serving off): nothing stays active unwatched."""
    with db.write() as conn:
        return int(conn.execute("UPDATE self_retrain_models SET status = 'demoted', status_reason = ?, status_ns = ? "
                                "WHERE status = 'active'", (reason, now_ns())).rowcount)


def latest_runs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """The newest run's rows (one per broker), each with `n_active` read from the models table (every model active
    now, not only this run's decisions); [] before any run or before the table exists."""
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'self_retrain_runs'").fetchone() \
            is None:
        return []
    r = conn.execute("SELECT MAX(slot_ns) FROM self_retrain_runs").fetchone()
    if r is None or r[0] is None:
        return []
    cols = ["run_id", "broker", "status", "slot_ns", "run_ns", "n_labels", "n_with_features", "n_features",
            "n_weights", "n_arms", "n_active", "n_shadow", "n_demoted", "detail"]
    got = [dict(zip(cols, g, strict=True)) for g in
           conn.execute(f"SELECT {', '.join(cols)} FROM self_retrain_runs WHERE slot_ns = ?", (r[0],)).fetchall()]
    active = dict(conn.execute("SELECT broker, COUNT(*) FROM self_retrain_models WHERE status = 'active' "
                               "GROUP BY broker").fetchall())
    for g in got:
        g["n_active"] = int(active.get(g["broker"], 0))
    return got


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
           f"weights, {active} active (re-activation candidates: nothing trades)")
    if now - last > RUN_MAX_AGE:
        return "warn", f"no self-retrain run for {(now - last).days} d (> {RUN_MAX_AGE.days} d); {msg}"
    if bad:
        return "warn", f"self-retrain run failed ({', '.join(bad)}); {msg}"
    if labels == 0 or feats == 0:
        return "warn", f"self-retrain trained on 0 labels with features; {msg}"
    return "ok", msg
