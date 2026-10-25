"""Exploration arms, data side (architect plan 2026-10-25, lane L3): what an exploration trade taught us, kept apart
from every trading record.

* `record_obs`     one row per CLOSED exploration trade in aux.db `exploration_obs` (migration 16): arm, family,
                   timeframe, side, decision/entry/exit times (epoch ns, UTC), prices, stop, the R multiple net of the
                   measured round-trip cost, a censored flag and a reference to the decision-time feature snapshot.
                   Written once (INSERT OR IGNORE: the first record is the record; a later revision is logged and
                   ignored). A row that fails validation is still written with its `quarantine` reason, never trained
                   on. The fills and closes themselves stay in `cost_probes` (purpose "exploration", written by the
                   engine lane); `obs_id` is that row's client order id.
* `training_table` one arm's rows whose label is known by `as_of` (exit_utc <= as_of), each joined through
                   `asof_join` to the feature rows available STRICTLY before its decision instant, at most
                   MAX_FEATURES columns.
* `cross_fit_meta` a per-family meta-labeller (P(net R > 0)), out-of-fold through purged, embargoed time folds;
                   scored per arm, never pooled across arms for evaluation.
* `thompson_shares` / `write_weights` / `weights`   the nightly bandit: Thompson sampling on net R per arm (a backtest
                   prior when present), each arm's share = P(best arm) capped at its ceiling and never re-normalised
                   upward, so shares only divide out the configured exploration ceilings, never raise them. Missing,
                   stale, corrupt or incomplete weights read as equal shares (capped the same way).

Isolation (research/population.py `agent_trades`): nothing here reads or writes the shadow book, closed trades, the
fills table, the probe ledger or the phase gate, and the test suite checks the module never names them.
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.data.store import asof_join
from goldbot.data.timeutil import epoch_ns
from goldbot.db.connection import Database, StateDbError, now_ns

log = logging.getLogger("goldbot.exploration")

MAX_FEATURES = 40                               # CLAUDE.md: max 40 features per live model
MAX_WEIGHTS_AGE = pd.Timedelta(days=3)          # older bandit weights are stale: equal shares until the next run
TIMEFRAMES = ("15m", "1h", "4h", "1d")
MIN_FIT_ROWS = 20                               # a fold trains only with this many purged rows and both classes
PRIOR_SD_R = 1.0                                # net-R spread of one trade when an arm has too few of its own
MIN_SD_R = 0.25
MAX_PRIOR_N = 10.0                              # a backtest prior counts as at most this many live trades
THOMPSON_DRAWS = 4000
_KEY_COLS = ("arm_id", "decision_utc", "exit_utc", "r_net")


# ---------------------------------------------------------------------------------------------- the ledger
def r_multiple(side: int, entry: float, exit_: float, stop: float, cost_per_oz: float) -> float:
    """Net R: the move in the trade's favour less the measured round-trip cost (USD per oz: spread, commission and swap
    per oz), in units of the initial risk |entry - stop|."""
    return (side * (exit_ - entry) - cost_per_oz) / abs(entry - stop)


def _ns(ts: pd.Timestamp) -> int:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        raise ValueError("exploration timestamps must be timezone-aware UTC")
    return int(epoch_ns(pd.DatetimeIndex([t.tz_convert("UTC")]))[0])


def _problems(side: int, tf: str, d: int, e: int, x: int, entry: float, exit_: float, stop: float,
              cost: float) -> list[str]:
    out = []
    if side not in (-1, 1):
        out.append(f"side {side} not +-1")
    if tf not in TIMEFRAMES:
        out.append(f"timeframe {tf!r} not one of {TIMEFRAMES}")
    if not d <= e <= x:
        out.append("times out of order (decision <= entry <= exit)")
    if not all(math.isfinite(v) and v > 0 for v in (entry, exit_, stop)):
        out.append("a price is missing, non-finite or not positive")
    elif side in (-1, 1) and side * (entry - stop) <= 0:
        out.append("stop not on the losing side of entry")
    if not (math.isfinite(cost) and cost >= 0):
        out.append("cost not a finite non-negative USD/oz")
    return out


def record_obs(db: Database, *, obs_id: str, account_id: str, arm_id: str, family: str, tf: str, side: int,
               decision_utc: pd.Timestamp, entry_utc: pd.Timestamp, exit_utc: pd.Timestamp, entry_price: float,
               exit_price: float, stop: float, cost_per_oz: float, censored: bool,
               snapshot_ref: str | None = None) -> bool:
    """Record one closed exploration trade; True when written, False when `obs_id` was already recorded (the first
    record stands). `decision_utc` is the instant the arm decided (after the signal bar closed): only features
    available strictly before it are ever joined. `cost_per_oz` is the measured round trip in USD per oz. `censored`:
    the outcome was cut short (a forced or time exit before either barrier). Invalid input is written quarantined."""
    if not (obs_id and account_id and arm_id and family):
        raise ValueError("obs_id, account_id, arm_id and family are required")
    d, e, x = _ns(decision_utc), _ns(entry_utc), _ns(exit_utc)
    entry, exit_, stop_, cost = float(entry_price), float(exit_price), float(stop), float(cost_per_oz)
    bad = _problems(int(side), tf, d, e, x, entry, exit_, stop_, cost)
    r = None if bad else r_multiple(int(side), entry, exit_, stop_, cost)

    def real(v: float) -> float | None:
        return v if math.isfinite(v) else None

    row = (obs_id, account_id, arm_id, family, tf, int(side), d, e, x, real(entry), real(exit_), real(stop_),
           real(cost), r, int(bool(censored)), snapshot_ref, "; ".join(bad) or None, now_ns())
    with db.write() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO exploration_obs (obs_id, account_id, arm_id, family, tf, side, decision_ns, "
            "entry_ns, exit_ns, entry_price, exit_price, stop, cost_per_oz, r_net, censored, snapshot_ref, quarantine, "
            "recorded_ns) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row)
        written = cur.rowcount == 1
    if bad:
        log.warning("exploration obs %s quarantined: %s", obs_id, "; ".join(bad))
    if not written:
        log.info("exploration obs %s already recorded: the first record stands", obs_id)
    return written


def read_obs(db: Database, *, arm_id: str | None = None, as_of: pd.Timestamp | None = None,
             include_quarantined: bool = False) -> pd.DataFrame:
    """Observations ordered by decision time, times as UTC datetimes (ns). `as_of`: only labels known by then."""
    where: list[str] = ["1 = 1"]
    params: list[Any] = []
    if not include_quarantined:
        where.append("quarantine IS NULL")
    if arm_id is not None:
        where.append("arm_id = ?")
        params.append(arm_id)
    if as_of is not None:
        where.append("exit_ns <= ?")
        params.append(_ns(as_of))
    with db.connection() as conn:
        df = pd.read_sql_query(
            "SELECT obs_id, account_id, arm_id, family, tf, side, decision_ns, entry_ns, exit_ns, entry_price, "
            "exit_price, stop, cost_per_oz, r_net, censored, snapshot_ref, quarantine FROM exploration_obs "
            f"WHERE {' AND '.join(where)} ORDER BY decision_ns, obs_id", conn, params=params)
    for c in ("decision", "entry", "exit"):
        df[f"{c}_utc"] = pd.to_datetime(df.pop(f"{c}_ns").astype("int64"), unit="ns", utc=True)
    df["censored"] = df["censored"].astype(bool)
    return df


# ---------------------------------------------------------------------------------------------- training table
def _check_width(cols: Sequence[str]) -> None:
    if len(cols) > MAX_FEATURES:
        raise ValueError(f"{len(cols)} features: at most {MAX_FEATURES} per model")


def training_table(db: Database, features: pd.DataFrame, *, arm_id: str, as_of: pd.Timestamp,
                   feature_cols: Sequence[str] | None = None) -> pd.DataFrame:
    """One arm's labelled rows: label known by `as_of`, features from the newest row available strictly before each
    decision (asof_join on available_utc, the one join to time). Columns: obs_id, arm_id, family, tf, side,
    decision_utc, exit_utc, r_net, y (net R > 0), censored, then the features."""
    if not arm_id:
        raise ValueError("training_table is per arm: arm_id is required")
    cols = list(feature_cols) if feature_cols is not None else [c for c in features.columns if c != "available_utc"]
    _check_width(cols)
    obs = read_obs(db, arm_id=arm_id, as_of=as_of)
    base = ["obs_id", "arm_id", "family", "tf", "side", "decision_utc", "exit_utc", "r_net", "censored"]
    if obs.empty:
        return pd.DataFrame(columns=[*base, "y", *cols])
    left = obs[base].copy()
    # strictly before: a row released at the decision instant itself is not used (join at decision - 1 ns)
    left["_join_utc"] = pd.DatetimeIndex(left["decision_utc"]).as_unit("ns") - pd.Timedelta(1, "ns")
    right = features[["available_utc", *cols]]
    out = asof_join(left, right, on="_join_utc").drop(columns=["_join_utc"])
    out["y"] = (out["r_net"] > 0).astype(int)
    return out[[*base, "y", *cols]].sort_values("decision_utc").reset_index(drop=True)


def select_features(features: pd.DataFrame, max_n: int = MAX_FEATURES) -> list[str]:
    """Label-free choice of at most `max_n` numeric columns: best coverage first, then name (deterministic; choosing
    on the labels before cross-fitting would leak them)."""
    num = [c for c in features.columns if c != "available_utc" and pd.api.types.is_numeric_dtype(features[c])]
    cover = {c: float(features[c].notna().mean()) for c in num}
    return sorted(num, key=lambda c: (-cover[c], c))[:max_n]


# ---------------------------------------------------------------------------------------------- meta-labeller
def purged_folds(decision: pd.DatetimeIndex, exit_: pd.DatetimeIndex, *, k: int = 5,
                 embargo: pd.Timedelta = pd.Timedelta(days=1)) -> list[tuple[np.ndarray, np.ndarray]]:
    """k contiguous folds in decision order. A training row's label interval [decision, exit] never touches the test
    window [first decision, last exit] (purge), and nothing decided within `embargo` after it trains (embargo).
    López de Prado (2018) ch. 7."""
    dec, ext = pd.DatetimeIndex(decision), pd.DatetimeIndex(exit_)
    order = np.argsort(epoch_ns(dec), kind="stable")
    out = []
    for test in np.array_split(order, k):
        if len(test) == 0:
            continue
        t0, t1 = dec[test].min(), ext[test].max()
        keep = (ext < t0) | (dec > t1 + embargo)
        keep[test] = False
        out.append((np.flatnonzero(keep), np.sort(test)))
    return out


def _fit(X: np.ndarray, y: np.ndarray) -> Any:
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True), StandardScaler(),
                         LogisticRegression(C=0.5, max_iter=500)).fit(X, y)


def cross_fit_meta(table: pd.DataFrame, feature_cols: Sequence[str], *, k: int = 5,
                   embargo: pd.Timedelta = pd.Timedelta(days=1)) -> dict[str, Any]:
    """Out-of-fold P(net R > 0) for a family's table (rows from its arms), scored per arm (n, Brier, AUC on OOF rows
    only), plus the final model's parameters fitted on every row. A fold without MIN_FIT_ROWS purged rows of both
    classes leaves its rows unscored."""
    from sklearn.metrics import brier_score_loss, roc_auc_score
    cols = list(feature_cols)
    _check_width(cols)
    tab = table.sort_values("decision_utc").reset_index(drop=True)
    X = tab[cols].to_numpy(dtype=float, copy=True)
    X[~np.isfinite(X)] = np.nan
    y = (tab["r_net"].to_numpy(dtype=float) > 0).astype(int)
    oof = np.full(len(tab), np.nan)
    for train, test in purged_folds(pd.DatetimeIndex(tab["decision_utc"]), pd.DatetimeIndex(tab["exit_utc"]),
                                    k=k, embargo=embargo):
        if len(train) >= MIN_FIT_ROWS and len(set(y[train])) == 2:
            oof[test] = _fit(X[train], y[train]).predict_proba(X[test])[:, 1]
    per_arm: dict[str, dict[str, Any]] = {}
    for arm, idx in tab.groupby("arm_id").indices.items():
        ok = idx[np.isfinite(oof[idx])]
        s: dict[str, Any] = {"n": int(len(idx)), "n_scored": int(len(ok)), "brier": None, "auc": None}
        if len(ok):
            s["brier"] = float(brier_score_loss(y[ok], oof[ok]))
            if len(set(y[ok])) == 2:
                s["auc"] = float(roc_auc_score(y[ok], oof[ok]))
        per_arm[str(arm)] = s
    model: dict[str, Any] = {"features": cols, "coef": [], "intercept": None, "n": int(len(tab))}
    if len(tab) >= MIN_FIT_ROWS and len(set(y)) == 2:
        pipe = _fit(X, y)
        imp, sc, lr = pipe.steps[0][1], pipe.steps[1][1], pipe.steps[2][1]
        model.update(coef=[float(v) for v in lr.coef_[0]], intercept=float(lr.intercept_[0]),
                     medians=[float(v) for v in imp.statistics_], means=[float(v) for v in sc.mean_],
                     scales=[float(v) for v in sc.scale_])
    return {"per_arm": per_arm, "model": model}


# ---------------------------------------------------------------------------------------------- the bandit
class ArmPrior(FrozenRecord):
    """A backtest's belief about an arm's mean net R per trade, worth `n_eff` live trades (capped at MAX_PRIOR_N)."""
    mean_r: float
    sd_r: float = PRIOR_SD_R
    n_eff: float = 2.0


def _cap(shares: Mapping[str, float], ceilings: Mapping[str, float] | None) -> dict[str, float]:
    c = ceilings or {}
    return {a: float(min(max(v, 0.0), 1.0, max(float(c.get(a, 1.0)), 0.0))) for a, v in shares.items()}


def thompson_shares(r_by_arm: Mapping[str, Sequence[float]], *, priors: Mapping[str, ArmPrior] | None = None,
                    ceilings: Mapping[str, float] | None = None, draws: int = THOMPSON_DRAWS,
                    seed: int = 0) -> dict[str, float]:
    """Each arm's share = P(its mean net R is the highest), from Normal posteriors on the mean (prior mean 0 worth 2
    trades unless a backtest prior is given), then capped at the arm's ceiling. Shares sum to at most 1 and a cap
    never moves budget to another arm."""
    arms = sorted(r_by_arm)
    if not arms:
        return {}
    rng = np.random.default_rng(seed)
    samples = np.empty((draws, len(arms)))
    for j, a in enumerate(arms):
        r = np.asarray([v for v in r_by_arm[a] if math.isfinite(v)], dtype=float)
        p = (priors or {}).get(a) or ArmPrior(mean_r=0.0)
        n0 = min(max(p.n_eff, 0.5), MAX_PRIOR_N)
        sd = max(float(np.std(r, ddof=1)) if len(r) >= 3 else p.sd_r, MIN_SD_R)
        mu = (n0 * p.mean_r + r.sum()) / (n0 + len(r))
        samples[:, j] = rng.normal(mu, sd / math.sqrt(n0 + len(r)), size=draws)
    best = np.bincount(samples.argmax(axis=1), minlength=len(arms)) / draws
    return _cap({a: float(best[j]) for j, a in enumerate(arms)}, ceilings)


def write_weights(db: Database, run_ns: int, shares: Mapping[str, float],
                  detail: Mapping[str, Mapping[str, Any]] | None = None) -> None:
    with db.write() as conn:
        conn.executemany("INSERT OR REPLACE INTO exploration_weights (run_ns, arm_id, weight, detail) VALUES (?, ?, ?, ?)",
                         [(int(run_ns), a, float(w), json.dumps(dict((detail or {}).get(a, {})), default=str))
                          for a, w in shares.items()])


def equal_shares(arms: Sequence[str], ceilings: Mapping[str, float] | None = None) -> dict[str, float]:
    arms = list(dict.fromkeys(arms))
    return _cap({a: 1.0 / len(arms) for a in arms}, ceilings) if arms else {}


def weights(db: Database, arms: Sequence[str], *, ceilings: Mapping[str, float] | None = None,
            now: pd.Timestamp | None = None, max_age: pd.Timedelta = MAX_WEIGHTS_AGE) -> dict[str, float]:
    """The newest run's share per arm, each capped at its ceiling: the fraction of the configured exploration budget
    an arm may use. Equal shares when there is no run, it is older than `max_age` at `now`, it does not score every
    arm in `arms`, a share is outside [0, 1], the shares sum above 1, or aux.db is unreadable."""
    want = list(dict.fromkeys(arms))
    fallback = equal_shares(want, ceilings)
    if not want:
        return {}
    try:
        with db.connection() as conn:
            r = conn.execute("SELECT MAX(run_ns) FROM exploration_weights").fetchone()
            if r is None or r[0] is None:
                return fallback
            run = int(r[0])
            got = {str(a): float(w) for a, w in
                   conn.execute("SELECT arm_id, weight FROM exploration_weights WHERE run_ns = ?", (run,))}
    except (sqlite3.Error, StateDbError, OSError, ValueError, TypeError) as exc:
        log.warning("exploration weights unreadable (%s): equal shares", type(exc).__name__)
        return fallback
    if now is not None and pd.Timestamp(run, unit="ns", tz="UTC") + max_age < now:
        log.warning("exploration weights from run %s are stale: equal shares", run)
        return fallback
    if any(a not in got for a in want) or any(not (math.isfinite(v) and 0 <= v <= 1) for v in got.values()) \
            or sum(got.values()) > 1 + 1e-9:
        log.warning("exploration weights of run %s incomplete or corrupt: equal shares", run)
        return fallback
    return _cap({a: got[a] for a in want}, ceilings)


# ---------------------------------------------------------------------------------------------- the nightly run
FeaturesFor = Callable[[str, str, pd.Timestamp, pd.Timestamp], pd.DataFrame]


def learn(db: Database, features_for: FeaturesFor, *, as_of: pd.Timestamp,
          priors: Mapping[str, ArmPrior] | None = None, ceilings: Mapping[str, float] | None = None,
          arms: Sequence[str] = (), seed: int = 0, k: int = 5,
          embargo: pd.Timedelta = pd.Timedelta(days=1)) -> dict[str, Any]:
    """Retrain each family's meta-labeller on its arms' labelled rows (features from `features_for(family, tf, start,
    end)`, available_utc + feature columns) and write the bandit's shares for every arm seen or configured. One family
    failing leaves its meta scores out; the weights are still written from net R alone."""
    obs = read_obs(db, as_of=as_of)
    all_arms = sorted(set(obs["arm_id"]) | set(arms))
    if not all_arms:
        return {"arms": 0}
    meta: dict[str, Any] = {}
    for (family, tf), grp in obs.groupby(["family", "tf"]):
        key = f"{family}:{tf}"
        try:
            feats = features_for(str(family), str(tf), grp["decision_utc"].min() - pd.Timedelta(days=1), as_of)
            if feats.empty:
                meta[key] = {"error": "no features"}
                continue
            cols = select_features(feats)
            tables = [t for a in sorted(grp["arm_id"].unique())
                      if not (t := training_table(db, feats, arm_id=str(a), as_of=as_of, feature_cols=cols)).empty]
            table = pd.concat(tables, ignore_index=True) if tables else pd.DataFrame()
            if table.empty:
                meta[key] = {"error": "no labelled rows"}
                continue
            meta[key] = cross_fit_meta(table[(table["family"] == family) & (table["tf"] == tf)], cols, k=k,
                                       embargo=embargo)
        except Exception as exc:       # noqa: BLE001 - one family's features must not stop the weights
            log.exception("exploration meta-labeller for %s failed", key)
            meta[key] = {"error": type(exc).__name__}
    r_by_arm = {a: obs.loc[obs["arm_id"] == a, "r_net"].tolist() for a in all_arms}
    shares = thompson_shares(r_by_arm, priors=priors, ceilings=ceilings, seed=seed)
    detail: dict[str, dict[str, Any]] = {}
    for a in all_arms:
        rs = [v for v in r_by_arm[a] if math.isfinite(v)]
        scored = [m for m in meta.values() if a in m.get("per_arm", {})]
        detail[a] = {"n": len(rs), "mean_r": float(np.mean(rs)) if rs else None,
                     "prior": (priors or {})[a].model_dump() if a in (priors or {}) else None,
                     "ceiling": (ceilings or {}).get(a), "meta": scored[0]["per_arm"][a] if scored else None,
                     "model": scored[0]["model"] if scored else None, "method": "thompson_normal_mean_net_r"}
    write_weights(db, _ns(as_of), shares, detail)
    return {"arms": len(all_arms), "weights": shares, "meta": meta}


__all__ = ["MAX_FEATURES", "MAX_WEIGHTS_AGE", "ArmPrior", "cross_fit_meta", "equal_shares", "learn", "purged_folds",
           "r_multiple", "read_obs", "record_obs", "select_features", "thompson_shares", "training_table", "weights",
           "write_weights"]
