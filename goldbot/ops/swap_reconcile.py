"""BACKLOG 175: the broker's swap on every weekend hold of a seven-night (crypto) book, reconciled automatically.

Rule (BACKLOG 153 / R130): a crypto book "is charged swap every server-midnight rollover, weekends included, with no
triple day" (`SwapSpec.seven_night`), from the item's reading of the broker, not from a measured charge; "Vantage
reports triple day 5 (Friday) for BTCUSD (`swap_rollover3days`), so reconcile whether Friday is also charged three
nights on top of the weekend ones, or not every night is charged". Acceptance: "the broker's per-position swap (deal
history) is compared ... with `paid_swap_per_oz` x lots x contract at the refresh price; the result is recorded".

The trigger is the hold itself, no one has to watch for it: the daily scheduler job `swap_reconcile` reads each
account's exploration ledger (aux.db `exploration_trades`: the swap there is the broker's own, from the position's
deals) for closed positions of a registry crypto instrument (`instruments_db.seven_night`) held across a rollover
that ends a Friday, Saturday or Sunday server day (`weekend_rollovers`: the nights the candidate schedules price
differently). Each is compared with the projection: the instruments.db rate per lot per night (profit currency, at
its refresh price, broker sign) x lots x the seven-night count, in the account currency at the order's
`fx_acct_per_usd`. Recorded once per position in aux.db `swap_reconciliations` (aux migration 35): the broker's swap,
the projection, the nights each candidate schedule counts (`nights`: seven_night, five_plus_triple,
seven_plus_triple), the nights the broker's charge implies, the schedules within tolerance of it, and ok when the
broker's swap is within `costs.swap_reconcile_tolerance` (relative) of the seven-night projection. Health
`swap_reconcile` warns, and the owner is told (NOTIFY_ON_WARN), while a hold of the last RECENT_DAYS is outside the
tolerance or could not be priced; the finance-auditor then keeps or corrects the schedule (BACKLOG 153) with a test
pinning the measured one. No weekend hold: nothing is read past the ledger and nothing is written.

Read-only on money and the broker: no broker call, no order, no setting changed; the only write is its own table."""
from __future__ import annotations

import logging
import math
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord, UtcTimestamp, load_record_json
from goldbot.data.timeutil import epoch_ns, server_wall
from goldbot.db import Database
from goldbot.ops.checks.registry import BLOCK_TAIL, register_check

if TYPE_CHECKING:
    from goldbot.ops.accounts import Account
    from goldbot.ops.checks._base import Check
    from goldbot.ops.health import HealthContext

log = logging.getLogger(__name__)

SCHEDULES = ("seven_night", "five_plus_triple", "seven_plus_triple")
WEEKEND_DAYS = (4, 5, 6)          # a rollover ending a Friday, Saturday or Sunday server day: where they differ
RECENT_DAYS = 14                  # health reads holds closed this recently (a corrected schedule clears it)
CHECK = "swap_reconcile"


class SwapReconciliation(FrozenRecord):
    """One weekend hold of a seven-night book: the broker's swap against the projection (module docstring). Money in
    the account currency, broker sign (negative = paid); `rate_per_lot` in the profit currency per lot per night."""
    account_id: str
    client_order_id: str
    arm_id: str
    symbol: str
    position_id: int | None = None
    side: int
    lots: float
    entry_utc: UtcTimestamp
    exit_utc: UtcTimestamp
    broker_swap: float
    rate_per_lot: float | None = None
    fx_acct_per_usd: float | None = None
    nights: dict[str, int]
    projected: float | None = None            # the seven-night projection; None: it cannot be priced
    implied_nights: float | None = None       # the broker's swap / the charge of one night
    deviation: float | None = None            # |broker - projected| / |projected|
    matches: list[str] = Field(default_factory=list)   # the schedules whose count is within tolerance of the implied nights
    tolerance: float
    ok: bool
    reason: str
    recorded_utc: UtcTimestamp


def _days(ts: pd.Timestamp, server_tz: str) -> np.datetime64:
    return np.datetime64(server_wall(pd.DatetimeIndex([ts]), server_tz).normalize()[0].date(), "D")


def _span(entry: pd.Timestamp, exit: pd.Timestamp, server_tz: str) -> tuple[np.datetime64, np.datetime64]:
    """[d0, d1): the server days whose closing rollover the hold was open across (as `rollover_nights`)."""
    d0 = _days(entry, server_tz)
    return d0, max(d0, _days(exit - pd.Timedelta(1, "ns"), server_tz))


def _count(d0: np.datetime64, d1: np.datetime64, weekdays: Iterable[int]) -> int:
    mask = ["0"] * 7
    for w in weekdays:
        mask[w] = "1"
    return int(np.busday_count(d0, d1, weekmask="".join(mask))) if "1" in mask else 0


