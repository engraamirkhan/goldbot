"""ADR 0049 D3 / T1: the demo epoch roll, the automated mark reset of a demo `probe_only` host paused at -8% (amends
ADR 0043 R1 there only; called by `capital_flows.roll_risk_period` (`Engine._roll_risk_period`) at each risk-day roll).

"It runs at the first 00:00 UTC risk-day roll after the pause, once the host is flat except P0 (open arms exit by their
own rules first) ... Mark reset: mark = max(balance, equity), stage NORMAL, automated on the fenced path (T1). It never
touches `day_start_equity` or `week_start_equity`, and never touches another engine." "-12% / HALTED: no roll. The ADR
0043 K2 re-arm path stays as it is." T1: "refused unless all of these hold: config `mode: demo` and `probe_only` and an
`approval_mode` that is not confirm; the broker-reported trade mode is DEMO when the roll is called; the drawdown is
>= 8% and < 12%; the host is flat except P0; it is a 00:00 UTC roll." T1 accepted by trading-safety on PR #290 with
F1-F6 applied.

Fences, in order:
- any account that is not `mode: demo` + `probe_only` returns at once: nothing read, nothing journalled, so a real-money
  account keeps the owner's TOTP re-arm (`Engine._poll_rearm`, `RiskGate.rearm`) as its only mark-lowering path;
- a host above the -8% pause (`exploration.DD_PAUSE`) is left alone;
- in the pause, every other condition has a named refusal, journalled (`rotation_refused`) and retried at the next
  risk-day roll: `halted`, `owner_halt` / `supervisor_halt` / `drift_halt` (an entry halt standing, F5),
  `drawdown_unknown`, `drawdown_at_halt` (at or past the 12% line, or a 12% read awaiting its ADR 0016 confirmation),
  `account_unreadable`, `terminal_not_demo` / `terminal_unknown` (a fresh read at the call), `approval_not_auto` (the
  EFFECTIVE mode, as the engine's lock works it out: the owner's /mode, else the account's, through
  `auto_entry.locked` on the fresh read, F1; ADR 0036: anything but auto is owner-confirmed), `propose_only` (the
  30-day window after an owner re-arm, F1), `positions_unreadable`, `not_flat` (any position, at the broker or in the
  engine, other than a scheduled cost probe, P0), `order_in_flight` (an unsettled send), `closes_pending`,
  `drawdown_moved` (the fresh read left the 8-12% band) and `error_<type>` (anything unexpected: fail closed).

The roll changes the mark (to max(balance, equity) read at the call: an open P0's floating loss is never banked as
headroom, F3; anchored there for ADR 0042 capital flows), the stage (NORMAL) and the epoch counter, nothing else: no
position, stop, target or exit is touched (exits are never gated), and the arms' H-61 stops, ledgers, posteriors and
look counters carry over (they live in the exploration ledger, which this never reads). Dynamic-limit state is
untouched (ADR 0043 R1): a revert at the 8% stage stays until the owner's re-arm. Each roll is journalled (`rotation`:
epoch n -> n+1, old and new mark, balance and equity, the epoch's loss; amounts only, no identifier) and, once the risk
state is saved (F4), sent to the owner on Telegram as information, never as an ask. An unsaved roll stays in memory
(the stage machine reads it), is retried on disk by the next state write, and sends no notice.

Not here (ADR 0049 T2, T4-T6, own lanes): the top-up guard against the reference balance, the tripwires and step-down
rungs, the ledger's epoch rows by sleeve and the dashboard's two drawdowns.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

import pandas as pd

from goldbot.engine import auto_entry, cost_probe, exploration
from goldbot.risk import hwm
from goldbot.risk.gate import Stage
from goldbot.telegram.entry_text import account_name

if TYPE_CHECKING:
    from goldbot.engine.runner import Engine

log = logging.getLogger(__name__)

ACTION = "rotation"                 # the journal row of a roll (ADR 0043 accounting: "the journal `rotation` row")
REFUSED = "rotation_refused"        # a paused host the roll could not reset at this risk-day roll
BY = "demo_roll"                    # who rolled: the automated path, never the owner


def eligible(eng: Engine) -> bool:
    """The config fence (T1 "refused at load on any account that could be live"): a demo `probe_only` account. Nothing
    else is ever rolled, and nothing else is read or journalled by this module."""
    return eng.cfg.mode == "demo" and eng.cfg.probe_only


def at_day_roll(eng: Engine, now: pd.Timestamp) -> None:
    """Roll a paused demo probe host at a risk-day roll when every T1 condition holds; else journal the refusal.
    Never raises: an error is a refusal (fail closed: the host stays paused, as before)."""
    if not eligible(eng):
        return
    dd = exploration.drawdown(eng)
    if dd is not None and dd < exploration.DD_PAUSE - 1e-12:
        return
    try:
        why, read = _refusal(eng, dd, now)
    except Exception as exc:                 # unreachable by design; the risk-period roll must never fail on it
        why, read = f"error_{type(exc).__name__}", None
    if why is not None or read is None:
        log.warning("demo roll refused (%s): the host stays paused until the next risk-day roll", why)
        eng.decisions.append({"ts": time.time(), "action": REFUSED, "reason": why,
                              "drawdown": None if dd is None else round(dd, 4)})
        return
    _roll(eng, now, *read)


def _refusal(eng: Engine, dd: float | None, now: pd.Timestamp) -> tuple[str | None, tuple[float, float] | None]:
    """(reason, None) when the roll is refused, else (None, (balance, equity) read at the call)."""
    st = eng.state
    if Stage(st.stage) == Stage.HALTED:
        return "halted", None
    bus = eng.center.bus
    control = bus.control() if bus is not None else None     # an unreadable control.json reads halted + propose
    if st.owner_halt or (control is not None and control.halted):
        return "owner_halt", None
    if st.supervisor_halt:
        return "supervisor_halt", None
    if st.drift_halt:
        return "drift_halt", None
    if dd is None:
        return "drawdown_unknown", None
    if st.equity_unconfirmed or eng._stage2_pending_since is not None or dd >= eng.gate.limits.dd_stage2 - 1e-12:
        return "drawdown_at_halt", None
    try:
        acc = eng.broker.account()
    except Exception:
        return "account_unreadable", None
    trade_mode = getattr(acc, "trade_mode", None)
    if trade_mode != "demo":
        return ("terminal_unknown" if trade_mode is None else "terminal_not_demo"), None
    owner_mode = control.approval_mode if control is not None else None
    if auto_entry.locked(owner_mode or eng._account_approval, eng.cfg.mode, trade_mode) != "auto":
        return "approval_not_auto", None
    if eng._propose_only_until is not None and now < eng._propose_only_until:
        return "propose_only", None
    try:
        positions = eng.broker.positions()
    except Exception:
        return "positions_unreadable", None
    p0 = eng.cfg.magic_base + cost_probe.SCHEDULED_MAGIC_OFFSET
    if any(int(p.magic) != p0 for p in positions) or \
            any(tr.agent_id != cost_probe.SCHEDULED_ID for tr in eng.open.values()):
        return "not_flat", None
    if st.unconfirmed_order:
        return "order_in_flight", None
    if eng._closes:
        return "closes_pending", None
    balance, equity = float(acc.balance), float(acc.equity)
    mark = st.balance_closed_hwm
    if not (equity > 0 and mark > 0 and 1.0 - equity / mark >= exploration.DD_PAUSE - 1e-12
            and 1.0 - equity / mark < eng.gate.limits.dd_stage2 - 1e-12):
        return "drawdown_moved", None            # the fresh read left the 8-12% band: nothing to roll, or the halt's
    return None, (balance, equity)


def _roll(eng: Engine, now: pd.Timestamp, balance: float, equity: float) -> None:
    """ADR 0049 D3 step 2: mark = max(balance, equity), stage NORMAL; the epoch counts up. The starts are not touched.
    The owner's notice goes only once the risk state is on disk (F4)."""
    st = eng.state
    before, epoch, started = st.balance_closed_hwm, eng._epoch, eng._epoch_utc
    new = max(balance, equity)
    st.equity = equity
    st.balance_closed_hwm = new
    st.stage = Stage.NORMAL
    # the deal-history anchor is the roll: a later top-up is an ADR 0042 capital flow on the new mark
    eng._set_hwm(hwm.HwmConfirmation(account_id=eng.cfg.account_id, mark=new, mark_utc=now))
    eng._epoch, eng._epoch_utc = epoch + 1, now
    dd = 1.0 - equity / before
    eng.decisions.append({"ts": time.time(), "action": ACTION, "by": BY, "epoch_from": epoch, "epoch_to": epoch + 1,
                          "epoch_started": None if started is None else started.isoformat(), "mark_from": before,
                          "mark_to": new, "balance": balance, "equity": equity, "drawdown": round(dd, 4),
                          "epoch_loss": round(before - equity, 2)})
    log.warning("demo roll: epoch %d -> %d, mark %.2f -> %.2f (drawdown %.2f%%); exploration resumes", epoch,
                epoch + 1, before, new, 100 * dd)
    if not eng._save_risk_state_now("the demo roll"):
        log.error("demo roll not saved: no owner notice until it is on disk (the next state write saves it)")
        return
    if eng.center.bus is not None:
        cur = eng._account_view.get("currency") or ""
        eng.center.bus.notify_owner(
            f"Demo {account_name(eng.cfg.account_id)} rolled to epoch {epoch + 1} (ADR 0049): it was {dd:.1%} below "
            f"its high-water mark, so the mark was reset from {before:,.2f} to {new:,.2f} {cur}".rstrip()
            + ". Exploration resumes; today's and this week's loss limits are unchanged and exits were never paused. "
            "Nothing to do: a demo top-up to the reference balance is optional.")
