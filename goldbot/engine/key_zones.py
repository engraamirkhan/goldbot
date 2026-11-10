"""The engine's current key zones (owner 2026-11-02: "identify key levels, support and resistance on all time
frames"), published in engine_<account>.json for the dashboard's price chart.

Design rules:
- Display only. Nothing here sizes, gates, places, moves or closes an order: no RiskGate input reads it. A failure,
  a non-finite price or a non-finite ATR keeps the last published zones and never fails a bar close.
- Never blocks the bar close. The bar close only parks the build's inputs, the engine's own completed 15m bars
  (cheap: the incremental resampler), its close, price and calendar (`update`). The build starts after the pass's
  entries were evaluated (`launch`; SRE diagnosis 2026-11-09, fix 4c: "move gold's key-zones build (1.2-2.6 s, every
  15m) off the bar-close pass"): on one book at the end of its pass, on a multi-book account once every book ran
  (`books.account_pass`). It builds what it would have built at the close, on its own daemon thread; a finished
  build is collected at the next pass.
  Builds over SLOW_BUILD_S are logged as warnings. The first failure of each exception type is logged with its
  traceback, later ones in one line.
- Full history (owner 2026-11-02). The engine holds about 62 trading days of 1m bars, too few for 1w swings (they
  live 540 days) and the 250-day strength pool. The worker keeps a long history of the broker's own M15 bars,
  LONG_DAYS deep. They come from `get_bars_range` through its own bridge client (`levels_client`: one attempt,
  CLIENT_TIMEOUT_S) in PAGE_DAYS pages, PAGE_PAUSE_S apart, within PULL_BUDGET_S (history._Deadline). They are topped
  up daily. Only one pull per MT5 box runs at a time (a file lock in state/locks, across engine processes), and the
  first pull after a restart waits a random delay. Every pull resumes at the oldest hole in the cache. The
  bars are cached in the store (`bars_15m_broker`, source = account, symbol = the account's symbol) and filtered
  through the account's own sessions, and they are used before the first of the engine's 15m bars. A cache that
  starts well after its target head is re-pulled from the head. An empty page over open market time counts as a
  failed pull (EmptyPage). An account on short history (accounts.yaml `key_levels_history: short`, for instruments
  that do not need it) gets no client. 15m is the book's own evaluation clock, so a 15m input gives every zone timeframe and the same
  definition research uses on 1m (`key_levels.zones_asof`). Without a client (paper, tests) the store cache, if any,
  and the engine's bars are used.
- Density (display only; the research definition stays frozen, ADR 0068). `select` publishes at most NEAREST zones
  each side. A shown zone has strength_pct >= DISPLAY_MIN_PCT (the median zone, a definition, not fitted). It is
  never a lone single-touch 15m swing. Neighbours less than CLUSTER_ATR x ATR(1d) apart merge for display, up to
  DISPLAY_MAX_WIDTH_ATR wide. The kept zones are ranked by strength-weighted proximity, strength_pct / 100 /
  (1 + edge distance / ATR(1d)).
"""
from __future__ import annotations

import hashlib
import logging
import random
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import urlparse

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.data.calendar import DEFAULT_SESSIONS, SessionTable
from goldbot.data.resample import BAR_COLUMNS
from goldbot.data.timeutil import epoch_ns
from goldbot.engine.history import _Deadline, rates_to_bars
from goldbot.features.key_levels import (
    CLUSTER_ATR,
    GRID_TF,
    MATURE_DAYS,
    PCT_WINDOW_DAYS,
    KeyZone,
    KeyZones,
    zones_asof,
)

try:
    import fcntl
except ImportError:                       # pragma: no cover - Windows: engines run on the Linux brain
    fcntl = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from goldbot.engine.runner import Engine

log = logging.getLogger(__name__)

