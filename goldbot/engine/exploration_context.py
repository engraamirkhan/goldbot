"""What an exploration arm's rule reads beyond the engine's ~40-day 1m window, so live decides on the history run's
input (arms expansion 2026-10-26, section 3: "The same function runs in the history matrix and live"; ARM-SPEC 8.0:
"Each rule is one function used by the history matrix and live").

* Stored bars (`StoredBars`). C30A/C30C read 30 completed trading days and ATR14(1d) of D-1 off their 15m bars, DOND
  Donchian-55 and ATR14 on 1d, G1A/G1C the previous month's high and low on 1h (`history_bars`): more than the
  engine's 1m memory. The window is extended with the store's release bars of the arm's own timeframe (the bars the
  history matrix runs on: `bars_<tf>`, source dukascopy), read once and kept. Every stored bar opens before the
  window's first complete bar and closes by the decision close (point in time), and the store must reach into the
  window, so no bar is missing between the two. No store, a store that ends before the window, or fewer bars than
  declared: the arm is held back this bar, never decided on a short input (fail closed).
* Carried replica state (`ArmContext.replica`). smc_structure tvr1/tvr2 (XTV1, VXTV2) read the structure replica,
  whose running FVG threshold and structure depend on the series start (B4a: "the replica's state starts at the 2010
  series' first bar"). The arms get the shadow book's `ReplicaShadow` path on a feed of their own pinned to the
  research start (`smc_live.SERIES_START`, feed `<account>-<symbol>-s2010-<tf>-<params>`: each host its own, as the
  engines share aux.db and their brokers' bars differ): bootstrapped once in the background from the store's series
  since 2010 plus the window's complete bars, persisted in aux.db every bar and resumed bar by bar (after a restart
  too, while the bars stay continuous). Until the state is there (no store, a store that does not start the
  2010 series, a bootstrap running, the snapshot `state_lag` bars back not kept yet) the arm is held back. The
  engine's own shadow replica (settings tv.replica_series_start, bounded years) is another feed and is unchanged: its
  agents are shadow only, while these arms' values must equal the history run's.

Nothing here writes the engine's bars, ticks or state; every failure is an arm held back, never a failed bar close."""
from __future__ import annotations

import json
import logging
import time
from functools import lru_cache
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from goldbot.base import Record
from goldbot.config import tf_seconds
from goldbot.data.resample import mid
from goldbot.data.timeutil import epoch_ns, utc_index
from goldbot.engine.exploration import _bars, arm_bars
from goldbot.engine.history import RELEASE_SOURCE
from goldbot.engine.replica_context import ReplicaShadow
from goldbot.features import smc_replica as R
from goldbot.features.smc_live import SERIES_START, STATE_KEY
from goldbot.research.arms import Arm

if TYPE_CHECKING:
    from goldbot.db.connection import Database
    from goldbot.specialists.base import Specialist

log = logging.getLogger(__name__)

REREAD_S = 600.0                    # a store that does not reach the window is read again at most this often
HISTORY_UNAVAILABLE, REPLICA_WARMING, NO_BAR = "history_unavailable", "replica_warming", "no_bar"


def history_bars(arm: Arm) -> int:
    """Bars of the arm's timeframe its rule reads beyond the engine's window, declared by the rule
    (`arm_rules_channels.HISTORY_BARS`, `arm_rules_levels.LEVEL_HISTORY_BARS`); 0: the window is enough."""
    if arm.rule == "level_break":
        from goldbot.research.arm_rules_levels import LEVEL_HISTORY_BARS
        return LEVEL_HISTORY_BARS
    from goldbot.research.arm_rules_channels import HISTORY_BARS
    return HISTORY_BARS.get((arm.rule, arm.timeframe), 0)


