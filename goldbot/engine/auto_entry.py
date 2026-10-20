"""Automatic entries: who may enter without the owner's tap, and what an automatic entry does after the order.

ADR 0036, the owner 2026-10-18: "for the demo account, the trades will be opened and closed by the system, no
confirmation is needed, for real account with real money, stick to original plan of confirming from me".

The lock (`refusal`) is the same rule at every layer: an automatic entry needs an account whose mode is demo AND a
terminal that itself reports a demo account (MT5 `account_info().trade_mode`); the paper broker (no terminal, no money)
may run auto for replays and tests. It is checked when the account is loaded (`accounts.Account`), when the engine
starts (`check_start`), on every account refresh (`locked`, after the owner's /mode) and once more at the order itself
(`permitted`, a fresh broker read). A refusal means propose: the owner is asked, nothing is placed without him. The
re-arm propose window and the 12% kill switch still force propose on demo; the RiskGate is still the only path to an
order and exits are never gated.

The post-order bookkeeping (`after_order`, `action`, `note_refusal`, `note_bridge_refusal`) moved here from runner.py (ADR 0023:
runner.py only shrinks).
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Literal, cast

import pandas as pd

from goldbot.execution.bridge import UNREADABLE_REFUSAL
from goldbot.execution.broker import ACCOUNT_REFUSAL, OrderResult
from goldbot.telegram.approvals import ACCOUNT_MODES, AskReason
from goldbot.telegram.entry_text import account_name

if TYPE_CHECKING:
    from goldbot.engine.runner import Engine
    from goldbot.risk.gate import GateDecision
    from goldbot.specialists.base import Specialist
    from goldbot.telegram.approvals import Proposal

log = logging.getLogger(__name__)


def refusal(mode: str, trade_mode: str | None) -> str | None:
    """Why automatic entries are refused for an engine of `mode` (paper | demo | live) on a terminal reporting
    `trade_mode` (demo | real | contest; None = no terminal), or None when they are allowed. Demo needs the terminal to
    say demo; paper needs no terminal or a demo one; anything else (live above all) is always refused."""
    if mode == "demo" and trade_mode == "demo":
        return None
    if mode == "paper" and trade_mode in (None, "demo"):
        return None
    return f"auto entries need a demo account on a demo terminal (account {mode}, terminal {trade_mode or 'unknown'})"


def check_start(mode: str, approval_mode: str) -> None:
    """The engine's start: a non-demo account configured auto fails loudly (ADR 0036), never runs as propose quietly."""
    if approval_mode == "auto" and mode not in ("demo", "paper"):
        raise ValueError(f"auto entries are for demo accounts only (ADR 0036): a {mode} account always asks the owner")


def lock(eng: Engine, mode: str, acc: object) -> str:
    """`locked` on the account refresh, keeping what the terminal reported (`Engine._terminal_mode`) for `ask_reason`.
    `mode` is the owner's /mode (A10), else the account's; the kill switch and the re-arm window win after it."""
    eng._terminal_mode = getattr(acc, "trade_mode", None)
    return locked(mode, eng.cfg.mode, eng._terminal_mode)


def locked(mode: str, cfg_mode: str, trade_mode: str | None) -> str:
    """The approval mode after the lock, on every refresh: `mode` (the owner's /mode, else the account's) unless it is
    auto and `refusal` refuses it, which gives propose."""
    if mode == "auto" and (why := refusal(cfg_mode, trade_mode)) is not None:
        log.warning("approval mode auto refused: %s; entries are proposed to the owner", why)
        return "propose"
    return mode


