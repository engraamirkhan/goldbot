"""Order not placed: the owner's words for an approved entry the MT5 box or the broker refused (docs/ux/order-not-placed.md).

"The card on Telegram, the dashboard note and the owner notice say the same reason in the same words. To keep them
from drifting, the words live in one Python table", used by `bot.outcome_text`, the engine's owner notice and the API
(`DecidedProposal.not_placed`; the browser holds no wording policy, ADR 0011).

The engine stores a refusal code in `Proposal.gate_refusal` (never shown to the owner): `bridge_notional_ceiling` /
`bridge_notional_ceiling_unreadable` (the MT5 box's notional ceiling, BACKLOG 66), `bridge_stale_order` (the MT5 box
dropped an order that aged past ORDER_MAX_AGE_S) and `broker_refused:<retcode>` for any other definite reject.
"Bridge prefixes are tested before the retcode, because the bridge's own refusals also use 10006." 10027 depends on
the broker: on a terminal it is Algo Trading switched off, on the paper broker a duplicate send, so the paper broker's
is stored `broker_refused:10027:paper`. "Never shown: the proposal id, the bridge or broker message, a host, a login."
The retcode number appears only in the generic fallback; the journal keeps it for every cause.
"""
from __future__ import annotations

import re
from typing import Literal

from goldbot.base import FrozenRecord
from goldbot.execution.bridge import CEILING_REFUSAL, ORDER_MAX_AGE_S, STALE_REFUSAL, UNREADABLE_REFUSAL
from goldbot.execution.broker import OrderResult

By = Literal["broker", "mt5_box"]
BROKER_REFUSED = "broker_refused"
PAPER_SUFFIX = ":paper"                 # the paper broker's 10027 (a duplicate), not a terminal's (Algo Trading off)
CEILING_UNREADABLE = f"{CEILING_REFUSAL}_unreadable"
CEILING_CODES = (CEILING_REFUSAL, CEILING_UNREADABLE)
_BROKER_CODE = re.compile(rf"{BROKER_REFUSED}:(\d+)({PAPER_SUFFIX})?")
BY_PHRASE: dict[str, str] = {"broker": "the broker", "mt5_box": "the MT5 box"}


class NotPlaced(FrozenRecord):
    """Why an approved order was not placed, in the owner's words. `reason` is a lower-case clause with no final full
    stop that reads after either heading ("Refused by the broker." / "Refused by the MT5 box."); None for the
    fallback, which names the broker's `code` instead. `next_step` closes the owner notice. `nothing_sent` False only
    for the paper broker's duplicate: its first send did go out, so no surface may say "Nothing was sent"
    (trading-safety review LOW)."""
    by: By
    reason: str | None = None
    code: int | None = None             # the retcode, set for the fallback only
    next_step: str
    nothing_sent: bool = True


_ONE_OFF = "Nothing to do if it's a one-off."
_RESTRICTED = "Nothing to do: it's the broker's restriction."
_SETTINGS = ("the order's settings weren't accepted",
             "Ask Claude to check: the order settings and the broker's rules disagree.")
_LIMITS = ("the account's limit on open trades or total size was reached", "Check open trades on the Overview.")
# docs/ux/order-not-placed.md "Per cause": (reason, next_step) per broker retcode; any other definite reject (10006
# from the broker itself included: it gives no reason, so the copy invents none) reaches the fallback
_BROKER: dict[int, tuple[str, str]] = {
    10004: ("the price moved before the order could be filled", "Nothing to do: the next signal is priced afresh."),
    10020: ("the price moved before the order could be filled", "Nothing to do: the next signal is priced afresh."),
    10021: ("there was no price to fill it at",
            f"{_ONE_OFF} If it repeats, check the MT5 terminal is connected."),
    10007: ("the order was cancelled on the MT5 terminal", "Check nobody is working on the MT5 terminal by hand."),
    10013: _SETTINGS, 10015: _SETTINGS, 10022: _SETTINGS, 10030: _SETTINGS, 10035: _SETTINGS,
    10014: ("the trade size wasn't accepted", "Ask Claude to check the broker's lot size rules for this account."),
    10016: ("the stop or target wasn't accepted (too close to the price)",
            f"{_ONE_OFF} If it repeats, ask Claude to check the broker's minimum stop distance."),
    10017: ("trading in this symbol is switched off on the account",
            "Check the account in MT5: the broker may have switched trading off."),
    10018: ("the market was closed", "Nothing to do: entries resume when the market reopens."),
    10019: ("there wasn't enough free margin", "Check free margin on the Overview before approving the next trade."),
    10024: ("it was too busy (too many requests at once)", _ONE_OFF),
    10026: ("automatic trading is switched off for this account at the broker",
            "Contact the broker: automatic trading is off at their end."),
    10027: ("Algo Trading is switched off in the MT5 terminal", "Turn on Algo Trading in the MT5 terminal."),
    10033: _LIMITS, 10034: _LIMITS, 10040: _LIMITS,
    10042: ("only buys are allowed in this symbol right now", _RESTRICTED),
    10043: ("only sells are allowed in this symbol right now", _RESTRICTED),
    10044: ("only closing trades are allowed in this symbol right now", _RESTRICTED),
    10046: ("the account doesn't allow opposite trades at the same time (no hedging)",
            "Check open trades on the Overview."),
}
_PAPER_DUPLICATE = ("it was already sent; this repeat was refused", "Nothing to do: the first send stands.")
_CEILING_STEP = "If this repeats, the trade sizing and the MT5 box's limit disagree: ask Claude to check."
_MT5_BOX: dict[str, tuple[str, str]] = {
    STALE_REFUSAL: (f"it took more than {ORDER_MAX_AGE_S:g} s to check the order, so it dropped it rather than trade "
                    f"on an old price",
                    f"{_ONE_OFF} If it repeats, the MT5 box is overloaded or slow: check the Health tab."),
    # BACKLOG 66, as shipped (the dashboard's words); the card and the notice keep their own shipped forms below
    CEILING_REFUSAL: ("its safety limit stops open gold worth more than 6× equity, and this trade would have gone "
                      "over it", _CEILING_STEP),
    CEILING_UNREADABLE: ("it couldn't read the account figures it checks, so it refused to be safe", _CEILING_STEP),
}
_CEILING_CARD = {
    CEILING_REFUSAL: ("🛑 NOT PLACED: approved, but the MT5 box's safety limit refused it (open gold would be worth "
                      "more than 6× equity). Nothing was sent."),
    CEILING_UNREADABLE: ("🛑 NOT PLACED: approved, but the MT5 box couldn't read the account figures it checks, so it "
                         "refused to be safe. Nothing was sent."),
}


