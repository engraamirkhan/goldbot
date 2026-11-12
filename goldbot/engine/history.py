"""The engine's 1m history must be unbroken (brain 2026-10-19: twelve restarts joined release bars ending 10-05 to a few
live minutes, so every decision that day ran on a window with a two-week hole).

Design rules (trading-safety and quant reviews of 9525b5b8):
- Nothing here runs on, or blocks, the tick thread's path to an exit. The broker's history is pulled on a background
  thread through its own broker client (`HistoryFill`; the engine's own client is never shared across threads) under a
  hard deadline that is checked before every page; the tick thread only absorbs a finished fill at a bar close. The
  engine's minutes are written by a background writer (`BarWriter`): a bounded queue, oldest batch dropped with a log
  on overflow, so a slow or locked store never stalls a tick.
- One feed per window, one cut-over: release (`bars_1m`, source RELEASE_SOURCE) minutes before the first minute of the
  traded broker's feed, the broker's minutes (`LIVE_TABLE`, source = broker name) from it on. The broker's minutes are
  built from its ticks with `ticks_to_1m(..., DEFAULT_SESSIONS)`, the live bar builder, and live in their own table,
  never blended into `bars_1m` (the convention of `bars_1m_broker`); another broker's minutes are never read.
- No decision on a window with a hole (`refuse`): more than `history_gap_max_min` (60) minutes missing that the
  engine's session table calls open refuse that timeframe's entries, journalled `history_gap:<tf>`, unless a broker
  pull covered the span (the broker had no ticks then: a real closure, e.g. a holiday the table calls open). A window
  with a hole is re-filled from the broker in the background every REFILL_EVERY of engine clock.
- A hole before the cut-over that the release cannot fill moves the cut-over back to the hole (`rebase`; vantage-demo
  2026-10-26..11-03: the release lacked the 10-11 Sunday open, the account's own minutes began 10-19, and every close
  refused for eight days). The broker's 1m bars from the hole to the old cut-over replace the release minutes there,
  so the window stays one feed per span with one cut-over; only when a good bar pull reaches back to the hole (fail
  closed), only within backfill_max_days, and only if the cut-over has not moved while the fill ran. The new cut-over
  is recorded in aux.db (`history_cutover`, only ever moving back) and a restart starts from it (#327 review L2).
- A store read of the history fails closed (backlog 155; duckdb 2.0 made every ts-filtered `Store.read` raise, and the
  engine service died at its warm start, open trades unmanaged). A warm-start or release re-read that raises, or a
  warm start that loads fewer 1m bars than `history_min_bars` (settings engine.warm_start_min_bars; weekday gold
  only), refuses every entry: `blocking` is a blocking data-quality check (RiskGate's data_quality_error on every
  entry path; the engine health check FAILs and names it) and every specialist close is journalled
  `history_read:<tf>` / `history_short:<tf>` (`refuse`). Exits and position management never read it: the engine
  starts and runs its exits instead of crashing. While either holds the store is read again every REFILL_EVERY of
  engine clock (`reread`): a read that succeeds merges its minutes (the engine's own win) and lifts history_read;
  history_short lifts once the window holds the minimum (the broker fill, a re-read or live minutes).
- Store reads after the warm start never run on the tick thread (backlog 157: a hung disk stalled every tick of the
  re-read; only the broker's SL/TP protected open trades until the watchdog restarted the service). The re-read and
  the release reload each run on a thread of their own (`StoreRead`), one of each in flight at a time; the tick
  thread merges a finished read at its next bar close (`after_close`), as `absorb` does a fill. A read that has not
  finished delays nothing and changes nothing: the refusal it would lift stays in force, and a read still running
  after READ_HUNG_S is logged once as an error. Exits never wait on it.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import TYPE_CHECKING, Any, Callable

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.data.calendar import DEFAULT_SESSIONS, SessionTable
from goldbot.data.resample import BAR_COLUMNS, ticks_to_1m
from goldbot.data.ticks import Pace, PullDeferred, pull_day
from goldbot.data.timeutil import epoch_ns
from goldbot.engine import weekend_lane
from goldbot.engine.shadow import minutes_of

if TYPE_CHECKING:
    from goldbot.data.store import Store
    from goldbot.engine.runner import Engine
    from goldbot.specialists.base import Specialist

log = logging.getLogger(__name__)

MINUTE_NS = 60 * 1_000_000_000
RELEASE_SOURCE = "dukascopy"        # the release bars research trained on (data/release.py load_into_store)
LIVE_TABLE = "bars_1m_live"         # the traded broker's minutes, source = broker name (store.TABLES)
PAUSE_S = 0.2                       # between pages: the engine's own requests get the bridge (ticks.py pacing)
PAGE_LIMIT_S = 30.0                 # one page slower than this stops the fill
REFILL_EVERY = pd.Timedelta(minutes=30)
REREAD_WARN_S = 2.0                 # a store read (`StoreRead`, own thread) slower than this is a warning
READ_HUNG_S = 120.0                 # a store read still running after this is logged once as an error
RATES_MAX = 100_000                 # broker 1m bars per fill (~10 weeks of trading minutes; MT5's chart default)
WRITER_QUEUE = 256                 # batches of minutes waiting for the store; the oldest is dropped beyond it

Span = tuple[pd.Timestamp, pd.Timestamp]


class HistoryGap(FrozenRecord):
    """Minutes missing between two consecutive 1m bars: [start, end), `open_min` of them in session and not covered."""
    start: UtcTimestamp       # the first missing minute
    end: UtcTimestamp         # the next bar present
    open_min: int


class GapMemo:
    """`open_gaps`' answer per hole, kept between bar closes (live 2026-11-04, icm-probe: every close of every book
    re-judged the same weekend holes minute by minute). A hole's answer depends only on its two bars, the session
    table, the covered spans and `max_open_min`: any of the last three changing forgets every answer, and a hole that
    left the window is dropped."""

    def __init__(self) -> None:
        self.inputs: tuple[Any, ...] | None = None
        self.holes: dict[tuple[int, int], HistoryGap | None] = {}


def open_gaps(ts_utc: pd.DatetimeIndex, sessions: SessionTable, *, max_open_min: int,
              covered: list[Span] | tuple[Span, ...] = (), memo: GapMemo | None = None) -> list[HistoryGap]:
    """Every hole in `ts_utc` (sorted 1m bar opens) with more than `max_open_min` missing minutes that the session
    table calls open and no `covered` span (a broker pull that found the market without ticks) explains. Only spans
    longer than `max_open_min` wall minutes are examined, so a 60000-bar window costs one diff. `memo`: the holes
    already judged with the same table, spans and limit (GapMemo)."""
    if len(ts_utc) < 2:
        return []
    ns = epoch_ns(ts_utc)
    idx = np.flatnonzero(np.diff(ns) > (max_open_min + 1) * MINUTE_NS)
    holes: dict[tuple[int, int], HistoryGap | None] = {}
    if memo is not None:
        inputs = (sessions, tuple(covered), max_open_min)
        if memo.inputs is None or memo.inputs[0] is not sessions or memo.inputs[1:] != inputs[1:]:
            memo.inputs, memo.holes = inputs, {}
        holes = memo.holes
    seen: dict[tuple[int, int], HistoryGap | None] = {}
    for i in idx:
        key = (int(ns[i]), int(ns[i + 1]))
        seen[key] = holes[key] if key in holes else _hole(ns[i], ns[i + 1], sessions, max_open_min, covered)
    if memo is not None:
        memo.holes = seen
    return [g for g in seen.values() if g is not None]


def _hole(before_ns: int, after_ns: int, sessions: SessionTable, max_open_min: int,
          covered: list[Span] | tuple[Span, ...]) -> HistoryGap | None:
    """The hole between two consecutive bars, or None when at most `max_open_min` of its minutes are open, uncovered."""
    start = pd.Timestamp(before_ns + MINUTE_NS, unit="ns", tz="UTC")
    end = pd.Timestamp(after_ns, unit="ns", tz="UTC")
    minutes = pd.date_range(start, end, freq="min", inclusive="left")
    missing = np.asarray(sessions.expected_open(minutes), dtype=bool)     # CME holiday closures are not holes
    for lo, hi in covered:
        missing &= ~((minutes >= lo) & (minutes < hi))
    n = int(missing.sum())
    return HistoryGap(start=start, end=end, open_min=n) if n > max_open_min else None


class _Deadline(Pace):
    """Pace without the quiet-window rule: the fill runs in market hours on its own thread; the deadline and the
    per-page time limit are checked at every page, so one long day can never overrun the budget."""

    def before_page(self) -> pd.Timestamp:
        if self.pages and self.pause_s > 0:
            self.sleep(self.pause_s)
        now = self.clock()
        if now >= self.deadline:
            raise PullDeferred(f"history fill budget spent after {self.pages} pages")
        return now


def rates_to_bars(r: pd.DataFrame | None, point: float, sessions: SessionTable = DEFAULT_SESSIONS) -> pd.DataFrame:
    """The broker's 1m rates (`get_bars`: ts_utc, open/high/low/close bid, tick_count, spread in points) as BAR_COLUMNS,
    one definition for the history fill and the live minutes (engine/broker_bars.py): bid_* = the rates (MT5 bars are
    bid), ask_* = bid_* + spread x point, spread_mean = spread_max = spread x point (MT5 keeps one spread per bar),
    tick_count = tick volume, visible_at = ts_utc + 1 min, in-session minutes only (DEFAULT_SESSIONS, as the live
    builder), sorted and de-duplicated. Empty for no rates or an unusable point."""
    if r is None or len(r) == 0 or not point > 0:
        return pd.DataFrame(columns=BAR_COLUMNS)
    ts = pd.DatetimeIndex(pd.to_datetime(r["ts_utc"], utc=True))
    spread = pd.to_numeric(r["spread"]).to_numpy(float) * point
    out = pd.DataFrame({"ts_utc": ts, "visible_at": ts + pd.Timedelta(minutes=1)})
    for side in ("open", "high", "low", "close"):
        bid = pd.to_numeric(r[side]).to_numpy(float)
        out[f"bid_{side}"] = bid
        out[f"ask_{side}"] = bid + spread
    out["tick_count"] = pd.to_numeric(r["tick_count"]).to_numpy(float)
    out["spread_mean"] = spread
    out["spread_max"] = spread
    out = out[np.asarray(sessions.is_open(ts), dtype=bool)]        # ADR 0062: the engine's bar table
    return out[BAR_COLUMNS].drop_duplicates("ts_utc").sort_values("ts_utc").reset_index(drop=True)


class BrokerRates:
    """The broker's own 1m bars (MT5 copy_rates via `get_bars(symbol, "1m", n)`), the fallback for minutes older than
    the terminal's tick history (brain 2026-10-19: ticks reached ~15 h back, rates weeks). Pulled once per fill, on the
    fill thread, under the fill's deadline; converted exactly to BAR_COLUMNS: bid_* = the rates (MT5 bars are bid),
    ask_* = bid_* + spread x point, spread_mean = spread_max = spread x point (MT5 keeps one spread per bar),
    tick_count = tick volume, visible_at = ts_utc + 1 min, in-session minutes only (DEFAULT_SESSIONS, as the live
    builder). A broker without get_bars/symbol_info (or a failed pull) yields no minutes: the gap check decides."""

    def __init__(self, broker: Any, symbol: str, pace: Pace, sessions: SessionTable = DEFAULT_SESSIONS):
        self.broker, self.symbol, self.pace, self.sessions = broker, symbol, pace, sessions
        self.frame: pd.DataFrame | None = None
        self.ok = False                  # a good pull (safety review MEDIUM: a failed pull never vouches for a day)

    def vouches(self, day: pd.Timestamp, since: pd.Timestamp) -> bool:
        """True when a good pull reached back to `day`, so a day it has no minutes for really had none (a closure)."""
        f = self._load(since)
        return self.ok and not f.empty and pd.Timestamp(f["ts_utc"].iloc[0]) <= day

    def _load(self, since: pd.Timestamp) -> pd.DataFrame:
        if self.frame is not None:
            return self.frame
        empty = pd.DataFrame(columns=BAR_COLUMNS)
        if not (hasattr(self.broker, "get_bars") and hasattr(self.broker, "symbol_info")):
            self.frame = empty
            return empty
        started = self.pace.before_page()                     # PullDeferred past the deadline, like a tick page
        n = min(RATES_MAX, int((started - since) / pd.Timedelta(minutes=1)) + 2)
        try:
            point = float(self.broker.symbol_info(self.symbol).point)
            r = self.broker.get_bars(self.symbol, "1m", max(n, 1))
        except Exception as exc:                              # noqa: BLE001 - fail soft: the gap check decides
            log.warning("broker 1m bars not available for the history fill: %s", type(exc).__name__)
            self.frame = empty
            return empty
        self.pace.after_page(started)
        if r is None or len(r) == 0 or not point > 0:
            self.frame = empty
            return empty
        self.ok = True
        self.frame = rates_to_bars(r, point, self.sessions)
        return self.frame

    def minutes(self, lo: pd.Timestamp, hi: pd.Timestamp, *, since: pd.Timestamp) -> pd.DataFrame:
        """Converted broker minutes with lo <= ts_utc < hi (the pull reaches back to `since`)."""
        f = self._load(since)
        return f[(f["ts_utc"] >= lo) & (f["ts_utc"] < hi)] if not f.empty else f


def pull_bars(broker: Any, symbol: str, span: Span, *, page_ticks: int, pace: Pace, keep: bool,
              rates: BrokerRates | None = None, since: pd.Timestamp | None = None,
              sessions: SessionTable = DEFAULT_SESSIONS,
              ) -> tuple[list[pd.DataFrame], list[Span], str | None]:
    """The broker's ticks in `span`, one UTC day per `pull_day`, as completed 1m bars (the live bar builder); minutes
    the ticks do not reach are taken from the broker's 1m bars (`rates`), so a terminal with a short tick history still
    fills a long hole. Returns (bars, covered, error): a day pulled whole is covered; with keep=False (a hole before the
    cut-over, where broker minutes would mix feeds) the bars are not kept and a day counts as covered only when the
    broker had neither ticks nor bars in it."""
    bars: list[pd.DataFrame] = []
    covered: list[Span] = []
    day, end = span
    since = span[0] if since is None else since
    while day < end:
        nxt = min(day.floor("D") + pd.Timedelta(days=1), end)
        try:
            ticks = pull_day(broker, symbol, day, nxt, chunk=page_ticks, pace=pace)
            b = ticks_to_1m(ticks[["ts_utc", "bid", "ask"]], sessions) if not ticks.empty else ticks.iloc[:0]
            if rates is not None:
                fb = rates.minutes(day, nxt, since=since)
                if not fb.empty:
                    fb = fb[~fb["ts_utc"].isin(b["ts_utc"])] if not b.empty else fb
                    b = pd.concat([x for x in (b, fb) if not x.empty]).sort_values("ts_utc").reset_index(drop=True)
        except PullDeferred:
            return bars, covered, "budget"
        except Exception as exc:                      # noqa: BLE001 - fail soft: the gap check decides
            log.warning("history fill stopped at %s: %s", day, type(exc).__name__)
            return bars, covered, type(exc).__name__
        if keep and not b.empty:
            bars.append(b)
        # covered (explains missing minutes to the gap check): only a day with no minutes at all, and only when a good
        # broker-bar pull reaching back to it shows the broker had none (a closure). A day with minutes is judged by
        # them; a failed or too-short pull never vouches (safety review MEDIUM: fail closed)
        if b.empty and rates is not None and rates.vouches(day, since):
            covered.append((day, nxt))
        day = nxt
    return bars, covered, None


class HistoryFill:
    """One background fill of `spans` (span, keep) from `broker`; `done` is set when it has finished."""

    def __init__(self, broker: Any, symbol: str, spans: list[tuple[Span, bool]], *, page_ticks: int, budget_s: float,
                 clock: Callable[[], pd.Timestamp] | None = None, sleep: Callable[[float], None] = time.sleep,
                 sessions: SessionTable = DEFAULT_SESSIONS, rebase: Span | None = None):
        self.broker, self.symbol, self.spans, self.page_ticks = broker, symbol, spans, page_ticks
        self.rebase = rebase                  # (oldest pre-cut-over hole, cut-over): the broker's bars for a new cut-over
        self.rebase_bars: pd.DataFrame | None = None     # set only when a good bar pull reached back to rebase[0]
        self.sessions = sessions              # the engine's bar table (ADR 0062)
        clk = clock or (lambda: pd.Timestamp.now("UTC"))
        self.pace = _Deadline(clk, sleep, pause_s=PAUSE_S, page_limit_s=PAGE_LIMIT_S, budget_s=budget_s)
        self.bars: list[pd.DataFrame] = []
        self.covered: list[Span] = []
        self.error: str | None = None
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._run, name="history-fill", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        try:
            rates = BrokerRates(self.broker, self.symbol, self.pace, self.sessions)
            starts = [s[0] for s, _ in self.spans] + ([self.rebase[0]] if self.rebase is not None else [])
            since = min(starts) if starts else None
            if self.rebase is not None and since is not None:
                lo, hi = self.rebase
                try:
                    if rates.vouches(lo, since):            # fail closed: the pull must reach back to the hole
                        self.rebase_bars = rates.minutes(lo, hi, since=since)
                except PullDeferred:
                    self.error = "budget"
                    return
            for span, keep in self.spans:
                bars, covered, err = pull_bars(self.broker, self.symbol, span, page_ticks=self.page_ticks,
                                               pace=self.pace, keep=keep, rates=rates, since=since,
                                               sessions=self.sessions)
                self.bars += bars
                self.covered += covered
                if err:
                    self.error = err
                    break
        except Exception as exc:                      # noqa: BLE001 - a fill thread never dies silently
            log.exception("history fill failed")
            self.error = type(exc).__name__
        finally:
            self.done.set()


class BarWriter:
    """Writes the engine's minutes to the store on its own thread: `put` never blocks (a full queue drops its oldest
    batch, logged and counted; a restart's fill re-pulls the minutes), queued batches are written as one append."""

    def __init__(self, store: Store, source: str, symbol: str, maxsize: int = WRITER_QUEUE):
        self.store, self.source, self.symbol = store, source, symbol
        self.q: queue.Queue[pd.DataFrame] = queue.Queue(maxsize)
        self.dropped = 0
        threading.Thread(target=self._run, name="history-writer", daemon=True).start()

    def put(self, bars: pd.DataFrame) -> None:
        while True:
            try:
                self.q.put_nowait(bars)
                return
            except queue.Full:
                try:
                    self.q.get_nowait()
                    self.q.task_done()
                    self.dropped += 1
                    log.warning("history writer queue full: oldest batch of minutes dropped (%d so far)", self.dropped)
                except queue.Empty:
                    pass

    def join(self, timeout: float) -> bool:
        """True once every queued batch is written (tests, shutdown)."""
        deadline = time.monotonic() + timeout
        while self.q.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        return not self.q.unfinished_tasks

    def _run(self) -> None:
        while True:
            batch = [self.q.get()]
            while True:
                try:
                    batch.append(self.q.get_nowait())
                except queue.Empty:
                    break
            try:
                self.store.append(LIVE_TABLE, pd.concat(batch, ignore_index=True)[BAR_COLUMNS], source=self.source,
                                  symbol=self.symbol)
            except Exception as exc:                  # noqa: BLE001 - storing history never stops trading
                log.warning("engine minutes not stored (%s): a restart's fill re-pulls them", type(exc).__name__)
            finally:
                for _ in batch:
                    self.q.task_done()


