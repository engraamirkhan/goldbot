"""The engine's side of the live pilot (goldbot/risk/pilot.py, ADR 0055): "after £25 cumulative realised loss or 30
filled entries, whichever comes first: entries halt for good. Open positions keep their automatic exits".

Trading-safety conditions (ADR 0055 review): "the halt state and the fill/trade count persist in SQLite, survive a
restart, count fills from broker deals, and refuse entries if the record can't be read".

* Both counts come from the broker's deal history since the pilot's `start_utc`, never from the engine's own book: the
  realised loss is the net profit, commission, swap and fees of every deal that is not a balance or credit operation
  (a deposit never hides a loss), and the filled entries are the DEAL_ENTRY_IN buys and sells, whichever magic (an
  entry the owner placed by hand counts too). Read on the first refresh, whenever the balance or the number of open
  positions changed since the last read, and at least every READ_EVERY_S.
* The record (counts, the sticky stop) is one row per account in core.db `pilot_state`. A row that cannot be read at
  start, or a deal history that cannot be read, leaves the counts unknown: the RiskGate refuses every entry
  (`pilot_record_unknown`) until a read succeeds. A failed write is logged and retried on the next refresh; the stop
  holds in memory meanwhile.
* The stop is recorded once (journal `pilot_stop`) and stays for that pilot_id whatever happens next. A run after it
  is a new pilot_id under a new recorded decision.
* The terminal's leverage is passed to the gate (`AccountState.broker_leverage`), which uses it only when it is lower
  than the pilot's `leverage_cap`.

Exits never read any of this.
"""
from __future__ import annotations

import logging
import math
import time
from pathlib import Path
from typing import Any

import pandas as pd

from goldbot.data.timeutil import epoch_ns
from goldbot.db.connection import Database, connect, write
from goldbot.db.repo import RecordRepo
from goldbot.risk.pilot import PilotRules, PilotState, pilot_start, stop_reason

log = logging.getLogger("goldbot.engine")

READ_EVERY_S = 60.0                  # the deal history is read again at least this often while nothing changes
WRITE_BUSY_TIMEOUT_MS = 500          # the write runs on the tick thread (as hwm.HwmStore)

PILOT: RecordRepo[PilotState] = RecordRepo(
    "pilot_state", PilotState, key="account_id", tolerant=True,
    columns={"account_id": lambda r: r.account_id,
             "ts_ns": lambda r: int(epoch_ns(pd.DatetimeIndex([r.read_utc or pd.Timestamp(0, tz="UTC")]))[0])})


class PilotStore:
    """core.db `pilot_state`, opened once (as hwm.HwmStore). `load` raises when the row cannot be read: the caller
    treats that as an unknown record (entries refused), never as "no pilot yet"."""

    def __init__(self, state_dir: str | Path):
        self.state_dir = Path(state_dir)
        self.db: Database | None = None

    def _open(self) -> Database:
        if self.db is None:
            self.db = Database.open(self.state_dir, "core")
        return self.db

    def load(self, account_id: str) -> PilotState | None:
        with self._open().connection() as conn:
            return PILOT.get(conn, account_id)

    def save(self, rec: PilotState) -> bool:
        try:
            conn = connect(self._open().path, "core", busy_timeout_ms=WRITE_BUSY_TIMEOUT_MS)
            try:
                with write(conn):
                    PILOT.upsert(conn, rec)
            finally:
                conn.close()
            return True
        except Exception as exc:
            log.error("pilot record not saved (%s): kept in memory, retried on the next refresh", type(exc).__name__)
            return False


def tally(deals: pd.DataFrame | None, start: pd.Timestamp) -> tuple[float, int]:
    """(realised loss, filled entries) of an MT5 deal history from `start` on. Raises on a history that is not MT5's
    (the paper broker): the caller leaves the counts unknown."""
    from goldbot.ops.ledger import DEAL_BALANCE, DEAL_BUY, DEAL_CREDIT, DEAL_SELL, ENTRY_IN, normalize_deals
    d = normalize_deals(deals)
    d = d[d["ts_utc"] >= start]
    trades = d[~d["type"].isin([DEAL_BALANCE, DEAL_CREDIT])]
    net = float((trades["profit"] + trades["commission"] + trades["swap"] + trades["fee"]).sum())
    entries = int(((d["type"].isin([DEAL_BUY, DEAL_SELL])) & (d["entry"] == ENTRY_IN)).sum())
    return max(0.0, -net), entries


class _Tracker:
    def __init__(self, state_dir: str | Path):
        self.store = PilotStore(state_dir)
        self.seen: tuple[float, int] | None = None
        self.saved: PilotState | None = None


def _tracker(eng: Any) -> _Tracker:
    t = getattr(eng, "_pilot_tracker", None)
    if t is None:
        t = _Tracker(eng.cfg.state_dir)
        eng._pilot_tracker = t
    return t


def refresh(eng: Any, acc: Any, now: pd.Timestamp) -> None:
    """Update `eng.state.pilot` and `broker_leverage` after the account read `acc`; every account refresh. No pilot on
    this account: both cleared (read only while one is set)."""
    st = eng.state
    rules: PilotRules | None = eng.gate.limits.pilot
    if rules is None:
        st.pilot, st.broker_leverage = None, None
        return
    lev = float(getattr(acc, "leverage", 0) or 0)
    st.broker_leverage = lev if math.isfinite(lev) and lev > 0 else None
    t = _tracker(eng)
    ps: PilotState | None = st.pilot
    if ps is None or ps.pilot_id != rules.pilot_id:
        try:
            row = t.store.load(eng.cfg.account_id)
        except Exception as exc:          # unreadable: unknown, entries refused, tried again on the next refresh
            log.error("pilot record unreadable (%s): entries refused", type(exc).__name__)
            st.pilot = None
            return
        ps = row if row is not None and row.pilot_id == rules.pilot_id else \
            PilotState(account_id=eng.cfg.account_id, pilot_id=rules.pilot_id)
        t.saved, t.seen = row, None
    seen = (float(acc.balance), int(st.open_positions))
    due = not ps.known or t.seen != seen or ps.read_utc is None \
        or (now - pd.Timestamp(ps.read_utc)).total_seconds() >= READ_EVERY_S
    if due:
        try:
            loss, entries = tally(eng.broker.deals_since(pilot_start(rules)), pilot_start(rules))
            ps = ps.model_copy(update={"realised_loss": loss, "entries": entries, "read_utc": now})
            t.seen = seen
        except Exception as exc:
            log.warning("pilot deal history unreadable (%s): entries refused until it is read", type(exc).__name__)
            ps = ps.model_copy(update={"realised_loss": None, "entries": None})
    why = stop_reason(rules, ps.realised_loss, ps.entries) if ps.known and ps.realised_loss is not None \
        and ps.entries is not None else None
    if ps.stopped_utc is None and why is not None:
        ps = ps.model_copy(update={"stopped_utc": now, "stop_reason": why})
        log.error("pilot %s stopped (%s): realised loss %.2f, %d filled entries; no new entries, exits keep running",
                  rules.pilot_id, why, ps.realised_loss, ps.entries)
        eng.decisions.append({"ts": time.time(), "action": "pilot_stop", "pilot_id": rules.pilot_id, "reason": why,
                              "realised_loss": ps.realised_loss, "entries": ps.entries})
    st.pilot = ps
    if ps.known and ps != t.saved and t.store.save(ps):
        t.saved = ps
