"""Shadow trades open across an engine restart are replayed through the bars that closed while it was down (backlog
165; quant review Q-1 of backlog 161).

Design rule (engine/shadow.py): "trade mechanics mirror the triple-barrier labels the models were trained on, so shadow
and backtest are comparable". The shadow book advances only on the bars the engine closes, so a trade open across a
restart missed every bar of the downtime (its count, barriers, policy and excursion), and backlog 161 could only tag it
`restart_gap`. Now:

* at the engine's start each open trade with a resume point (`ShadowTrade.last_bar_ts`, or no bar advanced yet) is
  held (`ShadowBook.__init__`, host): `on_bar` skips it;
* at every base close, before the timeframes advance their trades, a held trade whose missed span the history covers is
  advanced through the completed bars of its own timeframe after its last one, oldest first, exactly as `on_bar` would
  have (`ShadowBook.replay`), then released: the bar this close completes is advanced by the normal path right after.
  Point in time: only bars visible before this close (`visible_at < close_ts`), built from the completed 1m bars the
  close decides on (the same resample as the frames). Covered (`_covered`, quant review C1): no history fill running
  or unabsorbed, the 1m window starts at or before the trade's last bar, and not one expected-open minute is missing
  between it and this close (no tolerance: a missing minute could hold a barrier touch);
* a trade the history has not covered REPLAY_WAIT after the first close tried (the background fill had its chance) is
  released `restart_gap` and advances from the next bar, as before (`ShadowBook.release`).

Shadow only: nothing here reads the broker or sends an order. Never raises into the bar close (logged)."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pandas as pd

from goldbot.engine.history import open_gaps
from goldbot.engine.shadow import ShadowBook, ShadowTrade, minutes_of

if TYPE_CHECKING:
    from goldbot.engine.runner import Engine

log = logging.getLogger(__name__)

REPLAY_WAIT = pd.Timedelta(hours=6)     # a hold the history fill (every 30 min while a hole stays) never covered


def catch_up(eng: Engine, complete: pd.DataFrame, close_ts: pd.Timestamp) -> None:
    """At a base close (`Engine.on_bar_close`, before the timeframes advance their trades): replay every held shadow
    trade the history covers, release the ones it has not covered within REPLAY_WAIT."""
    book = eng.shadow
    if book is None or not (held := book.held()):
        return
    try:
        _catch_up(eng, book, held, complete, close_ts)
    except Exception:                                 # noqa: BLE001 - shadow bookkeeping never stops a bar close
        log.exception("shadow restart replay failed: the held trades are tried again at the next close")


def _catch_up(eng: Engine, book: ShadowBook, held: list[ShadowTrade], complete: pd.DataFrame,
              close_ts: pd.Timestamp) -> None:
    if book.held_since is None:
        book.held_since = close_ts
    ts = pd.DatetimeIndex(complete["ts_utc"]) if len(complete) else pd.DatetimeIndex([], tz="UTC")
    for t in held:
        since = pd.Timestamp(t.last_bar_ts if t.last_bar_ts is not None else t.entry_ts)
        if not _covered(eng, ts, since, close_ts):
            continue                                  # not covered yet: held for a later close
        tf = t.timeframe
        bars = eng._resample(complete, tf)
        bars = bars[(bars["ts_utc"] > since) & (bars["visible_at"] < close_ts)]
        book.replay(t, bars, lambda bar, tf=tf: minutes_of(eng.bars_1m, bar, tf))   # type: ignore[misc]
        log.info("shadow %s %s (%s, entered %s) replayed %d %s bar(s) closed while the engine was down%s", t.version,
                 t.agent_id, tf, t.entry_ts, len(bars), tf, f": exited {t.barrier}" if t.exit is not None else "")
    if close_ts - book.held_since >= REPLAY_WAIT:
        for t in book.held():
            book.release(t)
            log.warning("shadow %s %s (entered %s): the history never covered the bars missed while the engine was "
                        "down; tagged restart_gap", t.version, t.agent_id, t.entry_ts)


def _covered(eng: Engine, ts: pd.DatetimeIndex, since: pd.Timestamp, close_ts: pd.Timestamp) -> bool:
    """Quant review C1: the replay span is whole. No history fill is running or waiting to be absorbed (the tail of a
    short restart arrives through it), the 1m window starts at or before `since`, and from the last minute at or before
    it to `close_ts` not one expected-open minute is missing (`open_gaps` with no tolerance; spans a broker pull found
    without ticks, `History.covered`, are not holes). The entry refusal's tolerance (history_gap_max_min) is not used:
    a minute missing inside a missed bar could hold the touch of a barrier."""
    if eng.history.fill is not None or not len(ts) or ts[0] > since:
        return False
    lo = ts[ts <= since][-1]
    span = pd.DatetimeIndex(ts[(ts >= lo) & (ts < close_ts)].append(pd.DatetimeIndex([close_ts])))
    return not open_gaps(span, eng.sessions, max_open_min=0, covered=eng.history.covered)
