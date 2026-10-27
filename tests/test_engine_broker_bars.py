"""The engine's completed 1m bars are the broker's own M1 bars (engine/broker_bars.py).

Built from sampled `last_tick` quotes, the engine missed each minute's last ticks: ~1-2% of signals differed and the
D12 reconcile found 83 closes a day off the broker's. Completed minutes now come from the broker's rates call, fetched
once each minute closes; the sampled bar is only the provisional current minute and the fallback when the broker's bar
is late or missing (a dq warning, replaced on arrival, never revising a decision already taken). A minute joins only
once the broker's bar has been fetched at or after its close (visible_at = open + 1 min, as research)."""
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.data.resample import ticks_to_1m
from goldbot.data.store import Store
from goldbot.data.synthetic import synthetic_ticks
from goldbot.data.timeutil import NY_PLUS_7, server_wall
from goldbot.engine import ConstantModel, Engine, EngineConfig, broker_bars, history, runner
from goldbot.engine.history import LIVE_TABLE
from goldbot.execution import mt5_adapter
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker
from goldbot.specialists import SPECIALISTS

POINT = 0.01


def _rates(ticks: pd.DataFrame) -> pd.DataFrame:
    """What MT5 copy_rates returns for `ticks`: bid OHLC, tick volume and the close's spread in points."""
    b = ticks_to_1m(ticks, DEFAULT_SESSIONS)
    return pd.DataFrame({"ts_utc": b["ts_utc"], "open": b["bid_open"], "high": b["bid_high"], "low": b["bid_low"],
                         "close": b["bid_close"], "tick_count": b["tick_count"].astype(int),
                         "spread": ((b["ask_close"] - b["bid_close"]) / POINT).round().astype(int)})


def _as_bars(r: pd.DataFrame) -> pd.DataFrame:
    return history.rates_to_bars(r, POINT).set_index("ts_utc")


class Terminal(PaperBroker):
    """The broker's terminal: it holds every tick (`truth`), the engine samples only some. Its rates call
    (copy_rates_from_pos(0, n)) returns its latest n M1 bars up to the latest quote, the forming bar included;
    `withhold` minutes are not there yet (a late bar) until released; `fail` makes the call raise."""

    def __init__(self, truth: pd.DataFrame, withhold: tuple[pd.Timestamp, ...] = (), ahead_s: float = 0.0):
        super().__init__(equity=10_000)
        self.point = POINT
        self.truth = truth.reset_index(drop=True)
        self.ns = self.truth["ts_utc"].to_numpy()
        self.now: pd.Timestamp | None = None
        self.withhold, self.ahead, self.fail = set(withhold), pd.Timedelta(seconds=ahead_s), False
        self.corrupt: set[pd.Timestamp] = set()           # minutes whose bar comes back with its high below its low
        self.malformed = False
        self.calls: list[pd.Timestamp] = []

    def on_tick(self, tick: Tick) -> None:
        super().on_tick(tick)
        self.now = tick.ts_utc

    def get_bars(self, symbol: str, tf: str, n: int) -> pd.DataFrame:
        assert tf == "1m" and self.now is not None
        self.calls.append(self.now)
        if self.fail:
            raise ConnectionError("bridge down")
        hi = self.now + self.ahead
        lo = hi.floor("min") - pd.Timedelta(minutes=n + 5)
        seen = self.truth[(self.truth["ts_utc"] >= lo) & (self.truth["ts_utc"] <= hi)]
        if self.malformed:
            return pd.DataFrame({"ts_utc": [hi], "close": ["n/a"]})
        r = _rates(seen)
        bad = r["ts_utc"].isin(self.corrupt)
        r.loc[bad, "high"] = r.loc[bad, "low"] - 1.0
        return r[~r["ts_utc"].isin(self.withhold)].tail(n).reset_index(drop=True)


def _sampled(ticks: pd.DataFrame) -> pd.DataFrame:
    """The engine's view: every quote but the last of each minute (the poll misses the minute's final ticks)."""
    last = ticks.groupby(ticks["ts_utc"].dt.floor("min")).tail(1).index
    return ticks.drop(index=last).reset_index(drop=True)


class Inline(broker_bars.Fetcher):
    """The fetcher with its call run at once (a deterministic stand-in for the thread); `paused` keeps calls until
    `release` (a call still in flight)."""

    def __init__(self, client: Any):
        super().__init__(client)
        self.paused = False
        self.kept: list[Any] = []

    def _spawn(self, fn: Any) -> None:
        if self.paused:
            self.kept.append(fn)
        else:
            fn()

    def release(self) -> None:
        self.paused = False
        for fn in self.kept:
            fn()
        self.kept = []


