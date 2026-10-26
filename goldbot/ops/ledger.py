"""Broker ledger: a daily balance snapshot per account, and goldbot's trade records reconciled with the broker
(docs/finance/audit-framework.md GAP-07 and GAP-08; audit checks RC-8 and RC-9).

Design rules quoted from the audit:
* RC-8 "Every engine position (magic in the account's range) in the broker's deals has exactly one closed record or
  is still open; no closed record without broker deals."
* RC-9 "Opening balance + net closed P&L + deposits - withdrawals = closing balance."

The daily `broker_ledger` job (goldbot/ops/jobs.py) calls `run_ledger` per account. It only reads from the broker
(`account`, `positions`, `deals_since`: read-only bridge methods, retried once, never an order) and writes aux.db:

* Snapshot (GAP-07). Balance, equity, margin, free margin and currency at the read, plus `deals_through_utc`, the
  newest deal in the read. The deal history is read before and after the account and must be the same both times
  (`STABLE_READS` attempts), so the balance and the deals describe the same moment. Stamped with the real read time;
  the scheduler slot's UTC date keys the row (one per account and day, `balance_snapshots`; a re-run replaces it).
  `deals_through_utc` = max(newest deal, read time - MIN_WINDOW): the cursor never trails the read by more than a
  day, so an idle account never drifts into a new baseline. The login, server and deal comments are never stored;
  only `identity`, a truncated hash of (account, login, server) that detects a login change. It is never printed.
* Balance deals. MT5 non-trade deals: DEAL_TYPE_BALANCE (2) is a deposit (> 0) or a withdrawal (< 0), CREDIT (3) is
  kept apart (credit is not balance), CHARGE, CORRECTION, BONUS, INTEREST and the rest are `adjustment`; commission
  deals (7-11) count as commission. Booked once per deal ticket (`balance_deals`): with the tie-out they explain, or,
  on a baseline day, everything read flagged `baseline` (already in the opening balance). A cancelled deal (13/14)
  has its P&L zeroed and reversed by a separate balance operation; balance operations at or after it are tagged
  `follows_cancel` and the report WARNs (ADR 0015 lists the limits).
* Tie-out (RC-9). Over the deals after the previous snapshot's `deals_through_utc`:

      unexplained = (closing balance - opening balance)
                    - (trade P&L + commission + swap + fee + deposits + withdrawals + adjustments)

  |unexplained| > ledger.balance_tolerance + ledger.balance_tolerance_per_deal x deals is a FAIL: money moved that
  no deal accounts for; before it is committed the read is repeated once, RECHECK_S later (a balance can change a
  moment before its deal shows in the history). The first snapshot, a currency change or a previous snapshot older than MAX_LOOKBACK has no
  tie-out (a new baseline, with a note). A broker adjustment is explained but WARNs: someone should know why.
* Trades (RC-8), by position id, over closes since the previous snapshot (at least the last day) less the grace:
  goldbot's positions are those whose deals or position carry a magic in magic_base .. magic_base + 99 (as
  `GuardedBroker`), less the bridge smoke's magic_base + 99 (ADR 0021: it has no engine record by design, so its
  positions are listed in `smoke_positions` and the note, never reconciled; RC-9 still counts their P&L) and less
  the closed cost probes found in the probe ledger (ADR 0037: aux.db cost_probes is their record; named in the note).
  Against the closed-trade records (`gates_phase.load_closed_trades`) and the engine's open trades
  (state/orders_<account>.json "open"):
      missing   at the broker (open, or fully closed by its deals) but not in goldbot's records
      extra     in goldbot's records but neither open at the broker nor in its deals
      mismatch  both, differing in side, lots (size at entry for a closed trade, current size for an open one), entry
                or exit price (> ledger.price_tolerance, lots-weighted over the deals), net P&L (> ledger.pnl_tolerance,
                profit + commission + swap + fee of the position's deals) or P&L currency (the own-fills fallback)
  A broker close younger than ledger.close_grace_minutes is `pending`, not missing (the engine writes the record on
  its next pass). Any difference WARNs.

The report (`ledger_reports`, one per account and day) holds the amounts; the health check reconcile:<account>
(`ledger_status`, folded into `crossfeed.check_reconciliation`) and the job's output carry counts only, so amounts stay
in local state. `python -m goldbot.ops.ledger show <account>` prints the latest report and snapshots for the owner's
monthly tie-out.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import pandas as pd

from goldbot.base import FrozenRecord, UtcTimestamp, load_record_json
from goldbot.config import LedgerSettings
from goldbot.data.timeutil import epoch_ns
from goldbot.db.connection import Database, db_path
from goldbot.db.repo import RecordRepo
from goldbot.execution.broker import SMOKE_MAGIC_OFFSET
from goldbot.ops.gates_phase import ClosedTrade, load_closed_trades

if TYPE_CHECKING:
    from goldbot.execution.broker import AccountInfo, Position
    from goldbot.ops.accounts import Account
    from goldbot.ops.health import HealthContext, Status

# MT5 ENUM_DEAL_TYPE / ENUM_DEAL_ENTRY
DEAL_BUY, DEAL_SELL, DEAL_BALANCE, DEAL_CREDIT = 0, 1, 2, 3
COMMISSION_DEALS = frozenset({7, 8, 9, 10, 11})   # COMMISSION, _DAILY, _MONTHLY, _AGENT_DAILY, _AGENT_MONTHLY
CANCELLED_DEALS = frozenset({13, 14})              # BUY_CANCELED, SELL_CANCELED: profit zeroed, reversed by a balance deal
ENTRY_IN = 0
ENTRY_OUT = (1, 2, 3)                              # OUT, INOUT, OUT_BY (as the engine's _exit_deals)
MAGIC_SPAN = 100                                   # an account's goldbot magics: magic_base .. magic_base + 99
STABLE_READS = 3                                   # attempts at a deal history that does not change around the account read
MAX_LOOKBACK = pd.Timedelta(days=31)               # deal history read at most this far back
MIN_WINDOW = pd.Timedelta(days=1)                  # trades closed in at least the last day are reconciled
READ_MARGIN = pd.Timedelta(hours=1)                # deal reads start this much earlier (server-time conversion edges)
RECHECK_S = 5.0                                    # an unexplained balance is read again after this, once, before a FAIL
LOTS_EPS = 1e-6
DEAL_COLUMNS = ("ticket", "ts_utc", "type", "entry", "position_id", "volume", "price", "magic")
MONEY_COLUMNS = ("profit", "commission", "swap", "fee")


class LedgerReadError(RuntimeError):
    """The broker's deal history could not be read consistently (or is not an MT5 deal history)."""