def weekend_rollovers(entry: pd.Timestamp, exit: pd.Timestamp, server_tz: str) -> int:
    """Rollovers the hold was open across that end a Friday, Saturday or Sunday server day."""
    return _count(*_span(entry, exit, server_tz), WEEKEND_DAYS)


def nights(entry: pd.Timestamp, exit: pd.Timestamp, server_tz: str, triple_weekday: int | None) -> dict[str, int]:
    """The nights each candidate schedule charges: every rollover once (seven_night, BACKLOG 153's), weekday
    rollovers with the triple day three times (five_plus_triple, every other book's), and every rollover with the
    triple day three times (seven_plus_triple: Friday "on top of the weekend ones"). Without a triple day only
    seven_night. `triple_weekday`: 0 = Monday."""
    d0, d1 = _span(entry, exit, server_tz)
    out = {"seven_night": int((d1 - d0).astype(int))}
    if triple_weekday is not None:
        triples = _count(d0, d1, [triple_weekday])
        out["five_plus_triple"] = _count(d0, d1, range(5)) + 2 * triples
        out["seven_plus_triple"] = out["seven_night"] + 2 * triples
    return out


def _db(state_dir: str | Path) -> Database:
    return Database.open(state_dir, "aux")


def _rate(state_dir: Path, acc: Account, symbol: str) -> tuple[Any, int | None, str]:
    """(the instruments.db row, its triple weekday 0 = Monday or None, why none) for `symbol` on `acc`."""
    from goldbot.engine.instruments_db import InstrumentsDb, InstrumentsDbError
    from goldbot.instruments import registry
    spec = registry().instruments.get(symbol)
    sym = spec.broker_symbols.get(acc.broker) if spec is not None else None
    if sym is None:
        return None, None, f"{symbol} has no {acc.broker} symbol in the registry"
    if not (state_dir / "instruments.db").exists():
        return None, None, "no swap rate: instruments.db not written yet"
    try:
        row = InstrumentsDb(state_dir).load(acc.account_id, sym)
    except InstrumentsDbError as exc:
        return None, None, f"no swap rate: {exc}"
    if row is None:
        return None, None, f"no swap rate: instruments.db has no {sym} row"
    triple = row.swap_triple_weekday
    return row, triple - 1 if triple is not None and 1 <= triple <= 5 else None, ""


def reconcile_one(rec: Any, acc: Account, row: Any, triple: int | None, why: str, tolerance: float,
                  now: pd.Timestamp) -> SwapReconciliation:
    """One closed weekend hold (`exploration.ExplorationRecord`) against the projection (module docstring)."""
    n = nights(rec.sent_utc, rec.exit_utc, acc.server_tz, triple)
    rate = None
    if row is not None:
        rate = row.swap_long_per_lot if rec.side > 0 else row.swap_short_per_lot
        if (row.currency_profit or "USD").strip().upper() != "USD":
            rate, why = None, f"no swap rate: profit currency {row.currency_profit}, the ledger converts USD only"
        elif rate is None or not math.isfinite(rate):
            rate, why = None, "no swap rate: instruments.db rate unknown"
    fx = rec.fx_acct_per_usd
    if rate is not None and (fx is None or not math.isfinite(fx) or fx <= 0):
        rate, why = None, "no swap rate in the account currency: the order's fx_acct_per_usd is unknown"
    projected = implied = deviation = None
    matches: list[str] = []
    if rate is None:
        ok, reason = False, why
    else:
        per_night = rate * rec.lots * fx
        projected = per_night * n["seven_night"]
        if per_night != 0:
            implied = rec.swap / per_night
            matches = [k for k in SCHEDULES if k in n and abs(implied - n[k]) <= tolerance * max(n[k], 1)]
        if projected != 0:
            deviation = abs(rec.swap - projected) / abs(projected)
            ok = deviation <= tolerance
        else:
            ok = abs(rec.swap) < 0.01
        reason = (f"broker {rec.swap:+.2f} against {projected:+.2f} projected over {n['seven_night']} nights"
                  + (f" (implies {implied:.2f} nights; matches {', '.join(matches) or 'none'})"
                     if implied is not None else ""))
    return SwapReconciliation(
        account_id=rec.account_id, client_order_id=rec.client_order_id, arm_id=rec.arm_id, symbol=rec.symbol,
        position_id=rec.position_id, side=rec.side, lots=rec.lots, entry_utc=rec.sent_utc, exit_utc=rec.exit_utc,
        broker_swap=rec.swap, rate_per_lot=rate, fx_acct_per_usd=fx, nights=n, projected=projected,
        implied_nights=implied, deviation=deviation, matches=matches, tolerance=tolerance, ok=ok, reason=reason,
        recorded_utc=now)


