"""Open trades age at each base close, including the closes missed while the engine was down (backlog 161; the count
moved out of `Engine._manage_open`, ADR 0023 runner split).

Design rule (`Engine.on_bar_close`): "position management stays on the base clock". A trade's label counts every bar
of the market, so a close the engine never saw (a restart, a deploy, a crash) still belongs to the hold:

* each open trade carries `bars_through`, the last base close counted in its `bars_held`, in the same orders-file
  record, so a save at any moment never splits the two. A close at or before it is never counted again (a restart
  whose first quote is stamped before a close the previous engine processed re-closes that bar);
* at the first base close this engine counts, a trade is credited the closes strictly between its `bars_through` and
  that close whose bar the market was open for: a base bar counts when any of its minutes is in
  `SessionTable.expected_open`, the engine's session table (D13: the broker's session windows when loaded, else the
  static rule on the server clock; ADR 0062 always-open tables keep their pauses) less the exchange holidays, the
  calendar the history gap guard uses. A weekend, the daily break or a holiday never counts;
* at that close its time exit fires when the credit makes it due, and an exit policy's hard flat fires there too
  (engine/below_floor.py's rule), above or below the 400-bar floor, when the policy timeframe's close at or after the
  deadline (the close its label flattens at) fell inside the downtime; one still ahead is left to that close;
* the credit is logged per trade and summed in `downtime_bars` (orders file, then the closed-trade record): such a
  trade's time exit or hard flat comes at the restart close, not at its label's deadline (quant review Q-2).

Exits only get earlier than before, never later. A trade with no `bars_through` (saved before it existed: the first
deploy; adopted at the broker) counts from its next close, as before. A session table that cannot answer credits
nothing (logged); a downtime longer than CATCH_UP_DAYS is credited its first CATCH_UP_DAYS. Closes this engine skipped
while running (a failed positions read, a bar no tick fell in) are not credited: that is unchanged. Automatic, never
gated."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from goldbot.config import tf_seconds
from goldbot.data.calendar import SessionTable
from goldbot.engine import below_floor

if TYPE_CHECKING:
    from goldbot.engine.runner import Engine
    from goldbot.engine.store import OpenTrade

log = logging.getLogger(__name__)

CATCH_UP_DAYS = 92      # bounds the one-off minute scan of a stale orders file; any hold ends well inside it


def missed_closes(sessions: SessionTable, sec: int, after: pd.Timestamp, before: pd.Timestamp) -> int:
    """Base closes strictly between the closes `after` and `before` whose `sec`-second bar has a minute the market
    was open for (`SessionTable.expected_open`); 0 when the table cannot answer."""
    n = min(int((before - after).total_seconds() // sec) - 1, CATCH_UP_DAYS * 86400 // sec)
    if n <= 0:
        return 0
    per = max(sec // 60, 1)
    try:
        minutes = pd.date_range(after, periods=n * per, freq="60s")
        open_ = np.asarray(sessions.expected_open(minutes), dtype=bool).reshape(n, per)
    except Exception as exc:                          # noqa: BLE001 - a broken table never closes a trade
        log.warning("session table unusable for the downtime catch-up (%s): credited nothing", type(exc).__name__)
        return 0
    return int(open_.any(axis=1).sum())


def age(eng: Engine, live: set[int]) -> None:
    """One base close for every open trade the broker still holds: `bars_held` + 1, plus at this engine's first
    counted close the closes missed while it was down (logged, and summed in `downtime_bars`); then the due time exits
    and, at that first close, the hard flats whose policy close was missed."""
    close = eng._closed_through
    if eng._first_counted is None:
        eng._first_counted = close
    first = eng._first_counted
    sec = tf_seconds(eng.cfg.decision_tf)
    before = {pid: tr.bars_through for pid, tr in eng.open.items()} if close is not None and close == first else {}
    for pid in list(eng.open):
        if pid not in live:
            continue                                  # waiting for its exit deal (_sweep_closed)
        tr = eng.open[pid]
        if close is None or tr.bars_through is None or close > tr.bars_through:   # never a close counted already
            if close is not None and first is not None and tr.bars_through is not None and tr.bars_through < first:
                n = missed_closes(eng.sessions, sec, tr.bars_through, min(first, close))
                if n:
                    log.info("position %s credited %d base bar(s) closed while the engine was down (%s to %s)",
                             pid, n, tr.bars_through, close)
                tr.bars_held += n
                tr.downtime_bars += n
            tr.bars_held += 1
            tr.bars_through = close
        if tr.bars_held >= tr.max_bars and pid not in eng._close_due:
            eng._try_close(pid, "time_exit", None)    # forgotten only once the close succeeds
    if close is not None and close == first:
        missed = {pid for pid, tr in eng.open.items() if _flat_close_missed(tr, before.get(pid), close)}
        below_floor.hard_flats(eng, close, live & missed)


def _flat_close_missed(tr: OpenTrade, counted: pd.Timestamp | None, first: pd.Timestamp) -> bool:
    """Safety review S-b: the label flattens at the first close of the policy's own timeframe at or after `flat_at`.
    Only when that close fell between the last close counted before the restart and this engine's first close was it
    missed; otherwise (still ahead, seen before the restart, unknown: no `bars_through`, or a 1d policy, whose close is
    the feature day's) the normal path flattens it at that close."""
    if tr.policy is None or tr.flat_at is None or counted is None or tr.timeframe in (None, "1d"):
        return False
    tf = tf_seconds(tr.timeframe or "")
    due = pd.Timestamp(-(-int(pd.Timestamp(tr.flat_at).timestamp()) // tf) * tf, unit="s", tz="UTC")
    return counted < due < first
