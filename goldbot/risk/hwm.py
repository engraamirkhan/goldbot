"""BACKLOG 54 (ADR 0017): a rise in the balance high-water mark is applied only once it is confirmed.

The drawdown stages measure equity against `balance_closed_hwm`. Raising it on every read (`max(hwm, balance)`) let one
inflated balance read (a glitch, a half-filled account struct) raise the mark for good, so every later real read looked
like a drawdown: size-down at 8% and, two reads later, a phantom 12% kill switch. The rule (ADR 0017):

* "a rise in balance_closed_hwm is applied only once confirmed: by the deal history (closed deals and balance
  operations since the last mark account for the rise within a tolerance), or by a second balance read that agrees;
  one disagreeing read never raises it, two agreeing reads always do";
* a deposit is a balance operation in the deal history, so it explains a rise and raises the mark (as before);
* the mark is never lowered by this rule (only the owner's re-arm resets it); a refused rise is logged and recorded.

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
from goldbot.db.connection import Database, db_path
from goldbot.db.repo import RecordRepo

log = logging.getLogger(__name__)


class HwmConfirmation(FrozenRecord):
    """One account's high-water-mark confirmation state (core.db `hwm_confirmation`). S12: every optional field's
    default is the strict side. No anchor means the deal history cannot explain a rise (a second read must), and no
    candidate means a rise needs a fresh pair of reads."""

    account_id: str
    mark: float                                     # the mark this row refers to; another mark voids the row
    mark_utc: UtcTimestamp | None = None            # when that mark was set from a balance read (deal-history anchor)
    pending_balance: float | None = None            # an unconfirmed balance read above the mark
    pending_since_utc: UtcTimestamp | None = None   # when it was read


def _ns(ts: pd.Timestamp) -> int:
    return int(epoch_ns(pd.DatetimeIndex([ts]))[0])


HWM: RecordRepo[HwmConfirmation] = RecordRepo(
    "hwm_confirmation", HwmConfirmation, key="account_id", tolerant=True,
    columns={"account_id": lambda c: c.account_id,
             "ts_ns": lambda c: _ns(c.pending_since_utc or c.mark_utc or pd.Timestamp(0, tz="UTC"))})


def load_confirmation(state_dir: str | Path, account_id: str) -> HwmConfirmation | None:
    """The persisted row, or None when there is none or it cannot be read (the strict side: no anchor, no candidate).
    A missing core.db is not created by a read."""
    if not db_path(state_dir, "core").exists():
        return None
    try:
        db = Database.open(state_dir, "core")
        with db.connection() as conn:
            return HWM.get(conn, account_id)
    except Exception as exc:          # never stops the engine: the strict side instead
        log.warning("high-water-mark confirmation state unreadable (%s): a rise needs a fresh confirmation",
                    type(exc).__name__)
        return None


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


def save_confirmation(state_dir: str | Path, conf: HwmConfirmation) -> bool:
    """Upsert the row (core.db, synchronous FULL); False (logged) when it cannot be written. A lost write only loses a
    candidate or an anchor, so the next rise needs a fresh confirmation (never a rise applied on one read)."""
    try:
        db = Database.open(state_dir, "core")
        with db.write() as conn:
            HWM.upsert(conn, conf)
        return True
    except Exception as exc:
        log.warning("high-water-mark confirmation state not saved (%s): a restart forgets the pending rise",
                    type(exc).__name__)
        return False


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


class HwmStep(FrozenRecord):
    """What one balance read did to the mark."""

    mark: float
    conf: HwmConfirmation
    outcome: Outcome
    by: Literal["deals", "second_read"] | None = None   # how a rise was confirmed
    deposits: float = 0.0                               # deposits in the deal history that explained it
    dropped: float | None = None                        # a candidate this read disagreed with (a refused rise)


def step(conf: HwmConfirmation, mark: float, balance: float, now: pd.Timestamp, tolerance: float,
         deals: Callable[[pd.Timestamp], pd.DataFrame]) -> HwmStep:
    """Apply one balance read to the mark (ADR 0017). `conf` must refer to `mark` (the caller voids it otherwise);
    `tolerance` is a fraction of the mark; `deals(since)` reads the broker's deal history.

    * A read at or below the mark (or not a number) never moves it; a candidate it disagrees with is refused.
    * A read above it is confirmed when the deal history since the mark was set explains it (at least one
      balance-changing deal, and mark + their sum within the tolerance of the read), or when it agrees with the
      candidate within the tolerance (then the mark rises to the lower of the two). Otherwise it becomes the candidate
      (replacing, and refusing, a candidate it disagrees with), and the next read must confirm it.
    """
    tol = tolerance * mark
    pend = conf.pending_balance
    if not (math.isfinite(balance) and balance > mark):
        if pend is None:
            return HwmStep(mark=mark, conf=conf, outcome="unchanged")
        cleared = conf.model_copy(update={"pending_balance": None, "pending_since_utc": None})
        return HwmStep(mark=mark, conf=cleared, outcome="refused", dropped=pend)
    explained = _explained(conf, mark, balance, tol, deals)
    if explained is not None:
        return _raised(conf, balance, now, "deals", explained)
    if pend is not None and abs(balance - pend) <= tol:
        return _raised(conf, min(balance, pend), now, "second_read", 0.0)
    candidate = conf.model_copy(update={"pending_balance": balance, "pending_since_utc": now})
    return HwmStep(mark=mark, conf=candidate, outcome="pending", dropped=pend)


def _raised(conf: HwmConfirmation, to: float, now: pd.Timestamp, by: Literal["deals", "second_read"],
            deposits: float) -> HwmStep:
    new = HwmConfirmation(account_id=conf.account_id, mark=to, mark_utc=now)
    return HwmStep(mark=to, conf=new, outcome="raised", by=by, deposits=deposits)


def _explained(conf: HwmConfirmation, mark: float, balance: float, tol: float,
               deals: Callable[[pd.Timestamp], pd.DataFrame]) -> float | None:
    """The deposits among the deals when the deal history since the anchor explains the read, else None. No anchor,
    an unreadable or non-MT5 history, or no balance-changing deal never explains a rise."""
    if conf.mark_utc is None:
        return None
    try:
        change, deposits, counted = deals_balance_change(deals(conf.mark_utc))
    except Exception as exc:
        log.warning("deal history unreadable for the high-water mark (%s); a second read confirms the rise",
                    type(exc).__name__)
        return None
    if counted == 0 or abs(mark + change - balance) > tol:
        return None
    return deposits


__all__ = ["HWM", "HwmConfirmation", "HwmStep", "deals_balance_change", "load_confirmation", "read_only_confirmation",
           "save_confirmation", "step"]