class BalanceSnapshot(FrozenRecord):
    account_id: str
    ts_utc: UtcTimestamp
    balance: float
    equity: float
    margin: float
    margin_free: float
    currency: str
    # every deal up to here was in this read: max(newest deal, read time - MIN_WINDOW). The next tie-out starts after
    # it; it never trails the read by more than a day, so an idle account never drifts into a new baseline
    deals_through_utc: UtcTimestamp | None = None
    slot_utc: UtcTimestamp | None = None            # the scheduler slot: its UTC date keys the row (ts_utc = read time)
    # one-way fingerprint of the terminal's login and server (never the login itself): a change means the account was
    # switched, so the previous balance belongs to another account and the tie-out starts a new baseline. None on rows
    # written before this field (nothing to compare)
    identity: str | None = None


class BalanceDeal(FrozenRecord):
    account_id: str
    ticket: int
    ts_utc: UtcTimestamp
    kind: Literal["deposit", "withdrawal", "credit", "adjustment"]
    deal_type: int                                  # MT5 ENUM_DEAL_TYPE
    amount: float                                   # account currency, broker sign
    # read on a baseline day (first snapshot, new baseline): in the opening balance or in an un-tied gap, so a monthly
    # ledger must not add it to the opening balance again
    baseline: bool = False
    # a balance operation at or after a cancelled deal (MT5 13/14) in the same read: possibly the broker reversing that
    # deal's P&L, not the owner's deposit or withdrawal (ADR 0015)
    follows_cancel: bool = False


class BalanceTieOut(FrozenRecord):
    from_utc: UtcTimestamp                          # deals after this (the previous snapshot's deals_through_utc)
    to_utc: UtcTimestamp
    opening: float
    closing: float
    trade_pnl: float
    commission: float
    swap: float
    fee: float
    deposits: float
    withdrawals: float
    adjustments: float
    deals: int
    unexplained: float
    tolerance: float


class TradeDiff(FrozenRecord):
    position_id: int
    kind: Literal["missing", "extra", "mismatch"]
    where: Literal["open", "closed"]
    fields: list[str] = []
    detail: str = ""


