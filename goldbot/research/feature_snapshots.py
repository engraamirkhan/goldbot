"""Point-in-time feature snapshots of the exploration candidates (ML review 2026-11-13, improvement B,
docs/research/ml-review-2026-11-13.md; ADR 0080). aux.db migration 30.

"None of the 19,990 logged candidates, each with a propensity and a replay net-R label, carries the features the
engine saw ... Today it is the main reason the 'learning loop' learns nothing." This module gives every gold
candidate the engine's default feature row (`research.pipeline.DEFAULT_FEATURE_NAMES` plus the h1/h4/d1 context
columns, the frame `engine.frames.frame` builds) at its decision, so a later model can learn from our own broker's
fills. It never chooses features for a model: a model reads at most 40 of these columns (`research.model`).

Design rules:
- Off the engine's hot path (engine/frames.py: the features are about two thirds of a 15m close's pass, and a book
  without agents, every exploration host, never builds them). The nightly job rebuilds each candidate's frame from the
  account's stored minutes, never in the tick. Nothing in goldbot/engine reads or writes these tables, so a snapshot
  never touches a decision.
- Point in time by WRITE time (quant review of a8c3a6d7): a minute counts at a decision only if it was in the store by
  then. Its time is the later of its nominal close (`visible_at`) and its write time (`available_utc`, stamped by the
  engine's minute writer and the broker-M1 reconcile since 2026-11-13). Feeds are resolved per decision
  (`data.account_bars.minute_versions` / `as_of_minutes`): a minute revised after the decision is read from the next
  feed that had it then, or not at all. A quarantined minute is read by no snapshot (a removal, never a value from the
  future). Where a broker feed has no minute, the release minutes the engine loads after a restart are used.
- The guarantee, precisely: no snapshot reads a minute whose stored write time is after its decision. A minute with no
  stored write time (written before 2026-11-13, or a release minute) is taken at its nominal close, which can be
  earlier than when it really reached the store; such a snapshot is marked `pit = nominal` with the count of such
  minutes, and research that needs strict point in time reads `pit = written` rows only. The frame rule is the
  engine's (tests hold the row equal to `engine.frames.frame` on the same minutes).
- Bounded and fair: at most `snapshot_max_decisions` decision bars a night. With per-timeframe quotas (`TF_QUOTAS`,
  label unlock 2026-11-25: the shared cap left the slow timeframes, where the judged arms are, behind a 1m backlog)
  each timeframe gets its own quota, the slowest first, each shared round-robin between the accounts, newest first
  within each, so one account's or one timeframe's backlog never starves another. Candidates on one decision bar
  (account, timeframe, decision minute) are snapshotted once, at the earliest decision (never later than any of them).
  A night stops at its deadline (`snapshot_deadline`: SNAPSHOT_BUDGET, and never into the 00:45-04:05 UTC research
  window); the rest waits for the next night. An account with no bars, or not in
  accounts.yaml, is recorded with a retry date (ACCOUNT_RETRY_AFTER) and skipped until then. A decision with too few
  bars is retried on later nights (MAX_ATTEMPTS, at most once per RETRY_AFTER), then recorded as insufficient_bars.

Acceptance (tests/test_feature_snapshots.py, tests/test_feature_snapshots_pit.py): recomputing each stored row from
the minutes known at its decision gives 0 mismatches, and a model on shuffled labels reads an AUC of 0.50 +- 0.02.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from collections.abc import Callable, Mapping
from typing import Any

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.data import holdout_guard
from goldbot.data.account_bars import as_of_minutes, minute_versions
from goldbot.data.resample import mid, resample_bars
from goldbot.data.timeutil import epoch_ns
from goldbot.db.connection import Database, now_ns
from goldbot.features import build_features
from goldbot.features.mtf import TF_LABEL, context_tfs, merge_higher_tf
from goldbot.research.model import MAX_FEATURES, check_feature_cap
from goldbot.research.pipeline import CONTEXT_FEATURES, DEFAULT_FEATURE_NAMES

log = logging.getLogger("goldbot.feature_snapshots")

ENGINE_MINUTES = 90_000        # the engine's memory (EngineConfig.max_bars_in_memory), tests hold them equal
FEATURE_WINDOW = 800           # decision bars the features are built on (EngineConfig.feature_window)
MIN_BARS = 120                 # engine/frames.py MIN_BARS: fewer decision bars, no frame
MIN_CONTEXT_BARS = 30          # Engine._context: fewer context bars, no context columns
READ_DAYS = 100                # calendar days of minutes read before the earliest decision (>= ENGINE_MINUTES)
MAX_ATTEMPTS = 3               # nights a too-short history is retried before insufficient_bars is recorded
RETRY_AFTER = pd.Timedelta(hours=12)              # at most one attempt per candidate a night
ACCOUNT_RETRY_AFTER = pd.Timedelta(days=1)        # an account with no bars / not configured: tried again after this
MINUTE_NS = 60_000_000_000
GOLD = "XAUUSD"
# decision bars a night per timeframe, slowest first (label unlock 2026-11-25; the nine timeframes of
# self_retrain.CELL_TFS). 950 a night at 0.46 s median each (brain, measured 2026-11-25) is about 7-8 min
TF_QUOTAS: dict[str, int] = {"1w": 0, "1d": 0, "4h": 100, "2h": 100, "1h": 150, "30m": 100, "15m": 200, "5m": 150,
                             "1m": 150}
# 1d / 1w have quota 0 (skipped): ENGINE_MINUTES (about 65 trading days) never holds MIN_BARS daily bars (a closed-HTF-bar store
# would seed them, W3-3). The quotas sum to 950; `scaled_quotas` fits them to the configured nightly cap
DEFAULT_TF_QUOTA = 50          # a timeframe not named above
SNAPSHOT_BUDGET = pd.Timedelta(minutes=25)
QUIET_FROM, QUIET_TO = pd.Timedelta(minutes=45), pd.Timedelta(hours=4, minutes=5)   # UTC time of day: research window
Row = tuple[str, str, "Snapshot | None", int, int, "str | None"]


class Snapshot(FrozenRecord):
    """One decision's default feature row, the latest bar visibility it used, and how many of the minutes it read
    carry no write time (`pit` nominal when any)."""
    features: dict[str, float | None]
    feature_version: str
    available_utc_max: UtcTimestamp
    nominal_minutes: int = 0

    @property
    def pit(self) -> str:
        return "nominal" if self.nominal_minutes else "written"


def vector_sha256(snap: Snapshot) -> str:
    """A vector's content address (`exploration_feature_vectors.sha256`): SHA-256 of its feature version and its
    values as sorted JSON. The engine's shadow scorer stores the same hash, so engine-vs-snapshot parity is a join."""
    body = json.dumps(snap.features, sort_keys=True)
    return hashlib.sha256(f"{snap.feature_version}|{body}".encode()).hexdigest()


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def decision_features(minutes: pd.DataFrame, tf: str, at: pd.Timestamp, *,
                      ctx: dict[str, Any] | None = None) -> Snapshot | None:
    """The engine's frame of `tf` at the decision `at` (engine/frames.py `frame` and `Engine._context`) from 1m bars
    with `visible_at`, reading only minutes visible by `at` (with a `rank` column, the stored versions of
    `minute_versions`, resolved as of `at`): its last row's default features plus context columns. None with fewer
    than MIN_BARS decision bars."""
    at = pd.Timestamp(at).tz_convert("UTC")
    if "rank" in minutes.columns:
        seen = as_of_minutes(minutes, at)
    else:
        seen = minutes[(pd.to_datetime(minutes["visible_at"], utc=True) <= at).to_numpy()]
    complete = seen.tail(ENGINE_MINUTES).reset_index(drop=True)
    if complete.empty:
        return None
    dec = resample_bars(complete, tf)
    dec = dec[pd.to_datetime(dec["visible_at"], utc=True) <= at].tail(FEATURE_WINDOW).reset_index(drop=True)
    if len(dec) < MIN_BARS:
        return None
    X = build_features(mid(dec), DEFAULT_FEATURE_NAMES, ctx=ctx)
    version = str(X.attrs.get("feature_version", ""))
    latest = pd.to_datetime(dec["visible_at"], utc=True).max()
    for ctf in context_tfs(tf):
        hb = resample_bars(complete, ctf)
        hb = hb[pd.to_datetime(hb["visible_at"], utc=True) <= at].reset_index(drop=True)
        if len(hb) < MIN_CONTEXT_BARS:
            continue
        X = merge_higher_tf(X, build_features(mid(hb), list(CONTEXT_FEATURES), ctx=ctx), hb, TF_LABEL[ctf])
        latest = max(latest, pd.to_datetime(hb["visible_at"], utc=True).max())
    row = X.drop(columns=[c for c in ("ts_utc", "visible_at") if c in X.columns]).iloc[-1]
    nominal = int((~complete["written"].astype(bool)).sum()) if "written" in complete.columns else len(complete)
    return Snapshot(features={str(k): _num(v) for k, v in row.items()}, feature_version=version,
                    available_utc_max=latest, nominal_minutes=nominal)