def _thread(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="history-read", daemon=True).start()


# the background store reads: kind -> (its name in the log, the refusal that stays in force while it has not merged)
READS = {"reread": ("re-read", "history_read / history_short"), "release": ("release reload", "history_gap")}


class StoreRead:
    """One store read on a thread of its own (backlog 157): `fn` runs off the tick thread; `done` is set when it has
    returned or raised (`result` / `error`). `fn` only reads the store: everything it needs is captured on the tick
    thread before it starts, and the tick thread alone merges its result (`after_close`)."""

    def __init__(self, kind: str, fn: Callable[[], Any], at: pd.Timestamp,
                 spawn: Callable[[Callable[[], None]], None]):
        self.kind, self.fn, self.at = kind, fn, at        # at: engine clock of the start, the read's point in time
        self.result: Any = None
        self.error: Exception | None = None
        self.elapsed_s = 0.0
        self.t0 = time.monotonic()
        self.hung_logged = False
        self.done = threading.Event()
        spawn(self._run)

    def _run(self) -> None:
        try:
            self.result = self.fn()
        except Exception as exc:                      # noqa: BLE001 - judged on the tick thread (fail closed)
            self.error = exc
        finally:
            self.elapsed_s = time.monotonic() - self.t0
            self.done.set()


def _running(h: History, kind: str) -> bool:
    """True while a `kind` read is in flight (nothing new is started); one running past READ_HUNG_S is logged once."""
    r = h.reads.get(kind)
    if r is None or r.done.is_set():
        return False
    took = time.monotonic() - r.t0
    if took > READ_HUNG_S and not r.hung_logged:
        r.hung_logged = True
        name, held = READS[kind]
        log.error("history: the store %s started at %s has not returned after %.0f s: entries stay refused while %s "
                  "holds, no other %s starts; exits run", name, r.at, took, held, name)
    return True


