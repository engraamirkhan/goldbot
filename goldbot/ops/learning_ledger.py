"""Closed exploration, control and cost-probe trades for the reports that read `state/closed_trades*.jsonl` (the perf
report, the `pnl_basis` health check): read-only, from the trade ledger that already merges every source
(`goldbot.api.trades_ledger.build_ledger`: aux.db `exploration_trades` and `cost_probes`).

Design rule (engine/exploration.py, engine/cost_probe.py): exploration and probe closes are NEVER written to the
closed-trade record, because that record feeds the paper -> tiny-live gate, the stop rule, the throttle and the
broker-ledger checks. This module therefore only reads: it adds nothing to that record, and the gate, throttle and
stop rule never see these trades. A report that read only the record said "no closed trades" for accounts closing
hundreds of exploration trades a day; this is the second source those reports now add, labelled by kind.

A trade that is in both sources (same account and broker position) is counted once, in the closed-trade record
(`recorded_keys`): the caller passes that record's keys and the matching ledger rows are dropped.

Net follows the closed-trade rule (gates_phase.pnl_basis, "nothing gross is labelled net"): a row is net only when its
commission AND its swap are both recorded; otherwise it is counted but has no net money and no net R.

The ledger import is deferred to the call: ops modules import this one without pulling the API stack at import time.
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Literal

import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.ops.gates_phase import ClosedTrade
from goldbot.telegram.entry_text import account_name

KINDS = ("exploration", "control", "cost_probe")
KIND_LABEL: dict[str, str] = {"exploration": "exploration", "control": "control", "cost_probe": "cost probe"}
# ledger sources whose loss means these trades are missing (not zero), named as the ledger names them
SOURCES = ("exploration", "probes")


class LearningClose(FrozenRecord):
    """One closed learning trade. `account` is the ledger's account name (any 5+ digit run cut, `account_name`)."""
    account: str
    kind: str                           # one of KINDS
    family: str
    arm_id: str | None = None
    timeframe: str | None = None
    position_id: int | None = None
    exit_utc: pd.Timestamp
    side: Literal["long", "short"]
    currency: str | None = None
    pnl: float | None = None            # net of every cost the broker reported
    r: float | None = None              # pnl over the loss at the stop when sent
    gross: float | None = None          # pnl + costs
    risk: float | None = None
    net: bool                           # commission and swap both recorded (gates_phase.pnl_basis "net")


class LearningCloses(FrozenRecord):
    rows: list[LearningClose]
    unreadable: list[str]               # SOURCES that could not be read: their trades are missing, not zero


def recorded_keys(trades: Iterable[ClosedTrade]) -> frozenset[tuple[str, int]]:
    """(account name, broker position) of every closed-trade record: the double-count key."""
    return frozenset((account_name(t.account_id), t.position_id) for t in trades if t.position_id is not None)


def closed_learning_trades(state_dir: str | Path, now: pd.Timestamp,
                           recorded: frozenset[tuple[str, int]] = frozenset()) -> LearningCloses:
    """Every closed exploration, control and cost-probe trade, less those already in the closed-trade record
    (`recorded`). Reads only; a torn source is named in `unreadable`."""
    from goldbot.api.trades_ledger import build_ledger
    ledger = build_ledger(state_dir, now)
    rows = []
    for r in ledger.rows:
        if r.kind not in KINDS or r.status != "closed" or r.exit_utc is None or r.account is None:
            continue
        if r.position_id is not None and (r.account, r.position_id) in recorded:
            continue
        c = r.costs
        rows.append(LearningClose(
            account=r.account, kind=r.kind, family=r.family, arm_id=r.arm_id, timeframe=r.timeframe,
            position_id=r.position_id, exit_utc=pd.Timestamp(r.exit_utc), side=r.side, currency=r.currency,
            pnl=r.pnl, r=r.r, gross=r.gross, risk=r.risk_amount,
            net=r.pnl is not None and c is not None and c.commission is not None and c.swap is not None))
    return LearningCloses(rows=rows, unreadable=[s for s in ledger.unreadable if s in SOURCES])
