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
                      Written once; an unresolved label stays NULL (excluded, never censored).
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

Isolation (research/population.py `agent_trades`): nothing here reads or writes the shadow book, closed trades, the
fills table, the probe ledger or the phase gate; the test suite checks the module never names them.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import pickle
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.data.store import asof_join
from goldbot.data.timeutil import NEW_YORK, epoch_ns
from goldbot.db.connection import Database, StateDbError, now_ns
from goldbot.labels.triple_barrier import SwapSpec, rollover_nights
from goldbot.research.model import MAX_FEATURES, MetaLabelModel, check_feature_cap, fit_calibrator

log = logging.getLogger("goldbot.exploration")

Style = Literal["scalp", "intraday", "swing"]
STYLES: tuple[str, ...] = ("scalp", "intraday", "swing")
TIMEFRAMES: tuple[str, ...] = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")
BRANCHES: tuple[str, ...] = ("ranked", "random", "uniform", "blocked")

# H-61 allocation (prereg): within a style, every live arm keeps at least FLOOR_SHARE and at most CAP_SHARE
FLOOR_SHARE = 0.05
CAP_SHARE = 0.30
MAX_WEIGHTS_AGE = pd.Timedelta(days=3)          # older bandit weights are stale: equal shares until the next run
THOMPSON_DRAWS = 4000
PRIOR_SD_R = 1.0                                # net-R spread of one trade under the flat prior

# H-60 priors: history 2019-01-01..2025-09-30 only (the current cost era); the holdout is never read
PRIOR_HISTORY_START = pd.Timestamp("2019-01-01", tz="UTC")
HOLDOUT_START = pd.Timestamp("2025-10-01", tz="UTC")
HOLDOUT_END = pd.Timestamp("2026-10-01", tz="UTC")       # exclusive
PRIOR_SHRINK = 0.5
PRIOR_N_MAX = 50.0
PRIOR_N_PER_TRADES = 100.0

# label replay (prereg H-60 costs): slippage per leg never below the prior, never credited
SLIPPAGE_FLOOR_USD = 0.15

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
    if style not in STYLES:
        out.append(f"style {style!r} not one of {STYLES}")
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
             as_of: pd.Timestamp | None = None, include_quarantined: bool = False) -> pd.DataFrame:
    """Candidates ordered by decision time; times as UTC datetimes (ns). `as_of`: only labels resolved by then;
    `broker`: only that broker's rows (an unkeyed row matches no broker). Every learner passes `broker`."""
    where: list[str] = ["1 = 1"]
    params: list[Any] = []
    if not include_quarantined:
        where.append("quarantine IS NULL")
    if broker is not None:
        where.append("broker = ?")
        params.append(broker)
    if style is not None:
        where.append("style = ?")
        params.append(style)
    if arm_id is not None:
        where.append("arm_id = ?")
        params.append(arm_id)
    if as_of is not None:
        where.append("label_sim_r IS NOT NULL AND label_exit_ns <= ?")
        params.append(_ns(as_of))
    with db.connection() as conn:
        df = pd.read_sql_query(f"SELECT * FROM exploration_obs WHERE {' AND '.join(where)} "
                               "ORDER BY decision_ns, candidate_id", conn, params=params)
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
          target_d: float, ex_open: np.ndarray, ex_high: np.ndarray, ex_low: np.ndarray
          ) -> tuple[pd.Timestamp, float, str] | None:
    """Exit time, price and barrier of a position entered at minute k at `entry`; exits read on the exit side (bid for
    a long, ask for a short). None when the data ends before the time limit with no barrier hit."""
    stop, target = entry - side * stop_d, entry + side * target_d
    for j in range(k, len(t)):
        if t[j] >= deadline:
            return t[j], float(ex_open[j]), "time"
        o, hi, lo = float(ex_open[j]), float(ex_high[j]), float(ex_low[j])
        end = t[j] + pd.Timedelta(minutes=1)
        if side > 0:
            if lo <= stop:                                  # the stop first when both fall in one minute
                return end, min(stop, o), "stop"            # a gap through it fills at the open
            if hi >= target:
                return end, target, "target"                # never better than the target
        else:
            if hi >= stop:
                return end, max(stop, o), "stop"
            if lo <= target:
                return end, target, "target"
    return None


