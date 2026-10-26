"""Evidence exclusions (quant ruling 2026-10-26, blind; prereg-2026-10-25-high-volume.md Amendment 4).

"Void iff decided at or after a recorded span's start and before the account's first engine restart on the corrected
store bars, or labelled before the correction from a path overlapping the span. Indicators are recomputed from stored
bars at every decision, so there is no lookback term. Decided blind; K +0." Void as evidence means n, posterior, cost
cells and graduation; the H-61 stops, account P&L and the broker reconciliation keep every trade.

aux.db (migration 19), both tables append-only (their triggers refuse UPDATE and DELETE):
* `evidence_reloads`: per (account, span) C, when the corrected bars became visible, and R, the account's first engine
  warm start at or after C (R >= C, refused otherwise). Recorded once by scripts/evidence_exclusions.py.
* `evidence_exclusions`: one row per void order id (an exploration candidate's id is its order id, so a candidate and
  its trade share the row; a cost probe's client order id) with its scopes, reason, the span it rests on, the
  criterion version and when it was decided.

Criterion v1 (`decide`), from the spans `goldbot.data.quarantine.record_span` wrote to dq_events (the account's own
tick source and its broker's bar source), only for a span whose R is recorded:
* an exploration candidate or trade is void when S.start <= decision < R, or when it was labelled before C from a
  label path [entry, label exit] overlapping S;
* a scheduled cost probe (purpose `cost_measurement`) entered inside S is void for the cost cells only.
Over-voiding cannot be undone (append-only), so a span with no recorded R voids nothing: the nightly learner holds
its decisions at or after S.start out in memory (`held_out`), the cost table its scheduled probes inside S, and the
daily review raises an alert line until R is recorded.

Nothing here reads or prints an R multiple or a P&L (blinding).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
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
RELOADS = "evidence_reloads"

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


Reloads = Mapping[tuple[str, str], tuple[pd.Timestamp, pd.Timestamp]]     # (account, span ref) -> (C, R)


def _ts(v: Any) -> pd.Timestamp | None:
    return None if v is None or pd.isna(v) else pd.Timestamp(v)


def decide(*, candidates: pd.DataFrame, probes: Sequence[Any], spans_by_account: Mapping[str, Sequence[Span]],
           reloads: Reloads, now: pd.Timestamp, reason: str) -> list[Exclusion]:
    """Criterion v1 (module docstring) over the spans with a recorded (C, R). `candidates`: candidate_id, account_id,
    decision_utc, entry_utc, label_exit_utc, labelled_utc (None where unknown); `probes`: cost_probe.ProbeRecord."""
    out: dict[str, Exclusion] = {}
    for r in candidates.to_dict("records"):
        cid, acct = str(r["candidate_id"]), str(r["account_id"])
        dec = pd.Timestamp(r["decision_utc"])
        entry, exit_, labelled = _ts(r.get("entry_utc")), _ts(r.get("label_exit_utc")), _ts(r.get("labelled_utc"))
        for s in spans_by_account.get(acct, ()):
            if (acct, s[2]) not in reloads or cid in out:
                continue
            c, rr = reloads[(acct, s[2])]
            why = None
            if s[0] <= dec < rr:
                why = f"decided before the engine restarted on the corrected bars ({rr.isoformat()})"
            elif labelled is not None and exit_ is not None and labelled < c and (entry or dec) < s[1] and exit_ >= s[0]:
                why = f"labelled before the correction ({c.isoformat()}) from a path overlapping the span"
            if why is not None:
                out[cid] = Exclusion(client_order_id=cid, kind="exploration", account_id=acct,
                                     scope=EXPLORATION_SCOPE, reason=f"{reason}: {why}", span_ref=s[2],
                                     decided_utc=now)
    for p in probes:
        if getattr(p, "purpose", None) != SCHEDULED_PURPOSE or p.client_order_id in out:
            continue
        hit = next((s for s in spans_by_account.get(p.account_id, ())
                    if (p.account_id, s[2]) in reloads and s[0] <= p.sent_utc < s[1]), None)
        if hit is not None:
            out[p.client_order_id] = Exclusion(client_order_id=p.client_order_id, kind="cost_probe",
                                               account_id=p.account_id, scope=PROBE_SCOPE,
                                               reason=f"{reason}: scheduled probe entered inside the span",
                                               span_ref=hit[2], decided_utc=now)
    return sorted(out.values(), key=lambda e: e.client_order_id)


def held_out(candidates: pd.DataFrame, spans_by_account: Mapping[str, Sequence[Span]], reloads: Reloads) -> set[str]:
    """In memory only, for a span whose R is not recorded yet: the candidates decided at or after its start, and those
    whose label path [entry, label exit] overlaps it (C is not known either, so any such label is held out)."""
    out: set[str] = set()
    for r in candidates.to_dict("records"):
        acct = str(r["account_id"])
        dec, exit_ = pd.Timestamp(r["decision_utc"]), _ts(r.get("label_exit_utc"))
        entry = _ts(r.get("entry_utc")) or dec
        if any((acct, s[2]) not in reloads and (dec >= s[0] or (exit_ is not None and entry < s[1] and exit_ >= s[0]))
               for s in spans_by_account.get(acct, ())):
            out.add(str(r["candidate_id"]))
    return out


def held_out_in(conn: sqlite3.Connection, store: Any, hosts: Mapping[str, str], *, symbol: str,
                now: pd.Timestamp) -> set[str]:
    """`held_out` for a read-only reader (labs n): the spans of `hosts` with no recorded R, from `conn`."""
    spans = spans_of(store, hosts, symbol=symbol, now=now)
    if not spans:
        return set()
    rel = _reloads(conn)
    return held_out(_candidates(conn, sorted(spans)), spans, rel)


def unresolved(spans_by_account: Mapping[str, Sequence[Span]], reloads: Reloads) -> list[str]:
    """The accounts with a recorded span and no recorded R for it."""
    return sorted(a for a, ss in spans_by_account.items() if any((a, s[2]) not in reloads for s in ss))


def record_reload(db: Database, *, account_id: str, span: str, corrected: pd.Timestamp, reloaded: pd.Timestamp,
                  now: pd.Timestamp) -> bool:
    """Record (C, R) for (account, span) once (the first row stands). R before C is refused: a restart before the
    corrected bars were visible loaded the bad ones."""
    if reloaded < corrected:
        raise ValueError(f"{account_id}: restart {reloaded.isoformat()} is before the correction "
                         f"{corrected.isoformat()}")
    with db.write() as conn:
        return bool(conn.execute(
            f"INSERT OR IGNORE INTO {RELOADS} (account_id, span_ref, corrected_ns, reloaded_ns, recorded_ns) "  # noqa: S608
            "VALUES (?, ?, ?, ?, ?)", (account_id, span, int(corrected.value), int(reloaded.value),
                                       int(now.value))).rowcount)


def reloads(db: Database) -> dict[tuple[str, str], tuple[pd.Timestamp, pd.Timestamp]]:
    with db.connection() as conn:
        return _reloads(conn)


def _reloads(conn: sqlite3.Connection) -> dict[tuple[str, str], tuple[pd.Timestamp, pd.Timestamp]]:
    try:
        rows = conn.execute(f"SELECT account_id, span_ref, corrected_ns, reloaded_ns FROM {RELOADS}").fetchall()  # noqa: S608
    except sqlite3.OperationalError:            # an older aux.db opened read-only: nothing recorded
        return {}
    return {(str(a), str(s)): (pd.Timestamp(c, unit="ns", tz="UTC"), pd.Timestamp(r, unit="ns", tz="UTC"))
            for a, s, c, r in rows}


def _candidates(conn: sqlite3.Connection, accts: Sequence[str]) -> pd.DataFrame:
    """Every exploration candidate of `accts` with its decision, entry, label exit and label time, and every trade
    with no candidate row (its signal as the decision)."""
    from goldbot.engine.exploration import LEDGER
    marks = ",".join("?" * len(accts))
    obs = pd.read_sql_query("SELECT candidate_id, account_id, decision_ns, entry_ns, label_exit_ns, labelled_ns "  # noqa: S608
                            f"FROM exploration_obs WHERE account_id IN ({marks})", conn, params=list(accts))
    trades = LEDGER.select(conn, f"account_id IN ({marks})", tuple(accts))
    for col in ("decision", "entry", "label_exit", "labelled"):
        obs[f"{col}_utc"] = pd.to_datetime(pd.to_numeric(obs.pop(f"{col}_ns"), errors="coerce"), unit="ns", utc=True)
    seen = set(obs["candidate_id"])
    extra = pd.DataFrame([{"candidate_id": t.client_order_id, "account_id": t.account_id,
                           "decision_utc": t.signal_utc, "entry_utc": None, "label_exit_utc": None,
                           "labelled_utc": None} for t in trades if t.client_order_id not in seen],
                         columns=list(obs.columns))
    return pd.concat([obs, extra], ignore_index=True) if not extra.empty else obs


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


# ---------------------------------------------------------------- spec-bug-midnight (quant review 2026-10-26)
# ARM-SPEC 1.1 says "no signal after 20:00 UTC"; the S1 code read the close's minute of day, so on GMT (from the UK
# switch, 2026-10-25) the 23:55 bar, still in the London day, closing at 00:00 UTC passed the cut. A correction to the
# spec, not an amendment (arms-2026-10-25.md section 1.1 note): every S1A/S1B row decided on a 00:00 UTC close since
# the switch is void for every evidence use, live and shadow alike. Decided from arm ids and decision times only.
SPEC_BUG_MIDNIGHT = "spec-bug-midnight"
SPEC_BUG_MIDNIGHT_REF = "spec:arms-2026-10-25:1.1:no-signal-after-20:00-utc"
SPEC_BUG_MIDNIGHT_VERSION = "spec-bug-midnight/v1"
SPEC_BUG_MIDNIGHT_ARMS = ("S1A", "S1B")
SPEC_BUG_MIDNIGHT_FROM = pd.Timestamp("2026-10-25 01:00", tz="UTC")     # the UK leaves BST: London day = UTC day


def midnight_spec_bug(db: Database, *, now: pd.Timestamp) -> list[Exclusion]:
    """Every S1A/S1B candidate (taken or not) and every S1A/S1B trade without a candidate row whose decision bar
    closed at 00:00 UTC in [SPEC_BUG_MIDNIGHT_FROM, now]."""
    from goldbot.engine.exploration import LEDGER
    marks = ",".join("?" * len(SPEC_BUG_MIDNIGHT_ARMS))
    with db.connection() as conn:
        obs = conn.execute(f"SELECT candidate_id, account_id, decision_ns FROM exploration_obs "  # noqa: S608
                           f"WHERE arm_id IN ({marks})", SPEC_BUG_MIDNIGHT_ARMS).fetchall()
        trades = LEDGER.select(conn, f"arm_id IN ({marks})", SPEC_BUG_MIDNIGHT_ARMS)
    rows = [(str(c), str(a), pd.Timestamp(int(d), unit="ns", tz="UTC")) for c, a, d in obs]
    seen = {r[0] for r in rows}
    rows += [(t.client_order_id, t.account_id, t.signal_utc) for t in trades if t.client_order_id not in seen]
    return sorted((Exclusion(client_order_id=cid, kind="exploration", account_id=acct, scope=EXPLORATION_SCOPE,
                             reason=f"{SPEC_BUG_MIDNIGHT}: S1 signal on the 00:00 UTC close, after the spec's "
                                    "20:00 UTC cut", span_ref=SPEC_BUG_MIDNIGHT_REF,
                             criterion_version=SPEC_BUG_MIDNIGHT_VERSION, decided_utc=now)
                   for cid, acct, at in rows
                   if SPEC_BUG_MIDNIGHT_FROM <= at <= now and at == at.normalize()),
                  key=lambda e: e.client_order_id)


class Screen(FrozenRecord):
    """What one screening found: the exclusions to record (spans with a recorded R), the candidates held out in memory
    and the accounts whose span has no recorded R."""
    exclusions: tuple[Exclusion, ...] = ()
    held_out: frozenset[str] = frozenset()
    unresolved: tuple[str, ...] = ()


def spans_of(store: Any, hosts: Mapping[str, str], *, symbol: str, now: pd.Timestamp) -> dict[str, list[Span]]:
    """account -> the spans recorded for its tick source and its broker's bar source (accounts with none left out)."""
    found = {a: recorded_spans(store, (a, b), symbol=symbol, start=now - pd.Timedelta(days=400), end=now)
             for a, b in hosts.items()}
    return {a: s for a, s in found.items() if s}