def permitted(eng: Engine, close_ts: pd.Timestamp) -> bool:
    """The check at the order itself: the engine is in auto and a fresh broker read still passes `refusal`. A refusal
    or an unreadable account sets the engine to propose (the entry is proposed instead) and is journalled. Auto
    entries paused for this bar close (`after_order`) stay on the auto path, which journals the pause and sends
    nothing: no proposal either, as before ADR 0036."""
    if eng.cfg.approval_mode != "auto":
        return False
    if eng._auto_paused is not None and eng._auto_paused[0] == close_ts:
        return True
    try:
        eng._terminal_mode = getattr(eng.broker.account(), "trade_mode", None)
        why = refusal(eng.cfg.mode, eng._terminal_mode)
    except Exception as exc:                # unread: the owner decides (fail closed), and the card says the terminal
        eng._terminal_mode = None
        why = f"account unreadable ({type(exc).__name__})"
    if why is None:
        return True
    eng.cfg.approval_mode = "propose"
    eng.decisions.append({"ts": pd.Timestamp.now("UTC").timestamp(), "action": "auto_refused", "reason": why})
    log.warning("auto entry refused at the order: %s; proposed to the owner", why)
    return False


def ask_reason(eng: Engine, now: pd.Timestamp) -> AskReason | None:
    """Why a demo account asks the owner instead of entering on its own (backlog 103; ADR 0036: demo enters
    automatically, so a demo card is an exception and says why). None on live and paper ("asking is the rule, not an
    exception": design note principle 3) and while auto entries are on. First match wins, the safety fault first:
    a terminal that does not report demo at the last read (refresh or the order itself; `terminal_not_demo`, which no
    channel may approve), the 12% kill switch, the re-arm window, the owner's /mode propose over an account configured
    auto, else the account's own configuration."""
    from goldbot.risk.gate import Stage
    if eng.cfg.mode != "demo":
        return None
    if eng._terminal_mode != "demo":
        return AskReason(kind="terminal_not_demo", terminal=eng._terminal_mode)
    if eng.state.stage == Stage.HALTED:
        return AskReason(kind="kill_switch")
    if eng._propose_only_until is not None and now < eng._propose_only_until:
        return AskReason(kind="rearm_lock", until=eng._propose_only_until.timestamp())
    if eng.cfg.approval_mode == "auto":
        return None
    return AskReason(kind="owner_propose" if eng._account_approval == "auto" else "config_propose")


def asking(eng: Engine, prop: Proposal, now: pd.Timestamp) -> Proposal:
    """Stamp a proposal about to go to the owner with the account's mode and `ask_reason` (the card's badge and its
    "Asking because" line); returns it, for `ApprovalCenter.propose`."""
    mode = eng.cfg.mode if eng.cfg.mode in ACCOUNT_MODES else None
    prop.account_mode = cast("Literal['demo', 'live', 'paper'] | None", mode)
    prop.ask_reason = ask_reason(eng, now)
    return prop


def published_reason(eng: Engine) -> dict | None:
    """`ask_reason` for the engine's state file (the dashboard's entry-mode card reads the same reason as the card)."""
    r = ask_reason(eng, pd.Timestamp.now("UTC"))
    return r.model_dump() if r is not None else None


def action(res: OrderResult | None) -> str:
    """The journal's word for an auto entry (review LOW-B): `executed:auto` only for a fill (or a duplicate, sent
    before); `auto_rejected:<retcode>` for a definite reject (nothing placed); `auto_unconfirmed:<retcode>` for
    anything else (`after_order` then stops auto entries for the rest of the bar close)."""
    from goldbot.engine.runner import DEFINITE_REJECT_RETCODES
    if res is None or (res.ok and res.position_id is not None):
        return "executed:auto"
    kind = "auto_rejected" if res.retcode in DEFINITE_REJECT_RETCODES else "auto_unconfirmed"
    return f"{kind}:{res.retcode}"


def opened_text(prop: Proposal, res: OrderResult, symbol: str, account: str) -> str:
    """The owner's notice for a filled automatic entry: information only, no buttons (ADR 0036)."""
    side = "LONG" if prop.side > 0 else "SHORT"
    entry = res.price if res.price is not None else prop.entry
    return (f"Opened automatically (demo): {side} {res.filled_lots:g} lots {symbol} on {account} at {entry:.2f}. "
            f"Stop {prop.stop:.2f}, target {prop.target:.2f}; exits are automatic.")


