"""Position management at a base bar close with no decision frame yet (backlog 160, R124; moved out of
`Engine.on_bar_close`, ADR 0023 runner split).

Design rule (`Engine.on_bar_close`): "position management stays on the base clock". Below the 400-bar floor, or
before the first decision frame of a cold start (about 30 h for a 15m arm), a close decides nothing, but:

* every open trade ages: `_manage_open` counts `bars_held` and fires its time exit at this close;
* an exit policy's hard flat fires at the first base close at or after its deadline (review C1): it needs only the
  deadline and the close, never the frame. The trail needs the frame's bars and waits for it, the server stop staying
  the floor;
* the orders file is saved at the close (review C2), so a crash right after it never loses the bar.

Exits only: no entry is decided here. Automatic, never gated."""
from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from goldbot.engine.runner import Engine
    from goldbot.engine.store import OpenTrade


def manage(eng: Engine, complete: pd.DataFrame, close_ts: pd.Timestamp) -> None:
    eng._manage_open(complete)
    if _due(eng, close_ts):
        positions = eng._read_positions("hard flats run at the next bar close; the server stops stay in place")
        hard_flats(eng, close_ts, set() if positions is None else {p.position_id for p in positions})
    eng._save_orders()


def _due(eng: Engine, close_ts: pd.Timestamp) -> list[tuple[int, OpenTrade]]:
    return [(pid, tr) for pid, tr in eng.open.items()
            if tr.policy is not None and tr.flat_at is not None and close_ts >= tr.flat_at and pid not in eng._close_due]


def hard_flats(eng: Engine, close_ts: pd.Timestamp, live: set[int]) -> None:
    """Every exit policy's hard flat whose deadline is at or before `close_ts`, for the positions in `live` (the
    broker's). Also run at the first close after a restart above the floor (engine/downtime.py, backlog 161)."""
    for pid, tr in _due(eng, close_ts):
        if pid in live and pid not in eng._close_due:     # closed at the broker, or a time exit just went out
            eng._policy_close(pid, tr, "hard_flat")
