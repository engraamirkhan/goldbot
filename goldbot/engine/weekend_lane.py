"""The weekend rule of an always-open account (ADR 0062, the XAUUSD247 weekend lane).

Design rule: "an always-open account trades when its own calendar says open; every other account keeps the weekday
calendar and the Friday cut byte for byte. A weekend_only account takes entries only inside its window and is flat
before the Monday reconvergence."

* `lane(cfg, now)` is None for every account whose `always_open` is off: the engine then runs its Friday cut exactly as
  before (`Engine._server_clock`, `_host_clock_exits`). For an always-open account there is no Friday cut at all; with
  a `weekend_window` entries are blocked outside it (`AccountState.weekend`, the gate's `weekend` reason) and, once
  past its flat cut, every position of this engine is closed, winners too (`weekend_lane_flat`), retried every refresh
  until done, once per cut (`Engine._weekend_done` holds the cut's key, persisted with the risk state).
* No quote is needed to flatten (market closes): the no-quote pass flattens at once, without waiting for a dead feed,
  because no price has to tell a loser from a winner.
* `crosses_cut`: an entry whose maximum hold reaches the next flat cut is refused (exploration's `weekend_hold`), so a
  label never straddles the lane's end.
Exits are never gated: the flatten only closes; it never blocks a stop, target or close of any other path.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.data.calendar import DEFAULT_SESSIONS, SessionTable
from goldbot.engine import pilot
from goldbot.execution.broker import OrderResult, Tick

if TYPE_CHECKING:
    from goldbot.engine.runner import Engine, EngineConfig

log = logging.getLogger("goldbot.engine")      # the engine's logger, as the rule logged before its move
FLAT_ACTION = "weekend_lane_flat"


class Lane(FrozenRecord):
    """An always-open account's weekend state at one instant."""
    block: bool          # no new entry now (outside the weekend window)
    flatten: bool        # past the flat cut: every own position must be closed
    key: str             # the cut this flatten belongs to (ISO instant), kept once it completed


def lane(cfg: EngineConfig, now: pd.Timestamp) -> Lane | None:
    """None for a weekday account (its Friday cut is unchanged); else what the lane allows at `now`."""
    if not cfg.always_open:
        return None
    w = cfg.weekend_window
    if w is None:                                         # 24/7 without a weekend-only window: no cut at all
        return Lane(block=False, flatten=False, key="")
    outside = not w.inside(now)
    return Lane(block=outside, flatten=outside, key=w.last_cut(now).isoformat())


def crosses_cut(cfg: EngineConfig, now: pd.Timestamp, hold_s: float) -> bool:
    """An entry at `now` held up to `hold_s` reaches the lane's next flat cut (or is sent outside the window)."""
    w = cfg.weekend_window
    if w is None:
        return False
    return not w.inside(now) or now + pd.Timedelta(seconds=hold_s) >= w.next_cut(now)


def flatten(eng: Engine, now: pd.Timestamp) -> bool:
    """Close every position of this engine (its magic range), winners too, at market. Each close is tried on its own;
    a refused one is re-sent every tick by `_retry_closes`. True only when every own position was closed (an unreadable
    book is not done: retried on the next refresh)."""
    positions = eng._read_positions("the weekend lane's flatten is retried on the next refresh")
    if positions is None:
        return False
    done = True
    for p in positions:
        if not eng._own(p.magic):
            continue
        ok = p.position_id not in eng._close_due and eng._try_close(p.position_id, FLAT_ACTION, now).ok
        done = done and ok
    return done


def bar_table(eng: Any) -> SessionTable:
    """The table the engine builds its 1m bars on (live minutes, broker bars, the history fill): DEFAULT_SESSIONS on
    every weekday account, exactly as before; an always-open account's own table, so its weekend minutes are bars at
    all (the weekday table would drop every one of them)."""
    return eng.sessions if getattr(eng.cfg, "always_open", False) else DEFAULT_SESSIONS


def weekday_rule(eng: Engine, tick: Tick) -> bool:
    """`Engine._weekend_rule`, moved here verbatim (runner.py line cap; the weekday accounts' Friday cut, unchanged).
    Design: weekend gaps blow through stops, so losers are closed and winners' stops moved to lock in half the open
    profit (never loosened). Exits are automatic and never gated. Each position is handled on its own (one raising or
    refused call never stops the others); a failed loser close is re-sent every tick (`_retry_closes`) and the rule
    runs again on each account refresh until it completes. Returns whether it completed for every position (an
    unreadable book is not complete)."""
    positions = eng._read_positions("the weekend rule is retried on the next refresh")
    if positions is None:
        return False
    done, left, flat = True, len(positions), pilot.weekend_flat(eng)     # ADR 0055: the pilot closes winners too
    for p in positions:
        if not eng._own(p.magic):
            continue
        px = tick.bid if p.side > 0 else tick.ask
        if p.side * (px - p.open_price) <= 0 or flat:
            ok = p.position_id not in eng._close_due and \
                eng._try_close(p.position_id, "weekend_flat" if flat else "weekend_close_loser", tick.ts_utc).ok
            left -= ok
            done = done and ok
            continue
        sl = round(p.open_price + 0.5 * (px - p.open_price), 2)
        tr = eng.open.get(p.position_id)
        if tr is not None and tr.sl is not None and p.side * (tr.sl - sl) > 0:
            sl = tr.sl                    # the engine's stop is already tighter (e.g. a rejected trail modify)
        if p.sl is None or p.side * (sl - p.sl) > 0:
            if tr is not None:
                tr.sl = sl                # never loosens; reconciliation and the stop check enforce it meanwhile
            try:
                res = eng.broker.modify(p.position_id, sl, p.tp)
            except Exception as e:        # one position never stops the others
                log.exception("weekend tighten failed for position %s; retried on the next refresh", p.position_id)
                res = OrderResult(ok=False, retcode=-1, order_id=None, position_id=p.position_id, filled_lots=0.0,
                                  price=None, message=repr(e))
            eng.decisions.append({"ts": time.time(), "action": "weekend_tighten", "position": p.position_id, "sl": sl,
                                  "ok": res.ok, "retcode": res.retcode})
            done = done and res.ok
    eng.state.open_positions = left
    return done


def step(eng: Engine, now: pd.Timestamp, ln: Lane) -> None:
    """At an account refresh or a no-quote pass: set the entry block and run the flatten once per cut."""
    eng.state.weekend = ln.block
    if ln.flatten and eng._weekend_done != ln.key and flatten(eng, now):
        eng._weekend_done = ln.key
        eng._save_risk_state_now("the weekend lane's flatten")
