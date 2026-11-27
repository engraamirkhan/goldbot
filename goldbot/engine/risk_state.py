"""The engine's risk file: save (`Engine._save_risk_state`), save from the account-refresh path without raising
(`Engine._save_risk_state_now`) and restore at start (`Engine._load_risk_state`), moved verbatim out of
engine/runner.py (ADR 0023 runner split; ADR 0106's real-money daily stop needed the line room). Behaviour is
unchanged: the runner's methods call these, and `save_now` still goes through `Engine._save_risk_state`.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from goldbot.engine.store import RiskSnapshot
from goldbot.risk.gate import Stage

if TYPE_CHECKING:
    from goldbot.engine.runner import Engine

log = logging.getLogger("goldbot.engine")


def save(eng: Engine) -> None:
    st = eng.state
    eng.state_store.save_risk(RiskSnapshot(
        last_reset=eng._last_reset, day_start_equity=st.day_start_equity, week_start_equity=st.week_start_equity,
        balance_closed_hwm=st.balance_closed_hwm, stage=Stage(st.stage), rearm_seen=eng._rearm_seen,
        weekend_done=eng._weekend_done, halted_at=eng._halted_at, propose_only_until=eng._propose_only_until,
        rearm_refused=eng._rearm_refused, kill_everything=eng._kill_everything,
        stage2_pending_since=eng._stage2_pending_since, week_reset=eng._week_reset,
        flows_through=eng._flows_through, epoch=eng._epoch, epoch_utc=eng._epoch_utc,
        rm_daily_stop=st.rm_daily_stop))


def save_now(eng: Engine, what: str) -> bool:
    """Save the risk state from the account-refresh path; never raises (BACKLOG 44 re-review MEDIUM-1, LOW-2). The
    in-memory state (the pending 12% read, the stage, the mark) decides the kill switch, so a failed write (a full
    disk) must never abort the refresh before the stage machine, skip the flatten, or narrow it. The failure is
    logged and counted as a tick error, which blocks entries until a clean decision bar; the next state write
    (`_write_state`) saves the risk state again. True when saved."""
    try:
        eng._save_risk_state()
    except Exception as exc:
        log.error("risk state not saved after %s (%s); kept in memory, entries blocked, retried with the next "
                  "state write", what, type(exc).__name__)
        eng.note_tick_error(exc)
        return False
    return True


def load(eng: Engine) -> None:
    eng._kill_everything = False
    snap = eng.state_store.load_risk()       # unreadable -> raises: fail loudly rather than forget a drawdown halt
    if snap is None:
        # first start: an earlier re-arm on the bus is not a re-arm of this engine
        eng._rearm_seen = eng.center.bus.control().rearm_id if eng.center.bus is not None else None
        return
    st = eng.state
    eng._last_reset = snap.last_reset
    st.day_start_equity = snap.day_start_equity
    st.week_start_equity = snap.week_start_equity
    st.balance_closed_hwm = snap.balance_closed_hwm
    st.stage = snap.stage
    eng._rearm_seen, eng._weekend_done = snap.rearm_seen, snap.weekend_done
    eng._halted_at = snap.halted_at
    eng._propose_only_until = snap.propose_only_until
    eng._rearm_refused = snap.rearm_refused
    eng._stage2_pending_since = snap.stage2_pending_since
    eng._kill_everything = snap.kill_everything
    eng._week_reset, eng._flows_through = snap.week_reset, snap.flows_through
    eng._epoch, eng._epoch_utc = snap.epoch, snap.epoch_utc
    st.rm_daily_stop = eng._rm_daily_stop_saved = snap.rm_daily_stop   # ADR 0106: held to the roll across a restart