def replica_bars(arm: Arm) -> int:
    """Bars a `specialist` arm whose family resumes a carried replica state reads: the replica's context up to the
    snapshot, the snapshot's lag and the decision bar (`smc_replica.replica_run`); 0 for any other arm."""
    if arm.rule != "specialist":
        return 0
    return _replica_bars(arm.timeframe, json.dumps(arm.params, sort_keys=True, default=str))


@lru_cache(maxsize=256)
def _replica_bars(timeframe: str, params_json: str) -> int:
    from goldbot.research.arm_rules_specialist import build_specialist
    spec: Any = build_specialist(Arm(id="replica-bars", rule="specialist", timeframe=timeframe,  # type: ignore[arg-type]
                                     params=json.loads(params_json)))
    params = getattr(spec, "replica_params", None)
    if params is None:
        return 0
    return R.context_bars(params) + int(spec.state_lag) + 1


def needs_context(arm: Arm) -> bool:
    """True for an arm whose rule reads stored bars or a carried replica state (this module); the others keep the
    engine's own path (`exploration.step`)."""
    return history_bars(arm) > 0 or replica_bars(arm) > 0


class RuleInputs(Record):
    """What `arm_rules.RuleContext` gets for the arm at a close: its bars, the 1m bars, the point-in-time tables."""
    bars: pd.DataFrame
    b1: pd.DataFrame
    spec_ctx: dict[str, Any] | None = None


def _ts(frame: pd.DataFrame) -> pd.DatetimeIndex:
    return utc_index(frame["ts_utc"])


