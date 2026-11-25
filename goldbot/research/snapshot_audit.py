"""Audit of nominal-pit feature snapshots for the self-retrain (quant S5, review of PR #434; card
docs/research/prereg-2026-11-24-self-retrain.md section 2).

A snapshot is `pit = nominal` when any of the up to 90,000 minutes it read carries no write stamp. Release minutes (the
reference feed the engine loads after a restart) never get one, so "every minute of the last day is stamped" cannot be
assumed from the date alone. This audit checks it per candidate: an admitted nominal snapshot (decided on/after
`self_retrain.NOMINAL_FROM`) is clean only when every minute visible at its decision in [decision - AUDIT_WINDOW,
decision] carries its write time; otherwise its candidate id is excluded. The weekly job runs it in memory before the
trainer reads; `scripts/research/audit_nominal_snapshots.py` runs it once by hand and writes the excluded ids.
Reads only (aux.db, the store's minutes).
"""
from __future__ import annotations

import logging
from collections.abc import Collection, Mapping
from typing import Any

import pandas as pd

from goldbot.data import holdout_guard
from goldbot.data.account_bars import as_of_minutes, minute_versions
from goldbot.db.connection import Database
from goldbot.research.feature_snapshots import ENGINE_MINUTES, READ_DAYS

log = logging.getLogger("goldbot.snapshot_audit")

AUDIT_WINDOW = pd.Timedelta(hours=24)
STAMPS_FROM = pd.Timestamp("2026-11-13", tz="UTC")     # minute write stamps began (quant H2 of #451)
GOLD = "XAUUSD"


def nominal_candidates(db: Database, since: pd.Timestamp | None, until: pd.Timestamp | None = None) -> pd.DataFrame:
    """Candidates with an ok nominal snapshot decided in [since, until) (None: unbounded): candidate_id, account_id,
    broker, decision."""
    lo = -(2 ** 63) if since is None else int(pd.Timestamp(since).tz_convert("UTC").value)
    hi = 2 ** 63 - 1 if until is None else int(pd.Timestamp(until).tz_convert("UTC").value)
    with db.connection() as conn:
        df = pd.read_sql_query(
            "SELECT o.candidate_id, o.account_id, o.broker, s.decision_ns FROM exploration_feature_snapshots s "
            "JOIN exploration_obs o ON o.candidate_id = s.candidate_id "
            "WHERE s.status = 'ok' AND s.pit = 'nominal' AND s.decision_ns >= ? AND s.decision_ns < ?", conn,
            params=(lo, hi))
    df["decision_utc"] = pd.to_datetime(df.pop("decision_ns"), unit="ns", utc=True)
    return df


