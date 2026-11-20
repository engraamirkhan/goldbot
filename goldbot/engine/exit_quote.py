"""The exit quote on a trade's record (finance audit P6, 2026-11-20: "Record exit bid/ask on every exploration exit";
exit slippage was unmeasured on 0 of 811 exits). Two moments set it on the open trade (`OpenTrade.exit_bid`,
`exit_ask`, `exit_quote_utc`), and the exploration ledger copies it at the close (`exploration.record_close`):

* the engine sends a close (`Engine._try_close`): the quote the close was decided on; a retried close overwrites it
  with the quote of the send that succeeds;
* the broker holds the stop or target (`Engine._track_excursions`): the first quote at or through that level, the one
  the broker's own trigger fired on (a gap the engine never quoted leaves it empty: never a quote from before it).

Reporting only: it never moves an exit, and a failure is swallowed (the close is never blocked by its bookkeeping)."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from goldbot.engine.store import OpenTrade
    from goldbot.execution.broker import Tick

log = logging.getLogger("goldbot.engine")


def note(tr: OpenTrade | None, t: Tick | None) -> None:
    """Record `t` as `tr`'s exit quote (both sides and its stamp); nothing without a trade or a quote."""
    if tr is None or t is None:
        return
    try:
        tr.exit_bid, tr.exit_ask, tr.exit_quote_utc = float(t.bid), float(t.ask), t.ts_utc
    except Exception:          # noqa: BLE001 - reporting only: an exit is never blocked by its bookkeeping
        log.warning("exit quote not recorded", exc_info=True)
