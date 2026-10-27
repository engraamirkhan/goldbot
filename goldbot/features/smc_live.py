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
  continuity after restart/gap/revised bar, re-bootstrap on any discontinuity": `update` resumes from the newest kept
  snapshot whose bar is in the tail with `context_bars` bars up to it and whose digest of those context bars is
  unchanged; when none is, it rebuilds from the series start (logged with the reason). Every update writes the
  snapshot of each of the last KEEP_STATES bars it ran to aux.db (`replica_state`, one row per feed and bar) before
  it returns; `state_at` gives an earlier bar's snapshot (TVR-1 reads the gaps of the two bars before its decision
  bar, so it resumes from the state fvg_lookback + 1 bars back: `SmcStructureSpecialist.state_lag`).

Revisions and counts (SRE 2026-10-27: 61 rebuilds of ~60 CPU-s each on the brain's 2 OCPU, at the quarter-hour closes
when orders go out). A bar that came back corrected after its close (the broker's M1 bar replacing the one built from
ticks) changes the newest snapshots' digests only: the update resumes from the newest snapshot before it and runs the
corrected bar again, so it equals a fresh rebuild. The store's count of bars before the tail is not a continuity
check: it mixes the store's release bars before the engine's window with the broker's bars in it, so it moves by a bar
whenever the window's start passes a bar one source has and the other has not, while the replica's FVG threshold is
its own running sum over its own count (`smc_replica._fvg_run`), consistent whatever the store says. A drift is
logged; the state keeps its own count. The digest covers the context window (`context_bars`, 312 bars with the
defaults): a revision inside every kept snapshot's context rebuilds; one older than the context is not seen (the
store's data-quality checks own revisions of old bars, and a new feed name or deleting its rows rebuilds).

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


def check_series_start(bars: pd.DataFrame, start: pd.Timestamp = SERIES_START) -> None:
    """ValueError unless `bars` start at the pinned `start` (default SERIES_START, research) within START_SLACK."""
    first = utc_index(bars["ts_utc"])[0] if len(bars) else None
    if first is None or not start <= first < start + START_SLACK:
        raise ValueError(f"the replica's state is pinned to a series starting {start:%Y-%m-%d} (B4a); these "
                         f"bars start {first:%Y-%m-%d}" if first is not None else "no bars")


def context_digest(bars: pd.DataFrame) -> str:
    """Digest of the bars' times and every input the replica reads (open, high, low, close, volume)."""
    vol = bars["tick_count"] if "tick_count" in bars.columns else bars["volume"]
    parts = [epoch_ns(utc_index(bars["ts_utc"])).astype(np.int64).tobytes()]
    parts += [bars[c].to_numpy(dtype=float).tobytes() for c in ("open", "high", "low", "close")]
    parts.append(vol.to_numpy(dtype=float).tobytes())
    return hashlib.sha1(b"".join(parts)).hexdigest()


class FeedUpdate(Record):
    frame: pd.DataFrame            # replica rows for the tail's bars after the snapshot resumed from (index: position)
    state: R.ReplicaState
    bootstrapped: bool
    reason: str | None = None      # why it rebuilt from the series start (None: resumed)


