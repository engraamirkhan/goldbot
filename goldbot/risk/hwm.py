"""BACKLOG 54 (ADR 0018): a rise in the balance high-water mark is applied only once it is confirmed.

The drawdown stages measure equity against `balance_closed_hwm`. Raising it on every read (`max(hwm, balance)`) let one
inflated balance read (a glitch, a half-filled account struct) raise the mark for good, so every later real read looked
like a drawdown: size-down at 8% and, two reads later, a phantom 12% kill switch. The rule (ADR 0018):

* "a rise in balance_closed_hwm is applied only once confirmed: by the deal history (closed deals and balance
  operations since the last mark account for the rise within a tolerance), or by a second balance read that agrees;
  one disagreeing read never raises it, two agreeing reads always do";
* a deposit is a balance operation in the deal history, so it explains a rise and raises the mark (as before);
* the mark is never lowered by this rule (only the owner's re-arm resets it); a refused rise is logged and recorded.

ADR 0042 (D1, D3) amends the deposit case: "A deposit or withdrawal is a capital flow (MT5 DEAL_BALANCE). Never in
P&L"; "on a confirmed balance operation the mark becomes max(mark + net flow, balance), confirmed by the deal history
only (never by a second read alone). A loss before a deposit is not forgiven. A withdrawal lowers the mark by exactly
the withdrawn amount (the only lowering besides the owner's re-arm)". `apply_flows` moves the mark by the flows; the
`max(.., balance)` part is the rule above, explained by the deals since the anchor (`HwmConfirmation.flows` keeps them
from being counted twice).

The confirmation state (the candidate rise, and when the current mark was set: the deal-history anchor) is one row per
account in core.db (`hwm_confirmation`, migration 2), written only when it changes. The mark itself stays in the risk
state (`risk_<account>.json`); a row whose `mark` differs from it (a re-arm, or an older release that raised the mark
after a rollback) is void, so a rollback and a roll-forward never confirm against a stale candidate or anchor.
"""
from __future__ import annotations

import logging
import math
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import pandas as pd

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.data.timeutil import epoch_ns
from goldbot.db.connection import Database, connect, db_path, write
from goldbot.db.repo import RecordRepo

log = logging.getLogger(__name__)


#: a candidate older than this is void (L2: the engine was down, or no read came): a rise then needs a fresh pair
CANDIDATE_MAX_AGE = pd.Timedelta(minutes=10)
#: the engine's write runs on the tick thread: a locked core.db gives up after this (the strict side), never blocks exits
WRITE_BUSY_TIMEOUT_MS = 500


class HwmConfirmation(FrozenRecord):
    """One account's high-water-mark confirmation state (core.db `hwm_confirmation`). S12: every optional field's
    default is the strict side. No anchor means the deal history cannot explain a rise (a second read must), and no
    candidate means a rise needs a fresh pair of reads."""

    account_id: str
    mark: float                                     # the mark this row refers to; another mark voids the row
    mark_utc: UtcTimestamp | None = None            # when that mark was set from a balance read (deal-history anchor)
    pending_balance: float | None = None            # an unconfirmed balance read above the mark (the candidate)
    # when the first of an unbroken run of candidates was read (a disagreeing candidate replacing another keeps it; a
    # raise or a read at or below the mark clears it): health's "waiting for confirmation" age
    pending_since_utc: UtcTimestamp | None = None
    # when the current candidate was read: its age, and the anchor for the deals that may corroborate it later.
    # Missing (a row from before this field) = pending_since_utc, which then meant the same
    candidate_utc: UtcTimestamp | None = None
    # ADR 0042: the net capital flow `apply_flows` added to `mark` since `mark_utc` (the deals since the anchor hold
    # it too, so a rise is explained from mark - flows). Missing (an older row, or an older release reading this one:
    # it then sees no rise explained and confirms by a second read) = 0
    flows: float = 0.0

    def candidate_at(self) -> pd.Timestamp | None:
        return self.candidate_utc or self.pending_since_utc

    def without_candidate(self) -> HwmConfirmation:
        return self.model_copy(update={"pending_balance": None, "pending_since_utc": None, "candidate_utc": None})

    def fresh(self, now: pd.Timestamp) -> HwmConfirmation:
        """This row with a candidate older than CANDIDATE_MAX_AGE dropped (L2). A candidate stamped after `now` (a
        clock that ran ahead, a row from another host) is void too: its age cannot be known, and a negative age must
        never keep it alive."""
        at = self.candidate_at()
        if self.pending_balance is not None and (at is None or at > now or abs(now - at) > CANDIDATE_MAX_AGE):
            return self.without_candidate()
        return self