class LedgerReport(FrozenRecord):
    account_id: str
    ts_utc: UtcTimestamp                            # the broker read
    window_from: UtcTimestamp                       # broker closes from here to ts_utc - grace are reconciled
    currency: str
    positions_checked: int
    matched: int
    pending: int                                    # closed at the broker within the grace, record not written yet
    diffs: list[TradeDiff] = []
    tie_out: BalanceTieOut | None = None
    status: Literal["ok", "warn", "fail"]
    note: str = ""
    slot_utc: UtcTimestamp | None = None            # the scheduler slot: its UTC date keys the row
    rechecked: bool = False                         # an unexplained balance was read again before this verdict
    # ADR 0021: bridge smoke positions (magic_base + SMOKE_MAGIC_OFFSET) open or closed in the read, listed apart: they
    # have no engine record by design (the X7 departure), so RC-8 does not reconcile them; RC-9 still counts their P&L
    smoke_positions: list[int] = []

    def counts(self) -> dict[str, int]:
        return {k: sum(d.kind == k for d in self.diffs) for k in ("missing", "extra", "mismatch")}

    def summary(self) -> dict[str, Any]:
        """Counts and verdicts only (no amounts): the job's output and the health reason."""
        return {"status": self.status, "positions": self.positions_checked, "matched": self.matched,
                "pending": self.pending, **self.counts(), "smoke": len(self.smoke_positions),
                "balance": "baseline" if self.tie_out is None else _balance_verdict(self.tie_out), "note": self.note}


class _SystemOpen(FrozenRecord):
    position_id: int
    side: int
    lots: float
    open_price: float | None = None
    opened_utc: UtcTimestamp | None = None


class _BrokerTrade(FrozenRecord):
    position_id: int
    side: int
    lots: float | None                              # entry volume; None when the entry deal is outside the read
    entry_price: float | None
    exit_price: float | None
    pnl: float
    last_exit_utc: UtcTimestamp | None
    closed: bool


def _ns(ts: pd.Timestamp) -> int:
    return int(epoch_ns(pd.DatetimeIndex([ts]))[0])


def _key(account_id: str, part: str) -> str:
    return f"{account_id}|{part}"


def _day_key(account_id: str, slot: pd.Timestamp | None, ts: pd.Timestamp) -> str:
    return _key(account_id, f"{slot if slot is not None else ts:%Y-%m-%d}")


SNAPSHOTS: RecordRepo[BalanceSnapshot] = RecordRepo(
    "balance_snapshots", BalanceSnapshot, key="key", tolerant=True,
    columns={"key": lambda s: _day_key(s.account_id, s.slot_utc, s.ts_utc), "account_id": lambda s: s.account_id,
             "ts_ns": lambda s: _ns(s.ts_utc)})
DEALS: RecordRepo[BalanceDeal] = RecordRepo(
    "balance_deals", BalanceDeal, key="key", tolerant=True,
    columns={"key": lambda d: _key(d.account_id, str(d.ticket)), "account_id": lambda d: d.account_id,
             "ts_ns": lambda d: _ns(d.ts_utc), "kind": lambda d: d.kind})
REPORTS: RecordRepo[LedgerReport] = RecordRepo(
    "ledger_reports", LedgerReport, key="key", tolerant=True,
    columns={"key": lambda r: _day_key(r.account_id, r.slot_utc, r.ts_utc), "account_id": lambda r: r.account_id,
             "ts_ns": lambda r: _ns(r.ts_utc), "status": lambda r: r.status})


# ---------------------------------------------------------------------------------------------- broker reads
def normalize_deals(raw: pd.DataFrame | None) -> pd.DataFrame:
    """MT5 deal history (MT5Broker.deals_since, direct or over the bridge) with typed columns; money columns a terminal
    does not report are 0. Raises LedgerReadError on a history without MT5's deal columns (the paper broker)."""
    cols = [*DEAL_COLUMNS, *MONEY_COLUMNS]
    if raw is None or raw.empty:
        return pd.DataFrame({c: pd.Series(dtype="float64") for c in cols}).astype({"ts_utc": "datetime64[ns, UTC]"})
    missing = [c for c in DEAL_COLUMNS if c not in raw.columns]
    if missing:
        raise LedgerReadError(f"deal history lacks {', '.join(missing)}: not an MT5 deal history")
    d = raw.copy()
    for c in MONEY_COLUMNS:
        d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0.0) if c in d.columns else 0.0
    for c in ("ticket", "type", "entry", "position_id", "magic"):
        d[c] = pd.to_numeric(d[c], errors="coerce").fillna(-1).astype("int64")
    for c in ("volume", "price"):
        d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0.0)
    d["ts_utc"] = pd.to_datetime(d["ts_utc"], utc=True)
    return d[cols].sort_values(["ts_utc", "ticket"]).reset_index(drop=True)


def _read_stable(broker: Any, since: pd.Timestamp) -> tuple[AccountInfo, pd.DataFrame, list[Position]]:
    """The account, its positions and the deal history, the deals read before and after and equal both times."""
    for _ in range(STABLE_READS):
        before = normalize_deals(broker.deals_since(since))
        info = broker.account()
        positions = list(broker.positions())
        after = normalize_deals(broker.deals_since(since))
        if before["ticket"].tolist() == after["ticket"].tolist():
            return info, after, positions
    raise LedgerReadError(f"the deal history changed during each of {STABLE_READS} reads; the snapshot is retried "
                          "on the next run")