def _tell_opened(eng: Engine, prop: Proposal, res: OrderResult) -> None:
    """Best effort: a notice that cannot be queued is logged and never changes the outcome."""
    if eng.center.bus is None:
        return
    try:
        eng.center.bus.notify_owner(opened_text(prop, res, eng.cfg.symbol, account_name(eng.cfg.account_id)))
    except Exception as exc:                # noqa: BLE001 - the entry stands either way
        log.warning("auto-entry notice not queued (%s)", type(exc).__name__)


def after_order(eng: Engine, res: OrderResult | None, gd: GateDecision, prop: Proposal, close_ts: pd.Timestamp) -> None:
    """Trading-safety review HIGH (BACKLOG 19 review): every auto entry at one bar close must see the previous
    one, or `max_positions`, the combined cap, the heat cap and the same-direction cap can each be passed within a
    bar close. After an order the account is read again (`_refresh_account` on the broker's last tick). A fill the
    positions read does not show yet is added conservatively: one more position on its side, its lots and notional,
    the gate's margin, and its stop risk scaled to the filled lots (unknown open risk stays unknown). An ambiguous
    result (neither a fill nor a definite reject, `DEFINITE_REJECT_RETCODES`) or a failed re-read stops auto entries
    for the rest of this bar close. `res` None: nothing was sent (a duplicate), nothing changes. A fill tells the owner
    it opened on a demo account (ADR 0036)."""
    from goldbot.engine.runner import DEFINITE_REJECT_RETCODES, bridge_refused
    if res is None:
        return
    filled = res.ok and res.position_id is not None
    if filled and eng.cfg.mode == "demo":           # a demo account's broker fill; paper replays stay quiet
        _tell_opened(eng, prop, res)
    if bridge_refused(res):                         # BACKLOG 66 (trading-safety C2): nothing placed, but the brain
        eng._auto_paused = (close_ts, "bridge_refused")   # sized above the bridge's ceiling: no more this close
        return
    if not filled and res.retcode in DEFINITE_REJECT_RETCODES:
        return                                      # nothing placed: the facts are unchanged
    if not filled:
        eng._auto_paused = (close_ts, "unconfirmed_order")
        log.warning("auto entry result unconfirmed (retcode %s): auto entries stop for this bar close", res.retcode)
    try:
        eng._refresh_account(eng.broker.last_tick(eng.cfg.symbol))
        seen = eng._position_ids is not None and res.position_id in eng._position_ids
    except Exception as exc:                        # fail closed: no further auto entry on unknown facts
        log.warning("account re-read after an auto entry failed (%s): auto entries stop for this bar close",
                    type(exc).__name__)
        eng._auto_paused = eng._auto_paused or (close_ts, "account_unread")
        seen = False
    if not filled or seen:
        return
    st, lots = eng.state, float(res.filled_lots)
    st.open_positions += 1
    st.open_lots = round(st.open_lots + lots, 6)
    px = eng._price or res.price            # USD notional at the mid, as `_refresh_account` publishes it
    if px:
        st.open_notional = st.open_lots * eng._contract_size() * px
    if prop.side > 0:
        st.open_long += 1
    else:
        st.open_short += 1
    st.margin_used += gd.margin_needed
    if st.open_risk is not None:
        st.open_risk += gd.risk_amount * lots / gd.lots if gd.lots > 0 else gd.risk_amount