NEAREST = 3                         # zones published on each side of the price
LONG_TF = GRID_TF                   # the long history's bars: the book's own clock (15m)
LONG_DAYS = MATURE_DAYS + PCT_WINDOW_DAYS + 10   # 800: a mature book plus a full strength pool
PAGE_DAYS = 30                      # one get_bars_range call (~2 000 M15 bars): the bridge also serves the engine
PAGE_PAUSE_S = 1.0                  # between pages
PAGE_LIMIT_S = 30.0                 # a slower page defers the rest of the pull (bridge slow)
PULL_BUDGET_S = 300.0               # a whole pull; past it the rest waits for the retry
CLIENT_TIMEOUT_S = 20.0             # the levels client: one attempt, this timeout
REFRESH = pd.Timedelta(days=1)      # the long history is topped up once a day
RETRY = pd.Timedelta(hours=1)       # after a failed, deferred or empty pull
BUSY_RETRY = pd.Timedelta(minutes=5)   # another worker is pulling through the same bridge
START_JITTER_S = 600.0              # the first pull after a restart waits a random 0 .. this long
OVERLAP = pd.Timedelta(days=2)      # a refresh re-reads its last two days (a bar the broker corrected)
HEAD_GAP = pd.Timedelta(days=5)     # a cache starting later than this after its target head is re-pulled from the head
HEAD_RECHECK = pd.Timedelta(days=7)   # ... at most this often (a broker whose history starts later is not re-read daily)
LONG_TABLE = "bars_15m_broker"
SLOW_BUILD_S = 1.0
DISPLAY_MIN_PCT = 50.0
DISPLAY_MAX_WIDTH_ATR = 2 * CLUSTER_ATR
_STEP = pd.Timedelta(GRID_TF)

# one long-history pull per MT5 box at a time (M2: up to 26 instruments' engines, one PROCESS each, share a box): an
# flock on state/locks/levels-<box>.lock, which every engine process of the brain sees; in-process locks only without a
# lock dir (paper, tests)
_LOCAL_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


def box_key(url: str, box: str | None = None) -> str:
    """The MT5 box a bridge URL points at, as a short hash (no host or box name ever lands in a file name or a log
    line). `box`: the account's own box name (accounts.yaml `box`, lane L1), which wins. Else the URL's host, so
    accounts on one box share it whatever their port; but a loopback host is an SSH tunnel's local end (every brain
    bridge is http://127.0.0.1:<port>), which names no box, so there the port does (safety re-review of #306, P1:
    one host key would make one lock for the whole brain)."""
    if box:
        key = f"box:{box}"
    else:
        u = urlparse(url)
        host = u.hostname or url
        key = f"{host}:{u.port}" if host in LOOPBACK else host
    return hashlib.sha256(key.encode()).hexdigest()[:12]


@contextmanager
def box_lock(lock_dir: Path | None, box: str) -> Iterator[bool]:
    """True while this process holds the box's pull lock, False when another holds it (never waits). The lock file
    stays; the flock dies with its process, so a killed engine never leaves the box locked."""
    if lock_dir is None or fcntl is None:
        with _LOCKS_GUARD:
            lk = _LOCAL_LOCKS.setdefault(box, threading.Lock())
        got = lk.acquire(blocking=False)
        try:
            yield got
        finally:
            if got:
                lk.release()
        return
    lock_dir.mkdir(parents=True, exist_ok=True)
    with open(lock_dir / f"levels-{box}.lock", "a") as fh:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


class EmptyPage(RuntimeError):
    """A page over open market time came back empty after bars were already known: the terminal had not loaded that
    history yet. The pull counts as failed (retried after RETRY), never as a hole to keep."""


def levels_client(url: str, token: str, account: str, *, post: Callable[..., Any] | None = None) -> Any:
    """The bridge client of the long-history pulls: its own session, ONE attempt per read and CLIENT_TIMEOUT_S, so a
    slow terminal costs one bounded request, never a retry storm."""
    from goldbot.execution.bridge import RemoteBroker
    client = RemoteBroker(url, token, name=f"mt5-remote-{account}-levels", timeout_s=CLIENT_TIMEOUT_S, post=post)
    client.read_attempts = 1
    return client


class _Job:
    """One build: inputs from the bar close, the result or the error from the worker thread."""

    def __init__(self, recent: pd.DataFrame, close_ts: pd.Timestamp, price: float):
        self.recent, self.close_ts, self.price = recent, close_ts, price
        self.result: dict[str, Any] | None = None
        self.error: BaseException | None = None
        self.elapsed_s = 0.0
        self.done = threading.Event()


