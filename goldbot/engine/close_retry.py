"""Close retries (live 2026-11-04, vantage-demo: 3,087 closes refused with retcode 10013 on five scheduled cost probes,
about 600 tries over 14.5 min each; each probe had already closed at the broker on its own stop or target).

Design rules (the runner's `_try_close` and `_retry_closes` call these):
- A due close whose position the broker no longer lists (a complete read, or the engine's own symbol) is never sent
  again (`gone`): the trade is recorded at once from the broker's exit deal (reason `broker_close`, as the bar close's
  sweep records it); with no exit deal yet, or an unreadable history, the close stays due without being sent and is
  looked at again after a backoff, and the bar close's sweep confirms it as before. The hold timers skip a due close,
  so nothing re-sends it. A position that shows up again is closed by the next retry.
- A close refused while the position is still open keeps being sent (`refused`): every CLOSE_RETRY_MIN_S for
  CLOSE_FREE_TRIES refusals in a row, then backed off, doubling, with one warning per backed-off refusal. The wait
  is at most CLOSE_RETRY_MAX_S only for a scheduled exit (SCHEDULED_EXITS: a probe's or an arm's hold, a time exit)
  whose position the last quote shows short of the engine's stop; every other close (kill switch, Friday cut, hard
  flat, engine stop, ...) and a scheduled one through the stop wait at most CLOSE_PROTECT_MAX_S (trading-safety
  review 2026-11-04: a refused protective close must not leave the position unprotected for 30 s). Exits are never
  stopped and never gated. A market-closed refusal keeps its own backoff and count and ends the refusals in a row.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from goldbot.engine.runner import Engine
    from goldbot.execution.broker import OrderResult

log = logging.getLogger("goldbot.engine")

CLOSE_FREE_TRIES = 30         # refusals in a row retried every CLOSE_RETRY_MIN_S (a requote clears) before the backoff
CLOSE_RETRY_MAX_S = 30.0      # the longest wait between two tries of a refused scheduled exit
CLOSE_PROTECT_MAX_S = 5.0     # the longest wait between two tries of any other refused close
SCHEDULED_EXITS = frozenset({"cost_probe_hold", "exploration_hold", "time_exit"})


def _wait_s(fails: int, cap: float = CLOSE_RETRY_MAX_S) -> float:
    from goldbot.engine.runner import CLOSE_RETRY_MIN_S
    if fails <= CLOSE_FREE_TRIES:
        return CLOSE_RETRY_MIN_S
    return float(min(CLOSE_RETRY_MIN_S * 2 ** (fails - CLOSE_FREE_TRIES), cap))


def _counts(eng: Engine, key: str) -> dict[int, int]:
    """A per-position count of this engine, pruned to the closes still due."""
    counts: dict[int, int] = {k: v for k, v in eng.__dict__.get(key, {}).items() if k in eng._close_due}
    eng.__dict__[key] = counts
    return counts


def refusals(eng: Engine, pid: int) -> int:
    """The close of `pid` refused this many times in a row (not market-closed)."""
    return int(eng.__dict__.get("_close_fails", {}).get(pid, 0))


def market_closed(eng: Engine, pid: int) -> None:
    """A market-closed refusal ends the refusals in a row (its own backoff counts it)."""
    eng.__dict__.get("_close_fails", {}).pop(pid, None)


def _cap(eng: Engine, pid: int, action: str) -> float:
    """CLOSE_RETRY_MAX_S for a scheduled exit short of the engine's stop on the last quote, else CLOSE_PROTECT_MAX_S
    (also when the stop or the quote is unknown: fail toward the faster retry)."""
    tr, q = eng.open.get(pid), eng._last_tick
    if action not in SCHEDULED_EXITS or tr is None or tr.sl is None or q is None:
        return CLOSE_PROTECT_MAX_S
    px = q.bid if tr.side > 0 else q.ask
    return CLOSE_RETRY_MAX_S if tr.side * (px - tr.sl) > 0 else CLOSE_PROTECT_MAX_S


def refused(eng: Engine, pid: int, now: pd.Timestamp, res: OrderResult, action: str) -> None:
    """A close refused (not market-closed) or raised: tried again CLOSE_RETRY_MIN_S later (`Engine._retry_wait`) for
    CLOSE_FREE_TRIES refusals in a row, then after `_wait_s` up to `_cap` (in the backoff table, whose market-closed
    count is kept)."""
    counts = _counts(eng, "_close_fails")
    fails = counts[pid] = counts.get(pid, 0) + 1
    if fails <= CLOSE_FREE_TRIES:
        eng._close_backoff.pop(pid, None)
        return
    wait = _wait_s(fails, _cap(eng, pid, action))
    eng._close_backoff[pid] = (eng._close_backoff.get(pid, (0, now))[0], now + pd.Timedelta(seconds=wait))
    log.warning("close of position %s refused %d times in a row (retcode %s): next try in %.0f s",
                pid, fails, res.retcode, wait)


def gone(eng: Engine, pid: int, action: str, now: pd.Timestamp) -> None:
    """The due close of a position the broker no longer lists: recorded from its exit deal and forgotten, or (no
    deal yet) kept due, unsent, until the next look after the backoff."""
    tr = eng.open.get(pid)
    if tr is not None:
        try:
            summary, _ = eng._exit_deals(pid, tr)
        except Exception:                   # counted by _read_deals; asked again after the backoff
            summary = None
        if summary is None:
            looks = _counts(eng, "_gone_looks")
            fails = looks[pid] = looks.get(pid, 0) + 1
            eng._close_backoff[pid] = (eng._close_backoff.get(pid, (0, now))[0],
                                       now + pd.Timedelta(seconds=_wait_s(fails + CLOSE_FREE_TRIES, _cap(eng, pid, action))))
            if fails == 1:
                log.info("position %s is gone at the broker, its exit deal not seen yet: %s is not sent again",
                         pid, action)
            return
        eng._unconfirmed.pop(pid, None)
        del eng.open[pid]
        eng._note_closed(pid, tr, "broker_close")
        eng.decisions.append({"ts": time.time(), "agent": tr.agent_id, "action": "close_due_gone", "position": pid,
                              "due": action})
    eng._close_due.pop(pid, None)
    eng._close_backoff.pop(pid, None)