def replay_label(bars_1m: pd.DataFrame, *, side: int, decision_utc: pd.Timestamp, stop_dist: float,
                 target_dist: float, max_hold_s: int, costs: ReplayCosts) -> Replay | None:
    """Net and gross R of one candidate on 1m bid/ask bars (stamped at their open). See the module docstring."""
    b = bars_1m.sort_values("ts_utc").reset_index(drop=True)
    t = pd.DatetimeIndex(pd.to_datetime(b["ts_utc"], utc=True)).as_unit("ns")
    deadline = pd.Timestamp(decision_utc) + pd.Timedelta(seconds=int(max_hold_s))
    k = int(t.searchsorted(pd.Timestamp(decision_utc), side="left"))
    if k >= len(t) or t[k] >= deadline:
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
                                     costs.swap.triple_weekday)[0])
        swap_paid = max(0.0, -float(costs.swap.usd_per_oz(np.array([side]))[0])) * nights
    sim = (side * (exit_p - entry) - 2 * costs.slip - costs.commission_rt_per_oz - swap_paid) / stop_dist
    mid = side * (gross[1] - float(mids["open"][k])) / stop_dist
    return Replay(exit_utc=exit_t, sim_r=float(sim), mid_r=float(mid), barrier=barrier)


def label_candidates(db: Database, bars_1m: pd.DataFrame, costs: ReplayCosts, *, cutoff: pd.Timestamp,
                     account_id: str | None = None) -> int:
    """Replay every unlabelled, unquarantined candidate of `costs.broker` (and `account_id` when given) whose label
    resolves by `cutoff` on the bars before it, and write label_sim_r / label_mid_r once. Returns the number labelled.
    The cost table must be effective by `cutoff`; another broker's rows are never labelled with it."""
    if costs.effective_utc > cutoff:
        raise ValueError("cost model effective after the labelling cutoff: labels must be point in time")
    if bars_1m.empty:
        return 0
    bars = bars_1m[pd.to_datetime(bars_1m["ts_utc"], utc=True) < cutoff]
    obs = read_obs(db, broker=costs.broker)
    obs = obs[obs["label_sim_r"].isna()]
    if account_id is not None:
        obs = obs[obs["account_id"] == account_id]
    rows = []
    for r in obs.to_dict("records"):
        rep = replay_label(bars, side=int(r["side"]), decision_utc=r["decision_utc"], stop_dist=float(r["stop_dist"]),
                           target_dist=float(r["target_dist"]), max_hold_s=int(r["max_hold_s"]), costs=costs)
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


# ---------------------------------------------------------------------------------------------- training table
_BASE = ["candidate_id", "broker", "arm_id", "family", "style", "tf", "side", "is_control", "decision_utc", "session_day",
         "max_hold_s", "taken", "branch", "mu", "meta_p", "model_version", "label_exit_utc", "label_sim_r",
         "label_mid_r", "label_fill_r", "cost_model_version", "feature_version", "feature_list_id",
         "available_utc_max"]


def training_table(db: Database, *, style: str, broker: str, as_of: pd.Timestamp,
                   features: pd.DataFrame | None = None, arm_id: str | None = None,
                   feature_cols: Sequence[str] | None = None) -> pd.DataFrame:
    """One broker's admissible rows of a style (spec section 2; never pooled across brokers): label resolved by `as_of`, not quarantined, no decision in the
    holdout, taken or not. `y` = 1[label_sim_r > 0]; label_fill_r and label_mid_r ride along as diagnostics. The
    columns are the style's frozen v1 list (or `feature_cols`), from each row's snapshot when it has one, else from
    `features` (history and tests) through `asof_join` on available_utc <= decision_utc. The row invariant
    available_utc_max <= decision_utc fails the build (ValueError)."""
    if style not in STYLES:
        raise ValueError(f"style {style!r} not one of {STYLES}")
    cols = list(feature_cols) if feature_cols is not None else list(FEATURE_LISTS[STYLE_FEATURE_LIST[style]])
    check_feature_cap([*cols, "side", "arm_id"])
    obs = read_obs(db, broker=broker, style=style, arm_id=arm_id, as_of=as_of)
    dec = pd.DatetimeIndex(obs["decision_utc"])
    obs = obs[~((dec >= HOLDOUT_START) & (dec < HOLDOUT_END))].reset_index(drop=True)      # L-10
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