def _engine(tmp_path: Path, broker: PaperBroker, fetcher: broker_bars.Fetcher | None = None, **cfg: Any) -> Engine:
    spec = SPECIALISTS["session_open"](min_body_pct=0.0, asia_range_max_atr_d=99.0)
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", approval_mode="auto", state_dir=str(tmp_path),
                              data_root=str(tmp_path / "data"), broker_bars=True, **cfg),
                 broker, [spec], {"session_open": ConstantModel(p=0.65)})
    eng.broker_m1.fetcher = fetcher or Inline(broker)
    return eng


def _tick(ts: Any, bid: float, ask: float) -> Tick:
    return Tick(ts_utc=pd.Timestamp(ts), bid=float(bid), ask=float(ask))


def _feed(eng: Engine, ticks: pd.DataFrame, check: Any = None) -> list[dict]:
    out: list[dict] = []
    for ts, bid, ask in zip(ticks["ts_utc"], ticks["bid"].to_numpy(float), ticks["ask"].to_numpy(float)):
        t = _tick(ts, bid, ask)
        out += eng.on_tick(t)
        if check is not None:
            check(eng, t)
    return out


def _ticks(start: str, end: str, per_minute: int, seed: int) -> pd.DataFrame:
    """Synthetic quotes with distinct stamps (two quotes in one millisecond have no defined last)."""
    return synthetic_ticks(start, end, ticks_per_minute=per_minute, seed=seed).drop_duplicates("ts_utc").reset_index(drop=True)


TICKS = _ticks("2025-03-10 08:00", "2025-03-10 11:00", 4, 3)


def _no_look_ahead(eng: Engine, t: Tick) -> None:
    if not eng.bars_1m.empty:
        assert pd.Timestamp(eng.bars_1m["visible_at"].max()) <= t.ts_utc


def test_completed_minutes_are_the_brokers_m1_bars_not_the_sampled_ones(tmp_path):
    term = Terminal(TICKS)
    eng = _engine(tmp_path, term)
    _feed(eng, _sampled(TICKS), _no_look_ahead)
    eng._rebuild_bars()
    got = eng.bars_1m.set_index("ts_utc")
    truth = _as_bars(_rates(TICKS)).loc[got.index]
    for c in ("bid_open", "bid_high", "bid_low", "bid_close", "ask_close", "tick_count"):
        assert np.allclose(got[c].to_numpy(float), truth[c].to_numpy(float)), c
    sampled = ticks_to_1m(_sampled(TICKS)).set_index("ts_utc").loc[got.index]
    assert (sampled["bid_close"] != got["bid_close"]).mean() > 0.5        # the lag this removes
    assert len(got) >= 170 and got.index.is_monotonic_increasing and got.index.is_unique
    assert ((pd.to_datetime(got["visible_at"], utc=True) - got.index) == pd.Timedelta(minutes=1)).all()
    assert len(term.calls) <= len(got) + 2                                 # once per closed minute, no retries
    assert eng.broker_m1.stats["fallback"] == 0 and not eng._dq_errors()


def test_the_forming_bar_and_a_bar_from_a_terminal_clock_ahead_never_join_before_their_close(tmp_path):
    """No look-ahead: a bar joins only when fetched at or after its visible_at; the terminal's forming bar (and one a
    fast server clock already shows) is never taken early."""
    term = Terminal(TICKS, ahead_s=45)
    eng = _engine(tmp_path, term)
    _feed(eng, _sampled(TICKS), _no_look_ahead)
    assert not eng.bars_1m.empty