def _ns(ts: pd.Timestamp) -> int:
    return int(epoch_ns(pd.DatetimeIndex([ts]))[0])


HWM: RecordRepo[HwmConfirmation] = RecordRepo(
    "hwm_confirmation", HwmConfirmation, key="account_id", tolerant=True,
    columns={"account_id": lambda c: c.account_id,
             "ts_ns": lambda c: _ns(c.candidate_at() or c.mark_utc or pd.Timestamp(0, tz="UTC"))})


class HwmStore:
    """The engine's handle on core.db, opened (and migrated) once at start with the default busy timeout (review L1),
    so a save on the tick thread is one short write with a WRITE_BUSY_TIMEOUT_MS busy timeout. When core.db cannot be
    opened, the open is retried on the next save; until then nothing is loaded or saved (logged), and every rise needs
    its confirmation within this process, the strict side."""

    def __init__(self, state_dir: str | Path):
        self.state_dir = Path(state_dir)
        self.db: Database | None = None
        self._open()

    def _open(self) -> Database | None:
        """Open and migrate core.db (default busy timeout: a lock at start-up is waited out, not given up at once)."""
        try:
            self.db = Database.open(self.state_dir, "core")
        except Exception as exc:
            log.warning("core.db unavailable for the high-water-mark confirmation (%s): retried on the next save",
                        type(exc).__name__)
        return self.db

    def load(self, account_id: str) -> HwmConfirmation | None:
        """The persisted row, or None when there is none or it cannot be read (no anchor, no candidate)."""
        if self.db is None:
            return None
        try:
            with self.db.connection() as conn:
                return HWM.get(conn, account_id)
        except Exception as exc:          # never stops the engine: the strict side instead
            log.warning("high-water-mark confirmation state unreadable (%s): a rise needs a fresh confirmation",
                        type(exc).__name__)
            return None

    def save(self, conf: HwmConfirmation) -> bool:
        """Upsert the row (synchronous FULL); False (logged) when it cannot be written. A lost write only loses a
        candidate or an anchor, so the next rise needs a fresh confirmation (never a rise applied on one read)."""
        db = self.db or self._open()
        if db is None:
            return False
        try:
            conn = connect(db.path, "core", busy_timeout_ms=WRITE_BUSY_TIMEOUT_MS)
            try:
                with write(conn):
                    HWM.upsert(conn, conf)
            finally:
                conn.close()
            return True
        except Exception as exc:
            log.warning("high-water-mark confirmation state not saved (%s): a restart forgets the pending rise",
                        type(exc).__name__)
            return False


def read_only_confirmation(state_dir: str | Path, account_id: str) -> HwmConfirmation | None:
    """The row read without the engine's writer path (health): core.db opened read-only (mode=ro), never created or
    migrated. None when there is no core.db, no table or no row; any other database error raises."""
    path = db_path(state_dir, "core")
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    try:
        return HWM.get(conn, account_id)
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return None
        raise
    finally:
        conn.close()


def deals_balance_change(deals: pd.DataFrame) -> tuple[float, float, int]:
    """(balance change, deposits, balance-changing deals) over an MT5 deal history: profit, commission, swap and fee
    of every deal except credit (MT5 DEAL_CREDIT does not touch the balance), as the ledger's RC-9 tie-out counts it.
    Deposits are the positive balance operations (MT5 DEAL_BALANCE). Raises on a history without MT5's deal columns
    (the paper broker): the caller treats that as "not explained"."""
    from goldbot.ops.ledger import DEAL_BALANCE, DEAL_CREDIT, normalize_deals
    d = normalize_deals(deals)
    d = d[d["type"] != DEAL_CREDIT]
    money = d["profit"] + d["commission"] + d["swap"] + d["fee"]
    counted = int((money != 0).sum())
    deposits = float(d.loc[(d["type"] == DEAL_BALANCE) & (d["profit"] > 0), "profit"].sum())
    return float(money.sum()), deposits, counted


Outcome = Literal["unchanged", "raised", "pending", "refused"]
By = Literal["deals", "second_read", "candidate_deals", "exact"]


