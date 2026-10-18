"""The structure replica (`goldbot.features.smc_replica`) computed live on our own bars, never from TradingView (owner
directive 2026-10-18: the replicated indicators are the system's own).

Several pieces of the replica carry unbounded memory (the running FVG threshold, the structure machine, the seeded
averages, the channel book), so a fixed tail of bars does not give the full-history value: on Dukascopy 15m 2010-2025
a fresh 800-bar window differed from the series on 72 of 837 sampled TVR-1 decision bars. Research runs the replica on
the whole series from its pinned start (SERIES_START, prereg B4a). Live, `ReplicaFeed` carries the state instead, as
the quant review of 2026-10-18 requires before any shadow or live use of replica columns:

* "the engine refuses to compute replica features on a plain tail": the feature is tagged `needs_state` and
  `registry.build_features` refuses it unless the frame is declared to start the series (`registry.FULL_HISTORY`);
* "refuse stale state": `ReplicaState` carries the code VERSION and the full parameter hash; a mismatch raises
  `StaleReplicaState` and the feed re-bootstraps;
* "bootstrap state from the 2010 series start, persist in SQLite every bar (no JSON state files), check bar_index
  continuity after restart/gap/revised bar, re-bootstrap on any discontinuity": `update` resumes only when the stored
  snapshot bar is in the tail with `context_bars` bars up to it, its `bar_index` equals the store's count of bars
  before it, and the digest of those context bars is unchanged; otherwise it rebuilds from the series start. Every
  update writes the new state to aux.db (`replica_state`, one row per feed and bar, the last KEEP_STATES kept) before
  it returns; `state_at` gives an earlier bar's snapshot (TVR-1 reads the gaps of the two bars before its decision
  bar, so it resumes from the state fvg_lookback + 1 bars back: `SmcStructureSpecialist.state_lag`).

The digest covers the context window (`context_bars`, 312 bars with the defaults). A revision further back with the
same bar count is not seen by it; the store's data-quality checks own revisions of old bars, and a re-bootstrap
(a new feed name, or deleting its row) rebuilds from scratch.

The engine calls `update` on each closed decision bar with its tail, the store's bar count before the tail and a loader
of the series from SERIES_START, through `engine.replica_context.ReplicaShadow` (shadow only; the host supplies the
loader).
"""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd

from goldbot.base import Record
from goldbot.data.timeutil import epoch_ns, utc_index
from goldbot.db.connection import Database, now_ns
from goldbot.features import smc_replica as R

log = logging.getLogger(__name__)

SERIES_START = pd.Timestamp("2010-01-01", tz="UTC")     # B4a: the replica's state starts at the 2010 series' first bar
START_SLACK = pd.Timedelta(days=7)                       # the first 2010 bar is the first Sunday open (3 January)
STATE_KEY = "smc_replica_state"                          # ctx key: a ReplicaState of a bar inside a window
KEEP_STATES = 16                                         # snapshots kept per feed (the latest and the bars before it)


def check_series_start(bars: pd.DataFrame) -> None:
    """ValueError unless `bars` start at the pinned SERIES_START (within START_SLACK)."""
    first = utc_index(bars["ts_utc"])[0] if len(bars) else None
    if first is None or not SERIES_START <= first < SERIES_START + START_SLACK:
        raise ValueError(f"the replica's state is pinned to a series starting {SERIES_START:%Y-%m-%d} (B4a); these "
                         f"bars start {first:%Y-%m-%d}" if first is not None else "no bars")


def context_digest(bars: pd.DataFrame) -> str:
    """Digest of the bars' times and every input the replica reads (open, high, low, close, volume)."""
    vol = bars["tick_count"] if "tick_count" in bars.columns else bars["volume"]
    parts = [epoch_ns(utc_index(bars["ts_utc"])).astype(np.int64).tobytes()]
    parts += [bars[c].to_numpy(dtype=float).tobytes() for c in ("open", "high", "low", "close")]
    parts.append(vol.to_numpy(dtype=float).tobytes())
    return hashlib.sha1(b"".join(parts)).hexdigest()


class FeedUpdate(Record):
    frame: pd.DataFrame            # replica rows for the tail's bars after the previous snapshot (index: tail position)
    state: R.ReplicaState
    bootstrapped: bool
    reason: str | None = None      # why it re-bootstrapped (None: resumed)