def test_a_late_broker_bar_falls_back_to_the_sampled_bar_and_is_replaced_on_arrival(tmp_path):
    late = pd.Timestamp("2025-03-10 09:14", tz="UTC")
    term = Terminal(TICKS, withhold=(late,))
    eng = _engine(tmp_path, term)
    s = _sampled(TICKS)
    _feed(eng, s[s["ts_utc"] < late + pd.Timedelta(minutes=1, seconds=40)], _no_look_ahead)
    eng._rebuild_bars()
    row = eng.bars_1m.set_index("ts_utc").loc[late]
    fallback = ticks_to_1m(s[(s["ts_utc"] >= late) & (s["ts_utc"] < late + pd.Timedelta(minutes=1))]).iloc[0]
    assert row["bid_close"] == fallback["bid_close"]                          # the sampled bar stands in
    assert late in eng.broker_m1.fallback
    assert any(e.check == "broker_bar_late" for e in eng._dq_bar_events)     # dq flag (warning, entries not blocked)
    assert not eng._dq_errors()
    retries = len(term.calls)
    term.withhold.clear()                                                    # the broker's bar arrives
    _feed(eng, s[(s["ts_utc"] >= late + pd.Timedelta(minutes=1, seconds=40)) & (s["ts_utc"] < late + pd.Timedelta(minutes=3))])
    row = eng.bars_1m.set_index("ts_utc").loc[late]
    truth = _as_bars(_rates(TICKS)).loc[late]
    assert row["bid_close"] == truth["bid_close"] and row["tick_count"] == truth["tick_count"]
    assert late not in eng.broker_m1.fallback and eng.broker_m1.stats["replaced"] == 1
    assert any(e.check == "broker_bar_replaced" for e in eng._dq_pending + eng._dq_bar_events)
    assert retries > 2                                                       # retried while it was late
    assert eng.bars_1m["ts_utc"].is_unique and eng.bars_1m["ts_utc"].is_monotonic_increasing


def test_a_late_bar_replaced_after_a_decision_leaves_that_decision_as_journalled(tmp_path):
    live = pd.Timestamp("2025-03-13", tz="UTC")
    ticks = _ticks("2025-03-03", "2025-03-13 09:30", 2, 8)
    Store(tmp_path / "data").append("bars_1m", ticks_to_1m(ticks[ticks["ts_utc"] < live]), source="dukascopy")
    late = pd.Timestamp("2025-03-13 08:29", tz="UTC")                       # the last minute before the 08:30 close
    term = Terminal(ticks, withhold=(late,))
    eng = _engine(tmp_path, term)
    eng.warm_start(live)
    s = _sampled(ticks[ticks["ts_utc"] >= live])
    before = _feed(eng, s[s["ts_utc"] < late + pd.Timedelta(minutes=2)])
    assert eng._closed_through == late + pd.Timedelta(minutes=1)
    assert [d["action"] for d in before] == ["executed:auto"] and before[0]["ts"] == "2025-03-13T08:30:00+00:00"
    taken = [dict(d) for d in eng.decisions]
    used = eng.bars_1m.set_index("ts_utc").loc[late, "bid_close"]
    term.withhold.clear()
    _feed(eng, s[(s["ts_utc"] >= late + pd.Timedelta(minutes=2)) & (s["ts_utc"] < late + pd.Timedelta(minutes=20))])
    assert eng.decisions[:len(taken)] == taken                               # the decision stands as taken
    assert eng.bars_1m.set_index("ts_utc").loc[late, "bid_close"] != used    # the window now holds the broker's bar
    events = Store(tmp_path / "data").read("dq_events", source="icm-demo")
    assert {"broker_bar_late", "broker_bar_replaced"} <= set(events["check"])  # both journalled with the bars


def test_a_restarted_engine_loads_the_replaced_broker_bars_and_rejoins_without_duplicates(tmp_path):
    late = pd.Timestamp("2025-03-10 09:14", tz="UTC")
    term = Terminal(TICKS, withhold=(late,))
    s = _sampled(TICKS)
    first = _engine(tmp_path, term)
    _feed(first, s[s["ts_utc"] < late + pd.Timedelta(minutes=1, seconds=40)])
    term.withhold.clear()
    stop = pd.Timestamp("2025-03-10 09:30:30", tz="UTC")
    _feed(first, s[(s["ts_utc"] >= late + pd.Timedelta(minutes=1, seconds=40)) & (s["ts_utc"] < stop)])
    first._rebuild_bars()
    assert first.history.writer is not None and first.history.writer.join(30)
    stored = Store(tmp_path / "data").read(LIVE_TABLE, source="icm").set_index("ts_utc")
    assert stored.loc[late, "bid_close"] == _as_bars(_rates(TICKS)).loc[late, "bid_close"]   # the last copy wins

    second = _engine(tmp_path, term)
    second.warm_start(stop)
    assert second.bars_1m.set_index("ts_utc").loc[late, "bid_close"] == stored.loc[late, "bid_close"]
    _feed(second, s[s["ts_utc"] >= stop], _no_look_ahead)
    second._rebuild_bars()
    b = second.bars_1m
    assert b["ts_utc"].is_unique and b["ts_utc"].is_monotonic_increasing and not second._dq_errors()
    after = b[b["ts_utc"] >= stop.floor("min")].set_index("ts_utc")
    assert np.allclose(after["bid_close"].to_numpy(float), _as_bars(_rates(TICKS)).loc[after.index, "bid_close"].to_numpy(float))


