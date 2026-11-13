"""Exploration arms, data side (architect plan 2026-10-25, lane L3; pre-registration H-60/H-61/H-62,
docs/research/prereg-2026-10-25-high-volume.md, and the H-62 spec docs/research/h62-meta-labeller-spec-2026-10-25.md).

What exploration taught us, kept apart from every trading record. aux.db, migrations 16 and 17.

Broker key (ADR 0043 amendment, quant 2026-10-26): "evidence and costs are keyed by BROKER (and arm); never pooled
across brokers; ICM hosts may pool with each other only because the cost basis is the same". Every candidate row
carries the broker of the account that recorded it; a label is replayed with that broker's own cost model
(`ReplayCosts.broker`); the bandit weights and the H-62 models are fitted and read per (broker, style). Every reader
that learns takes the broker as a required argument, so no caller can pool by omission.

* `record_candidate`  one row per exploration CANDIDATE at its decision, taken or not, with the propensity the policy
                      logged (taken, blocked_reason, branch, u, mu, tau_arm, pi_rand_arm), the served meta_p and
                      versions, and the decision-time feature snapshot (`features`, the frozen v1 list of its style,
                      with `available_utc_max`). Written once; a row failing validation is kept with its `quarantine`
                      reason and never trained on.
* `record_fill`       a taken candidate's broker fill and close: `label_fill_r`, a DIAGNOSTIC (parity, off-policy
                      gap), never the training target. Written once.
* `label_candidates`  the training label for every candidate, taken or not: `label_sim_r`, a bar replay on 1m bid/ask
                      (entry at the first 1m open at or after the decision at ask/bid, barriers read minute by minute,
                      the stop first when both fall in one minute, the time limit at the first minute at or after
                      decision + max_hold): the spread paid once through the bid/ask path (never charged again),
                      slippage max(0.15, measured) USD/oz on each leg and never credited, commission, the paid swap per
                      rollover; and `label_mid_r`, the same path on mid prices with no cost (the gross diagnostic).
                      The cost table is point in time: its effective time must not be after the labelling cutoff.
                      Written once; an unresolved label stays NULL (excluded, never censored). This is replay r1.
* `label_candidates_v2`  replay r2 (EVAL-2, prereg-2026-11-09-eval2.md section 5), in the side table
                      `exploration_replay` keyed (candidate_id, replay_version), never over label_sim_r: the same path
                      with each leg charged from the broker's own exploration fills (`ledger_legs` ->
                      `replay_costs_v2`: max(0, adverse upper 95 % bound) per session and leg at >= min_fills, else the
                      prior; never a credit), the stop's overshoot in its fill price, and the 1m/5m time limit from
                      decision + send delay. `void_non_gold_labels` voids every non-gold r1 label (replayed on gold
                      bars before 2026-11-04); `read_obs` names the version it reads and never returns a void label.
* `training_table`    a style's admissible rows (label resolved by `as_of`, not quarantined, no holdout row) with the
                      style's frozen v1 columns from the snapshot; rows without a snapshot (history and tests only) are
                      as-of joined (`asof_join`, available_utc <= decision: spec rule B). The row invariant
                      available_utc_max <= decision_utc fails the build.
* `cross_fit_meta`    H-62: LightGBM per style, 5 contiguous session-day blocks, purge by the style's max hold on both
                      sides, embargo 1 session day (5 for swing); out-of-fold scores, a calibrator on them, scored per
                      arm on the net and the gross label. `retrain_due`: >= 1,500 new and >= 3,000 admissible labels.
* `thompson_shares` / `write_weights` / `weights`   H-61: a Normal-Inverse-Gamma posterior on net R per arm (prior: the
                      arm's H-60 history prior, `prior_from_history`, else flat), each style's share = P(arm best within
                      its style), then held in [FLOOR_SHARE, CAP_SHARE] (and an arm's own ceiling below the cap) without
                      raising any ceiling. Missing, stale, corrupt or incomplete weights read as equal shares in the
                      same bounds.
* `allocate`          the nightly shares: equal among the enterable arms by default (ML review 2026-11-13: the bandit's
                      weighted next-day R +0.085 against equal +0.086, rank correlation 0.003 over 24 broker-days), the
                      bandit above only when settings research.exploration.allocator is `thompson`.

Retired 2026-11-13 (ML review; code and tables kept): the H-62 meta-labeller's Sunday retrain (`cross_fit_meta`,
`save_model`) runs only while research.exploration.meta_labeller is on: `exploration_models` holds 0 rows and no
candidate carried a snapshot it could train on.

Isolation (research/population.py `agent_trades`): nothing here reads or writes the shadow book, closed trades, the
fills table, the probe ledger or the phase gate; the test suite checks the module never names them.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import pickle
import re
import sqlite3
from collections.abc import Callable, Collection, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import Field, model_validator

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.data.calendar import DEFAULT_SESSIONS, SessionTable
from goldbot.data.store import asof_join
from goldbot.data.timeutil import NEW_YORK, epoch_ns
from goldbot.db.connection import Database, StateDbError, now_ns
from goldbot.labels.triple_barrier import SwapSpec, rollover_nights
from goldbot.research import arms as arm_spec
from goldbot.research.evidence_exclusions import Scope, excluded
from goldbot.research.model import MAX_FEATURES, MetaLabelModel, check_feature_cap, fit_calibrator

log = logging.getLogger("goldbot.exploration")

Style = Literal["scalp", "intraday", "swing"]
STYLES: tuple[str, ...] = ("scalp", "intraday", "swing")
# a candidate may also come from the growth pot (growth sprint 2026-11-01, GS1/GS1C): recorded and replayed like any
# (a refused growth entry is the read's shadow fill, quant requirement 5), never in a style's training table or weights
CANDIDATE_STYLES: tuple[str, ...] = (*STYLES, "growth")
TIMEFRAMES: tuple[str, ...] = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")
BRANCHES: tuple[str, ...] = ("ranked", "random", "uniform", "blocked")

# H-61 allocation (prereg): within a style, every live arm keeps at least FLOOR_SHARE and at most CAP_SHARE
FLOOR_SHARE = 0.05
CAP_SHARE = 0.30
MAX_WEIGHTS_AGE = pd.Timedelta(days=3)          # older bandit weights are stale: equal shares until the next run
THOMPSON_DRAWS = 4000
PRIOR_SD_R = 1.0                                # net-R spread of one trade under the flat prior
# a gold label beyond this many R in size is suspect (brain 2026-11-04: FX and sugar controls replayed on gold bars at
# -2,679 R; the learners now read gold rows only, `read_obs` `instrument`). It is kept, never dropped (a real gap
# through a tight 1m stop must stay; quant review of 75824e5c), counted per arm in the run's detail and logged
MAX_LABEL_R = 20.0
GOLD = "XAUUSD"                                 # ReplayCosts is gold's per-oz model: the only instrument it prices

# H-60 priors: history 2019-01-01..2025-09-30 only (the current cost era); the holdout is never read
PRIOR_HISTORY_START = pd.Timestamp("2019-01-01", tz="UTC")
HOLDOUT_START = pd.Timestamp("2025-10-01", tz="UTC")
HOLDOUT_END = pd.Timestamp("2026-10-01", tz="UTC")       # exclusive
PRIOR_SHRINK = 0.5
PRIOR_N_MAX = 50.0
PRIOR_N_PER_TRADES = 100.0
# the H-60 priors rest on IC Markets' cost history (the canonical cost tables): they seed only these brokers' bandits;
# any other broker starts from the flat prior (ADR 0043 amendment 2026-10-26, quant review of c8433d3d)
PRIOR_BROKERS: frozenset[str] = frozenset({"icm"})
# accounts.yaml `broker`; also a path segment (models/h62/<broker>/): matched in full (`fullmatch`), never `match`,
# whose `$` also accepts a trailing newline
BROKER_KEY = re.compile(r"[a-z0-9_-]+")

# label replay (prereg H-60 costs): slippage per leg never below the prior, never credited
SLIPPAGE_FLOOR_USD = 0.15

# versioned replay (EVAL-2 prereg-2026-11-09-eval2.md section 5): r1 is the row's own label_sim_r (the 2 x $0.15
# prior), r2 the side table `exploration_replay` (measured legs, no credit, stop overshoot). Never overwritten
ReplayVersion = Literal["r1", "r2"]
REPLAY_V1: ReplayVersion = "r1"
REPLAY_V2: ReplayVersion = "r2"
REPLAY_VERSIONS: tuple[ReplayVersion, ...] = (REPLAY_V1, REPLAY_V2)
# read_obs's label column -> the column holding it in each version's table
_LABEL_SQL: dict[str, dict[str, str]] = {
    REPLAY_V1: {"label_sim_r": "label_sim_r", "label_mid_r": "label_mid_r", "label_exit_ns": "label_exit_ns",
                "label_barrier": "label_barrier", "cost_model_version": "cost_model_version",
                "cost_effective_ns": "cost_effective_ns"},
    REPLAY_V2: {"label_sim_r": "sim_r", "label_mid_r": "mid_r", "label_exit_ns": "label_exit_ns",
                "label_barrier": "barrier", "cost_model_version": "cost_model_version",
                "cost_effective_ns": "cost_effective_ns"},
}
# replay v2: "1m/5m deadline = decision + delay + max_hold" (the engine holds 1m/5m from the fill on its own clock)
SEND_CLOCK_TFS: frozenset[str] = frozenset({"1m", "5m"})
# ledger exit reasons by leg (engine/exploration.py `record_close`); anything else (broker_close, weekend_flat, an
# orphan) is no leg of the arm's own brackets
EXIT_REASON_LEG: dict[str, str] = {"stop": "stop", "target": "target", "exploration_hold": "time", "time_exit": "time"}
VOID_NON_GOLD_REASON = ("non-gold candidate replayed on gold bars with gold costs (before the 2026-11-04 instrument "
                        "guard; EVAL-2 method section 5, S5 decomposition section 0)")

# H-62 cross-fitting and retrain gate (spec sections 3 and 5)
N_FOLDS = 5
EMBARGO_DAYS: dict[str, int] = {"scalp": 1, "intraday": 1, "swing": 5}
RETRAIN_MIN_NEW = 1500
RETRAIN_MIN_TOTAL = 3000
H62_PARAMS: dict[str, Any] = dict(objective="binary", num_leaves=7, min_child_samples=100, learning_rate=0.03,
                                  n_estimators=200, feature_fraction=0.7, bagging_fraction=0.8, bagging_freq=1,
                                  lambda_l2=10.0, verbose=-1)

# H-62 feature list v1 (spec section 1), frozen per quarter; 38 market columns + side + arm_id = 40 per style
FEATURE_VERSION_V1 = "f-079deacc61"
FEATURE_LISTS: dict[str, tuple[str, ...]] = {
    "h62-v1-scalp": (
        "em_atr_cost", "spread_atr", "spread_rel_median_48", "tick_count_z_48", "atr_ratio_14_100", "rv_ratio",
        "vol_ts_yz_20_60", "vol_of_vol_60", "jump_z", "bars_since_jump", "hour_utc", "min_since_london_open",
        "min_since_newyork_open", "dow", "ret_1", "ret_4", "ret_16", "bb_z_20", "rsi14", "dist_vwap48_atr", "mfi12",
        "body_pct", "er_10", "vr_4", "dist_ema20_atr", "slope_ema50", "dist_high_24_atr", "dist_low_24_atr",
        "compression_8_96", "sess_high_dist_atr", "sess_low_dist_atr", "prev_sess_high_dist_atr",
        "prev_sess_low_dist_atr", "sweep_dir", "round_10_dist_atr", "h1_dist_ema50_atr", "h1_adx14", "h4_slope_ema50"),
    "h62-v1-intraday": (
        "em_atr_cost", "spread_atr", "tick_count_z_48", "atr_ratio_14_100", "rv_ratio", "vol_ts_yz_20_60", "jump_z",
        "em_yz_16_cost", "hour_utc", "session_id", "dow", "min_since_newyork_open", "ret_4", "ret_16", "ret_96",
        "tsmom_score", "im_ldn_ret_atr", "dist_ema50_atr", "slope_ema50", "dist_ema200_atr", "adx14", "ribbon_state",
        "bb_z_20", "dist_vwap48_atr", "mfi12", "er_30", "donchian_pos_20", "compression_8_96", "sess_high_dist_atr",
        "sess_low_dist_atr", "pdh_dist_atr", "pdl_dist_atr", "prev_sess_pos", "sweep_dir", "h1_adx14",
        "h4_dist_ema50_atr", "d1_slope_ema20", "d1_atr_ratio_14_100"),
    "h62-v1-swing": (
        "em_atr_cost", "spread_atr", "atr_ratio_14_100", "rv_ratio", "vol_ts_yz_20_60", "vol_of_vol_60", "jump_z",
        "atr14_pct", "dow", "hour_utc", "ret_4", "ret_16", "ret_96", "tsmom_z_24", "tsmom_z_120", "tsmom_z_480",
        "tsmom_agree", "dist_ema50_atr", "dist_ema200_atr", "slope_ema50", "slope_ema200", "adx14", "ribbon_state",
        "rsi14", "bb_z_20", "mfi12", "er_30", "hurst_128", "donchian_pos_20", "dist_high_96_atr", "dist_low_96_atr",
        "pwh_dist_atr", "pwl_dist_atr", "structure_state", "bos_dir", "d1_dist_ema50_atr", "d1_adx14", "d1_rv_20"),
}
STYLE_FEATURE_LIST: dict[str, str] = {s: f"h62-v1-{s}" for s in STYLES}


def model_columns(feature_list_id: str) -> list[str]:
    """The model's inputs: the frozen market columns, then `side` and `arm_id` (both count toward the 40)."""
    cols = [*FEATURE_LISTS[feature_list_id], "side", "arm_id"]
    check_feature_cap(cols)
    return cols


