"""Order not confirmed: the owner's words for an approved or automatic entry the broker neither filled nor definitely
refused (BACKLOG 72, docs/ux/order-not-confirmed.md). A sibling of `not_placed.py`, which imports it.

"The words live in one Python table, used by `bot.outcome_text`, the runner's notices and the API. The browser holds
no wording policy (ADR 0011)." The engine archives such an approval with the refusal code `not_confirmed` and the facts
of what it found (`Confirmation`, on the archived proposal); every surface words them here. "Never shown: the proposal
id, the word "retcode", a code number, raw broker or bridge text, a host, a login." The copy promises only what a test
proves: the engine looks it up at the broker on every pass, with or without ticks, and tells the owner what it finds,
or tells them after 10 minutes that it still doesn't know; "no trade" only after two clean looks at least 2 minutes
apart; new entries wait until it's settled (trading-safety condition 1, the refusal path).
"""
from __future__ import annotations

from typing import Literal

from goldbot.base import FrozenRecord

NOT_CONFIRMED = "not_confirmed"                       # the refusal code on the archived proposal (not a refusal)
PENDING_REFUSAL_CODE = "unconfirmed_order_pending"    # the RiskGate's refusal while an order is unresolved
PENDING_REFUSAL = "an earlier order isn't confirmed by the broker yet"
GATE = " New entries wait until it's settled."        # the refusal path of condition 1 is the one built
# the numbers the copy states ("checked twice, at least 2 minutes apart", "after 10 minutes"): the engine's look-up
# (goldbot/engine/unconfirmed.py) and the health WARN use these, so the words cannot drift from the behaviour
AMBIGUOUS_SETTLE_S = 120.0
AMBIGUOUS_ALERT_S = 600.0
NcOutcome = Literal["pending", "opened", "opened_closed", "no_trade", "opened_late", "still_unknown"]
NoticeKind = Literal["opened", "opened_stop_set", "opened_no_stop", "opened_no_stop_through", "opened_closed",
                     "no_trade", "opened_late", "still_unknown", "gave_up"]
Tone = Literal["warn", "ok", "neutral", "bad"]


class Confirmation(FrozenRecord):
    """What the engine found for an unconfirmed approval, kept on the archived proposal (`Proposal.not_confirmed`) so
    the card and the dashboard word it. `lots` are the dealt lots when the broker shows them; prices and P&L only for
    an order that opened and closed."""
    outcome: NcOutcome = "pending"
    symbol: str = "XAUUSD"
    entry: float | None = None
    exit: float | None = None
    pnl: float | None = None                  # account currency, from the broker's deals
    currency: str | None = None
    digits: int = 2


class NotConfirmed(FrozenRecord):
    """The dashboard note (API `DecidedProposal.not_confirmed`): a heading word that carries the state, then a
    sentence; `tone` picks the colour (amber while unknown: it is not a halt; red only for "Not placed")."""
    outcome: NcOutcome
    heading: str
    text: str
    tone: Tone


def _px(x: float | None, digits: int) -> str:
    return "?" if x is None else f"{x:.{digits}f}"


def _pnl(x: float | None, currency: str | None) -> str:
    """"+£12.30" / "−£8.20" (U+2212), in the account currency as the approval card writes money."""
    # lazily: approvals imports this module
    from goldbot.telegram.approvals import cash
    if x is None:
        return "?"
    return ("−" if x < 0 else "+") + cash(abs(x), currency)


def _known(c: Confirmation | None) -> Confirmation:
    """The fallback (a future enum value, or none): the pending texts, "because "not confirmed" needs no code to be
    true"."""
    if c is None or c.outcome not in _CARD:
        return Confirmation(symbol=c.symbol if c is not None else "XAUUSD")
    return c