def _start(h: History, kind: str, fn: Callable[[], Any], at: pd.Timestamp) -> None:
    h.reads[kind] = StoreRead(kind, fn, at, h.spawn)


def _collect(h: History, kind: str) -> StoreRead | None:
    """The finished `kind` read (its duration logged, a warning over REREAD_WARN_S), or None while none has finished."""
    r = h.reads.get(kind)
    if r is None or not r.done.is_set():
        return None
    del h.reads[kind]
    (log.warning if r.elapsed_s > REREAD_WARN_S else log.info)("history: the store %s took %.2f s", READS[kind][0],
                                                               r.elapsed_s)
    return r


# ------------------------------------------------------------------------------------------- the engine's side
class History:
    """The engine's continuity state (runtime; only a rebased cut-over is persisted, `save_cutover`): the cut-over and
    fill status published as history_backfill, each decision timeframe's hole at its last close (history_gap), the
    spans broker pulls covered, the running fill and the writer."""

    def __init__(self) -> None:
        self.backfill: dict[str, Any] = {"cutover": None, "from": None, "bars": 0, "error": None, "running": False,
                                         "dropped": 0, "read_error": None, "read_short": None}
        self.gaps: dict[str, dict[str, Any]] = {}
        self.covered: list[Span] = []
        self.gap_memo = GapMemo()             # window_gaps' holes already judged (GapMemo)
        self.cutover: pd.Timestamp | None = None
        self.broker: Any = None
        self.fill: HistoryFill | None = None
        self.last_fill: pd.Timestamp | None = None
        self.writer: BarWriter | None = None
        self.read_error: str | None = None    # the last store read of the history raised (fail closed: `blocking`)
        self.read_short = False               # the warm start loaded fewer than history_min_bars (`blocking`)
        self.read_at: pd.Timestamp | None = None    # engine clock of the last store read while either holds
        self.reads: dict[str, StoreRead] = {}      # "reread" / "release": the store read in flight, one of each
        self.spawn: Callable[[Callable[[], None]], None] = _thread   # tests run the reads inline


