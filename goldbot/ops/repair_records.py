"""Repair lost exploration records (trading-safety MEDIUM 2026-10-26).

An exploration close whose record failed `closed_record_retries` writes is given up as lost (`closed_records_lost` in
the engine state, a failing health check). Its loss counts on its own day and its arm stays spent after it ("on a later
day its arm stays a key at 0", `exploration.realised`) until the record is written. This writes it, with the engine
stopped:

  python -m goldbot.ops.run repair-lost-record <account> [--state DIR] [--position ID ...] [--dry-run]

Each lost id with an open exploration ledger row is read from the broker's deal history (`deals.exit_summary`, as
the engine reads a close) and its row is closed: exit, reason, P&L, commission, swap, `censored` (a record written
after the fact is never read as a clean label). Its id then leaves the engine's lost list (orders_<account>.json), so
the engine's health check clears at its next start. What it cannot repair stays listed and is named: another ledger's
record (`not_exploration`: a strategy's or a cost probe's), no exit deal yet (`no_exit_deal`), deals unreadable
(`deals_unreadable`). It writes records only: it places, closes and changes no order.
It holds the account's engine lock (`engine_lock`: an engine start is refused meanwhile, and a running engine refuses
the repair), and reads the engine's unit and heartbeat again immediately before writing (any change: nothing written).
Exit 0 all repaired (or none lost), 1 some left, 2 refused (engine running, unknown account, no bridge endpoint).
"""
from __future__ import annotations

import argparse
import time
from collections.abc import Callable, Collection
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from goldbot.base import FrozenRecord

EXIT_OK, EXIT_LEFT, EXIT_REFUSED = 0, 1, 2

Outcome = Literal["repaired", "already_recorded", "not_exploration", "no_exit_deal", "deals_unreadable"]


class EngineStarted(RuntimeError):
    """The engine may have started since the repair began: nothing was written."""


class Repaired(FrozenRecord):
    """One lost id and what became of it."""
    position_id: int
    outcome: Outcome
    arm_id: str | None = None
    pnl: float | None = None


def repair(state_dir: str | Path, account_id: str, broker: Any, *, positions: Collection[int] | None = None,
           dry_run: bool = False, check: Callable[[], str | None] | None = None) -> list[Repaired]:
    """Repair the account's lost exploration records (`positions`: only those ids); the engine must be stopped (it
    rewrites the orders file the engine saves; `main` holds the account's engine lock). Everything is read first;
    `check` (why the engine may be running, else None) is asked again immediately before anything is written, and
    an answer raises EngineStarted with nothing written. A dry run reads and reports, and writes nothing."""
    from goldbot.engine.exploration import LEDGER, ledger_db
    from goldbot.engine.store import JsonEngineStore
    from goldbot.execution.deals import exit_summary
    store = JsonEngineStore(Path(state_dir), account_id)
    snap = store.load_orders()
    if snap is None:
        return []
    ids = [p for p in snap.records_lost if positions is None or p in positions]
    db = ledger_db(str(state_dir))
    out: list[Repaired] = []
    writes: list[Any] = []
    for pid in ids:
        with db.connection() as conn:
            rows = LEDGER.select(conn, "account_id = ? AND position_id = ?", (account_id, pid))
        if any(r.exit_utc is not None for r in rows):
            out.append(Repaired(position_id=pid, outcome="already_recorded", arm_id=rows[0].arm_id))
            continue
        if not rows:
            out.append(Repaired(position_id=pid, outcome="not_exploration"))
            continue
        row = rows[0]
        try:
            deals = broker.deals_since(min(row.signal_utc, row.sent_utc) - pd.Timedelta(days=1))
        except Exception:
            out.append(Repaired(position_id=pid, outcome="deals_unreadable", arm_id=row.arm_id))
            continue
        summary = exit_summary(deals, pid) if "position_id" in deals.columns else None
        if summary is None:
            out.append(Repaired(position_id=pid, outcome="no_exit_deal", arm_id=row.arm_id))
            continue
        writes.append(row.model_copy(update={
            "exit_utc": summary["exit_utc"], "exit_price": summary["exit_price"],
            "exit_reason": summary["reason"] or "repaired", "swap": summary["swap"],
            "commission": summary["commission"], "pnl": summary["pnl"], "censored": True}))
        out.append(Repaired(position_id=pid, outcome="repaired", arm_id=row.arm_id, pnl=summary["pnl"]))
    done = {o.position_id for o in out if o.outcome in ("repaired", "already_recorded")}
    if dry_run or not done:
        return out
    if check is not None and (why := check()) is not None:
        raise EngineStarted(why)
    if writes:
        with db.write() as conn:
            for rec in writes:
                LEDGER.upsert(conn, rec)
    store.save_orders(snap.model_copy(update={"records_lost": [p for p in snap.records_lost if p not in done]}))
    return out


