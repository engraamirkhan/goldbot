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
from collections.abc import Mapping
from typing import Any

import pandas as pd

from goldbot.data.account_bars import as_of_minutes, minute_versions
from goldbot.db.connection import Database

log = logging.getLogger("goldbot.snapshot_audit")

AUDIT_WINDOW = pd.Timedelta(hours=24)
GOLD = "XAUUSD"


def nominal_candidates(db: Database, since: pd.Timestamp) -> pd.DataFrame:
    """Candidates with an ok nominal snapshot decided at or after `since`: candidate_id, account_id, broker, decision."""
    with db.connection() as conn:
        df = pd.read_sql_query(
            "SELECT o.candidate_id, o.account_id, o.broker, s.decision_ns FROM exploration_feature_snapshots s "
            "JOIN exploration_obs o ON o.candidate_id = s.candidate_id "
            "WHERE s.status = 'ok' AND s.pit = 'nominal' AND s.decision_ns >= ?", conn,
            params=(int(pd.Timestamp(since).tz_convert("UTC").value),))
    df["decision_utc"] = pd.to_datetime(df.pop("decision_ns"), unit="ns", utc=True)
    return df


def audit(db: Database, store: Any, *, since: pd.Timestamp,
          symbol_of: Mapping[str, str] | None = None) -> dict[str, Any]:
    """{"clean": ids, "excluded": ids, "unread": ids, "counts": {...}}: per admitted nominal snapshot, whether every
    minute visible at its decision in the last AUDIT_WINDOW carries a write stamp. An account whose minutes cannot be
    read leaves its candidates `unread` (never clean)."""
    cands = nominal_candidates(db, since)
    clean: set[str] = set()
    excluded: set[str] = set()
    unread: set[str] = set()
    for (account, broker), g in cands.groupby(["account_id", "broker"], dropna=False):
        if broker is None or (isinstance(broker, float) and pd.isna(broker)):
            unread.update(g["candidate_id"].astype(str))
            continue
        lo, hi = g["decision_utc"].min() - AUDIT_WINDOW, g["decision_utc"].max()
        try:
            versions = minute_versions(store, account_id=str(account), broker=str(broker),
                                       symbol=(symbol_of or {}).get(str(account), GOLD), start=lo,
                                       end=hi + pd.Timedelta(minutes=1), as_of=hi)
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
    return {"clean": clean, "excluded": excluded, "unread": unread,
            "counts": {"nominal": int(len(cands)), "clean": len(clean), "excluded": len(excluded),
                       "unread": len(unread)}}
