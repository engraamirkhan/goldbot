"""Evidence exclusions (quant ruling 2026-10-26, blind; prereg-2026-10-25-high-volume.md Amendment 4).

"Trades whose decision window overlaps a recorded dq quarantine span are void as evidence (n, posterior, cost cells,
graduation); they still count toward H-61 stops and P&L. Decided blind, before any read; criterion v1; adds 0 to K."

aux.db `evidence_exclusions` (migration 19) is append-only: one row per excluded order id (an exploration candidate's
id is its order id, so a candidate and its trade share the row; a cost probe's client order id), with the scopes it
is void for, the reason, the dq span it rests on, the criterion version and when it was decided. A row is never
updated or deleted (the table's triggers refuse both); `record` inserts what is not there yet (idempotent).

Criterion v1 (`decide`), from the spans `goldbot.data.quarantine.record_span` wrote to dq_events (the account's own
tick source and its broker's bar source):
* an exploration candidate (taken or not) is void when its decision window [decision - the arm's lookback, decision]
  overlaps a span, or when it was decided at or after a span's start but before its engine reloaded the corrected
  bars (`reloaded`, per account; unknown = criterion one only). The lookback is the window the engine feeds the
  arm's rule, from the arm spec's timeframe (`lookback`), never a constant per arm;
* a scheduled cost probe (purpose `cost_measurement`) whose entry lies inside a span is void for the cost cells only.

What reads it (scope): the nightly learner's posterior and H-62 models (`posterior`), the n toward the 300 read
(`n`, `graduation`), the cost table's probe fills and the daily review's refit cells (`cost_cells`). What never does:
the H-61 stops, account P&L and the broker reconciliation. Nothing here reads or prints an R or a P&L (blinding).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.db.connection import Database

log = logging.getLogger("goldbot.evidence")

Scope = Literal["n", "posterior", "cost_cells", "graduation"]
SCOPES: tuple[Scope, ...] = ("n", "posterior", "cost_cells", "graduation")
CRITERION_VERSION = "v1"
EXPLORATION_SCOPE: tuple[Scope, ...] = SCOPES                  # Amendment 4: every evidence use
PROBE_SCOPE: tuple[Scope, ...] = ("cost_cells",)               # a scheduled probe feeds the cost cells only
SCHEDULED_PURPOSE = "cost_measurement"                         # cost_probe.ProbeRecord.purpose of a scheduled probe
TABLE = "evidence_exclusions"

Span = tuple[pd.Timestamp, pd.Timestamp, str]                  # [start, end) and its reference


class Exclusion(FrozenRecord):
    """One void order id (aux.db `evidence_exclusions`)."""
    client_order_id: str
    kind: Literal["exploration", "cost_probe"]
    account_id: str
    scope: tuple[Scope, ...] = Field(min_length=1)
    reason: str
    span_ref: str                                # the dq_events quarantine span it rests on
    criterion_version: str = CRITERION_VERSION
    decided_utc: UtcTimestamp
    blind: Literal[True] = True                  # decided before any read of the evidence it voids


def lookback(tf: str) -> pd.Timedelta:
    """The window the engine feeds an arm's rule on `tf` (engine/exploration.py `_bars`: LOOKBACK + 1 bars of the
    timeframe, and at least LOOKBACK_1M minutes): nothing older can enter a decision."""
    from goldbot.config import tf_seconds
    from goldbot.engine.exploration import LOOKBACK, LOOKBACK_1M
    return pd.Timedelta(seconds=max(tf_seconds(tf) * (LOOKBACK + 1), 60 * LOOKBACK_1M))


def spec_timeframes(path: Path | None = None) -> dict[str, str]:
    """arm id -> its timeframe in the frozen arm spec (config/exploration_arms.yaml)."""
    from goldbot.engine.exploration import SPEC_PATH
    from goldbot.research.arms import load_arms
    return {a.id: a.timeframe for a in load_arms(path or SPEC_PATH, check_hash=False)}


def span_ref(source: str, start: pd.Timestamp, end: pd.Timestamp) -> str:
    return f"dq_events:quarantine_span:{source}:{start.isoformat()}/{end.isoformat()}"


def recorded_spans(store: Any, sources: Iterable[str], *, symbol: str, start: pd.Timestamp,
                   end: pd.Timestamp) -> list[Span]:
    """The quarantine spans of `sources` overlapping [start, end), each with its reference."""
    from goldbot.data.quarantine import spans
    out: list[Span] = []
    for src in sorted(set(sources)):
        out += [(a, b, span_ref(src, a, b)) for a, b in spans(store, source=src, symbol=symbol, start=start, end=end)]
    return sorted(set(out))


def decide(*, candidates: pd.DataFrame, probes: Sequence[Any], spans_by_account: Mapping[str, Sequence[Span]],
           timeframes: Mapping[str, str], reloaded: Mapping[str, pd.Timestamp], now: pd.Timestamp,
           reason: str) -> list[Exclusion]:
    """Criterion v1 (module docstring). `candidates`: candidate_id, account_id, arm_id, tf, decision_utc (exploration
    candidates and trades); `probes`: cost_probe.ProbeRecord rows; `timeframes`: the arm spec's (a row's own tf when
    the arm is not in the spec)."""
    out: dict[str, Exclusion] = {}
    for r in candidates.to_dict("records"):
        cid, acct = str(r["candidate_id"]), str(r["account_id"])
        spans = spans_by_account.get(acct, ())
        if not spans:
            continue
        dec = pd.Timestamp(r["decision_utc"])
        lo = dec - lookback(timeframes.get(str(r["arm_id"]), str(r["tf"])))
        reload = reloaded.get(acct)
        hit = next((s for s in spans if s[0] <= dec and s[1] > lo), None)
        why = "decision window overlaps the span"
        if hit is None and reload is not None:
            hit = next((s for s in spans if s[0] <= dec < reload), None)
            why = f"decided before the engine reloaded corrected bars ({reload.isoformat()})"
        if hit is not None and cid not in out:
            out[cid] = Exclusion(client_order_id=cid, kind="exploration", account_id=acct, scope=EXPLORATION_SCOPE,
                                 reason=f"{reason}: {why}", span_ref=hit[2], decided_utc=now)
    for p in probes:
        if getattr(p, "purpose", None) != SCHEDULED_PURPOSE:
            continue
        hit = next((s for s in spans_by_account.get(p.account_id, ()) if s[0] <= p.sent_utc < s[1]), None)
        if hit is not None and p.client_order_id not in out:
            out[p.client_order_id] = Exclusion(client_order_id=p.client_order_id, kind="cost_probe",
                                               account_id=p.account_id, scope=PROBE_SCOPE,
                                               reason=f"{reason}: scheduled probe entered inside the span",
                                               span_ref=hit[2], decided_utc=now)
    return sorted(out.values(), key=lambda e: e.client_order_id)


def record(db: Database, rows: Iterable[Exclusion]) -> int:
    """Insert the exclusions not recorded yet (an order id already there keeps its first row). Returns the new rows."""
    new = 0
    with db.write() as conn:
        for e in rows:
            new += conn.execute(
                f"INSERT OR IGNORE INTO {TABLE} (client_order_id, kind, account_id, scope, reason, span_ref, "  # noqa: S608
                "criterion_version, decided_ns, blind) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (e.client_order_id, e.kind, e.account_id, json.dumps(list(e.scope)), e.reason, e.span_ref,
                 e.criterion_version, int(e.decided_utc.value))).rowcount
    return new


def excluded(conn: sqlite3.Connection, scope: Scope) -> set[str]:
    """The order ids void for `scope`. A file without the table (a read-only report on an older aux.db) has none."""
    try:
        rows = conn.execute(f"SELECT client_order_id, scope FROM {TABLE}").fetchall()    # noqa: S608
    except sqlite3.OperationalError:
        return set()
    return {str(cid) for cid, s in rows if scope in json.loads(s)}


def excluded_ids(db: Database, scope: Scope) -> set[str]:
    with db.connection() as conn:
        return excluded(conn, scope)


def compute(db: Database, store: Any, *, hosts: Mapping[str, str], symbol: str, now: pd.Timestamp,
            reloaded: Mapping[str, pd.Timestamp] | None = None, reason: str = "dq quarantine span",
            timeframes: Mapping[str, str] | None = None, since: pd.Timestamp | None = None) -> list[Exclusion]:
    """Criterion v1 over every exploration candidate and trade and every scheduled probe of `hosts` (account ->
    broker), against the spans recorded for each account's tick source and its broker's bar source. Reads ids,
    arms, accounts and times only (never an R or a P&L)."""
    from goldbot.engine import cost_probe
    from goldbot.engine.exploration import LEDGER
    lo = since if since is not None else now - pd.Timedelta(days=400)
    spans_by_account = {a: recorded_spans(store, (a, b), symbol=symbol, start=lo, end=now) for a, b in hosts.items()}
    spans_by_account = {a: s for a, s in spans_by_account.items() if s}
    if not spans_by_account:
        return []
    accts = sorted(spans_by_account)
    marks = ",".join("?" * len(accts))
    with db.connection() as conn:
        obs = pd.read_sql_query(f"SELECT candidate_id, account_id, arm_id, tf, decision_ns FROM exploration_obs "  # noqa: S608
                                f"WHERE account_id IN ({marks})", conn, params=accts)
        trades = LEDGER.select(conn, f"account_id IN ({marks})", tuple(accts))
        probes = cost_probe.LEDGER.select(conn, f"account_id IN ({marks})", tuple(accts))
    obs["decision_utc"] = pd.to_datetime(obs.pop("decision_ns"), unit="ns", utc=True)
    seen = set(obs["candidate_id"])
    extra = pd.DataFrame([{"candidate_id": t.client_order_id, "account_id": t.account_id, "arm_id": t.arm_id,
                           "tf": t.tf, "decision_utc": t.signal_utc} for t in trades if t.client_order_id not in seen],
                         columns=["candidate_id", "account_id", "arm_id", "tf", "decision_utc"])
    cands = pd.concat([obs, extra], ignore_index=True) if not extra.empty else obs
    return decide(candidates=cands, probes=probes, spans_by_account=spans_by_account,
                  timeframes=timeframes if timeframes is not None else spec_timeframes(),
                  reloaded=reloaded or {}, now=now, reason=reason)


def counts(conn: sqlite3.Connection, accounts: Sequence[str], lo: pd.Timestamp, hi: pd.Timestamp) -> int:
    """Exploration trades of `accounts` sent in [lo, hi) that are void as evidence (the daily review's count line)."""
    from goldbot.engine.exploration import LEDGER
    if not accounts:
        return 0
    void = excluded(conn, "n")
    marks = ",".join("?" * len(accounts))
    rows = LEDGER.select(conn, f"account_id IN ({marks}) AND ts_ns >= ? AND ts_ns < ?",
                         (*accounts, int(lo.value), int(hi.value)))
    return sum(t.client_order_id in void for t in rows)


__all__ = ["CRITERION_VERSION", "EXPLORATION_SCOPE", "PROBE_SCOPE", "SCOPES", "Exclusion", "compute", "counts",
           "decide", "excluded", "excluded_ids", "lookback", "record", "recorded_spans", "span_ref",
           "spec_timeframes"]
