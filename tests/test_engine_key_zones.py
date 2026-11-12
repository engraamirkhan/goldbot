"""The engine publishes its current key zones (goldbot/engine/key_zones.py) for the dashboard's price chart.

Design rules: display only, so the zones never reach an order, a size or the RiskGate. The build runs on the worker's
own thread and never blocks a bar close. A failure, a non-finite price or a non-finite ATR keeps the last zones. The
zones are built on a long history of the broker's own M15 bars, cached in the store and refreshed daily, with the
engine's own bars used for the recent part. The display shows a few strong zones, not every zone.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.data.resample import resample_bars, ticks_to_1m
from goldbot.data.store import Store
from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine import ConstantModel, Engine, EngineConfig, key_zones
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker
from goldbot.features.key_levels import KeyZone, KeyZones
from goldbot.specialists import SPECIALISTS

BARS = ticks_to_1m(synthetic_ticks("2025-01-06", "2025-02-28", ticks_per_minute=1, seed=3))
POINT = 0.01


def _inline(eng: Engine) -> Engine:
    setattr(eng.key_zone_worker, "_spawn", lambda fn: fn())     # the build runs on the calling thread in tests
    eng.key_zone_worker.sleep = lambda s: None                  # no real pause between pages
    return eng


def _engine(tmp_path: Path, **cfg: Any) -> Engine:
    spec = SPECIALISTS["session_open"](min_body_pct=0.0, asia_range_max_atr_d=99.0)
    return _inline(Engine(EngineConfig(account_id="icm-demo", broker_name="icm", approval_mode="auto",
                                       state_dir=str(tmp_path), **cfg),
                          PaperBroker(equity=10_000), [spec], {"session_open": ConstantModel(p=0.65)}))


def _close(eng: Engine, n: int, bars: pd.DataFrame = BARS) -> tuple[pd.Timestamp, list[dict]]:
    b = bars.iloc[:n].reset_index(drop=True)
    eng.bars_1m = b
    close_ts = pd.Timestamp(b["visible_at"].iloc[-1])
    last = b.iloc[-1]
    tick = Tick(ts_utc=close_ts, bid=float(np.nan_to_num(last["bid_close"], nan=2000.0)),
                ask=float(np.nan_to_num(last["ask_close"], nan=2000.2)))
    setattr(eng, "_rebuild_bars", lambda until=None: None)      # the bars are set above
    out = eng.on_bar_close(close_ts, tick)
    key_zones.launch(eng)                     # the pass's end: the parked build starts after the entries (SRE 11-09)
    eng._write_state()                        # the next state write publishes what it built
    return close_ts, out


def _n_at_15m_close(back: int = 50) -> int:
    vis = pd.to_datetime(BARS["visible_at"], utc=True)
    on = vis.dt.minute.isin([0, 15, 30, 45]).to_numpy()
    return int(np.flatnonzero(on)[-back]) + 1


def _state(tmp_path: Path) -> dict[str, Any]:
    return json.loads(Path(tmp_path, "engine_icm-demo.json").read_text())


# ------------------------------------------------------------------------------------------- the bar close
def test_a_bar_close_publishes_at_most_three_strong_zones_each_side_with_strength_and_timeframes(tmp_path):
    eng = _engine(tmp_path)
    close_ts, _ = _close(eng, _n_at_15m_close())
    kz = _state(tmp_path)["key_zones"]
    assert kz["as_of"] == close_ts.floor("15min").isoformat()
    assert 1 <= len(kz["above"]) <= 3 and 1 <= len(kz["below"]) <= 3
    assert kz["zones_total"] > len(kz["above"]) + len(kz["below"])
    for z in kz["above"] + kz["below"]:
        assert set(z) == {"price", "lo", "hi", "strength", "strength_pct", "tfs", "touches", "role", "flipped"}
        assert z["strength_pct"] >= key_zones.DISPLAY_MIN_PCT and z["tfs"]
    assert all(z["price"] > kz["price"] for z in kz["above"]) and all(z["price"] <= kz["price"] for z in kz["below"])


def test_zones_are_handed_to_the_worker_only_when_a_new_15m_bar_has_closed():
    calls: list[pd.Timestamp] = []

    class Worker:
        parked: Any = None
        sessions: Any = None

        def collect(self) -> None:
            return None

        def busy(self) -> bool:
            return False

        def submit(self, recent: pd.DataFrame, t: pd.Timestamp, price: float) -> bool:
            calls.append(t)
            return True

    eng: Any = SimpleNamespace(key_zones=None, key_zones_at=None, key_zone_worker=Worker(), key_zones_errors=set(), sessions=None,
                               _resample=lambda complete, tf: resample_bars(complete, tf))
    t0 = pd.Timestamp("2025-02-03 10:15", tz="UTC")
    for m in range(0, 31):
        key_zones.update(eng, BARS[BARS["visible_at"] <= t0 + pd.Timedelta(minutes=m)], t0 + pd.Timedelta(minutes=m))
        key_zones.launch(eng)                                     # every pass's end
    assert calls == [t0, t0 + pd.Timedelta(minutes=15), t0 + pd.Timedelta(minutes=30)]


def test_the_bar_close_never_waits_for_a_running_build(tmp_path):
    eng = _engine(tmp_path)
    started: list[Any] = []
    setattr(eng.key_zone_worker, "_spawn", lambda fn: started.append(fn))   # a build that has not finished
    _close(eng, _n_at_15m_close(60))
    assert len(started) == 1 and eng.key_zone_worker.busy() and eng.key_zones is None
    _close(eng, _n_at_15m_close(40))                                         # a later 15m close: no second build
    assert len(started) == 1
    started[0]()                                                              # the thread finishes
    _close(eng, _n_at_15m_close(40) + 1)
    assert eng.key_zones is not None                                         # collected at the next close


def test_a_zone_failure_keeps_the_last_zones_and_never_fails_the_bar_close_or_its_decisions(tmp_path, monkeypatch,
                                                                                            caplog):
    n = _n_at_15m_close()
    _, want = _close(_engine(tmp_path / "ref"), n)
    eng = _engine(tmp_path / "x")
    eng.key_zones = {"as_of": "kept"}

    def boom(*_: Any, **__: Any) -> Any:
        raise RuntimeError("bad book")

    monkeypatch.setattr(key_zones, "zones_asof", boom)
    with caplog.at_level(logging.WARNING, logger="goldbot.engine.key_zones"):
        _, got = _close(eng, n)
        _close(eng, _n_at_15m_close(30))
    assert eng.key_zones == {"as_of": "kept"}
    assert [(d.get("agent_id"), d.get("action")) for d in got] == [(d.get("agent_id"), d.get("action")) for d in want]
    assert json.loads(Path(tmp_path, "x", "engine_icm-demo.json").read_text())["key_zones"] == {"as_of": "kept"}
    fails = [r for r in caplog.records if "RuntimeError" in r.getMessage()]
    assert len(fails) == 2 and fails[0].exc_info is not None and fails[1].exc_info is None   # traceback once per type


def test_a_non_finite_price_keeps_the_last_zones_and_builds_nothing(tmp_path):
    eng = _engine(tmp_path)
    eng.key_zones = {"as_of": "kept"}
    bars = BARS.copy()
    n = _n_at_15m_close()
    bars.loc[n - 1, ["bid_close", "ask_close"]] = np.nan
    _close(eng, n, bars)
    assert eng.key_zones == {"as_of": "kept"} and eng.key_zone_worker.job is None


def test_a_non_finite_atr_keeps_the_last_zones(tmp_path, monkeypatch):
    eng = _engine(tmp_path)
    eng.key_zones = {"as_of": "kept"}
    monkeypatch.setattr(key_zones, "zones_asof", lambda bars, t: KeyZones(
        as_of=t, price=2000.0, atr_1d=float("nan"), zones=()))
    _close(eng, _n_at_15m_close())
    assert eng.key_zones == {"as_of": "kept"}


def test_a_slow_build_is_logged(tmp_path, monkeypatch, caplog):
    eng = _engine(tmp_path)
    monkeypatch.setattr(key_zones, "SLOW_BUILD_S", -1.0)
    with caplog.at_level(logging.WARNING, logger="goldbot.engine.key_zones"):
        _close(eng, _n_at_15m_close())
    assert any("build took" in r.getMessage() for r in caplog.records)


def test_no_bars_publish_no_zones():
    assert key_zones.build(BARS.iloc[:0], pd.Timestamp("2025-02-03", tz="UTC"), 2000.0) is None


# ------------------------------------------------------------------------------------------- the long history
@pytest.fixture(scope="module")
def m15_rates() -> pd.DataFrame:
    """The broker's M15 rates (bid OHLC, tick volume, spread in points) for 2023-01..2025-02."""
    b = resample_bars(ticks_to_1m(synthetic_ticks("2023-01-02", "2025-02-28", ticks_per_minute=1, seed=4)), "15m")
    return pd.DataFrame({"ts_utc": pd.to_datetime(b["ts_utc"], utc=True), "open": b["bid_open"], "high": b["bid_high"],
                         "low": b["bid_low"], "close": b["bid_close"], "tick_count": b["tick_count"],
                         "spread": 20})