class KeyZoneWorker:
    """The long history and the background build (module docstring). `client`: a bridge client of its own
    (`levels_client`), None without a bridge (or for an account on short history)."""

    def __init__(self, account: str, symbol: str, store: Any = None, client: Any = None,
                 sessions: SessionTable = DEFAULT_SESSIONS):
        self.account, self.symbol, self.store, self.client = account, symbol, store, client
        self.sessions = sessions                  # the account's own calendar (the engine's, set at each build)
        self.box = account                        # the MT5 box's key (box_key); one pull per box at a time
        self.lock_dir: Path | None = None         # state/locks: the cross-process pull lock
        self.long = pd.DataFrame(columns=BAR_COLUMNS)
        self.loaded = False                       # the store cache was read
        self.next_pull: pd.Timestamp | None = None
        self.broker_start: pd.Timestamp | None = None   # the first bar a pull from the head returned
        self.head_checked: pd.Timestamp | None = None   # when a pull from the head last completed
        # safety re-review of #306 (P2): a hole a completed pull re-read without getting a bar (the broker has none
        # there) -> when; skipped until HEAD_RECHECK, as `broker_start` is for the head; `_gap` the hole being pulled
        self.dead_gaps: dict[pd.Timestamp, pd.Timestamp] = {}
        self._gap: tuple[pd.Timestamp, pd.Timestamp] | None = None
        self.job: _Job | None = None
        # the inputs a bar close parked (`update`): recent bars, close, price, the account's calendar; `launch` starts it
        self.parked: tuple[pd.DataFrame, pd.Timestamp, float, SessionTable] | None = None
        self.clock: Callable[[], pd.Timestamp] = lambda: pd.Timestamp.now("UTC")   # pacing (wall time)
        self.sleep: Callable[[float], None] = time.sleep

    def attach(self, client: Any, box: str, lock_dir: Path | None = None, jitter_s: float | None = None) -> None:
        """Pull through `client`; `box` names its MT5 box (box_key), `lock_dir` holds the cross-process pull locks
        (one pull per box at a time). The first pull waits a random 0 .. START_JITTER_S, so engines restarted together
        do not all pull at once."""
        self.client, self.box, self.lock_dir = client, box, lock_dir
        wait = random.uniform(0, START_JITTER_S) if jitter_s is None else jitter_s
        self.next_pull = self.clock() + pd.Timedelta(seconds=wait)

    # ---------------------------------------------------------------- thread plumbing (tests run it inline)
    def _spawn(self, fn: Callable[[], None]) -> None:
        threading.Thread(target=fn, name=f"key-zones-{self.account}", daemon=True).start()

    def busy(self) -> bool:
        return self.job is not None and not self.job.done.is_set()

    def submit(self, recent: pd.DataFrame, close_ts: pd.Timestamp, price: float) -> bool:
        """Start a build; False (nothing started) while one runs. A thread that cannot start leaves no job behind
        (trading-safety L1, R110: the worker would read busy for ever); the error goes to the caller."""
        if self.busy():
            return False
        job = self.job = _Job(recent, close_ts, price)
        try:
            self._spawn(lambda: self._run(job))
        except BaseException:
            self.job = None
            raise
        return True

    def collect(self) -> _Job | None:
        job = self.job
        if job is None or not job.done.is_set():
            return None
        self.job = None
        return job

    def _run(self, job: _Job) -> None:
        t0 = time.monotonic()
        try:
            try:
                self.refresh(job.close_ts)
            finally:                               # a failed pull still builds on what is cached
                job.result = build(self.merged(job.recent), job.close_ts, job.price)
        except Exception as exc:              # noqa: BLE001 - reported by the bar close that collects it
            job.error = exc
        finally:
            job.elapsed_s = time.monotonic() - t0
            job.done.set()

    # ---------------------------------------------------------------- the long history
    def refresh(self, now: pd.Timestamp) -> None:
        """Read the store cache once; pull from the broker when due (daily; hourly after a failure), one pull per
        bridge at a time."""
        if not self.loaded:
            self.loaded = True
            if self.store is not None:
                try:
                    got = self.store.read(LONG_TABLE, source=self.account, symbol=self.symbol,
                                          start=now - pd.Timedelta(days=LONG_DAYS))
                    if got is not None and not got.empty:
                        self._absorb(got[BAR_COLUMNS], now)
                except Exception as exc:      # noqa: BLE001 - the broker pull below refills it
                    log.warning("key zones: cache unreadable (%s)", type(exc).__name__)
        if self.client is None or (self.next_pull is not None and now < self.next_pull):
            return
        with box_lock(self.lock_dir, self.box) as mine:
            if not mine:
                self.next_pull = now + BUSY_RETRY
                return
            try:
                self._pull(now)
                self.next_pull = now + REFRESH
            except Exception:
                self.next_pull = now + RETRY
                raise

    def _start(self, now: pd.Timestamp) -> pd.Timestamp:
        """Where the next pull starts, so every pull resumes at the oldest hole:
        - the head (now - LONG_DAYS) when the cache is empty, or starts more than HEAD_GAP after it (unless the broker's
          own history starts there, dated by a head page WITH bars), at most every HEAD_RECHECK once a head pull has
          completed (an empty first page on a cold terminal dates nothing: the head is asked again a week later);
        - else the last cached bar before the first gap holding more than HEAD_GAP of open market time (a head
          re-pull that was cut short leaves one between its pages and the older cache, review N2);
        - else the cache's last bars minus OVERLAP."""
        head = now - pd.Timedelta(days=LONG_DAYS)
        if self.long.empty:
            return head
        ts = pd.DatetimeIndex(pd.to_datetime(self.long["ts_utc"], utc=True))
        first, last = ts[0], ts[-1]
        recheck = self.head_checked is None or now - self.head_checked >= HEAD_RECHECK
        if first > head + HEAD_GAP and recheck and (self.broker_start is None or first > self.broker_start + HEAD_GAP):
            return head
        for i in map(int, np.flatnonzero(np.diff(epoch_ns(ts)) > HEAD_GAP.value)):   # calendar gaps first: cheap
            dead = self.dead_gaps.get(ts[i])
            if dead is not None and now - dead < HEAD_RECHECK:
                continue                                  # re-read empty within the week: the broker has nothing there
            if self._open_time(ts[i], ts[i + 1]) > HEAD_GAP:
                self._gap = (ts[i], ts[i + 1])
                return max(head, ts[i])
        return max(head, last - OVERLAP)

    def _open_time(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.Timedelta:
        """Open market time strictly between two bar opens, on the account's own sessions (15m resolution)."""
        grid = pd.date_range(start + _STEP, end - _STEP, freq=_STEP)
        return _STEP * int(np.asarray(self.sessions.is_open(grid)).sum()) if len(grid) else pd.Timedelta(0)

    def _pull(self, now: pd.Timestamp) -> None:
        """Paged, paced and time-boxed (history._Deadline): PAGE_DAYS a page, PAGE_PAUSE_S between pages, PAGE_LIMIT_S
        a page and PULL_BUDGET_S in all. Each page is cached as it arrives, so a deferred pull keeps its progress."""
        pace = _Deadline(self.clock, self.sleep, pause_s=PAGE_PAUSE_S, page_limit_s=PAGE_LIMIT_S,
                         budget_s=PULL_BUDGET_S)
        point = float(self.client.symbol_info(self.symbol).point)
        self._gap = None
        start = first_page = self._start(now)
        gap = self._gap if self._gap is not None and first_page == max(now - pd.Timedelta(days=LONG_DAYS),
                                                                       self._gap[0]) else None
        from_head = first_page == now - pd.Timedelta(days=LONG_DAYS)
        seen = not self.long.empty and pd.Timestamp(self.long["ts_utc"].iloc[0]) <= start
        while start < now:
            end = min(start + pd.Timedelta(days=PAGE_DAYS), now)
            began = pace.before_page()
            page = rates_to_bars(self.client.get_bars_range(self.symbol, LONG_TF, start, end), point, self.sessions)
            pace.after_page(began)
            if not page.empty:
                page["visible_at"] = pd.to_datetime(page["ts_utc"], utc=True) + _STEP   # an M15 bar is seen at its close
                page = page[pd.to_datetime(page["visible_at"], utc=True) <= now]         # never the forming bar
            if page.empty:
                if seen and self._open_between(start, end):
                    raise EmptyPage(f"{self.symbol} M15 {start} .. {end}: no bars over open market time")
            else:
                if from_head and start == first_page:     # only a head page WITH bars dates the broker's start
                    self.broker_start = pd.Timestamp(page["ts_utc"].iloc[0])
                seen = True
                if self.store is not None:
                    self.store.append(LONG_TABLE, page, source=self.account, symbol=self.symbol)
                self._absorb(page, now)
            start = end
        if from_head:
            self.head_checked = now
        if gap is not None:                               # completed: a hole still without a bar is the broker's
            ts = pd.to_datetime(self.long["ts_utc"], utc=True)
            if not ((ts > gap[0]) & (ts < gap[1])).any():
                self.dead_gaps[gap[0]] = now
                log.info("key zones: %s has no M15 bars in a %s hole; not re-read before %s", self.symbol,
                         gap[1] - gap[0], now + HEAD_RECHECK)

    def _open_between(self, start: pd.Timestamp, end: pd.Timestamp) -> bool:
        grid = pd.date_range(start.ceil(_STEP), end, freq=_STEP)
        return bool(len(grid)) and bool(np.asarray(self.sessions.is_open(grid)).any())

    def _absorb(self, got: pd.DataFrame, now: pd.Timestamp) -> None:
        b = pd.concat([self.long, got], ignore_index=True) if not self.long.empty else got.copy()
        b["ts_utc"] = pd.to_datetime(b["ts_utc"], utc=True)
        b["visible_at"] = pd.to_datetime(b["visible_at"], utc=True)
        b = b.drop_duplicates("ts_utc", keep="last").sort_values("ts_utc")
        self.long = b[b["ts_utc"] >= now - pd.Timedelta(days=LONG_DAYS)].reset_index(drop=True)[BAR_COLUMNS]

    def merged(self, recent: pd.DataFrame) -> pd.DataFrame:
        """The long history before the engine's first 15m bar, the engine's bars from it on."""
        if recent.empty:
            return self.long
        if self.long.empty:
            return recent
        first = pd.Timestamp(pd.to_datetime(recent["ts_utc"], utc=True).iloc[0])
        older = self.long[pd.to_datetime(self.long["ts_utc"], utc=True) < first]
        return pd.concat([older, recent[BAR_COLUMNS]], ignore_index=True)


# -------------------------------------------------------------------------------------------- display selection
class Shown(FrozenRecord):
    """A zone as the dashboard shows it (possibly several research zones merged for display)."""
    price: float
    lo: float
    hi: float
    strength: float
    strength_pct: float
    tfs: tuple[str, ...]
    touches: int
    role: str
    flipped: bool
    score: float


_TF_ORDER = ("15m", "1h", "4h", "1d", "1w", "1M", "round")


def _role(lo: float, hi: float, price: float) -> str:
    return "support" if price > hi else "resistance" if price < lo else "inside"


def _merge(group: list[KeyZone], price: float, atr: float) -> Shown:
    s = sum(z.strength for z in group)
    lo, hi = min(z.lo for z in group), max(z.hi for z in group)
    strongest = max(group, key=lambda z: z.strength)
    tfs = tuple(t for t in _TF_ORDER if any(t in z.tfs for z in group))
    pct = max(z.strength_pct or 0.0 for z in group)
    edge = 0.0 if lo <= price <= hi else min(abs(lo - price), abs(hi - price))
    role = _role(lo, hi, price)
    flipped = strongest.flipped and role == strongest.role
    return Shown(price=sum(z.price * z.strength for z in group) / s, lo=lo, hi=hi, strength=s, strength_pct=pct,
                 tfs=tfs, touches=sum(z.touches for z in group), role=role, flipped=flipped,
                 score=pct / 100.0 / (1.0 + edge / atr))


def select(z: KeyZones, price: float, n: int = NEAREST) -> tuple[list[Shown], list[Shown]]:
    """The zones the dashboard shows above and at or below `price`, nearest first (module docstring)."""
    atr = z.atr_1d
    if atr is None or not np.isfinite(atr) or atr <= 0:
        return [], []
    keep = [x for x in z.zones if x.strength_pct is not None and x.strength_pct >= DISPLAY_MIN_PCT
            and not (x.tfs == ("15m",) and x.touches <= 1)]
    groups: list[list[KeyZone]] = []
    for x in keep:                                         # by price (zones come sorted)
        g = groups[-1] if groups else None
        if g is not None and x.lo - g[-1].hi <= CLUSTER_ATR * atr and x.hi - g[0].lo <= DISPLAY_MAX_WIDTH_ATR * atr:
            g.append(x)
        else:
            groups.append([x])
    shown = [_merge(g, price, atr) for g in groups]
    above = sorted([s for s in shown if s.price > price], key=lambda s: -s.score)[:n]
    below = sorted([s for s in shown if s.price <= price], key=lambda s: -s.score)[:n]
    return sorted(above, key=lambda s: s.price), sorted(below, key=lambda s: -s.price)


def _zone(s: Shown) -> dict[str, Any]:
    return {"price": round(s.price, 2), "lo": round(s.lo, 2), "hi": round(s.hi, 2), "strength": round(s.strength, 3),
            "strength_pct": round(s.strength_pct, 1), "tfs": list(s.tfs), "touches": s.touches, "role": s.role,
            "flipped": s.flipped}


def build(bars: pd.DataFrame, close_ts: pd.Timestamp, price: float) -> dict[str, Any] | None:
    """The published zones at `close_ts` from completed bars (BAR_COLUMNS, 1m or 15m); `price`: the engine's last
    close. None without bars or with a non-finite price or ATR (the caller keeps the last zones)."""
    if bars.empty or not np.isfinite(price):
        return None
    z = zones_asof(bars, close_ts)
    if z.as_of is None or z.atr_1d is None or not np.isfinite(z.atr_1d):
        return None
    above, below = select(z, price)
    return {"as_of": z.as_of.isoformat(), "price": round(price, 2), "atr_1d": round(z.atr_1d, 2),
            "history_days": None if z.history_days is None else round(z.history_days, 1),
            "pct_ready": z.pct_ready, "zones_total": len(z.zones),
            "above": [_zone(s) for s in above], "below": [_zone(s) for s in below]}


# -------------------------------------------------------------------------------------------- the engine's side
def _last_price(complete: pd.DataFrame) -> float:
    if complete.empty:
        return float("nan")
    last = complete.iloc[-1]
    return float((float(last["bid_close"]) + float(last["ask_close"])) / 2.0)


def _report(eng: Engine, exc: BaseException) -> None:
    kind = type(exc).__name__
    first = kind not in eng.key_zones_errors
    eng.key_zones_errors.add(kind)
    log.warning("key zones not updated: %s: %s", kind, exc, exc_info=exc if first else None)


def _publish(eng: Engine, job: _Job | None) -> None:
    if job is None:
        return
    if job.elapsed_s > SLOW_BUILD_S:
        log.warning("key zones: build took %.1f s (> %.1f s)", job.elapsed_s, SLOW_BUILD_S)
    if job.error is not None:                    # a failed pull still built on the cache (published below)
        _report(eng, job.error)
    if job.result is not None:
        eng.key_zones = job.result
    elif job.error is None:
        log.warning("key zones: no finite price or ATR(1d) at %s: last zones kept", job.close_ts)


def update(eng: Engine, complete: pd.DataFrame, close_ts: pd.Timestamp) -> None:
    """At a bar close: publish a finished build, then, once a 15m bar has closed, park the next build's inputs
    (`KeyZoneWorker.parked`, replacing any not started yet) for `launch`. Never raises, never waits."""
    try:
        worker = eng.key_zone_worker
        _publish(eng, worker.collect())
        key = close_ts.floor(_STEP)
        if eng.key_zones_at == key:
            return
        eng.key_zones_at = key
        price = _last_price(complete)
        if not np.isfinite(price):
            log.warning("key zones: no finite price at %s: last zones kept", close_ts)
            return
        recent = eng._resample(complete, LONG_TF)
        recent = recent[pd.to_datetime(recent["visible_at"], utc=True) <= close_ts].reset_index(drop=True).copy()
        worker.parked = (recent, close_ts, price, eng.sessions)   # the account's own calendar for the broker's M15
    except Exception as exc:          # noqa: BLE001 - display only: a bar close never fails on it
        _report(eng, exc)


def launch(eng: Engine) -> None:
    """After the pass's entries (`books.exploration_pass`, `books.account_pass`): publish a finished build, then start
    the one a bar close parked once the worker is free (a build still running keeps it parked). Never raises, never
    waits."""
    try:
        worker = eng.key_zone_worker
        _publish(eng, worker.collect())
        if worker.parked is None or worker.busy():
            return
        recent, close_ts, price, sessions = worker.parked
        worker.sessions = sessions
        if worker.submit(recent, close_ts, price):
            worker.parked = None
            _publish(eng, worker.collect())          # a build already finished (run inline) is published now
    except Exception as exc:          # noqa: BLE001 - display only: a pass never fails on it
        _report(eng, exc)


def published(eng: Engine) -> dict[str, Any]:
    """engine_<account>.json: `key_zones` (None until the first build)."""
    return {"key_zones": eng.key_zones}


__all__ = ["KeyZoneWorker", "build", "launch", "published", "select", "update"]