class ReplicaFeed:
    """One persisted replica state per feed (e.g. "xauusd-15m") in aux.db's `replica_state`."""

    def __init__(self, db: Database, feed: str, params: R.ReplicaParams = R.DEFAULT_PARAMS):
        self.db, self.feed, self.params = db, feed, params

    def stored(self) -> dict[str, Any] | None:
        """The latest snapshot's row (None: no state)."""
        with self.db.connection() as conn:
            row = conn.execute("SELECT code_version, params_hash, bar_index, bar_time, context_digest, state "
                               "FROM replica_state WHERE feed = ? ORDER BY bar_index DESC LIMIT 1",
                               (self.feed,)).fetchone()
        if row is None:
            return None
        keys = ("code_version", "params_hash", "bar_index", "bar_time", "context_digest", "state")
        return dict(zip(keys, row))

    def state_at(self, bar_time: pd.Timestamp) -> R.ReplicaState | None:
        """The kept snapshot after the bar opening at `bar_time` (None: not kept, or written by other code)."""
        with self.db.connection() as conn:
            row = conn.execute("SELECT state FROM replica_state WHERE feed = ? AND bar_time = ?",
                               (self.feed, pd.Timestamp(bar_time).isoformat())).fetchone()
        if row is None:
            return None
        try:
            state = R.state_from_json(row[0])
        except ValueError:
            return None
        return state if state.code_version == R.VERSION and state.params_hash == self.params.digest() else None

    def _resume_point(self, tail: pd.DataFrame, bars_before_tail: int) -> tuple[R.ReplicaState | None, str | None]:
        """(the stored state when the tail continues it, else None and the reason)."""
        row = self.stored()
        if row is None:
            return None, "no state"
        try:
            state = R.state_from_json(row["state"])
        except ValueError as exc:                    # pydantic's ValidationError is a ValueError: an older state
            return None, f"unreadable state ({type(exc).__name__})"
        if state.code_version != R.VERSION or state.params_hash != self.params.digest():
            return None, "stale state (code version or parameters)"
        ts = utc_index(tail["ts_utc"])
        hit = np.flatnonzero(ts == pd.Timestamp(state.bar_time))
        if not len(hit):
            return None, "snapshot bar not in the tail (gap)"
        k = int(hit[0])
        cb = R.context_bars(self.params)
        if k + 1 < cb:
            return None, "too few context bars before the snapshot"
        if bars_before_tail + k != state.bar_index:
            return None, f"bar_index discontinuity ({bars_before_tail + k} != {state.bar_index})"
        if context_digest(tail.iloc[k + 1 - cb:k + 1]) != row["context_digest"]:
            return None, "a bar up to the snapshot was revised"
        return state, None

    def update(self, tail: pd.DataFrame, bars_before_tail: int, series: Callable[[], pd.DataFrame]) -> FeedUpdate:
        """Replica rows for the tail's new bars, resumed from the stored state or rebuilt from `series()` (the mid
        bars from SERIES_START through the tail's last bar). The new state is persisted before returning."""
        tail = tail.reset_index(drop=True)
        cb = R.context_bars(self.params)
        if len(tail) < cb:
            raise ValueError(f"the tail must hold at least {cb} bars, got {len(tail)}")
        state, reason = self._resume_point(tail, bars_before_tail)
        if state is not None:
            frame, new = R.replica_run(tail, self.params, state)
            update = FeedUpdate(frame=frame, state=new, bootstrapped=False)
        else:
            update = self._bootstrap(tail, series(), reason or "")
        self._persist(update.state, context_digest(tail.iloc[-cb:]), fresh=update.bootstrapped)
        return update

    def _bootstrap(self, tail: pd.DataFrame, full_series: pd.DataFrame, reason: str) -> FeedUpdate:
        log.info("replica feed %s: re-bootstrap from the series start (%s)", self.feed, reason)
        check_series_start(full_series)
        full_series = full_series.reset_index(drop=True)
        ts_tail, ts_full = utc_index(tail["ts_utc"]), utc_index(full_series["ts_utc"])
        if ts_full[-1] != ts_tail[-1]:
            raise ValueError("the series must end at the tail's last bar")
        frame, new = R.replica_run(full_series, self.params)
        old = self.stored()                          # rows after the last snapshot served are the new ones
        after = pd.Timestamp(old["bar_time"]) if old and old["bar_time"] else None
        pos = np.flatnonzero(np.isin(epoch_ns(ts_full), epoch_ns(ts_tail)))
        rows = frame.iloc[pos].copy()
        rows.index = pd.Index(np.searchsorted(epoch_ns(ts_tail), epoch_ns(ts_full[pos])))
        if after is not None:
            rows = rows[np.asarray(ts_tail[rows.index] > after)]
        return FeedUpdate(frame=rows, state=new, bootstrapped=True, reason=reason)

    def _persist(self, state: R.ReplicaState, digest: str, *, fresh: bool) -> None:
        """Write the snapshot in one transaction: after a bootstrap every older row of the feed goes (they belong to
        the discontinued history); otherwise rows beyond the last KEEP_STATES bars, and any at or after this bar."""
        with self.db.write() as conn:
            if fresh:
                conn.execute("DELETE FROM replica_state WHERE feed = ?", (self.feed,))
            conn.execute("DELETE FROM replica_state WHERE feed = ? AND (bar_index >= ? OR bar_index <= ?)",
                         (self.feed, state.bar_index, state.bar_index - KEEP_STATES))
            conn.execute(
                "INSERT INTO replica_state (feed, code_version, params_hash, bar_index, bar_time, context_digest, "
                "state, updated_ns) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (self.feed, state.code_version, state.params_hash, state.bar_index, state.bar_time, digest,
                 R.state_json(state), now_ns()))