def _ours(magic: int, magic_base: int) -> bool:
    """An engine position of this account (RC-8): the account's magic range less the bridge smoke's (ADR 0021)."""
    return magic_base <= int(magic) < magic_base + MAGIC_SPAN and not _smoke(magic, magic_base)


def _smoke(magic: int, magic_base: int) -> bool:
    return int(magic) == magic_base + SMOKE_MAGIC_OFFSET


def probe_note(probes: set[int]) -> str:
    """ADR 0037: closed cost probes have no closed-trade record by design (the probe ledger, aux.db cost_probes, is
    theirs), so RC-8 does not count them missing; RC-9 still counts their P&L."""
    return f"{len(probes)} cost probe position(s) reconciled in the cost-probe ledger (ADR 0037)"


def smoke_positions(deals: pd.DataFrame, positions: list[Position], magic_base: int) -> list[int]:
    """The bridge smoke's positions in the read (its deals or open positions): listed, not reconciled (ADR 0021)."""
    t = deals[deals["type"].isin([DEAL_BUY, DEAL_SELL]) & (deals["position_id"] > 0)]
    ids = {int(p) for p, m in zip(t["position_id"], t["magic"]) if _smoke(m, magic_base)}
    return sorted(ids | {p.position_id for p in positions if _smoke(p.magic, magic_base)})


# ---------------------------------------------------------------------------------------------- balance (RC-9)
def classify_balance_deals(account_id: str, deals: pd.DataFrame, *, baseline: bool = False) -> list[BalanceDeal]:
    """Deposits, withdrawals, credit and adjustments among the deals (trade, commission and cancelled deals excluded).
    A balance operation at or after the first cancelled deal in `deals` is tagged `follows_cancel`."""
    cancelled = deals.loc[deals["type"].isin(list(CANCELLED_DEALS)), "ts_utc"]
    first_cancel = cancelled.min() if not cancelled.empty else None
    rows = deals[~deals["type"].isin([DEAL_BUY, DEAL_SELL, *COMMISSION_DEALS, *CANCELLED_DEALS])]
    out = []
    for ticket, ts, t, amount in zip(rows["ticket"].tolist(), rows["ts_utc"].tolist(), rows["type"].tolist(),
                                     rows["profit"].tolist()):
        kind: Literal["deposit", "withdrawal", "credit", "adjustment"] = (
            ("deposit" if amount >= 0 else "withdrawal") if t == DEAL_BALANCE
            else "credit" if t == DEAL_CREDIT else "adjustment")
        out.append(BalanceDeal(account_id=account_id, ticket=int(ticket), ts_utc=ts, kind=kind, deal_type=int(t),
                               amount=float(amount), baseline=baseline,
                               follows_cancel=t == DEAL_BALANCE and first_cancel is not None and ts >= first_cancel))
    return out


def tie_out(prev: BalanceSnapshot, snap: BalanceSnapshot, deals: pd.DataFrame, cfg: LedgerSettings) -> BalanceTieOut:
    """RC-9 over the deals after `prev.deals_through_utc` (see the module docstring)."""
    start = prev.deals_through_utc if prev.deals_through_utc is not None else prev.ts_utc
    w = deals[deals["ts_utc"] > start]
    trade = w["type"].isin([DEAL_BUY, DEAL_SELL])
    comm_deal = w["type"].isin(list(COMMISSION_DEALS))
    bal = w["type"] == DEAL_BALANCE
    other = ~(trade | comm_deal | bal | (w["type"] == DEAL_CREDIT) | w["type"].isin(list(CANCELLED_DEALS)))
    parts = {"trade_pnl": float(w.loc[trade, "profit"].sum()),
             "commission": float(w["commission"].sum() + w.loc[comm_deal, "profit"].sum()),
             "swap": float(w["swap"].sum()), "fee": float(w["fee"].sum()),
             "deposits": float(w.loc[bal & (w["profit"] > 0), "profit"].sum()),
             "withdrawals": float(w.loc[bal & (w["profit"] < 0), "profit"].sum()),
             "adjustments": float(w.loc[other, "profit"].sum())}
    parts = {k: round(v, 8) for k, v in parts.items()}
    unexplained = round((snap.balance - prev.balance) - sum(parts.values()), 8)
    return BalanceTieOut(from_utc=start, to_utc=snap.ts_utc, opening=prev.balance, closing=snap.balance,
                         deals=len(w), unexplained=unexplained,
                         tolerance=cfg.balance_tolerance + cfg.balance_tolerance_per_deal * len(w), **parts)


