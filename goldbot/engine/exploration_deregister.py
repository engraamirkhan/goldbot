"""BACKLOG 187: a retired arm's registration is dropped by the engine itself once the account is flat in it.

Rule (R133, docs/research/profitability-2026-11-12.md section 4): the retired arms (I0-USTEC, I0-XAUEUR, I0-DE40,
I0-US500, I0-US30, I0-XAUJPY, ZCD, ...) "stay registered so their open positions keep every exit (a dropped
registration is closed at once as `exploration_orphan`)"; "once each account shows no open position of them, drop the
rows". Waiting for someone to check and edit config/accounts.yaml is the manual step this module removes.

How, at every reconciliation after a successful broker position read (`exploration.sweep`, called by
`Engine._reconcile` with the book's live positions, after its adoptions): an arm whose frozen spec row records a
retirement (`research.arms.Arm.retired_day`) is de-registered when its book holds no open trade of it, no order of it
is in flight (sending or ambiguous) and no signal of it is waiting (queued or deferred). Retired, it can open nothing
again, so flat now is flat for good. De-registered means: no signal or bar work at its closes (`exploration.step`,
`_minutes` leave it out), so a book whose arms are all retired does no exploration bar work. Its identity stays known
to the exits and to adoption (it stays in `Exploration.arms`), so a position of it that ever reappears is adopted and
managed with every exit, never closed as an orphan: this never closes, modifies or reads more than the positions the
reconciliation already read.

Idempotent: the first de-registration of an (account, arm) is recorded in aux.db `exploration_arm_deregistered`
(aux migration 35, INSERT OR IGNORE) and journalled `exploration_arm_deregistered` with its rule; a restart registers
the arm again from config and drops it again after its first read, logged only. A table that cannot be written still
de-registers in memory (logged; the next start retries the record). The config row itself stays: a running service
never rewrites the deployed config, and a row of a de-registered arm is inert."""
from __future__ import annotations

import logging
import sqlite3
import time
from typing import TYPE_CHECKING

from goldbot.db import Database

if TYPE_CHECKING:
    from goldbot.engine.exploration import Exploration, ExplorationConfig
    from goldbot.engine.runner import Engine

log = logging.getLogger(__name__)

ACTION = "exploration_arm_deregistered"


def candidates(cfg: ExplorationConfig) -> list[str]:
    """The arms of `cfg` the engine drops once flat: those whose frozen spec row records a retirement. An arm retired
    at its stop (aux.db status) or refused for any other reason is not one: only the spec retirement is permanent by
    the frozen row itself."""
    return sorted(a.arm_id for a in cfg.arms if a.spec.retired)


def holds(eng: Engine, ex: Exploration, arm_id: str) -> str | None:
    """Why `arm_id` must stay registered on this book now, or None when it is flat: an open trade (the broker's
    positions as reconciled, and any close not yet confirmed), an order in flight, or a signal waiting."""
    from goldbot.engine import unconfirmed
    from goldbot.engine.exploration import PREFIX
    tag = PREFIX + arm_id
    if any(t.agent_id == tag for t in eng.open.values()):
        return "position open"
    if any(o.status in ("sending", unconfirmed.AMBIGUOUS) and o.agent_id == tag for o in eng._orders.values()):
        return "order in flight"
    if any(s.arm_id == arm_id for s in (*ex.queue, *ex.deferred.values())):
        return "signal waiting"
    return None


def refresh(eng: Engine, ex: Exploration) -> None:
    """After a successful broker position read (the caller's): every spec-retired arm of this book that is flat
    (`holds`) joins `ex.deregistered`, recorded and journalled once. Never raises, never touches a position."""
    try:
        new = [a for a in candidates(ex.cfg) if a in ex.arms and a not in ex.deregistered and holds(eng, ex, a) is None]
        if not new:
            return
        ex.deregistered = ex.deregistered | frozenset(new)
        first = _record(eng.cfg.state_dir, eng.cfg.account_id, new)
        for a in new:
            rule = ex.arms[a].spec.retired_rule
            if a in first:
                log.warning("exploration arm %s de-registered on %s: retired (%s) and flat", a, eng.cfg.account_id,
                            rule)
                eng.decisions.append({"ts": time.time(), "agent": ex.arms[a].tag, "action": ACTION, "arm": a,
                                      "rule": rule})
            else:
                log.info("exploration arm %s de-registered again on %s (recorded before)", a, eng.cfg.account_id)
    except Exception:
        log.exception("exploration de-registration failed; retried at the next reconciliation")


def _record(state_dir: str, account_id: str, arms: list[str]) -> set[str]:
    """Inserts (account, arm) rows not there yet; the arms inserted now. A write error: every arm counts as first
    (journalled; the next start records them)."""
    now = time.time_ns()
    try:
        with Database.open(state_dir, "aux").write() as conn:
            before = recorded_in(conn, account_id)
            conn.executemany("INSERT OR IGNORE INTO exploration_arm_deregistered (account_id, arm_id, deregistered_ns)"
                             " VALUES (?, ?, ?)", [(account_id, a, now) for a in arms])
    except (sqlite3.Error, OSError) as exc:
        log.error("exploration de-registrations not recorded in %s (%s)", state_dir, exc)
        return set(arms)
    return set(arms) - before


def recorded_in(conn: sqlite3.Connection, account_id: str) -> set[str]:
    return {str(a) for (a,) in conn.execute(
        "SELECT arm_id FROM exploration_arm_deregistered WHERE account_id = ?", (account_id,))}


def recorded(state_dir: str, account_id: str) -> set[str]:
    """The arms ever de-registered on `account_id` (reports and tests); raises when aux.db cannot be read."""
    with Database.open(state_dir, "aux").read() as conn:
        return recorded_in(conn, account_id)