def run(state_dir: str | Path, accounts: Iterable[Account], *, tolerance: float,
        now: pd.Timestamp | None = None) -> dict[str, Any]:
    """The job: every closed, not yet reconciled weekend hold of a seven-night book on `accounts` is reconciled and
    stored once. Returns the count stored and the ones outside tolerance ("<account>:<client order id>")."""
    from goldbot.engine.exploration import LEDGER
    from goldbot.engine.instruments_db import seven_night
    from goldbot.instruments import registry
    state_dir = Path(state_dir)
    now = now if now is not None else pd.Timestamp.now(tz="UTC")
    crypto = {k for k, s in registry().instruments.items() if seven_night(s)}
    stored: list[SwapReconciliation] = []
    db = _db(state_dir)
    for acc in accounts:
        with db.read() as conn:
            done = {str(c) for (c,) in conn.execute(
                "SELECT client_order_id FROM swap_reconciliations WHERE account_id = ?", (acc.account_id,))}
            closed = LEDGER.select(conn, "account_id = ? AND closed = 1", (acc.account_id,))
        due = [r for r in closed if r.symbol in crypto and r.client_order_id not in done and r.exit_utc is not None
               and r.swap is not None and weekend_rollovers(r.sent_utc, r.exit_utc, acc.server_tz) > 0]
        terms: dict[str, tuple[Any, int | None, str]] = {}
        for r in due:
            if r.symbol not in terms:
                terms[r.symbol] = _rate(state_dir, acc, r.symbol)
            stored.append(reconcile_one(r, acc, *terms[r.symbol], tolerance=tolerance, now=now))
    if stored:
        with db.write() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO swap_reconciliations (account_id, client_order_id, exit_ns, ok, body) "
                "VALUES (?, ?, ?, ?, ?)",
                [(s.account_id, s.client_order_id, int(epoch_ns(pd.DatetimeIndex([s.exit_utc]))[0]), int(s.ok),
                  s.model_dump_json()) for s in stored])
    outside = [f"{s.account_id}:{s.client_order_id}" for s in stored if not s.ok]
    for s in stored:
        (log.info if s.ok else log.warning)("swap reconcile %s %s: %s", s.account_id, s.client_order_id, s.reason)
    return {"reconciled": len(stored), "outside": outside}


def results(state_dir: str | Path, since: pd.Timestamp | None = None) -> list[SwapReconciliation]:
    """The stored reconciliations (holds closed at or after `since`), oldest exit first; raises when unreadable."""
    lo = int(epoch_ns(pd.DatetimeIndex([since]))[0]) if since is not None else 0
    with _db(state_dir).read() as conn:
        rows = conn.execute("SELECT body FROM swap_reconciliations WHERE exit_ns >= ? ORDER BY exit_ns",
                            (lo,)).fetchall()
    return [load_record_json(SwapReconciliation, b, source="swap_reconciliations") for (b,) in rows]


@register_check("global", block=BLOCK_TAIL, pos=165)
def check_swap_reconcile(ctx: HealthContext) -> Check:
    """swap_reconcile: WARN while a weekend hold of a seven-night book closed in the last RECENT_DAYS has a broker swap
    outside `costs.swap_reconcile_tolerance` of the seven-night projection, or could not be priced (BACKLOG 175: the
    schedule of BACKLOG 153 is then kept or corrected by the finance-auditor). OK otherwise, also before any weekend
    hold. Never FAIL: a cost projection, not a position."""
    from goldbot.ops.checks._base import Check
    since = pd.Timestamp(ctx.now) - pd.Timedelta(days=RECENT_DAYS)
    try:
        rows = results(ctx.state_dir, since)
    except (sqlite3.Error, OSError, ValueError) as exc:
        return Check(name=CHECK, status="warn", reason=f"swap reconciliations unreadable ({type(exc).__name__})")
    if not rows:
        return Check(name=CHECK, status="ok", reason=f"no weekend hold of a seven-night book closed in the last "
                                                     f"{RECENT_DAYS} days (job swap_reconcile)")
    bad = [r for r in rows if not r.ok]
    if bad:
        return Check(name=CHECK, status="warn", reason=f"{len(bad)} of {len(rows)} weekend holds off the seven-night "
                     "swap projection (BACKLOG 153 schedule to keep or correct): " + "; ".join(
                         f"{r.account_id} {r.symbol} {r.client_order_id}: {r.reason}" for r in bad[-3:]))
    last = rows[-1]
    return Check(name=CHECK, status="ok", reason=f"{len(rows)} weekend holds within tolerance; last "
                                                 f"{last.client_order_id}: {last.reason}")