def _balance_verdict(t: BalanceTieOut) -> str:
    return "ties out" if abs(t.unexplained) <= t.tolerance + 1e-9 else "unexplained"


# ---------------------------------------------------------------------------------------------- trades (RC-8)
def broker_trades(deals: pd.DataFrame, magic_base: int) -> dict[int, _BrokerTrade]:
    """goldbot's positions in the deal history (any deal of the position with a magic in the account's range)."""
    t = deals[deals["type"].isin([DEAL_BUY, DEAL_SELL]) & (deals["position_id"] > 0)]
    ours = {int(p) for p, m in zip(t["position_id"], t["magic"]) if _ours(m, magic_base)}
    out: dict[int, _BrokerTrade] = {}
    for pid in sorted(ours):
        g = t[t["position_id"] == pid]
        ent, ex = g[g["entry"] == ENTRY_IN], g[g["entry"].isin(ENTRY_OUT)]
        lots_in, lots_out = float(ent["volume"].sum()), float(ex["volume"].sum())

        def vwap(rows: pd.DataFrame) -> float | None:
            v = float(rows["volume"].sum())
            return float((rows["price"] * rows["volume"]).sum()) / v if v > 0 else None
        if not ent.empty:
            side = 1 if ent["type"].tolist()[0] == DEAL_BUY else -1
        else:                                          # an exit sells a long and buys back a short
            side = -1 if ex["type"].tolist()[0] == DEAL_BUY else 1
        out[pid] = _BrokerTrade(
            position_id=pid, side=side, lots=lots_in or None, entry_price=vwap(ent), exit_price=vwap(ex),
            pnl=float((g["profit"] + g["commission"] + g["swap"] + g["fee"]).sum()),
            last_exit_utc=ex["ts_utc"].max() if not ex.empty else None,
            closed=lots_out > 0 and (ent.empty or lots_out >= lots_in - LOTS_EPS))
    return out


def system_open(state_dir: Path, account_id: str) -> tuple[dict[int, _SystemOpen], str | None]:
    """The engine's open trades (state/orders_<account>.json "open"); (rows, error). Read tolerantly: only the
    fields compared here."""
    f = Path(state_dir) / f"orders_{account_id}.json"
    if not f.exists():
        return {}, None
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        out = {}
        for k, v in (d.get("open") or {}).items():
            out[int(k)] = _SystemOpen(position_id=int(k), side=int(v["side"]), lots=float(v["lots"]),
                                      open_price=v.get("open_price") or v.get("entry"),
                                      opened_utc=v.get("opened_utc") or v.get("entry_bar_ts"))
        return out, None
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        return {}, f"orders_{account_id}.json unreadable ({type(exc).__name__}): open positions not compared"


def _differs(a: float | None, b: float | None, tol: float) -> bool:
    return a is not None and b is not None and abs(float(a) - float(b)) > tol + 1e-9


def _closed_fields(r: ClosedTrade, b: _BrokerTrade, currency: str, cfg: LedgerSettings) -> list[str]:
    out = []
    if r.side is not None and r.side != b.side:
        out.append("side")
    if _differs(r.lots, b.lots, LOTS_EPS):
        out.append("lots")
    if _differs(r.entry_price, b.entry_price, cfg.price_tolerance):
        out.append("entry_price")
    if _differs(r.exit_price, b.exit_price, cfg.price_tolerance):
        out.append("exit_price")
    if r.pnl_currency is not None and r.pnl_currency != currency:
        out.append("pnl_currency")                     # the unconverted own-fills fallback: pnl is not comparable
    elif _differs(r.pnl, b.pnl, cfg.pnl_tolerance):
        out.append("pnl")
    return out


def _open_fields(s: _SystemOpen, p: Position, cfg: LedgerSettings) -> list[str]:
    out = ["side"] if s.side != p.side else []
    if _differs(s.lots, p.lots, LOTS_EPS):
        out.append("lots")
    if _differs(s.open_price, p.open_price, cfg.price_tolerance):
        out.append("open_price")
    return out


