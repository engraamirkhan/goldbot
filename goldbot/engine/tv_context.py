"""TradingView values for the live engine's TradingView-dependent agents (plan docs/plans/tradingview-strategies.md,
"Live staleness fails closed"), kept out of runner.py (ADR 0023: runner.py only shrinks).

`with_tv` is the frame a `requires_tv` agent's `candidates` sees at a bar close: the decision frame plus the agent's
own signal column, read from the store's `tv_indicators` by the agent's rule (`TvEventSpecialist.decision_events`: the
decision bar's own TradingView bar, arrived no later than its close + `tv.max_lag_s` and no later than `now`). Every
other agent gets the frame unchanged (the same object). The model's scoring frame is never touched: the signal column
reaches the candidate rule only.

"A missed alert sets the feature to NaN rather than a stale value" (DESIGN, TradingView signals): a store that is
missing or unreadable, or a rule that fails, reads NaN (logged) and the agent proposes nothing. Nothing here raises,
so a TradingView outage can never stop a bar close, its position management or any exit.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from goldbot.data.store import Store
from goldbot.data.timeutil import utc_index
from goldbot.specialists.base import Specialist
from goldbot.specialists.tv_event import TvEventSpecialist

log = logging.getLogger(__name__)
TABLE = "tv_indicators"


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
