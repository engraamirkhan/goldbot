"""Stop moves that only tighten: the exit policies' trail and an exploration runner's (`exploration.trail`, H-80 BXR).

Moved out of `goldbot/engine/runner.py` (its line cap, tests/test_engine_structure.py): "move code out, never in".
`Engine._tighten_stop` is a one-line wrapper around `tighten_stop`.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from goldbot.engine.runner import Engine, OpenTrade
    from goldbot.execution.broker import Position


def tighten_stop(eng: Engine, pid: int, tr: OpenTrade, pos: Position, sl: float) -> None:
    """Move one position's stop to `sl` (the caller's new level, already never looser than `tr.sl`). "A policy only
    tightens the stop": when the price is already through `sl` and the broker's stop is not there the trade is closed
    at market (`trail_close`); the broker's stop at or beyond `sl` is the floor and is never loosened; otherwise
    `tr.sl` takes `sl` (reconciliation re-sends it if the modify does not stick) and `modify` sends it. Automatic,
    never gated."""
    tick = eng.broker.last_tick(eng.cfg.symbol)
    broker_looser = pos.sl is None or tr.side * (sl - pos.sl) > 1e-9
    if broker_looser and tr.side * ((tick.bid if tr.side > 0 else tick.ask) - sl) <= 0:
        # the price is through the stop the policy set but the broker's stop is not there (a new level, or a
        # modify that did not stick): close at market, as the labels exit at that stop
        eng._policy_close(pid, tr, "trail_close")
        return
    if pos.sl is not None and tr.side * (sl - pos.sl) <= 1e-9:
        # review MEDIUM-2 (c), defence in depth: the broker's stop is already at or beyond this one (a restart
        # or an adoption left the engine's copy loose): never send a looser stop; the broker's is the floor
        if tr.sl is None or tr.side * (pos.sl - tr.sl) > 1e-9:
            tr.sl = pos.sl
        return
    if sl == tr.sl:
        return
    tr.sl = sl                                    # reconciliation re-sends it if the modify did not stick
    res = eng.broker.modify(pid, sl, pos.tp)
    eng.decisions.append({"ts": time.time(), "agent": tr.agent_id, "action": "trail", "position": pid,
                          "sl": sl, "ok": res.ok, "retcode": res.retcode})