def reconcile_trades(trades: dict[int, _BrokerTrade], broker_open: dict[int, Position],
                     sys_open: dict[int, _SystemOpen] | None, records: dict[int, ClosedTrade], *,
                     window_from: pd.Timestamp, now: pd.Timestamp, currency: str,
                     cfg: LedgerSettings) -> tuple[list[TradeDiff], int, int, int]:
    """(diffs, positions checked, matched, pending) by position id (see the module docstring). `sys_open` None: the
    engine's open trades are unreadable, so open positions are not compared."""
    grace_from = now - pd.Timedelta(minutes=cfg.close_grace_minutes)
    s_open = sys_open or {}
    ids = set(broker_open) if sys_open is not None else set()
    ids |= {p for p, b in trades.items() if b.closed and b.last_exit_utc is not None and b.last_exit_utc >= window_from}
    ids |= set(s_open) | {p for p, r in records.items() if r.exit_utc >= window_from}
    diffs: list[TradeDiff] = []
    checked = matched = pending = 0
    for pid in sorted(ids):
        before = len(diffs)
        if pid in broker_open:
            if pid in s_open:
                if f := _open_fields(s_open[pid], broker_open[pid], cfg):
                    diffs.append(TradeDiff(position_id=pid, kind="mismatch", where="open", fields=f))
            elif pid in records:
                diffs.append(TradeDiff(position_id=pid, kind="mismatch", where="open", fields=["status"],
                                       detail="recorded closed, still open at the broker"))
            else:
                diffs.append(TradeDiff(position_id=pid, kind="missing", where="open",
                                       detail="open at the broker, unknown to the engine"))
        elif pid in trades and trades[pid].closed:
            b = trades[pid]
            if pid in records:
                if f := _closed_fields(records[pid], b, currency, cfg):
                    diffs.append(TradeDiff(position_id=pid, kind="mismatch", where="closed", fields=f))
            elif b.last_exit_utc is not None and b.last_exit_utc > grace_from:
                pending += 1                           # the engine records it on its next pass
                continue
            else:
                diffs.append(TradeDiff(position_id=pid, kind="missing", where="closed",
                                       detail="closed at the broker, no closed-trade record"))
        elif pid in s_open:
            diffs.append(TradeDiff(position_id=pid, kind="extra", where="open",
                                   detail="open in the engine; the broker has neither the position nor its close"))
        elif pid in records:
            diffs.append(TradeDiff(position_id=pid, kind="extra", where="closed",
                                   detail="closed-trade record with no broker deals"))
        else:
            continue                                   # not ours or nothing to compare (e.g. a still-open id read late)
        checked += 1
        matched += len(diffs) == before
    return diffs, checked, matched, pending


# ---------------------------------------------------------------------------------------------- the daily run
def _latest(db: Database, repo: RecordRepo[Any], account_id: str, before_key: str | None = None) -> Any:
    with db.read() as conn:
        where, params = "account_id = ?", [account_id]
        if before_key is not None:
            where += " AND key <> ?"
            params.append(before_key)
        rows = repo.select(conn, where, params, order_by="ts_ns")
    return rows[-1] if rows else None


_Verdict = Literal["ok", "warn", "fail"]


def account_identity(info: AccountInfo, account_id: str) -> str:
    """A truncated SHA-256 of (account id, login, server): enough to see the login change, not to recover the login."""
    return hashlib.sha256(f"{account_id}|{info.login}|{info.server}".encode()).hexdigest()[:16]


def _assess(prev: BalanceSnapshot | None, info: AccountInfo, deals: pd.DataFrame, read_at: pd.Timestamp,
            since: pd.Timestamp, slot: pd.Timestamp, acc_id: str,
            cfg: LedgerSettings) -> tuple[BalanceSnapshot, BalanceTieOut | None, _Verdict, list[str]]:
    """(snapshot, tie-out, balance status, notes) of one stable read."""
    newest = deals["ts_utc"].max() if not deals.empty else None
    through = read_at - MIN_WINDOW if newest is None else max(newest, read_at - MIN_WINDOW)
    snap = BalanceSnapshot(account_id=acc_id, ts_utc=read_at, slot_utc=slot, balance=float(info.balance),
                           equity=float(info.equity), margin=float(info.margin), margin_free=float(info.margin_free),
                           currency=str(info.currency), deals_through_utc=through,
                           identity=account_identity(info, acc_id))
    notes: list[str] = []
    if prev is None:
        return snap, None, "ok", ["first snapshot: a baseline, nothing to tie out against"]
    if prev.identity is not None and snap.identity is not None and prev.identity != snap.identity:
        return snap, None, "warn", ["the account's login or server changed since the previous snapshot: a new "
                                    "baseline (the previous balance belongs to another account)"]
    if prev.currency != snap.currency:
        return snap, None, "fail", ["the account currency changed since the previous snapshot: a new baseline"]
    if (prev.deals_through_utc or prev.ts_utc) < since:
        return snap, None, "warn", [f"previous snapshot older than {MAX_LOOKBACK.days} days: a new baseline"]
    tie = tie_out(prev, snap, deals, cfg)
    status: _Verdict = "ok"
    if _balance_verdict(tie) != "ties out":
        status = "fail"
    elif tie.adjustments:
        notes.append("broker adjustment deals (charge, correction, bonus or interest) in the window")
        status = "warn"
    if deals.loc[deals["ts_utc"] > tie.from_utc, "type"].isin(list(CANCELLED_DEALS)).any():
        notes.append("cancelled deals in the window: their P&L is reversed by a balance operation (tagged)")
        status = "fail" if status == "fail" else "warn"
    return snap, tie, status, notes


