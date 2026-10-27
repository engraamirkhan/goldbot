"""D12 reconcile, broker coverage (root-cause review 2026-10-27): icm-probe FAILed "divergence 40.41%" because the
broker returned 497 M1 minutes against the engine's 834 (matched 497, close_mismatch 0): a short broker return, not a
price fault. Engine minutes outside the span the broker returned are graded as a coverage warning of their own; the
broker's M1 is fetched over the window by range (get_bars_range, MT5 copy_rates_range) with get_bars as the fallback
for a bridge that lacks it."""
from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from goldbot.data import crossfeed as cf
from goldbot.data.resample import ticks_to_1m
from goldbot.data.store import Store
from goldbot.execution.bridge import (
    API_VERSION,
    METHODS,
    MIN_COMPATIBLE,
    OPTIONAL_METHODS,
    GuardedBroker,
    RemoteBroker,
    make_handler,
)
from goldbot.ops.bridge_compat import REQUIRED_METHODS, BridgeInfo, assess
from tests.test_bridge_compat import TOKEN, TZ, _serve
from tests.test_crossfeed import NOW, FakeBroker, _health_ctx, _sampled_some, as_broker_m1, day_ticks

START = NOW - pd.Timedelta(days=1)


class RangeBroker(FakeBroker):
    """A terminal answering copy_rates_range: every M1 bar in [start, end]."""

    def __init__(self, m1: pd.DataFrame) -> None:
        super().__init__(m1)
        self.ranges: list[tuple[pd.Timestamp, pd.Timestamp]] = []

    def get_bars_range(self, symbol: str, tf: str, start_utc: pd.Timestamp, end_utc: pd.Timestamp) -> pd.DataFrame:
        self.ranges.append((start_utc, end_utc))
        ts = pd.to_datetime(self.m1["ts_utc"], utc=True)
        return self.m1[(ts >= start_utc) & (ts <= end_utc)].reset_index(drop=True)


def test_a_short_broker_return_is_a_coverage_warning_not_a_price_divergence():
    eb = ticks_to_1m(day_ticks())
    m1 = as_broker_m1(eb)
    short = m1.tail(497).reset_index(drop=True)            # the terminal served its last 497 minutes only
    n = cf.reconcile(eb, cf.broker_m1_bars(short), start=START, end=NOW, tolerance=0.02)
    assert n["matched"] == 497 and n["close_mismatch"] == 0
    assert n["missing_broker"] > 300 and n["missing_broker_short"] == n["missing_broker"]
    assert n["divergence"] == 0.0 and n["severity"] == "warning"


def test_broker_holes_inside_its_returned_span_still_diverge():
    eb = ticks_to_1m(day_ticks())
    m1 = as_broker_m1(eb)
    n = cf.reconcile(eb, cf.broker_m1_bars(m1.drop(index=range(400, 600))), start=START, end=NOW, tolerance=0.02)
    assert n["missing_broker"] == 200 and n["missing_broker_short"] == 0
    assert n["divergence"] > cf.ERROR_FRACTION and n["severity"] == "error"


def test_health_names_the_short_broker_coverage_and_warns(tmp_path):
    rep = cf.ReconcileReport(account_id="icm-demo", ts_utc=NOW, window_from=START, window_to=NOW, tolerance=0.02,
                             broker_minutes=497, engine_minutes=834, matched=497, close_mismatch=0, missing_engine=0,
                             missing_broker=337, missing_broker_short=337, max_close_diff=0.5, close_lag=83,
                             divergence=0.0, severity="warning")
    rep.save(tmp_path)
    c = cf.check_reconciliation(_health_ctx(tmp_path), "icm-demo")
    assert c.status == "warn"
    assert "broker returned 497 M1 minutes" in c.reason and "337 engine minutes" in c.reason
    assert "coverage" in c.reason and "83 closes inside the broker's minute range" in c.reason


def test_an_older_report_without_the_short_count_grades_as_before(tmp_path):
    raw = cf.ReconcileReport(account_id="icm-demo", ts_utc=NOW, window_from=START, window_to=NOW, tolerance=0.02,
                             broker_minutes=497, engine_minutes=834, matched=497, close_mismatch=0, missing_engine=0,
                             missing_broker=337, max_close_diff=0.5, divergence=0.4041, severity="error")
    raw.save(tmp_path)
    assert cf.check_reconciliation(_health_ctx(tmp_path), "icm-demo").status == "fail"


def _store_with(tmp_path: Any, ticks: pd.DataFrame) -> Store:
    store = Store(tmp_path / "data")
    store.append("ticks", ticks, source="icm-demo", dedupe=False)
    return store


def test_reconcile_account_fetches_the_window_by_range_and_matches(tmp_path):
    ticks = day_ticks()
    store = _store_with(tmp_path, _sampled_some(ticks))
    broker = RangeBroker(as_broker_m1(ticks_to_1m(ticks)))
    rep = cf.reconcile_account(store, broker, "icm-demo", "XAUUSD", NOW, state_dir=tmp_path)
    assert broker.ranges == [(START, NOW - pd.Timedelta(minutes=1))] and broker.calls == []   # range, inclusive
    assert rep.broker_fetch == "range"
    assert rep.matched == rep.engine_minutes == rep.broker_minutes > 1000
    assert rep.missing_broker == 0 and rep.close_mismatch == 0 and rep.close_lag > 0 and rep.severity == "ok"