def _clean(b: pd.DataFrame, now: pd.Timestamp) -> pd.DataFrame:
    if b.empty:
        return b
    b = b[pd.to_datetime(b["visible_at"], utc=True) <= now]
    return b[[c for c in BAR_COLUMNS if c in b.columns]].drop_duplicates("ts_utc", keep="last").sort_values("ts_utc")


def warm_start(eng: Engine, now: pd.Timestamp, broker: Any = None) -> int:
    """Seed eng.bars_1m from the store so a restarted engine can decide at once instead of rebuilding days of bars from
    live ticks (1h agents and the daily context need weeks): release minutes before the cut-over, the traded broker's
    stored minutes from it on. With `broker` (a client of its own: the bridge serves this thread separately) the tail
    to `now` and the holes are filled in the background (`start_fill`); entries stay refused (`refuse`) until the
    window is whole. A store read that raises starts the engine on no stored minutes with every entry refused
    (`read_failed`), never a crashed start: the exits must run. Returns the number of bars loaded (0 without a store)."""
    cfg, h = eng.cfg, eng.history
    if eng.store is None:
        return 0
    h.broker = broker
    lo = now - pd.Timedelta(days=_window_days(eng))
    try:
        rel, own = _read_store(eng, lo, now)
    except Exception as exc:                          # noqa: BLE001 - fail closed: entries refused, exits run
        read_failed(eng, now, exc)
        rel = own = pd.DataFrame()
    if not own.empty:
        h.cutover = pd.Timestamp(own["ts_utc"].iloc[0])
    saved = saved_cutover(eng)              # a rebased cut-over (#327 review L2): kept even if its minutes were lost
    # only inside the window: an older saved cut-over with no own minutes behind it (aux.db restored without
    # bars_1m_live after a re-host) would drop the release and hide a gap at the window start (#327 follow-up L1)
    if saved is not None and lo <= saved and (h.cutover is None or saved < h.cutover):
        h.cutover = saved
    if h.cutover is not None:
        rel = rel[rel["ts_utc"] < h.cutover] if not rel.empty else rel
    b = pd.concat([x for x in (rel, own) if not x.empty]) if not (rel.empty and own.empty) else pd.DataFrame()
    eng.bars_1m = b.tail(cfg.max_bars_in_memory).reset_index(drop=True) if not b.empty else pd.DataFrame()
    # a tick stamped inside these minutes is a re-delivery (the terminal's frozen quote after a restart with the market
    # closed or quiet): rebuilding its minute would duplicate the stored one and block entries with duplicate_ts
    eng._history_through = pd.Timestamp(eng.bars_1m["visible_at"].max()) if not eng.bars_1m.empty else None
    h.backfill["cutover"] = h.cutover.isoformat() if h.cutover is not None else None
    if h.read_error is None:
        _judge_short(eng, now)
    start_fill(eng, now)
    return len(eng.bars_1m)