def retrain_due(db: Database, style: str, cutoff: pd.Timestamp, *, broker: str) -> tuple[bool, int, int]:
    """(due, new, total) of one broker's style: >= RETRAIN_MIN_NEW admissible labels resolved since its last fit's
    cutoff and >= RETRAIN_MIN_TOTAL in all (spec section 5)."""
    obs = read_obs(db, broker=broker, style=style, as_of=cutoff)
    last = _last_cutoff(db, style, broker)
    new = int((obs["label_exit_utc"] > last).sum()) if last is not None else len(obs)
    return new >= RETRAIN_MIN_NEW and len(obs) >= RETRAIN_MIN_TOTAL, new, len(obs)


def save_model(db: Database, models_dir: Path, fit: Mapping[str, Any], *, broker: str, cutoff: pd.Timestamp,
               activation: pd.Timestamp) -> str:
    """Persist one broker's fitted style model (fold models + calibrator) as a pickle with its SHA-256, and register
    it as 'shadow': it never serves until a separate champion decision (spec section 5) marks it 'champion'."""
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
    a model without a broker key never serves): the newest version with status 'champion' whose
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


def bounded_shares(p: Mapping[str, float], *, ceilings: Mapping[str, float] | None = None,
                   floor: float = FLOOR_SHARE, cap: float = CAP_SHARE) -> dict[str, float]:
    """Shares proportional to `p`, each in [floor, min(cap, its ceiling)], summing to 1 when the caps allow it (less
    when they do not: a cap never moves budget onto another arm past that arm's own cap). With more arms than 1/floor
    the floor becomes 1/n."""
    arms = sorted(p)
    n = len(arms)
    if n == 0:
        return {}
    hi = {a: max(0.0, min(cap, float((ceilings or {}).get(a, cap)))) for a in arms}
    lo = {a: min(floor, 1.0 / n, hi[a]) for a in arms}
    total = min(1.0, sum(hi.values()))
    w = {a: max(float(p[a]), 0.0) if math.isfinite(float(p[a])) else 0.0 for a in arms}
    fixed: dict[str, float] = {}
    for _ in range(2 * n + 1):
        free = [a for a in arms if a not in fixed]
        if not free:
            break
        mass = total - sum(fixed.values())
        sw = sum(w[a] for a in free)
        s = {a: mass * (w[a] / sw if sw > 0 else 1.0 / len(free)) for a in free}
        over = [a for a in free if s[a] > hi[a] + 1e-15]
        under = [a for a in free if s[a] < lo[a] - 1e-15]
        if not over and not under:
            fixed.update(s)
            break
        for a in over or under:
            fixed[a] = hi[a] if over else lo[a]
    return {a: float(min(max(fixed.get(a, lo[a]), lo[a]), hi[a])) for a in arms}


def thompson_shares(r_by_arm: Mapping[str, Sequence[float]], style_of: Mapping[str, str], *,
                    priors: Mapping[str, ArmPrior] | None = None, ceilings: Mapping[str, float] | None = None,
                    draws: int = THOMPSON_DRAWS, seed: int = 0) -> dict[str, float]:
    """Per style: P(arm has the highest mean net R among its style's arms) under NIG posteriors, then
    `bounded_shares`. Shares are within the style (each style's sum at most 1)."""
    rng = np.random.default_rng(seed)
    out: dict[str, float] = {}
    for style in sorted({style_of[a] for a in r_by_arm}):
        arms = sorted(a for a in r_by_arm if style_of[a] == style)
        samples = np.column_stack([
            _nig_draws(np.asarray([v for v in r_by_arm[a] if math.isfinite(v)], dtype=float),
                       (priors or {}).get(a), draws, rng) for a in arms])
        best = np.bincount(samples.argmax(axis=1), minlength=len(arms)) / draws
        out.update(bounded_shares({a: float(best[j]) for j, a in enumerate(arms)}, ceilings=ceilings))
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
    [FLOOR_SHARE, min(CAP_SHARE, its ceiling)]. Equal shares in the same bounds when there is no run, it is older
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
    hi = {a: min(CAP_SHARE, float((ceilings or {}).get(a, CAP_SHARE))) for a in want}
    vals = {a: got[a][0] for a in want}
    if any(not (math.isfinite(v) and min(floor, hi[a]) - 1e-9 <= v <= CAP_SHARE + 1e-9) for a, v in vals.items()) \
            or sum(vals.values()) > 1 + 1e-9:
        log.warning("exploration weights of run %s out of bounds: equal shares", run)
        return fallback
    return {a: float(min(v, hi[a])) for a, v in vals.items()}


