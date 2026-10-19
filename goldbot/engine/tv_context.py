"""TradingView values for the live engine's TradingView-dependent agents (plan docs/plans/tradingview-strategies.md,
"Live staleness fails closed"), kept out of runner.py (ADR 0023: runner.py only shrinks).

`with_tv` is the frame a `requires_tv` agent's `candidates` sees: the decision frame plus the agent's own signal
column, read from the store's `tv_indicators` by the agent's rule (`TvEventSpecialist.decision_events`: the decision
bar's own TradingView bar, arrived no later than its close + `tv.max_lag_s` and no later than `now`). Every other
agent gets the frame unchanged (the same object). The model's scoring frame is never touched: the signal column
reaches the candidate rule only.

`TvShadowWatch` is how a `requires_tv` agent trades (shadow only; DESIGN: TradingView signals are "filter features
only" until the promotion rule). The bar close is never delayed: at the close (`candidates`) such an agent proposes
nothing to the live path, and the watch re-checks its signal on the ticks that follow (`poll`, at most once a second)
until close + max_lag. The first check that sees the value records the shadow entry at that tick's price, on the
trade's side; past the deadline nothing is recorded. Research decides at close + max_lag and enters at the next 1m
open (`labels.triple_barrier_delayed`), so research and the shadow book enter after the value is known, both.

"A missed alert sets the feature to NaN rather than a stale value" (DESIGN, TradingView signals): a store that is
missing or unreadable, or a rule that fails, reads NaN (logged) and the agent proposes nothing. Nothing here raises,
so a TradingView outage can never stop a bar close, its position management or any exit.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from goldbot.base import Record, UtcTimestamp
from goldbot.data.store import Store
from goldbot.data.timeutil import utc_index
from goldbot.engine.replica_context import ReplicaShadow
from goldbot.engine.shadow import record_candidate
from goldbot.execution.broker import Tick
from goldbot.specialists.base import Specialist
from goldbot.specialists.tv_event import TvEventSpecialist

log = logging.getLogger(__name__)
TABLE = "tv_indicators"
POLL = pd.Timedelta(seconds=1)       # a pending agent is re-checked at most this often (each check reads the store)
LATE = pd.Timedelta(seconds=5)       # a check this long past the deadline records nothing: its price is not the arrival's


def with_tv(agent: Specialist, mid_bars: pd.DataFrame, X: pd.DataFrame, store: Store | None, now: pd.Timestamp,
            max_lag_s: int) -> pd.DataFrame:
    """X for `agent`'s candidates: X itself unless the agent needs TradingView values (see the module docstring)."""
    if not agent.requires_tv:
        return X
    if not isinstance(agent, TvEventSpecialist):        # no reader for this family: no signal column, nothing fires
        return X
    long: pd.DataFrame | None = None
    try:
        if store is not None and len(mid_bars):
            last_open = utc_index(mid_bars["ts_utc"])[-1]
            long = store.read(TABLE, start=last_open - pd.Timedelta(days=1), end=now + pd.Timedelta(seconds=1))
        values = agent.decision_events(long, mid_bars, now=now, max_lag=pd.Timedelta(seconds=max_lag_s))
    except Exception as exc:                            # noqa: BLE001 - fail closed: NaN, never a failed bar close
        log.warning("TradingView values unavailable for %s (%s: %s): no candidate this bar", agent.agent_id,
                    type(exc).__name__, exc)
        values = np.full(len(X), np.nan)
    return X.assign(**{agent.column: values})


class _Pending(Record):
    agent: Any                       # a TvEventSpecialist
    fr: Any                          # the decision frame of its bar close (runner._Frame: dec, m, X)
    tf: str
    deadline: UtcTimestamp           # the bar's close + max_lag
    atr_usd: float
    cost_atr: float
    next_check: UtcTimestamp


def _empty() -> pd.DataFrame:
    return pd.DataFrame({"idx": pd.Series(dtype=int), "side": pd.Series(dtype=int)})


class TvShadowWatch:
    """Shadow entries of `requires_tv` agents between their bar close and close + max_lag (module docstring)."""

    def __init__(self) -> None:
        self._pending: dict[str, _Pending] = {}
        self.replica: ReplicaShadow | None = None      # set by the host (ops.run) to serve replica agents, shadow only

    @property
    def pending(self) -> list[str]:
        return sorted(self._pending)

    def candidates(self, eng: Any, tf: str, fr: Any, agents: list[Specialist], now: pd.Timestamp, tick: Tick,
                   atr_usd: float, cost_atr: float) -> dict[str, pd.DataFrame]:
        """Each agent's candidates at a bar close of `tf`: its own rule on the frame, except a `requires_tv` agent,
        which proposes none to the live path; when the engine runs a shadow book it is watched until its deadline
        (first check now)."""
        out: dict[str, pd.DataFrame] = {}
        for ag in agents:
            if getattr(ag, "replica_params", None) is not None:      # carried state, or nothing (ReplicaShadow)
                out[ag.agent_id] = self.replica.candidates(ag, fr) if self.replica is not None else _empty()
                continue
            if not ag.requires_tv:
                out[ag.agent_id] = ag.candidates(fr.m, fr.X)
                continue
            out[ag.agent_id] = _empty()
            if eng.shadow is not None and isinstance(ag, TvEventSpecialist):
                close = pd.Timestamp(fr.dec["visible_at"].iloc[-1])
                self._pending[ag.agent_id] = _Pending(
                    agent=ag, fr=fr, tf=tf, deadline=close + pd.Timedelta(seconds=eng.cfg.tv_max_lag_s),
                    atr_usd=atr_usd, cost_atr=cost_atr, next_check=now)
        self.poll(eng, now, tick)
        return out

    def poll(self, eng: Any, now: pd.Timestamp, tick: Tick) -> None:
        """Re-check every pending agent (see the class); never raises."""
        for key, p in list(self._pending.items()):
            if now > p.deadline + LATE:
                del self._pending[key]
                continue
            if now < p.next_check:
                continue
            p.next_check = now + POLL
            try:
                X = with_tv(p.agent, p.fr.m, p.fr.X, eng.store, now, eng.cfg.tv_max_lag_s)
                cands = p.agent.candidates(p.fr.m, X)
                last = len(p.fr.dec) - 1
                if not cands.empty and int(cands["idx"].iloc[-1]) == last:
                    side = int(cands["side"].iloc[-1])
                    self._record(eng, p, side, tick.ask if side > 0 else tick.bid, last)
                    del self._pending[key]
                    continue
            except Exception as exc:                    # noqa: BLE001 - shadow bookkeeping never stops a tick
                log.warning("TradingView shadow check failed for %s (%s: %s)", key, type(exc).__name__, exc)
                del self._pending[key]
                continue
            if now >= p.deadline:
                del self._pending[key]

    @staticmethod
    def _record(eng: Any, p: _Pending, side: int, price: float, last: int) -> None:
        for version, (agent_key, model) in eng.shadow_models.items():
            if agent_key not in (p.agent.agent_id, p.agent.family):
                continue
            Xm = eng._scoring_frame(p.fr, model)
            if Xm is None:
                continue          # a model only scores the frame version it was trained on, in shadow too
            record_candidate(eng.shadow, version=version, model=model, agent=p.agent, tf=p.tf, bar=p.fr.dec.iloc[last],
                             Xm=Xm, last=last, side=side, atr_usd=p.atr_usd, cost_atr=p.cost_atr, entry=price)
