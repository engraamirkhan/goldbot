"""The engine's state files: the pending_orders table, the risk state and the engine state file (TD-2, ADR 0023 step 1;
docs/proposals/2026-10-runner-seam-map.md, "Step 1"). Moved out of engine/runner.py unchanged.

Design rule (DESIGN Stack, "SQLite for live state"; TRACEABILITY X7, S4): "write the id to the pending_orders table
(atomically, on disk) before order_send", and a restart reconciles from what was written. The store holds no Engine
state. The runner builds a snapshot from its attributes and applies the one it loads, so `_orders_record` (X7: the id
joins `sent_ids` before the write), the 7-day prune, reconciliation and the kill-switch and re-arm rules stay in the
runner. `load_*` returns None only when the state is confirmed absent (no file). An unreadable file raises the parse's
own error, so the engine refuses to start rather than forget what was sent or a drawdown halt.

The snapshots are built from the values the parse produces, never by validating the file's dict (ADR 0023 S1):
FrozenRecord forbids extra keys, while the risk parse ignores keys it does not know.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import pandas as pd

from goldbot.base import FrozenRecord, Record, UtcTimestamp, load_record, note_schema_skew, write_atomic
from goldbot.labels.excursion import Excursion
from goldbot.labels.exit_policy import ExitPolicy
from goldbot.ops.gates_phase import EntryAudit
from goldbot.risk.gate import Stage


class OpenTrade(Record):
    position_id: int
    agent_id: str
    side: int
    lots: float
    entry_bar_ts: UtcTimestamp
    max_bars: int
    bars_held: int = 0
    sl: float | None = None           # the protection this engine expects at the broker (reinstated if lost)
    tp: float | None = None
    # the specialist's exit policy (labels.exit_policy), run on bars of the agent's timeframe as its labels were built
    policy: ExitPolicy | None = None
    timeframe: str | None = None
    entry: float | None = None        # fill price: the policy's distances are measured from it
    atr_usd: float | None = None      # the signal bar's ATR, which the policy's distances are in
    opened_utc: UtcTimestamp | None = None   # fill time (tick clock): bars closing after it count for the trail
    flat_at: UtcTimestamp | None = None      # hard-flat deadline
    scaled: bool = False              # the scale-out is done: taken, skipped (minimum volume) or out of retries
    scale_tries: int = 0              # failed scale-out attempts (bounded by EngineConfig.scale_out_retries)
    # for the closed-trade record (gates_phase.ClosedTrade): set at the fill, None on trades saved before they existed
    client_order_id: str | None = None
    open_price: float | None = None   # fill price (with or without an exit policy)
    initial_sl: float | None = None   # the stop at entry: R is measured against it
    initial_lots: float | None = None
    equity_before: float | None = None
    scaled_lots: float = 0.0          # taken by the scale-out, at scaled_price: folded into the final record
    scaled_price: float | None = None
    # MAE / MFE so far (BACKLOG 23), from every tick while this engine holds the position (`_track_excursions`);
    # approx: part of the path was not seen by ticks (carried across a restart, adopted), so the record fills it from
    # the stored 1m bars and flags it
    excursion: Excursion | None = None
    excursion_approx: bool = False
    # False for an orphan adopted without an order record: its initial stop is the broker's current one (maybe trailed
    # since entry), so the record leaves R-based excursions empty (prices and times are still recorded)
    risk_known: bool = True
    audit: EntryAudit | None = None   # finance audit entry facts for the record; None: adopted or saved before them


class PendingClose(Record):
    """A closed-trade record waiting to be written (EngineConfig.closed_record_retries attempts in all)."""
    position_id: int
    trade: OpenTrade
    reason: str
    price: float | None = None
    at: UtcTimestamp
    tries: int = 0
    next_try: UtcTimestamp | None = None


class SentOrder(Record):
    """One row of the pending_orders table (state/orders_<account>.json), written BEFORE order_send."""
    client_order_id: str
    ts_utc: UtcTimestamp
    agent_id: str
    side: int
    magic: int
    lots: float
    max_bars: int
    sl: float
    tp: float
    status: str = "sending"         # sending -> filled | rejected | unfilled (restart found no fill)
    position_id: int | None = None
    atr_usd: float | None = None    # the signal bar's ATR: a fill recovered on restart gets its exit policy back
    audit: EntryAudit | None = None  # finance audit entry facts, handed to a fill recovered on restart


class OrdersSnapshot(FrozenRecord):
    """The pending_orders table and the open trades, as saved and as a restart reads them back."""
    sent: dict[str, SentOrder]
    open: dict[int, OpenTrade]
    # closed-trade records not written yet (a failed write) survive a restart; lost ones stay listed
    closing: list[PendingClose]
    records_lost: list[int]


class RiskSnapshot(FrozenRecord):
    """The risk state that survives a restart: the period marks, the stage and the kill switch's and re-arm's state."""
    last_reset: pd.Timestamp | None
    day_start_equity: float
    week_start_equity: float
    balance_closed_hwm: float
    stage: Stage
    rearm_seen: str | None
    weekend_done: str | None
    halted_at: pd.Timestamp | None
    propose_only_until: pd.Timestamp | None
    rearm_refused: str | None
    kill_everything: bool
    stage2_pending_since: pd.Timestamp | None


@runtime_checkable
class EngineStore(Protocol):
    """Where the engine keeps its state (ADR 0023). W2-1 adds a core.db implementation and may only add to this (S6)."""

    def load_orders(self) -> OrdersSnapshot | None:
        """None = confirmed absent; unreadable raises."""

    def save_orders(self, s: OrdersSnapshot) -> None: ...

    def load_risk(self) -> RiskSnapshot | None:
        """None = confirmed absent; unreadable raises."""

    def save_risk(self, s: RiskSnapshot) -> None: ...

    def write_engine_state(self, payload: dict[str, Any], durable: bool = False) -> None: ...


def orders_file(state_dir: str | Path, account_id: str) -> Path:
    return Path(state_dir, f"orders_{account_id}.json")   # not engine_*: the supervisor globs those


def risk_file(state_dir: str | Path, account_id: str) -> Path:
    return Path(state_dir, f"risk_{account_id}.json")   # not engine_*: the supervisor globs those


class JsonEngineStore:
    """The state files under `state_dir`: orders_<account>.json, risk_<account>.json and engine_<account>.json."""

    def __init__(self, state_dir: Path, account_id: str):
        self.state_dir = state_dir
        self.account_id = account_id

    def save_orders(self, s: OrdersSnapshot) -> None:
        payload = {"sent": {k: o.model_dump(mode="json") for k, o in s.sent.items()},
                   "open": {str(k): t.model_dump(mode="json") for k, t in s.open.items()},
                   # closed-trade records not written yet (a failed write) survive a restart; lost ones stay listed
                   "closing": [c.model_dump(mode="json") for c in s.closing],
                   "records_lost": s.records_lost}
        # durable: restart reconciliation reads it
        write_atomic(orders_file(self.state_dir, self.account_id), json.dumps(payload))

    def load_orders(self) -> OrdersSnapshot | None:
        path = orders_file(self.state_dir, self.account_id)
        if not path.exists():
            return None
        d = json.loads(path.read_text())          # unreadable -> fail loudly rather than forget what was sent
        # ADR 0010 (rollback = restart): fields a newer release added are dropped, a wrong type still fails loudly
        src = "orders_<account>.json"
        if extra := sorted(set(d) - {"sent", "open", "closing", "records_lost"}):
            note_schema_skew(src, "orders file", extra)      # a newer release's table: not read here (S12)
        sent = {k: load_record(SentOrder, v, source=src) for k, v in d.get("sent", {}).items()}
        open_ = {int(k): load_record(OpenTrade, v, source=src) for k, v in d.get("open", {}).items()}
        closing = [load_record(PendingClose, v, source=src) for v in d.get("closing", [])]
        records_lost = [int(x) for x in d.get("records_lost", [])]
        return OrdersSnapshot(sent=sent, open=open_, closing=closing, records_lost=records_lost)

    def save_risk(self, s: RiskSnapshot) -> None:
        payload = {"last_reset": s.last_reset.isoformat() if s.last_reset is not None else None,
                   "day_start_equity": s.day_start_equity, "week_start_equity": s.week_start_equity,
                   "balance_closed_hwm": s.balance_closed_hwm, "stage": Stage(s.stage).value,
                   "rearm_seen": s.rearm_seen, "weekend_done": s.weekend_done,
                   "halted_at": s.halted_at.isoformat() if s.halted_at is not None else None,
                   "propose_only_until": s.propose_only_until.isoformat() if s.propose_only_until is not None else None,
                   "rearm_refused": s.rearm_refused, "kill_everything": s.kill_everything,
                   "stage2_pending_since": None if s.stage2_pending_since is None
                   else s.stage2_pending_since.isoformat()}
        # durable: halts and stages survive a power cut
        write_atomic(risk_file(self.state_dir, self.account_id), json.dumps(payload))

    def load_risk(self) -> RiskSnapshot | None:
        path = risk_file(self.state_dir, self.account_id)
        if not path.exists():
            return None
        d = json.loads(path.read_text())          # unreadable -> fail loudly rather than forget a drawdown halt
        last_reset = pd.Timestamp(d["last_reset"]) if d.get("last_reset") else None
        day_start_equity = float(d.get("day_start_equity", 0.0))
        week_start_equity = float(d.get("week_start_equity", 0.0))
        balance_closed_hwm = float(d.get("balance_closed_hwm", 0.0))
        stage = Stage(d.get("stage", Stage.NORMAL.value))
        rearm_seen = d.get("rearm_seen")
        weekend_done = d.get("weekend_done")
        halted_at = pd.Timestamp(d["halted_at"]) if d.get("halted_at") else None
        propose_only_until = pd.Timestamp(d["propose_only_until"]) if d.get("propose_only_until") else None
        rearm_refused = d.get("rearm_refused")
        # ADR 0016: absent (an older file) = nothing pending; an older release drops it and trips on one read instead
        pending = d.get("stage2_pending_since")
        stage2_pending_since = pd.Timestamp(pending) if pending else None
        # F1: a trip whose whole-account flatten had not been seen flat goes on closing the whole account; absent (an
        # older file) = this engine's book only, as that release did
        kill_everything = bool(d.get("kill_everything", False))
        return RiskSnapshot(last_reset=last_reset, day_start_equity=day_start_equity,
                            week_start_equity=week_start_equity, balance_closed_hwm=balance_closed_hwm, stage=stage,
                            rearm_seen=rearm_seen, weekend_done=weekend_done, halted_at=halted_at,
                            propose_only_until=propose_only_until, rearm_refused=rearm_refused,
                            kill_everything=kill_everything, stage2_pending_since=stage2_pending_since)

    def write_engine_state(self, payload: dict[str, Any], durable: bool = False) -> None:
        # the supervisor never reads a half-written file; a heartbeat, rewritten constantly, needs no fsync
        write_atomic(Path(self.state_dir, f"engine_{self.account_id}.json"), json.dumps(payload), durable=durable)