def screen(db: Database, store: Any, *, hosts: Mapping[str, str], symbol: str, now: pd.Timestamp,
           reason: str = "dq quarantine span", planned: Reloads | None = None) -> Screen:
    """Criterion v1 over every exploration candidate and trade and every scheduled probe of `hosts` (account ->
    broker), with the (C, R) recorded in aux.db (and `planned` ones, for a dry run). Reads ids, accounts and times
    only (never an R multiple or a P&L)."""
    from goldbot.engine import cost_probe
    spans = spans_of(store, hosts, symbol=symbol, now=now)
    if not spans:
        return Screen()
    accts = sorted(spans)
    marks = ",".join("?" * len(accts))
    with db.connection() as conn:
        rel = {**_reloads(conn), **(planned or {})}
        cands = _candidates(conn, accts)
        probes = cost_probe.LEDGER.select(conn, f"account_id IN ({marks})", tuple(accts))
    return Screen(exclusions=tuple(decide(candidates=cands, probes=probes, spans_by_account=spans, reloads=rel,
                                          now=now, reason=reason)),
                  held_out=frozenset(held_out(cands, spans, rel)), unresolved=tuple(unresolved(spans, rel)))


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


__all__ = ["CRITERION_VERSION", "EXPLORATION_SCOPE", "PROBE_SCOPE", "SCOPES", "Exclusion", "Screen", "counts",
           "decide", "excluded", "excluded_ids", "held_out", "held_out_in", "record", "record_reload", "recorded_spans", "reloads",
           "screen", "span_ref", "spans_of", "unresolved"]