def note_refusal(eng: Engine, prop: Proposal, agent: Specialist, res: OrderResult, lots: float) -> None:
    """After every order (`Engine._execute`: the auto, probe and approval paths): the bridge's ceiling refusal
    (`note_bridge_refusal`), or the direct terminal's per-order account check (`note_account_refusal`); a fill ends
    an account-refusal streak, so the next refusal alerts the owner again."""
    from goldbot.engine.runner import bridge_refused
    if bridge_refused(res):
        note_bridge_refusal(eng, prop, agent, res, lots)
    elif not res.ok and res.message.startswith(ACCOUNT_REFUSAL):
        note_account_refusal(eng, prop, agent, res)
    elif res.ok and res.position_id is not None:
        eng._account_alerted = False


def note_account_refusal(eng: Engine, prop: Proposal, agent: Specialist, res: OrderResult) -> None:
    """Trading-safety follow-up (MT5Broker `verify`): the terminal is no longer on the registered account, so every
    entry is refused (a definite reject, nothing sent; exits are never checked). Each refusal is journalled
    (`account_refused`, with the raw reason) and logged; the owner is told once per streak, at its first refusal (a
    fill ends the streak, `note_refusal`), so a switched terminal never refuses entries silently and never floods the
    chat. Plain words: the masked account name, never the reason (it names the account) or the proposal id. Best
    effort: a notice that cannot be queued is logged, never changes the outcome, and is tried again next refusal."""
    log.warning("order %s refused: %s", prop.proposal_id, res.message)
    eng.decisions.append({"ts": time.time(), "agent": agent.agent_id, "action": "account_refused",
                          "proposal": prop.proposal_id, "message": res.message})
    if eng._account_alerted or eng.center.bus is None:
        return
    try:
        eng.center.bus.notify_owner(
            f"Entries refused: the MT5 terminal for {account_name(eng.cfg.account_id)} is logged in to a different "
            f"account. Nothing was sent; open trades keep their stops and exits stay automatic. Entries stay refused "
            f"until the terminal is back on this account; this alert is not repeated until an entry goes through.")
        eng._account_alerted = True
    except Exception as exc:                        # noqa: BLE001 - the refusal stands either way
        log.warning("account refusal notice not queued (%s)", type(exc).__name__)


def note_bridge_refusal(eng: Engine, prop: Proposal, agent: Specialist, res: OrderResult, lots: float) -> None:
    """BACKLOG 66 (trading-safety C2): the bridge's notional ceiling refused the order, so the brain sized above
    6x this terminal's equity (or the terminal could not be read). Nothing was placed. It is journalled
    (`bridge_refused`) and the owner is told on Telegram; the auto path then pauses auto entries for the rest of the
    bar close (`after_order`) and the approval path shows the card as refused by the bridge (`Engine._on_decision`).
    The notice is plain words (ui-ux review): the trade, the masked account name and which of the two causes,
    never the proposal id or the raw message, which stay in the journal and the log. Best effort: a notice that
    cannot be queued is logged and never changes the outcome. Moved from runner.py unchanged (ADR 0023)."""
    log.warning("order %s refused by the bridge: %s", prop.proposal_id, res.message)
    eng.decisions.append({"ts": time.time(), "agent": agent.agent_id, "action": "bridge_refused",
                          "proposal": prop.proposal_id, "message": res.message})
    auto = " Automatic entries pause until the next bar." if eng.cfg.approval_mode == "auto" else ""
    why = ("(it couldn't read the account figures it checks)" if res.message.startswith(UNREADABLE_REFUSAL)
           else "(open gold would be worth more than 6× equity)")
    if eng.center.bus is None:
        return
    try:
        eng.center.bus.notify_owner(
            f"Order not placed: the MT5 box's safety limit refused {'LONG' if prop.side > 0 else 'SHORT'} "
            f"{lots:g} lots {eng.cfg.symbol} on {account_name(eng.cfg.account_id)} {why}. Nothing was sent; open "
            f"trades keep their stops.{auto} If this repeats, the trade sizing and the MT5 box's limit disagree: "
            f"ask Claude to check.")
    except Exception as exc:                        # noqa: BLE001 - the refusal stands either way
        log.warning("bridge refusal notice not queued (%s)", type(exc).__name__)
