"""The structure replica for the live engine's decision frame, SHADOW ONLY (kept out of runner.py: ADR 0023).

A specialist that reads the replica (`replica_params`, e.g. `smc_structure`) cannot decide on the engine's 800-bar tail:
the replica carries unbounded memory, so a tail gives other values than the series (`features.smc_live`). `ReplicaShadow`
gives such an agent the full-history value instead: `ReplicaFeed.update` carries the state in aux.db (resume after a
restart, rebuild from SERIES_START on a gap, a revised bar, a bar_index jump or stale code), and the agent's rule runs
on the window resumed from the snapshot `state_lag` bars before the decision bar, exactly as research computes it.

Shadow only (trading-safety rule, like `requires_tv`): `Specialist.shadow_only` is true for such an agent, so the engine
gives it share 0 and the shadow book alone trades it; no entry reaches the RiskGate. Fail closed: with no source, a
tail too short, a state not kept (after a rebuild only the latest snapshot is kept, so the next `state_lag` bars have
none) or any error, the agent proposes nothing this bar (logged); nothing here raises, so a bar close and every exit are
unaffected. The price_action features have no state path yet (research only), so nothing here serves them.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import pandas as pd

from goldbot.data.timeutil import utc_index
from goldbot.db.connection import Database
from goldbot.features import smc_replica as R
from goldbot.features.smc_live import STATE_KEY, ReplicaFeed
from goldbot.specialists.base import Specialist

log = logging.getLogger(__name__)


def _empty() -> pd.DataFrame:
    return pd.DataFrame({"idx": pd.Series(dtype=int), "side": pd.Series(dtype=int)})


class ReplicaShadow:
    """Candidates of replica-reading agents from the carried state. `series(tf)`: the mid bars of the decision timeframe
    from SERIES_START through the last closed bar; `bars_before(tf, first)`: the store's count of those bars before
    the bar opening at `first`."""

    def __init__(self, db: Database, series: Callable[[str], pd.DataFrame],
                 bars_before: Callable[[str, pd.Timestamp], int], symbol: str = "xauusd"):
        self.db, self.series, self.bars_before, self.symbol = db, series, bars_before, symbol
        self._feeds: dict[str, ReplicaFeed] = {}

    def feed(self, agent: Any) -> ReplicaFeed:
        params: R.ReplicaParams = agent.replica_params
        name = f"{self.symbol}-{agent.timeframe}-{params.digest()[:8]}"
        if name not in self._feeds:
            self._feeds[name] = ReplicaFeed(self.db, name, params)
        return self._feeds[name]

    def candidates(self, agent: Specialist, fr: Any) -> pd.DataFrame:
        """`agent`'s candidates on the decision frame `fr` (mid bars `fr.m`, features `fr.X`); idx positional in fr.m."""
        try:
            m = fr.m.reset_index(drop=True)
            lag = int(agent.state_lag)                           # type: ignore[attr-defined]
            feed = self.feed(agent)
            if len(m) < R.context_bars(feed.params) + lag:
                return _empty()
            ts = utc_index(m["ts_utc"])
            feed.update(m, self.bars_before(agent.timeframe, ts[0]), lambda: self.series(agent.timeframe))
            state = feed.state_at(ts[-1 - lag])
            if state is None:
                log.info("replica state of %s not kept for %s yet: no candidate this bar", feed.feed, agent.agent_id)
                return _empty()
            return agent.candidates_in_context(m, fr.X, None, {STATE_KEY: state})
        except Exception as exc:                                 # noqa: BLE001 - fail closed: never a failed bar close
            log.warning("replica candidates unavailable for %s (%s: %s): none this bar", agent.agent_id,
                        type(exc).__name__, exc)
            return _empty()
