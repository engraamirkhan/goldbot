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
WRITER_QUEUE = 256                  # batches of minutes waiting for the store; the oldest is dropped beyond it

Span = tuple[pd.Timestamp, pd.Timestamp]


class HistoryGap(FrozenRecord):
    """Minutes missing between two consecutive 1m bars: [start, end), `open_min` of them in session and not covered."""
    start: UtcTimestamp       # the first missing minute
    end: UtcTimestamp         # the next bar present
    open_min: int


def open_gaps(ts_utc: pd.DatetimeIndex, sessions: SessionTable, *, max_open_min: int,
              covered: list[Span] | tuple[Span, ...] = ()) -> list[HistoryGap]:
    """Every hole in `ts_utc` (sorted 1m bar opens) with more than `max_open_min` missing minutes that the session
    table calls open and no `covered` span (a broker pull that found the market without ticks) explains. Only spans
    longer than `max_open_min` wall minutes are examined, so a 60000-bar window costs one diff."""
    if len(ts_utc) < 2:
        return []
    ns = epoch_ns(ts_utc)
    idx = np.flatnonzero(np.diff(ns) > (max_open_min + 1) * MINUTE_NS)
    out = []
    for i in idx:
        start = pd.Timestamp(ns[i] + MINUTE_NS, unit="ns", tz="UTC")
        end = pd.Timestamp(ns[i + 1], unit="ns", tz="UTC")
        minutes = pd.date_range(start, end, freq="min", inclusive="left")
        missing = np.asarray(sessions.is_open(minutes), dtype=bool)
        for lo, hi in covered:
            missing &= ~((minutes >= lo) & (minutes < hi))
        n = int(missing.sum())
        if n > max_open_min:
            out.append(HistoryGap(start=start, end=end, open_min=n))
    return out


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


def pull_bars(broker: Any, symbol: str, span: Span, *, page_ticks: int, pace: Pace,
              keep: bool) -> tuple[list[pd.DataFrame], list[Span], str | None]:
    """The broker's ticks in `span`, one UTC day per `pull_day`, as completed 1m bars (the live bar builder). Returns
    (bars, covered, error): a day pulled whole is covered; with keep=False (a hole before the cut-over, where broker
    minutes would mix feeds) the bars are not kept and a day counts as covered only when the broker had no bars in it."""
    bars: list[pd.DataFrame] = []
    covered: list[Span] = []
    day, end = span
    while day < end:
        nxt = min(day.floor("D") + pd.Timedelta(days=1), end)
        try:
            ticks = pull_day(broker, symbol, day, nxt, chunk=page_ticks, pace=pace)
        except PullDeferred:
            return bars, covered, "budget"
        except Exception as exc:                      # noqa: BLE001 - fail soft: the gap check decides
            log.warning("history fill stopped at %s: %s", day, type(exc).__name__)
            return bars, covered, type(exc).__name__
        b = ticks_to_1m(ticks[["ts_utc", "bid", "ask"]], DEFAULT_SESSIONS) if not ticks.empty else ticks.iloc[:0]
        if keep and not b.empty:
            bars.append(b)
        if keep or b.empty:
            covered.append((day, nxt))
        day = nxt
    return bars, covered, None


class HistoryFill:
    """One background fill of `spans` (span, keep) from `broker`; `done` is set when it has finished."""

    def __init__(self, broker: Any, symbol: str, spans: list[tuple[Span, bool]], *, page_ticks: int, budget_s: float,
                 clock: Callable[[], pd.Timestamp] | None = None, sleep: Callable[[float], None] = time.sleep):
        self.broker, self.symbol, self.spans, self.page_ticks = broker, symbol, spans, page_ticks
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
            for span, keep in self.spans:
                bars, covered, err = pull_bars(self.broker, self.symbol, span, page_ticks=self.page_ticks,
                                               pace=self.pace, keep=keep)
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