def test_in_a_quiet_market_the_minute_is_fetched_once_the_clock_passes_its_close_plus_the_grace(tmp_path):
    """No quote of the next minute: the live clock past close + GRACE fetches it; before the grace nothing is asked."""
    term = Terminal(TICKS)
    eng = _engine(tmp_path, term, live_clock=True)
    s = _sampled(TICKS)
    part = s[s["ts_utc"] < pd.Timestamp("2025-03-10 08:10:40", tz="UTC")]
    clock = {"now": pd.Timestamp(part["ts_utc"].iloc[0])}
    eng.clock = lambda: clock["now"]

    def follow(e: Engine, t: Tick) -> None:
        clock["now"] = t.ts_utc
    _feed(eng, part, follow)
    last = part.iloc[-1]
    minute = pd.Timestamp(last["ts_utc"]).floor("min")
    calls = len(term.calls)
    clock["now"] = minute + pd.Timedelta(minutes=1, seconds=1)               # closed, inside the grace
    eng.on_tick(_tick(last["ts_utc"], last["bid"], last["ask"]))             # the terminal repeats its quote
    assert len(term.calls) == calls
    clock["now"] = minute + pd.Timedelta(minutes=1) + broker_bars.GRACE
    eng.on_tick(_tick(last["ts_utc"], last["bid"], last["ask"]))
    assert len(term.calls) == calls + 1
    eng._rebuild_bars()
    row = eng.bars_1m.set_index("ts_utc").loc[minute]
    assert row["bid_close"] == _as_bars(_rates(TICKS[TICKS["ts_utc"] <= last["ts_utc"]])).loc[minute, "bid_close"]


def test_a_failing_rates_call_keeps_the_sampled_minutes_and_never_blocks_entries(tmp_path):
    term = Terminal(TICKS)
    term.fail = True
    eng = _engine(tmp_path, term)
    s = _sampled(TICKS)
    _feed(eng, s[s["ts_utc"] < pd.Timestamp("2025-03-10 08:30", tz="UTC")])
    eng._rebuild_bars()
    assert len(eng.bars_1m) >= 28 and eng.broker_m1.stats["fetch_errors"] > 0
    assert not eng.state.tick_error and not eng._dq_errors()


def test_paper_and_replays_keep_the_sampled_bars_when_broker_bars_are_off(tmp_path):
    term = Terminal(TICKS)
    spec = SPECIALISTS["session_open"](min_body_pct=0.0, asia_range_max_atr_d=99.0)
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path)), term, [spec],
                 {"session_open": ConstantModel(p=0.65)})
    _feed(eng, _sampled(TICKS))
    assert term.calls == []


def test_the_fetch_grace_is_the_bar_close_grace():
    assert broker_bars.GRACE == pd.Timedelta(seconds=runner.BAR_CLOSE_GRACE_S)


# ------------------------------------------------------------------------------------------------ DST
def _fake_mt5(rates: pd.DataFrame, tz: str) -> SimpleNamespace:
    """copy_rates_from_pos on a terminal whose clock is `tz` (MT5 stamps bars with the server wall clock)."""
    wall = server_wall(pd.DatetimeIndex(rates["ts_utc"]), tz)
    secs = (wall - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1)
    raw = pd.DataFrame({"time": np.asarray(secs, dtype=np.int64), "open": rates["open"], "high": rates["high"],
                        "low": rates["low"], "close": rates["close"], "tick_volume": rates["tick_count"],
                        "spread": rates["spread"], "real_volume": 0})
    state: dict[str, Any] = {"raw": raw}
    return SimpleNamespace(initialize=lambda **k: True, symbol_select=lambda s, on: True, last_error=lambda: (1, "e"),
                           shutdown=lambda: None, TIMEFRAME_M1=1, state=state, symbol_info=lambda s: SimpleNamespace(visible=True, point=POINT),
                           copy_rates_from_pos=lambda sym, tf, pos, n: state["raw"].tail(n).to_records(index=False))


