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
* the account read is not used: equity, balance, the high-water mark, the drawdown stages and the account view keep
  this account's last reading (`Engine._refresh_account` returns before them). The risk-period roll checks its own read
  and does not roll (`capital_flows.roll_risk_period`: `_last_reset` stays, so it rolls once the terminal is back); a
  re-arm waits unconsumed (`rearm_refused` = terminal_account_changed); a closed-trade record takes the engine's last
  equity, never the terminal's balance;
* every positions and deal-history read of the engine fails as unreadable (`Engine._read_positions`,
  `Engine._read_deals`, the kill switch's book), so nothing is adopted, swept closed or resolved from the other book;
* exits are never gated, and a false positive (a check that raised) must not stop one. With no book to read, each due
  close is sent by its ticket (`_retry_closes`, never dropped as gone), a trade through this engine's stop is closed by
  its ticket (`_stop_check`, without the broker's stop to compare), and the kill switch closes the tickets this engine
  knows (never re-reading the whole account, never seen flat). MT5 tickets are unique per server, so on another login
  such a close is refused (10013) and stays due; on this account it closes. The positions rest with their broker-side
  stop and target meanwhile. Residual-risk ruling (safety re-check): tickets are unique per trade SERVER only, so
  while the read names another server than the one this account was last seen on (`_terminal_other_server`) nothing
  is sent by ticket (`by_ticket`); the broker's stops protect the positions until the terminal is back. A check that
  raised, a read with no server, or another login on the same server still send. No server name is ever logged;
* the engine state carries the reason (`terminal_account_changed`), health FAILs on it (alerted on Telegram), and the
  owner is told once when it starts and once when the terminal is back.

The check runs on every account refresh (the state cadence and every bar close), on every reconciliation before its
positions read (so an orphan of the other login is never adopted between two refreshes), before the kill switch's
whole-account read, on the risk-period roll and the re-arm reads, and every `reconcile_every_s` in `no_quote_pass`
(no refresh runs without a quote). Without a check wired in (`Engine.verify_account` None: the paper broker, replays, backtests)
nothing here runs. A check that raises counts as a mismatch (fail closed for entries; exits still run, above). Back on
the account the next read clears it; every book's entries then wait out `healthy_resume_s` from the restore
(`recovering`, part of `data_recovering`), not only the book that saw the mismatch."""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

import pandas as pd

from goldbot.base import Record, UtcTimestamp
from goldbot.telegram.entry_text import account_name

if TYPE_CHECKING:
    from goldbot.engine.runner import Engine

log = logging.getLogger("goldbot.engine")

REASON = "terminal_account_changed"


class TerminalAccountChanged(RuntimeError):
    """A broker read refused because the terminal is not on this engine's account (counted as positions_unreadable)."""


class Watch(Record):
    """`Engine.terminal`, what the checks found: `mismatch` the reason while the terminal is elsewhere (None on this
    account); `restored_at` when it came back (every book waits healthy_resume_s from it); `server` the trade server this
    account was last verified on and `other_server` whether a flagged read named another one (never logged:
    `by_ticket`); `checked_at` no_quote_pass's own check, every reconcile_every_s."""
    mismatch: str | None = None
    restored_at: UtcTimestamp | None = None
    server: str | None = None
    other_server: bool = False
    checked_at: UtcTimestamp | None = None


def changed(eng: Engine) -> bool:
    """Whether the last check found the terminal on another account."""
    return eng.terminal.mismatch is not None


def check(eng: Engine, info: Any) -> bool:
    """Judge one account read (`broker.account()`): True when it is this engine's account (or no check is wired in).
    A change either way is logged, journalled and told to the owner once."""
    verify, w = eng.verify_account, eng.terminal
    if verify is None:
        return True
    raised = False
    try:
        why = verify(info)
    except Exception as exc:  # noqa: BLE001 - a reply the check cannot read is never this account (fail closed)
        why, raised = f"the terminal's account could not be checked ({type(exc).__name__})", True
    server = str(getattr(info, "server", "") or "").strip()     # compared only; never logged or told
    if why is None:
        w.server, w.other_server = server or w.server, False
        if w.mismatch is not None:
            w.mismatch, w.restored_at = None, _now(eng)
            eng.state.terminal_account_changed = False
            log.warning("%s: the MT5 terminal is back on this account; reads resume, entries after the data resume "
                        "delay", eng.cfg.account_id)
            eng.decisions.append({"ts": time.time(), "action": "terminal_account_restored"})
            _tell(eng, f"{account_name(eng.cfg.account_id)}: the MT5 terminal is back on this account. Positions and "
                       "deals are read again; new entries resume after a minute of healthy data.")
        return True
    first = w.mismatch is None
    w.mismatch = why
    # residual-risk ruling: only a read that names another, known trade server stops the by-ticket closes
    w.other_server = not raised and bool(server) and w.server is not None and server != w.server
    eng.state.terminal_account_changed = True
    eng._unhealthy_at = _now(eng)
    if first:
        log.error("%s: %s. Entries refused (%s); no close or adoption is inferred from its book; positions keep "
                  "their broker stops", eng.cfg.account_id, why, REASON)
        eng.decisions.append({"ts": time.time(), "action": REASON, "detail": why})
        _tell(eng, f"⚠️ {account_name(eng.cfg.account_id)}: the MT5 terminal is no longer on this account. New "
                   "entries are refused and nothing is read from the other account; open trades keep their stops "
                   "and targets at the broker. Log the terminal back in to this account; trading resumes by itself.")
    return False


def refused_refresh(eng: Engine) -> None:
    """`_refresh_account` on another account's read: none of its figures are this account's (equity, stages, high-water
    mark, risk period); the positions read is refused and counted (positions_unreadable)."""
    eng._read_positions("exposure kept from the last reading, entries refused (terminal_account_changed)")


def by_ticket(eng: Engine) -> bool:
    """Whether a close may be sent by its ticket while the terminal is flagged (`_retry_closes`, `_stop_check`, the
    kill switch). MT5 tickets are unique per trade server only, so not while the terminal is on ANOTHER server than the
    one this account was last seen on: a ticket there could be a stranger's position. The broker's own stop and target
    protect the positions until the terminal is back. Every other case sends: a check that raised (a false positive
    must never stop an exit), a read with no server, another login on the same server (refused there, 10013)."""
    return not eng.terminal.other_server


def retry_closes(eng: Engine, due: list[int], now: pd.Timestamp) -> None:
    """`_retry_closes` while flagged: exits are never gated. No book can be read, so each due close is sent by its
    ticket (another login on the same server refuses it, 10013, and it stays due) and none is dropped as gone; on
    another server nothing is sent (`by_ticket`)."""
    if by_ticket(eng):
        for pid in due:
            action, extra, _ = eng._close_due[pid]
            eng._try_close(pid, action, now, **extra)
        eng._save_orders()


def stop_closes(eng: Engine, through: list[tuple[int, Any]], now: pd.Timestamp) -> None:
    """`_stop_check` while flagged: the broker's stop cannot be read, so a trade through this engine's stop is closed by
    its ticket (a false positive closes it, as its stop would); never on another server (`by_ticket`)."""
    if by_ticket(eng):
        for pid, tr in through:
            if pid not in eng._close_due:
                eng._try_close(pid, "engine_stop_close", now, sl=tr.sl, broker_sl=None)
        eng._save_orders()


def kill_closes(eng: Engine) -> None:
    """The kill switch while flagged: no book can be read, so the tickets this engine knows are closed by ticket, never
    another account's, never on another server (`by_ticket`). Not flat: it runs again once the terminal is back."""
    if changed(eng) and by_ticket(eng):
        for pid, tr in list(eng.open.items()):
            if pid not in eng._close_due:
                eng._try_close(pid, "kill_switch_close", None, symbol=eng.cfg.symbol, lots=tr.lots)


def no_quote_check(eng: Engine, now: pd.Timestamp) -> None:
    """`no_quote_pass`: no refresh runs without a quote, so the account is checked here every reconcile_every_s."""
    w = eng.terminal
    if w.checked_at is None or (now - w.checked_at).total_seconds() >= eng.cfg.reconcile_every_s:
        w.checked_at = now
        verified(eng)


def rearm_waits(eng: Engine) -> bool:
    """A re-arm while flagged waits, unconsumed (`rearm_refused` = terminal_account_changed): it reads the account."""
    if changed(eng):
        eng._rearm_refused = REASON
    return changed(eng)


def recovering(eng: Engine, now: pd.Timestamp) -> bool:
    """Within `healthy_resume_s` of the terminal coming back to this account (every book's `data_recovering`)."""
    at = eng.terminal.restored_at
    return at is not None and (now - at).total_seconds() < eng.cfg.healthy_resume_s


def _now(eng: Engine) -> pd.Timestamp:
    return eng._now(eng._last_tick) if eng._last_tick is not None else eng.clock()


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


def refuse_read(eng: Engine, kind: str, then: str) -> bool:
    """While the terminal is on another account, count the read as refused (positions_unreadable, logged once per
    streak) and return True for a positions read, raise `TerminalAccountChanged` for a deals read; False otherwise."""
    if eng.terminal.mismatch is None:
        return False
    eng._read_failed("positions", then, traceback=False)
    if eng._read_fails["positions"] == 1:
        log.warning("positions read refused: %s; %s", eng.terminal.mismatch, then)
    if kind == "deals":
        raise TerminalAccountChanged(eng.terminal.mismatch)
    return True


def _tell(eng: Engine, text: str) -> None:
    """One Telegram notice (the bus's owner notices); best effort, never raised. Never a login, server or host."""
    bus = eng.center.bus
    if bus is None:
        return
    try:
        bus.notify_owner(text)
    except Exception as exc:  # noqa: BLE001 - the refusal stands either way; health FAILs on it too
        log.warning("terminal-account notice not queued (%s)", type(exc).__name__)
