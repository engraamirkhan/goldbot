"""The engine's completed 1m bars are the broker's own M1 bars (BACKLOG "engine 1m close lag").

Built from sampled `last_tick` quotes, the engine missed each minute's last ticks: about 1-2% of signals differed and
the D12 reconcile found 83 closes a day off the broker's. Design rules (trading-safety review follows):
- A completed minute is the broker's bar: its rates call (`get_bars(symbol, "1m", FETCH_N)`, MT5 copy_rates_from_pos),
  converted exactly as the history fill (`history.rates_to_bars`). It is read on the tick thread after the pass's
  exits, once when each in-session minute closes (the first pass with a tick of a later minute, or the clock past the
  close plus GRACE); a bridge call that fails is logged and retried, never a tick error, never an exit delayed.
- No look-ahead: a broker bar is taken only when it was fetched at or after its visible_at (open + 1 min, research's
  rule, unchanged) and the broker had finished it (a later bar in the reply, a tick at or after its close, or the clock
  past close + GRACE): the terminal's forming bar never joins. FETCH_N < 60, so a server zone off by a DST hour puts
  every bar of a reply either in the future (refused) or before the bars already held (ignored), never on a minute
  being built.
- The sampled bar (ticks_to_1m of the engine's quotes) is only the provisional current minute and the fallback of a
  completed minute whose broker bar is late or missing: that minute joins sampled, flagged `broker_bar_late` (a dq
  warning stored with the bar close's dq_events; entries are not blocked, the bar is what the engine always used), and
  is replaced when the broker's bar arrives within GIVE_UP (`broker_bar_replaced`; the store keeps the last copy, so a
  restart loads the broker's bar). A decision already taken on the sampled bar is never revised: its journal row and
  dq_events stand. A broker bar that joined is never replaced.
- Off (`EngineConfig.broker_bars`, the default) the bars are built exactly as before: paper, replays and backtests.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.data.quality import DQEvent
from goldbot.data.resample import BAR_COLUMNS, ticks_to_1m
from goldbot.data.timeutil import epoch_ns
from goldbot.engine import history

if TYPE_CHECKING:
    from goldbot.engine.runner import Engine
    from goldbot.execution.broker import Tick

log = logging.getLogger(__name__)

MINUTE = pd.Timedelta(minutes=1)
GRACE = pd.Timedelta(seconds=1.5)       # = runner.BAR_CLOSE_GRACE_S (design D10), so a decision close finds its bar
FETCH_N = 16                            # bars per rates call: covers GIVE_UP's retries; < 60 (a DST hour never matches)
RETRY = pd.Timedelta(seconds=5)         # a late bar is asked again this often (engine clock)
GIVE_UP = pd.Timedelta(minutes=10)      # after this the sampled minute stands for good (counted `never_arrived`)
KEEP = pd.Timedelta(minutes=30)         # fetched broker minutes kept for joining
ERROR_LOG_S = 300.0                     # a failing rates call is logged at most this often


class BrokerBars:
    """Runtime state (not persisted: a restart reads the store, whose minutes are the last copy written)."""

    def __init__(self) -> None:
        self.got = pd.DataFrame(columns=BAR_COLUMNS)        # finished broker minutes fetched, newest KEEP
        self.asked: pd.Timestamp | None = None              # open of the latest closed minute a fetch was due for
        self.wanted: set[pd.Timestamp] = set()              # closed in-session minutes still without their broker bar
        self.retry_at: pd.Timestamp | None = None
        self.fallback: set[pd.Timestamp] = set()            # minutes in bars_1m from sampled ticks, awaiting the broker
        self.point: float | None = None
        self.stats = {"fetches": 0, "fetch_errors": 0, "broker": 0, "fallback": 0, "replaced": 0, "never_arrived": 0}
        self._err_logged = -float("inf")

    @property
    def through(self) -> pd.Timestamp | None:
        """End of the latest finished broker minute fetched: every minute before it is complete."""
        return None if self.got.empty else pd.Timestamp(self.got["ts_utc"].iloc[-1]) + MINUTE


def poll(eng: Engine, t: Tick) -> None:
    """After the pass's exits: fetch the broker's bars once when a minute has closed, and again every RETRY while a
    closed minute the engine has quotes for still lacks its broker bar (up to GIVE_UP)."""
    if not eng.cfg.broker_bars:
        return
    bb = eng.broker_m1
    now = max(t.ts_utc, eng._now(t))
    closed = max(t.ts_utc.floor("min"), (now - GRACE).floor("min")) - MINUTE
    new = bb.asked is None or closed > bb.asked
    if new:
        start = closed if bb.asked is None else max(bb.asked + MINUTE, closed - (FETCH_N - 1) * MINUTE)
        minutes = pd.date_range(start, closed, freq="min")
        bb.wanted |= set(minutes[np.asarray(DEFAULT_SESSIONS.is_open(minutes), dtype=bool)])
        bb.asked = closed
    _expire(bb, now)
    if bb.wanted and (new or (bb.retry_at is not None and now >= bb.retry_at)):
        _fetch(eng, bb, t, now)


def _expire(bb: BrokerBars, now: pd.Timestamp) -> None:
    bb.wanted = {m for m in bb.wanted if now - (m + MINUTE) < GIVE_UP}
    gone = [m for m in bb.fallback if now - (m + MINUTE) >= GIVE_UP]
    bb.fallback -= set(gone)
    if gone:
        bb.stats["never_arrived"] += len(gone)
        log.warning("broker 1m bar never arrived for %d minute(s) from %s: the sampled bars stand", len(gone), min(gone))


def _fetch(eng: Engine, bb: BrokerBars, t: Tick, now: pd.Timestamp) -> None:
    bb.retry_at = now + RETRY
    bb.stats["fetches"] += 1
    try:
        if bb.point is None:
            bb.point = float(eng.broker.symbol_info(eng.cfg.symbol).point)
        r = eng.broker.get_bars(eng.cfg.symbol, "1m", FETCH_N)
    except Exception as exc:              # noqa: BLE001 - fail soft: the sampled minutes stand (dq warning)
        bb.stats["fetch_errors"] += 1
        mono = time.monotonic()
        if mono - bb._err_logged >= ERROR_LOG_S:
            bb._err_logged = mono
            log.warning("broker 1m bars not read (%s): completed minutes fall back to the sampled ticks",
                        type(exc).__name__)
        return
    bars = history.rates_to_bars(r, bb.point)
    if not bars.empty:
        ts = pd.DatetimeIndex(bars["ts_utc"])
        vis = ts + MINUTE
        finished = (vis <= now) & ((ts < ts.max()) | (vis <= t.ts_utc) | (vis + GRACE <= now))
        bars = bars[np.asarray(finished, dtype=bool)].reset_index(drop=True)
    received: set[pd.Timestamp] = set()
    if not bars.empty:
        merged = bars if bb.got.empty else pd.concat([bb.got, bars])
        merged = merged.drop_duplicates("ts_utc", keep="last").sort_values("ts_utc")
        bb.got = merged[merged["ts_utc"] >= now - KEEP].reset_index(drop=True)
        received = set(pd.DatetimeIndex(bars["ts_utc"]))
        late = sorted(bb.fallback & received)
        if late:
            _replace(eng, bb, bars[bars["ts_utc"].isin(late)])
    bb.wanted = {m for m in bb.wanted - received if m in bb.fallback or _has_quotes(eng, m)}


def _has_quotes(eng: Engine, minute: pd.Timestamp) -> bool:
    end = minute + MINUTE
    return any(minute <= x.ts_utc < end for x in eng.ticks)


def _like(frame: pd.DataFrame, ref: pd.DataFrame) -> pd.DataFrame:
    """`frame` with `ref`'s column dtypes (the incremental resampler and the clean append compare dtypes)."""
    if frame.empty or ref.empty:
        return frame
    out = frame.copy()
    for c in BAR_COLUMNS:
        if c in ref.columns and out[c].dtype != ref[c].dtype:
            out[c] = out[c].astype(ref[c].dtype)
    return out