class Mt5Terminal(Terminal):
    """The terminal reached through the real MT5 adapter (server clock -> UTC)."""

    def __init__(self, truth: pd.DataFrame, adapter: Any, fake: SimpleNamespace, tz: str):
        super().__init__(truth)
        self.adapter, self.fake, self.tz = adapter, fake, tz

    def get_bars(self, symbol: str, tf: str, n: int) -> pd.DataFrame:
        assert self.now is not None
        self.calls.append(self.now)
        seen = self.truth[(self.truth["ts_utc"] >= self.now.floor("min") - pd.Timedelta(minutes=n + 5))
                          & (self.truth["ts_utc"] <= self.now)]
        self.fake.state.update(_fake_mt5(_rates(seen), self.tz).state)
        return self.adapter.get_bars(symbol, tf, n)


@pytest.mark.parametrize("zone,joins", [(NY_PLUS_7, True), ("Europe/Athens", False)])
def test_in_the_us_eu_dst_gap_week_the_brokers_bars_land_on_their_utc_minutes(tmp_path, monkeypatch, zone, joins):
    """2026-10-28 (EU back on winter time, US not yet): the ICM server runs NY+7 (UTC+3). Converted with the right
    zone the broker's bars replace the sampled bars of the same UTC minutes; with the wrong one (Athens, UTC+2) every
    bar would land an hour in the future, so none joins and the sampled minutes stand (no look-ahead either way)."""
    ticks = _ticks("2026-10-28 09:00", "2026-10-28 09:40", 4, 5)
    fake = _fake_mt5(_rates(ticks.iloc[:4]), NY_PLUS_7)
    monkeypatch.setattr(mt5_adapter, "mt5", fake)
    adapter = mt5_adapter.MT5Broker(terminal_path="", login=None, password=None, server="s", server_tz=zone,
                                    symbol="XAUUSD", account_label="icm-demo", verify=None)
    term = Mt5Terminal(ticks, adapter, fake, NY_PLUS_7)
    eng = _engine(tmp_path, term)
    _feed(eng, _sampled(ticks), _no_look_ahead)
    eng._rebuild_bars()
    got = eng.bars_1m.set_index("ts_utc")
    assert len(got) >= 38
    truth = _as_bars(_rates(ticks)).loc[got.index, "bid_close"]
    sampled = ticks_to_1m(_sampled(ticks)).set_index("ts_utc").loc[got.index, "bid_close"]
    if joins:
        assert np.allclose(got["bid_close"].to_numpy(float), truth.to_numpy(float))
        assert eng.broker_m1.stats["fallback"] == 0
    else:
        assert np.allclose(got["bid_close"].to_numpy(float), sampled.to_numpy(float))
        assert eng.broker_m1.stats["broker"] == 0


# ------------------------------------------------------------------------------------- the tick thread never waits
class Hung(Terminal):
    """A bridge that does not answer the rates call until `gate` is set (the safety review's 10 s x 2 stall)."""

    def __init__(self, truth: pd.DataFrame):
        super().__init__(truth)
        self.gate = threading.Event()

    def get_bars(self, symbol: str, tf: str, n: int) -> pd.DataFrame:
        self.calls.append(self.now)       # type: ignore[arg-type]
        self.gate.wait(30)
        raise TimeoutError("read timed out")


def test_a_hung_rates_call_never_slows_the_exits_or_the_stop_checks(tmp_path):
    term = Hung(TICKS)
    eng = _engine(tmp_path, term, fetcher=broker_bars.Fetcher(term))     # the production fetcher: its own thread
    checks: list[pd.Timestamp] = []
    retries: list[pd.Timestamp] = []
    stop_check, retry_closes = eng._stop_check, eng._retry_closes

    def counted_stop(t: Tick) -> None:
        checks.append(t.ts_utc)
        stop_check(t)

    def counted_retry(now: pd.Timestamp) -> None:
        retries.append(now)
        retry_closes(now)
    eng._stop_check, eng._retry_closes = counted_stop, counted_retry     # type: ignore[method-assign]
    s = _sampled(TICKS).iloc[:120]
    slowest = 0.0
    try:
        for ts, bid, ask in zip(s["ts_utc"], s["bid"], s["ask"]):
            t0 = time.monotonic()
            eng.on_tick(_tick(ts, bid, ask))
            slowest = max(slowest, time.monotonic() - t0)
        assert slowest < 1.0                                  # the call hangs 30 s; no pass waits on it
        assert len(checks) == len(retries) == len(s)          # every pass ran its stop check and close retries
        assert len(term.calls) == 1 and eng.broker_m1.fetcher is not None and eng.broker_m1.fetcher.busy()
        assert not eng.state.tick_error
    finally:
        term.gate.set()
    job = eng.broker_m1.fetcher.job
    assert job is not None and job.done.wait(5)
    last = s.iloc[-1]
    later = pd.Timestamp(last["ts_utc"]) + pd.Timedelta(seconds=1)
    eng.on_tick(_tick(later, last["bid"], last["ask"]))
    assert eng.broker_m1.stats["fetch_errors"] == 1 and not eng.state.tick_error
    assert eng.broker_m1.retry_at == later + broker_bars.RETRY  # backoff measured from when the failure was applied


