"""Point-in-time feature snapshots of the exploration candidates (ML review 2026-11-13, improvement B,
docs/research/ml-review-2026-11-13.md; ADR 0080). aux.db migration 30.

"None of the 19,990 logged candidates, each with a propensity and a replay net-R label, carries the features the
engine saw ... Today it is the main reason the 'learning loop' learns nothing." This module gives every gold
candidate the engine's default feature row (`research.pipeline.DEFAULT_FEATURE_NAMES` plus the h1/h4/d1 context
columns, the frame `engine.frames.frame` builds) at its decision, so a later model can learn from our own broker's
fills. It never chooses features for a model: a model reads at most 40 of these columns (`research.model`).

Design rules:
- Off the engine's hot path (engine/frames.py: the features are about two thirds of a 15m close's pass, and a book
  without agents, every exploration host, never builds them). The nightly job computes each candidate's row from the
  account's own stored broker minutes (`data.account_bars.account_bars_1m`), never in the tick. Nothing in
  goldbot/engine reads or writes these tables, so a snapshot never touches a decision.
- Point in time: a minute counts only if it was visible by the decision (`visible_at`, and `available_utc` when stored,
  the as-of rule of every join), and the frame is the engine's: the last ENGINE_MINUTES minutes, resampled, the last
  FEATURE_WINDOW decision bars visible at the decision, context bars visible then. Of several stored versions of a
  minute the last one visible by the job's read is kept, so a minute corrected after the decision is left out rather
  than read early (conservative, never a look-ahead). `available_utc_max` (the latest bar used) <= the decision is a
  row invariant SQLite enforces.
- Bounded: at most `snapshot_max_decisions` decision bars a night, newest first, so the day's candidates are covered
  and the backlog fills over later nights. Every outcome is written once (ok, insufficient_bars, error) and never
  retried; an account with no bars at all writes nothing and is tried again the next night.

Acceptance (tests/test_feature_snapshots.py): recomputing each stored row from the bars cut at its decision gives 0
mismatches, and a model on shuffled labels reads an AUC of 0.50 +- 0.02 (`shuffled_label_auc`).
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.data.account_bars import account_bars_1m
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
GOLD = "XAUUSD"


class Snapshot(FrozenRecord):
    """One decision's default feature row and the latest bar visibility it used."""
    features: dict[str, float | None]
    feature_version: str
    available_utc_max: UtcTimestamp


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def decision_features(minutes: pd.DataFrame, tf: str, at: pd.Timestamp, *,
                      ctx: dict[str, Any] | None = None) -> Snapshot | None:
    """The engine's frame of `tf` at the decision `at` (engine/frames.py `frame` and `Engine._context`) from 1m bars
    with `visible_at`, reading only minutes visible by `at`: its last row's default features plus context columns.
    None with fewer than MIN_BARS decision bars."""
    at = pd.Timestamp(at).tz_convert("UTC")
    vis = pd.to_datetime(minutes["visible_at"], utc=True)
    complete = minutes[(vis <= at).to_numpy()].tail(ENGINE_MINUTES).reset_index(drop=True)
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
    return Snapshot(features={str(k): _num(v) for k, v in row.items()}, feature_version=version,
                    available_utc_max=latest)


# ---------------------------------------------------------------------------------------------- the nightly job
def _pending(db: Database, limit_decisions: int, instruments: Mapping[str, str] | None) -> pd.DataFrame:
    """Candidates without a snapshot row, of the newest `limit_decisions` distinct (account, tf, decision) bars."""
    from goldbot.research import arms as arm_spec
    with db.connection() as conn:
        df = pd.read_sql_query(
            "SELECT o.candidate_id, o.account_id, o.arm_id, o.tf, o.decision_ns FROM exploration_obs o "
            "LEFT JOIN exploration_feature_snapshots s ON s.candidate_id = o.candidate_id "
            "WHERE s.candidate_id IS NULL ORDER BY o.decision_ns DESC", conn)
    if df.empty:
        return df
    inst = arm_spec.arm_instruments() if instruments is None else instruments
    df = df[df["arm_id"].map(lambda a: inst.get(str(a))) == GOLD]
    keys = df[["account_id", "tf", "decision_ns"]].drop_duplicates().head(limit_decisions)
    return df.merge(keys, on=["account_id", "tf", "decision_ns"])


