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
RATES_MAX = 100_000                 # broker 1m bars per fill (~10 weeks of trading minutes; MT5's chart default)
WRITER_QUEUE = 256                 # batches of minutes waiting for the store; the oldest is dropped beyond it

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
        missing = np.asarray(sessions.expected_open(minutes), dtype=bool)     # CME holiday closures are not holes
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


class BrokerRates:
    """The broker's own 1m bars (MT5 copy_rates via `get_bars(symbol, "1m", n)`), the fallback for minutes older than
    the terminal's tick history (brain 2026-10-19: ticks reached ~15 h back, rates weeks). Pulled once per fill, on the
    fill thread, under the fill's deadline; converted exactly to BAR_COLUMNS: bid_* = the rates (MT5 bars are bid),
    ask_* = bid_* + spread x point, spread_mean = spread_max = spread x point (MT5 keeps one spread per bar),
    tick_count = tick volume, visible_at = ts_utc + 1 min, in-session minutes only (DEFAULT_SESSIONS, as the live
    builder). A broker without get_bars/symbol_info (or a failed pull) yields no minutes: the gap check decides."""

    def __init__(self, broker: Any, symbol: str, pace: Pace):
        self.broker, self.symbol, self.pace = broker, symbol, pace
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
        out = out[np.asarray(DEFAULT_SESSIONS.is_open(ts), dtype=bool)]
        self.ok = True
        self.frame = out[BAR_COLUMNS].drop_duplicates("ts_utc").sort_values("ts_utc").reset_index(drop=True)
        return self.frame

    def minutes(self, lo: pd.Timestamp, hi: pd.Timestamp, *, since: pd.Timestamp) -> pd.DataFrame:
        """Converted broker minutes with lo <= ts_utc < hi (the pull reaches back to `since`)."""
        f = self._load(since)
        return f[(f["ts_utc"] >= lo) & (f["ts_utc"] < hi)] if not f.empty else f


def pull_bars(broker: Any, symbol: str, span: Span, *, page_ticks: int, pace: Pace, keep: bool,
              rates: BrokerRates | None = None, since: pd.Timestamp | None = None,
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
            b = ticks_to_1m(ticks[["ts_utc", "bid", "ask"]], DEFAULT_SESSIONS) if not ticks.empty else ticks.iloc[:0]
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
            rates = BrokerRates(self.broker, self.symbol, self.pace)
            since = min(s[0] for s, _ in self.spans) if self.spans else None
            for span, keep in self.spans:
                bars, covered, err = pull_bars(self.broker, self.symbol, span, page_ticks=self.page_ticks,
                                               pace=self.pace, keep=keep, rates=rates, since=since)
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
    # a tick stamped inside these minutes is a re-delivery (the terminal's frozen quote after a restart with the market
    # closed or quiet): rebuilding its minute would duplicate the stored one and block entries with duplicate_ts
    eng._history_through = pd.Timestamp(eng.bars_1m["visible_at"].max()) if not eng.bars_1m.empty else None
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
    if h.cutover is None and tail < now.floor("min"):
        # re-review HIGH: fix the cut-over at the tail start now, so a live minute arriving before the fill finishes
        # cannot move it later and turn the pulled tail into "covered" minutes that were never merged
        h.cutover = tail
        h.backfill["cutover"] = tail.isoformat()
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
    # a span counts as covered only where pulled bars can be merged (at or after the cut-over) or the broker had none
    # (keep=False spans are added by fill_spans only when empty); never a kept span before the cut-over
    cut = h.cutover
    h.covered += [(max(a, cut), b) if cut is not None and a < cut < b else (a, b) for a, b in f.covered
                  if cut is None or b > cut or not _kept_before(f, (a, b))]
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


def _kept_before(f: HistoryFill, span: Span) -> bool:
    """True when `span` was pulled with keep=True (its bars, not an empty broker answer, made it covered)."""
    return any(keep and s[0] <= span[0] and span[1] <= s[1] for s, keep in f.spans)


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
