"""Close retries (live 2026-11-04, vantage-demo: 3,087 closes refused with retcode 10013 on five scheduled cost probes,
about 600 tries over 14.5 min each; each probe had already closed at the broker on its own stop or target).

Design rules (the runner's `_try_close` and `_retry_closes` call these):
- A due close whose position the broker no longer lists (a complete read, or the engine's own symbol) is never sent
  again (`gone`): the trade is recorded at once from the broker's exit deal (reason `broker_close`, as the bar close's
  sweep records it); with no exit deal yet, or an unreadable history, the close stays due without being sent and is
  looked at again after a backoff, and the bar close's sweep confirms it as before. The hold timers skip a due close,
  so nothing re-sends it. A position that shows up again is closed by the next retry.
- A close refused while the position is still open keeps being sent (`refused`): every CLOSE_RETRY_MIN_S for
  CLOSE_FREE_TRIES refusals in a row, then backed off, doubling up to CLOSE_RETRY_MAX_S, with one warning per
  backed-off refusal. Exits are never stopped and never gated; a market-closed refusal keeps its own backoff.
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
CLOSE_RETRY_MAX_S = 30.0      # the longest wait between two tries of a refused close of an open position


def _wait_s(fails: int) -> float:
    from goldbot.engine.runner import CLOSE_RETRY_MIN_S
    if fails <= CLOSE_FREE_TRIES:
        return CLOSE_RETRY_MIN_S
    return float(min(CLOSE_RETRY_MIN_S * 2 ** (fails - CLOSE_FREE_TRIES), CLOSE_RETRY_MAX_S))


def refused(eng: Engine, pid: int, now: pd.Timestamp, res: OrderResult) -> None:
    """A close refused (not market-closed) or raised: tried again CLOSE_RETRY_MIN_S later (`Engine._retry_wait`) for
    CLOSE_FREE_TRIES refusals in a row, then after `_wait_s` (the market-closed table, its own count kept)."""
    counts: dict[int, int] = {k: v for k, v in eng.__dict__.get("_close_fails", {}).items() if k in eng._close_due}
    fails = counts[pid] = counts.get(pid, 0) + 1
    eng.__dict__["_close_fails"] = counts
    if fails <= CLOSE_FREE_TRIES:
        eng._close_backoff.pop(pid, None)
        return
    wait = _wait_s(fails)
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
            fails = eng._close_backoff.get(pid, (0, now))[0] + 1
            eng._close_backoff[pid] = (fails, now + pd.Timedelta(seconds=_wait_s(fails + CLOSE_FREE_TRIES)))
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