def _replace(eng: Engine, bb: BrokerBars, bars: pd.DataFrame) -> None:
    """Fallback minutes still in bars_1m take the broker's bars (same ts_utc and visible_at); the caches built on them
    are dropped and the store gets the broker's copy. Decisions already taken are not touched."""
    b = eng.bars_1m
    if b.empty:
        return
    hit = b["ts_utc"].isin(bars["ts_utc"]).to_numpy()
    if not hit.any():
        return
    src = _like(bars, b).set_index("ts_utc").loc[b.loc[hit, "ts_utc"]]
    new = b.copy()
    moved = float(np.abs(new.loc[hit, "bid_close"].to_numpy(float) - src["bid_close"].to_numpy(float)).max())
    for c in BAR_COLUMNS[2:]:
        new.loc[hit, c] = src[c].to_numpy()
    eng.bars_1m = new
    eng._resamplers, eng._ctx_cache, eng._bars_clean = {}, {}, None
    replaced = list(pd.DatetimeIndex(new.loc[hit, "ts_utc"]))
    bb.fallback -= set(replaced)
    bb.stats["replaced"] += len(replaced)
    history.queue_minutes(eng, new.loc[hit])
    eng._dq_pending.append(DQEvent(ts_utc=min(replaced), check="broker_bar_replaced", severity="warning",
                                   detail=f"{len(replaced)} minute(s) from sampled ticks replaced by the broker's bars "
                                          f"(largest close change {moved:.2f}); decisions already taken on them stand"))


