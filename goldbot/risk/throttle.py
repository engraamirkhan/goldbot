"""Per-agent loss-streak and entry-rate throttle (BACKLOG 21, playbook G-6): the facts RiskGate's throttle rule reads.

Playbook G-6: "after 3 consecutive full stop-outs ..., or more than N entries in a session, an agent makes no entries
until [it clears]. Logged and alerted. Never affects exits." The owner's brief fixes the clearing rule: the cooldown
(`risk.throttle.cooldown_bars` bars of the agent's timeframe after its last full loss) passes, or a winning shadow
trade of that agent closes after it; and the rate is counted per risk day (00:00 UTC).

Nothing here is stored on its own: every fact is derived from durable records the engine already keeps, so the
throttle survives a restart without a new state file. Full losses come from the closed-trade record
(state/closed_trades*.jsonl, fsynced per line), entries from that record plus the open book (rebuilt from the broker
and state/orders_<account>.json on restart), shadow wins from state/shadow_book.json. The thresholds and the decision
live in RiskGate (`RiskGate.throttle_reasons`); this module only counts.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Iterable

import pandas as pd

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.data.timeutil import risk_day

if TYPE_CHECKING:                           # gates_phase and shadow import heavier modules; only types are needed
    from goldbot.engine.shadow import ShadowBook
    from goldbot.ops.gates_phase import ClosedTrade

# a full loss: the initial stop was hit. R is net of costs, so a clean stop-out is a little worse than -1R; 0.9 leaves
# room for a stop filled a touch better than its level and for the rounding of lots, and excludes partial losses (a
# time exit or a trail that gave back part of the risk), which end a streak rather than extend it
FULL_LOSS_R = 0.9


class AgentThrottle(FrozenRecord):
    """One agent's throttle facts on one account at `now` (published in engine_<account>.json `throttle.agents`)."""

    agent_id: str
    timeframe: str                                 # the agent's decision timeframe, which the cooldown counts bars of
    loss_streak: int = 0                           # trailing consecutive full losses since its latest shadow win
    last_loss_utc: UtcTimestamp | None = None      # exit time of the last of them (None when the streak is 0)
    last_shadow_win_utc: UtcTimestamp | None = None   # the shadow win that cleared a streak (only looked up when needed)
    entries_today: int = 0                         # positions this agent opened on this account in the risk day of now


def is_full_loss(t: ClosedTrade) -> bool:
    """R <= -FULL_LOSS_R; a record without R (legacy, or no currency conversion) counts when it exited at the stop at a
    loss."""
    if t.r is not None:
        return t.r <= -FULL_LOSS_R
    return t.exit_reason == "stop" and t.pnl < 0


def loss_streak(trades: Iterable[ClosedTrade], after: pd.Timestamp | None = None) -> tuple[int, pd.Timestamp | None]:
    """(trailing consecutive full losses among `trades` exiting after `after`, ordered by exit; the last one's exit).
    Any other trade (a win, a scratch, a partial loss) ends the streak."""
    rows = sorted((t for t in trades if after is None or pd.Timestamp(t.exit_utc) > after),
                  key=lambda t: pd.Timestamp(t.exit_utc))
    n, last = 0, None
    for t in reversed(rows):
        if not is_full_loss(t):
            break
        n += 1
        last = last if last is not None else pd.Timestamp(t.exit_utc)
    return n, last


def entries_on_day(entry_times: Iterable[pd.Timestamp], now: pd.Timestamp) -> int:
    """How many of `entry_times` fall in the risk day (00:00 UTC) of `now`."""
    times = [pd.Timestamp(t) for t in entry_times]
    if not times:
        return 0
    days = risk_day(pd.DatetimeIndex(times + [pd.Timestamp(now)]))
    return int((days[:-1] == days[-1]).sum())


def latest_shadow_win(book: ShadowBook, agent_id: str) -> pd.Timestamp | None:
    """Exit time of the latest winning taken shadow trade of `agent_id` in any version (a candidate the model would not
    have taken proves nothing about the agent's trading)."""
    wins = [pd.Timestamp(t.exit_ts) for b in book.books.values() for t in b.closed
            if t.agent_id == agent_id and t.taken and t.ret is not None and t.ret > 0 and t.exit_ts is not None]
    return max(wins) if wins else None


def agent_throttle(agent_id: str, timeframe: str, *, closed: Iterable[ClosedTrade],
                   entry_times: Iterable[pd.Timestamp], now: pd.Timestamp,
                   shadow_win: Callable[[], pd.Timestamp | None] | None = None,
                   consult_shadow_at: int = 1) -> AgentThrottle:
    """The throttle facts of one agent. `closed` are this account's closed trades (other agents' are skipped);
    `entry_times` this agent's entries (closed and open, one per position). The shadow book is consulted only when the
    streak reaches `consult_shadow_at` (the gate's threshold), so a quiet agent never reads it; a win after the last
    loss ends the streak, and losses after the win start a new one."""
    mine = [t for t in closed if t.agent_id == agent_id]
    n, last = loss_streak(mine)
    win = None
    if n >= consult_shadow_at and shadow_win is not None:
        win = shadow_win()
        if win is not None:
            n, last = loss_streak(mine, after=win)
    return AgentThrottle(agent_id=agent_id, timeframe=timeframe, loss_streak=n, last_loss_utc=last,
                         last_shadow_win_utc=win, entries_today=entries_on_day(entry_times, now))