# ------------------------------------------------------------------------------------------- the engine's side
class History:
    """The engine's continuity state (runtime, not persisted): the cut-over and fill status published as
    history_backfill, each decision timeframe's hole at its last close (history_gap), the spans broker pulls covered,
    the running fill and the writer."""

    def __init__(self) -> None:
        self.backfill: dict[str, Any] = {"cutover": None, "from": None, "bars": 0, "error": None, "running": False,
                                         "dropped": 0}
        self.gaps: dict[str, dict[str, Any]] = {}
        self.covered: list[Span] = []
        self.cutover: pd.Timestamp | None = None
        self.broker: Any = None
        self.fill: HistoryFill | None = None
        self.last_fill: pd.Timestamp | None = None
        self.writer: BarWriter | None = None


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
    window is whole. Returns the number of bars loaded (0 without a store)."""
    cfg, h = eng.cfg, eng.history
    if eng.store is None:
        return 0
    h.broker = broker
    days = cfg.max_bars_in_memory // (60 * 23) * 7 // 5 + 3     # trading minutes -> calendar days
    lo = now - pd.Timedelta(days=days)
    rel = _clean(eng.store.read("bars_1m", source=RELEASE_SOURCE, symbol=cfg.symbol, start=lo, end=now), now)
    own = _clean(eng.store.read(LIVE_TABLE, source=cfg.broker_name, symbol=cfg.symbol, start=lo, end=now), now)
    if not own.empty:
        h.cutover = pd.Timestamp(own["ts_utc"].iloc[0])
        rel = rel[rel["ts_utc"] < h.cutover] if not rel.empty else rel
    b = pd.concat([x for x in (rel, own) if not x.empty]) if not (rel.empty and own.empty) else pd.DataFrame()
    eng.bars_1m = b.tail(cfg.max_bars_in_memory).reset_index(drop=True) if not b.empty else pd.DataFrame()
    h.backfill["cutover"] = h.cutover.isoformat() if h.cutover is not None else None
    start_fill(eng, now)
    return len(eng.bars_1m)


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
    cut = h.cutover if h.cutover is not None else tail
    spans: list[tuple[Span, bool]] = [((tail, now.floor("min")), True)] if tail < now.floor("min") else []
    if not eng.bars_1m.empty:
        for g in open_gaps(pd.DatetimeIndex(eng.bars_1m["ts_utc"]), eng.sessions, max_open_min=cfg.history_gap_max_min,
                           covered=h.covered):
            if g.end > floor:
                spans.append(((max(g.start, floor), g.end), bool(g.start >= cut)))
    if not spans:
        return
    h.fill = HistoryFill(h.broker, cfg.symbol, spans, page_ticks=cfg.backfill_page_ticks, budget_s=cfg.backfill_budget_s)
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
    h.covered += f.covered
    got = pd.concat(f.bars, ignore_index=True) if f.bars else pd.DataFrame()
    h.backfill.update({"bars": int(h.backfill["bars"]) + len(got), "error": f.error, "running": False})
    if got.empty:
        return False
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


def _write(eng: Engine, bars: pd.DataFrame) -> None:
    h = eng.history
    if eng.store is None or bars.empty:
        return
    if h.writer is None:
        h.writer = BarWriter(eng.store, eng.cfg.broker_name, eng.cfg.symbol)
    h.writer.put(bars)


def queue_minutes(eng: Engine, done: pd.DataFrame) -> None:
    """Newly completed live minutes -> the background writer (never blocks the tick thread)."""
    h = eng.history
    if h.cutover is None and not done.empty:
        h.cutover = pd.Timestamp(done["ts_utc"].iloc[0])
        h.backfill["cutover"] = h.cutover.isoformat()
    _write(eng, done)


def after_close(eng: Engine, close_ts: pd.Timestamp) -> None:
    """After a bar close's exits and decisions: absorb a finished fill; while a window has a hole, re-fill it from the
    broker every REFILL_EVERY. Never raises."""
    try:
        absorb(eng)
        h = eng.history
        if h.gaps and (h.last_fill is None or close_ts - h.last_fill >= REFILL_EVERY):
            start_fill(eng, close_ts)
        if h.writer is not None:
            h.backfill["dropped"] = h.writer.dropped
    except Exception:                                 # noqa: BLE001 - history upkeep never stops trading
        log.exception("history upkeep failed")


def window_gaps(eng: Engine, complete: pd.DataFrame) -> list[HistoryGap]:
    """The holes in the completed 1m bars a bar close decides on (the engine's session table, covered spans out)."""
    return open_gaps(pd.DatetimeIndex(complete["ts_utc"]), eng.sessions, max_open_min=eng.cfg.history_gap_max_min,
                     covered=eng.history.covered)


def refuse(eng: Engine, tf: str, fr: Any, gaps: list[HistoryGap], agents: list[Specialist],
           close_ts: pd.Timestamp) -> list[dict] | None:
    """Design rule: no decision on a window with a hole. When one of `gaps` falls inside the window `fr` (the frame of
    tf) computes its features on, every agent of tf is journalled `history_gap:<tf>` and no live or shadow entry is
    made at this close (the open shadow trades still age); the hole is published as history_gap[tf]. None when the
    window is unbroken. Exits and position management never look at it."""
    first = pd.Timestamp(fr.dec["ts_utc"].iloc[0])
    hit = [g for g in gaps if g.end > first]
    if not hit:
        eng.history.gaps.pop(tf, None)
        return None
    g = max(hit, key=lambda x: x.open_min)
    eng.history.gaps[tf] = {"from": g.start.isoformat(), "to": g.end.isoformat(), "open_min": g.open_min,
                            "at": close_ts.isoformat()}
    if eng.shadow is not None:
        bar = fr.dec.iloc[-1]
        eng.shadow.on_bar(bar, tf, minutes_of(eng.bars_1m, bar, tf))
    return [eng._record(a, close_ts, 0.0, 0.0, f"history_gap:{tf}") for a in agents]


def published(eng: Engine) -> dict[str, Any]:
    """engine_<account>.json: history_gap (per decision timeframe, the hole in its window at its last close; {} =
    every evaluated window unbroken) and history_backfill (cut-over, fill status, minutes the writer dropped)."""
    return {"history_gap": dict(eng.history.gaps), "history_backfill": dict(eng.history.backfill)}
