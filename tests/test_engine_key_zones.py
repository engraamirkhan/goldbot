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
    return close_ts, eng.on_bar_close(close_ts, tick)


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
        def collect(self) -> None:
            return None

        def submit(self, recent: pd.DataFrame, t: pd.Timestamp, price: float) -> bool:
            calls.append(t)
            return True

    eng: Any = SimpleNamespace(key_zones=None, key_zones_at=None, key_zone_worker=Worker(), key_zones_errors=set(), sessions=None,
                               _resample=lambda complete, tf: resample_bars(complete, tf))
    t0 = pd.Timestamp("2025-02-03 10:15", tz="UTC")
    for m in range(0, 31):
        key_zones.update(eng, BARS[BARS["visible_at"] <= t0 + pd.Timedelta(minutes=m)], t0 + pd.Timedelta(minutes=m))
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

    def __init__(self, rates: pd.DataFrame, fail: bool = False):
        self.rates, self.fail = rates, fail
        self.calls: list[tuple[str, pd.Timestamp, pd.Timestamp]] = []

    def symbol_info(self, symbol: str) -> Any:
        return SimpleNamespace(point=POINT)

    def get_bars_range(self, symbol: str, tf: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        self.calls.append((tf, start, end))
        if self.fail:
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


def test_one_pull_per_bridge_at_a_time_and_a_random_first_delay(tmp_path, m15_rates):
    w = _worker(m15_rates, tmp_path)
    before = w.clock()
    w.attach(w.client, "box-2")
    assert w.next_pull is not None
    assert before <= w.next_pull <= w.clock() + pd.Timedelta(seconds=key_zones.START_JITTER_S)
    w.next_pull = None
    lock = key_zones._bridge_lock("box-2")
    lock.acquire()                                  # another account's pull on the same MT5 box
    try:
        w.refresh(NOW)
    finally:
        lock.release()
    assert w.client.calls == [] and w.next_pull == NOW + key_zones.BUSY_RETRY


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


def test_an_account_can_opt_out_of_the_long_history():
    from goldbot.ops.accounts import Account
    fields = Account.model_fields["key_levels_history"]
    assert fields.default == "long"


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
