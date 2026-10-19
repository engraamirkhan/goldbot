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
import threading
from collections.abc import Callable
from typing import Any, Protocol

import pandas as pd

from goldbot.data.resample import mid
from goldbot.data.store import Store
from goldbot.data.timeutil import utc_index
from goldbot.db.connection import Database
from goldbot.features import smc_replica as R
from goldbot.features.smc_live import SERIES_START, STATE_KEY, ReplicaFeed
from goldbot.specialists.base import Specialist

log = logging.getLogger(__name__)


def _empty() -> pd.DataFrame:
    return pd.DataFrame({"idx": pd.Series(dtype=int), "side": pd.Series(dtype=int)})


class BarHistory(Protocol):
    def before(self, tf: str, first: pd.Timestamp, n: int | None = None) -> pd.DataFrame: ...
    def count(self, tf: str, first: pd.Timestamp) -> int: ...


class StoreHistory:
    """The store's mid bars of a timeframe from the live series start (settings replica.series_start): the host's
    source for a replica feed's bootstrap and for tails shorter than the replica's context. Reads `bars_<tf>`."""

    def __init__(self, store: Store, symbol: str, series_start: pd.Timestamp):
        self.store, self.symbol, self.series_start = store, symbol, series_start

    def before(self, tf: str, first: pd.Timestamp, n: int | None = None) -> pd.DataFrame:
        """Mid bars with series_start <= ts_utc < first (the last `n` when given), oldest first."""
        b = self.store.read(f"bars_{tf}", symbol=self.symbol, start=self.series_start, end=first)
        if b.empty:
            return pd.DataFrame(columns=["ts_utc", "visible_at", "open", "high", "low", "close", "spread",
                                         "tick_count"])
        b = b.sort_values("ts_utc").drop_duplicates("ts_utc", keep="last").reset_index(drop=True)
        out = mid(b)
        return (out.tail(n) if n is not None else out).reset_index(drop=True)

    def count(self, tf: str, first: pd.Timestamp) -> int:
        b = self.store.read(f"bars_{tf}", symbol=self.symbol, start=self.series_start, end=first, columns=["ts_utc"])
        return int(b["ts_utc"].nunique()) if len(b) else 0


class ReplicaShadow:
    """Candidates of replica-reading agents from the carried state. `series(tf)`: the mid bars of the decision timeframe
    from SERIES_START through the last closed bar; `bars_before(tf, first)`: the store's count of those bars before
    the bar opening at `first`."""

    def __init__(self, db: Database, series: Callable[[str], pd.DataFrame] | None = None,
                 bars_before: Callable[[str, pd.Timestamp], int] | None = None, symbol: str = "xauusd",
                 history: BarHistory | None = None, series_start: pd.Timestamp = SERIES_START,
                 warm_wait_s: float = 0.0):
        self.db, self.series, self.bars_before, self.symbol = db, series, bars_before, symbol
        # warm_wait_s: how long a bar close waits for a bootstrap it started (production 0: never; tests wait)
        self.history, self.series_start, self.warm_wait_s = history, series_start, warm_wait_s
        self._feeds: dict[str, ReplicaFeed] = {}
        self._warming: dict[str, threading.Thread] = {}      # feed -> its background bootstrap

    def feed(self, agent: Any) -> ReplicaFeed:
        params: R.ReplicaParams = agent.replica_params
        name = f"{self.symbol}-{agent.timeframe}-{params.digest()[:8]}"
        if name not in self._feeds:
            self._feeds[name] = ReplicaFeed(self.db, name, params, self.series_start)
        return self._feeds[name]

    def _ready(self, feed: ReplicaFeed, m: pd.DataFrame, before: int, series: Callable[[], pd.DataFrame]) -> bool:
        """True when the stored state continues on this tail (the bar's update is a cheap resume). Otherwise a
        bootstrap is due: it runs on a background thread (never in the engine loop: a store read and a replica run
        over years of bars), one per feed at a time, and the agent proposes nothing until a later bar resumes it."""
        running = self._warming.get(feed.feed)
        if running is not None and running.is_alive():
            return False
        state, _ = feed._resume_point(m.reset_index(drop=True), before)
        if state is not None:
            return True
        tail = m.copy()

        def work() -> None:
            try:
                feed.update(tail, before, series)
            except Exception as exc:                         # noqa: BLE001 - logged; the next bar retries
                log.warning("replica bootstrap of %s failed (%s: %s)", feed.feed, type(exc).__name__, exc)
        th = threading.Thread(target=work, name=f"replica-{feed.feed}", daemon=True)
        self._warming[feed.feed] = th
        th.start()
        if self.warm_wait_s > 0:
            th.join(self.warm_wait_s)
        return False

    def candidates(self, agent: Specialist, fr: Any) -> pd.DataFrame:
        """`agent`'s candidates on the decision frame `fr` (mid bars `fr.m`, features `fr.X`); idx positional in fr.m.
        With a store `history`, a tail shorter than the replica's context (4h: the engine keeps ~40 days of 1m bars)
        is extended with the store's earlier bars, and the bootstrap series is the store's bars before the tail plus
        the tail itself."""
        try:
            m = fr.m.reset_index(drop=True)
            X = fr.X
            tf = agent.timeframe
            lag = int(agent.state_lag)                           # type: ignore[attr-defined]
            feed = self.feed(agent)
            need = R.context_bars(feed.params) + lag
            offset = 0
            if len(m) < need and self.history is not None and len(m):
                pre = self.history.before(tf, utc_index(m["ts_utc"])[0], need - len(m))
                offset = len(pre)
                m = pd.concat([pre, m], ignore_index=True)
                X = X.reset_index(drop=True).reindex(range(-offset, len(X))).reset_index(drop=True)
            if len(m) < need:
                return _empty()
            ts = utc_index(m["ts_utc"])
            if self.history is not None:
                hist = self.history
                before, series = hist.count(tf, ts[0]), (lambda: pd.concat([hist.before(tf, ts[0]), m],
                                                                           ignore_index=True))
            elif self.series is not None and self.bars_before is not None:
                src, count = self.series, self.bars_before
                before, series = count(tf, ts[0]), (lambda: src(tf))
            else:
                return _empty()
            if not self._ready(feed, m, before, series):
                return _empty()
            feed.update(m, before, series)
            state = feed.state_at(ts[-1 - lag])
            if state is None:
                log.info("replica state of %s not kept for %s yet: no candidate this bar", feed.feed, agent.agent_id)
                return _empty()
            out = agent.candidates_in_context(m, X, None, {STATE_KEY: state})
            if offset:
                out = out.assign(idx=out["idx"] - offset)
                out = out[out["idx"] >= 0].reset_index(drop=True)
            return out
        except Exception as exc:                                 # noqa: BLE001 - fail closed: never a failed bar close
            log.warning("replica candidates unavailable for %s (%s: %s): none this bar", agent.agent_id,
                        type(exc).__name__, exc)
            return _empty()
