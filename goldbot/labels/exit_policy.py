"""Exit policies layered on the triple barrier (design: Specialists table, Exit column; Order lifecycle).

A policy is one set of mechanics executed in three places that must agree: the labels the model is trained on
(`triple_barrier(..., policy=)`), the shadow book (`engine/shadow.py`) and the live engine's open-position management
(`engine/runner.py`). The labels and the shadow book both advance a position through `policy_step`, one completed bar
of the agent's timeframe at a time; the live engine applies the same rules with broker `modify`/`close`.

A policy only ever tightens the stop or reduces size; the initial stop and target stay where the barriers put them.
Per bar after entry, in this order (conservative, as the plain barriers):
1. the stop in force since the last bar close (initial, or trailed) is touched -> exit at it ("stop" if it is still
   the initial stop, "trail" once trailed); a stop and anything else in the same bar is the stop;
2. scale-out: the bar reaches entry + side x scale_atr x ATR -> `scale_fraction` of the position exits at that level
   (once);
3. the target is touched -> the rest exits at it ("target");
4. the most favourable price so far (bid high for longs, ask low for shorts) arms the trail once it is
   `trail_after_atr` x ATR beyond entry; the trail stop is that price minus side x trail_atr x ATR and only ever
   tightens; it takes effect from the next bar (live: `modify` at the bar close);
5. hard flat: the bar closes at or after the deadline (`flat_before_min` before the next session open after the
   signal bar's close) -> the rest exits at the close ("flat").
The time barrier (callers) applies after these, at the close of the (max_bars + 1)-th bar.

ATR is the signal bar's, as the barriers use. The return of a scaled position is the size-weighted return of its
two exits.

Known optimistic assumption (gaps): a stop (initial or trailed) is filled AT the stop even when the bar opens
through it; bars carry no open-vs-level ordering, so a gap is indistinguishable from a touch. The broker fills a gap
at the market (the paper broker models this: `min(sl, bid)` for longs), so on a gap the live trade exits on the same
bar as the label but lower by the gap (tests/test_exit_policies.py::test_a_gap_through_the_trail_exits_on_the_label_bar_but_fills_at_the_market).
Levels on the favourable side (scale-out, target) are likewise filled at the level, which on a gap is pessimistic.
Labels and the shadow book keep this rule so they agree with each other; the cost model, not the labels, carries
gap risk.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord, Record, UtcTimestamp


class ExitPolicy(FrozenRecord):
    trail_atr: float | None = Field(None, gt=0)          # trail distance behind the best price, in ATR
    trail_after_atr: float = Field(0.0, ge=0)            # favourable excursion (ATR beyond entry) that arms the trail
    scale_atr: float | None = Field(None, gt=0)          # partial exit level, in ATR beyond entry
    scale_fraction: float = Field(0.5, gt=0, lt=1)       # share of the position closed there
    flat_before_min: int | None = Field(None, gt=0)      # hard flat this many minutes before the next session open
    session_opens: tuple[tuple[str, str], ...] = ()      # (IANA zone, "HH:MM" local) of every session open

    @property
    def active(self) -> bool:
        return self.trail_atr is not None or self.scale_atr is not None or self.flat_before_min is not None

    def scale_level(self, side: int, entry: float, atr: float) -> float | None:
        return None if self.scale_atr is None else entry + side * self.scale_atr * atr

    def trail_stop(self, side: int, entry: float, atr: float, best: float) -> float | None:
        """The trail stop for the most favourable price `best`, or None while the trail is not armed."""
        if self.trail_atr is None or side * (best - entry) < self.trail_after_atr * atr - 1e-12:
            return None
        return best - side * self.trail_atr * atr

    def next_session_open(self, after: pd.Timestamp) -> pd.Timestamp | None:
        """The first session open strictly after `after` (UTC), on a local weekday; None without session opens."""
        after = pd.Timestamp(after).tz_convert("UTC")
        best: pd.Timestamp | None = None
        for tz, hhmm in self.session_opens:
            zone = ZoneInfo(tz)
            hh, mm = (int(x) for x in hhmm.split(":"))
            day0 = after.tz_convert(zone).date()
            for d in range(8):
                day = day0 + timedelta(days=d)
                cand = pd.Timestamp(datetime.combine(day, time(hh, mm), tzinfo=zone)).tz_convert("UTC")
                if day.weekday() < 5 and cand > after:
                    best = cand if best is None or cand < best else best
                    break
        return best

    def flat_deadline(self, after: pd.Timestamp) -> pd.Timestamp | None:
        """Hard-flat time for a position whose signal bar closed at `after`: `flat_before_min` before the next open."""
        if self.flat_before_min is None:
            return None
        nxt = self.next_session_open(after)
        return None if nxt is None else nxt - pd.Timedelta(minutes=self.flat_before_min)


class PolicyState(Record):
    """A position's policy state between bars (persisted in the shadow book)."""
    stop: float                         # the stop in force: the initial one until the trail tightens it
    best: float                         # most favourable exit-side price since entry (entry before the first bar)
    scale_exit: float | None = None     # the scale-out fill, once done
    flat_at: UtcTimestamp | None = None


def new_state(policy: ExitPolicy, *, entry: float, stop: float, signal_close: pd.Timestamp) -> PolicyState:
    return PolicyState(stop=stop, best=entry, flat_at=policy.flat_deadline(signal_close))


def policy_step(policy: ExitPolicy, st: PolicyState, *, side: int, entry: float, atr: float, initial_stop: float,
                target: float, fav: float, adv: float, close: float, close_ts: pd.Timestamp) -> tuple[str, float] | None:
    """Advance one completed bar (fav/adv/close on the exit side: bid for longs, ask for shorts). Returns
    (barrier, exit price) when the rest of the position exits on this bar, else None (mutates `st`)."""
    if side * (adv - st.stop) <= 0:
        return ("stop" if st.stop == initial_stop else "trail"), st.stop
    level = policy.scale_level(side, entry, atr)
    if level is not None and st.scale_exit is None and side * (fav - level) >= 0:
        st.scale_exit = level
    if side * (fav - target) >= 0:
        return "target", target
    if side * (fav - st.best) > 0:
        st.best = fav
    trail = policy.trail_stop(side, entry, atr, st.best)
    if trail is not None and side * (trail - st.stop) > 0:
        st.stop = trail
    if st.flat_at is not None and close_ts >= st.flat_at:
        return "flat", close
    return None


def policy_return(side: int, entry: float, exit_px: float, scale_exit: float | None, fraction: float) -> float:
    """Signed return of the whole position: the scaled share at its fill, the rest at the final exit."""
    rest = side * (exit_px - entry) / entry
    if scale_exit is None:
        return rest
    return fraction * side * (scale_exit - entry) / entry + (1 - fraction) * rest


def policy_label(barrier: str, ret: float) -> int:
    """+1 target, -1 initial stop, else the sign of the realised return (as a plain time exit)."""
    return 1 if barrier == "target" else -1 if barrier == "stop" else int(np.sign(ret))