def _write(db: Database, rows: list[tuple[str, str, Snapshot | None, int, int, str | None]]) -> int:
    """(candidate_id, status, snapshot, decision_ns, bars_as_of_ns, detail) -> both tables, once each."""
    written = 0
    with db.write() as conn:
        for cid, status, snap, dec_ns, as_of_ns, detail in rows:
            sha = avail = None
            if snap is not None:
                body = json.dumps(snap.features, sort_keys=True)
                sha = hashlib.sha256(f"{snap.feature_version}|{body}".encode()).hexdigest()
                avail = int(epoch_ns(pd.DatetimeIndex([snap.available_utc_max]))[0])
                conn.execute("INSERT OR IGNORE INTO exploration_feature_vectors (sha256, feature_version, n_columns, "
                             "features, recorded_ns) VALUES (?, ?, ?, ?, ?)",
                             (sha, snap.feature_version, len(snap.features), body, now_ns()))
            cur = conn.execute(
                "INSERT OR IGNORE INTO exploration_feature_snapshots (candidate_id, status, vector_sha256, "
                "decision_ns, available_utc_max_ns, bars_as_of_ns, detail, recorded_ns) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (cid, status, sha, dec_ns, avail, as_of_ns, detail, now_ns()))
            written += cur.rowcount
    return written


def snapshot_pending(db: Database, store: Any, *, broker_of: Mapping[str, str], symbol_of: Mapping[str, str],
                     as_of: pd.Timestamp, max_decisions: int = 600,
                     instruments: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Snapshot the gold candidates that have none, newest decision bars first, at most `max_decisions` bars. Each
    account's minutes are read once (visible by `as_of`) and cut at each decision (`decision_features`). A candidate
    of an account not in `broker_of` is left for a later night."""
    as_of = pd.Timestamp(as_of).tz_convert("UTC")
    todo = _pending(db, max_decisions, instruments)
    out: dict[str, Any] = {"written": 0, "decisions": 0, "status": {}, "no_bars": [], "unknown_account": []}
    if todo.empty:
        return out
    status: dict[str, int] = {}
    for account, part in todo.groupby("account_id", sort=True):
        account = str(account)
        if account not in broker_of:
            out["unknown_account"].append(account)
            continue
        first = pd.Timestamp(int(part["decision_ns"].min()), tz="UTC")
        last = pd.Timestamp(int(part["decision_ns"].max()), tz="UTC")
        minutes = account_bars_1m(store, account_id=account, broker=broker_of[account],
                                  symbol=symbol_of.get(account, GOLD), start=first - pd.Timedelta(days=READ_DAYS),
                                  end=last, as_of=as_of, meta=True)
        if minutes.empty:
            out["no_bars"].append(account)
            log.warning("no 1m bars for %s: its candidates get feature snapshots another night", account)
            continue
        rows: list[tuple[str, str, Snapshot | None, int, int, str | None]] = []
        for tf, dec_ns in sorted({(str(t), int(d)) for t, d in zip(part["tf"], part["decision_ns"], strict=True)}):
            group = part[(part["tf"] == tf) & (part["decision_ns"] == dec_ns)]
            at = pd.Timestamp(dec_ns, tz="UTC")
            snap: Snapshot | None = None
            detail: str | None = None
            try:
                snap = decision_features(minutes, tf, at)
                st = "ok" if snap is not None else "insufficient_bars"
            except Exception as exc:   # noqa: BLE001 - one bar's failure is recorded; the others go on
                log.exception("feature snapshot of %s %s at %s failed", account, tf, at)
                st, detail = "error", type(exc).__name__
            out["decisions"] += 1
            for cid in group["candidate_id"]:
                rows.append((str(cid), st, snap, dec_ns, int(as_of.value), detail))
                status[st] = status.get(st, 0) + 1
        out["written"] += _write(db, rows)
    out["status"] = status
    return out


def read_snapshots(db: Database) -> pd.DataFrame:
    """Every candidate's stored vector (status ok), one column per feature, indexed by candidate id."""
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT s.candidate_id, v.features FROM exploration_feature_snapshots s "
            "JOIN exploration_feature_vectors v ON v.sha256 = s.vector_sha256 WHERE s.status = 'ok'").fetchall()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([json.loads(f) for _, f in rows], index=pd.Index([c for c, _ in rows], name="candidate_id"),
                        dtype=float)


def coverage(db: Database, *, since: pd.Timestamp) -> dict[str, Any]:
    """Candidates decided since `since`, how many have a snapshot row and how many carry features (the review's
    "snapshot coverage ratio", reported nightly; memory verify-learning-outputs: show counts, never schedules)."""
    cut = int(pd.Timestamp(since).tz_convert("UTC").value)
    with db.connection() as conn:
        n, snaps, ok = conn.execute(
            "SELECT COUNT(*), COUNT(s.candidate_id), SUM(CASE WHEN s.status = 'ok' THEN 1 ELSE 0 END) "
            "FROM exploration_obs o LEFT JOIN exploration_feature_snapshots s ON s.candidate_id = o.candidate_id "
            "WHERE o.decision_ns >= ?", (cut,)).fetchone()
    ok = int(ok or 0)
    return {"candidates": int(n), "snapshots": int(snaps), "with_features": ok, "ratio": ok / n if n else None}


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


__all__ = ["ENGINE_MINUTES", "FEATURE_WINDOW", "Snapshot", "coverage", "decision_features", "label_auc",
           "read_snapshots", "shuffled_label_auc", "snapshot_pending"]
