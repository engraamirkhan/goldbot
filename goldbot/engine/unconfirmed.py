"""Orders the broker neither filled nor refused (BACKLOG 72; docs/proposals/2026-10-backlog72-not-confirmed-plan.md
with its trading-safety conditions 1-7; owner copy docs/ux/order-not-confirmed.md). Kept out of engine/runner.py (CTO
ruling 12, ADR 0023): the runner calls in from `_execute`, `_on_decision`, `_load_orders`, `_save_orders`,
`_resolve_sent`, `_reconcile`, `no_quote_pass` and `_adopt`.

Design rule (plan, "Design"): an order result that is neither a fill nor a definite reject (`DEFINITE_REJECT_RETCODES`
or the bridge's refusal), including `ok` with no position, leaves its pending_orders row `ambiguous`. It stays in
`sent_ids`, so it is never re-sent, and it is looked up at the broker on every pass, with or without ticks: from
`_reconcile` before its adoption loop (condition 7) and from `no_quote_pass`, every `reconcile_every_s`. Reads only:
"RiskGate stays the only path to an order; the loop only reads; no re-send exists". The outcome:

- a position for its comment (or for the position id its entry deal names) is adopted with the row's stop, lots and
  exit policy, and the owner is told it is open. A stop missing at the broker, or looser than the one sent, is put back
  in the same pass (`modify` needs no quote; condition 5); one that cannot be set tells the owner to set it in MT5;
- an entry deal for the comment and an exit deal for its position id ("exit deals do not carry our comment", condition
  3), with no position: recorded as a closed trade (`_note_closed`, so the stop rule, the phase gates and the ledger
  see it) at the dealt lots (condition 4), and the owner told it opened and closed. An entry deal alone is a close in
  flight, and deals without position ids never resolve the row: it keeps looking;
- no position and no deal on two clean looks in this process at least `AMBIGUOUS_SETTLE_S` apart, counted from the
  first ("one empty deal read is not proof of no fill", condition 2): `unfilled`, and the owner told no trade opened. A
  clean look is one where the positions read and the deal-history read both succeeded while the feed is live (a valid
  quote no older than stale_tick_seconds): a terminal that lost the trade server still answers both from its cache,
  and the bridge publishes no terminal-connected flag without a protocol bump. No look in `no_quote_pass` is clean,
  and a `sending` row with no position on its look is handed over as `ambiguous`. Row age alone never declares
  `unfilled` (a restart). Market orders on a hedging account leave no resting order, which is why `orders_get` is not
  read;
- anything else (deal history unreadable, a close in flight) keeps looking; after `AMBIGUOUS_ALERT_S` one "still not
  confirmed" notice and a health WARN (engine state `unconfirmed_orders`); after `KEEP` (the 7-day prune) the row is
  terminal `unknown`, journalled, the owner told the engine stopped looking, then pruned.

While any row is `ambiguous`, entries are refused `unconfirmed_order_pending` (condition 1, the refusal path:
`AccountState.unconfirmed_order`, set on every save of the table). Notices: at most one per outcome, plus one
correction when a fill appears after "no trade" (`adopted`); `notified` is persisted before sending (a crash between
the two loses one notice, journalled). The card and the dashboard show the outcome whether or not a notice went out.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Literal

import pandas as pd

from goldbot.engine.store import OpenTrade, SentOrder
from goldbot.execution.broker import OrderResult, Position
from goldbot.execution.not_confirmed import (
    AMBIGUOUS_ALERT_S,
    AMBIGUOUS_SETTLE_S,
    NOT_CONFIRMED,
    Confirmation,
    NcOutcome,
    NoticeKind,
    notice_text,
)
from goldbot.execution.not_placed import refusal_code
from goldbot.telegram.entry_text import account_name

if TYPE_CHECKING:
    from goldbot.engine.runner import Engine
    from goldbot.telegram.approvals import Proposal

log = logging.getLogger("goldbot.engine")

AMBIGUOUS = "ambiguous"
# AMBIGUOUS_SETTLE_S (120 s, condition 2: two clean looks at least this far apart; a late 10012 can still fill) and
# AMBIGUOUS_ALERT_S (600 s: one "still not confirmed" notice and the health WARN) live beside the copy that states them
KEEP = pd.Timedelta(days=7)    # the pending_orders prune: an unresolved row turns terminal `unknown` at this age
LOOK_BACK = pd.Timedelta(days=1)   # deal history read from the row's send time less this (broker vs host clock)
NEVER_CONFIRMED = frozenset({AMBIGUOUS, "unfilled", "unknown"})
# rows whose order may exist at the broker: entries are refused while any is in the table (condition 1). A `sending`
# row counts: the engine is single-threaded, so one seen at a gate check is never that check's own send but a send
# that raised (trading-safety review, round 1)
UNRESOLVED = frozenset({"sending", AMBIGUOUS})
Look = Literal["opened", "opened_closed", "no_trade", "wait", "gave_up"]


def _now() -> pd.Timestamp:
    """The host clock, as the row was stamped (`_execute`)."""
    return pd.Timestamp.now("UTC")


class Looks:
    """What one engine process has seen: the first clean look per row (condition 2 counts looks in this process, so a
    restart starts again) and when the last look-up ran (`no_quote_pass` paces itself by it)."""

    def __init__(self) -> None:
        self.first_clean: dict[str, pd.Timestamp] = {}
        self.last: pd.Timestamp | None = None


# ---------------------------------------------------------------------------------------------- pure
def sent_status(res: OrderResult, *, paper: bool, definite: frozenset[int]) -> str:
    """The pending_orders status for an order result: `filled`, `rejected` (exactly the results item 71 words as not
    placed: a definite reject or the bridge's refusal) or `ambiguous` (everything else, `ok` without a position too)."""
    if res.ok and res.position_id is not None:
        return "filled"
    return "rejected" if refusal_code(res, paper=paper, definite=definite) is not None else AMBIGUOUS


def decide(*, age_s: float, position: bool, deals_read: bool, entry_dealt: bool, exit_dealt: bool,
           clean_for_s: float | None) -> Look:
    """One look's outcome (see the module docstring). `clean_for_s`: on a clean look, the seconds since this process's
    first clean look for the row; None when this look was not clean."""
    if position:
        return "opened"
    if age_s >= KEEP.total_seconds():
        return "gave_up"
    if not deals_read:
        return "wait"
    if entry_dealt:
        return "opened_closed" if exit_dealt else "wait"
    return "no_trade" if clean_for_s is not None and clean_for_s >= AMBIGUOUS_SETTLE_S else "wait"


def pending(orders: dict[str, SentOrder]) -> bool:
    return any(o.status in UNRESOLVED for o in orders.values())


def tighter(side: int, a: float | None, b: float | None) -> float | None:
    """The tighter of two stops for a position on `side` (the higher for a long); None only when both are."""
    if a is None or b is None:
        return a if b is None else b
    return max(a, b) if side > 0 else min(a, b)


def summary(orders: dict[str, SentOrder], now: pd.Timestamp, symbol: str) -> dict[str, Any]:
    """Engine state `unconfirmed_orders`: how many rows are unresolved and the oldest's age (health WARNs from
    AMBIGUOUS_ALERT_S) and send time (the entry status's "since")."""
    rows = [pd.Timestamp(o.ts_utc) for o in orders.values() if o.status in UNRESOLVED]
    if not rows:
        return {"count": 0, "oldest_age_s": None, "oldest_utc": None, "symbol": symbol}
    oldest = min(rows)
    return {"count": len(rows), "oldest_age_s": round((now - oldest).total_seconds(), 1),
            "oldest_utc": oldest.isoformat(), "symbol": symbol}


def _entries(deals: pd.DataFrame | None, cid: str) -> pd.DataFrame:
    """The entry deals of the order `cid`: by client id (paper) or MT5 comment, entry side only (MT5 DEAL_ENTRY_IN;
    the paper broker's "entry" rows). Exit deals are found by position id, never by comment (condition 3)."""
    if deals is None or deals.empty:
        return pd.DataFrame()
    hit = pd.Series(False, index=deals.index)
    if "client_order_id" in deals.columns:
        hit |= deals["client_order_id"] == cid
    if "comment" in deals.columns:
        hit |= deals["comment"].isin([cid, cid[-31:]])
    rows = deals[hit]
    if "entry" in rows.columns:
        return rows[rows["entry"] == 0]
    return rows[rows["type"] == "entry"] if "type" in rows.columns else rows.iloc[0:0]


def _dealt(entries: pd.DataFrame) -> float | None:
    col = "volume" if "volume" in entries.columns else "lots"
    return float(entries[col].sum()) if not entries.empty and col in entries.columns else None


def _closed_trade(rec: SentOrder, cid: str, pid: int, entries: pd.DataFrame) -> OpenTrade:
    """The trade a closed fill is recorded as: the row's agent, stop and target, the dealt lots (condition 4) and the
    entry deal's price and time."""
    lots = _dealt(entries) or rec.lots
    first = entries.sort_values("ts_utc").iloc[0] if "ts_utc" in entries.columns else None
    at = pd.Timestamp(first["ts_utc"]) if first is not None else pd.Timestamp(rec.ts_utc)
    price = float(first["price"]) if first is not None and "price" in entries.columns else None
    return OpenTrade(position_id=pid, agent_id=rec.agent_id, side=rec.side, lots=lots, entry_bar_ts=at,
                     max_bars=rec.max_bars, sl=rec.sl, tp=rec.tp, client_order_id=cid, open_price=price,
                     initial_sl=rec.sl, initial_lots=lots, opened_utc=at, audit=rec.audit)


# ---------------------------------------------------------------------------------------------- shared with the runner
def adopt_sent(eng: Engine, cid: str, rec: SentOrder, pos: Position) -> OpenTrade:
    """A position the engine sent but never saw filled (a `sending` row on restart, an `ambiguous` one) adopted with
    the row's agent, entry stop and exit policy and the dealt lots (the position's: condition 4), unless the open
    table already holds it (adopted by reconciliation, or carried over a restart): that trade is kept with its stop,
    bars held and scale-out state and only the row is marked filled (review MEDIUM-2 b). BACKLOG 84: a `sending` row
    whose card was shown NOT CONFIRMED (`shown_unconfirmed`) tells the owner it is open and updates the card ("a later
    fill found by the look-up still sends the "opened" notice and updates the card")."""
    was = rec.status
    tr = _adopt_row(eng, cid, rec, pos)
    if was == "sending" and _card_unconfirmed(eng, cid):
        _journal(eng, rec, "opened", position=pos.position_id)
        _tell(eng, rec, "opened", notified="opened", dealt=pos.lots, entry=pos.open_price)
        _card(eng, rec, "opened")
    return tr


def _adopt_row(eng: Engine, cid: str, rec: SentOrder, pos: Position) -> OpenTrade:
    rec.status, rec.position_id = "filled", pos.position_id
    if pos.position_id in eng.open:
        eng.decisions.append({"ts": time.time(), "agent": rec.agent_id, "action": "reconcile_sent_known",
                              "proposal": cid, "position": pos.position_id})
        return eng.open[pos.position_id]
    tr = eng.open[pos.position_id] = OpenTrade(position_id=pos.position_id, agent_id=rec.agent_id, side=pos.side,
                                               lots=pos.lots, entry_bar_ts=pos.open_time_utc, max_bars=rec.max_bars,
                                               sl=rec.sl, tp=rec.tp, client_order_id=cid, open_price=pos.open_price,
                                               initial_sl=rec.sl, initial_lots=pos.lots,
                                               opened_utc=pos.open_time_utc, excursion_approx=True, audit=rec.audit)
    agent = eng.agents.get(rec.agent_id)
    if agent is not None:
        atr_usd = rec.atr_usd if rec.atr_usd is not None else eng._implied_atr(agent, pos.open_price, rec.sl)
        eng._attach_policy(tr, agent, entry=pos.open_price, atr_usd=atr_usd, opened=pos.open_time_utc)
    eng.decisions.append({"ts": time.time(), "agent": rec.agent_id, "action": "reconcile_adopt_sent",
                          "proposal": cid, "position": pos.position_id})
    return tr


def archive_pending(eng: Engine, p: Proposal) -> None:
    """`_on_decision`: an approval whose order came back ambiguous is archived NOT CONFIRMED (refusal code
    `not_confirmed`, the facts pending), never ✅ APPROVED; no notice yet, the look-up tells the owner what it finds."""
    p.gate_refusal = [NOT_CONFIRMED]
    p.not_confirmed = Confirmation(symbol=eng.cfg.symbol)
    if eng.center.bus is not None:
        eng.center.bus.archive(p)


def adopted(eng: Engine, rec: SentOrder, p: Position) -> bool:
    """`_adopt` met the position of a row it has not seen filled. The row is marked filled; True when the engine never
    saw it confirmed (`NEVER_CONFIRMED`: the trade then keeps the stop and target sent, which the reconcile loop puts
    back). An `ambiguous` row, or a `sending` one whose card was shown NOT CONFIRMED (BACKLOG 84), tells the owner it
    is open (when the look-up did not get there first); an `unfilled` row already reported "no trade" sends the one
    correction (condition 6)."""
    was = rec.status
    rec.status, rec.position_id = "filled", p.position_id
    kind: Literal["opened", "opened_late"] | None = None
    if was == AMBIGUOUS or (was == "sending" and _card_unconfirmed(eng, rec.client_order_id)):
        kind = "opened"
    elif was == "unfilled" and rec.notified == "no_trade":
        kind = "opened_late"
    if kind is not None:
        _journal(eng, rec, kind, position=p.position_id)
        _tell(eng, rec, kind, notified=kind, dealt=p.lots, entry=p.open_price)
        _card(eng, rec, kind)
    return was in NEVER_CONFIRMED


# ---------------------------------------------------------------------------------------------- the look-up
def resolve(eng: Engine, positions: dict[int, Position], *, quoted: bool = True) -> bool:
    """Look every `ambiguous` row up against `positions` (a read that succeeded) and the deal history. Returns whether
    any row changed (the caller saves the table). `quoted` False (`no_quote_pass`): no look is clean. A look that
    raises (a malformed deal row) is logged and waits, so it never stops the other rows or the caller's reconcile
    loop (code review LOW 1)."""
    now = _now()
    eng._looks.last = now
    fresh = quoted and _fresh_quote(eng)
    changed = False
    for cid, rec in list(eng._orders.items()):
        if rec.status != AMBIGUOUS:
            continue
        try:
            changed = _look(eng, cid, rec, positions, now, fresh=fresh) or changed
        except Exception:
            log.exception("look-up of unconfirmed order %s failed; looked up again next pass", cid)
    return changed


def _fresh_quote(eng: Engine) -> bool:
    """Trading-safety review (condition 2): a terminal that lost the trade server still answers positions and deals
    from its cache, so a look is clean only while the feed is live: a valid quote no older than the gate's
    stale_tick_seconds (the engine's clock, as `_refresh_account` measures it)."""
    t = eng._last_tick
    if t is None or eng._quote_invalid_since is not None:
        return False
    return abs((eng._now(t) - t.ts_utc).total_seconds()) <= eng.gate.limits.stale_tick_seconds


def no_quote_look(eng: Engine) -> None:
    """`no_quote_pass` (market closed, feed down): the look-up needs only the bridge, every `reconcile_every_s`."""
    if not pending(eng._orders):
        return
    now = _now()
    if eng._looks.last is not None and (now - eng._looks.last).total_seconds() < eng.cfg.reconcile_every_s:
        return
    positions = eng._read_positions("the unconfirmed-order look-up is retried next pass")
    if positions is not None and resolve(eng, {p.position_id: p for p in positions}, quoted=False):
        eng._save_orders()


def _look(eng: Engine, cid: str, rec: SentOrder, positions: dict[int, Position], now: pd.Timestamp, *,
          fresh: bool) -> bool:
    age = (now - pd.Timestamp(rec.ts_utc)).total_seconds()
    pos = next((p for p in positions.values() if p.comment in (cid, cid[-31:])), None)
    try:
        deals: pd.DataFrame | None = eng._read_deals(pd.Timestamp(rec.ts_utc) - LOOK_BACK)
    except Exception:                   # counted by _read_deals; never "no deal"
        deals = None
    entries = _entries(deals, cid)
    pid = int(entries["position_id"].iloc[0]) if not entries.empty and "position_id" in entries.columns else None
    if pos is None and pid is not None:
        pos = positions.get(pid)
    closed: tuple[OpenTrade, dict] | None = None
    if pos is None and pid is not None:
        tr = _closed_trade(rec, cid, pid, entries)
        try:
            info, _ = eng._exit_deals(pid, tr)  # by position id: the exit deal carries the broker's comment
        except Exception:
            info = None
        closed = (tr, info) if info is not None else None
    clean = fresh and deals is not None and entries.empty and pos is None
    if clean:
        eng._looks.first_clean.setdefault(cid, now)
    first = eng._looks.first_clean.get(cid)
    out = decide(age_s=age, position=pos is not None, deals_read=deals is not None, entry_dealt=not entries.empty,
                 exit_dealt=closed is not None,
                 clean_for_s=(now - first).total_seconds() if clean and first is not None else None)
    if out == "opened" and pos is not None:
        _opened(eng, cid, rec, pos, _dealt(entries) or pos.lots)
    elif out == "opened_closed" and closed is not None and pid is not None:
        tr, info = closed
        rec.status, rec.position_id = "filled", pid
        eng._note_closed(pid, tr, "broker_close")
        _journal(eng, rec, out, position=pid)
        facts = dict(entry=tr.open_price, exit=info["exit_price"], pnl=info["pnl"],
                     currency=eng._account_currency() or None)
        _tell(eng, rec, out, notified=out, dealt=tr.initial_lots, **facts)
        _card(eng, rec, out, **facts)
    elif out == "no_trade":
        rec.status = "unfilled"
        eng._looks.first_clean.pop(cid, None)
        _journal(eng, rec, out)
        _tell(eng, rec, out, notified=out)
        _card(eng, rec, out)
    elif out == "gave_up":
        rec.status = "unknown"
        eng._looks.first_clean.pop(cid, None)
        _journal(eng, rec, out, sent=pd.Timestamp(rec.ts_utc).isoformat())
        _tell(eng, rec, out, notified=out)
    elif age >= AMBIGUOUS_ALERT_S and not rec.unknown_notified:
        rec.unknown_notified = True
        _journal(eng, rec, "still_unknown", sent=pd.Timestamp(rec.ts_utc).isoformat())
        _tell(eng, rec, "still_unknown", notified=None)
        _card(eng, rec, "still_unknown")
    else:
        return False
    return True


def _opened(eng: Engine, cid: str, rec: SentOrder, pos: Position, dealt: float) -> None:
    """Adopt a found position and put back a stop missing or looser at the broker, or a lost target, in this pass
    (condition 5; a target that is there is kept, a stop is never loosened). `pos` is updated in place on success, so
    `_reconcile`'s loop over the same read does not send it again."""
    tr = adopt_sent(eng, cid, rec, pos)
    tr.sl = tighter(pos.side, tr.sl if tr.sl is not None else rec.sl, pos.sl)   # never looser than the broker's (TS 6)
    loose = pos.sl is None or pos.side * (rec.sl - pos.sl) > 1e-9
    lost_tp = pos.tp is None
    kind: NoticeKind = "opened"
    if loose or lost_tp:
        sl, tp = (rec.sl if loose else pos.sl), (rec.tp if lost_tp else pos.tp)
        try:
            res = eng.broker.modify(pos.position_id, sl, tp)
            ok, retcode = res.ok, res.retcode
        except Exception as exc:        # noqa: BLE001 - the reconcile loop retries it; the owner is told now
            ok, retcode = False, None
            log.warning("stop for adopted position %s not set (%s)", pos.position_id, type(exc).__name__)
        eng.decisions.append({"ts": time.time(), "action": "reinstate_stops", "position": pos.position_id, "sl": sl,
                              "tp": tp, "ok": ok, "retcode": retcode})
        if ok:
            pos.sl, pos.tp = sl, tp
        if loose:
            kind = "opened_stop_set" if ok else "opened_no_stop_through" if _through(eng, pos.side, rec.sl) \
                else "opened_no_stop"
    _journal(eng, rec, "opened", position=pos.position_id, lots=dealt)
    _tell(eng, rec, kind, notified="opened", dealt=dealt, entry=pos.open_price)
    _card(eng, rec, "opened")


def _through(eng: Engine, side: int, sl: float) -> bool:
    """The exit-side price is already at or past `sl` (the per-tick stop check closes the trade at the first quote)."""
    try:
        t = eng.broker.last_tick(eng.cfg.symbol)
    except Exception:                   # noqa: BLE001 - no quote: the plain "set it in MT5" notice
        return False
    return side * ((t.bid if side > 0 else t.ask) - sl) <= 0


def _journal(eng: Engine, rec: SentOrder, what: str, **extra: Any) -> None:
    eng.decisions.append({"ts": time.time(), "agent": rec.agent_id, "action": f"unconfirmed_{what}",
                          "proposal": rec.client_order_id, **extra})


def _digits(eng: Engine) -> int:
    try:
        return int(eng._symbol_info().digits)
    except Exception:                   # noqa: BLE001 - wording only: XAUUSD prices have 2 digits
        return 2


def _tell(eng: Engine, rec: SentOrder, kind: NoticeKind, *, notified: str | None, dealt: float | None = None,
          entry: float | None = None, exit: float | None = None, pnl: float | None = None,
          currency: str | None = None) -> None:
    """One owner notice, `notified` persisted first (at most once). Best effort: a notice that cannot be queued is
    logged; the outcome stands either way."""
    if notified is not None:
        rec.notified = notified
    eng._save_orders()
    if eng.center.bus is None:
        return
    try:
        eng.center.bus.notify_owner(notice_text(
            kind, side=rec.side, lots=rec.lots, dealt=dealt, symbol=eng.cfg.symbol,
            account=account_name(eng.cfg.account_id), entry=entry, exit=exit, pnl=pnl, currency=currency, sl=rec.sl,
            tp=rec.tp, digits=_digits(eng)))
    except Exception as exc:            # noqa: BLE001
        log.warning("unconfirmed-order notice not queued (%s)", type(exc).__name__)


def shown_unconfirmed(p: Proposal | None) -> bool:
    """The approved card was shown NOT CONFIRMED, or may have been: archived not confirmed (`archive_pending`, or
    `_finish` after a send that raised) or left `placing` by an engine that died mid-send, which the outbox's
    silent-engine fallback reports NOT CONFIRMED (BACKLOG 84). Never a card the engine answered otherwise."""
    if p is None or p.outcome is None or p.outcome.value != "APPROVED":
        return False
    return p.not_confirmed is not None or p.placing or p.gate_refusal == [NOT_CONFIRMED]


def _card_unconfirmed(eng: Engine, cid: str) -> bool:
    bus = eng.center.bus
    if bus is None:
        return False
    try:
        return shown_unconfirmed(bus.get(cid))
    except Exception as exc:            # noqa: BLE001 - an unreadable card: no notice, as before BACKLOG 84
        log.warning("card for an unconfirmed order not read (%s)", type(exc).__name__)
        return False


def _card(eng: Engine, rec: SentOrder, outcome: NcOutcome, **facts: Any) -> None:
    """Rewrite the archived card's facts (an approved card shown NOT CONFIRMED only: the auto path has none). A card
    the silent-engine fallback reported while it was still `placing` gets the facts and the refusal code here
    (BACKLOG 84), so it never reads APPROVED or "submitted". Best effort, logged."""
    bus = eng.center.bus
    if bus is None:
        return
    try:
        p = bus.get(rec.client_order_id)
        if p is None or not shown_unconfirmed(p):
            return
        was = p.not_confirmed or Confirmation(symbol=eng.cfg.symbol)
        c = was.model_copy(update={"outcome": outcome, "digits": _digits(eng), **facts})
        bus.archive(p.model_copy(update={"not_confirmed": c, "gate_refusal": [NOT_CONFIRMED], "placing": False}))
    except Exception as exc:            # noqa: BLE001
        log.warning("card for an unconfirmed order not updated (%s)", type(exc).__name__)
