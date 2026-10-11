"""Per-agent loss-streak and entry-rate throttle (BACKLOG 21, playbook G-6): the facts RiskGate's throttle rule reads.

Playbook G-6: "after 3 consecutive full stop-outs ..., or more than N entries in a session, an agent makes no entries
until [it clears]. Logged and alerted. Never affects exits." The owner's brief fixes the clearing rule: the cooldown
(`risk.throttle.cooldown_bars` bars of the agent's timeframe after its last full loss) passes, or a real shadow win
(target hit or >= SHADOW_WIN_R) of the agent's current live model, entered after that loss, closes; the count
persists past the cooldown (one more full loss re-blocks) until such a win. The rate is counted per risk day
(00:00 UTC). Both are counted per agent per account and checked at every entry decision.

Nothing here is stored on its own: every fact is derived from durable records the engine already keeps, so the
throttle survives a restart without a new state file. Full losses come from the closed-trade record
(state/closed_trades*.jsonl, fsynced per line), entries from that record plus the open book (rebuilt from the broker
and state/orders_<account>.json on restart), shadow wins from state/shadow_book.json. A close the engine has seen
but not yet recorded counts as a provisional full loss (runner `_agent_throttle`), so the loss just taken is counted
at the very next decision and a failed record write fails closed. The thresholds and the decision live in RiskGate
(`RiskGate.throttle_reasons`); this module only counts.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Iterable

import pandas as pd

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.data.timeutil import risk_day

if TYPE_CHECKING:                           # gates_phase and shadow import heavier modules; only types are needed
    from goldbot.engine.shadow import ShadowBook, ShadowTrade
    from goldbot.ops.gates_phase import ClosedTrade

# a full loss: the initial stop was hit. R is net of costs, so a clean stop-out is a little worse than -1R; 0.9 leaves
# room for a stop filled a touch better than its level and for the rounding of lots, and excludes partial losses (a
# time exit or a trail that gave back part of the risk), which end a streak rather than extend it
FULL_LOSS_R = 0.9
# a shadow trade clears a streak only as a real win: its target hit, or at least this many R (never a scratch)
SHADOW_WIN_R = 0.25
# exit reasons that mean the stop (or the broker's margin stop-out) closed the trade, for records without R
STOP_REASONS = ("stop", "stop_out")


class AgentThrottle(FrozenRecord):
    """One agent's throttle facts on one account at `now` (published in engine_<account>.json `throttle.agents`)."""

    agent_id: str
    timeframe: str                                 # the agent's decision timeframe, which the cooldown counts bars of
    loss_streak: int = 0                           # trailing consecutive full losses since its latest qualifying win
    last_loss_utc: UtcTimestamp | None = None      # exit time of the last of them (None when the streak is 0)
    last_shadow_win_utc: UtcTimestamp | None = None   # exit of the shadow win that cleared losses (looked up when needed)
    entries_today: int = 0                         # positions this agent opened on this account in the risk day of now
    provisional_losses: int = 0                    # closes not yet in the record, counted as full losses (fail closed)
    unknown: bool = False                          # the facts could not be derived: RiskGate refuses `throttle_unknown`


def is_full_loss(t: ClosedTrade) -> bool:
    """R <= -FULL_LOSS_R. A record without R (a legacy line, or a non-USD account the broker gave no conversion for)
    counts when the stop or the broker's stop-out closed it at a loss. A `broker_close` loss without R is not counted:
    the record carries no initial stop, so whether it lost the full risk cannot be measured (the engine's deal reader
    maps the broker's SL/TP/stop-out reasons, so `broker_close` is only left on deals that report none)."""
    if t.r is not None:
        return t.r <= -FULL_LOSS_R
    return t.exit_reason in STOP_REASONS and t.pnl < 0


def loss_streak(trades: Iterable[ClosedTrade], since: pd.Timestamp | None = None) -> tuple[int, pd.Timestamp | None]:
    """(trailing consecutive full losses among `trades` exiting at or after `since`, ordered by exit; the last one's
    exit). Any other trade (a win, a scratch, a partial loss) ends the streak."""
    rows = sorted((t for t in trades if since is None or pd.Timestamp(t.exit_utc) >= since),
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


def shadow_r(t: ShadowTrade) -> float | None:
    """A closed shadow trade's outcome in R (its return over the initial stop distance); None when unknown."""
    if t.ret is None or t.entry == t.stop:
        return None
    return float(t.ret * t.entry / abs(t.entry - t.stop))


def latest_shadow_win(book: ShadowBook, agent_id: str, version: str | None) -> ShadowTrade | None:
    """The qualifying shadow win of `agent_id` that entered last: a closed, taken trade of the agent's current live model
    `version` (a challenger's or an old champion's paper record says nothing about the model now trading) that hit its
    target or made at least SHADOW_WIN_R. None without a live version. It clears the losses that exited before its
    signal bar opened (`agent_throttle`)."""
    if version is None or version not in book.books:
        return None
    wins = [t for t in book.books[version].closed
            if t.agent_id == agent_id and t.taken and t.exit_ts is not None
            and (t.barrier == "target" or (shadow_r(t) or 0.0) >= SHADOW_WIN_R)]
    return max(wins, key=lambda t: pd.Timestamp(t.entry_ts)) if wins else None


def agent_throttle(agent_id: str, timeframe: str, *, closed: Iterable[ClosedTrade],
                   entry_times: Iterable[pd.Timestamp], now: pd.Timestamp,
                   shadow_win: Callable[[], ShadowTrade | None] | None = None,
                   consult_shadow_at: int = 1, cooldown: pd.Timedelta | None = None,
                   provisional_losses: int = 0) -> AgentThrottle:
    """The throttle facts of one agent. `closed` are this account's closed trades (other agents' are skipped), including
    any provisional full losses the caller added for closes not yet recorded (counted in `provisional_losses`);
    `entry_times` this agent's entries (closed and open, one per position). The shadow book is consulted only while it
    can matter: the streak has reached `consult_shadow_at` (the gate's threshold) and its `cooldown` has not passed, so
    a quiet agent never reads it. A qualifying win (`latest_shadow_win`) clears the losses that exited before its entry
    (entry_ts > last loss); losses after it start a new streak. After the cooldown the count persists, so one more full
    loss re-blocks, until a qualifying win."""
    mine = [t for t in closed if t.agent_id == agent_id]
    n, last = loss_streak(mine)
    win_ts = None
    cooling = cooldown is None or last is None or now < last + cooldown
    if n >= consult_shadow_at and cooling and shadow_win is not None:
        win = shadow_win()
        if win is not None:
            n, last = loss_streak(mine, since=pd.Timestamp(win.entry_ts))
            win_ts = pd.Timestamp(win.exit_ts) if win.exit_ts is not None else None
    return AgentThrottle(agent_id=agent_id, timeframe=timeframe, loss_streak=n, last_loss_utc=last,
                         last_shadow_win_utc=win_ts, entries_today=entries_on_day(entry_times, now),
                         provisional_losses=provisional_losses)