class ReplicaFeed:
    """One persisted replica state per feed (e.g. "xauusd-15m") in aux.db's `replica_state`."""

    def __init__(self, db: Database, feed: str, params: R.ReplicaParams = R.DEFAULT_PARAMS,
                 series_start: pd.Timestamp = SERIES_START):
        # the live host pins the store's own first year (settings replica.series_start); research keeps SERIES_START
        self.db, self.feed, self.params, self.series_start = db, feed, params, series_start
        self._drift = 0                              # the store's count minus the state's, last seen (logged on change)

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

    def _kept(self) -> list[tuple[str, str]]:
        """(state JSON, context digest) of the feed's kept snapshots, newest first."""
        with self.db.connection() as conn:
            return [(str(r[0]), str(r[1])) for r in conn.execute(
                "SELECT state, context_digest FROM replica_state WHERE feed = ? ORDER BY bar_index DESC", (self.feed,))]

    def _resume_point(self, tail: pd.DataFrame,
                      bars_before_tail: int) -> tuple[R.ReplicaState | None, int, str | None]:
        """(the newest kept state the tail continues, its position in the tail, None), else (None, -1, why the newest
        snapshot does not continue it). A drift of the store's count from the state's is logged, never a rebuild."""
        kept = self._kept()
        if not kept:
            return None, -1, "no state"
        ts = utc_index(tail["ts_utc"])
        why: str | None = None
        for text, digest in kept:
            state, k, reason = self._continues(text, digest, tail, ts)
            if state is not None:
                drift = bars_before_tail + k - state.bar_index
                if drift != self._drift:
                    log.info("replica feed %s: store bar count drift %+d at %s; resumed on the state's own count",
                             self.feed, drift, state.bar_time)
                    self._drift = drift
                return state, k, None
            why = why or reason
        return None, -1, why

    def _continues(self, text: str, digest: str, tail: pd.DataFrame,
                   ts: pd.DatetimeIndex) -> tuple[R.ReplicaState | None, int, str]:
        """(a kept snapshot, its position in the tail, "") when the tail continues it, else (None, -1, why not)."""
        try:
            state = R.state_from_json(text)
        except ValueError as exc:                    # pydantic's ValidationError is a ValueError: an older state
            return None, -1, f"unreadable state ({type(exc).__name__})"
        if state.code_version != R.VERSION or state.params_hash != self.params.digest():
            return None, -1, "stale state (code version or parameters)"
        hit = np.flatnonzero(ts == pd.Timestamp(state.bar_time))
        if not len(hit):
            return None, -1, "snapshot bar not in the tail (gap)"
        k = int(hit[0])
        cb = R.context_bars(self.params)
        if k + 1 < cb:
            return None, -1, "too few context bars before the snapshot"
        if context_digest(tail.iloc[k + 1 - cb:k + 1]) != digest:
            return None, -1, "a bar up to the snapshot was revised"
        return state, k, ""

    def update(self, tail: pd.DataFrame, bars_before_tail: int, series: Callable[[], pd.DataFrame]) -> FeedUpdate:
        """Replica rows for the tail's bars after the snapshot it resumed from (a corrected bar is served again),
        resumed from a kept state or rebuilt from `series()` (the mid bars from SERIES_START through the tail's last
        bar). The new snapshots are persisted before returning."""
        tail = tail.reset_index(drop=True)
        cb = R.context_bars(self.params)
        if len(tail) < cb:
            raise ValueError(f"the tail must hold at least {cb} bars, got {len(tail)}")
        state, k, reason = self._resume_point(tail, bars_before_tail)
        if state is None:
            return self._bootstrap(tail, series(), reason or "")
        frame, snaps = self._run(tail, state, k)
        self._persist(snaps, after=state.bar_index)
        return FeedUpdate(frame=frame, state=snaps[-1][0] if snaps else state, bootstrapped=False)

    def _run(self, bars: pd.DataFrame, state: R.ReplicaState | None,
             k: int) -> tuple[pd.DataFrame, list[tuple[R.ReplicaState, str]]]:
        """The replica rows of `bars` after position `k` (the bar of `state`; None and -1: from the series start), and
        (state, context digest) after each of the last KEEP_STATES bars: one run up to them, then bar by bar on the
        context window alone (`replica_run` resumes exactly on `context_bars` bars up to the snapshot)."""
        n, cb = len(bars), R.context_bars(self.params)
        step_from = max(k + 1, n - KEEP_STATES, cb)
        frames: list[pd.DataFrame] = []
        snaps: list[tuple[R.ReplicaState, str]] = []
        if step_from > k + 1:
            f, state = R.replica_run(bars.iloc[:step_from], self.params, state)
            frames.append(f)
        for i in range(step_from, n):
            f, state = R.replica_run(bars.iloc[i - cb:i + 1], self.params, state)
            frames.append(f)
            snaps.append((state, context_digest(bars.iloc[i + 1 - cb:i + 1])))
        if not frames:                               # nothing after the snapshot: the replica's empty frame
            frames.append(R.replica_run(bars.iloc[k + 1 - cb:], self.params, state)[0])
        return pd.concat(frames), snaps

    def _bootstrap(self, tail: pd.DataFrame, full_series: pd.DataFrame, reason: str) -> FeedUpdate:
        log.warning("replica feed %s: rebuild from the series start (%s)", self.feed, reason)
        check_series_start(full_series, self.series_start)
        full_series = full_series.reset_index(drop=True)
        ts_tail, ts_full = utc_index(tail["ts_utc"]), utc_index(full_series["ts_utc"])
        if ts_full[-1] != ts_tail[-1]:
            raise ValueError("the series must end at the tail's last bar")
        frame, snaps = self._run(full_series, None, -1)
        old = self.stored()                          # rows after the last snapshot served are the new ones
        after = pd.Timestamp(old["bar_time"]) if old and old["bar_time"] else None
        pos = np.flatnonzero(np.isin(epoch_ns(ts_full), epoch_ns(ts_tail)))
        rows = frame.loc[pos].copy()
        rows.index = pd.Index(np.searchsorted(epoch_ns(ts_tail), epoch_ns(ts_full[pos])))
        if after is not None:
            rows = rows[np.asarray(ts_tail[rows.index] > after)]
        self._persist(snaps, after=None)
        return FeedUpdate(frame=rows, state=snaps[-1][0], bootstrapped=True, reason=reason)

    def _persist(self, snaps: list[tuple[R.ReplicaState, str]], *, after: int | None) -> None:
        """Write the snapshots in one transaction. `after` None (a rebuild): every older row of the feed goes (they
        belong to the discontinued history); otherwise the rows after the snapshot resumed from (`after`, a bar_index)
        go, as they ran on bars that came back corrected or are written again now. Rows beyond the last KEEP_STATES
        bars go too."""
        if not snaps:
            return
        last = snaps[-1][0].bar_index
        with self.db.write() as conn:
            if after is None:
                conn.execute("DELETE FROM replica_state WHERE feed = ?", (self.feed,))
            else:
                conn.execute("DELETE FROM replica_state WHERE feed = ? AND (bar_index > ? OR bar_index <= ?)",
                             (self.feed, after, last - KEEP_STATES))
            conn.executemany(
                "INSERT INTO replica_state (feed, code_version, params_hash, bar_index, bar_time, context_digest, "
                "state, updated_ns) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(self.feed, st.code_version, st.params_hash, st.bar_index, st.bar_time, digest, R.state_json(st),
                  now_ns()) for st, digest in snaps])