# ---------------------------------------------------------------------------------------------- the nightly job
def _gold(df: pd.DataFrame, instruments: Mapping[str, str] | None) -> pd.DataFrame:
    from goldbot.research import arms as arm_spec
    inst = arm_spec.arm_instruments() if instruments is None else instruments
    return df[df["arm_id"].map(lambda a: inst.get(str(a))) == GOLD]


def _pending(db: Database, as_of: pd.Timestamp, instruments: Mapping[str, str] | None) -> pd.DataFrame:
    """Gold candidates without a snapshot row and not attempted within RETRY_AFTER, newest first."""
    with db.connection() as conn:
        df = pd.read_sql_query(
            "SELECT o.candidate_id, o.account_id, o.arm_id, o.tf, o.decision_ns FROM exploration_obs o "
            "LEFT JOIN exploration_feature_snapshots s ON s.candidate_id = o.candidate_id "
            "LEFT JOIN exploration_snapshot_attempts a ON a.candidate_id = o.candidate_id "
            "WHERE s.candidate_id IS NULL AND (a.last_ns IS NULL OR a.last_ns <= ?) ORDER BY o.decision_ns DESC",
            conn, params=(int((as_of - RETRY_AFTER).value),))
    return df if df.empty else _gold(df, instruments)


def _fair(keys: pd.DataFrame, limit: int) -> pd.DataFrame:
    """At most `limit` decision bars, round-robin between the accounts (newest first within each)."""
    keys = keys.sort_values("decision_ns", ascending=False, kind="stable")
    keys = keys.assign(_turn=keys.groupby("account_id").cumcount())
    return keys.sort_values(["_turn", "account_id"], kind="stable").head(limit).drop(columns="_turn")