def main(argv: list[str]) -> int:
    """`repair-lost-record <account> [--state DIR] [--position ID ...] [--dry-run]`: the keyring's bridge endpoint."""
    ap = argparse.ArgumentParser(prog="python -m goldbot.ops.run repair-lost-record",
                                 description="Write lost exploration records from the broker's deals and drop them "
                                             "from the engine's lost list. Stop the engine first. Exit 0 all "
                                             "repaired, 1 some left, 2 refused.")
    ap.add_argument("account")
    ap.add_argument("--state", default="state", help="state directory (default: state)")
    ap.add_argument("--position", type=int, action="append", help="only this lost position id (repeatable)")
    ap.add_argument("--dry-run", action="store_true", help="read and report; write nothing")
    a = ap.parse_args(argv)
    from goldbot.ops import accounts, engine_lock
    acc = accounts.load_accounts(logins=False).get(a.account)
    if acc is None:
        print(f"unknown account {a.account!r} (config/accounts.yaml)")
        return EXIT_REFUSED
    try:
        fd = engine_lock.take_repair(a.state, acc.account_id)
    except BlockingIOError:
        print(f"refused: an engine of {acc.account_id} holds its lock: stop the engine first")
        return EXIT_REFUSED
    except (OSError, ImportError) as exc:
        print(f"refused: the engine lock of {acc.account_id} cannot be taken ({type(exc).__name__})")
        return EXIT_REFUSED
    try:
        return _locked(a, acc)
    finally:
        engine_lock.release(fd)


def _locked(a: argparse.Namespace, acc: Any) -> int:
    """The repair under the account's engine lock: the unit inactive and its heartbeat old, before and again
    immediately before anything is written."""
    from goldbot.ops import accounts, smoke

    def unread(t: pd.Timestamp) -> pd.DataFrame:
        raise RuntimeError("not read by the repair")

    io = smoke.SmokeIO(monotonic=time.monotonic, sleep=time.sleep, engine_state=smoke.systemctl_state,
                       calendar=unread, news=unread)
    def running() -> str | None:
        return smoke.engine_running(acc, Path(a.state), pd.Timestamp.now("UTC"), io)

    why = running()
    if why is not None:
        print(f"refused: {why.replace('bridge smoke', 'repair lost records')}")
        return EXIT_REFUSED
    endpoint = accounts.bridge_endpoint(acc.account_id)
    if endpoint is None:
        print(f"refused: no bridge endpoint for {acc.account_id} in the keyring (mt5-bridge-url/-token)")
        return EXIT_REFUSED
    from goldbot.execution.bridge import RemoteBroker
    broker = RemoteBroker(endpoint[0], endpoint[1], name=f"mt5-remote-{acc.account_id}-repair")
    try:
        out = repair(a.state, acc.account_id, broker, positions=a.position, dry_run=a.dry_run, check=running)
    except EngineStarted as exc:
        print(f"refused, nothing written: {exc}")
        return EXIT_REFUSED
    for o in out:
        pnl = f" P&L {o.pnl:.2f}" if o.pnl is not None else ""
        print(f"position {o.position_id}: {o.outcome}{f' (arm {o.arm_id})' if o.arm_id else ''}{pnl}"
              f"{' [dry run]' if a.dry_run and o.outcome == 'repaired' else ''}")
    if not out:
        print("no lost records")
    return EXIT_OK if all(o.outcome in ("repaired", "already_recorded") for o in out) else EXIT_LEFT