class HwmStep(FrozenRecord):
    """What one balance read did to the mark."""

    mark: float
    conf: HwmConfirmation
    outcome: Outcome
    by: By | None = None                                # how a rise was confirmed
    deposits: float = 0.0                               # deposits in the deal history that explained it
    dropped: float | None = None                        # a candidate this read disagreed with (a refused rise)
    expired: float | None = None                        # a candidate voided by age before this read (L2)


def step(conf: HwmConfirmation, mark: float, balance: float, now: pd.Timestamp, tolerance: float,
         deals: Callable[[pd.Timestamp], pd.DataFrame], *, exact: bool = False) -> HwmStep:
    """Apply one balance read to the mark (ADR 0018). `conf` must refer to `mark` (the caller voids it otherwise);
    `tolerance` is a fraction of the mark; `deals(since)` reads the broker's deal history; `exact`: the broker's
    balance reads cannot glitch (the paper broker's own ledger), so a read above the mark raises it at once.

    * A candidate older than CANDIDATE_MAX_AGE is dropped first.
    * A read above the mark is confirmed when the deal history since the mark was set explains it (at least one
      balance-changing deal, and mark + their sum within the tolerance of the read), or when it agrees with the
      candidate within the tolerance (the mark rises to the lower of the two).
    * A read that disagrees with the candidate first asks whether the deals since the candidate was read take the
      candidate to this read (review M1: a real peak followed by a loss). If they do, the candidate was real and the
      mark rises to it (and further, if this read is itself above it and explained). Otherwise the candidate is
      refused. A read above the mark then becomes the new candidate, keeping the first pending_since_utc (review M2).
    * A read at or below the mark (or not a number) never moves the mark, apart from that corroborated candidate.
    """
    tol = tolerance * mark
    expired = conf.pending_balance if conf.fresh(now) is not conf else None
    conf = conf.fresh(now)
    pend = conf.pending_balance
    above = math.isfinite(balance) and balance > mark
    if above and exact:
        return _raised(conf, balance, now, "exact", 0.0, expired)
    if above:
        explained = _explained(conf.mark_utc, mark - conf.flows, balance, tol, deals)
        if explained is not None:
            return _raised(conf, balance, now, "deals", explained, expired)
        if pend is not None and abs(balance - pend) <= tol:
            return _raised(conf, min(balance, pend), now, "second_read", 0.0, expired)
    if pend is not None:
        at = conf.candidate_at()
        backed = _explained(at, pend, balance, tol, deals) if math.isfinite(balance) else None
        if backed is not None and at is not None:
            raised = _raised(conf, pend, at, "candidate_deals", backed, expired)
            if not (balance > pend):
                return raised
            after = step(raised.conf, pend, balance, now, tolerance, deals)
            if after.outcome == "raised":
                return after.model_copy(update={"by": "candidate_deals", "expired": expired})
            return raised.model_copy(update={"conf": after.conf})      # raised to the candidate, this read pending
    if not above:
        if pend is None:
            return HwmStep(mark=mark, conf=conf, outcome="unchanged", expired=expired)
        return HwmStep(mark=mark, conf=conf.without_candidate(), outcome="refused", dropped=pend, expired=expired)
    candidate = conf.model_copy(update={"pending_balance": balance, "candidate_utc": now,
                                        "pending_since_utc": conf.pending_since_utc or now})
    return HwmStep(mark=mark, conf=candidate, outcome="pending", dropped=pend, expired=expired)


class CapitalFlow(FrozenRecord):
    """One balance operation in the deal history (MT5 DEAL_BALANCE): the owner's deposit (+) or withdrawal (-), in
    the account currency. Never P&L (ADR 0042 D1). No ticket: the journal records the amount, never an identifier."""

    ts_utc: UtcTimestamp
    amount: float


def capital_flows(deals: pd.DataFrame, after: pd.Timestamp) -> list[CapitalFlow]:
    """The balance operations in an MT5 deal history stamped after `after`, oldest first (credit, trade, commission
    and fee deals are not capital flows). A balance operation at or after a cancelled deal (MT5 13/14) in the read is
    left out (review of 14f154aa, MEDIUM): possibly the broker reversing that deal's P&L (ledger `follows_cancel`,
    ADR 0015), so it stays P&L, never a withdrawal that would hide a loss. Raises on a history without MT5's deal
    columns (the paper broker)."""
    from goldbot.ops.ledger import classify_balance_deals, normalize_deals
    rows = classify_balance_deals("", normalize_deals(deals))
    return [CapitalFlow(ts_utc=b.ts_utc, amount=b.amount) for b in rows
            if b.kind in ("deposit", "withdrawal") and not b.follows_cancel and b.ts_utc > after and b.amount != 0]