def run_ledger(db: Database, broker: Any, account: Account, state_dir: Path, slot: pd.Timestamp,
               cfg: LedgerSettings, *, clock: Callable[[], pd.Timestamp] | None = None,
               sleep: Callable[[float], Any] = time.sleep) -> LedgerReport:
    """One account's snapshot, balance deals, tie-out and trade reconciliation; the rows commit in one transaction
    after every broker read (never a lock held across the network). Times are the real read time (`clock`); the
    scheduler `slot` only keys the day's rows. An unexplained balance is read again once, RECHECK_S later, before
    it is committed as a FAIL (a balance updated a moment before its deal shows in the history)."""
    clock = clock or (lambda: pd.Timestamp.now(tz="UTC"))
    slot = pd.Timestamp(slot).tz_convert("UTC")
    now = pd.Timestamp(clock()).tz_convert("UTC")
    acc_id = account.account_id
    prev: BalanceSnapshot | None = _latest(db, SNAPSHOTS, acc_id, before_key=_day_key(acc_id, slot, slot))
    window_from = now - MIN_WINDOW
    if prev is not None and prev.ts_utc < window_from:
        window_from = max(prev.ts_utc, now - MAX_LOOKBACK)
    window_from -= pd.Timedelta(minutes=cfg.close_grace_minutes)

    sys_open, open_err = system_open(state_dir, acc_id)
    trades_all, records_err = load_closed_trades(state_dir)
    records = {t.position_id: t for t in trades_all
               if t.account_id == acc_id and t.position_id is not None}
    in_window = [r for r in records.values() if r.exit_utc >= window_from]
    starts = [window_from] + [s.opened_utc for s in sys_open.values() if s.opened_utc is not None]
    starts += [r.entry_utc for r in in_window if r.entry_utc is not None]
    if prev is not None and prev.deals_through_utc is not None:
        starts.append(prev.deals_through_utc)
    since = max(min(starts), now - MAX_LOOKBACK) - READ_MARGIN

    info, deals, positions = _read_stable(broker, since)
    read_at = pd.Timestamp(clock()).tz_convert("UTC")
    snap, tie, status, notes = _assess(prev, info, deals, read_at, since, slot, acc_id, cfg)
    rechecked = status == "fail" and tie is not None
    if rechecked:
        sleep(RECHECK_S)
        info, deals, positions = _read_stable(broker, since)
        read_at = pd.Timestamp(clock()).tz_convert("UTC")
        snap, tie, status, notes = _assess(prev, info, deals, read_at, since, slot, acc_id, cfg)
    notes = [e for e in (open_err, records_err) if e] + notes

    broker_open = {p.position_id: p for p in positions if _ours(p.magic, account.magic_base)}
    from goldbot.engine.cost_probe import closed_position_ids, probe_positions
    trades = broker_trades(deals, account.magic_base)
    # ADR 0037: probes (their own magic) closed and recorded in the probe ledger are reconciled there; a probe closed
    # at the broker without a probe record stays missing
    probes = closed_position_ids(db, acc_id) & set(trades)
    if probe_positions(deals, account.magic_base) | probes:
        notes.append(probe_note(probes))
    diffs, checked, matched, pending = reconcile_trades(
        {p: b for p, b in trades.items() if p not in probes}, broker_open, None if open_err else sys_open, records,
        window_from=window_from, now=read_at, currency=snap.currency, cfg=cfg)
    if status == "ok" and (diffs or open_err or records_err):
        status = "warn"
    smoke = smoke_positions(deals, positions, account.magic_base)
    if smoke:
        notes.append(f"{len(smoke)} bridge smoke position(s) listed, not reconciled (ADR 0021)")
    rep = LedgerReport(account_id=acc_id, ts_utc=read_at, slot_utc=slot, window_from=window_from,
                       currency=snap.currency, positions_checked=checked, matched=matched, pending=pending,
                       diffs=diffs, tie_out=tie, status=status, note="; ".join(notes), rechecked=rechecked,
                       smoke_positions=smoke)
    # a tie-out books the deals it explains; a baseline books what it read, flagged (already in its opening balance)
    booked = (classify_balance_deals(acc_id, deals[deals["ts_utc"] > tie.from_utc]) if tie is not None
              else classify_balance_deals(acc_id, deals, baseline=True))
    with db.write() as conn:
        SNAPSHOTS.upsert(conn, snap)
        for d in booked:
            DEALS.insert_if_absent(conn, d)
        REPORTS.upsert(conn, rep)
    return rep