# ---------------------------------------------------------------------------------------------- helpers
def _ns(ts: pd.Timestamp) -> int:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        raise ValueError("exploration timestamps must be timezone-aware UTC")
    return int(epoch_ns(pd.DatetimeIndex([t.tz_convert("UTC")]))[0])


def _ts(ns: Any) -> pd.Timestamp:
    return pd.Timestamp(int(ns), unit="ns", tz="UTC")


def session_day(decision_utc: pd.Timestamp | pd.DatetimeIndex) -> Any:
    """Spec section 3: the date of decision_utc + 7 h in America/New_York (the day rolls at 17:00 New York)."""
    idx = pd.DatetimeIndex([decision_utc] if isinstance(decision_utc, pd.Timestamp) else decision_utc)
    days = np.asarray((idx.tz_convert(NEW_YORK) + pd.Timedelta(hours=7)).strftime("%Y-%m-%d"))
    return str(days[0]) if isinstance(decision_utc, pd.Timestamp) else days


def _finite(v: float | None) -> float | None:
    return None if v is None or not math.isfinite(float(v)) else float(v)


# ---------------------------------------------------------------------------------------------- candidates
_CANDIDATE_COLS = ("candidate_id", "account_id", "broker", "arm_id", "arm_config_hash", "family", "style", "tf", "side",
                   "is_control", "decision_ns", "session_day", "stop_dist", "target_dist", "max_hold_s", "taken",
                   "blocked_reason", "branch", "u", "mu", "tau_arm", "pi_rand_arm", "arm_share", "meta_p", "model_version",
                   "bandit_version", "feature_version", "feature_list_id", "features", "available_utc_max_ns",
                   "snapshot_ref", "quarantine", "recorded_ns")


def _candidate_problems(*, style: str, tf: str, side: int, stop_dist: float, target_dist: float, max_hold_s: int,
                        taken: bool, branch: str | None, mu: float | None, u: float | None,
                        features: Mapping[str, Any] | None, feature_list_id: str | None, avail_max: int | None,
                        decision: int) -> list[str]:
    out = []
    if style not in CANDIDATE_STYLES:
        out.append(f"style {style!r} not one of {CANDIDATE_STYLES}")
    if tf not in TIMEFRAMES:
        out.append(f"timeframe {tf!r} not one of {TIMEFRAMES}")
    if side not in (-1, 1):
        out.append(f"side {side} not +-1")
    if not (math.isfinite(stop_dist) and stop_dist > 0):
        out.append("stop distance not a positive USD/oz")
    if not (math.isfinite(target_dist) and target_dist > 0):
        out.append("target distance not a positive USD/oz")
    if not max_hold_s > 0:
        out.append("max_hold_s not positive")
    if branch is not None and branch not in BRANCHES:
        out.append(f"branch {branch!r} not one of {BRANCHES}")
    if mu is not None and not (math.isfinite(mu) and 0 <= mu <= 1):
        out.append("mu outside [0, 1]")
    if u is not None and not (math.isfinite(u) and 0 <= u <= 1):
        out.append("u outside [0, 1]")
    if taken and mu is not None and mu == 0:
        out.append("taken with mu 0 (the policy had no choice: logging error)")
    if features is not None:
        if feature_list_id not in FEATURE_LISTS:
            out.append(f"feature_list_id {feature_list_id!r} unknown")
        elif set(FEATURE_LISTS[feature_list_id]) - set(features):
            out.append("snapshot misses columns of its feature list")
        if avail_max is None:
            out.append("snapshot without available_utc_max")
    if avail_max is not None and avail_max > decision:
        out.append("available_utc_max after decision_utc (row invariant)")
    return out


def record_candidate(db: Database, *, candidate_id: str, account_id: str, broker: str, arm_id: str, family: str,
                     style: str,
                     tf: str, side: int, decision_utc: pd.Timestamp, stop_dist: float, target_dist: float,
                     max_hold_s: int, taken: bool, is_control: bool = False, arm_config_hash: str | None = None,
                     blocked_reason: str | None = None, branch: str | None = None, u: float | None = None,
                     mu: float | None = None, tau_arm: float | None = None, pi_rand_arm: float | None = None,
                     arm_share: float | None = None, meta_p: float | None = None, model_version: str | None = None,
                     bandit_version: str | None = None, feature_version: str | None = None,
                     feature_list_id: str | None = None, features: Mapping[str, float | None] | None = None,
                     available_utc_max: pd.Timestamp | None = None, snapshot_ref: str | None = None) -> bool:
    """One exploration candidate at its decision (taken or not; a blocked one with mu 0 and its reason). True when
    written, False when `candidate_id` exists (the first record stands). `decision_utc` is the decision bar's close,
    the as-of point of every feature; `broker` is the recording account's broker (accounts.yaml `broker`: the key
    its evidence is learnt under); `stop_dist`/`target_dist` are the arm's own barriers in USD/oz from the entry,
    `max_hold_s` its time limit. `features` is the snapshot as computed at the decision (the style's frozen list),
    `available_utc_max` the latest availability among its columns. Invalid input is written quarantined."""
    if not (candidate_id and account_id and broker and arm_id and family):
        raise ValueError("candidate_id, account_id, broker, arm_id and family are required")
    d = _ns(decision_utc)
    am = _ns(available_utc_max) if available_utc_max is not None else None
    bad = _candidate_problems(style=style, tf=tf, side=int(side), stop_dist=float(stop_dist),
                              target_dist=float(target_dist), max_hold_s=int(max_hold_s), taken=bool(taken),
                              branch=branch, mu=mu, u=u, features=features, feature_list_id=feature_list_id,
                              avail_max=am, decision=d)
    feats = None if features is None else json.dumps({k: _finite(v) for k, v in features.items()})
    row = (candidate_id, account_id, broker, arm_id, arm_config_hash, family, style, tf, int(side),
           int(bool(is_control)), d, session_day(_ts(d)), _finite(stop_dist), _finite(target_dist), int(max_hold_s), int(bool(taken)),
           blocked_reason, branch, _finite(u), _finite(mu), _finite(tau_arm), _finite(pi_rand_arm), _finite(arm_share),
           _finite(meta_p),
           model_version, bandit_version, feature_version, feature_list_id, feats, am, snapshot_ref,
           "; ".join(bad) or None, now_ns())
    with db.write() as conn:
        cur = conn.execute(f"INSERT OR IGNORE INTO exploration_obs ({', '.join(_CANDIDATE_COLS)}) "
                           f"VALUES ({', '.join('?' * len(_CANDIDATE_COLS))})", row)
        written = cur.rowcount == 1
    if bad:
        log.warning("exploration candidate %s quarantined: %s", candidate_id, "; ".join(bad))
    if not written:
        log.info("exploration candidate %s already recorded: the first record stands", candidate_id)
    return written


def record_fill(db: Database, *, candidate_id: str, entry_utc: pd.Timestamp, exit_utc: pd.Timestamp,
                entry_price: float, exit_price: float, cost_per_oz: float, censored: bool) -> bool:
    """A taken candidate's broker fill and close (the fill's client order id is `candidate_id`): `label_fill_r` =
    (side x (exit - entry) - cost_per_oz) / stop_dist, cost_per_oz the measured round trip beyond the fill prices
    (commission and paid swap; the spread is already in the fill prices). A diagnostic, never the training target.
    Written once; False when the candidate is unknown, not taken, or its fill is already recorded."""
    e, x = _ns(entry_utc), _ns(exit_utc)
    if not (math.isfinite(entry_price) and math.isfinite(exit_price) and math.isfinite(cost_per_oz) and x >= e):
        raise ValueError("record_fill needs finite prices and cost and exit at or after entry")
    with db.write() as conn:
        r = conn.execute("SELECT side, stop_dist, taken, fill_recorded_ns FROM exploration_obs WHERE candidate_id = ?",
                         (candidate_id,)).fetchone()
        if r is None or not r[2] or r[3] is not None or not r[1]:
            log.warning("exploration fill %s not recorded (unknown, untaken or already recorded)", candidate_id)
            return False
        # quant Q2 (exploration review 2026-10-25): a censored close (cut short before either barrier) has no
        # label; training reads only trades that ran to their own barrier
        fill_r = None if censored else (int(r[0]) * (exit_price - entry_price) - cost_per_oz) / float(r[1])
        conn.execute("UPDATE exploration_obs SET entry_ns = ?, exit_ns = ?, entry_price = ?, exit_price = ?, "
                     "fill_cost_per_oz = ?, label_fill_r = ?, censored = ?, fill_recorded_ns = ? "
                     "WHERE candidate_id = ? AND fill_recorded_ns IS NULL",
                     (e, x, float(entry_price), float(exit_price), float(cost_per_oz), fill_r, int(bool(censored)),
                      now_ns(), candidate_id))
    return True