def audit(db: Database, store: Any, *, since: pd.Timestamp,
          symbol_of: Mapping[str, str] | None = None) -> dict[str, Any]:
    """{"clean": ids, "excluded": ids, "unread": ids, "counts": {...}}: per admitted nominal snapshot, whether every
    minute visible at its decision in the last AUDIT_WINDOW carries a write stamp. An account whose minutes cannot be
    read leaves its candidates `unread` (never clean). "rank3_beyond_24h": the clean ones that read a reference
    (rank-3, never stamped) minute stamped-era old, ts_utc in [STAMPS_FROM, decision - AUDIT_WINDOW) (quant H2 of
    #451: sr-v2's remaining exposure, counted every run; sr-v3 excludes them)."""
    cands = nominal_candidates(db, since)
    clean: set[str] = set()
    rank3: set[str] = set()
    excluded: set[str] = set()
    unread: set[str] = set()
    for (account, broker), g in cands.groupby(["account_id", "broker"], dropna=False):
        if broker is None or (isinstance(broker, float) and pd.isna(broker)):
            unread.update(g["candidate_id"].astype(str))
            continue
        lo, hi = min(g["decision_utc"].min() - AUDIT_WINDOW, STAMPS_FROM), g["decision_utc"].max()
        try:
            versions = minute_versions(store, account_id=str(account), broker=str(broker),
                                       symbol=(symbol_of or {}).get(str(account), GOLD), start=lo,
                                       end=hi + pd.Timedelta(minutes=1), as_of=hi)
            versions = holdout_guard.cut(versions, what=f"snapshot audit minutes of {account}")   # quant R10
        except Exception as exc:              # noqa: BLE001 - never clean on an unread store: excluded by default
            log.warning("snapshot audit: %s minutes unreadable (%s)", account, type(exc).__name__)
            unread.update(g["candidate_id"].astype(str))
            continue
        for cid, at in zip(g["candidate_id"].astype(str), g["decision_utc"], strict=True):
            m = as_of_minutes(versions, at) if len(versions) else versions
            win = m[(pd.to_datetime(m["ts_utc"], utc=True) >= at - AUDIT_WINDOW).to_numpy()] if len(m) else m
            if len(win) == 0 or not win["written"].astype(bool).all():
                excluded.add(cid)
            else:
                clean.add(cid)
                ts = pd.to_datetime(m["ts_utc"], utc=True)
                old = m[((ts >= STAMPS_FROM) & (ts < at - AUDIT_WINDOW)).to_numpy()]
                if len(old) and (old["rank"].astype(int) == 3).any():
                    rank3.add(cid)
    return {"clean": clean, "excluded": excluded, "unread": unread, "rank3_beyond_24h": rank3,
            "counts": {"nominal": int(len(cands)), "clean": len(clean), "excluded": len(excluded),
                       "unread": len(unread), "rank3_beyond_24h": len(rank3)}}


def live_feed_audit(db: Database, store: Any, *, before: pd.Timestamp, brokers: Collection[str],
                    symbol_of: Mapping[str, str]) -> dict[str, Any]:
    """sr-v3 early admission (docs/research/prereg-amendment-2026-11-25-sr-v3-snapshot-admission.md section 3): a
    nominal snapshot decided before `before` on a broker in `brokers` (those that passed the minute-revision audit) is
    clean only when every minute its snapshot read (the ENGINE_MINUTES window visible at its decision) came from the
    live feed (rank 0): no broker-M1 back-fill (often written after the decision) and no reference minute. Needs no write
    stamp. An account without a known symbol or with unreadable minutes leaves its candidates `unread` (never clean)."""
    cands = nominal_candidates(db, None, before)
    cands = cands[cands["broker"].astype(str).isin(set(brokers))] if len(cands) else cands
    clean: set[str] = set()
    excluded: set[str] = set()
    unread: set[str] = set()
    for (account, broker), g in cands.groupby(["account_id", "broker"], dropna=False):
        if str(account) not in symbol_of:
            unread.update(g["candidate_id"].astype(str))
            continue
        lo, hi = g["decision_utc"].min() - pd.Timedelta(days=READ_DAYS), g["decision_utc"].max()
        try:
            versions = minute_versions(store, account_id=str(account), broker=str(broker),
                                       symbol=symbol_of[str(account)], start=lo, end=hi + pd.Timedelta(minutes=1),
                                       as_of=hi)
            # ADR 0100: the ENGINE_MINUTES lookback may reach holdout minutes; the audit only counts feed ranks
        except Exception as exc:              # noqa: BLE001 - never clean on an unread store
            log.warning("live-feed audit: %s minutes unreadable (%s)", account, type(exc).__name__)
            unread.update(g["candidate_id"].astype(str))
            continue
        for cid, at in zip(g["candidate_id"].astype(str), g["decision_utc"], strict=True):
            # quant M1 (review of #451): every minute the snapshot read, its ENGINE_MINUTES window, not only the last day
            win = as_of_minutes(versions, at).tail(ENGINE_MINUTES) if len(versions) else versions
            (clean if len(win) and (win["rank"].astype(int) == 0).all() else excluded).add(cid)
    return {"clean": clean, "excluded": excluded, "unread": unread,
            "counts": {"nominal_early": int(len(cands)), "clean": len(clean), "excluded": len(excluded),
                       "unread": len(unread)}}