def test_failed_calls_back_off_exponentially_up_to_a_minute(tmp_path):
    term = Terminal(TICKS)
    term.fail = True
    eng = _engine(tmp_path, term)
    _feed(eng, _sampled(TICKS).iloc[:400])
    gaps = [(b - a).total_seconds() for a, b in zip(term.calls, term.calls[1:])]
    assert len(gaps) >= 5
    floors = [5, 10, 20, 40] + [60] * (len(gaps) - 4)
    assert all(g >= f for g, f in zip(gaps, floors))
    assert eng.broker_m1.fails == len(term.calls) and not eng.state.tick_error


def test_a_decision_close_waits_briefly_for_its_last_broker_bar_and_decides_on_it(tmp_path):
    live = pd.Timestamp("2025-03-13", tz="UTC")
    ticks = _ticks("2025-03-03", "2025-03-13 09:30", 2, 8)
    Store(tmp_path / "data").append("bars_1m", ticks_to_1m(ticks[ticks["ts_utc"] < live]), source="dukascopy")
    close = pd.Timestamp("2025-03-13 08:30", tz="UTC")
    term = Terminal(ticks)
    fetcher = Inline(term)
    eng = _engine(tmp_path, term, fetcher=fetcher)
    eng.warm_start(live)
    s = _sampled(ticks[ticks["ts_utc"] >= live])
    _feed(eng, s[s["ts_utc"] < close])
    fetcher.paused = True                                      # the call for 08:29 is in flight
    first = s[s["ts_utc"] >= close].iloc[0]
    assert eng.on_tick(_tick(close + pd.Timedelta(seconds=0.5), first["bid"], first["ask"])) == []
    assert eng.broker_m1.held == close and eng._closed_through is not None and eng._closed_through < close
    fetcher.release()                                          # it answers
    out = eng.on_tick(_tick(close + pd.Timedelta(seconds=1), first["bid"], first["ask"]))
    assert [d["action"] for d in out] == ["executed:auto"]
    used = eng.bars_1m.set_index("ts_utc").loc[close - pd.Timedelta(minutes=1)]
    assert used["bid_close"] == _as_bars(_rates(ticks)).loc[close - pd.Timedelta(minutes=1), "bid_close"]
    assert eng.broker_m1.stats["fallback"] == 0


def test_a_close_whose_bar_call_never_answers_decides_after_the_hold_on_the_sampled_bar(tmp_path):
    term = Terminal(TICKS)
    fetcher = Inline(term)
    eng = _engine(tmp_path, term, fetcher=fetcher)
    s = _sampled(TICKS)
    close = pd.Timestamp("2025-03-10 09:15", tz="UTC")
    _feed(eng, s[s["ts_utc"] < close])
    fetcher.paused = True
    q = s[s["ts_utc"] >= close].iloc[0]
    eng.on_tick(_tick(close + pd.Timedelta(seconds=1), q["bid"], q["ask"]))
    assert eng.broker_m1.held == close
    eng.on_tick(_tick(close + broker_bars.GRACE + broker_bars.HOLD, q["bid"], q["ask"]))
    assert eng.broker_m1.held is None and eng._closed_through == close
    assert close - pd.Timedelta(minutes=1) in eng.broker_m1.fallback