def read_obs(db: Database, *, broker: str | None = None, style: str | None = None, arm_id: str | None = None,
             as_of: pd.Timestamp | None = None, include_quarantined: bool = False,
             evidence: Scope | None = None, held_out: Collection[str] = (), instrument: str | None = None,
             instruments: Mapping[str, str] | None = None, replay_version: ReplayVersion = REPLAY_V1) -> pd.DataFrame:
    """Candidates ordered by decision time; times as UTC datetimes (ns). `as_of`: only labels resolved by then;
    `broker`: only that broker's rows (an unkeyed row matches no broker). Every learner passes `broker`. `evidence`:
    leave out the candidates void for that scope (aux.db `evidence_exclusions`, prereg Amendment 4: "void as evidence
    (n, posterior, cost cells, graduation)"); every learner passes "posterior". `held_out`: candidate ids left out in
    memory only (decisions after a quarantine span whose engine restart is not recorded yet). `instrument`: only the
    rows of arms on that instrument key (`instruments`, default the spec's `arms.arm_instruments`); an arm the spec
    does not name (unknown or removed) matches none. Every learner passes GOLD: quant review of 75824e5c, non-gold
    rows labelled on gold bars before 2026-11-04 (XAUEUR, DE40, US500, ... at plausible but wrong R) never train.

    `replay_version` (EVAL-2 method section 5: "the learner and the evaluator name the version they read"): the label
    columns (label_sim_r, label_mid_r, label_exit_utc, label_barrier, cost_model_version, cost_effective_ns) are that
    version's: r1 the row's own, r2 the `exploration_replay` side table's. A label declared void for its version
    (`exploration_label_void`, e.g. every non-gold r1 label) is NULL for EVERY reader, filtered by instrument or not."""
    if replay_version not in REPLAY_VERSIONS:
        raise ValueError(f"replay_version {replay_version!r} not one of {REPLAY_VERSIONS}")
    where: list[str] = ["1 = 1"]
    params: list[Any] = [replay_version, replay_version]
    if not include_quarantined:
        where.append("o.quarantine IS NULL")
    if broker is not None:
        where.append("o.broker = ?")
        params.append(broker)
    if style is not None:
        where.append("o.style = ?")
        params.append(style)
    if arm_id is not None:
        where.append("o.arm_id = ?")
        params.append(arm_id)
    lbl = "o" if replay_version == REPLAY_V1 else "r"
    if as_of is not None:
        where.append(f"void.candidate_id IS NULL AND {lbl}.{_LABEL_SQL[replay_version]['label_sim_r']} IS NOT NULL "
                     f"AND {lbl}.label_exit_ns <= ?")
        params.append(_ns(as_of))
    sel = ", ".join(f"r.{src} AS _r2_{dst}" for dst, src in _LABEL_SQL[REPLAY_V2].items())
    with db.connection() as conn:
        df = pd.read_sql_query(
            f"SELECT o.*, void.candidate_id IS NOT NULL AS _void, {sel} FROM exploration_obs o "
            "LEFT JOIN exploration_replay r ON r.candidate_id = o.candidate_id AND r.replay_version = ? "
            "LEFT JOIN exploration_label_void void ON void.candidate_id = o.candidate_id AND void.replay_version = ? "
            f"WHERE {' AND '.join(where)} ORDER BY o.decision_ns, o.candidate_id", conn, params=params)
        if evidence is not None:
            df = df[~df["candidate_id"].isin(excluded(conn, evidence))].reset_index(drop=True)
    r2 = [f"_r2_{c}" for c in _LABEL_SQL[REPLAY_V2]]
    if replay_version == REPLAY_V2:
        for c in _LABEL_SQL[REPLAY_V2]:
            df[c] = df[f"_r2_{c}"]
    void = df.pop("_void").astype(bool)
    df = df.drop(columns=r2)
    df.loc[void, list(_LABEL_SQL[REPLAY_V2])] = None
    if held_out:
        df = df[~df["candidate_id"].isin(set(held_out))].reset_index(drop=True)
    if instrument is not None:
        inst = arm_spec.arm_instruments() if instruments is None else instruments
        df = df[df["arm_id"].map(lambda a: inst.get(str(a))) == instrument].reset_index(drop=True)
    for c, name in (("decision_ns", "decision_utc"), ("entry_ns", "entry_utc"), ("exit_ns", "exit_utc"),
                    ("label_exit_ns", "label_exit_utc"), ("available_utc_max_ns", "available_utc_max")):
        df[name] = pd.to_datetime(pd.to_numeric(df.pop(c), errors="coerce"), unit="ns", utc=True)
    for c in ("taken", "is_control"):
        df[c] = df[c].astype(bool)
    for c in ("label_sim_r", "label_mid_r", "label_fill_r", "stop_dist", "target_dist", "mu", "meta_p"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    return df


def stamp_brokers(db: Database, broker_of: Mapping[str, str]) -> int:
    """Key the rows recorded before migration 17 (broker NULL) by their account's broker (accounts.yaml), once: a
    recorded broker is never rewritten, and a row whose account is unknown stays unkeyed, so no learner reads it.
    Returns the rows stamped."""
    with db.write() as conn:
        return sum(conn.execute("UPDATE exploration_obs SET broker = ? WHERE broker IS NULL AND account_id = ?",
                                (str(b), str(a))).rowcount for a, b in broker_of.items() if a and b)


# ---------------------------------------------------------------------------------------------- label replay
class ReplayCosts(FrozenRecord):
    """One broker's cost model a label is replayed with, point in time: never effective after the labelling cutoff,
    and only ever applied to that broker's rows (costs are never pooled across brokers)."""
    broker: str = Field(min_length=1)
    version: str
    effective_utc: UtcTimestamp
    slippage_usd: float = Field(SLIPPAGE_FLOOR_USD, ge=0)    # measured per leg; the floor applies on use
    commission_rt_per_oz: float = Field(0.0, ge=0)          # both sides, USD/oz
    swap: SwapSpec | None = None

    @property
    def slip(self) -> float:
        return max(SLIPPAGE_FLOOR_USD, self.slippage_usd)


class Replay(FrozenRecord):
    exit_utc: UtcTimestamp
    sim_r: float
    mid_r: float
    barrier: str


def _walk(t: pd.DatetimeIndex, k: int, deadline: pd.Timestamp, side: int, entry: float, stop_d: float,
          target_d: float, ex_open: np.ndarray, ex_high: np.ndarray, ex_low: np.ndarray,
          stop_overshoot: float = 0.0) -> tuple[pd.Timestamp, float, str] | None:
    """Exit time, price and barrier of a position entered at minute k at `entry`; exits read on the exit side (bid for
    a long, ask for a short). None when the data ends before the time limit with no barrier hit. `stop_overshoot`
    (replay v2, $/oz >= 0): a stop fills at the level less it, or at a gapped open when that is worse."""
    stop, target = entry - side * stop_d, entry + side * target_d
    fill_stop = stop - side * stop_overshoot
    for j in range(k, len(t)):
        if t[j] >= deadline:
            return t[j], float(ex_open[j]), "time"
        o, hi, lo = float(ex_open[j]), float(ex_high[j]), float(ex_low[j])
        end = t[j] + pd.Timedelta(minutes=1)
        if side > 0:
            if lo <= stop:                                  # the stop first when both fall in one minute
                return end, min(fill_stop, o), "stop"       # a gap through it fills at the open
            if hi >= target:
                return end, target, "target"                # never better than the target
        else:
            if hi >= stop:
                return end, max(fill_stop, o), "stop"
            if lo <= target:
                return end, target, "target"
    return None


def replay_label(bars_1m: pd.DataFrame, *, side: int, decision_utc: pd.Timestamp, stop_dist: float,
                 target_dist: float, max_hold_s: int, costs: ReplayCosts,
                 entry_delay_s: float = 0.0) -> Replay | None:
    """Net and gross R of one candidate on 1m bid/ask bars (stamped at their open). See the module docstring. The
    entry is the open of the minute holding decision + `entry_delay_s` (the order's send delay, `arms.send_delay_s`;
    under a minute: the first minute after the decision)."""
    b = bars_1m.sort_values("ts_utc").reset_index(drop=True)
    t = pd.DatetimeIndex(pd.to_datetime(b["ts_utc"], utc=True)).as_unit("ns")
    deadline = pd.Timestamp(decision_utc) + pd.Timedelta(seconds=int(max_hold_s))
    sent = (pd.Timestamp(decision_utc) + pd.Timedelta(seconds=float(entry_delay_s))).floor("min")
    k = int(t.searchsorted(sent, side="left"))
    if k >= len(t) or t[k] != sent or t[k] >= deadline:      # no entry minute: never a later minute's open
        return None
    ent, ex = ("ask", "bid") if side > 0 else ("bid", "ask")
    entry = float(b[f"{ent}_open"].iloc[k])
    net = _walk(t, k, deadline, side, entry, stop_dist, target_dist, b[f"{ex}_open"].to_numpy(float),
                b[f"{ex}_high"].to_numpy(float), b[f"{ex}_low"].to_numpy(float))
    mids = {c: ((b[f"bid_{c}"] + b[f"ask_{c}"]) / 2).to_numpy(float) for c in ("open", "high", "low")}
    gross = _walk(t, k, deadline, side, float(mids["open"][k]), stop_dist, target_dist, mids["open"], mids["high"],
                  mids["low"])
    if net is None or gross is None:
        return None
    exit_t, exit_p, barrier = net
    swap_paid = 0.0
    if costs.swap is not None:
        nights = int(rollover_nights(pd.DatetimeIndex([t[k]]), pd.DatetimeIndex([exit_t]), costs.swap.server_tz,
                                     costs.swap.triple_weekday, costs.swap.seven_night)[0])
        swap_paid = max(0.0, -float(costs.swap.usd_per_oz(np.array([side]))[0])) * nights
    sim = (side * (exit_p - entry) - 2 * costs.slip - costs.commission_rt_per_oz - swap_paid) / stop_dist
    mid = side * (gross[1] - float(mids["open"][k])) / stop_dist
    return Replay(exit_utc=exit_t, sim_r=float(sim), mid_r=float(mid), barrier=barrier)


def instrument_key(symbol: str, broker: str | None = None) -> str:
    """The instrument key (config/instruments.yaml) a key, store symbol or broker symbol stands for: `broker`'s symbol
    first (vantage XAUUSD+, vantage247 XAUUSD247 -> XAUUSD), else any; `symbol` itself when the registry does not
    list it (an unknown symbol is never gold by accident)."""
    from goldbot.instruments import registry
    try:
        reg = registry()
    except (OSError, ValueError) as exc:
        log.warning("instrument registry unreadable (%s): %s is read as itself", type(exc).__name__, symbol)
        return symbol
    spec = reg.resolve(symbol, broker) or reg.resolve(symbol)
    return spec.key if spec is not None else symbol


def label_candidates(db: Database, bars_1m: pd.DataFrame, costs: ReplayCosts, *, cutoff: pd.Timestamp,
                     account_id: str | None = None, symbol: str = GOLD,
                     instruments: Mapping[str, str] | None = None) -> int:
    """Replay every unlabelled, unquarantined candidate of `costs.broker` (and `account_id` when given) whose label
    resolves by `cutoff` on the bars before it, and write label_sim_r / label_mid_r once. Returns the number labelled.
    The cost table must be effective by `cutoff`; another broker's rows are never labelled with it.

    `bars_1m` are `symbol`'s (a key or a broker symbol: `instrument_key`, so Vantage's XAUUSD+ and XAUUSD247 are
    gold; quant review of 75824e5c); only candidates of an arm on that instrument are replayed (`instruments`: arm id ->
    instrument, default the spec's `arms.arm_instruments`; an arm it does not name is never replayed). `costs` is gold's per-oz
    model, so nothing is labelled for another symbol: brain 2026-11-04, the multi-instrument controls (S0-NZDUSD stop
    0.00019, S0-SUGAR 0.00026) were replayed on the account's XAUUSD bars with gold's costs, mean -2,679 R and
    -1,882 R. Another instrument's candidate stays NULL (unresolved, never censored) until its own replay exists."""
    if costs.effective_utc > cutoff:
        raise ValueError("cost model effective after the labelling cutoff: labels must be point in time")
    key = instrument_key(symbol, costs.broker)
    if bars_1m.empty or key != GOLD:
        return 0
    bars = bars_1m[pd.to_datetime(bars_1m["ts_utc"], utc=True) < cutoff]
    obs = read_obs(db, broker=costs.broker, instrument=key, instruments=instruments)
    obs = obs[obs["label_sim_r"].isna()]
    if account_id is not None:
        obs = obs[obs["account_id"] == account_id]
    rows = []
    for r in obs.to_dict("records"):
        delay = arm_spec.send_delay_s(str(r["arm_id"]), str(r["tf"]), pd.Timestamp(r["decision_utc"]))
        rep = replay_label(bars, side=int(r["side"]), decision_utc=r["decision_utc"], stop_dist=float(r["stop_dist"]),
                           target_dist=float(r["target_dist"]), max_hold_s=int(r["max_hold_s"]), costs=costs,
                           entry_delay_s=delay)
        if rep is None or rep.exit_utc > cutoff:
            continue                                        # unresolved: stays NULL, never censored
        rows.append((_ns(rep.exit_utc), rep.sim_r, rep.mid_r, rep.barrier, costs.version, _ns(costs.effective_utc),
                     now_ns(), r["candidate_id"]))
    done = 0
    if rows:
        with db.write() as conn:
            for row in rows:
                done += conn.execute(
                    "UPDATE exploration_obs SET label_exit_ns = ?, label_sim_r = ?, label_mid_r = ?, label_barrier = ?, "
                    "cost_model_version = ?, cost_effective_ns = ?, labelled_ns = ? "
                    "WHERE candidate_id = ? AND label_sim_r IS NULL", row).rowcount
    return done


# ---------------------------------------------------------------------------------------------- replay v2 (EVAL-2)
# EVAL-2 Lane 5A (docs/research/eval2-method-2026-11-09.md section 5; prereg-2026-11-09-eval2.md section 5 and its
# amendment of 2026-11-09). The S5 decomposition found the +0.084 R fill-vs-replay bias is the r1 replay's 2 x $0.15
# slippage PRIOR (+0.078 R), not a path error. r2 replaces it: "Per broker, per leg (entry, stop exit, target exit, time
# exit), by session: charge max(0, adverse upper 95 % bound of the measured slippage mean) once the cell has >=
# min_fills, else the $0.15 prior. Favourable slippage is never credited." "Stop exit price = level - side x measured
# stop overshoot (its own term, not double-charged with the stop leg)." "1m/5m deadline = decision + delay +
# max_hold." Amendment: "Each r2 label is costed only from ledger fills before its own session day." r2 lives in the
# side table `exploration_replay`; label_sim_r (r1) is never overwritten. It covers the candidates decided within the
# labeller's window (jobs.EXPLORATION_LABEL_DAYS); older ones keep r1 only.
class ReplayCostsV2(FrozenRecord):
    """One broker's r2 cost model, point in time: `legs_before` is the exclusive as-of of the ledger fills it rests on
    and `effective_utc` (its newest fill) is always before it. `charges`: $/oz per "<session>:<leg>"
    (`execution.costs.leg_slippage_table`; a missing cell pays `prior_usd`); commission and swap as r1."""
    broker: str = Field(min_length=1)
    version: str
    effective_utc: UtcTimestamp
    legs_before: UtcTimestamp | None = None
    charges: dict[str, float] = Field(default_factory=dict)
    fills: dict[str, int] = Field(default_factory=dict)        # the fills each cell rests on (audit)
    prior_usd: float = Field(SLIPPAGE_FLOOR_USD, ge=0)
    commission_rt_per_oz: float = Field(0.0, ge=0)
    swap: SwapSpec | None = None
    entry_at_quote_share: float | None = None                  # quant C7: entries filled within $0.005 of the quote

    @model_validator(mode="after")
    def _no_credit(self) -> ReplayCostsV2:
        if any(not (math.isfinite(v) and v >= 0) for v in self.charges.values()):
            raise ValueError("a leg charge is a finite cost >= 0: favourable slippage is never credited")
        return self

    def charge(self, leg: str, session: str) -> float:
        return float(self.charges.get(f"{session}:{leg}", self.prior_usd))


def cost_model_hash(costs: ReplayCostsV2) -> str:
    """sha256 of the model's canonical JSON: what a card records at forward start (quant C5)."""
    return hashlib.sha256(json.dumps(costs.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()


CostOfDay = Callable[[pd.Timestamp], ReplayCostsV2]      # session-day start -> the r2 model a label of that day uses


class ReplayV2(FrozenRecord):
    exit_utc: UtcTimestamp
    sim_r: float
    mid_r: float
    barrier: Literal["stop", "target", "time"]
    entry_price: float                  # the replay's entry (ask for a long, bid for a short), before any charge
    exit_price: float                   # a stop's includes the overshoot
    entry_charge_usd: float
    exit_charge_usd: float              # 0 on a stop: its charge is the overshoot in `exit_price`, never twice


def session_start(day: str) -> pd.Timestamp:
    """The UTC start of session day `day` (`session_day`): 17:00 New York the evening before."""
    eve = (pd.Timestamp(day) - pd.Timedelta(days=1)).replace(hour=17, minute=0, second=0)
    return pd.Timestamp(eve.tz_localize(NEW_YORK).tz_convert("UTC"))


def replay_label_v2(bars_1m: pd.DataFrame, *, side: int, decision_utc: pd.Timestamp, stop_dist: float,
                    target_dist: float, max_hold_s: int, tf: str, costs: ReplayCostsV2, entry_delay_s: float = 0.0,
                    sessions: SessionTable = DEFAULT_SESSIONS) -> ReplayV2 | None:
    """r2 label of one candidate on 1m bid/ask bars: r1's path (entry at the open of the minute holding decision +
    `entry_delay_s`, barriers minute by minute, the stop first), with a 1m/5m time limit from decision + delay, the
    measured entry leg of the entry minute's session, the stop's overshoot in its fill price, and the target or time
    leg of the exit minute's session. The gross `mid_r` is r1's (no cost). None when unresolved, and when the bars
    have no entry minute (never a later minute's open)."""
    b = bars_1m.sort_values("ts_utc").reset_index(drop=True)
    t = pd.DatetimeIndex(pd.to_datetime(b["ts_utc"], utc=True)).as_unit("ns")
    decision = pd.Timestamp(decision_utc)
    delay = pd.Timedelta(seconds=float(entry_delay_s))
    deadline = decision + (delay if tf in SEND_CLOCK_TFS else pd.Timedelta(0)) + pd.Timedelta(seconds=int(max_hold_s))
    sent = (decision + delay).floor("min")
    k = int(t.searchsorted(sent, side="left"))
    if k >= len(t) or t[k] != sent or t[k] >= deadline:
        return None
    ent, ex = ("ask", "bid") if side > 0 else ("bid", "ask")
    entry = float(b[f"{ent}_open"].iloc[k])
    path = (b[f"{ex}_open"].to_numpy(float), b[f"{ex}_high"].to_numpy(float), b[f"{ex}_low"].to_numpy(float))
    net = _walk(t, k, deadline, side, entry, stop_dist, target_dist, *path)
    mids = {c: ((b[f"bid_{c}"] + b[f"ask_{c}"]) / 2).to_numpy(float) for c in ("open", "high", "low")}
    gross = _walk(t, k, deadline, side, float(mids["open"][k]), stop_dist, target_dist, mids["open"], mids["high"],
                  mids["low"])
    if net is None or gross is None:
        return None
    exit_t, exit_p, barrier = net
    minute = exit_t - pd.Timedelta(minutes=1) if barrier != "time" else exit_t     # the minute the exit filled in
    s_entry, s_exit = (str(s) for s in sessions.session_label(pd.DatetimeIndex([t[k], minute])))
    entry_charge = costs.charge("entry", s_entry)
    exit_charge = 0.0
    if barrier == "stop":                       # the overshoot moves the fill, never the exit minute
        again = _walk(t, k, deadline, side, entry, stop_dist, target_dist, *path,
                      stop_overshoot=costs.charge("stop", s_exit))
        if again is None or again[0] != exit_t:
            raise AssertionError("the stop overshoot moved the exit minute")
        exit_p = again[1]
    else:
        exit_charge = costs.charge(barrier, s_exit)
    swap_paid = 0.0
    if costs.swap is not None:
        nights = int(rollover_nights(pd.DatetimeIndex([t[k]]), pd.DatetimeIndex([exit_t]), costs.swap.server_tz,
                                     costs.swap.triple_weekday, costs.swap.seven_night)[0])
        swap_paid = max(0.0, -float(costs.swap.usd_per_oz(np.array([side]))[0])) * nights
    sim = (side * (exit_p - entry) - entry_charge - exit_charge - costs.commission_rt_per_oz - swap_paid) / stop_dist
    mid = side * (gross[1] - float(mids["open"][k])) / stop_dist
    return ReplayV2(exit_utc=exit_t, sim_r=float(sim), mid_r=float(mid), barrier=barrier,  # type: ignore[arg-type]
                    entry_price=entry, exit_price=float(exit_p), entry_charge_usd=entry_charge,
                    exit_charge_usd=exit_charge)


def ledger_legs(db: Database, *, broker_of: Mapping[str, str], as_of: pd.Timestamp
                ) -> tuple[pd.DataFrame, dict[str, int]]:
    """The exploration ledger's gold fills as slippage legs for `execution.costs.leg_slippage_table`, point in time
    (sent, or for an exit closed, by `as_of`). Entry: the send quote vs the fill, the position's side. Stop/target
    exit: the bracket level (anchored on the send quote: quote -/+ the gate's stop or target distance) vs the exit
    deal, the closing order's side; `level` keeps the bracket level and `requested` starts equal to it (a stop's is
    moved to the bar-implied price by `bar_implied_stop_legs`). A time close has no recorded quote (its leg is always
    the prior). Left out and counted (quant vantage ruling 2026-11-09 (c): "Rows with `exit_price` None are excluded
    and counted. They are never imputed at the level"): `no_exit_price`, `censored` (the stop moved or something else
    closed it: the level is not the bracket's), `pyramid_add` exits, `non_gold`, `unknown_account`, `evidence_void`
    (void for the cost cells)."""
    cut = _ns(as_of)
    counts = dict.fromkeys(("no_exit_price", "censored", "pyramid_add", "non_gold", "unknown_account",
                            "evidence_void"), 0)
    with db.connection() as conn:
        rows = conn.execute("SELECT client_order_id, account_id, body FROM exploration_trades WHERE ts_ns <= ? "
                            "ORDER BY ts_ns, client_order_id", (cut,)).fetchall()
        void = excluded(conn, "cost_cells")
    keys: dict[tuple[str, str], str] = {}
    out: list[tuple[Any, ...]] = []
    for cid, acct, body in rows:
        broker = broker_of.get(str(acct))
        if broker is None:
            counts["unknown_account"] += 1
            continue
        if cid in void:
            counts["evidence_void"] += 1
            continue
        r = json.loads(body)
        sym = str(r.get("symbol") or GOLD)
        if (key := keys.get((sym, broker))) is None:
            key = keys[(sym, broker)] = instrument_key(sym, broker)
        if key != GOLD:
            counts["non_gold"] += 1
            continue
        side, q, fill = int(r["side"]), r.get("requested_price"), r.get("fill_price")
        if q is None or fill is None:
            continue
        out.append((cid, acct, broker, "entry", pd.Timestamp(r["sent_utc"]), side, float(q), float(q), float(fill)))
        leg = EXIT_REASON_LEG.get(str(r.get("exit_reason")))
        if r.get("exit_utc") is None or leg is None or leg == "time" or _ns(pd.Timestamp(r["exit_utc"])) > cut:
            continue
        if r.get("exit_price") is None:
            counts["no_exit_price"] += 1
        elif r.get("censored"):
            counts["censored"] += 1
        elif r.get("group_id") not in (None, cid):
            counts["pyramid_add"] += 1
        else:
            dist = float(r["stop_usd"] if leg == "stop" else r["target_usd"])
            level = float(q) + (-side if leg == "stop" else side) * dist
            out.append((cid, acct, broker, leg, pd.Timestamp(r["exit_utc"]), -side, level, level,
                        float(r["exit_price"])))
    legs = pd.DataFrame(out, columns=["client_order_id", "account_id", "broker", "leg", "ts_utc", "side", "level",
                                      "requested", "filled"])
    legs["ts_utc"] = pd.to_datetime(legs["ts_utc"], utc=True)
    return legs, counts


def bar_implied_stop_legs(legs: pd.DataFrame, bars_by_account: Mapping[str, pd.DataFrame]
                          ) -> tuple[pd.DataFrame, int]:
    """Quant C6 (review of 8b2345c4): the stop overshoot is measured against the bar-implied price, not the level: a
    sell close against min(level, the bid open of its exit minute), a buy close against max(level, the ask open), so a
    gap the bars already show (and the replay's path already pays) is not charged again. A stop leg whose account has
    no bar at its exit minute is left out; returns the legs and how many were left out."""
    if legs.empty or not (legs["leg"] == "stop").any():
        return legs, 0
    out = legs.reset_index(drop=True).copy()
    keep = np.ones(len(out), dtype=bool)
    stop = out["leg"].to_numpy() == "stop"
    for acct in sorted(set(out.loc[stop, "account_id"].astype(str))):
        rows = np.flatnonzero(stop & (out["account_id"].astype(str).to_numpy() == acct))
        bars = bars_by_account.get(acct)
        if bars is None or bars.empty:
            keep[rows] = False
            continue
        t = pd.DatetimeIndex(pd.to_datetime(bars["ts_utc"], utc=True)).as_unit("ns")
        minute = pd.DatetimeIndex(pd.to_datetime(out["ts_utc"].to_numpy()[rows], utc=True)).floor("min").as_unit("ns")
        pos = t.get_indexer(minute)
        found = pos >= 0
        keep[rows[~found]] = False
        r, p = rows[found], pos[found]
        level = out["level"].to_numpy(dtype=float)[r]
        sell = out["side"].to_numpy(dtype=float)[r] < 0
        implied = np.where(sell, np.minimum(level, bars["bid_open"].to_numpy(dtype=float)[p]),
                           np.maximum(level, bars["ask_open"].to_numpy(dtype=float)[p]))
        out.loc[r, "requested"] = implied
    return out[keep].reset_index(drop=True), int((~keep).sum())


def replay_costs_v2(base: ReplayCosts, legs: pd.DataFrame, *, before: pd.Timestamp, min_fills: int,
                    prior_usd: float = SLIPPAGE_FLOOR_USD) -> ReplayCostsV2:
    """`base.broker`'s r2 cost model from its own ledger legs strictly before `before` (`ledger_legs`, stops through
    `bar_implied_stop_legs`), commission and swap from its r1 model. Never another broker's measurements, with the
    exceptions the quant ruled (2026-11-09 and C7): a broker whose demo server fills stops and targets at the level
    (`costs.DEMO_EXIT_AT_LEVEL`) has no measurable exit leg, so each exit cell charges "the larger of the ICM-measured
    exit slip per leg and session and the stop-slip prior"; when 95 % or more of its entries also fill within $0.005 of
    the quote, its entry cells are treated the same way."""
    from goldbot.execution.costs import (
        DEMO_EXIT_AT_LEVEL,
        ENTRY_AT_QUOTE_SHARE,
        EXIT_LEGS,
        entry_at_quote_share,
        leg_slippage_table,
        real_money_leg_charge,
    )
    upto = legs[pd.to_datetime(legs["ts_utc"], utc=True) < before] if not legs.empty else legs
    own = upto[upto["broker"] == base.broker] if not upto.empty else upto
    table = leg_slippage_table(own, prior_usd=prior_usd, min_fills=min_fills)
    charges = {k: v.charged for k, v in table.items()}
    share = entry_at_quote_share(own)
    if base.broker in DEMO_EXIT_AT_LEVEL:
        icm = leg_slippage_table(upto[upto["broker"] == "icm"] if not upto.empty else upto, prior_usd=prior_usd,
                                 min_fills=min_fills)
        synthetic_entry = share is not None and share >= ENTRY_AT_QUOTE_SHARE
        charges.update({k: real_money_leg_charge(icm, k, prior_usd=prior_usd) for k in charges
                        if k.rsplit(":", 1)[1] in EXIT_LEGS or (synthetic_entry and k.endswith(":entry"))})
    last = pd.Timestamp(pd.to_datetime(upto["ts_utc"], utc=True).max()) if not upto.empty else None
    return ReplayCostsV2(broker=base.broker, version=f"{base.version}+legs-{REPLAY_V2}:<{before:%Y%m%dT%H%M%S}",
                         effective_utc=last if last is not None else before - pd.Timedelta(1, "ns"),
                         legs_before=before, charges=charges, fills={k: v.n for k, v in table.items()},
                         prior_usd=prior_usd, commission_rt_per_oz=base.commission_rt_per_oz, swap=base.swap,
                         entry_at_quote_share=share)


def daily_costs_v2(base: ReplayCosts, legs: pd.DataFrame, *, min_fills: int,
                   prior_usd: float = SLIPPAGE_FLOOR_USD) -> CostOfDay:
    """Quant C1: one cached r2 model per session day, from the ledger fills before that session day's start."""
    cache: dict[pd.Timestamp, ReplayCostsV2] = {}

    def of_day(start: pd.Timestamp) -> ReplayCostsV2:
        if start not in cache:
            cache[start] = replay_costs_v2(base, legs, before=start, min_fills=min_fills, prior_usd=prior_usd)
        return cache[start]
    return of_day


def pinned_costs(costs: ReplayCostsV2, costs_hash: str) -> CostOfDay:
    """Quant C5: the forward replay uses the r2 model whose hash the card recorded at forward start, whatever the
    nightly refit says later. A model that does not match the recorded hash is refused."""
    if cost_model_hash(costs) != costs_hash:
        raise ValueError("the r2 cost model does not match the hash recorded in the card")
    return lambda _start: costs


_REPLAY_ROW_COLS = ("candidate_id", "arm_id", "tf", "side", "decision_utc", "session_day", "stop_dist", "target_dist",
                    "max_hold_s")


def replay_frame_v2(bars_1m: pd.DataFrame, rows: pd.DataFrame, cost_of_day: CostOfDay, *,
                    cutoff: pd.Timestamp) -> pd.DataFrame:
    """r2 labels of `rows` (read_obs columns) on the bars before `cutoff`, each with the model of its session day
    (`cost_of_day`), whose fills must all precede that day's start (point in time; ValueError otherwise). A
    candidate decided before the first bar, or whose label does not resolve by `cutoff`, gets no row."""
    cols = ["candidate_id", "label_exit_ns", "sim_r", "mid_r", "barrier", "entry_price", "exit_price",
            "entry_charge_usd", "exit_charge_usd", "cost_model_version", "cost_effective_ns"]
    bars = bars_1m[pd.to_datetime(bars_1m["ts_utc"], utc=True) < cutoff] if not bars_1m.empty else bars_1m
    if bars.empty or rows.empty:
        return pd.DataFrame(columns=cols)
    first = pd.Timestamp(pd.to_datetime(bars["ts_utc"], utc=True).min())
    out = []
    for r in rows[list(_REPLAY_ROW_COLS)].to_dict("records"):
        decision = pd.Timestamp(r["decision_utc"])
        if decision < first:
            continue                                        # its entry minute is not in these bars
        start = session_start(str(r["session_day"]))
        costs = cost_of_day(start)
        if not costs.effective_utc < start:
            raise ValueError(f"r2 cost model {costs.version} effective at {costs.effective_utc}, not before the "
                             f"session day of {r['candidate_id']} ({start}): labels must be point in time")
        delay = arm_spec.send_delay_s(str(r["arm_id"]), str(r["tf"]), decision)
        rep = replay_label_v2(bars, side=int(r["side"]), decision_utc=decision, stop_dist=float(r["stop_dist"]),
                              target_dist=float(r["target_dist"]), max_hold_s=int(r["max_hold_s"]), tf=str(r["tf"]),
                              costs=costs, entry_delay_s=delay)
        if rep is None or rep.exit_utc > cutoff:
            continue                                        # unresolved: no row, never censored
        out.append((r["candidate_id"], _ns(rep.exit_utc), rep.sim_r, rep.mid_r, rep.barrier, rep.entry_price,
                    rep.exit_price, rep.entry_charge_usd, rep.exit_charge_usd, costs.version,
                    _ns(costs.effective_utc)))
    return pd.DataFrame(out, columns=cols)


def entry_bar_missing(bars_1m: pd.DataFrame, rows: pd.DataFrame) -> int:
    """How many of `rows` (read_obs columns) decided inside the bars' span have no bar at their entry minute (decision
    + the arm's send delay, floored): unresolved by r1 and r2 alike, never entered at a later minute's open (quant
    re-check N4: reported nightly)."""
    if bars_1m.empty or rows.empty:
        return 0
    t = pd.DatetimeIndex(pd.to_datetime(bars_1m["ts_utc"], utc=True)).as_unit("ns")
    have = set(t)
    lo, hi = t.min(), t.max()
    n = 0
    for r in rows[["arm_id", "tf", "decision_utc"]].to_dict("records"):
        decision = pd.Timestamp(r["decision_utc"])
        sent = (decision + pd.Timedelta(seconds=arm_spec.send_delay_s(str(r["arm_id"]), str(r["tf"]), decision))
                ).floor("min")
        n += int(lo <= decision and sent <= hi and sent not in have)
    return n


def void_ids(db: Database, replay_version: ReplayVersion = REPLAY_V1) -> set[str]:
    """The candidates whose `replay_version` label is void."""
    with db.connection() as conn:
        return {str(c) for (c,) in conn.execute("SELECT candidate_id FROM exploration_label_void "
                                                "WHERE replay_version = ?", (replay_version,))}


def label_candidates_v2(db: Database, bars_1m: pd.DataFrame, cost_of_day: CostOfDay, *, broker: str,
                        cutoff: pd.Timestamp, account_id: str | None = None, symbol: str = GOLD,
                        instruments: Mapping[str, str] | None = None) -> int:
    """r2 for every unquarantined gold candidate of `broker` (and `account_id`) with no r2 row, whose label resolves
    by `cutoff` on the bars before it (`replay_frame_v2`: each with its session day's model, `daily_costs_v2`): one
    `exploration_replay` row each, written once. Gold only, as r1 since 2026-11-04 (`label_candidates`); a candidate
    whose r1 label is void is never replayed (its ledger traded another instrument). Returns the rows written."""
    if bars_1m.empty or instrument_key(symbol, broker) != GOLD:
        return 0
    obs = read_obs(db, broker=broker, instrument=GOLD, instruments=instruments, replay_version=REPLAY_V2)
    obs = obs[obs["label_sim_r"].isna() & ~obs["candidate_id"].isin(void_ids(db))]
    if account_id is not None:
        obs = obs[obs["account_id"] == account_id]
    f = replay_frame_v2(bars_1m, obs, cost_of_day, cutoff=cutoff)
    if f.empty:
        return 0
    rows = [(r["candidate_id"], REPLAY_V2, int(r["label_exit_ns"]), float(r["sim_r"]), float(r["mid_r"]),
             str(r["barrier"]), float(r["entry_price"]), float(r["exit_price"]), float(r["entry_charge_usd"]),
             float(r["exit_charge_usd"]), str(r["cost_model_version"]), int(r["cost_effective_ns"]), now_ns())
            for r in f.to_dict("records")]
    with db.write() as conn:
        return sum(conn.execute("INSERT OR IGNORE INTO exploration_replay (candidate_id, replay_version, label_exit_ns, "
                                "sim_r, mid_r, barrier, entry_price, exit_price, entry_charge_usd, exit_charge_usd, "
                                "cost_model_version, cost_effective_ns, labelled_ns) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row).rowcount for row in rows)


def void_non_gold_labels(db: Database, *, instruments: Mapping[str, str] | None = None,
                         now: pd.Timestamp | None = None) -> dict[str, int]:
    """Prereg-2026-11-09-eval2.md section 6.5: "241 non-gold paired rows in `exploration_obs` carry a `label_sim_r`
    replayed on gold bars with gold costs (written before the 2026-11-04 instrument guard). They and every other
    non-gold v1 label are void". Writes one `exploration_label_void` row (r1) per labelled candidate whose arm the spec
    puts on another instrument, or whose ledger order traded another symbol; append-only and idempotent. The stored
    label is never rewritten; `read_obs` drops it for every reader. An arm the spec does not name is not voided (no
    evidence either way; every learner already reads it as not gold) and is counted. Returns the new voids, of them
    the paired (a fill label too) and those beyond MAX_LABEL_R, and the labels on unknown arms."""
    inst = arm_spec.arm_instruments() if instruments is None else instruments
    with db.connection() as conn:
        rows = conn.execute("SELECT candidate_id, arm_id, broker, label_sim_r, label_fill_r FROM exploration_obs "
                            "WHERE label_sim_r IS NOT NULL").fetchall()
        traded = {str(c): str(s) for c, s in conn.execute(
            "SELECT client_order_id, json_extract(body, '$.symbol') FROM exploration_trades "
            "WHERE json_valid(body) AND json_extract(body, '$.symbol') IS NOT NULL")}
    done = void_ids(db)
    keys: dict[tuple[str, str | None], str] = {}
    new: list[tuple[str, float, float | None]] = []
    unknown = 0
    for cid, arm, broker, sim, fill in rows:
        on = inst.get(str(arm))
        if on is None:
            unknown += 1
        sym = traded.get(str(cid))
        if sym is not None and (sym, broker) not in keys:
            keys[(sym, broker)] = instrument_key(sym, broker)
        if (on is not None and on != GOLD) or (sym is not None and keys[(sym, broker)] != GOLD):
            if str(cid) not in done:
                new.append((str(cid), float(sim), fill))
    decided = _ns(now) if now is not None else now_ns()
    written: list[tuple[str, float, float | None]] = []
    if new:
        with db.write() as conn:
            for cid, sim, fill in new:
                if conn.execute("INSERT OR IGNORE INTO exploration_label_void VALUES (?, ?, ?, ?)",
                                (cid, REPLAY_V1, VOID_NON_GOLD_REASON, decided)).rowcount:
                    written.append((cid, sim, fill))
    if written:
        log.warning("exploration: %d non-gold r1 labels voided (replayed on gold bars before 2026-11-04)", len(written))
    return {"voided": len(written), "paired": sum(f is not None for _, _, f in written),
            "beyond_20r": sum(abs(s) > MAX_LABEL_R for _, s, _ in written), "unknown_arm_labels": unknown}


def fill_replay_pairs(db: Database, *, broker: str, replay_version: ReplayVersion = REPLAY_V2,
                      instruments: Mapping[str, str] | None = None) -> pd.DataFrame:
    """S5's paired rows (method section 6: "fill vs replay v2 ..., per broker and timeframe band, gold only"): every
    gold candidate of `broker` with both a fill label and a `replay_version` label, not void for the cost cells.
    `fill_r` = label_fill_r, `replay_r` = the version's label; `entry_fill_minus_replay_r` = the entry leg alone, in R,
    + = the fill better than the replay's charged entry (r2 only: r1 stored no entry price); `exit_kind` = the fill's
    own exit (stop/target/time, else other), for the bias by exit kind (quant C10). `attrs["unknown_arm_labels"]`:
    the broker's paired rows left out because the spec names no instrument for their arm. One row per candidate."""
    inst = arm_spec.arm_instruments() if instruments is None else instruments
    every = read_obs(db, broker=broker, replay_version=replay_version, evidence="cost_cells")
    paired = every["label_fill_r"].notna() & every["label_sim_r"].notna()
    unknown = int((paired & every["arm_id"].map(lambda a: inst.get(str(a))).isna()).sum())
    obs = read_obs(db, broker=broker, instrument=GOLD, instruments=inst, replay_version=replay_version,
                   evidence="cost_cells")
    obs = obs[obs["label_fill_r"].notna() & obs["label_sim_r"].notna()].reset_index(drop=True)
    entry = pd.DataFrame({"candidate_id": pd.Series(dtype=object), "entry_price_replay": pd.Series(dtype=float),
                          "entry_charge_usd": pd.Series(dtype=float)})
    reasons: dict[str, str] = {}
    if not obs.empty:
        with db.connection() as conn:
            if replay_version == REPLAY_V2:
                entry = pd.read_sql_query("SELECT candidate_id, entry_price AS entry_price_replay, entry_charge_usd "
                                          "FROM exploration_replay WHERE replay_version = ?", conn,
                                          params=(replay_version,))
            reasons = {str(c): str(x) for c, x in conn.execute(
                "SELECT client_order_id, json_extract(body, '$.exit_reason') FROM exploration_trades "
                "WHERE json_valid(body) AND json_extract(body, '$.exit_reason') IS NOT NULL")}
    p = obs.merge(entry, on="candidate_id", how="left")
    leg = (p["side"].astype(float) * (p["entry_price_replay"].astype(float) - p["entry_price"].astype(float))
           + p["entry_charge_usd"].astype(float)) / p["stop_dist"].astype(float)
    kind = p["candidate_id"].map(lambda c: EXIT_REASON_LEG.get(reasons.get(str(c), ""), "other"))
    out = pd.DataFrame({"candidate_id": p["candidate_id"], "broker": p["broker"], "tf": p["tf"],
                        "session_day": p["session_day"], "side": p["side"], "stop_dist": p["stop_dist"],
                        "fill_r": p["label_fill_r"], "replay_r": p["label_sim_r"],
                        "entry_fill_minus_replay_r": leg, "exit_kind": kind})
    out.attrs["unknown_arm_labels"] = unknown
    return out


def synthetic_stop_fills(legs: pd.DataFrame, bars_1m: pd.DataFrame, *, tol_usd: float = 0.005) -> int:
    """Quant vantage ruling 2026-11-09 (b): "List any stop that a bar gapped through and that still filled at the
    level. That is direct proof of a synthetic fill; include the count." A stop leg (`ledger_legs`) whose exit minute
    opened beyond its bracket `level` on the closing side (bid for a sell, ask for a buy) and whose deal is within
    `tol_usd` of the level."""
    stops = legs[legs["leg"] == "stop"] if not legs.empty else legs
    if stops.empty or bars_1m.empty:
        return 0
    t = pd.DatetimeIndex(pd.to_datetime(bars_1m["ts_utc"], utc=True)).as_unit("ns")
    minute = pd.DatetimeIndex(pd.to_datetime(stops["ts_utc"], utc=True)).floor("min").as_unit("ns")
    pos = t.get_indexer(minute)
    seen = pos >= 0
    side = stops["side"].to_numpy(dtype=float)[seen]
    level = stops["level"].to_numpy(dtype=float)[seen]
    filled = stops["filled"].to_numpy(dtype=float)[seen]
    sell = side < 0
    o = np.where(sell, bars_1m["bid_open"].to_numpy(dtype=float)[pos[seen]],
                 bars_1m["ask_open"].to_numpy(dtype=float)[pos[seen]])
    gapped = np.where(sell, o < level, o > level)
    return int(np.sum(gapped & (np.abs(filled - level) <= tol_usd)))


# ---------------------------------------------------------------------------------------------- training table
_BASE = ["candidate_id", "broker", "arm_id", "family", "style", "tf", "side", "is_control", "decision_utc", "session_day",
         "max_hold_s", "taken", "branch", "mu", "meta_p", "model_version", "label_exit_utc", "label_sim_r",
         "label_mid_r", "label_fill_r", "cost_model_version", "feature_version", "feature_list_id",
         "available_utc_max"]


def training_table(db: Database, *, style: str, broker: str, as_of: pd.Timestamp,
                   features: pd.DataFrame | None = None, arm_id: str | None = None,
                   feature_cols: Sequence[str] | None = None, held_out: Collection[str] = (),
                   instruments: Mapping[str, str] | None = None) -> pd.DataFrame:
    """One broker's admissible rows of a style (spec section 2; never pooled across brokers): label resolved by `as_of`, not quarantined, no decision in the
    holdout, taken or not. `y` = 1[label_sim_r > 0]; label_fill_r and label_mid_r ride along as diagnostics. The
    columns are the style's frozen v1 list (or `feature_cols`), from each row's snapshot when it has one, else from
    `features` (history and tests) through `asof_join` on available_utc <= decision_utc. The row invariant
    available_utc_max <= decision_utc fails the build (ValueError)."""
    if style not in STYLES:
        raise ValueError(f"style {style!r} not one of {STYLES}")
    cols = list(feature_cols) if feature_cols is not None else list(FEATURE_LISTS[STYLE_FEATURE_LIST[style]])
    check_feature_cap([*cols, "side", "arm_id"])
    obs = read_obs(db, broker=broker, style=style, arm_id=arm_id, as_of=as_of, evidence="posterior",
                   held_out=held_out, instrument=GOLD, instruments=instruments, replay_version=REPLAY_V1)
    dec = pd.DatetimeIndex(obs["decision_utc"])
    obs = obs[~((dec >= HOLDOUT_START) & (dec < HOLDOUT_END))].reset_index(drop=True)      # L-10
    if n_sus := int(suspect(obs["label_sim_r"]).sum()):          # kept: logged, never dropped
        log.warning("exploration %s %s training rows: %d labels beyond %.0f R (suspect, kept)", broker, style, n_sus,
                    MAX_LABEL_R)
    if obs.empty:
        return pd.DataFrame(columns=[*_BASE, "y", *cols])
    snap = obs["features"].notna()
    parts = []
    if snap.any():
        s = obs[snap].reset_index(drop=True)
        vals = pd.DataFrame([json.loads(v) for v in s["features"]]).reindex(columns=cols).astype(float)
        parts.append(pd.concat([s[_BASE], vals], axis=1))
    if (~snap).any():
        s = obs[~snap].reset_index(drop=True)
        if features is None:
            parts.append(pd.concat([s[_BASE], pd.DataFrame(np.nan, index=s.index, columns=cols)], axis=1))
        else:
            right = features[["available_utc", *cols]].copy()
            right["_avail"] = pd.DatetimeIndex(pd.to_datetime(right["available_utc"], utc=True)).as_unit("ns")
            j = asof_join(s[_BASE].drop(columns=["available_utc_max"]), right, on="decision_utc")
            j["available_utc_max"] = j.pop("_avail")
            parts.append(j[[*_BASE, *cols]])
    out = pd.concat(parts, ignore_index=True)
    am = pd.to_datetime(out["available_utc_max"], utc=True)
    bad = am.notna() & (am > pd.to_datetime(out["decision_utc"], utc=True))
    if bad.any():
        raise ValueError(f"{int(bad.sum())} rows with available_utc_max after decision_utc (L-2)")
    out["y"] = (out["label_sim_r"] > 0).astype(int)
    return out[[*_BASE, "y", *cols]].sort_values(["decision_utc", "candidate_id"]).reset_index(drop=True)


# ---------------------------------------------------------------------------------------------- cross-fitting
def purged_folds(decision: pd.DatetimeIndex, label_exit: pd.DatetimeIndex, *, max_hold: pd.Timedelta,
                 embargo: pd.Timedelta, k: int = N_FOLDS) -> list[tuple[np.ndarray, np.ndarray]]:
    """k contiguous blocks of session days (all candidates of one session day in one fold). A training row whose
    [decision, label exit] touches the test block widened by `max_hold` on both sides is purged; one decided within
    `embargo` after the block's end is embargoed (spec section 3; L-5, L-14)."""
    dec, ext = pd.DatetimeIndex(decision), pd.DatetimeIndex(label_exit)
    days = session_day(dec)
    uniq = np.array(sorted(set(days)))
    out = []
    for block in np.array_split(uniq, min(k, len(uniq))):
        test = np.flatnonzero(np.isin(days, block))
        if len(test) == 0:
            continue
        lo, hi = dec[test].min() - max_hold, ext[test].max() + max_hold
        overlap = (ext >= lo) & (dec <= hi)
        embargoed = (dec > hi) & (dec <= ext[test].max() + embargo)
        keep = ~(overlap | embargoed)
        keep[test] = False
        out.append((np.flatnonzero(keep), test))
    return out


def _design(table: pd.DataFrame, cols: Sequence[str]) -> pd.DataFrame:
    X = table[[*cols, "side"]].astype(float).replace([np.inf, -np.inf], np.nan)
    X["arm_id"] = pd.Categorical(table["arm_id"].astype(str))
    return X


def _auc(y: np.ndarray, p: np.ndarray) -> float | None:
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score(y, p)) if len(set(y.tolist())) == 2 else None


def cross_fit_meta(table: pd.DataFrame, style: str, *, feature_cols: Sequence[str] | None = None,
                   k: int = N_FOLDS, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """H-62 for one style: fold models on purged, embargoed session-day blocks; out-of-fold raw scores; the calibrator
    on them (isotonic, Platt below 500); per arm the OOF AUC on the net label (y) and the gross label (label_mid_r > 0)
    and the Brier score. The served score is the calibrated mean of the fold models (spec section 4)."""
    from sklearn.metrics import brier_score_loss
    cols = list(feature_cols) if feature_cols is not None else list(FEATURE_LISTS[STYLE_FEATURE_LIST[style]])
    tab = table.sort_values("decision_utc").reset_index(drop=True)
    X, y = _design(tab, cols), tab["y"].to_numpy(dtype=int)
    names = [*cols, "side", "arm_id"]
    max_hold = pd.Timedelta(seconds=int(tab["max_hold_s"].max()))
    oof = np.full(len(tab), np.nan)
    models: list[MetaLabelModel] = []
    for train, test in purged_folds(pd.DatetimeIndex(tab["decision_utc"]), pd.DatetimeIndex(tab["label_exit_utc"]),
                                    max_hold=max_hold, embargo=pd.Timedelta(days=EMBARGO_DAYS[style]), k=k):
        if len(train) == 0 or len(set(y[train].tolist())) < 2:
            continue
        m = MetaLabelModel(feature_names=names, params=dict(params or H62_PARAMS), feature_version=FEATURE_VERSION_V1)
        m.fit(X.iloc[train], pd.Series(y[train]))
        oof[test] = m.predict_raw(X.iloc[test])
        models.append(m)
    ok = np.isfinite(oof)
    calibrator = fit_calibrator(oof[ok], y[ok]) if ok.sum() >= 10 and len(set(y[ok].tolist())) == 2 else None
    gross = (tab["label_mid_r"].to_numpy(dtype=float) > 0).astype(int)
    per_arm: dict[str, dict[str, Any]] = {}
    for arm, idx in tab.groupby("arm_id").indices.items():
        sc = idx[ok[idx]]
        per_arm[str(arm)] = {"n": int(len(idx)), "n_scored": int(len(sc)),
                             "auc_net": _auc(y[sc], oof[sc]) if len(sc) else None,
                             "auc_gross": _auc(gross[sc], oof[sc]) if len(sc) else None,
                             "brier": float(brier_score_loss(y[sc], oof[sc])) if len(sc) else None}
    return {"style": style, "n": int(len(tab)), "features": names, "folds": models, "calibrator": calibrator,
            "oof": oof, "per_arm": per_arm, "auc_net": _auc(y[ok], oof[ok]) if ok.any() else None,
            "auc_gross": _auc(gross[ok], oof[ok]) if ok.any() else None}


def model_cutoff(slot: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    """(T_m, A_m): the data cutoff, the last Friday 21:00 UTC at or before `slot`, and the activation, the first
    Sunday 22:00 UTC at or after `slot` (strictly after the cutoff)."""
    s = pd.Timestamp(slot).tz_convert("UTC")
    fri = (s - pd.Timedelta(days=(s.dayofweek - 4) % 7)).normalize() + pd.Timedelta(hours=21)
    cutoff = fri if fri <= s else fri - pd.Timedelta(days=7)
    sun = (s + pd.Timedelta(days=(6 - s.dayofweek) % 7)).normalize() + pd.Timedelta(hours=22)
    activation = sun if sun >= s else sun + pd.Timedelta(days=7)
    return cutoff, activation


def _last_cutoff(db: Database, style: str, broker: str) -> pd.Timestamp | None:
    with db.connection() as conn:
        r = conn.execute("SELECT MAX(cutoff_ns) FROM exploration_models WHERE style = ? AND broker = ?",
                         (style, broker)).fetchone()
    return _ts(r[0]) if r and r[0] is not None else None


def retrain_due(db: Database, style: str, cutoff: pd.Timestamp, *, broker: str,
                held_out: Collection[str] = (), instruments: Mapping[str, str] | None = None) -> tuple[bool, int, int]:
    """(due, new, total) of one broker's style: >= RETRAIN_MIN_NEW admissible labels resolved since its last fit's
    cutoff and >= RETRAIN_MIN_TOTAL in all (spec section 5); gold arms' rows only (`read_obs` `instrument`)."""
    obs = read_obs(db, broker=broker, style=style, as_of=cutoff, evidence="posterior", held_out=held_out,
                   instrument=GOLD, instruments=instruments, replay_version=REPLAY_V1)
    last = _last_cutoff(db, style, broker)
    new = int((obs["label_exit_utc"] > last).sum()) if last is not None else len(obs)
    return new >= RETRAIN_MIN_NEW and len(obs) >= RETRAIN_MIN_TOTAL, new, len(obs)


def save_model(db: Database, models_dir: Path, fit: Mapping[str, Any], *, broker: str, cutoff: pd.Timestamp,
               activation: pd.Timestamp) -> str:
    """Persist one broker's fitted style model (fold models + calibrator) as a pickle with its SHA-256, and register
    it as 'shadow': it never serves until a separate champion decision (spec section 5) marks it 'champion'."""
    if not BROKER_KEY.fullmatch(broker):
        raise ValueError(f"broker key {broker!r} is not a lowercase slug")
    style = str(fit["style"])
    version = f"h62-{broker}-{style}-{cutoff:%Y%m%dT%H%M}"
    path = Path(models_dir) / "h62" / broker / style / f"{version}.pkl"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = pickle.dumps({"folds": fit["folds"], "calibrator": fit["calibrator"], "features": fit["features"],
                         "feature_list_id": STYLE_FEATURE_LIST[style], "feature_version": FEATURE_VERSION_V1,
                         "cutoff": cutoff, "activation": activation})
    path.write_bytes(data)
    detail = {"per_arm": fit["per_arm"], "auc_net": fit["auc_net"], "auc_gross": fit["auc_gross"]}
    with db.write() as conn:
        conn.execute("INSERT OR IGNORE INTO exploration_models (model_version, broker, style, feature_list_id, "
                     "feature_version, cutoff_ns, activation_ns, path, sha256, n_rows, detail, recorded_ns) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (version, broker, style, STYLE_FEATURE_LIST[style], FEATURE_VERSION_V1, _ns(cutoff), _ns(activation),
                      str(path), hashlib.sha256(data).hexdigest(), int(fit["n"]), json.dumps(detail, default=str),
                      now_ns()))
    return version


def serving_model(db: Database, style: str, now: pd.Timestamp, *, broker: str) -> dict[str, Any] | None:
    """The H-62 model the engine may serve for `style` on `broker` at `now` (fitted on that broker's evidence only;
    a model without a broker key, i.e. fitted before migration 17, never serves: none was ever champion, and the next
    Sunday fit replaces it under its broker): the newest version with status 'champion' whose
    activation is at or before `now` and whose cutoff is before it (strictly forward), its file matching the recorded
    SHA-256. Every Sunday fit registers as 'shadow' (scored and logged, never acting); None = champion zero (the
    per-arm base rate): serve no meta_p."""
    try:
        with db.connection() as conn:
            r = conn.execute("SELECT model_version, path, sha256 FROM exploration_models WHERE style = ? AND "
                             "broker = ? AND status = 'champion' AND activation_ns <= ? AND cutoff_ns < ? "
                             "ORDER BY activation_ns DESC LIMIT 1", (style, broker, _ns(now), _ns(now))).fetchone()
        if r is None:
            return None
        data = Path(r[1]).read_bytes()
    except (sqlite3.Error, StateDbError, OSError) as exc:
        log.warning("H-62 %s model unreadable (%s): champion zero", style, type(exc).__name__)
        return None
    if hashlib.sha256(data).hexdigest() != r[2]:
        log.warning("H-62 %s model %s fails its SHA-256: champion zero", style, r[0])
        return None
    model: dict[str, Any] = pickle.loads(data)      # noqa: S301 - our own file, checked against its recorded digest
    return {"model_version": r[0], **model}


# ---------------------------------------------------------------------------------------------- priors (H-60)
class ArmPrior(FrozenRecord):
    """An arm's H-60 history prior on its mean net R per trade: mean x PRIOR_SHRINK, worth
    n_eff = min(PRIOR_N_MAX, n_hist / PRIOR_N_PER_TRADES) trades. Never added to the arm's live n."""
    arm_id: str
    prior_version: str
    mean_r: float
    sd_r: float = Field(gt=0)
    n_eff: float = Field(ge=0)
    n_hist: int = Field(ge=0)
    hist_start: UtcTimestamp
    hist_end: UtcTimestamp


def prior_from_history(arm_id: str, history: pd.DataFrame, *, prior_version: str) -> ArmPrior:
    """`history`: the arm's H-60 out-of-sample trades (`decision_utc`, `r_net`). Every row must lie in
    2019-01-01..2025-09-30: a holdout or later row, or one from the earlier cost era, is refused (ValueError)."""
    dec = pd.DatetimeIndex(pd.to_datetime(history["decision_utc"], utc=True))
    if len(dec) == 0:
        raise ValueError("an empty history gives no prior")
    if (dec >= HOLDOUT_START).any():
        raise ValueError("history reaches the holdout (2025-10-01 on): never read for a prior")
    if (dec < PRIOR_HISTORY_START).any():
        raise ValueError("history before 2019-01-01: the prior uses the current cost era only")
    r = history["r_net"].to_numpy(dtype=float)
    r = r[np.isfinite(r)]
    sd = float(np.std(r, ddof=1)) if len(r) >= 2 else PRIOR_SD_R
    return ArmPrior(arm_id=arm_id, prior_version=prior_version, mean_r=PRIOR_SHRINK * float(np.mean(r)),
                    sd_r=max(sd, 1e-6), n_eff=min(PRIOR_N_MAX, len(r) / PRIOR_N_PER_TRADES), n_hist=len(r),
                    hist_start=dec.min(), hist_end=dec.max())


def write_prior(db: Database, p: ArmPrior) -> bool:
    if p.hist_end >= HOLDOUT_START or p.hist_start < PRIOR_HISTORY_START:
        raise ValueError("a prior's history must lie in 2019-01-01..2025-09-30")
    with db.write() as conn:
        return conn.execute("INSERT OR IGNORE INTO exploration_priors (arm_id, prior_version, mean_r, sd_r, n_eff, "
                            "n_hist, hist_start_ns, hist_end_ns, recorded_ns) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (p.arm_id, p.prior_version, p.mean_r, p.sd_r, p.n_eff, p.n_hist, _ns(p.hist_start),
                             _ns(p.hist_end), now_ns())).rowcount == 1


def read_priors(db: Database) -> dict[str, ArmPrior]:
    """The newest prior per arm (by recording time)."""
    with db.connection() as conn:
        rows = conn.execute("SELECT arm_id, prior_version, mean_r, sd_r, n_eff, n_hist, hist_start_ns, hist_end_ns "
                            "FROM exploration_priors ORDER BY recorded_ns").fetchall()
    return {str(a): ArmPrior(arm_id=a, prior_version=v, mean_r=m, sd_r=sd, n_eff=n, n_hist=nh, hist_start=_ts(s),
                             hist_end=_ts(e)) for a, v, m, sd, n, nh, s, e in rows}


# ---------------------------------------------------------------------------------------------- the bandit (H-61)
def _nig_draws(r: np.ndarray, prior: ArmPrior | None, draws: int, rng: np.random.Generator) -> np.ndarray:
    """Draws of the mean net R from its Normal-Inverse-Gamma posterior (prior mean m0 worth k0 trades; a0 = 1 + k0/2,
    b0 = sd0^2 (a0 - 1), so the prior's expected variance is sd0^2)."""
    m0, k0, sd0 = (prior.mean_r, max(prior.n_eff, 0.01), prior.sd_r) if prior is not None else (0.0, 1.0, PRIOR_SD_R)
    a0 = 1.0 + k0 / 2
    b0 = sd0 ** 2 * (a0 - 1.0)
    n = len(r)
    xbar = float(r.mean()) if n else 0.0
    ss = float(((r - xbar) ** 2).sum()) if n else 0.0
    kn, an = k0 + n, a0 + n / 2
    mn = (k0 * m0 + n * xbar) / kn
    bn = b0 + 0.5 * ss + k0 * n * (xbar - m0) ** 2 / (2 * kn)
    var = bn / rng.gamma(an, 1.0, size=draws)
    return np.asarray(rng.normal(mn, np.sqrt(var / kn)))


def suspect(labels: pd.Series) -> pd.Series:
    """True where a resolved label is beyond MAX_LABEL_R in size (a NULL label is unresolved, not suspect)."""
    r = pd.to_numeric(labels, errors="coerce").astype(float)
    return r.notna() & ~(r.abs() <= MAX_LABEL_R)


def _ceiling(ceilings: Mapping[str, float] | None, arm: str, cap: float) -> float:
    """`arm`'s share ceiling, `cap` when it has none; 0 when it is not a finite number (trading-safety review, LOW:
    min(cap, nan) is cap, which lifted a broken ceiling to the cap)."""
    if ceilings is None or arm not in ceilings:
        return cap
    try:
        v = float(ceilings[arm])
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) else 0.0


def bounded_shares(p: Mapping[str, float], *, ceilings: Mapping[str, float] | None = None,
                   floor: float = FLOOR_SHARE, cap: float = CAP_SHARE) -> dict[str, float]:
    """Shares proportional to `p`, each in [floor, min(cap, its ceiling)], summing to 1 when the caps allow it (less
    when they do not: a cap never moves budget onto another arm past that arm's own cap). With more arms than 1/floor
    the floor becomes 1/n.

    One scale for the whole style: s = clip(lambda x p, floor, cap), lambda found by bisection so the shares sum to
    the budget (the clipped proportional shares). The floors bind with the cap together, never one after the other:
    brain run 2026-11-03 (vantage intraday) fixed a leader at the 30% cap and then fifteen arms at the 5% floor, summed
    to 1.05, and the engine discarded the run as out of bounds every minute (`weights`)."""
    arms = sorted(p)
    n = len(arms)
    if n == 0:
        return {}
    hi = {a: max(0.0, min(cap, _ceiling(ceilings, a, cap))) for a in arms}
    lo = {a: min(floor, 1.0 / n, hi[a]) for a in arms}
    total = min(1.0, sum(hi.values()))
    w = {a: max(float(p[a]), 0.0) if math.isfinite(float(p[a])) else 0.0 for a in arms}

    def fill(group: list[str], mass: float, v: Mapping[str, float]) -> dict[str, float]:
        """clip(lambda x v, lo, hi) over `group`, summing to `mass` when its caps allow (all at the cap otherwise)."""
        def at(lam: float) -> dict[str, float]:
            return {a: min(max(lam * v[a], lo[a]), hi[a]) for a in group}
        low, top = 0.0, max(hi[a] / v[a] for a in group)
        if sum(at(top).values()) <= mass:
            return at(top)
        for _ in range(200):        # the sum is continuous and non-decreasing in lambda
            mid = (low + top) / 2
            low, top = (low, mid) if sum(at(mid).values()) > mass else (mid, top)
        return at(low)

    pos = [a for a in arms if w[a] > 0]
    zero = [a for a in arms if w[a] <= 0]
    if not pos:
        return fill(arms, total, dict.fromkeys(arms, 1.0))
    # the arms with no p are held at their floor while the others share; what the others' caps leave is theirs, equally
    out = fill(pos, total - sum(lo[a] for a in zero), w)
    if zero:
        out |= fill(zero, max(total - sum(out.values()), sum(lo[a] for a in zero)), dict.fromkeys(zero, 1.0))
    return out


def thompson_shares(r_by_arm: Mapping[str, Sequence[float]], style_of: Mapping[str, str], *,
                    priors: Mapping[str, ArmPrior] | None = None, ceilings: Mapping[str, float] | None = None,
                    draws: int = THOMPSON_DRAWS, seed: int = 0, cold: Collection[str] = ()) -> dict[str, float]:
    """Per style: P(arm has the highest mean net R among its style's arms) under NIG posteriors, then
    `bounded_shares`. Shares are within the style (each style's sum at most 1). A `cold` arm (enterable, no labelled
    fill yet; quant ruling 2026-11-06 Q2) is drawn by no one and holds the cold-start floor min(FLOOR_SHARE, 1/n),
    n the style's weighted arms, so it can still enter and earn its first labels."""
    rng = np.random.default_rng(seed)
    out: dict[str, float] = {}
    cold = set(cold)
    for style in sorted({style_of[a] for a in r_by_arm}):
        arms = sorted(a for a in r_by_arm if style_of[a] == style and a not in cold)
        frozen = sorted(a for a in r_by_arm if style_of[a] == style and a in cold)
        floor = min(FLOOR_SHARE, 1.0 / (len(arms) + len(frozen)))
        best: dict[str, float] = {}
        if arms:
            samples = np.column_stack([
                _nig_draws(np.asarray([v for v in r_by_arm[a] if math.isfinite(v)], dtype=float),
                           (priors or {}).get(a), draws, rng) for a in arms])
            hits = np.bincount(samples.argmax(axis=1), minlength=len(arms)) / draws
            best = {a: float(hits[j]) for j, a in enumerate(arms)}
        shares = bounded_shares({**best, **dict.fromkeys(frozen, 0.0)}, ceilings=ceilings)
        out.update({a: v for a, v in shares.items() if a not in cold})
        out.update({a: min(floor, shares[a]) for a in frozen})
    return out


Allocator = Literal["equal", "thompson"]
ALLOCATOR_EQUAL: Allocator = "equal"
ALLOCATOR_THOMPSON: Allocator = "thompson"
METHOD = {ALLOCATOR_EQUAL: ALLOCATOR_EQUAL, ALLOCATOR_THOMPSON: "thompson_nig_net_r_within_style"}


def allocate(r_by_arm: Mapping[str, Sequence[float]], style_of: Mapping[str, str], *, allocator: Allocator,
             priors: Mapping[str, ArmPrior] | None = None, ceilings: Mapping[str, float] | None = None,
             seed: int = 0, cold: Collection[str] = ()) -> dict[str, float]:
    """Each style's shares of its enterable arms (`r_by_arm`: excluded arms already left out). `equal` (default since
    the ML review of 2026-11-13: "Replace it with equal shares within the existing floor and cap", Thompson's
    weighted next-day R +0.085 against equal +0.086, rank correlation 0.003): every arm with a
    labelled fill alike inside [FLOOR_SHARE, min(CAP_SHARE, its ceiling)], a `cold` arm at the cold-start floor as
    under the bandit (quant ruling 2026-11-06 Q2), so a style never spends more of its pot than the bandit would; the
    pre-registered kill rules and the cost-budget gate do the selecting. `thompson`: H-61's `thompson_shares`, kept
    selectable."""
    if allocator == ALLOCATOR_THOMPSON:
        return thompson_shares(r_by_arm, style_of, priors=priors, ceilings=ceilings, seed=seed, cold=cold)
    if allocator != ALLOCATOR_EQUAL:
        raise ValueError(f"unknown allocator {allocator!r}: equal or thompson")
    out: dict[str, float] = {}
    cold = set(cold)
    for style in sorted({style_of[a] for a in r_by_arm}):
        arms = sorted(a for a in r_by_arm if style_of[a] == style)
        floor = min(FLOOR_SHARE, 1.0 / len(arms))
        shares = bounded_shares({a: 0.0 if a in cold else 1.0 for a in arms}, ceilings=ceilings)
        out.update({a: min(floor, v) if a in cold else v for a, v in shares.items()})
    return out


def write_weights(db: Database, run_ns: int, shares: Mapping[str, float], style_of: Mapping[str, str],
                  detail: Mapping[str, Mapping[str, Any]] | None = None, *, broker: str) -> None:
    """One broker's run (aux.db exploration_broker_weights, keyed by run, broker and arm)."""
    if not broker:
        raise ValueError("bandit weights are written per broker")
    with db.write() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO exploration_broker_weights (run_ns, broker, arm_id, style, weight, detail) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(int(run_ns), broker, a, style_of[a], float(w), json.dumps(dict((detail or {}).get(a, {})), default=str))
             for a, w in shares.items()])


def equal_shares(arms: Sequence[str], ceilings: Mapping[str, float] | None = None) -> dict[str, float]:
    """Equal shares of one style's arms inside the floor and caps."""
    return bounded_shares({a: 1.0 for a in dict.fromkeys(arms)}, ceilings=ceilings)


def weights(db: Database, arms: Sequence[str], *, broker: str, ceilings: Mapping[str, float] | None = None,
            now: pd.Timestamp | None = None, max_age: pd.Timedelta = MAX_WEIGHTS_AGE) -> dict[str, float]:
    """`broker`'s newest run's share of each of ONE style's strategy arms in that style's budget (learnt from that
    broker's evidence only), each in
    [FLOOR_SHARE, min(CAP_SHARE, its ceiling)], or 0 for an arm the run found unable to enter. Equal shares in the same bounds when there is no run, it is older
    than `max_age` at `now`, it does not score every requested arm, the arms span styles, a share is out of bounds,
    the shares sum above 1, or aux.db is unreadable."""
    want = list(dict.fromkeys(arms))
    if not want:
        return {}
    fallback = equal_shares(want, ceilings)
    try:
        with db.connection() as conn:
            r = conn.execute("SELECT MAX(run_ns) FROM exploration_broker_weights WHERE broker = ?",
                             (broker,)).fetchone()
            if r is None or r[0] is None:
                return fallback
            run = int(r[0])
            got = {str(a): (float(w), str(s)) for a, w, s in conn.execute(
                "SELECT arm_id, weight, style FROM exploration_broker_weights WHERE run_ns = ? AND broker = ?",
                (run, broker))}
    except (sqlite3.Error, StateDbError, OSError, ValueError, TypeError) as exc:
        log.warning("exploration weights unreadable (%s): equal shares", type(exc).__name__)
        return fallback
    if now is not None and _ts(run) + max_age < now:
        log.warning("exploration weights from run %s are stale: equal shares", run)
        return fallback
    if any(a not in got for a in want) or len({got[a][1] for a in want}) != 1:
        log.warning("exploration weights of run %s miss an arm or mix styles: equal shares", run)
        return fallback
    floor = min(FLOOR_SHARE, 1.0 / len(want))
    hi = {a: max(0.0, min(CAP_SHARE, _ceiling(ceilings, a, CAP_SHARE))) for a in want}
    vals = {a: got[a][0] for a in want}
    # PD 2026-11-06: a share of exactly 0 is an arm the learner found unable to enter (`entry_exclusions`); the others
    # keep their bounds
    if any(not (math.isfinite(v) and (v == 0.0 or min(floor, hi[a]) - 1e-9 <= v <= CAP_SHARE + 1e-9))
           for a, v in vals.items()) or sum(vals.values()) > 1 + 1e-9:
        log.warning("exploration weights of run %s out of bounds: equal shares", run)
        return fallback
    return {a: float(min(v, hi[a])) for a, v in vals.items()}


# ---------------------------------------------------------------------------------------------- the nightly run
SPEC_PATHS: tuple[Path, ...] = (arm_spec.REPO_ROOT / "config" / "exploration_arms.yaml",
                                arm_spec.REPO_ROOT / "config" / "spec_arms.yaml")


def entry_exclusions(db: Database, arm_ids: Collection[str], *, broker: str,
                     spec_paths: Sequence[Path] = SPEC_PATHS, as_of: pd.Timestamp | None = None) -> dict[str, str]:
    """The arms of `arm_ids` that can send no entry on `broker`'s accounts, with why (PD 2026-11-06: "retired arms,
    blocked arms ... get no weight"): `retired` (the spec row's `retired_day`, never undone, or the broker's
    `exploration_arm_status`, the daily review's stop) or `blocked` (spec status not run, or refused at load: ADR
    0061 qualification, a learning arm's cross refusal: `research.arms.load_arms`). An arm no spec names is not judged
    here (the engine drops it at load, so it has no new fill). An unreadable spec raises: the learner does not run on
    arms it cannot judge (its weights age into equal shares); a missing status table reads as no retirement.
    Point in time (quant ruling 2026-11-06 Q5): with `as_of`, a spec retirement counts from its `retired_day` and a
    stop from its `recorded_ns`, so a replayed run never knows a later retirement."""
    want = set(arm_ids)
    rows: dict[str, arm_spec.Arm] = {}
    for path in spec_paths:
        if path.exists():
            rows.update({a.id: a for a in arm_spec.load_arms(path) if a.id in want})
    out: dict[str, str] = {}
    for a in sorted(rows):
        row = rows[a]
        if row.retired and (as_of is None or str(row.retired_day) <= str(as_of.tz_convert("UTC").date())):
            out[a] = "retired"
        elif row.status != "run" or row.entry_refused:
            out[a] = "blocked"
    try:
        with db.connection() as conn:
            stopped = {str(a) for (a,) in conn.execute(
                "SELECT arm_id FROM exploration_arm_status WHERE broker = ? AND status = 'retired' "
                "AND recorded_ns <= ?", (broker, _ns(as_of) if as_of is not None else 2**63 - 1))}
    except sqlite3.Error as exc:
        log.warning("exploration_arm_status unreadable (%s): spec retirements only", type(exc).__name__)
        stopped = set()
    out.update({a: "retired" for a in sorted(stopped & want)})
    return out


def learn(db: Database, *, broker: str, as_of: pd.Timestamp, arms: Mapping[str, str] | None = None,
          controls: Sequence[str] = (), ceilings: Mapping[str, float] | None = None, seed: int = 0,
          retrain: bool = False, models_dir: Path | None = None, held_out: Collection[str] = (),
          learning: Collection[str] | None = None,
          instruments: Mapping[str, str] | None = None,
          spec_paths: Sequence[Path] = SPEC_PATHS, allocator: Allocator = ALLOCATOR_EQUAL) -> dict[str, Any]:
    """One broker's arm shares (nightly; `allocate`: equal by default, the H-61 bandit when `allocator` is
    thompson) and, when `retrain` (Sundays, and only while settings research.exploration.meta_labeller is on: H-62
    retired 2026-11-13), its H-62 style models whose retrain is due, on that broker's evidence only. `arms`: the strategy arm id -> style configured on the broker's accounts (joined
    with the arms seen in its candidates); control arms (random brackets) are never allocated by results. Shares use
    each arm's net R labels (label_sim_r) resolved by `as_of`. `learning` (default: the spec's `role: learning` rows,
    ADR 0061 amendment 2026-11-02): unqualified demo arms, never allocated by results and never in a style model's
    training rows, as a control is never allocated.

    PD 2026-11-06 (its top weights sat on retired and non-trading arms and never steered an entry): only arms that can
    actually enter are weighted. A retired or blocked arm (`entry_exclusions`, point in time) and, when `arms` is
    given, an arm not configured on the broker are written at 0 (`detail.excluded` says why); the shares renormalise
    over the rest within each style, where an arm with no labelled fill holds the cold-start floor (Q2). A run on labelled evidence is written even when no
    arm can enter (all at 0): the learner ran."""
    if not broker:
        raise ValueError("exploration evidence is learnt per broker")
    if learning is None:
        try:
            from goldbot.research.arms import learning_arm_ids
            learning = learning_arm_ids()
        except (OSError, ValueError, TypeError) as exc:
            log.warning("exploration spec unreadable (%s): no learning arm is set apart tonight", type(exc).__name__)
            learning = ()
    learning = frozenset(learning)
    obs = read_obs(db, broker=broker, as_of=as_of, evidence="posterior", held_out=held_out,    # prereg Amendment 4
                   instrument=GOLD, instruments=instruments, replay_version=REPLAY_V1)
    if learning:
        held_out = {*held_out, *(str(c) for c in read_obs(db, broker=broker, replay_version=REPLAY_V1).loc[
            lambda f: f["arm_id"].isin(learning), "candidate_id"])}
        obs = obs[~obs["arm_id"].isin(learning)]
    strat = obs[~obs["is_control"]]
    strat = strat[strat["style"].isin(STYLES)]        # quant Q5: a growth-pot row is read, never learnt from
    style_of = {str(a): str(s) for a, s in strat.groupby("arm_id")["style"].first().items()}
    style_of.update({a: s for a, s in (arms or {}).items() if s in STYLES})
    for c in (*controls, *learning):
        style_of.pop(c, None)
    out: dict[str, Any] = {"arms": len(style_of)}
    if retrain and models_dir is not None:
        cutoff, activation = model_cutoff(as_of)
        models: dict[str, Any] = {}
        for style in STYLES:
            due, new, total = retrain_due(db, style, cutoff, broker=broker, held_out=held_out,
                                          instruments=instruments)
            if not due:
                models[style] = {"due": False, "new": new, "total": total}
                continue
            try:
                fit = cross_fit_meta(training_table(db, style=style, broker=broker, as_of=cutoff, held_out=held_out,
                                                    instruments=instruments), style)
                models[style] = {"version": save_model(db, models_dir, fit, broker=broker, cutoff=cutoff,
                                                       activation=activation),
                                 "auc_net": fit["auc_net"], "auc_gross": fit["auc_gross"]}
            except Exception as exc:       # noqa: BLE001 - one style's fit must not stop the weights
                log.exception("H-62 %s %s retrain failed", broker, style)
                models[style] = {"error": type(exc).__name__}
        out["models"] = models
    if not style_of:
        return out
    priors = read_priors(db) if broker in PRIOR_BROKERS else {}      # ICM-cost history: never another broker's
    sus = strat.loc[suspect(strat["label_sim_r"]), "arm_id"].astype(str).value_counts().to_dict()
    out["suspect_labels"] = int(sum(sus.values()))
    if sus:                             # kept in the evidence (a real gap may be one); counted and logged
        log.warning("exploration %s: labels beyond %.0f R (suspect, kept) by arm: %s", broker, MAX_LABEL_R,
                    ", ".join(f"{a} {n}" for a, n in sorted(sus.items())))
    r_by_arm = {a: strat.loc[strat["arm_id"] == a, "label_sim_r"].astype(float).tolist() for a in style_of}
    if not any(r_by_arm.values()):      # quant 2026-10-27 (LOW): a run on no evidence is not a learnt run
        log.warning("exploration %s: no labelled evidence by %s, no weights run written", broker, as_of)
        out["weights"] = None
        return out
    excluded = entry_exclusions(db, style_of, broker=broker, spec_paths=spec_paths, as_of=as_of)
    if arms is not None:
        excluded.update({a: "not_on_broker" for a in style_of if a not in arms and a not in excluded})
    if excluded:
        log.info("exploration %s: no weight for %s", broker,
                 ", ".join(f"{a} ({why})" for a, why in sorted(excluded.items())))
    live = {a: r for a, r in r_by_arm.items() if a not in excluded}
    # quant ruling 2026-11-06 Q2: an enterable arm without a labelled fill (new, a swing arm, a non-gold arm the gold
    # labeller never reads) holds the cold-start floor, never 0
    cold = {a for a, r in live.items() if not any(math.isfinite(v) for v in r)}
    shares = {**allocate(live, style_of, allocator=allocator, priors=priors, ceilings=ceilings, seed=seed, cold=cold),
              **dict.fromkeys(excluded, 0.0)}
    detail = {a: {"n": len(r_by_arm[a]), "mean_r": float(np.mean(r_by_arm[a])) if r_by_arm[a] else None,
                  "prior": priors[a].prior_version if a in priors else None, "ceiling": (ceilings or {}).get(a),
                  "method": METHOD[allocator], "suspect": int(sus.get(a, 0)),
                  **({"excluded": excluded[a]} if a in excluded else {}),
                  **({"cold_start": True} if a in cold else {})} for a in style_of}
    write_weights(db, _ns(as_of), shares, style_of, detail, broker=broker)
    out["weights"] = shares
    return out


__all__ = ["ALLOCATOR_EQUAL", "ALLOCATOR_THOMPSON", "Allocator", "allocate", "CAP_SHARE", "FEATURE_LISTS", "FLOOR_SHARE", "MAX_FEATURES", "MAX_LABEL_R", "MAX_WEIGHTS_AGE", "ArmPrior", "ReplayCosts",
           "bounded_shares", "cross_fit_meta", "entry_exclusions", "equal_shares", "label_candidates", "learn", "model_columns",
           "model_cutoff", "prior_from_history", "purged_folds", "read_obs", "read_priors", "record_candidate",
           "record_fill", "replay_label", "retrain_due", "save_model", "serving_model", "session_day", "stamp_brokers", "suspect", "thompson_shares",
           "training_table", "weights", "write_prior", "write_weights",
           "REPLAY_V1", "REPLAY_V2", "ReplayCostsV2", "ReplayV2", "fill_replay_pairs", "label_candidates_v2",
           "ledger_legs", "replay_costs_v2", "replay_label_v2", "synthetic_stop_fills", "void_ids",
           "void_non_gold_labels", "CostOfDay", "bar_implied_stop_legs", "cost_model_hash", "daily_costs_v2",
           "pinned_costs", "replay_frame_v2", "session_start"]