class StoredBars:
    """The store's release bars of a timeframe before the engine's window, joined to the window's complete bars
    (`set_window`); also the replica's `BarHistory` (mid bars and their count from `series_start`). The stored bars
    must reach into the window (a bar opening at or after its first): then none is missing between them and the
    window, whatever weekends or holidays lie there, and a store that ends before the window is never joined to it."""

    def __init__(self, store: Any, symbol: str, series_start: pd.Timestamp = SERIES_START):
        self.store, self.symbol, self.series_start = store, symbol, series_start
        self.window: dict[str, pd.DataFrame] = {}                   # tf -> the window's complete bars
        self.through: dict[str, pd.Timestamp] = {}                  # tf -> the decision close they end at
        # tf -> (stored bars, bars wanted, the decision close they were read at, monotonic read time)
        self._tail: dict[str, tuple[pd.DataFrame, int, pd.Timestamp, float]] = {}
        # tf -> (stored opens since series_start, the decision close they were read at, monotonic read time)
        self._opens: dict[str, tuple[np.ndarray, pd.Timestamp, float]] = {}

    def set_window(self, tf: str, bars: pd.DataFrame, first_1m: pd.Timestamp, through: pd.Timestamp) -> None:
        """The window's bars of `tf` from its first complete one (opening at or after its first 1m bar)."""
        self.window[tf] = bars[np.asarray(_ts(bars) >= first_1m)].reset_index(drop=True)
        self.through[tf] = through

    def _w0(self, tf: str) -> pd.Timestamp:
        w = self.window.get(tf)
        if w is None or w.empty:
            raise ValueError(f"no complete {tf} bar in the engine's window")
        return pd.Timestamp(_ts(w)[0])

    def _read(self, tf: str, start: pd.Timestamp, columns: list[str] | None = None) -> pd.DataFrame:
        """Stored release bars opening from `start` and closed by the window's decision close (point in time)."""
        through = self.through[tf]
        b = self.store.read(f"bars_{tf}", source=RELEASE_SOURCE, symbol=self.symbol, start=start, end=through,
                            columns=columns)
        if b is None or b.empty:
            return pd.DataFrame()
        b = b.assign(ts_utc=pd.to_datetime(b["ts_utc"], utc=True))
        if "visible_at" in b.columns:
            b = b[np.asarray(pd.to_datetime(b["visible_at"], utc=True) <= through)]
        return b.sort_values("ts_utc", kind="stable").drop_duplicates("ts_utc", keep="last").reset_index(drop=True)

    def _reread(self, tf: str, covers: bool, read_through: pd.Timestamp, read_at: float) -> bool:
        """A kept read that does not reach the window is read again: at once when it was read before the window's
        first bar had closed (the window moved on since), else at most every REREAD_S (the store ends before it)."""
        first_close = pd.Timestamp(pd.to_datetime(self.window[tf]["visible_at"], utc=True).iloc[0])
        return not covers and (read_through < first_close or time.monotonic() - read_at >= REREAD_S)

    def _stored(self, tf: str, n: int) -> pd.DataFrame:
        """The last `n` stored bars opening before the window; empty when the store does not reach the window. Read
        once and kept; read again when more bars are wanted or the window has moved past what was read (`_reread`)."""
        w0 = self._w0(tf)
        cached = self._tail.get(tf)
        covers = cached is not None and len(cached[0]) > 0 and _ts(cached[0])[-1] >= w0
        if cached is None or cached[1] < n or self._reread(tf, covers, cached[2], cached[3]):
            want = max(n, cached[1] if cached is not None else 0)
            span = pd.Timedelta(seconds=tf_seconds(tf)) * want * 7 / 5 * 1.25 + pd.Timedelta(days=14)
            cached = (self._read(tf, w0 - span), want, self.through[tf], time.monotonic())
            self._tail[tf] = cached
        frame = cached[0]
        if not len(frame) or _ts(frame)[-1] < w0:
            return pd.DataFrame()
        return frame[np.asarray(_ts(frame) < w0)].tail(n).reset_index(drop=True)

    def bars(self, tf: str, n: int, through: pd.Timestamp) -> pd.DataFrame | None:
        """The last `n` bars of `tf` through the decision close: stored, then the window's; None when the store does
        not reach the window or there are fewer than `n` (fail closed)."""
        win = self.window[tf]
        need = n - len(win)
        if need > 0:
            pre = self._stored(tf, n)
            if len(pre) < need or not set(win.columns) <= set(pre.columns):
                return None
            win = pd.concat([pre[list(win.columns)].tail(need), win], ignore_index=True)
        out = win.tail(n).reset_index(drop=True)
        if len(out) < n or not (pd.to_datetime(out["visible_at"], utc=True) <= through).all():
            return None
        return out

    # ------------------------------------------------------------------ the replica's BarHistory (mid bars)
    def before(self, tf: str, first: pd.Timestamp, n: int | None = None) -> pd.DataFrame:
        """Mid bars with series_start <= ts_utc < first (the last `n` when given): stored, then the window's. Raises
        when the store does not reach the window."""
        w0 = self._w0(tf)
        if n is None:                                   # the bootstrap's series (on its own thread): read, never kept
            pre = self._read(tf, self.series_start)
            if not len(pre) or _ts(pre)[-1] < w0:
                raise ValueError(f"the stored {tf} bars do not reach the engine's window ({w0})")
            pre = pre[np.asarray(_ts(pre) < w0)]
        else:
            pre = self._stored(tf, n)
            if pre.empty:
                raise ValueError(f"the stored {tf} bars do not reach the engine's window ({w0})")
        win = self.window[tf]
        win = win[np.asarray(_ts(win) < first)]
        both = pd.concat([pre[list(win.columns)], win], ignore_index=True) if len(pre) else win
        out = mid(both).reset_index(drop=True)
        return (out.tail(n) if n is not None else out).reset_index(drop=True)

    def count(self, tf: str, first: pd.Timestamp) -> int:
        """Bars from series_start before the bar opening at `first`: the stored ones before the window, then the
        window's. The stored opens (one column) are read once, and again as `_stored`'s."""
        w0 = self._w0(tf)
        w0_ns = int(epoch_ns(pd.DatetimeIndex([w0]))[0])
        cached = self._opens.get(tf)
        covers = cached is not None and len(cached[0]) > 0 and cached[0][-1] >= w0_ns
        if cached is None or self._reread(tf, covers, cached[1], cached[2]):
            got = self._read(tf, self.series_start, columns=["ts_utc"])
            cached = (epoch_ns(_ts(got)) if len(got) else np.zeros(0, dtype=np.int64), self.through[tf],
                      time.monotonic())
            self._opens[tf] = cached
        opens = cached[0]
        if not len(opens) or opens[-1] < w0_ns:
            raise ValueError(f"the stored {tf} bars do not reach the engine's window ({w0})")
        return int((opens < w0_ns).sum()) + int((_ts(self.window[tf]) < first).sum())


