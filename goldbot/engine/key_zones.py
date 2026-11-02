"""The engine's current key zones (owner 2026-11-02: "identify key levels, support and resistance on all time
frames"), published in engine_<account>.json for the dashboard's price chart.

Design rules:
- Display only. Nothing here sizes, gates, places, moves or closes an order: no RiskGate input reads it. A failure,
  a non-finite price or a non-finite ATR keeps the last published zones and never fails a bar close.
- Never blocks the bar close. The bar close only hands the worker the engine's own completed 15m bars (cheap: the
  incremental resampler). The worker builds on its own daemon thread and the next bar close collects the result.
  Builds over SLOW_BUILD_S are logged as warnings. The first failure of each exception type is logged with its
  traceback, later ones in one line.
- Full history (owner 2026-11-02). The engine holds about 62 trading days of 1m bars, too few for 1w swings (they
  live 540 days) and the 250-day strength pool. The worker keeps a long history of the broker's own M15 bars,
  LONG_DAYS deep. They come from `get_bars_range` through its own bridge client (one paged pull, then a daily
  refresh), are cached in the store (`bars_15m_broker`, source = account), and are used before the first of the
  engine's 15m bars. 15m is the book's own evaluation clock, so a 15m input gives every zone timeframe and the same
  definition research uses on 1m (`key_levels.zones_asof`). Without a client (paper, tests) the store cache, if any,
  and the engine's bars are used.
- Density (display only; the research definition stays frozen, ADR 0068). `select` publishes at most NEAREST zones
  each side. A shown zone has strength_pct >= DISPLAY_MIN_PCT (the median zone, a definition, not fitted). It is
  never a lone single-touch 15m swing. Neighbours less than CLUSTER_ATR x ATR(1d) apart merge for display, up to
  DISPLAY_MAX_WIDTH_ATR wide. The kept zones are ranked by strength-weighted proximity, strength_pct / 100 /
  (1 + edge distance / ATR(1d)).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Any, Callable

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.data.resample import BAR_COLUMNS
from goldbot.engine.history import rates_to_bars
from goldbot.features.key_levels import (
    CLUSTER_ATR,
    GRID_TF,
    MATURE_DAYS,
    PCT_WINDOW_DAYS,
    KeyZone,
    KeyZones,
    zones_asof,
)

if TYPE_CHECKING:
    from goldbot.engine.runner import Engine

log = logging.getLogger(__name__)

NEAREST = 3                         # zones published on each side of the price
LONG_TF = GRID_TF                   # the long history's bars: the book's own clock (15m)
LONG_DAYS = MATURE_DAYS + PCT_WINDOW_DAYS + 10   # 800: a mature book plus a full strength pool
PAGE_DAYS = 90                      # one get_bars_range call (~6 000 M15 bars): the bridge also serves the engine
REFRESH = pd.Timedelta(days=1)      # the long history is topped up once a day
RETRY = pd.Timedelta(hours=1)       # after a failed pull
OVERLAP = pd.Timedelta(days=2)      # a refresh re-reads its last two days (a bar the broker corrected)
LONG_TABLE = "bars_15m_broker"
SLOW_BUILD_S = 1.0
DISPLAY_MIN_PCT = 50.0
DISPLAY_MAX_WIDTH_ATR = 2 * CLUSTER_ATR
_STEP = pd.Timedelta(GRID_TF)


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
    (`get_bars_range`, `symbol_info`), None without a bridge."""

    def __init__(self, account: str, symbol: str, store: Any = None, client: Any = None):
        self.account, self.symbol, self.store, self.client = account, symbol, store, client
        self.long = pd.DataFrame(columns=BAR_COLUMNS)
        self.loaded = False                       # the store cache was read
        self.next_pull: pd.Timestamp | None = None
        self.job: _Job | None = None

    # ---------------------------------------------------------------- thread plumbing (tests run it inline)
    def _spawn(self, fn: Callable[[], None]) -> None:
        threading.Thread(target=fn, name=f"key-zones-{self.account}", daemon=True).start()

    def busy(self) -> bool:
        return self.job is not None and not self.job.done.is_set()

    def submit(self, recent: pd.DataFrame, close_ts: pd.Timestamp, price: float) -> bool:
        """Start a build; False (nothing started) while one runs."""
        if self.busy():
            return False
        job = self.job = _Job(recent, close_ts, price)
        self._spawn(lambda: self._run(job))
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
            self.refresh(job.close_ts)
            job.result = build(self.merged(job.recent), job.close_ts, job.price)
        except Exception as exc:              # noqa: BLE001 - reported by the bar close that collects it
            job.error = exc
        finally:
            job.elapsed_s = time.monotonic() - t0
            job.done.set()

    # ---------------------------------------------------------------- the long history
    def refresh(self, now: pd.Timestamp) -> None:
        """Read the store cache once; pull from the broker when due (daily; hourly after a failure)."""
        if not self.loaded:
            self.loaded = True
            if self.store is not None:
                try:
                    got = self.store.read(LONG_TABLE, source=self.account, start=now - pd.Timedelta(days=LONG_DAYS))
                    if got is not None and not got.empty:
                        self._absorb(got[BAR_COLUMNS], now)
                except Exception as exc:      # noqa: BLE001 - the broker pull below refills it
                    log.warning("key zones: cache unreadable (%s)", type(exc).__name__)
        if self.client is None or (self.next_pull is not None and now < self.next_pull):
            return
        try:
            point = float(self.client.symbol_info(self.symbol).point)
            start = now - pd.Timedelta(days=LONG_DAYS)
            if not self.long.empty:
                start = max(start, pd.Timestamp(self.long["ts_utc"].iloc[-1]) - OVERLAP)
            pages = []
            while start < now:
                end = min(start + pd.Timedelta(days=PAGE_DAYS), now)
                pages.append(rates_to_bars(self.client.get_bars_range(self.symbol, LONG_TF, start, end), point))
                start = end
            got = pd.concat(pages, ignore_index=True) if pages else pd.DataFrame(columns=BAR_COLUMNS)
            if not got.empty:
                got["visible_at"] = pd.to_datetime(got["ts_utc"], utc=True) + _STEP     # an M15 bar is seen at its close
                got = got[pd.to_datetime(got["visible_at"], utc=True) <= now]            # never the forming bar
                if self.store is not None:
                    self.store.append(LONG_TABLE, got, source=self.account)
                self._absorb(got, now)
            self.next_pull = now + REFRESH
        except Exception:
            self.next_pull = now + RETRY
            raise

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
    if job.error is not None:
        _report(eng, job.error)
    elif job.result is None:
        log.warning("key zones: no finite price or ATR(1d) at %s: last zones kept", job.close_ts)
    else:
        eng.key_zones = job.result


def update(eng: Engine, complete: pd.DataFrame, close_ts: pd.Timestamp) -> None:
    """At a bar close: publish a finished build, then hand the worker the next one once a 15m bar has closed.
    Never raises, never waits."""
    try:
        worker = eng.key_zone_worker
        _publish(eng, worker.collect())
        key = close_ts.floor(_STEP)
        if eng.key_zones_at == key:
            return
        price = _last_price(complete)
        if not np.isfinite(price):
            log.warning("key zones: no finite price at %s: last zones kept", close_ts)
            eng.key_zones_at = key
            return
        recent = eng._resample(complete, LONG_TF)
        recent = recent[pd.to_datetime(recent["visible_at"], utc=True) <= close_ts].reset_index(drop=True).copy()
        if worker.submit(recent, close_ts, price):
            eng.key_zones_at = key
            _publish(eng, worker.collect())          # a build already finished (run inline) is published now
    except Exception as exc:          # noqa: BLE001 - display only: a bar close never fails on it
        _report(eng, exc)


def published(eng: Engine) -> dict[str, Any]:
    """engine_<account>.json: `key_zones` (None until the first build)."""
    return {"key_zones": eng.key_zones}


__all__ = ["KeyZoneWorker", "build", "published", "select", "update"]