FlowRefusal = Literal["no_anchor", "unreadable", "unreconciled"]


class FlowStep(FrozenRecord):
    """What the capital flows of one read did to the mark."""

    conf: HwmConfirmation
    applied: bool = False
    net: float = 0.0                                    # the net flow added to the mark (0 when not applied)
    reason: FlowRefusal | None = None                   # why flows after the anchor were not applied


def apply_flows(conf: HwmConfirmation, balance: float, tolerance: float, flows: list[CapitalFlow],
                deals: Callable[[pd.Timestamp], pd.DataFrame], *, lower: bool = True) -> FlowStep:
    """ADR 0042 D3: add the net flow stamped after the mark's anchor to the mark (a withdrawal lowers it by exactly its
    amount; never below 0, where the engine sets the mark afresh). Flows from before the anchor are already in the mark.

    Confirmed by the deal history only, and only when it reconciles: mark - flows already applied + every balance
    change since the anchor must be within `tolerance` x mark of this balance read. That refuses a flow the mark
    already holds (a deal clock skewed past the anchor, a rise an older release applied): counted twice, a deposit
    would be a phantom drawdown of its own size and trip the 12% kill switch. A refused flow leaves the mark to the
    ADR 0018 rule (a deposit then raises it to the balance; a withdrawal reads as a drawdown, the fail-safe side).
    An applied flow clears the candidate: it was the balance before the flow was seen. `lower=False` (real money,
    review of 14f154aa): a withdrawal is never applied, so it reads as a drawdown (tighten-only); the reconciliation is
    unchanged, as the mark then still stands for the balance at its anchor."""
    flows = flows if lower else [f for f in flows if f.amount > 0]
    mine = [f for f in flows if conf.mark_utc is not None and f.ts_utc > conf.mark_utc]
    if not flows or (conf.mark_utc is not None and not mine):
        return FlowStep(conf=conf)
    if conf.mark_utc is None:
        return FlowStep(conf=conf, reason="no_anchor")
    try:
        change, _, _ = deals_balance_change(deals(conf.mark_utc))
    except Exception as exc:
        log.warning("deal history unreadable for a capital flow (%s); the mark keeps the ADR 0018 rule",
                    type(exc).__name__)
        return FlowStep(conf=conf, reason="unreadable")
    if not math.isfinite(balance) or abs(conf.mark - conf.flows + change - balance) > tolerance * conf.mark:
        return FlowStep(conf=conf, reason="unreconciled")
    net = sum(f.amount for f in mine)
    new = conf.without_candidate().model_copy(update={"mark": max(0.0, conf.mark + net), "flows": conf.flows + net})
    return FlowStep(conf=new, applied=True, net=net)


def _raised(conf: HwmConfirmation, to: float, at: pd.Timestamp, by: By, deposits: float,
            expired: float | None) -> HwmStep:
    new = HwmConfirmation(account_id=conf.account_id, mark=to, mark_utc=at)
    return HwmStep(mark=to, conf=new, outcome="raised", by=by, deposits=deposits, expired=expired)


def _explained(since: pd.Timestamp | None, start: float, balance: float, tol: float,
               deals: Callable[[pd.Timestamp], pd.DataFrame]) -> float | None:
    """The deposits among the deals since `since` when `start` + their balance change is within `tol` of `balance`,
    else None. No anchor, an unreadable or non-MT5 history, or no balance-changing deal never explains anything."""
    if since is None:
        return None
    try:
        change, deposits, counted = deals_balance_change(deals(since))
    except Exception as exc:
        log.warning("deal history unreadable for the high-water mark (%s); a second read confirms the rise",
                    type(exc).__name__)
        return None
    if counted == 0 or abs(start + change - balance) > tol:
        return None
    return deposits


__all__ = ["CANDIDATE_MAX_AGE", "HWM", "WRITE_BUSY_TIMEOUT_MS", "CapitalFlow", "FlowStep", "HwmConfirmation", "HwmStep",
           "HwmStore", "apply_flows", "capital_flows", "deals_balance_change", "read_only_confirmation", "step"]