class Bridge:
    """get_bars_range / symbol_info as the bridge serves them; records each call."""

    def __init__(self, rates: pd.DataFrame, fail: bool = False, pages: int | None = None):
        self.rates, self.fail, self.pages = rates, fail, pages      # pages: a slow terminal fails after this many
        self.calls: list[tuple[str, pd.Timestamp, pd.Timestamp]] = []

    def symbol_info(self, symbol: str) -> Any:
        return SimpleNamespace(point=POINT)

    def get_bars_range(self, symbol: str, tf: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        self.calls.append((tf, start, end))
        if self.fail or (self.pages is not None and len(self.calls) > self.pages):
            raise ConnectionError("bridge down")
        r = self.rates
        return r[(r["ts_utc"] >= start) & (r["ts_utc"] <= end)].reset_index(drop=True)


def test_zones_are_built_on_years_of_broker_m15_cached_in_the_store(tmp_path, m15_rates):
    eng = _engine(tmp_path, data_root=str(tmp_path / "data"))
    bridge = Bridge(m15_rates)
    eng.key_zone_worker.client = bridge
    _close(eng, _n_at_15m_close())
    kz = eng.key_zones
    assert kz is not None and kz["history_days"] > 700 and kz["pct_ready"]
    assert {tf for tf, _, _ in bridge.calls} == {"15m"} and len(bridge.calls) >= 8      # paged pulls
    assert all(e - s <= pd.Timedelta(days=key_zones.PAGE_DAYS) for _, s, e in bridge.calls)
    cached = Store(tmp_path / "data").read(key_zones.LONG_TABLE, source="icm-demo")
    assert len(cached) > 50_000
    assert (pd.to_datetime(cached["visible_at"], utc=True) - pd.to_datetime(cached["ts_utc"], utc=True)
            == pd.Timedelta(minutes=15)).all()
    fresh = _engine(tmp_path / "restart", data_root=str(tmp_path / "data"))              # a restart without a bridge
    _close(fresh, _n_at_15m_close())
    assert fresh.key_zones is not None and fresh.key_zones["history_days"] > 700


def test_the_long_history_is_topped_up_once_a_day_from_its_last_bars(tmp_path, m15_rates):
    w = _worker(m15_rates, tmp_path)
    now = pd.Timestamp("2025-02-10 12:00", tz="UTC")
    w.refresh(now)
    first = len(w.client.calls)
    w.refresh(now + pd.Timedelta(hours=6))
    assert len(w.client.calls) == first                                        # same day: no pull
    last = pd.Timestamp(w.long["ts_utc"].iloc[-1])
    assert pd.Timestamp(w.long["visible_at"].iloc[-1]) <= now                  # never the forming bar
    later = now + pd.Timedelta(days=1, minutes=1)
    w.refresh(later)
    (tf, start, end), = w.client.calls[first:]                                 # one page: the last bars onward
    assert (tf, start, end) == ("15m", last - key_zones.OVERLAP, later)
    assert pd.Timestamp(w.long["ts_utc"].iloc[-1]) > last
    assert (pd.to_datetime(w.long["visible_at"], utc=True) <= later).all()
    assert w.long["ts_utc"].is_unique


def test_a_failed_pull_is_reported_retried_within_the_hour_and_the_engines_bars_still_build(tmp_path, m15_rates,
                                                                                          caplog):
    eng = _engine(tmp_path)
    eng.key_zone_worker.client = Bridge(m15_rates, fail=True)
    with caplog.at_level(logging.WARNING, logger="goldbot.engine.key_zones"):
        close_ts, _ = _close(eng, _n_at_15m_close())
    assert any("ConnectionError" in r.getMessage() for r in caplog.records)
    assert eng.key_zones is not None and eng.key_zones["history_days"] < 60     # built on the engine's own bars
    assert eng.key_zone_worker.next_pull == close_ts + key_zones.RETRY
    assert eng.key_zone_worker.sessions is eng.sessions                        # the account's own calendar


# ------------------------------------------------------------------------- backlog 159: the cache-read warning throttled
def _unreadable_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[key_zones.KeyZoneWorker, dict]:
    """A worker whose store cache read raises `state["exc"]` while it is set."""
    state: dict[str, Any] = {"exc": RuntimeError("Binder Error")}
    real = Store.read

    def read(store: Store, table: str, **kw: Any) -> pd.DataFrame:
        if state["exc"] is not None:
            raise state["exc"]
        return real(store, table, **kw)
    monkeypatch.setattr(Store, "read", read)
    return key_zones.KeyZoneWorker("icm-demo", "XAUUSD", Store(tmp_path / "data")), state


def _cache_logs(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if "key zones: cache" in r.getMessage()]


def test_an_unreadable_cache_warns_first_with_its_traceback_then_at_most_once_an_hour(tmp_path, monkeypatch, caplog):
    w, _ = _unreadable_cache(tmp_path, monkeypatch)
    t0 = pd.Timestamp("2025-03-13", tz="UTC")
    with caplog.at_level(logging.WARNING, logger="goldbot.engine.key_zones"):
        for k in range(8):                                 # a build every 15m close for two hours
            w.refresh(t0 + k * pd.Timedelta(minutes=15))
    logs = _cache_logs(caplog)
    assert len(logs) == 2 and not w.loaded                 # at t0 and t0 + 1h; the read is still retried each build
    assert logs[0].exc_info is not None and logs[1].exc_info is None
    assert "3 more" in logs[1].getMessage()                 # the failures it stood for since the last warning


def test_a_new_kind_of_cache_failure_is_logged_at_once_with_its_traceback(tmp_path, monkeypatch, caplog):
    w, state = _unreadable_cache(tmp_path, monkeypatch)
    t0 = pd.Timestamp("2025-03-13", tz="UTC")
    with caplog.at_level(logging.WARNING, logger="goldbot.engine.key_zones"):
        w.refresh(t0)
        state["exc"] = OSError("disk")
        w.refresh(t0 + pd.Timedelta(minutes=15))
    logs = _cache_logs(caplog)
    assert len(logs) == 2 and "OSError" in logs[1].getMessage() and logs[1].exc_info is not None


def test_the_quiet_count_in_an_hourly_warning_is_its_own_exception_type_s(tmp_path, monkeypatch, caplog):
    w, state = _unreadable_cache(tmp_path, monkeypatch)
    t0 = pd.Timestamp("2025-03-13", tz="UTC")
    with caplog.at_level(logging.WARNING, logger="goldbot.engine.key_zones"):
        w.refresh(t0)                                      # RuntimeError: logged
        w.refresh(t0 + pd.Timedelta(minutes=15))           # RuntimeError: quiet (1)
        state["exc"] = OSError("disk")
        w.refresh(t0 + pd.Timedelta(minutes=30))           # OSError: first of its type, nothing quiet of it
        w.refresh(t0 + pd.Timedelta(minutes=45))           # OSError: quiet (1)
        w.refresh(t0 + pd.Timedelta(minutes=50))           # OSError: quiet (2)
        state["exc"] = RuntimeError("Binder Error")
        w.refresh(t0 + pd.Timedelta(hours=1))              # RuntimeError: hourly, 1 more
    logs = _cache_logs(caplog)
    assert len(logs) == 3
    assert "OSError" in logs[1].getMessage() and "more" not in logs[1].getMessage()
    assert "RuntimeError" in logs[2].getMessage() and "(1 more since" in logs[2].getMessage()


def test_a_cache_that_reads_again_logs_its_recovery_once(tmp_path, monkeypatch, caplog):
    w, state = _unreadable_cache(tmp_path, monkeypatch)
    t0 = pd.Timestamp("2025-03-13", tz="UTC")
    with caplog.at_level(logging.INFO, logger="goldbot.engine.key_zones"):
        w.refresh(t0)
        w.refresh(t0 + pd.Timedelta(minutes=15))
        state["exc"] = None
        for k in range(2, 5):
            w.refresh(t0 + k * pd.Timedelta(minutes=15))
    back = [r for r in _cache_logs(caplog) if "readable again" in r.getMessage()]
    assert len(back) == 1 and back[0].levelno == logging.WARNING and "2 failed" in back[0].getMessage()
    assert w.loaded


def test_a_cache_that_reads_first_time_logs_nothing(tmp_path, caplog):
    w = key_zones.KeyZoneWorker("icm-demo", "XAUUSD", Store(tmp_path / "data"))
    with caplog.at_level(logging.INFO, logger="goldbot.engine.key_zones"):
        w.refresh(pd.Timestamp("2025-03-13", tz="UTC"))
    assert w.loaded and not _cache_logs(caplog)


# ------------------------------------------------------------------------------------------- pacing (safety M1, M2, L1, L2)
NOW = pd.Timestamp("2025-02-10 12:00", tz="UTC")


SLEEPS: list[float] = []


def _worker(rates: pd.DataFrame, tmp_path: Path, symbol: str = "XAUUSD", **kw: Any) -> key_zones.KeyZoneWorker:
    """A worker on a fake bridge whose pauses are recorded in SLEEPS, not slept."""
    SLEEPS.clear()
    w = key_zones.KeyZoneWorker("icm-demo", symbol, store=Store(tmp_path / "data"), client=Bridge(rates, **kw))
    w.sleep = SLEEPS.append
    return w


def test_a_pull_is_paged_by_30_days_with_a_pause_between_pages(tmp_path, m15_rates):
    w = _worker(m15_rates, tmp_path)
    w.refresh(NOW)
    calls = w.client.calls
    assert len(calls) == -(-key_zones.LONG_DAYS // key_zones.PAGE_DAYS)
    assert all(e - s <= pd.Timedelta(days=key_zones.PAGE_DAYS) for _, s, e in calls)
    assert SLEEPS == [key_zones.PAGE_PAUSE_S] * (len(calls) - 1)


def test_a_pull_stops_at_its_time_budget_keeps_its_pages_and_retries_within_the_hour(tmp_path, m15_rates):
    from goldbot.data.ticks import PullDeferred
    w = _worker(m15_rates, tmp_path)
    t = [NOW]

    def clock() -> pd.Timestamp:
        t[0] += pd.Timedelta(seconds=10)            # every page takes 10 s of wall time
        return t[0]

    w.clock = clock
    with pytest.raises(PullDeferred):
        w.refresh(NOW)
    assert 5 < len(w.client.calls) < 27 and not w.long.empty
    assert w.next_pull == NOW + key_zones.RETRY
    assert len(Store(tmp_path / "data").read(key_zones.LONG_TABLE, source="icm-demo")) == len(w.long)


def test_a_bridge_that_times_out_gets_exactly_one_request_of_20_s(tmp_path):
    import requests
    posts: list[float] = []

    def post(url: str, data: str, timeout: float, headers: dict[str, str]) -> Any:
        posts.append(timeout)
        time.sleep(0.05)                            # the terminal does not answer within the timeout
        raise requests.Timeout("read timed out")

    w = key_zones.KeyZoneWorker("icm-demo", "XAUUSD")
    w.attach(key_zones.levels_client("http://bridge.invalid", "t", "icm-demo", post=post), "box-1", jitter_s=0)
    with pytest.raises(Exception, match="symbol_info"):
        w.refresh(w.clock() + pd.Timedelta(seconds=1))
    assert posts == [key_zones.CLIENT_TIMEOUT_S]
    assert w.next_pull is not None


HOLDER = """
import sys, time
from pathlib import Path
from goldbot.engine.key_zones import box_lock
lock_dir, box, ready, done = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])
with box_lock(lock_dir, box) as mine:
    ready.write_text("held" if mine else "busy")
    t0 = time.time()
    while not done.exists() and time.time() - t0 < 60:
        time.sleep(0.05)
"""


def test_one_pull_per_mt5_box_across_engine_processes(tmp_path, m15_rates):
    import subprocess
    import sys
    locks, ready, done = tmp_path / "state" / "locks", tmp_path / "ready", tmp_path / "done"
    box = key_zones.box_key("http://box-a.invalid:8765")
    assert box == key_zones.box_key("http://box-a.invalid:9999") != key_zones.box_key("http://box-b.invalid:8765")
    holder = subprocess.Popen([sys.executable, "-c", HOLDER, str(locks), box, str(ready), str(done)],
                              cwd=Path(__file__).resolve().parents[1])
    try:
        t0 = time.time()
        while not ready.exists() and time.time() - t0 < 60:
            time.sleep(0.05)
        assert ready.read_text() == "held"                  # another engine process pulls on this box
        w = _worker(m15_rates, tmp_path)
        w.attach(w.client, box, lock_dir=locks, jitter_s=0)
        w.next_pull = None                                  # due now (the engine clock of this test)
        w.refresh(NOW)
        assert w.client.calls == [] and w.next_pull == NOW + key_zones.BUSY_RETRY
        other = _worker(m15_rates, tmp_path / "b")
        other.attach(other.client, key_zones.box_key("http://box-b.invalid:8765"), lock_dir=locks, jitter_s=0)
        other.next_pull = None
        other.refresh(NOW)
        assert other.client.calls                           # another box is not held up
    finally:
        done.write_text("x")
        holder.wait(30)
    w.refresh(NOW + key_zones.BUSY_RETRY)                   # the holder exited: this process pulls
    assert w.client.calls
    assert list(locks.iterdir()) and all("invalid" not in f.name for f in locks.iterdir())   # no host in a name


def test_two_loopback_tunnels_to_different_boxes_get_their_own_lock(tmp_path):
    """Safety re-review of #306, P1: every brain bridge is http://127.0.0.1:<port> (an SSH tunnel), so the host names
    no box. The tunnel's port does, and the account's own `box` (accounts.yaml) wins over both."""
    a, b = "http://127.0.0.1:18765", "http://127.0.0.1:18766"
    assert key_zones.box_key(a) != key_zones.box_key(b)
    assert key_zones.box_key(a, "vps-icm") == key_zones.box_key(b, "vps-icm") != key_zones.box_key(a, "vps-vantage")
    locks = tmp_path / "locks"
    with key_zones.box_lock(locks, key_zones.box_key(a)) as first:
        with key_zones.box_lock(locks, key_zones.box_key(b)) as second:
            assert first and second                       # two boxes: two pulls at once
    assert all("127" not in f.name and "vps" not in f.name for f in locks.iterdir())


def test_a_hole_the_broker_has_no_bars_for_is_not_re_read_until_the_head_recheck(tmp_path, m15_rates):
    """Safety re-review of #306, P2: a completed pull that re-read a hole and got no bar there is remembered, and the
    hole is skipped until HEAD_RECHECK (as `broker_start` is for the head), never re-read on every refresh."""
    ts = pd.to_datetime(m15_rates["ts_utc"], utc=True)
    hole = (NOW - pd.Timedelta(days=300), NOW - pd.Timedelta(days=270))
    holed = m15_rates[~((ts > hole[0]) & (ts < hole[1]))]
    w = _worker(holed, tmp_path)
    w.refresh(NOW)                                         # from the head: the hole is the broker's
    w.client, w.next_pull = Bridge(holed), None
    w.refresh(NOW + pd.Timedelta(days=1))                  # resumes at the hole, finds nothing there
    assert w.client.calls[0][1] <= hole[0] and w.dead_gaps
    w.client, w.next_pull = Bridge(holed), None
    w.refresh(NOW + pd.Timedelta(days=2))                  # remembered: a top-up only
    assert w.client.calls[0][1] > NOW - pd.Timedelta(days=3)
    w.client, w.next_pull = Bridge(holed), None
    w.refresh(NOW + key_zones.HEAD_RECHECK + pd.Timedelta(days=2))   # a week on: asked again
    assert w.client.calls[0][1] <= hole[0]


def test_the_first_pull_after_a_restart_waits_a_random_delay(tmp_path, m15_rates):
    w = _worker(m15_rates, tmp_path)
    before = w.clock()
    w.attach(w.client, "box-2")
    assert w.next_pull is not None
    assert before <= w.next_pull <= w.clock() + pd.Timedelta(seconds=key_zones.START_JITTER_S)


def test_a_head_re_pull_cut_short_is_resumed_at_the_hole_it_left(tmp_path, m15_rates):
    """Review N2: a 200-day cache, a head re-pull stopped after 10 pages, then a retry must fill the middle."""
    late = m15_rates[m15_rates["ts_utc"] >= NOW - pd.Timedelta(days=200)]
    _worker(late, tmp_path).refresh(NOW)
    w = _worker(m15_rates, tmp_path, pages=10)
    with pytest.raises(ConnectionError):
        w.refresh(NOW + pd.Timedelta(days=1))
    ts = pd.to_datetime(w.long["ts_utc"], utc=True)
    assert ts.diff().max() > pd.Timedelta(days=200)          # the hole between the head pages and the old cache
    w.client = Bridge(m15_rates)
    w.refresh(NOW + pd.Timedelta(days=1, hours=2))
    first_call = w.client.calls[0][1]
    assert first_call > NOW - pd.Timedelta(days=key_zones.LONG_DAYS - 200)   # resumed at the hole, not the head
    ts = pd.to_datetime(w.long["ts_utc"], utc=True)
    assert ts.diff().max() < pd.Timedelta(days=5)            # no hole left (weekends and holidays only)


def test_an_empty_first_page_on_a_cold_terminal_does_not_date_the_brokers_start(tmp_path, m15_rates):
    head = NOW - pd.Timedelta(days=key_zones.LONG_DAYS)
    cold = m15_rates[m15_rates["ts_utc"] >= head + pd.Timedelta(days=key_zones.PAGE_DAYS + 3)]
    w = _worker(cold, tmp_path)
    w.refresh(NOW)
    assert w.broker_start is None and w.head_checked == NOW
    w.client = Bridge(m15_rates)
    w.next_pull = None
    w.refresh(NOW + pd.Timedelta(days=1))                    # within the week: a top-up, not another head pull
    assert w.client.calls[0][1] > NOW - pd.Timedelta(days=3)
    w.client = Bridge(m15_rates)
    w.next_pull = None
    later = NOW + key_zones.HEAD_RECHECK + pd.Timedelta(days=1)
    w.refresh(later)                                         # a week on: the head is asked again
    assert w.client.calls[0][1] == later - pd.Timedelta(days=key_zones.LONG_DAYS)


def test_two_symbols_of_one_account_keep_their_own_cache(tmp_path, m15_rates):
    a = _worker(m15_rates, tmp_path, symbol="XAUUSD")
    b = _worker(m15_rates.assign(**{c: m15_rates[c] + 1000.0 for c in ("open", "high", "low", "close")}), tmp_path,
                symbol="XAUUSD+")
    a.refresh(NOW)
    b.refresh(NOW)
    reread = key_zones.KeyZoneWorker("icm-demo", "XAUUSD", store=Store(tmp_path / "data"))
    reread.refresh(NOW)
    assert len(reread.long) == len(a.long) and (reread.long["bid_close"].to_numpy() == a.long["bid_close"].to_numpy()).all()


def test_a_cache_missing_its_head_is_re_pulled_from_the_head(tmp_path, m15_rates):
    late = m15_rates[m15_rates["ts_utc"] >= NOW - pd.Timedelta(days=200)]
    _worker(late, tmp_path).refresh(NOW)            # a first pull that only got the last 200 days
    w = _worker(m15_rates, tmp_path)
    w.refresh(NOW + pd.Timedelta(days=1))
    head = NOW + pd.Timedelta(days=1) - pd.Timedelta(days=key_zones.LONG_DAYS)
    assert w.client.calls[0][1] == head
    assert pd.Timestamp(w.long["ts_utc"].iloc[0]) < NOW - pd.Timedelta(days=700)


def test_an_empty_weekday_page_counts_as_a_failed_pull(tmp_path, m15_rates):
    hole = (m15_rates["ts_utc"] >= NOW - pd.Timedelta(days=300)) & (m15_rates["ts_utc"] < NOW - pd.Timedelta(days=260))
    w = _worker(m15_rates[~hole], tmp_path)
    with pytest.raises(key_zones.EmptyPage):
        w.refresh(NOW)
    assert w.next_pull == NOW + key_zones.RETRY


def test_the_broker_bars_are_read_through_the_accounts_own_sessions(tmp_path, m15_rates, monkeypatch):
    seen: list[Any] = []
    real = key_zones.rates_to_bars

    def spy(r: pd.DataFrame, point: float, sessions: Any = None) -> pd.DataFrame:
        seen.append(sessions)
        return real(r, point, sessions)

    monkeypatch.setattr(key_zones, "rates_to_bars", spy)
    from goldbot.data.calendar import SessionTable
    own = SessionTable(always_open=True)
    w = _worker(m15_rates, tmp_path)
    w.sessions = own
    w.refresh(NOW)
    assert seen and all(s is own for s in seen)


# ------------------------------------------------------------------------------------------- service wiring (N3)
ACC_KW: dict[str, Any] = dict(account_id="icm-demo", broker="icm", mode="demo", server="ICMarketsSC-Demo", login=None,
                              terminal_path="", server_tz="Europe/Athens", symbol="XAUUSD", magic_base=260100,
                              enabled=True)


def test_a_long_history_account_gets_a_paced_levels_client_keyed_by_its_mt5_box(tmp_path):
    from goldbot.ops.accounts import Account
    from goldbot.ops.services import engine as service
    eng = _engine(tmp_path)
    acc = Account(**ACC_KW)
    assert acc.key_levels_history == "long"
    service.attach_levels(eng, acc, ("http://box-a.invalid:8765", "t"), tmp_path / "state")
    w = eng.key_zone_worker
    assert w.client is not None and w.client.read_attempts == 1 and w.client.timeout_s == key_zones.CLIENT_TIMEOUT_S
    assert w.box == key_zones.box_key("http://box-a.invalid:8765") and w.lock_dir == tmp_path / "state" / "locks"
    assert w.next_pull is not None                            # the random first delay
    import inspect
    assert "attach_levels(eng, acc, bridge" in inspect.getsource(service._run_engine)


def test_a_short_history_account_gets_no_client_and_still_reads_its_cache(tmp_path, m15_rates):
    from goldbot.ops.accounts import Account
    from goldbot.ops.services import engine as service
    _worker(m15_rates, tmp_path).refresh(NOW)                 # a cache from earlier
    eng = _engine(tmp_path, data_root=str(tmp_path / "data"))
    service.attach_levels(eng, Account(**ACC_KW, key_levels_history="short"), ("http://box-a.invalid:1", "t"),
                          tmp_path / "state")
    w = eng.key_zone_worker
    assert w.client is None
    w.refresh(NOW)
    assert len(w.long) > 50_000


def test_the_engines_own_bars_replace_the_long_history_from_its_first_15m_bar():
    w = key_zones.KeyZoneWorker("a", "XAUUSD")
    long = resample_bars(BARS, "15m")
    w.long = long
    recent = resample_bars(BARS.iloc[40_000:], "15m")
    recent = recent.assign(bid_close=recent["bid_close"] + 1.0)
    got = w.merged(recent)
    first = pd.Timestamp(recent["ts_utc"].iloc[0])
    assert (got[got["ts_utc"] < first]["bid_close"].to_numpy() == long[long["ts_utc"] < first]["bid_close"].to_numpy()).all()
    assert (got[got["ts_utc"] >= first]["bid_close"].to_numpy() == recent["bid_close"].to_numpy()).all()


# ------------------------------------------------------------------------------------------- display selection
def _z(price: float, pct: float | None, tfs: tuple[str, ...], touches: int = 2, width: float = 1.0) -> KeyZone:
    return KeyZone(price=price, lo=price - width / 2, hi=price + width / 2, strength=5.0, strength_pct=pct, tfs=tfs,
                   top_tf=tfs[-1], touches=touches, role="support", flipped=False)


def test_the_display_drops_weak_zones_and_lone_15m_swings_and_merges_neighbours():
    zones = (_z(1950.0, 95.0, ("1d",)), _z(1980.0, 30.0, ("4h",)), _z(1990.0, 80.0, ("15m",), touches=1),
             _z(2010.0, 70.0, ("1h",)), _z(2014.0, 60.0, ("4h", "round"), touches=1),
             _z(2040.0, 90.0, ("1w",)), _z(2080.0, 55.0, ("1h",)), _z(2120.0, 99.0, ("1d", "1w")))
    above, below = key_zones.select(KeyZones(as_of=None, price=2000.0, atr_1d=40.0, zones=zones), 2000.0)
    assert [s.price for s in below] == [1950.0]                      # 1980 too weak, 1990 a lone 15m swing
    merged = above[0]
    assert (merged.lo, merged.hi) == (2009.5, 2014.5) and merged.tfs == ("1h", "4h", "round")
    assert merged.strength_pct == 70.0 and merged.touches == 3
    assert [round(s.price) for s in above] == [2012, 2040, 2120]     # 2080 (weak, far) loses on score


def test_display_ranking_prefers_a_strong_near_zone_to_a_strong_far_one():
    zones = tuple(_z(2000.0 + 30 * k, 90.0, ("1d",)) for k in range(1, 8))
    above, _ = key_zones.select(KeyZones(as_of=None, price=2000.0, atr_1d=40.0, zones=zones), 2000.0)
    assert [s.price for s in above] == [2030.0, 2060.0, 2090.0]