def refusal_code(res: OrderResult, *, paper: bool, definite: frozenset[int]) -> str | None:
    """The refusal code for an order result, or None when it is not a definite reject (a fill or an ambiguous result:
    the card stays approved). `definite` is the engine's DEFINITE_REJECT_RETCODES. The bridge's message prefixes are
    tested before the retcode: its own refusals use 10006 too, and a broker's 10006 must never read as the box's."""
    if res.ok:                          # a fill, or accepted without a position yet: never "not placed"
        return None
    if not res.ok and res.message.startswith(CEILING_REFUSAL):     # as runner.bridge_refused
        return CEILING_UNREADABLE if res.message.startswith(UNREADABLE_REFUSAL) else CEILING_REFUSAL
    if res.retcode not in definite:     # never claim "not placed" when the order may exist
        return None
    if res.message.startswith(STALE_REFUSAL):
        return STALE_REFUSAL
    return f"{BROKER_REFUSED}:{res.retcode}{PAPER_SUFFIX if paper and res.retcode == 10027 else ''}"


def broker_code(code: str) -> int | None:
    """The retcode in a `broker_refused:<retcode>` code; None for any other code."""
    m = _BROKER_CODE.fullmatch(code)
    return int(m.group(1)) if m else None


def not_placed(code: str) -> NotPlaced | None:
    """The owner's words for a refusal code; None when the code is not one of this table's (a RiskGate refusal)."""
    if code in _MT5_BOX:
        reason, step = _MT5_BOX[code]
        return NotPlaced(by="mt5_box", reason=reason, next_step=step)
    retcode = broker_code(code)
    if retcode is None:
        return None
    if code.endswith(PAPER_SUFFIX) and retcode == 10027:
        return NotPlaced(by="broker", reason=_PAPER_DUPLICATE[0], next_step=_PAPER_DUPLICATE[1], nothing_sent=False)
    row = _BROKER.get(retcode)
    if row is None:
        return NotPlaced(by="broker", code=retcode,
                         next_step=f"If it repeats, ask Claude to check (broker code {retcode}).")
    return NotPlaced(by="broker", reason=row[0], next_step=row[1])


def first_not_placed(codes: list[str] | None) -> NotPlaced | None:
    """The first code in a card's refusal list that this table words (the engine stores one)."""
    for c in codes or []:
        if (np := not_placed(c)) is not None:
            return np
    return None


def _cause(np: NotPlaced) -> str:
    """": {reason}" or " (code N)": what follows the refused trade in the card and the notice."""
    return f": {np.reason}" if np.reason is not None else f" (code {np.code})"


def card_text(code: str) -> str | None:
    """The Telegram card's outcome line for a refusal code; None when the code is not one of this table's."""
    if code in _CEILING_CARD:
        return _CEILING_CARD[code]
    np = not_placed(code)
    if np is None:
        return None
    tail = " Nothing was sent." if np.nothing_sent else ""
    return f"🛑 NOT PLACED: approved, but {BY_PHRASE[np.by]} refused it{_cause(np)}.{tail}"


def notice_text(np: NotPlaced, *, side: int, lots: float, symbol: str, account: str) -> str:
    """The owner notice (a separate Telegram message): the trade, the account, the reason and the next step. `account`
    is already masked (`entry_text.account_name`: a login never reaches the chat). Not used for the ceiling codes,
    whose shipped notice the engine words itself (`_note_bridge_refusal`)."""
    return (f"Order not placed: {BY_PHRASE[np.by]} refused {'LONG' if side > 0 else 'SHORT'} {lots:g} lots {symbol} "
            f"on {account}{_cause(np)}. {'Nothing was sent; open' if np.nothing_sent else 'Open'} trades keep their "
            f"stops. {np.next_step}")