def _tf_seconds(tf: str) -> int:
    from goldbot.config import TF_SECONDS
    return int(TF_SECONDS.get(tf, {"2h": 7200}.get(tf, 0)))


def select_keys(keys: pd.DataFrame, *, quotas: Mapping[str, int] | None, limit: int) -> pd.DataFrame:
    """The night's decision bars: without `quotas` the newest `limit` shared between the accounts (`_fair`); with
    them each timeframe's quota (DEFAULT_TF_QUOTA when unnamed), the slowest timeframe first, each `_fair`, then at
    most `limit` in that order."""
    if quotas is None:
        return _fair(keys, limit)
    parts = [_fair(g, int(quotas.get(str(tf), DEFAULT_TF_QUOTA)))
             for tf, g in sorted(keys.groupby("tf", sort=False), key=lambda t: (-_tf_seconds(str(t[0])), str(t[0])))]
    parts = [p for p in parts if len(p)]          # a timeframe with quota 0 is skipped
    return pd.concat(parts, ignore_index=True).head(limit) if parts else keys.iloc[0:0]


def scaled_quotas(quotas: Mapping[str, int], cap: int) -> dict[str, int]:
    """`quotas` scaled down in proportion so they sum to at most `cap` (each at least 1, a 0 stays 0); unchanged when they fit. So a
    cap below the quotas' sum (settings snapshot_max_decisions 600) still serves every timeframe, never only the slow
    ones."""
    total = sum(quotas.values())
    if total <= cap:
        return dict(quotas)
    return {tf: q and max(1, int(q * cap // total)) for tf, q in quotas.items()}


def snapshot_deadline(start: pd.Timestamp) -> pd.Timestamp | None:
    """When a snapshot night started at `start` must stop: start + SNAPSHOT_BUDGET, never past the research window's
    start (00:45 UTC); None when `start` is inside that window (00:45-04:05 UTC: no snapshot work then)."""
    start = pd.Timestamp(start).tz_convert("UTC")
    day = start.normalize()
    if QUIET_FROM <= start - day < QUIET_TO:
        return None
    quiet = day + QUIET_FROM if start - day < QUIET_FROM else day + pd.Timedelta(days=1) + QUIET_FROM
    return min(start + SNAPSHOT_BUDGET, quiet)


def _deferred(db: Database, as_of: pd.Timestamp) -> set[str]:
    with db.connection() as conn:
        return {str(a) for (a,) in conn.execute(
            "SELECT account_id FROM exploration_snapshot_accounts WHERE retry_after_ns > ?", (int(as_of.value),))}


def _defer(db: Database, account: str, reason: str, as_of: pd.Timestamp) -> None:
    with db.write() as conn:
        conn.execute("INSERT OR REPLACE INTO exploration_snapshot_accounts (account_id, reason, retry_after_ns, "
                     "recorded_ns) VALUES (?, ?, ?, ?)",
                     (account, reason, int((as_of + ACCOUNT_RETRY_AFTER).value), now_ns()))


def _write(db: Database, rows: list[Row], as_of: pd.Timestamp) -> int:
    """(candidate_id, status, snapshot, decision_ns, bars_as_of_ns, detail) -> the tables, once each. A too-short
    history below MAX_ATTEMPTS only counts an attempt."""
    written = 0
    with db.write() as conn:
        for cid, status, snap, dec_ns, as_of_ns, detail in rows:
            if status == "insufficient_bars":
                conn.execute("INSERT INTO exploration_snapshot_attempts (candidate_id, attempts, last_ns) "
                             "VALUES (?, 1, ?) ON CONFLICT(candidate_id) DO UPDATE SET attempts = attempts + 1, "
                             "last_ns = excluded.last_ns", (cid, int(as_of.value)))
                n = conn.execute("SELECT attempts FROM exploration_snapshot_attempts WHERE candidate_id = ?",
                                 (cid,)).fetchone()[0]
                if n < MAX_ATTEMPTS:
                    continue
            sha = avail = pit = nominal = None
            if snap is not None:
                body = json.dumps(snap.features, sort_keys=True)
                sha = vector_sha256(snap)
                avail = int(epoch_ns(pd.DatetimeIndex([snap.available_utc_max]))[0])
                pit, nominal = snap.pit, snap.nominal_minutes
                conn.execute("INSERT OR IGNORE INTO exploration_feature_vectors (sha256, feature_version, n_columns, "
                             "features, recorded_ns) VALUES (?, ?, ?, ?, ?)",
                             (sha, snap.feature_version, len(snap.features), body, now_ns()))
            cur = conn.execute(
                "INSERT OR IGNORE INTO exploration_feature_snapshots (candidate_id, status, vector_sha256, "
                "decision_ns, available_utc_max_ns, bars_as_of_ns, pit, nominal_minutes, detail, recorded_ns) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (cid, status, sha, dec_ns, avail, as_of_ns, pit, nominal, detail, now_ns()))
            written += cur.rowcount
    return written


def snapshot_pending(db: Database, store: Any, *, broker_of: Mapping[str, str], symbol_of: Mapping[str, str],
                     as_of: pd.Timestamp, max_decisions: int = 600,
                     instruments: Mapping[str, str] | None = None, quotas: Mapping[str, int] | None = None,
                     deadline: pd.Timestamp | None = None,
                     clock: Callable[[], pd.Timestamp] | None = None) -> dict[str, Any]:
    """Snapshot the gold candidates that have none: at most `max_decisions` decision bars (per timeframe at most its
    `quotas` entry when given), shared between the accounts that may be read (`select_keys`); one snapshot per
    decision bar. Each account's stored minute versions over all its pending decisions are read once (visible by `as_of`)
    and resolved at each decision (`decision_features`). Stops at `deadline` (by `clock`, default now)."""
    as_of = pd.Timestamp(as_of).tz_convert("UTC")
    now = clock or (lambda: pd.Timestamp.now(tz="UTC"))
    out: dict[str, Any] = {"written": 0, "decisions": 0, "status": {}, "no_bars": [], "unknown_account": [],
                           "deferred": [], "stopped": None}
    todo = _pending(db, as_of, instruments)
    if todo.empty:
        return out
    waiting = _deferred(db, as_of)
    out["deferred"] = sorted(waiting & set(todo["account_id"].astype(str)))
    todo = todo[~todo["account_id"].astype(str).isin(waiting)]
    known = set(broker_of)          # the job passes every account's own store symbol (vantage: XAUUSD+)
    unknown = sorted(set(todo["account_id"].astype(str)) - known)
    for account in unknown:
        _defer(db, account, "unknown_account", as_of)
    out["unknown_account"] = unknown
    todo = todo[todo["account_id"].astype(str).isin(known)]
    status: dict[str, int] = {}
    # one key per decision bar: (account, tf, decision minute), at its earliest decision
    todo = todo.assign(_bar=todo["decision_ns"].astype("int64") // MINUTE_NS)
    bars = todo.groupby(["account_id", "tf", "_bar"])["decision_ns"].min().reset_index()
    # accounts with no bars are found before the batch is shared, so they never take a healthy account's turns. Each
    # account is read over the WHOLE range of its pending decisions before any is chosen (code review of #451: a
    # re-selection could hand an account decisions outside a narrower read, built from stale minutes and stored ok)
    minutes_of: dict[str, pd.DataFrame] = {}
    for key, part in todo.groupby("account_id", sort=True):
        account = str(key)
        first = pd.Timestamp(int(part["decision_ns"].min()), tz="UTC")
        last = pd.Timestamp(int(part["decision_ns"].max()), tz="UTC")
        # ADR holdout-lookback-warmup: holdout minutes are read only as lookback of a decision on/after HOLDOUT_END
        # (the engine's own memory); a decision inside the holdout is refused below
        minutes = minute_versions(store, account_id=account, broker=broker_of[account],
                                  symbol=symbol_of.get(account, GOLD), start=first - pd.Timedelta(days=READ_DAYS),
                                  end=last, as_of=as_of)
        if minutes.empty:
            out["no_bars"].append(account)
            _defer(db, account, "no_bars", as_of)
            log.warning("no 1m bars for %s: its candidates get feature snapshots after %s", account,
                        as_of + ACCOUNT_RETRY_AFTER)
            continue
        minutes_of[account] = minutes
    bars = bars[bars["account_id"].astype(str).isin(set(minutes_of))]
    keys = select_keys(bars[["account_id", "tf", "decision_ns", "_bar"]], quotas=quotas, limit=max_decisions)
    for account, tf, dec_ns, bar in zip(keys["account_id"].astype(str), keys["tf"].astype(str),
                                        keys["decision_ns"].astype("int64"), keys["_bar"].astype("int64"),
                                        strict=True):
        if deadline is not None and now() >= deadline:
            out["stopped"] = "deadline"
            break
        dec_ns = int(dec_ns)
        group = todo[(todo["account_id"] == account) & (todo["tf"] == tf) & (todo["_bar"] == int(bar))]
        at = pd.Timestamp(dec_ns, tz="UTC")
        snap: Snapshot | None = None
        detail: str | None = None
        try:
            holdout_guard.refuse(pd.DatetimeIndex([at]), what="feature snapshot decision")   # recorded as error
            snap = decision_features(minutes_of[account], tf, at)
            st = "ok" if snap is not None else "insufficient_bars"
        except Exception as exc:   # noqa: BLE001 - one bar's failure is recorded; the others go on
            log.exception("feature snapshot of %s %s at %s failed", account, tf, at)
            st, detail = "error", type(exc).__name__
        out["decisions"] += 1
        rows: list[Row] = [(str(cid), st, snap, int(d), int(as_of.value), detail)
                           for cid, d in zip(group["candidate_id"], group["decision_ns"], strict=True)]
        status[st] = status.get(st, 0) + len(rows)
        out["written"] += _write(db, rows, as_of)
    if minutes_of:
        with db.write() as conn:      # read again: no longer waiting
            conn.executemany("DELETE FROM exploration_snapshot_accounts WHERE account_id = ?",
                             [(a,) for a in minutes_of])
    out["status"] = status
    return out


def read_snapshots(db: Database, *, pit: str | None = None) -> pd.DataFrame:
    """Every candidate's stored vector (status ok; only `pit` written or nominal rows when given), one column per
    feature, indexed by candidate id."""
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT s.candidate_id, v.features FROM exploration_feature_snapshots s "
            "JOIN exploration_feature_vectors v ON v.sha256 = s.vector_sha256 WHERE s.status = 'ok' "
            "AND (? IS NULL OR s.pit = ?)", (pit, pit)).fetchall()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([json.loads(f) for _, f in rows], index=pd.Index([c for c, _ in rows], name="candidate_id"),
                        dtype=float)


def snapshot_pit(db: Database, candidate_id: str) -> tuple[str | None, int | None]:
    """(pit, nominal_minutes) of a candidate's snapshot; (None, None) without one."""
    with db.connection() as conn:
        r = conn.execute("SELECT pit, nominal_minutes FROM exploration_feature_snapshots WHERE candidate_id = ?",
                         (candidate_id,)).fetchone()
    return (None, None) if r is None else (r[0], r[1])


def coverage(db: Database, *, since: pd.Timestamp, instruments: Mapping[str, str] | None = None) -> dict[str, Any]:
    """GOLD candidates decided since `since`, how many have a snapshot row, how many carry features and how many of
    those are strictly point in time (`pit` written): the review's "snapshot coverage ratio", reported nightly
    (memory verify-learning-outputs: show counts, never schedules)."""
    cut = int(pd.Timestamp(since).tz_convert("UTC").value)
    with db.connection() as conn:
        df = pd.read_sql_query(
            "SELECT o.arm_id, s.status, s.pit FROM exploration_obs o LEFT JOIN exploration_feature_snapshots s "
            "ON s.candidate_id = o.candidate_id WHERE o.decision_ns >= ?", conn, params=(cut,))
    df = _gold(df, instruments) if not df.empty else df
    n = len(df)
    ok = int((df["status"] == "ok").sum()) if n else 0
    return {"candidates": n, "snapshots": int(df["status"].notna().sum()) if n else 0, "with_features": ok,
            "written_pit": int((df["pit"] == "written").sum()) if n else 0, "ratio": ok / n if n else None}


# ---------------------------------------------------------------------------------------------- sanity
def label_auc(X: pd.DataFrame, y: np.ndarray, *, columns: list[str] | None = None, folds: int = 5,
              seed: int = 0) -> float:
    """Out-of-fold AUC of a small LightGBM on `columns` (default: the first MAX_FEATURES numeric columns; never more
    than 40, the live-model cap) over contiguous folds (rows in decision order). A sanity reading, not a trial."""
    from lightgbm import LGBMClassifier
    from sklearn.metrics import roc_auc_score
    cols = list(columns) if columns is not None else list(X.select_dtypes("number").columns[:MAX_FEATURES])
    check_feature_cap(cols)
    data = X[cols].to_numpy(dtype=float)
    y = np.asarray(y).astype(int)
    oof = np.full(len(y), np.nan)
    for test in np.array_split(np.arange(len(y)), folds):
        train = np.setdiff1d(np.arange(len(y)), test)
        if len(np.unique(y[train])) < 2:
            continue
        model = LGBMClassifier(n_estimators=60, learning_rate=0.05, num_leaves=15, min_child_samples=20,
                               random_state=seed, n_jobs=1, verbose=-1)
        model.fit(data[train], y[train])
        oof[test] = np.asarray(model.predict_proba(data[test]))[:, 1]
    done = ~np.isnan(oof)
    return float(roc_auc_score(y[done], oof[done]))


def shuffled_label_auc(X: pd.DataFrame, y: np.ndarray, *, seed: int = 0, columns: list[str] | None = None) -> float:
    """`label_auc` on `y` permuted: about 0.50 when nothing in the pipeline leaks the label (review B)."""
    return label_auc(X, np.random.default_rng(seed).permutation(np.asarray(y)), columns=columns, seed=seed)


__all__ = ["ACCOUNT_RETRY_AFTER", "ENGINE_MINUTES", "FEATURE_WINDOW", "MAX_ATTEMPTS", "SNAPSHOT_BUDGET", "TF_QUOTAS",
           "scaled_quotas",
           "Snapshot", "coverage", "decision_features", "label_auc", "read_snapshots", "select_keys",
           "shuffled_label_auc", "snapshot_deadline", "snapshot_pending", "snapshot_pit"]
