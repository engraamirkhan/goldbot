"""Warm-up of the non-gold books from the broker's 1m history (live demo issue 2026-11-02: icm-probe's 19 and
vantage-demo's 7 non-gold books never traded after an engine start, because `on_bar_close` decides nothing below 400
1m bars and only gold was back-filled; every deploy or restart cost a book about 7 hours of live minutes).

Design rules:
- One book at a time, in the background. The run loop calls `step` once per pass, after every book's exits and
  decisions (`books.account_pass`): it absorbs a finished pull into its book, or starts the next book's pull on a
  thread of its own with its own bridge client (`client`: one attempt, CLIENT_TIMEOUT_S, as key_zones.levels_client).
  A pass absorbs or starts, never both, so at most one book's bars change per pass and nothing waits on the bridge.
  Gold's exits and price reads never wait on it; gold itself is never back-filled here (engine/history.py).
- Paced and bounded (history._Deadline): PAGE_MIN minutes a page (`get_bars_range`, at most 1 440 bars),
  PAGE_PAUSE_S between pages, PAGE_LIMIT_S a page and TURN_BUDGET_S a turn; past the budget the rest waits for the
  book's next turn and the next book goes first. One pull per MT5 box at a time across engine processes
  (key_zones.box_lock on the key zones' own levels-<box> lock, so their long pull and this one never overlap; a
  held lock ends the turn `busy`, tried again after BUSY_RETRY_S). A failed page ends the turn; the book is tried
  again after RETRY_S, and nothing raises into the pass.
- Only the gap. The book's stored minutes (`warm_start`, `bars_1m_live` keyed (account, store_symbol)) are reused:
  a turn pulls the holes inside them, the tail from the last stored minute to now, then older minutes until the book
  holds `need` bars (the largest of MIN_BARS, a bar close's frame and the longest lookback its arms read), never
  further back than MAX_DAYS.
- The book's own sessions and point in time: pages become bars through history.rates_to_bars with the book's bar
  table (weekend_lane.bar_table), and only minutes already closed when the turn started (visible_at <= its start) are
  kept. Minutes the engine built from live ticks win; a minute at or after the first unbuilt tick is never inserted.
  The bars join the book's `bars_1m` and go to the store through its writer (books.live_key: never pooled with
  XAUUSD).
- A book is `warm` (SymbolBook.warm) once a turn has pulled every page it planned and the book holds `need` bars or
  the broker has no older minutes within MAX_DAYS. Until then it takes no entry (`books.warming`: no decision at its
  bar closes, its exploration candidates blocked `book_warming`); its exits, stops, close retries and position
  management never look at it. A book is cold only when a client is attached (`attach`): without one (paper, tests,
  a direct terminal) nothing changes. A blocked book (`BookSpec.blocked`) is never pulled: it takes no entry anyway
  and a quiet one makes no broker call (books.quiet_books).
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import pandas as pd

from goldbot.config import tf_seconds
from goldbot.data.calendar import SessionTable
from goldbot.data.resample import BAR_COLUMNS
from goldbot.data.ticks import PullDeferred

if TYPE_CHECKING:
    from goldbot.engine.books import SymbolBook
    from goldbot.engine.runner import Engine

log = logging.getLogger(__name__)

MIN_BARS = 400                       # on_bar_close's floor: below it a bar close decides nothing
FRAME_BARS = 120                     # Engine._frame's floor: decision-timeframe bars a bar close's frame needs
PAGE_MIN = 1440                      # one get_bars_range call: one day of 1m bars at most
PAGE_PAUSE_S = 1.0                   # between pages: the bridge also serves every book's quotes and gold
PAGE_LIMIT_S = 30.0                  # a slower page ends the turn (bridge slow)
TURN_BUDGET_S = 60.0                 # one book's turn; the rest of its gap waits for its next turn
RETRY_S = 120.0                      # a book whose page failed is tried again after this long
BUSY_RETRY_S = 30.0                  # another engine is pulling through the same MT5 box
CLIENT_TIMEOUT_S = 20.0              # the backfill client: one attempt, this timeout (key_zones.CLIENT_TIMEOUT_S)
MAX_DAYS = 120                       # never page further back than this (a broker with a short history ends there)
MINUTE = pd.Timedelta(minutes=1)
PAGE = pd.Timedelta(minutes=PAGE_MIN)
Span = tuple[pd.Timestamp, pd.Timestamp]


def client(url: str, token: str, account: str, *, post: Callable[..., Any] | None = None) -> Any:
    """The bridge client of the books' warm-up: its own session, ONE attempt per read and CLIENT_TIMEOUT_S."""
    from goldbot.execution.bridge import RemoteBroker
    c = RemoteBroker(url, token, name=f"mt5-remote-{account}-books", timeout_s=CLIENT_TIMEOUT_S, post=post)
    c.read_attempts = 1
    return c