class ArmContext:
    """The inputs of the arms that need more than the engine's window (module docstring)."""

    def __init__(self, db: Database, store: Any, symbol: str, account_id: str, warm_wait_s: float = 0.0):
        self.stored = StoredBars(store, symbol) if store is not None else None
        # warm_wait_s: how long a close waits for a bootstrap it started (production 0: never; tests wait). The feed
        # is the host's own: every engine on the brain shares state/aux.db, and two hosts on one feed (XTV1 on
        # icm-demo, VXTV2 on vantage-demo: one replica parameter block) found each other's snapshot, of another
        # broker's bars, at every close and rebuilt from 2010 (SRE 2026-10-27)
        self.replica = ReplicaShadow(db, symbol=f"{account_id}-{symbol.lower()}-s{SERIES_START.year}",
                                     history=self.stored, series_start=SERIES_START, warm_wait_s=warm_wait_s)
        self._agents: dict[str, Specialist] = {}

    def agent(self, arm: Arm) -> Specialist:
        if arm.id not in self._agents:
            from goldbot.research.arm_rules_specialist import build_specialist
            self._agents[arm.id] = build_specialist(arm)
        return self._agents[arm.id]

    def inputs(self, arm: Arm, complete: pd.DataFrame, through: pd.Timestamp) -> RuleInputs | str:
        """The rule's inputs at the close `through`, from the engine's complete 1m bars, or why the arm is held back
        (HISTORY_UNAVAILABLE, REPLICA_WARMING; NO_BAR: no bar of its timeframe closes now)."""
        complete = complete.reset_index(drop=True)
        tf, n = arm.timeframe, history_bars(arm)
        win = _bars(complete, tf, through, len(complete) + 1) if n or self.stored is not None else None
        if n:
            if win is None:
                return NO_BAR
            bars = None
            try:
                if self.stored is not None:
                    self.stored.set_window(tf, win, pd.Timestamp(_ts(complete)[0]), through)
                    bars = self.stored.bars(tf, n, through)
            except Exception as exc:                                    # noqa: BLE001 - a store error holds it back
                log.warning("exploration arm %s: stored bars unavailable (%s: %s)", arm.id, type(exc).__name__, exc)
            return HISTORY_UNAVAILABLE if bars is None else RuleInputs(bars=bars, b1=complete)
        got = arm_bars(complete, arm, through)
        if got is None:
            return NO_BAR
        try:
            tail = got[0]
            if self.stored is not None and win is not None:
                self.stored.set_window(tf, win, pd.Timestamp(_ts(complete)[0]), through)
                # the same bars from the window's complete ones: the rule's slice of 1m rows may start inside its
                # first bar (`exploration._shared_bars`), and a rebuild must not carry a part bar into the state
                tail = self.stored.window[tf].tail(len(tail))
            state = self.replica.state_for(self.agent(arm), mid(tail))
        except Exception as exc:                                        # noqa: BLE001 - fail closed, logged
            log.warning("exploration arm %s: replica state unavailable (%s: %s)", arm.id, type(exc).__name__, exc)
            state = None
        if state is None:
            return REPLICA_WARMING
        return RuleInputs(bars=got[0], b1=got[1], spec_ctx={STATE_KEY: state})