def test_a_broker_bar_with_its_high_below_its_low_is_refused_and_the_sampled_bar_stands(tmp_path):
    bad = pd.Timestamp("2025-03-10 08:20", tz="UTC")
    term = Terminal(TICKS)
    term.corrupt.add(bad)
    eng = _engine(tmp_path, term)
    s = _sampled(TICKS)
    _feed(eng, s[s["ts_utc"] < pd.Timestamp("2025-03-10 08:40", tz="UTC")])
    eng._rebuild_bars()
    row = eng.bars_1m.set_index("ts_utc").loc[bad]
    assert row["bid_close"] == ticks_to_1m(s[(s["ts_utc"] >= bad) & (s["ts_utc"] < bad + pd.Timedelta(minutes=1))]).iloc[0]["bid_close"]
    assert eng.broker_m1.stats["refused"] >= 1 and not eng._dq_errors()
    assert any(e.check == "broker_bar_refused" for e in eng._dq_pending + eng._dq_bar_events)


def test_a_late_replacement_that_is_unsound_is_refused_and_never_replaces_the_sampled_bar(tmp_path):
    late = pd.Timestamp("2025-03-10 09:14", tz="UTC")
    term = Terminal(TICKS, withhold=(late,))
    eng = _engine(tmp_path, term)
    s = _sampled(TICKS)
    _feed(eng, s[s["ts_utc"] < late + pd.Timedelta(minutes=1, seconds=40)])
    eng._rebuild_bars()
    sampled = eng.bars_1m.set_index("ts_utc").loc[late, "bid_close"]
    term.withhold.clear()
    term.corrupt.add(late)
    _feed(eng, s[(s["ts_utc"] >= late + pd.Timedelta(minutes=1, seconds=40)) & (s["ts_utc"] < late + pd.Timedelta(minutes=4))])
    assert eng.bars_1m.set_index("ts_utc").loc[late, "bid_close"] == sampled
    assert eng.broker_m1.stats["replaced"] == 0 and eng.broker_m1.stats["refused"] >= 1


def test_a_malformed_rates_reply_is_a_warning_not_a_tick_error(tmp_path):
    term = Terminal(TICKS)
    term.malformed = True
    eng = _engine(tmp_path, term)
    s = _sampled(TICKS)
    _feed(eng, s[s["ts_utc"] < pd.Timestamp("2025-03-10 08:20", tz="UTC")])
    eng._rebuild_bars()
    assert eng.broker_m1.stats["fetch_errors"] >= 1 and len(eng.bars_1m) >= 18
    assert not eng.state.tick_error and eng.tick_errors_total == 0


def test_a_held_close_runs_its_time_exit_and_the_friday_cut_check_before_it_waits(tmp_path):
    """Only entries wait for the broker's bar (trading-safety LOW on 432fbba8): the close's time exit and its account
    refresh (stage machine, kill switch, the Friday cut in `_server_clock`) run when the hold starts."""
    live = pd.Timestamp("2025-03-13", tz="UTC")
    ticks = _ticks("2025-03-03", "2025-03-13 09:30", 2, 8)
    Store(tmp_path / "data").append("bars_1m", ticks_to_1m(ticks[ticks["ts_utc"] < live]), source="dukascopy")
    term = Terminal(ticks)
    fetcher = Inline(term)
    eng = _engine(tmp_path, term, fetcher=fetcher)
    eng.warm_start(live)
    s = _sampled(ticks[ticks["ts_utc"] >= live])
    close = pd.Timestamp("2025-03-13 08:45", tz="UTC")
    _feed(eng, s[s["ts_utc"] < close])
    [(pid, tr)] = list(eng.open.items())                     # the 08:30 entry
    tr.bars_held = tr.max_bars - 1                           # its time barrier is this close
    cuts: list[pd.Timestamp] = []
    server_clock = eng._server_clock

    def spy(now: pd.Timestamp, tick: Tick) -> None:
        cuts.append(now)
        server_clock(now, tick)
    eng._server_clock = spy                                  # type: ignore[method-assign]
    fetcher.paused = True                                    # the call for 08:44 is in flight: the close is held
    q = s[s["ts_utc"] >= close].iloc[0]
    assert eng.on_tick(_tick(close + pd.Timedelta(seconds=0.5), q["bid"], q["ask"])) == []
    assert eng.broker_m1.held == close and eng._closed_through is not None and eng._closed_through < close
    assert pid not in {p.position_id for p in term.positions()}          # closed at the held close, not after it
    assert cuts                                                          # the Friday cut was checked
    fetcher.release()
    eng.on_tick(_tick(close + pd.Timedelta(seconds=1), q["bid"], q["ask"]))
    assert eng._closed_through == close and not eng.state.tick_error