_CARD: dict[str, str] = {
    "pending": ("⚠️ NOT CONFIRMED: approved and sent, but the broker didn't confirm it. The engine looks it up at the "
                "broker and tells you what it finds, or tells you after 10 minutes that it still doesn't know.{gate}"),
    "opened": "✅ OPENED: the broker confirmed it late. Stop, target and exit are automatic.",
    "opened_closed": ("ℹ️ OPENED AND CLOSED: the broker confirmed it late; it opened at {entry} and closed at {exit} "
                      "({pnl})."),
    "no_trade": ("🛑 NOT PLACED: approved and sent, but the broker has no trade for it (checked twice, at least 2 "
                 "minutes apart). No trade was opened."),
    "opened_late": ("✅ OPENED AFTER ALL: the broker confirmed it after it was reported not placed. Stop, target and "
                    "exit are automatic."),
    "still_unknown": "⚠️ STILL NOT CONFIRMED after 10 minutes. The engine keeps looking; check MT5 for an open {symbol} trade.",
}
_NOTE: dict[str, tuple[str, str, Tone]] = {
    "pending": ("Not confirmed.", "Sent, but the broker didn't confirm it. The engine looks it up at the broker and "
                "tells you what it finds, or tells you after 10 minutes that it still doesn't know.{gate}", "warn"),
    "opened": ("Opened.", "The broker confirmed it late; stop, target and exit are automatic.", "ok"),
    "opened_closed": ("Opened and already closed.", "The broker confirmed it late: it opened at {entry} and closed at "
                      "{exit} ({pnl}). It's in the trade record.", "neutral"),
    "no_trade": ("Not placed.", "Sent, but the broker has no trade for it (checked twice, at least 2 minutes apart); no "
                 "trade was opened.", "bad"),
    "opened_late": ("Opened after all.", "The broker confirmed it after it was reported not placed; stop, target and "
                    "exit are automatic.", "ok"),
    "still_unknown": ("Still not confirmed.", "After 10 minutes the broker hasn't shown whether it opened. The engine "
                      "keeps looking; check MT5 for an open {symbol} trade.{gate}", "warn"),
}
_NOTICE: dict[str, str] = {
    "opened": ("Order confirmed: {trade} on {account} is open at {entry} (the broker confirmed it late). Stop {sl}, "
               "target {tp}; exits are automatic."),
    "opened_stop_set": ("Order confirmed: {trade} on {account} is open at {entry} (the broker confirmed it late). Its "
                        "stop was missing or wider at the broker, so the engine set stop {sl} and target {tp}; exits "
                        "are automatic."),
    "opened_no_stop": ("⚠️ Order confirmed, but without its stop: {trade} on {account} is open at {entry} and the "
                       "engine couldn't set stop {sl}. Set stop {sl} in MT5 now."),
    # trading-safety review (TS 5): the price is already past the stop, so the per-tick stop check closes the trade
    "opened_no_stop_through": ("⚠️ Order confirmed, but without its stop: {trade} on {account} is open at {entry} and "
                               "the engine couldn't set stop {sl}. The price is already past stop {sl}, so the engine "
                               "closes the trade at the first quote."),
    "opened_closed": ("Order confirmed, already closed: {trade} on {account} opened at {entry} and closed at {exit} "
                      "({pnl}). It's in the trade record."),
    "no_trade": ("Order not placed after all: the broker has no trade for {trade} on {account} (checked twice, at "
                 "least 2 minutes apart). Nothing to do; if it turns up later, you'll be told."),
    "opened_late": ("Correction: the order did open after all. {trade} on {account} is open at {entry}; the earlier "
                    "\"not placed\" notice was wrong. Stop {sl}, target {tp}; exits are automatic."),
    "still_unknown": ("Order still not confirmed after 10 minutes: the broker hasn't shown whether {trade} on {account} "
                      "opened. The engine keeps looking and tells you when it knows.{gate} Check MT5: if a {symbol} "
                      "trade is open without a stop, set stop {sl}."),
    "gave_up": ("Stopped looking: after 7 days the broker still hasn't shown whether {trade} on {account} opened. Check "
                "MT5 and ask Claude to reconcile the trade record."),
}


def _fields(c: Confirmation) -> dict[str, str]:
    return {"entry": _px(c.entry, c.digits), "exit": _px(c.exit, c.digits), "pnl": _pnl(c.pnl, c.currency),
            "symbol": c.symbol, "gate": GATE}


def card_text(c: Confirmation | None) -> str:
    """The Telegram card's outcome line (`bot.outcome_text`); None or an unknown outcome reads as pending."""
    c = _known(c)
    return _CARD[c.outcome].format(**_fields(c))


def note(c: Confirmation | None) -> NotConfirmed:
    """The dashboard note for the API (`DecidedProposal.not_confirmed`)."""
    c = _known(c)
    heading, text, tone = _NOTE[c.outcome]
    return NotConfirmed(outcome=c.outcome, heading=heading, text=text.format(**_fields(c)), tone=tone)


def notice_text(kind: NoticeKind, *, side: int, lots: float, dealt: float | None, symbol: str, account: str,
                entry: float | None = None, exit: float | None = None, pnl: float | None = None,
                currency: str | None = None, sl: float | None = None, tp: float | None = None,
                digits: int = 2) -> str:
    """The owner notice (a separate Telegram message, `bus.notify_owner`). `account` is already masked
    (`entry_text.account_name`). A part-fill names the dealt lots of the lots sent: "LONG 0.2 of 0.37 lots XAUUSD"."""
    size = f"{dealt:g} of {lots:g}" if dealt is not None and abs(dealt - lots) > 1e-9 else f"{lots:g}"
    trade = f"{'LONG' if side > 0 else 'SHORT'} {size} lots {symbol}"
    return _NOTICE[kind].format(trade=trade, account=account, entry=_px(entry, digits), exit=_px(exit, digits),
                                pnl=_pnl(pnl, currency), sl=_px(sl, digits), tp=_px(tp, digits), symbol=symbol,
                                gate=GATE)


def health_line(n: int, age: str, symbol: str) -> str:
    """The engine health WARN (`oldest_age_s >= 600`); `age` is already worded (`checks._base._age`)."""
    return (f"{n} order{'' if n == 1 else 's'} not confirmed by the broker, oldest {age}: the engine keeps looking; new "
            f"entries wait until it's settled; check MT5 for an open {symbol} trade and its stop (Runbook: Order not "
            f"confirmed)")
