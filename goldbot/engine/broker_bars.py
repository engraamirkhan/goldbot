"""The engine's completed 1m bars are the broker's own M1 bars (BACKLOG "engine 1m close lag").

Built from sampled `last_tick` quotes, the engine missed each minute's last ticks: about 1-2% of signals differed and
the D12 reconcile found 83 closes a day off the broker's. Design rules (trading-safety review of c77be48d):
- A completed minute is the broker's bar: its rates call (`get_bars(symbol, "1m", FETCH_N)`, MT5 copy_rates_from_pos),
  converted exactly as the history fill (`history.rates_to_bars`), once when each in-session minute closes (the first
  pass with a tick of a later minute, or the clock past the close plus GRACE).
- The tick thread never waits on the bridge: the call runs on a `Fetcher` thread through a client of its own (one
  attempt, FETCH_TIMEOUT_S; the engine's client is never shared across threads), one call at a time; the tick thread
  only starts a call and applies a finished one at a later pass, after the exits. A failed or malformed reply is a
  logged warning, never a tick error; the next try backs off exponentially (RETRY doubling to BACKOFF_MAX, measured
  from when the failure was applied). Without a fetcher (a terminal on this host: one MT5 connection per process)
  nothing is fetched and the bars are the sampled ones.
- A decision close waits at most HOLD past close + GRACE, while the fetch for its last minute has not answered, so it
  decides on the broker's bar (`hold`). Only entries wait: the close's time exits and its account refresh (stage
  machine, kill switch, Friday cut) run when the hold starts, and every pass runs its tick exits as usual.
- No look-ahead: a broker bar is taken only when the call started at or after its visible_at (open + 1 min, research's
  rule, unchanged) and the broker had finished it (a later bar in the reply, a tick at or after its close, or the clock
  past close + GRACE): the terminal's forming bar never joins. FETCH_N < 60, so a server zone off by a DST hour puts
  every bar of a reply either in the future (refused) or before the bars already held (ignored), never on a minute
  being built. A bar with a NaN, a high below its low or open/close, a non-positive price or bid above ask is refused
  (`broker_bar_refused`); so is a replacement that `bar_errors` flags.
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
import threading
import time
from typing import TYPE_CHECKING, Any, Callable

import numpy as np
import pandas as pd

from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.data.quality import DQEvent, bar_errors
from goldbot.data.resample import BAR_COLUMNS, ticks_to_1m
from goldbot.data.timeutil import epoch_ns
from goldbot.engine import history

if TYPE_CHECKING:
    from goldbot.engine.runner import Engine
    from goldbot.execution.broker import Tick

log = logging.getLogger(__name__)

MINUTE = pd.Timedelta(minutes=1)
GRACE = pd.Timedelta(seconds=1.5)       # = runner.BAR_CLOSE_GRACE_S (design D10), so a decision close finds its bar
HOLD = pd.Timedelta(seconds=3)          # a decision close waits at most this past close + GRACE for its last bar
FETCH_N = 16                            # bars per rates call: covers GIVE_UP's retries; < 60 (a DST hour never matches)
FETCH_TIMEOUT_S = 2.0                   # the fetcher's own bridge client: one attempt, this timeout
RETRY = pd.Timedelta(seconds=5)         # a late bar is asked again this often (engine clock); doubled per failed call
BACKOFF_MAX = pd.Timedelta(seconds=60)
GIVE_UP = pd.Timedelta(minutes=10)      # after this the sampled minute stands for good (counted `never_arrived`)
KEEP = pd.Timedelta(minutes=30)         # fetched broker minutes kept for joining
ERROR_LOG_S = 300.0                     # a failing rates call is logged at most this often
_PRICES = ["bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high", "ask_low", "ask_close"]


class Job:
    """One rates call: the request's context (engine clock and tick stamp when it started, the latest closed minute
    it covers) and its outcome (converted bars, or the error's type name). `done` is set when it has finished."""

    def __init__(self, started: pd.Timestamp, tick_ts: pd.Timestamp, upto: pd.Timestamp, point: float | None):
        self.started, self.tick_ts, self.upto, self.point = started, tick_ts, upto, point
        self.bars: pd.DataFrame | None = None
        self.error: str | None = None
        self.done = threading.Event()

    def run(self, client: Any, symbol: str) -> None:
        try:
            if self.point is None:
                self.point = float(client.symbol_info(symbol).point)
            self.bars = history.rates_to_bars(client.get_bars(symbol, "1m", FETCH_N), self.point)
        except Exception as exc:          # noqa: BLE001 - a bad reply is a warning applied on the tick thread
            self.error = type(exc).__name__
        finally:
            self.done.set()


class Fetcher:
    """One rates call at a time, each on a daemon thread, through `client` (its own: production gives a RemoteBroker
    with one attempt and FETCH_TIMEOUT_S). The tick thread only starts a call and collects a finished one."""

    def __init__(self, client: Any):
        self.client = client
        self.job: Job | None = None

    def busy(self) -> bool:
        return self.job is not None and not self.job.done.is_set()

    def start(self, job: Job, symbol: str) -> None:
        self.job = job
        self._spawn(lambda: job.run(self.client, symbol))

    def _spawn(self, fn: Callable[[], None]) -> None:
        threading.Thread(target=fn, name="broker-m1", daemon=True).start()

    def collect(self) -> Job | None:
        job = self.job
        if job is None or not job.done.is_set():
            return None
        self.job = None
        return job


class BrokerBars:
    """Runtime state (not persisted: a restart reads the store, whose minutes are the last copy written)."""

    def __init__(self) -> None:
        self.fetcher: Fetcher | None = None                 # production: set by goldbot/ops/services/engine.py
        self.got = pd.DataFrame(columns=BAR_COLUMNS)        # finished broker minutes fetched, newest KEEP
        self.asked: pd.Timestamp | None = None              # open of the latest closed minute a fetch was due for
        self.answered: pd.Timestamp | None = None           # latest closed minute a finished call covered
        self.wanted: set[pd.Timestamp] = set()              # closed in-session minutes still without their broker bar
        self.retry_at: pd.Timestamp | None = None
        self.fails = 0                                      # failed calls in a row (the backoff exponent)
        self.held: pd.Timestamp | None = None               # a decision close waiting for its last broker bar
        self.exits_for: pd.Timestamp | None = None          # the held close whose exit-side work has run
        self.fallback: set[pd.Timestamp] = set()            # minutes in bars_1m from sampled ticks, awaiting the broker
        self.point: float | None = None
        self.stats = {"fetches": 0, "fetch_errors": 0, "broker": 0, "fallback": 0, "replaced": 0, "refused": 0,
                      "never_arrived": 0}
        self._err_logged = -float("inf")

    @property
    def through(self) -> pd.Timestamp | None:
        """End of the latest finished broker minute fetched: every minute before it is complete."""
        return None if self.got.empty else pd.Timestamp(self.got["ts_utc"].iloc[-1]) + MINUTE


def poll(eng: Engine, t: Tick) -> None:
    """After the pass's exits: apply a finished call; start one when a minute has closed, and again after RETRY
    (backed off while calls fail) while a closed minute the engine has quotes for still lacks its broker bar (up to
    GIVE_UP). Never waits on the call."""
    bb = eng.broker_m1
    if not eng.cfg.broker_bars or bb.fetcher is None:
        return
    now = max(t.ts_utc, eng._now(t))
    _collect(eng, bb, now)
    closed = max(t.ts_utc.floor("min"), (now - GRACE).floor("min")) - MINUTE
    new = bb.asked is None or closed > bb.asked
    if new:
        start = closed if bb.asked is None else max(bb.asked + MINUTE, closed - (FETCH_N - 1) * MINUTE)
        minutes = pd.date_range(start, closed, freq="min")
        bb.wanted |= set(minutes[np.asarray(DEFAULT_SESSIONS.is_open(minutes), dtype=bool)])
        bb.asked = closed
    _expire(bb, now)
    due = bb.retry_at is None or now >= bb.retry_at
    if bb.wanted and not bb.fetcher.busy() and (due or (new and bb.fails == 0)):
        bb.stats["fetches"] += 1
        bb.fetcher.start(Job(now, t.ts_utc, closed, bb.point), eng.cfg.symbol)
        _collect(eng, bb, now)                      # a call that has already finished (an in-process client)


def hold(eng: Engine, due: pd.Timestamp | None, t: Tick) -> pd.Timestamp | None:
    """`due` (the decision close `_due_close` found), or None while that close waits for its last minute's broker
    bar: at most HOLD past close + GRACE, and only while the call covering that minute has not answered. The engine
    keeps the close pending (`held`), so a later pass decides it; exits never wait."""
    bb = eng.broker_m1
    bb.held = None
    if due is None:
        return due
    if awaiting(eng, due - MINUTE, max(t.ts_utc, eng._now(t))):
        if bb.exits_for != due:           # once per held close, before it waits
            bb.exits_for = due
            _held_exits(eng, t)
        bb.held = due
        return None
    return due


def awaiting(eng: Engine, minute: pd.Timestamp, now: pd.Timestamp) -> bool:
    """Whether a decision on the in-session `minute` (its open) waits for that minute's broker bar: broker bars on,
    the bar not fetched, no finished call has covered the minute, and `now` within HOLD past its close + GRACE. One
    rule for the engine's decision closes (`hold`) and the exploration minute pass (`exploration._minutes`)."""
    bb = eng.broker_m1
    if not eng.cfg.broker_bars or bb.fetcher is None:
        return False
    have = not bb.got.empty and bool((bb.got["ts_utc"] == minute).any())
    return not have and (bb.answered is None or bb.answered < minute) and now < minute + MINUTE + GRACE + HOLD \
        and bool(DEFAULT_SESSIONS.is_open(pd.DatetimeIndex([minute]))[0])


def fetched(eng: Engine, through: pd.Timestamp) -> pd.DataFrame:
    """The broker's finished minutes fetched so far (the last KEEP) that close at or before `through`, with bars_1m's
    dtypes; empty with broker bars off. The exploration minute pass lays them over the sampled minutes it builds from
    the engine's quotes, so every minute it has a broker bar for is the broker's (prereg-2026-10-27-scalp-volume.md
    section 0: "v = the bar's tick volume (... MT5 `tick_volume` live)", never the engine's count of sampled quotes)."""
    bb = eng.broker_m1
    if not eng.cfg.broker_bars or bb.got.empty:
        return bb.got.iloc[:0]
    got = bb.got[pd.DatetimeIndex(bb.got["ts_utc"]) + MINUTE <= through]
    return _like(got.reset_index(drop=True), eng.bars_1m)


def _held_exits(eng: Engine, t: Tick) -> None:
    """Only entries wait for the broker's bar (trading-safety LOW on 432fbba8): when a close is held, its exit-side
    work runs at once. A live trade whose bars_held reaches max_bars with this close is closed `time_exit` (as
    `_manage_open` would; that then skips it, closed or already due), and the account is refreshed: the stage
    machine, the kill switch and the Friday cut (`_server_clock`). Each step is guarded like a tick step."""
    def time_exits() -> None:
        positions = eng._read_positions("time exits of a held close run at the close")
        if positions is None:
            return
        live = {p.position_id for p in positions}
        for pid, tr in list(eng.open.items()):
            if pid in live and pid not in eng._close_due and tr.bars_held + 1 >= tr.max_bars:
                eng._try_close(pid, "time_exit", None)
    eng._guard(time_exits)
    eng._guard(lambda: eng._refresh_account(t))


def _expire(bb: BrokerBars, now: pd.Timestamp) -> None:
    bb.wanted = {m for m in bb.wanted if now - (m + MINUTE) < GIVE_UP}
    gone = [m for m in bb.fallback if now - (m + MINUTE) >= GIVE_UP]
    bb.fallback -= set(gone)
    if gone:
        bb.stats["never_arrived"] += len(gone)
        log.warning("broker 1m bar never arrived for %d minute(s) from %s: the sampled bars stand", len(gone), min(gone))


def _failed(bb: BrokerBars, now: pd.Timestamp, why: str) -> None:
    """A failed or malformed call: counted, logged at most every ERROR_LOG_S, the next try backed off from now (the
    time the failure is applied, so a slow call never makes the next one immediate)."""
    bb.stats["fetch_errors"] += 1
    bb.fails += 1
    bb.retry_at = now + min(RETRY * 2 ** min(bb.fails - 1, 6), BACKOFF_MAX)
    mono = time.monotonic()
    if mono - bb._err_logged >= ERROR_LOG_S:
        bb._err_logged = mono
        log.warning("broker 1m bars not read (%s): completed minutes fall back to the sampled ticks", why)


def _sound(bars: pd.DataFrame) -> np.ndarray:
    """Rows a bar can be: finite positive prices and tick count, low <= open, close <= high, ask >= bid, spread >= 0."""
    p = bars[_PRICES].to_numpy(float)
    ok = np.isfinite(p).all(axis=1) & (p > 0).all(axis=1) & np.isfinite(bars["tick_count"].to_numpy(float))
    for side in ("bid", "ask"):
        o, h, lo, c = (bars[f"{side}_{x}"].to_numpy(float) for x in ("open", "high", "low", "close"))
        ok &= (lo <= np.minimum(o, c)) & (h >= np.maximum(o, c))
    ok &= bars["ask_close"].to_numpy(float) >= bars["bid_close"].to_numpy(float)
    ok &= bars["spread_mean"].to_numpy(float) >= 0
    return np.asarray(ok, dtype=bool)


def _refuse(eng: Engine, bb: BrokerBars, bars: pd.DataFrame, why: str) -> None:
    bb.stats["refused"] += len(bars)
    stamps = pd.DatetimeIndex(bars["ts_utc"])
    eng._dq_pending.append(DQEvent(ts_utc=stamps[0], check="broker_bar_refused", severity="warning",
                                   detail=f"{len(bars)} broker 1m bar(s) from {stamps[0]} refused ({why}): the sampled "
                                          "bars stand"))


def _collect(eng: Engine, bb: BrokerBars, now: pd.Timestamp) -> None:
    job = bb.fetcher.collect() if bb.fetcher is not None else None
    if job is None:
        return
    bb.answered = job.upto if bb.answered is None else max(bb.answered, job.upto)
    if job.error is not None or job.bars is None:
        _failed(bb, now, job.error or "no reply")
        return
    try:
        _apply(eng, bb, job, now)
    except Exception as exc:              # noqa: BLE001 - a malformed reply is a warning, never a tick error
        _failed(bb, now, f"malformed reply: {type(exc).__name__}")


def _apply(eng: Engine, bb: BrokerBars, job: Job, now: pd.Timestamp) -> None:
    bars = job.bars if job.bars is not None else pd.DataFrame(columns=BAR_COLUMNS)
    if not bars.empty:
        ts = pd.DatetimeIndex(bars["ts_utc"])
        vis = ts + MINUTE                       # no look-ahead: judged at the call's start, not at its arrival
        finished = (vis <= job.started) & ((ts < ts.max()) | (vis <= job.tick_ts) | (vis + GRACE <= job.started))
        bars = bars[np.asarray(finished, dtype=bool)].reset_index(drop=True)
    if not bars.empty:
        ok = _sound(bars)
        if not ok.all():
            _refuse(eng, bb, bars[~ok], "NaN, non-positive, high below low/open/close or bid above ask")
            bars = bars[ok].reset_index(drop=True)
    bb.fails, bb.retry_at, bb.point = 0, now + RETRY, job.point
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
    errs = bar_errors(src.reset_index())
    if errs or not _sound(src).all():           # never swap a sound sampled bar for a bad one
        _refuse(eng, bb, src.reset_index(), "; ".join(e.check for e in errs) or "unsound bar")
        bb.fallback -= set(pd.DatetimeIndex(src.index))     # the sampled bars stand for good
        return
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