def need(eng: Engine) -> int:
    """Bars the active book must hold before it decides: the largest of MIN_BARS, the FRAME_BARS decision bars of a
    bar close's frame and the longest lookback (in 1m rows, exactly as exploration reads them) of its arms, never more
    than the engine keeps (max_bars_in_memory)."""
    from goldbot.engine.exploration import LOOKBACK_1M, lookback
    ex = eng.exploration
    rows = [max(tf_seconds(a.tf) * (lookback(a.spec) + 1) // 60, LOOKBACK_1M)
            for a in (ex.arms.values() if ex is not None else ())]
    frame = tf_seconds(eng.cfg.decision_tf) * FRAME_BARS // 60
    return int(min(max([MIN_BARS, frame, *rows]), eng.cfg.max_bars_in_memory))


class Plan:
    """What one turn pulls for one book (computed on the run loop's thread from the book's own bars)."""

    def __init__(self, symbol: str, point: float, sessions: SessionTable, now: pd.Timestamp, have: int, want: int,
                 holes: list[Span], tail: Span | None, head: pd.Timestamp, floor: pd.Timestamp):
        self.symbol, self.point, self.sessions, self.now = symbol, point, sessions, now
        self.have, self.want, self.holes, self.tail, self.head, self.floor = have, want, holes, tail, head, floor


class Turn:
    """One book's pull on its own thread; `done` is set when it has finished."""

    def __init__(self, book: SymbolBook, plan: Plan):
        self.book, self.plan = book, plan
        self.bars: list[pd.DataFrame] = []
        self.complete = False           # every planned page pulled and the book reached its need or the floor
        self.error: str | None = None   # a failed page (retried after RETRY_S); "budget"/"busy" wait for the next turn
        self.done = threading.Event()


class Backfill:
    """The engine's warm-up state: the client, the turn in flight, the rotation and each book's next try."""

    def __init__(self, client: Any, *, box: str = "", lock_dir: Path | None = None,
                 clock: Callable[[], pd.Timestamp] | None = None, sleep: Callable[[float], None] = time.sleep,
                 spawn: Callable[[Callable[[], None]], None] | None = None):
        self.client, self.box, self.lock_dir = client, box, lock_dir
        self.clock: Callable[[], pd.Timestamp] = clock or (lambda: pd.Timestamp.now("UTC"))
        self.sleep = sleep
        self.spawn: Callable[[Callable[[], None]], None] = spawn or _thread
        self.turn: Turn | None = None
        self.cursor = 0
        self.next_try: dict[str, pd.Timestamp] = {}
        self.status: dict[str, dict[str, Any]] = {}

    def run(self, turn: Turn) -> None:
        """The thread: pull the plan's pages under the turn's deadline, one box at a time. Never raises."""
        from goldbot.engine.history import _Deadline
        from goldbot.engine.key_zones import box_lock
        p = turn.plan
        try:
            with box_lock(self.lock_dir, self.box) as mine:     # the key zones' lock: one pull per box
                if not mine:
                    turn.error = "busy"
                    return
                pace = _Deadline(self.clock, self.sleep, pause_s=PAGE_PAUSE_S, page_limit_s=PAGE_LIMIT_S,
                                 budget_s=TURN_BUDGET_S)
                for lo, hi in [*p.holes, *([p.tail] if p.tail is not None else [])]:
                    while lo < hi:
                        end = min(lo + PAGE, hi)
                        turn.bars.append(self._page(pace, p, lo, end))
                        lo = end
                have, cursor = p.have + sum(len(b) for b in turn.bars), p.head
                while have < p.want and cursor > p.floor:
                    start = max(cursor - PAGE, p.floor)
                    got = self._page(pace, p, start, cursor)
                    turn.bars.append(got)
                    have += len(got)
                    cursor = start
                turn.complete = True
        except PullDeferred:
            turn.error = "budget"
        except Exception as exc:                      # noqa: BLE001 - a failed page is retried, never raised
            turn.error = type(exc).__name__
        finally:
            turn.done.set()

    def _page(self, pace: Any, p: Plan, lo: pd.Timestamp, hi: pd.Timestamp) -> pd.DataFrame:
        from goldbot.engine.history import rates_to_bars
        began = pace.before_page()
        r = self.client.get_bars_range(p.symbol, "1m", lo, hi)
        pace.after_page(began)
        b = rates_to_bars(r, p.point, p.sessions)                 # the book's own sessions
        if b.empty:
            return b
        ts = pd.to_datetime(b["ts_utc"], utc=True)
        keep = (ts >= lo) & (ts < hi) & (pd.to_datetime(b["visible_at"], utc=True) <= p.now)   # point in time
        return b[keep]


def _thread(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="book-backfill", daemon=True).start()


def attach(eng: Engine, c: Any, *, box: str = "", lock_dir: Path | None = None, **kw: Any) -> Backfill:
    """Warm the engine's enabled non-gold books through `c`: each starts cold (no entry) until its backfill completes."""
    bf = Backfill(c, box=box, lock_dir=lock_dir, **kw)
    eng.__dict__["_book_backfill"] = bf
    for b in _candidates(eng):
        b.warm = False
    return bf


def _candidates(eng: Engine) -> list[SymbolBook]:
    return [b for b in eng.books.values() if not b.primary and (b.spec is None or b.spec.blocked is None)]


def step(eng: Engine) -> None:
    """Once per loop pass, after every book's exits and decisions: absorb a finished turn, else start the next cold
    book's turn. Never raises."""
    bf: Backfill | None = eng.__dict__.get("_book_backfill")
    if bf is None:
        return
    try:
        t = bf.turn
        if t is not None:
            if t.done.is_set():
                bf.turn = None
                _absorb(eng, bf, t)
            return
        _start(eng, bf)
    except Exception:                                 # noqa: BLE001 - the warm-up never stops trading
        log.exception("book backfill step failed; tried again next pass")


def _start(eng: Engine, bf: Backfill) -> None:
    from goldbot.engine import books
    now = bf.clock()
    cold = [b for b in _candidates(eng) if not b.warm]
    for i in range(len(cold)):
        b = cold[(bf.cursor + i) % len(cold)]
        sym = b.__dict__["cfg"].symbol
        if bf.next_try.get(sym, now) <= now:
            bf.cursor = (bf.cursor + i + 1) % len(cold)
            with books.using(eng, b):
                plan = _plan(eng, now)
            turn = bf.turn = Turn(b, plan)
            bf.status[sym] = {"warm": False, "bars": plan.have, "need": plan.want, "error": None}
            bf.spawn(lambda: bf.run(turn))
            return


def _plan(eng: Engine, now: pd.Timestamp) -> Plan:
    """The active book's turn: the holes in its stored minutes, the tail to `now`, then older minutes to its need."""
    from goldbot.engine import weekend_lane
    from goldbot.engine.history import open_gaps
    cfg, b = eng.cfg, eng.bars_1m
    end = now.floor("min")
    floor = end - pd.Timedelta(days=MAX_DAYS)
    holes: list[Span] = []
    tail: Span | None = None
    head = end
    if not b.empty:
        ts = pd.DatetimeIndex(pd.to_datetime(b["ts_utc"], utc=True))
        holes = [(max(g.start, floor), g.end) for g in open_gaps(ts, eng.sessions, max_open_min=cfg.history_gap_max_min)
                 if g.end > floor]
        last = max(ts[-1] + MINUTE, floor)
        tail = (last, end) if last < end else None
        head = max(ts[0], floor)
    return Plan(cfg.symbol, float(cfg.point), weekend_lane.bar_table(eng), now, len(b), need(eng), holes, tail, head,
                floor)


def _absorb(eng: Engine, bf: Backfill, t: Turn) -> None:
    """Merge a finished turn into its book (the run loop's thread): live minutes win, nothing at or after the first
    unbuilt tick's minute joins, the caches are rebuilt and the new minutes go to the store."""
    from goldbot.engine import books, history
    sym = t.book.__dict__["cfg"].symbol
    with books.using(eng, t.book):
        parts = [x for x in t.bars if not x.empty]
        got = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=BAR_COLUMNS)
        if eng.ticks and not got.empty:
            got = got[got["ts_utc"] < eng.ticks[0].ts_utc.floor("min")]
        if not got.empty:
            got = got[BAR_COLUMNS].drop_duplicates("ts_utc").sort_values("ts_utc")
            old = eng.bars_1m
            new = got[~got["ts_utc"].isin(old["ts_utc"])] if not old.empty else got
            merged = pd.concat([old, new]) if not old.empty else new
            eng.bars_1m = merged.sort_values("ts_utc", kind="stable").tail(eng.cfg.max_bars_in_memory) \
                .reset_index(drop=True)
            eng._resamplers, eng._ctx_cache, eng._bars_clean = {}, {}, None   # bars inserted: rebuild
            through = pd.Timestamp(eng.bars_1m["visible_at"].max())
            prev = eng._history_through
            eng._history_through = through if prev is None else max(prev, through)
            if not new.empty:
                history._write(eng, new)
        have, want = len(eng.bars_1m), t.plan.want
    if t.error not in (None, "budget"):
        wait = BUSY_RETRY_S if t.error == "busy" else RETRY_S
        bf.next_try[sym] = bf.clock() + pd.Timedelta(seconds=wait)
        if t.error != "busy":
            log.warning("%s: book backfill page failed (%s); tried again in %.0f s", sym, t.error, wait)
    t.book.warm = bool(t.complete)
    bf.status[sym] = {"warm": t.book.warm, "bars": have, "need": want, "error": t.error}
    if t.book.warm:
        log.info("%s: warmed up from the broker's history (%d 1m bars, need %d)", sym, have, want)
