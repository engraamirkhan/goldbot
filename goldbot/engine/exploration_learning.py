"""The demo learning class in the engine (ADR 0061 amendment 2026-11-02; owner 2026-11-02: "fix the limits of our system
to trade ... for the system to be not bound to test, learn and improve signals", and YES to a demo "learning" class).

A learning arm (`role: learning`, `research.arms.Learning`) enters on a demo probe_only account without a history
qualification, inside bounds of its own:

* At the engine's start (`start`) its `learning.control` must be configured on THIS account, not refused, and matched
  (`research.arms.control_match_refusal`: same timeframe, stop, target and hold), else the arm is refused from the start
  (`exploration_arm_unqualified`), like an unqualified strategy: configured, so an open position keeps every exit.
* Once a minute (`refresh`, beside the cost reads) this host's ledger is read: each learning arm's admissible closed
  fills (`exploration.admissible_fills`'s rule) and its cumulative net P&L over every filled close, a fill whose P&L
  is not readable counted as a loss at its stop plus its round trip (never as 0). The arm retires on whichever comes
  first: LEARNING_READ_FILLS (300) admissible fills (`exploration_arm_learning_read_due`: its read is due), a loss of
  `loss_cap_gbp` or more (`exploration_arm_learning_loss_cap`), or a day past `until`
  (`exploration_arm_learning_expired`). A control dedicated to learning arms (`research.arms.dedicated_control_of`)
  retires with them once none of the arms it serves here may enter. The verdict is recomputed from the ledger on every
  read, so it holds across a restart.
* Fail closed: until a read has succeeded in this process, and while reads fail, the learning arms and their dedicated
  controls send nothing (`exploration_refused:learning_unread`, `exploration.Exploration.learning_ok`); a failing
  read is journalled once (`exploration_learning_unread`) until one succeeds. Every other arm is unaffected.
* `learning_slots` (in `exploration._send`): at most `learning_max_open` learning positions open at once.

Entries only: nothing here touches an open position, a stop, a target or an exit.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

import pandas as pd
from pydantic import ValidationError

from goldbot.research.arms import (
    LEARNING_READ_FILLS,
    LEARNING_RULE,
    Learning,
    control_match_refusal,
    dedicated_control_of,
)

if TYPE_CHECKING:
    from goldbot.engine.exploration import Exploration
    from goldbot.engine.runner import Engine

log = logging.getLogger(__name__)

READ_DUE = "exploration_arm_learning_read_due"
LOSS_CAP = "exploration_arm_learning_loss_cap"
EXPIRED = "exploration_arm_learning_expired"
UNREAD = "exploration_learning_unread"


def _block(arm: Any) -> Learning | None:
    try:
        return Learning.model_validate(arm.spec.learning)
    except ValidationError:
        return None


def group(ex: Exploration) -> frozenset[str]:
    """The arms whose entries wait on a learning read: every learning arm and every control dedicated to one."""
    return frozenset(a for a, arm in ex.arms.items()
                     if arm.spec.role == "learning" or dedicated_control_of(arm.spec))


def _stop(eng: Engine, ex: Exploration, stops: dict[str, tuple[str, str, dict[str, Any]]], now: Any) -> None:
    """Retire `stops` (arm -> (journal action, rule, detail)): no new entry, configured (every exit kept), journalled
    once each, their waiting signals recorded blocked `arm_retired`."""
    from goldbot.engine.exploration import PREFIX
    from goldbot.engine.exploration import _block as block_signals
    if not stops:
        return
    ex.retired = ex.retired | frozenset(stops)
    ts = now.isoformat() if isinstance(now, pd.Timestamp) else time.time()
    for a, (action, rule, detail) in sorted(stops.items()):
        ex.unqualified[a] = rule
        log.warning("exploration arm %s refused: %s", a, rule)
        eng.decisions.append({"ts": ts, "agent": PREFIX + a, "action": action, "arm": a, "rule": rule, **detail})
    if isinstance(now, pd.Timestamp):
        block_signals(eng, ex, [s for s in ex.queue if s.arm_id in ex.retired], now, "arm_retired")


def _controls(ex: Exploration, stopping: set[str]) -> dict[str, tuple[str, str, dict[str, Any]]]:
    """Each dedicated control (not retired) none of whose served arms configured here may still enter."""
    out: dict[str, tuple[str, str, dict[str, Any]]] = {}
    for a, arm in ex.arms.items():
        serves = dedicated_control_of(arm.spec)
        if not serves or a in ex.retired or a in stopping:
            continue
        live = [s for s in serves if s in ex.arms and s not in ex.retired and s not in stopping]
        if not live:
            out[a] = ("exploration_arm_unqualified",
                      f"{LEARNING_RULE}: the dedicated control of {', '.join(serves)} stops with them", {})
    return out


def start(eng: Engine, ex: Exploration) -> None:
    """At the engine's start: a learning arm whose control is not configured on this account, is refused, or is not
    matched is refused with the reason; then every dedicated control left with no learning arm that may enter."""
    stops: dict[str, tuple[str, str, dict[str, Any]]] = {}
    for a, arm in sorted(ex.arms.items()):
        if arm.spec.role != "learning" or a in ex.retired:
            continue
        block = _block(arm)
        if block is None:                     # refused at load already (entry_refused); kept here as a backstop
            stops[a] = ("exploration_arm_unqualified", f"{LEARNING_RULE}: learning block invalid", {})
            continue
        ctl = ex.arms.get(block.control)
        if ctl is None:
            why: str | None = f"control {block.control} is not configured on {eng.cfg.account_id}"
        elif ctl.arm_id in ex.retired:
            why = f"control {block.control} may not enter on {eng.cfg.account_id}"
        else:
            why = control_match_refusal(arm.spec, ctl.spec)
        if why is not None:
            stops[a] = ("exploration_arm_unqualified", f"{LEARNING_RULE}: {why}", {})
    stops |= _controls(ex, set(stops))
    _stop(eng, ex, stops, None)


def read(conn: Any, arm_id: str, cap: float) -> tuple[int, float]:
    """(admissible closed fills, cumulative net P&L) of `arm_id` in this host's ledger, every account. Admissible:
    filled, closed by its own barrier (not censored), net P&L recorded. The P&L sums every filled close; a close
    without a readable P&L counts as a loss at its stop plus its round trip (`cap`, the arm's per-trade cap, when its
    loss at the stop was not recorded)."""
    from goldbot.engine.exploration import LEDGER
    fills, pnl = 0, 0.0
    for t in LEDGER.select(conn, "arm_id = ? AND closed = 1", (arm_id,)):
        if t.fill_price is None or t.exit_utc is None:
            continue
        if t.pnl is not None:
            pnl += t.pnl
            fills += int(not t.censored)
        else:
            pnl -= (t.stop_loss if t.stop_loss is not None else cap) + (t.cost or 0.0)
    return fills, pnl


def refresh(eng: Engine, ex: Exploration, now: pd.Timestamp) -> None:
    """Once a minute: read every learning arm not retired and retire it at its read, its loss cap or its end; its
    dedicated control with it. A failed read keeps `learning_ok` False (their entries refused) and is journalled once
    until a read succeeds."""
    minute = now.floor("min")
    if ex.learning_read_at == minute:
        return
    ex.learning_read_at = minute
    due = sorted(a for a in ex.arms if ex.arms[a].spec.role == "learning" and a not in ex.retired)
    if not due:
        ex.learning_ok = True
        _stop(eng, ex, _controls(ex, set()), now)
        return
    caps = eng.gate.limits.exploration_arms
    try:
        with ex.db.connection() as conn:
            reads = {a: read(conn, a, caps.get(a, max(caps.values(), default=0.0))) for a in due}
    except Exception as exc:
        ex.learning_ok = False
        log.warning("learning arms' ledger unreadable (%s): learning entries refused", type(exc).__name__)
        if not ex.learning_warned:
            ex.learning_warned = True
            eng.decisions.append({"ts": now.isoformat(), "agent": None, "action": UNREAD, "arms": due,
                                  "error": type(exc).__name__})
        return
    ex.learning_ok, ex.learning_warned = True, False
    stops: dict[str, tuple[str, str, dict[str, Any]]] = {}
    today = now.tz_convert("UTC").normalize()
    for a in due:
        block = _block(ex.arms[a])
        if block is None:
            stops[a] = ("exploration_arm_unqualified", f"{LEARNING_RULE}: learning block invalid", {})
            continue
        fills, pnl = reads[a]
        detail: dict[str, Any] = {"fills": fills, "loss": round(max(0.0, -pnl), 2), "loss_cap": block.loss_cap_gbp}
        if fills >= LEARNING_READ_FILLS:
            stops[a] = (READ_DUE, f"{LEARNING_RULE}: learning_read_due at {fills} admissible fills", detail)
        elif -pnl >= block.loss_cap_gbp - 1e-9:
            stops[a] = (LOSS_CAP, f"{LEARNING_RULE}: learning_loss_cap {block.loss_cap_gbp:g} reached", detail)
        elif today > pd.Timestamp(block.until, tz="UTC"):
            stops[a] = (EXPIRED, f"{LEARNING_RULE}: learning_expired after {block.until}", detail)
    stops |= _controls(ex, set(stops))
    _stop(eng, ex, stops, now)