def _merge(eng: Engine, bb: BrokerBars, done: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    """The completed minutes before `cutoff` that are new to bars_1m: the broker's bar where fetched, else the
    sampled one (flagged `broker_bar_late`, remembered for replacement)."""
    last = None if eng.bars_1m.empty else pd.Timestamp(eng.bars_1m["ts_utc"].iloc[-1])
    b = bb.got[bb.got["ts_utc"] < cutoff] if not bb.got.empty else bb.got
    if last is not None:
        b = b[b["ts_utc"] > last] if not b.empty else b
        done = done[done["ts_utc"] > last] if not done.empty else done
    s = done[~done["ts_utc"].isin(b["ts_utc"])] if not (b.empty or done.empty) else done
    if not s.empty:
        stamps = pd.DatetimeIndex(s["ts_utc"])
        bb.fallback |= set(stamps)
        bb.stats["fallback"] += len(s)
        eng._dq_pending.append(DQEvent(ts_utc=stamps[0], check="broker_bar_late", severity="warning",
                                       detail=f"{len(s)} minute(s) {stamps[0]}..{stamps[-1]} from sampled ticks: the "
                                              "broker's 1m bar was late or missing; replaced when it arrives"))
    bb.stats["broker"] += len(b)
    ref = eng.bars_1m if not eng.bars_1m.empty else done
    parts = [x for x in (_like(b.reset_index(drop=True), ref), s) if not x.empty]
    if not parts:
        return done.iloc[:0]
    return pd.concat(parts).sort_values("ts_utc").reset_index(drop=True)


def rebuild(eng: Engine, until: pd.Timestamp | None = None) -> None:
    """Incremental: only ticks since the last completed minute are aggregated; bars accumulate. Without `until`
    the latest tick's minute may still receive ticks and stays open; with it (a bar close) every minute ending at
    or before `until` is complete and none starting at or after it is built. With broker bars on, a minute the broker
    has finished is complete whatever the engine's quotes show, and the broker's bar is the one that joins."""
    on = eng.cfg.broker_bars
    bb = eng.broker_m1
    through = bb.through if on else None
    if not eng.ticks and through is None:
        return
    cutoff = until.floor("min") if until is not None else None
    if cutoff is None and eng.ticks and eng.ticks[0].ts_utc.floor("min") == eng.ticks[-1].ts_utc.floor("min") \
            and (through is None or through <= eng.ticks[0].ts_utc.floor("min")):
        return      # every tick is in the current minute (ticks arrive in time order): no minute has completed
    if eng.ticks:
        tdf = pd.DataFrame({"ts_utc": [x.ts_utc for x in eng.ticks], "bid": [x.bid for x in eng.ticks],
                            "ask": [x.ask for x in eng.ticks]})
        new = ticks_to_1m(tdf, DEFAULT_SESSIONS)
    else:
        new = pd.DataFrame(columns=BAR_COLUMNS)
    if new.empty and not on:
        return
    if cutoff is None:
        cuts = [x for x in (None if new.empty else pd.Timestamp(new["ts_utc"].iloc[-1]), through) if x is not None]
        if not cuts:
            return
        cutoff = max(cuts)                            # the current minute may still receive ticks
    done = new[new["ts_utc"] < cutoff] if not new.empty else new
    if on:
        done = _merge(eng, bb, done, cutoff)
    if not done.empty:
        eng._check_new_bars(done)
        eng.bars_1m = append_bars(eng, done)
        history.queue_minutes(eng, done)
    eng.ticks = [x for x in eng.ticks if x.ts_utc >= cutoff]


def append_bars(eng: Engine, done: pd.DataFrame) -> pd.DataFrame:
    """bars_1m + newly completed minutes, de-duplicated on ts_utc (last wins) and capped at max_bars_in_memory.
    When the buffer is one this method built (so already duplicate-free) and every new minute is later than its
    last bar, de-duplication cannot drop a row and is skipped: the same frame without hashing 60000 stamps on
    every 10-second refresh."""
    b, cap = eng.bars_1m, eng.cfg.max_bars_in_memory
    if b.empty:
        out = done.reset_index(drop=True)
    else:
        new_ts = epoch_ns(pd.DatetimeIndex(done["ts_utc"]))
        clean = b is eng._bars_clean and len(new_ts) and bool((np.diff(new_ts) > 0).all()) \
            and new_ts[0] > epoch_ns(pd.DatetimeIndex(b["ts_utc"].iloc[-1:]))[0]
        joined = pd.concat([b, done])
        out = (joined if clean else joined.drop_duplicates("ts_utc", keep="last")).tail(cap).reset_index(drop=True)
    eng._bars_clean = out          # duplicate-free by construction on every path above
    return out


def published(eng: Engine) -> dict[str, object]:
    """engine_<account>.json `broker_bars_1m`: whether completed minutes come from the broker, and the counts (minutes
    joined from the broker / from sampled ticks, replaced on arrival, never arrived; fetches and failed fetches)."""
    return {"broker_bars_1m": {"on": eng.cfg.broker_bars, **eng.broker_m1.stats,
                               "awaiting": len(eng.broker_m1.fallback)}}
