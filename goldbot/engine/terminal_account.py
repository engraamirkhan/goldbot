"""The terminal's account, checked again after start (trading-safety review 2026-11-09, R1). Kept out of
engine/runner.py (ADR 0023: runner.py only shrinks); the runner calls in from `_refresh_account`, `_reconcile`, the
broker reads and the state write.

The engine service checks the terminal's account once, before the engine starts (`accounts.verify_terminal_account`:
login, server, demo vs real, currency, hedging). A terminal can be logged in to another account afterwards (by hand,
over VNC, by a terminal restart on another saved login). The bridge and the direct adapter already refuse an ENTRY on a
mismatch at the order; nothing stopped the engine from READING the other account as its own: adopting its positions,
recording its own trades closed from a book that does not hold them, and taking its equity for the drawdown stages.

Design rule (R1): "run the same check as at start on every account read. On a mismatch refuse entries
(`terminal_account_changed`), set `positions_unreadable`, and stop inferring any close or adoption from that read.
Positions keep their broker-side SL/TP. Exits are never gated, but no state is lost. Raise a health FAIL and a Telegram
alert." Concretely, while the terminal is on another account:

* every entry is refused `terminal_account_changed` (`AccountState.terminal_account_changed`, RiskGate);
* the account read is not used: equity, balance, the high-water mark, the drawdown stages, the risk-period roll and the
  account view keep this account's last reading (`Engine._refresh_account` returns before them);
* every positions and deal-history read of the engine fails as unreadable (`Engine._read_positions`,
  `Engine._read_deals`, the kill switch's book), so nothing is adopted, swept closed or resolved from the other book.
  Closes already decided are still sent every tick (exits are never gated); against another login they fail and are
  retried. The positions themselves rest with their broker-side stop and target;
* the engine state carries the reason (`terminal_account_changed`), health FAILs on it (alerted on Telegram), and the
  owner is told once when it starts and once when the terminal is back.

The check runs on every account refresh (the state cadence and every bar close) and on every reconciliation, before
its positions read (so an orphan of the other login is never adopted between two refreshes); `no_quote_pass` looks go
through the same reads. Without a check wired in (`Engine.verify_account` None: the paper broker, replays, backtests)
nothing here runs. A check that raises counts as a mismatch (fail closed). Back on the account the next read clears
it; entries then wait out `healthy_resume_s` like any other data fault (`Engine._unhealthy_at`)."""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from goldbot.telegram.entry_text import account_name

if TYPE_CHECKING:
    from goldbot.engine.runner import Engine

log = logging.getLogger("goldbot.engine")

REASON = "terminal_account_changed"


class TerminalAccountChanged(RuntimeError):
    """A broker read refused because the terminal is not on this engine's account (counted as positions_unreadable)."""


def changed(eng: Engine) -> bool:
    """Whether the last check found the terminal on another account."""
    return eng._terminal_mismatch is not None


def check(eng: Engine, info: Any) -> bool:
    """Judge one account read (`broker.account()`): True when it is this engine's account (or no check is wired in).
    A change either way is logged, journalled and told to the owner once."""
    verify = eng.verify_account
    if verify is None:
        return True
    try:
        why = verify(info)
    except Exception as exc:  # noqa: BLE001 - a reply the check cannot read is never this account (fail closed)
        why = f"the terminal's account could not be checked ({type(exc).__name__})"
    if why is None:
        if eng._terminal_mismatch is not None:
            eng._terminal_mismatch = None
            eng.state.terminal_account_changed = False
            log.warning("%s: the MT5 terminal is back on this account; reads resume, entries after the data resume "
                        "delay", eng.cfg.account_id)
            eng.decisions.append({"ts": time.time(), "action": "terminal_account_restored"})
            _tell(eng, f"{account_name(eng.cfg.account_id)}: the MT5 terminal is back on this account. Positions and "
                       "deals are read again; new entries resume after a minute of healthy data.")
        return True
    first = eng._terminal_mismatch is None
    eng._terminal_mismatch = why
    eng.state.terminal_account_changed = True
    eng._unhealthy_at = eng._now(eng._last_tick) if eng._last_tick is not None else eng.clock()
    if first:
        log.error("%s: %s. Entries refused (%s); no close or adoption is inferred from its book; positions keep "
                  "their broker stops", eng.cfg.account_id, why, REASON)
        eng.decisions.append({"ts": time.time(), "action": REASON, "detail": why})
        _tell(eng, f"⚠️ {account_name(eng.cfg.account_id)}: the MT5 terminal is no longer on this account. New "
                   "entries are refused and nothing is read from the other account; open trades keep their stops "
                   "and targets at the broker. Log the terminal back in to this account; trading resumes by itself.")
    return False


def verified(eng: Engine) -> bool:
    """A fresh account read and `check` (reconciliation, before it reads positions). An account read that raises is
    not judged here: the positions read that follows fails the same way and is counted."""
    if eng.verify_account is None:
        return True
    try:
        info = eng.broker.account()
    except Exception:  # noqa: BLE001 - judged on the next read; the last verdict stands
        return not changed(eng)
    return check(eng, info)


def refuse_read(eng: Engine) -> None:
    """Raise `TerminalAccountChanged` while the terminal is on another account (before any positions or deals read)."""
    if eng._terminal_mismatch is not None:
        raise TerminalAccountChanged(eng._terminal_mismatch)


def _tell(eng: Engine, text: str) -> None:
    """One Telegram notice (the bus's owner notices); best effort, never raised. Never a login, server or host."""
    bus = eng.center.bus
    if bus is None:
        return
    try:
        bus.notify_owner(text)
    except Exception as exc:  # noqa: BLE001 - the refusal stands either way; health FAILs on it too
        log.warning("terminal-account notice not queued (%s)", type(exc).__name__)