# ---------------------------------------------------------------------------------------------- the nightly run
def learn(db: Database, *, broker: str, as_of: pd.Timestamp, arms: Mapping[str, str] | None = None,
          controls: Sequence[str] = (), ceilings: Mapping[str, float] | None = None, seed: int = 0,
          retrain: bool = False, models_dir: Path | None = None) -> dict[str, Any]:
    """One broker's bandit (nightly) and, when `retrain` (Sundays), its H-62 style models whose retrain is due, on
    that broker's evidence only. `arms`: the strategy arm id -> style configured on the broker's accounts (joined
    with the arms seen in its candidates); control arms (random brackets) are never allocated by results. Shares use
    each arm's net R labels (label_sim_r) resolved by `as_of`."""
    if not broker:
        raise ValueError("exploration evidence is learnt per broker")
    obs = read_obs(db, broker=broker, as_of=as_of)
    strat = obs[~obs["is_control"]]
    style_of = {str(a): str(s) for a, s in strat.groupby("arm_id")["style"].first().items()}
    style_of.update(arms or {})
    for c in controls:
        style_of.pop(c, None)
    out: dict[str, Any] = {"arms": len(style_of)}
    if retrain and models_dir is not None:
        cutoff, activation = model_cutoff(as_of)
        models: dict[str, Any] = {}
        for style in STYLES:
            due, new, total = retrain_due(db, style, cutoff, broker=broker)
            if not due:
                models[style] = {"due": False, "new": new, "total": total}
                continue
            try:
                fit = cross_fit_meta(training_table(db, style=style, broker=broker, as_of=cutoff), style)
                models[style] = {"version": save_model(db, models_dir, fit, broker=broker, cutoff=cutoff,
                                                       activation=activation),
                                 "auc_net": fit["auc_net"], "auc_gross": fit["auc_gross"]}
            except Exception as exc:       # noqa: BLE001 - one style's fit must not stop the weights
                log.exception("H-62 %s %s retrain failed", broker, style)
                models[style] = {"error": type(exc).__name__}
        out["models"] = models
    if not style_of:
        return out
    priors = read_priors(db)
    r_by_arm = {a: strat.loc[strat["arm_id"] == a, "label_sim_r"].astype(float).tolist() for a in style_of}
    shares = thompson_shares(r_by_arm, style_of, priors=priors, ceilings=ceilings, seed=seed)
    detail = {a: {"n": len(r_by_arm[a]), "mean_r": float(np.mean(r_by_arm[a])) if r_by_arm[a] else None,
                  "prior": priors[a].prior_version if a in priors else None, "ceiling": (ceilings or {}).get(a),
                  "method": "thompson_nig_net_r_within_style"} for a in style_of}
    write_weights(db, _ns(as_of), shares, style_of, detail, broker=broker)
    out["weights"] = shares
    return out


__all__ = ["CAP_SHARE", "FEATURE_LISTS", "FLOOR_SHARE", "MAX_FEATURES", "MAX_WEIGHTS_AGE", "ArmPrior", "ReplayCosts",
           "bounded_shares", "cross_fit_meta", "equal_shares", "label_candidates", "learn", "model_columns",
           "model_cutoff", "prior_from_history", "purged_folds", "read_obs", "read_priors", "record_candidate",
           "record_fill", "replay_label", "retrain_due", "save_model", "serving_model", "session_day", "stamp_brokers", "thompson_shares",
           "training_table", "weights", "write_prior", "write_weights"]