# ---------------------------------------------------------------------------------------------- readers
def snapshots(db: Database, account_id: str) -> list[BalanceSnapshot]:
    with db.read() as conn:
        return SNAPSHOTS.select(conn, "account_id = ?", [account_id], order_by="ts_ns")


def balance_deals(db: Database, account_id: str) -> list[BalanceDeal]:
    with db.read() as conn:
        return DEALS.select(conn, "account_id = ?", [account_id], order_by="ts_ns")


def _read_only(state_dir: str | Path) -> sqlite3.Connection:
    """aux.db opened read-only (mode=ro): never created, migrated, chmod-ed or switched to WAL. A plain read-only
    connection, never `immutable`: a writer may be mid-commit."""
    return sqlite3.connect(f"file:{db_path(state_dir, 'aux')}?mode=ro", uri=True, timeout=5)


def _no_table(exc: sqlite3.Error) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "no such table" in str(exc)


def latest_report(state_dir: str | Path, account_id: str) -> LedgerReport | None:
    """The newest ledger report, read-only; None without aux.db, without the table (a schema before migration 7) or
    without a report. Any other database error raises (health warns: a corrupt or locked aux.db is not "no report")."""
    if not db_path(state_dir, "aux").exists():
        return None
    conn = _read_only(state_dir)
    try:
        r = conn.execute("SELECT body FROM ledger_reports WHERE account_id = ? ORDER BY ts_ns DESC LIMIT 1",
                         (account_id,)).fetchone()
    except sqlite3.Error as exc:
        if _no_table(exc):
            return None
        raise
    finally:
        conn.close()
    return load_record_json(LedgerReport, r[0], source="db:ledger_reports") if r else None


DEFAULT_MAX_AGE_HOURS = LedgerSettings().max_age_hours


def ledger_status(ctx: HealthContext, account_id: str) -> tuple[Status, str] | None:
    """The ledger half of the health check reconcile:<account>: fail on an unexplained balance change, warn on any
    trade difference, an adjustment, a note-worthy read or a report older than ledger.max_age_hours; None when the
    job has not run yet. Counts only: no amount reaches the reason."""
    try:
        rep = latest_report(ctx.state_dir, account_id)
    except (sqlite3.Error, ValueError) as exc:
        return "warn", f"ledger report unreadable: {type(exc).__name__}"
    if rep is None:
        return None
    s = rep.summary()
    msg = (f"{s['missing']} missing, {s['extra']} extra, {s['mismatch']} mismatched of {s['positions']} positions "
           f"({s['pending']} pending); balance {s['balance']}")
    if s["balance"] == "unexplained":
        msg += f" beyond the tolerance (python -m goldbot.ops.ledger show {account_id})"
    msg += f"; {rep.note}" if rep.note else ""
    max_age = ctx.settings.ledger.max_age_hours if ctx.settings is not None else DEFAULT_MAX_AGE_HOURS
    age_h = (ctx.now - rep.ts_utc).total_seconds() / 3600
    if age_h > max_age:
        return ("fail" if rep.status == "fail" else "warn"), f"last ledger run {age_h:.0f} h ago: {msg}"
    return rep.status, msg


# ---------------------------------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    """`python -m goldbot.ops.ledger show <account> [--state state] [--days 31]`: the latest report, the snapshots and
    balance deals of the last days, as JSON on stdout (local amounts: never paste them into an issue). Opens aux.db
    read-only (mode=ro). On the brain: `sudo -u goldbot /opt/goldbot/.venv/bin/python -m goldbot.ops.ledger show
    <account> --state /opt/goldbot/state`."""
    ap = argparse.ArgumentParser(prog="python -m goldbot.ops.ledger")
    sub = ap.add_subparsers(dest="cmd", required=True)
    show = sub.add_parser("show")
    show.add_argument("account")
    show.add_argument("--state", default="state")
    show.add_argument("--days", type=int, default=31)
    a = ap.parse_args(argv)
    if not db_path(a.state, "aux").exists():
        print("no aux.db in", a.state, file=sys.stderr)
        return 1
    since = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=a.days)
    rep = latest_report(a.state, a.account)
    conn = _read_only(a.state)                 # read-only: safe to run as the service user next to the scheduler
    try:
        snaps = SNAPSHOTS.select(conn, "account_id = ?", [a.account], order_by="ts_ns")
        deals = DEALS.select(conn, "account_id = ?", [a.account], order_by="ts_ns")
    finally:
        conn.close()
    out = {"report": rep.model_dump(mode="json") if rep is not None else None,
           "snapshots": [s.model_dump(mode="json", exclude={"identity"}) for s in snaps if s.ts_utc >= since],
           "balance_deals": [d.model_dump(mode="json") for d in deals if d.ts_utc >= since]}
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