def _window_days(eng: Engine) -> int:
    """Calendar days the engine's memory spans: max_bars_in_memory trading minutes, five trading days a week."""
    return eng.cfg.max_bars_in_memory // (60 * 23) * 7 // 5 + 3


def _read_store(eng: Engine, lo: pd.Timestamp, now: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(release minutes, the traded broker's stored minutes) with lo <= ts_utc < now, visible by `now`. Raises what
    the store raises: the callers fail closed (`read_failed`)."""
    return _store_reader(eng, lo, now)()


def _store_reader(eng: Engine, lo: pd.Timestamp, now: pd.Timestamp
                  ) -> Callable[[], tuple[pd.DataFrame, pd.DataFrame]]:
    """`_read_store` as a call that touches no engine state: the store, symbols and keys are taken here, on the tick
    thread, so the call can run on a thread of its own (`StoreRead`)."""
    from goldbot.engine import books
    store, rel_sym = eng.store, eng.cfg.release_symbol
    assert store is not None
    src, sym = books.live_key(eng)       # gold: (broker, its symbol); another book: (account, store symbol)

    def read() -> tuple[pd.DataFrame, pd.DataFrame]:
        rel = _clean(store.read("bars_1m", source=RELEASE_SOURCE, symbol=rel_sym, start=lo, end=now), now)
        own = _clean(store.read(LIVE_TABLE, source=src, symbol=sym, start=lo, end=now), now)
        return rel, own
    return read


def read_failed(eng: Engine, now: pd.Timestamp, exc: BaseException) -> None:
    """A store read of the history raised: every entry is refused (`blocking`) until a re-read succeeds (`reread`),
    published as history_backfill.read_error; logged with its traceback the first time."""
    h = eng.history
    first = h.read_error is None
    h.read_error, h.read_at = type(exc).__name__, now
    h.backfill["read_error"] = h.read_error
    log.error("history: the store read of the engine's history failed (%s: %s): entries refused until a re-read "
              "succeeds; exits run", type(exc).__name__, exc, exc_info=exc if first else None)


def _judge_short(eng: Engine, now: pd.Timestamp) -> None:
    """history_short: a warm start that loaded fewer than history_min_bars 1m bars (0 = off: the other books warm up
    in book_backfill, a weekend lane has no release)."""
    h, need, n = eng.history, eng.cfg.history_min_bars, len(eng.bars_1m)
    h.read_short = bool(need) and n < need
    h.backfill["read_short"] = {"bars": n, "min": need} if h.read_short else None
    if h.read_short:
        h.read_at = now
        log.error("history: the store gave %d 1m bars, fewer than the %d a warm start needs: entries refused until the "
                  "window holds them", n, need)


def blocking(eng: Engine) -> set[str]:
    """The history read's blocking data-quality checks (`Engine._dq_blocking`): history_read while the last store read
    raised; history_short while the window holds fewer than history_min_bars 1m bars, lifted for good the first time
    it holds them."""
    h = eng.history
    if h.read_error is not None:
        return {"history_read"}
    if h.read_short:
        n, need = len(eng.bars_1m), eng.cfg.history_min_bars
        if n < need:
            h.backfill["read_short"] = {"bars": n, "min": need}
            return {"history_short"}
        h.read_short, h.read_at = False, None
        h.backfill["read_short"] = None
        log.warning("history: the window holds %d 1m bars (>= %d): history_short lifted", n, need)
    return set()


def hold(eng: Engine) -> str | None:
    """The refusal a bar close journals for every specialist (`refuse`): history_read or history_short, else None."""
    got = blocking(eng)
    return "history_read" if "history_read" in got else "history_short" if got else None


def reread(eng: Engine, now: pd.Timestamp) -> bool:
    """While history_read or history_short holds, read the store again, every REFILL_EVERY of engine clock
    (`after_close`). A read that raises keeps history_read; one that succeeds lifts it and merges its minutes into
    the window by the warm start's rule (release before the cut-over, the broker's stored minutes from it on; a minute
    the engine already holds wins, so nothing a decision used is replaced). history_read is lifted only once the merge
    succeeded, and the window is then judged for history_short (the warm start whose read raised could not judge it:
    review of R123, HIGH). The read runs on a thread of its own (`StoreRead`, backlog 157), one at a time; this call,
    on the tick thread, merges a finished read and starts the next when due, so a read is merged at the first close
    after it returns and a read that never returns keeps the refusal (logged once past READ_HUNG_S). True when bars
    changed."""
    h = eng.history
    if eng.store is None:
        return False
    changed = _absorb_reread(eng, now)
    if (h.read_error is None and not h.read_short) or _running(h, "reread"):
        return changed
    if h.read_at is not None and now - h.read_at < REFILL_EVERY:
        return changed
    h.read_at = now
    _start(h, "reread", _store_reader(eng, now - pd.Timedelta(days=_window_days(eng)), now), now)
    return _absorb_reread(eng, now) or changed       # a read already finished (run inline) is merged now


def _absorb_reread(eng: Engine, now: pd.Timestamp) -> bool:
    """On the tick thread: merge a finished re-read (`reread`); a read or merge that raised keeps history_read."""
    h = eng.history
    r = _collect(h, "reread")
    if r is None:
        return False
    try:
        if r.error is not None:
            raise r.error
        rel, own = r.result
        changed = _merge_read(eng, rel, own)
    except Exception as exc:                          # noqa: BLE001 - fail closed: entries stay refused
        read_failed(eng, now, exc)
        return False
    if h.read_error is not None:
        log.warning("history: the store reads again after %s: history_read lifted", h.read_error)
        h.read_error = None
        h.backfill["read_error"] = None
        _judge_short(eng, now)
    return changed


def _merge_read(eng: Engine, rel: pd.DataFrame, own: pd.DataFrame) -> bool:
    """`reread`'s merge of the stored minutes into the window (the engine's own minutes win). True when bars changed."""
    h = eng.history
    if h.cutover is None and not own.empty:
        h.cutover = pd.Timestamp(own["ts_utc"].iloc[0])
        h.backfill["cutover"] = h.cutover.isoformat()
    if h.cutover is not None:
        rel = rel[rel["ts_utc"] < h.cutover] if not rel.empty else rel
        own = own[own["ts_utc"] >= h.cutover] if not own.empty else own
    new = [x for x in (rel, own) if not x.empty]
    if not new:
        return False
    merged = pd.concat([eng.bars_1m, *new]) if not eng.bars_1m.empty else pd.concat(new)
    merged = merged.drop_duplicates("ts_utc", keep="first").sort_values("ts_utc", kind="stable")
    n = len(merged) - len(eng.bars_1m)
    if n <= 0:
        return False
    eng.bars_1m = merged.tail(eng.cfg.max_bars_in_memory).reset_index(drop=True)
    eng._resamplers, eng._ctx_cache, eng._bars_clean = {}, {}, None
    log.warning("history: %d stored minutes re-read into the window", n)
    return True


def start_fill(eng: Engine, now: pd.Timestamp) -> None:
    """Start a background fill of the tail (last bar -> now, at most backfill_max_days) and of every hole in the
    window, unless one is running or there is no history broker. Holes at or after the cut-over are filled; holes
    before it are only probed (a span the broker had no ticks for is covered)."""
    cfg, h = eng.cfg, eng.history
    if h.broker is None or (h.fill is not None and not h.fill.done.is_set()):
        return
    floor = now - pd.Timedelta(days=cfg.backfill_max_days)
    last = pd.Timestamp(eng.bars_1m["visible_at"].iloc[-1]) if not eng.bars_1m.empty else floor
    tail = max(last, floor)
    if h.cutover is None and tail < now.floor("min"):
        # re-review HIGH: fix the cut-over at the tail start now, so a live minute arriving before the fill finishes
        # cannot move it later and turn the pulled tail into "covered" minutes that were never merged
        h.cutover = tail
        h.backfill["cutover"] = tail.isoformat()
    cut = h.cutover if h.cutover is not None else tail
    spans: list[tuple[Span, bool]] = [((tail, now.floor("min")), True)] if tail < now.floor("min") else []
    rebase: Span | None = None
    if not eng.bars_1m.empty:
        for g in open_gaps(pd.DatetimeIndex(eng.bars_1m["ts_utc"]), eng.sessions, max_open_min=cfg.history_gap_max_min,
                           covered=h.covered):
            if g.end > floor:
                spans.append(((max(g.start, floor), g.end), bool(g.start >= cut)))
            if h.cutover is not None and floor <= g.start < h.cutover and (rebase is None or g.start < rebase[0]):
                rebase = (g.start, h.cutover)         # the release cannot fill it (reload_release ran): move the cut-over
    if not spans:
        return
    h.fill = HistoryFill(h.broker, cfg.symbol, spans, page_ticks=cfg.backfill_page_ticks, budget_s=cfg.backfill_budget_s,
                         sessions=weekend_lane.bar_table(eng), rebase=rebase)
    h.last_fill = now
    h.backfill.update({"from": spans[0][0][0].isoformat(), "running": True})
    log.info("history fill started in the background: %d spans from %s", len(spans), spans[0][0][0])


def absorb(eng: Engine) -> bool:
    """On the tick thread at a bar close: merge a finished fill into eng.bars_1m (the resample and context caches
    are rebuilt), extend the covered spans and hand the new minutes to the writer. True when bars changed."""
    h = eng.history
    f = h.fill
    if f is None or not f.done.is_set():
        return False
    h.fill = None
    rebased = _rebase(eng, f)
    # a span counts as covered only where pulled bars can be merged (at or after the cut-over) or the broker had none
    # (keep=False spans are added by fill_spans only when empty); never a kept span before the cut-over
    cut = h.cutover
    h.covered += [(max(a, cut), b) if cut is not None and a < cut < b else (a, b) for a, b in f.covered
                  if cut is None or b > cut or not _kept_before(f, (a, b))]
    got = pd.concat(f.bars, ignore_index=True) if f.bars else pd.DataFrame()
    h.backfill.update({"bars": int(h.backfill["bars"]) + len(got), "error": f.error, "running": False})
    if got.empty:
        return rebased
    if h.cutover is None:
        h.cutover = pd.Timestamp(got["ts_utc"].min())
        h.backfill["cutover"] = h.cutover.isoformat()
    got = got[got["ts_utc"] >= h.cutover]
    merged = pd.concat([eng.bars_1m, got]) if not eng.bars_1m.empty else got
    merged = merged.drop_duplicates("ts_utc", keep="first").sort_values("ts_utc", kind="stable")
    eng.bars_1m = merged.tail(eng.cfg.max_bars_in_memory).reset_index(drop=True)
    eng._resamplers, eng._ctx_cache, eng._bars_clean = {}, {}, None    # bars inserted before the last: rebuild
    _write(eng, got)
    return True


def _rebase(eng: Engine, f: HistoryFill) -> bool:
    """Move the cut-over back to the oldest pre-cut-over hole the release could not fill: the broker's bars in
    [hole, old cut-over) replace the release minutes there (one feed per span, one cut-over) and go to the store, so a
    restart keeps the earlier cut-over. Nothing changes when the bar pull did not reach the hole, when it found no
    minutes, or when the cut-over moved while the fill ran. True when bars changed.

    A vouched closure is not a hole (#327 trading-safety review L3): the target is re-checked with the gap rule the
    history_gap refusal uses (`window_gaps`: the session table's closures, plus the spans this fill's good bar pull
    found without minutes, `f.covered`), so the cut-over moves back only to the oldest hole still open; none left:
    nothing changes."""
    h = eng.history
    if f.rebase is None or f.rebase_bars is None or f.rebase_bars.empty or h.cutover != f.rebase[1]:
        return False
    first, old = f.rebase
    holes = open_gaps(pd.DatetimeIndex(eng.bars_1m["ts_utc"]), eng.sessions, max_open_min=eng.cfg.history_gap_max_min,
                      covered=[*h.covered, *f.covered])
    starts = [g.start for g in holes if first <= g.start < old]
    if not starts:
        log.info("history: the hole before the cut-over at %s is a vouched closure: the cut-over stays", first)
        return False
    lo = min(starts)
    bars = f.rebase_bars[f.rebase_bars["ts_utc"] >= lo]
    if bars.empty:
        return False
    ts = eng.bars_1m["ts_utc"]
    keep = eng.bars_1m[(ts < lo) | (ts >= old)]
    merged = pd.concat([keep, bars]).drop_duplicates("ts_utc", keep="first").sort_values("ts_utc", kind="stable")
    eng.bars_1m = merged.tail(eng.cfg.max_bars_in_memory).reset_index(drop=True)
    eng._resamplers, eng._ctx_cache, eng._bars_clean = {}, {}, None
    h.cutover = lo
    h.backfill["cutover"] = lo.isoformat()
    h.backfill["bars"] = int(h.backfill["bars"]) + len(bars)
    _write(eng, bars)
    save_cutover(eng, lo)
    log.warning("history: cut-over moved back from %s to %s: the release lacks that hole, %d broker minutes replace "
                "the release from it on", old, lo, len(bars))
    return True


def _cutover_key(eng: Engine) -> tuple[str, str, str]:
    from goldbot.engine import books
    src, sym = books.live_key(eng)
    return eng.cfg.account_id, src, sym


def saved_cutover(eng: Engine) -> pd.Timestamp | None:
    """The cut-over a rebase recorded for this account's live feed (aux.db `history_cutover`), or None. An unreadable
    record is logged and None: the restart then derives the cut-over from the stored broker minutes, as before, and
    at worst rebases again (fail safe: never a cut-over later than the stored minutes say)."""
    from goldbot.db.connection import Database
    try:
        with Database.open(eng.cfg.state_dir, "aux").connection() as conn:
            r = conn.execute("SELECT cutover_ns FROM history_cutover WHERE account_id = ? AND source = ? AND symbol = ?",
                             _cutover_key(eng)).fetchone()
    except Exception as exc:                          # noqa: BLE001 - a broken record never stops a warm start
        log.warning("history cut-over record unreadable (%s): the stored minutes set the cut-over", type(exc).__name__)
        return None
    return pd.Timestamp(int(r[0]), unit="ns", tz="UTC") if r is not None else None


def save_cutover(eng: Engine, cutover: pd.Timestamp) -> None:
    """Record a rebased cut-over (#327 trading-safety review L2: the rebase lived in memory and in the stored broker
    minutes only, so a dropped writer batch made every restart redo it). Idempotent and only ever moves back: an
    existing earlier cut-over is kept. A release that later gains the hole changes nothing: minutes from the cut-over
    on stay the broker's (one feed per span). A failed write is logged; the stored minutes still carry it."""
    from goldbot.db.connection import Database, now_ns
    ns = int(epoch_ns(pd.DatetimeIndex([cutover]))[0])
    try:
        with Database.open(eng.cfg.state_dir, "aux").write() as conn:
            conn.execute("INSERT INTO history_cutover (account_id, source, symbol, cutover_ns, updated_ns) "
                         "VALUES (?, ?, ?, ?, ?) ON CONFLICT (account_id, source, symbol) DO UPDATE SET "
                         "cutover_ns = MIN(cutover_ns, excluded.cutover_ns), updated_ns = excluded.updated_ns",
                         (*_cutover_key(eng), ns, now_ns()))
    except Exception as exc:                          # noqa: BLE001 - recording never stops trading
        log.warning("history cut-over record not written (%s): a restart derives it from the stored minutes",
                    type(exc).__name__)


def _kept_before(f: HistoryFill, span: Span) -> bool:
    """True when `span` was pulled with keep=True (its bars, not an empty broker answer, made it covered)."""
    return any(keep and s[0] <= span[0] and span[1] <= s[1] for s, keep in f.spans)


def _write(eng: Engine, bars: pd.DataFrame) -> None:
    h = eng.history
    if eng.store is None or bars.empty:
        return
    if h.writer is None:
        from goldbot.engine import books
        h.writer = BarWriter(eng.store, *books.live_key(eng))
    h.writer.put(bars)


def queue_minutes(eng: Engine, done: pd.DataFrame) -> None:
    """Newly completed live minutes -> the background writer (never blocks the tick thread)."""
    h = eng.history
    if h.cutover is None and not done.empty:
        h.cutover = pd.Timestamp(done["ts_utc"].iloc[0])
        h.backfill["cutover"] = h.cutover.isoformat()
    _write(eng, done)


def reload_release(eng: Engine, now: pd.Timestamp) -> int:
    """Heal release-side holes from the store. The engine reads the release once, at warm_start; the data-dukascopy
    job later re-publishes a month (a Sunday-open hole the first pull missed), the brain syncs it into the store, and
    the running engine kept refusing every bar on the stale hole until its next restart (brain 2026-10-23: 4h refused
    since 08-28..31 although the store was whole from 10-24). Only the minutes the window lacks, before the cut-over,
    of the open gaps are read: `visible_at <= now` (point in time), release source, never over a minute the engine
    already holds (first copy wins), so a revised value never replaces what a decision already used. The read runs on
    a thread of its own (`StoreRead`, backlog 157), one at a time, and is merged on the tick thread by the first call
    after it returns (`absorb_release`), keeping only minutes before the cut-over found then (a rebase may have moved
    it back meanwhile). Returns bars added by this call."""
    h, cfg = eng.history, eng.cfg
    added = absorb_release(eng, now)
    if eng.store is None or eng.bars_1m.empty or h.cutover is None or _running(h, "release"):
        return added
    spans = [(g.start, min(g.end, h.cutover)) for g in window_gaps(eng, eng.bars_1m)
             if g.start < h.cutover]                        # at or after it: the broker's feed, the broker fill's
    if not spans:
        return added
    store, sym = eng.store, cfg.release_symbol

    def read() -> pd.DataFrame:
        got = []
        for lo, hi in spans:
            rel = _clean(store.read("bars_1m", source=RELEASE_SOURCE, symbol=sym, start=lo, end=hi), now)
            if not rel.empty:
                got.append(rel[(rel["ts_utc"] >= lo) & (rel["ts_utc"] < hi)])
        return pd.concat([g for g in got if not g.empty]) if any(not g.empty for g in got) else pd.DataFrame()
    _start(h, "release", read, now)
    return added + absorb_release(eng, now)          # a read already finished (run inline) is merged now


def absorb_release(eng: Engine, now: pd.Timestamp) -> int:
    """On the tick thread: merge a finished release reload (`reload_release`) into the window, release minutes before
    the current cut-over only, never over a minute the engine holds. A read that raised fails closed (`read_failed`).
    Returns bars added."""
    h = eng.history
    r = _collect(h, "release")
    if r is None:
        return 0
    if r.error is not None:
        read_failed(eng, now, r.error)
        return 0
    got: pd.DataFrame = r.result
    if got.empty or eng.bars_1m.empty:
        return 0
    if h.cutover is not None:
        got = got[got["ts_utc"] < h.cutover]
    merged = pd.concat([eng.bars_1m, got]).drop_duplicates("ts_utc", keep="first").sort_values("ts_utc", kind="stable")
    n = len(merged) - len(eng.bars_1m)
    if n <= 0:
        return 0
    eng.bars_1m = merged.tail(eng.cfg.max_bars_in_memory).reset_index(drop=True)
    eng._resamplers, eng._ctx_cache, eng._bars_clean = {}, {}, None
    log.info("history: %d release minutes re-read from the store into the window's holes", n)
    return n


def after_close(eng: Engine, close_ts: pd.Timestamp) -> None:
    """After a bar close's exits and decisions: absorb a finished fill and finished store reads (on this, the tick
    thread: nothing a background read returns is merged anywhere else); while a window has a hole, re-fill it from the
    broker every REFILL_EVERY. Never raises, never waits on a store read."""
    try:
        absorb(eng)
        h = eng.history
        reread(eng, close_ts)                         # only while a failed or short read refuses entries
        if h.gaps and (h.last_fill is None or close_ts - h.last_fill >= REFILL_EVERY):
            try:
                reload_release(eng, close_ts)
            except Exception as exc:                  # noqa: BLE001 - fail closed (`read_failed`); the fill still runs
                read_failed(eng, close_ts, exc)
            start_fill(eng, close_ts)
        else:
            absorb_release(eng, close_ts)             # a reload started at an earlier close
        if h.writer is not None:
            h.backfill["dropped"] = h.writer.dropped
    except Exception:                                 # noqa: BLE001 - history upkeep never stops trading
        log.exception("history upkeep failed")


def window_gaps(eng: Engine, complete: pd.DataFrame) -> list[HistoryGap]:
    """The holes in the completed 1m bars a bar close decides on (the engine's session table, covered spans out)."""
    return open_gaps(pd.DatetimeIndex(complete["ts_utc"]), eng.sessions, max_open_min=eng.cfg.history_gap_max_min,
                     covered=eng.history.covered, memo=eng.history.gap_memo)


def refuse(eng: Engine, tf: str, fr: Any, gaps: list[HistoryGap], agents: list[Specialist],
           close_ts: pd.Timestamp) -> list[dict] | None:
    """Design rule: no decision on a window with a hole. When one of `gaps` falls inside the window `fr` (the frame of
    tf) computes its features on, every agent of tf is journalled `history_gap:<tf>` and no live or shadow entry is
    made at this close (the open shadow trades still age); the hole is published as history_gap[tf]. While the store
    read failed or was short (`hold`) every agent of tf is journalled `history_read:<tf>` / `history_short:<tf>` the
    same way. None when the window is unbroken and was read whole. Exits and position management never look at it."""
    if (held := hold(eng)) is not None:
        _age_shadow(eng, tf, fr)
        return [eng._record(a, close_ts, 0.0, 0.0, f"{held}:{tf}") for a in agents]
    first = pd.Timestamp(fr.dec["ts_utc"].iloc[0])
    hit = [g for g in gaps if g.end > first]
    if not hit:
        eng.history.gaps.pop(tf, None)
        return None
    g = max(hit, key=lambda x: x.open_min)
    eng.history.gaps[tf] = {"from": g.start.isoformat(), "to": g.end.isoformat(), "open_min": g.open_min,
                            "at": close_ts.isoformat()}
    _age_shadow(eng, tf, fr)
    return [eng._record(a, close_ts, 0.0, 0.0, f"history_gap:{tf}") for a in agents]


def _age_shadow(eng: Engine, tf: str, fr: Any) -> None:
    """A refused close: the open shadow trades still age on its bar; no shadow entry is made."""
    if eng.shadow is not None:
        bar = fr.dec.iloc[-1]
        eng.shadow.on_bar(bar, tf, minutes_of(eng.bars_1m, bar, tf))


def published(eng: Engine) -> dict[str, Any]:
    """engine_<account>.json: history_gap (per decision timeframe, the hole in its window at its last close; {} =
    every evaluated window unbroken) and history_backfill (cut-over, fill status, minutes the writer dropped, the
    failed store read `read_error` and the short warm start `read_short` {bars, min}; null when neither holds)."""
    return {"history_gap": dict(eng.history.gaps), "history_backfill": dict(eng.history.backfill)}