def test_reconcile_account_short_broker_writes_a_coverage_warning_event(tmp_path):
    ticks = day_ticks()
    store = _store_with(tmp_path, ticks)
    m1 = as_broker_m1(ticks_to_1m(ticks))
    broker = RangeBroker(m1.tail(497).reset_index(drop=True))
    rep = cf.reconcile_account(store, broker, "icm-demo", "XAUUSD", NOW, state_dir=tmp_path)
    assert rep.severity == "warning" and rep.missing_broker_short == rep.missing_broker > 300
    ev = store.read("dq_events", source="icm-demo")
    assert list(ev["check"]) == ["reconcile_broker_short"] and set(ev["severity"]) == {"warning"}
    assert "coverage" in ev["detail"].iloc[0]


def test_reconcile_account_falls_back_to_last_n_bars_without_a_range_method(tmp_path):
    ticks = day_ticks()
    store = _store_with(tmp_path, ticks)
    broker = FakeBroker(as_broker_m1(ticks_to_1m(ticks)))
    rep = cf.reconcile_account(store, broker, "icm-demo", "XAUUSD", NOW)
    assert broker.calls == [("XAUUSD", "1m", cf.RECONCILE_BARS)] and rep.broker_fetch == "last_n"
    assert rep.severity == "ok"


def test_reconcile_account_falls_back_when_the_bridge_refuses_the_range_method(tmp_path):
    ticks = day_ticks()
    store = _store_with(tmp_path, ticks)

    class OldBridge(FakeBroker):
        def get_bars_range(self, *a: Any) -> pd.DataFrame:
            raise RuntimeError("bridge get_bars_range: HTTP 403 method 'get_bars_range' not allowed")
    broker = OldBridge(as_broker_m1(ticks_to_1m(ticks)))
    rep = cf.reconcile_account(store, broker, "icm-demo", "XAUUSD", NOW)
    assert broker.calls and rep.broker_fetch == "last_n" and "get_bars_range" in rep.note


# ---------------------------------------------------------------------------------------------- the bridge side
class _Terminal:
    server_tz = TZ

    def __init__(self, m1: pd.DataFrame) -> None:
        self.m1 = m1

    def get_bars_range(self, symbol: str, tf: str, start_utc: pd.Timestamp, end_utc: pd.Timestamp) -> pd.DataFrame:
        ts = pd.to_datetime(self.m1["ts_utc"], utc=True)
        return self.m1[(ts >= start_utc) & (ts <= end_utc)].reset_index(drop=True)


def test_get_bars_range_is_a_read_only_optional_bridge_method_served_through_the_guard():
    assert METHODS["get_bars_range"] == ("frame", True) and "get_bars_range" in OPTIONAL_METHODS
    assert "get_bars_range" not in REQUIRED_METHODS
    m1 = as_broker_m1(ticks_to_1m(day_ticks()))
    httpd, url = _serve(make_handler(GuardedBroker(_Terminal(m1), magic_base=260100), TOKEN))
    try:
        a, b = pd.Timestamp("2026-10-07 10:00", tz="UTC"), pd.Timestamp("2026-10-07 10:59", tz="UTC")
        got = RemoteBroker(url, TOKEN).get_bars_range("XAUUSD", "1m", a, b)
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert len(got) == 60 and pd.to_datetime(got["ts_utc"], utc=True).min() == a


def test_a_v5_bridge_without_the_range_method_stays_compatible():
    """The MT5 box can be deployed after the brain: an older bridge lacks get_bars_range and is still ok."""
    assert API_VERSION >= 6 and MIN_COMPATIBLE == 5          # v7 (BACKLOG 135) kept MIN_COMPATIBLE 5
    v5 = BridgeInfo(reachable=True, server_tz=TZ, api_version=5, min_compatible=5,
                    methods=sorted(set(METHODS) - {"get_bars_range"}))
    v = assess(v5, TZ, "a" * 40)
    assert v.status == "ok" and not v.blocks and v.missing == []


@pytest.mark.parametrize("rates", [None, []])
def test_mt5_get_bars_range_returns_an_empty_frame_when_the_terminal_has_none(monkeypatch, rates):
    from goldbot.execution import mt5_adapter
    from goldbot.execution.mt5_adapter import MT5Broker

    class FakeMT5:
        TIMEFRAME_M1 = 1
        seen: list[Any] = []

        def copy_rates_range(self, symbol: str, tf: int, a: Any, b: Any) -> Any:
            self.seen.append((symbol, tf, a, b))
            return rates
    fake = FakeMT5()
    monkeypatch.setattr(mt5_adapter, "mt5", fake, raising=False)
    br = MT5Broker.__new__(MT5Broker)
    br.server_tz = "Europe/Athens"
    out = br.get_bars_range("XAUUSD", "1m", START, NOW)
    assert out.empty and list(out.columns) == ["ts_utc", "open", "high", "low", "close", "tick_count", "spread"]
    assert fake.seen and fake.seen[0][0] == "XAUUSD"
