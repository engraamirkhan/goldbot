"""Broker-calculated margin in the RiskGate (design: Hard limits, "checked with order_calc_margin and against the FCA
retail cap of 1:20; rejected if projected margin level would fall below 300%"; TRACEABILITY R8) and the live tick dedup
key (design: Live collector "dedups on (time_msc, bid, ask, flags)"; TRACEABILITY D11)."""
import threading
from collections import namedtuple
from http.server import HTTPServer
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.execution import mt5_adapter
from goldbot.execution.bridge import METHODS, GuardedBroker, RemoteBroker, make_handler
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker
from goldbot.risk import AccountState, Intent, RiskGate
from goldbot.risk.gate import broker_margin

TOKEN = "t" * 40
T0 = pd.Timestamp("2025-03-05 10:00", tz="UTC")


def _state(**kw: Any) -> AccountState:
    base: dict[str, Any] = dict(equity=10_000, balance_closed_hwm=10_000, day_start_equity=10_000, week_start_equity=10_000,
                                open_positions=0, margin_used=0.0, last_tick_age_s=1.0, spread_points=25.0)
    base.update(kw)
    return AccountState(**base)


def _intent(**kw: Any) -> Intent:
    base: dict[str, Any] = dict(agent_id="trend-g0-x", side=1, p=0.62, target_atr=1.5, stop_atr=1.0, atr_usd=4.0,
                                cost_atr=0.1, multiplier=1.0, price=2400.0)
    base.update(kw)
    return Intent(**base)


# ------------------------------------------------------------------------------------------------ R8: the gate
def test_the_gate_takes_the_stricter_of_the_broker_margin_and_the_one_to_twenty_cap():
    g = RiskGate()
    # 0.12 lots x 100 oz x 2400 / 20 = $1,440 at the FCA cap
    cap = g.check(_intent(), _state())
    assert cap.allowed and cap.lots == 0.12 and cap.margin_needed == pytest.approx(1_440.0)
    # a broker that asks for more (e.g. 1:10 on gold) wins: $2,880
    calls: list[tuple[int, float, float]] = []

    def strict(side: int, lots: float, price: float) -> float:
        calls.append((side, lots, price))
        return lots * 100 * price / 10

    d = g.check(_intent(), _state(), margin_required=strict)
    assert d.allowed and d.margin_needed == pytest.approx(2_880.0) and "broker" in d.margin_note
    assert len(calls) == 1 and calls[0][0] == 1 and calls[0][1] == pytest.approx(0.12) and calls[0][2] == 2400.0
    # ... and can refuse a trade the 1:20 figure lets through: 10,000 / (500 + 2,880) < 3 but 10,000 / (500 + 1,440) > 3
    assert g.check(_intent(), _state(margin_used=500)).allowed
    refused = g.check(_intent(), _state(margin_used=500), margin_required=strict)
    assert not refused.allowed and refused.reasons == ["margin_level_floor"]
    # a broker that asks for less never loosens the 1:20 figure
    lax = g.check(_intent(), _state(), margin_required=lambda s, lots, px: lots * 100 * px / 500)
    assert lax.allowed and lax.margin_needed == pytest.approx(1_440.0) and "1:20" in lax.margin_note
    assert not g.check(_intent(), _state(margin_used=2_000), margin_required=lambda s, lots, px: 1.0).allowed


@pytest.mark.parametrize("answer", [None, float("nan"), float("inf"), -5.0, "n/a", "boom"])
def test_the_gate_falls_back_to_one_to_twenty_and_says_why_when_the_broker_cannot_answer(answer, caplog):
    def broker(side: int, lots: float, price: float) -> float | None:
        if answer == "boom":
            raise ConnectionError("bridge down")
        return answer

    g = RiskGate()
    d = g.check(_intent(), _state(), margin_required=broker)
    assert d.allowed and d.margin_needed == pytest.approx(1_440.0)
    assert "fallback" in d.margin_note and "1:20" in d.margin_note
    if answer == "boom":
        assert "ConnectionError" in d.margin_note
    # never fails open: the fallback still refuses what 1:20 refuses
    crowded = g.check(_intent(), _state(margin_used=2_000), margin_required=broker)
    assert not crowded.allowed and crowded.reasons == ["margin_level_floor"]
    assert "fallback" in crowded.margin_note
    # no broker source at all (an engine not yet wired) is the same fallback, recorded
    assert "fallback" in g.check(_intent(), _state()).margin_note


def test_the_300_percent_margin_level_rule_at_its_boundary():
    g = RiskGate()
    # margin level after = 10,000 / (used + 1,440); exactly 300% at used = 10,000 / 3 - 1,440
    at = 10_000 / 3 - 1_440
    assert g.check(_intent(), _state(margin_used=at - 1e-6)).allowed                      # 300.0000...% passes
    below = g.check(_intent(), _state(margin_used=at + 0.01))
    assert not below.allowed and below.reasons == ["margin_level_floor"]
    # the same boundary with the broker's (larger) figure: used = 10,000 / 3 - 2,000
    bm = lambda s, lots, px: 2_000.0                                                       # noqa: E731
    at_b = 10_000 / 3 - 2_000
    assert g.check(_intent(), _state(margin_used=at_b - 1e-6), margin_required=bm).allowed
    assert not g.check(_intent(), _state(margin_used=at_b + 0.01), margin_required=bm).allowed


def test_broker_margin_wraps_a_broker_and_brokers_without_the_method_give_no_source():
    pb = PaperBroker(equity=10_000)
    fn = broker_margin(pb, "XAUUSD")
    assert fn is not None and fn(1, 0.5, 2400.0) == pytest.approx(0.5 * 100 * 2400.0 / 20)
    assert broker_margin(object(), "XAUUSD") is None


# ------------------------------------------------------------------------------------------------ R8: brokers
def test_the_paper_broker_computes_margin_at_one_to_twenty():
    pb = PaperBroker(equity=10_000)
    assert pb.margin_required("XAUUSD", 1, 0.12, 2400.0) == pytest.approx(1_440.0)
    assert pb.margin_required("XAUUSD", -1, 1.0, 2500.0) == pytest.approx(12_500.0)


def test_the_bridge_exposes_margin_required_as_a_retry_safe_read():
    assert METHODS["margin_required"] == ("float", True)
    pb = PaperBroker(equity=10_000)
    pb.on_tick(Tick(ts_utc=T0, bid=2400.0, ask=2400.2))
    httpd = HTTPServer(("127.0.0.1", 0), make_handler(GuardedBroker(pb, magic_base=260100), TOKEN))
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    try:
        rb = RemoteBroker(f"http://127.0.0.1:{httpd.server_address[1]}", TOKEN)
        assert rb.margin_required("XAUUSD", 1, 0.12, 2400.0) == pytest.approx(1_440.0)
    finally:
        httpd.shutdown()
        httpd.server_close()
    # retried once like every read
    n = {"calls": 0}

    def flaky(*a: Any, **kw: Any) -> Any:
        import requests
        n["calls"] += 1
        if n["calls"] == 1:
            raise requests.ConnectionError("blip")
        return SimpleNamespace(status_code=200, json=lambda: {"result": 2_880.0})

    assert RemoteBroker("http://x", TOKEN, post=flaky).margin_required("XAUUSD", 1, 0.12, 2400.0) == 2_880.0
    assert n["calls"] == 2


# ------------------------------------------------------------------------------------------------ the MT5 adapter
MTick = namedtuple("MTick", "time_msc bid ask flags")
Sym = namedtuple("Sym", "visible")


def _fake_mt5(**kw: Any) -> SimpleNamespace:
    base: dict[str, Any] = dict(initialize=lambda **k: True, symbol_select=lambda s, on: True, symbol_info=lambda s: Sym(True),
                                last_error=lambda: (1, "err"), shutdown=lambda: None, ORDER_TYPE_BUY=0, ORDER_TYPE_SELL=1,
                                COPY_TICKS_ALL=-1, symbol_info_tick=lambda s: MTick(1_741_168_800_000, 2400.0, 2400.2, 6))
    base.update(kw)
    return SimpleNamespace(**base)


def _adapter(monkeypatch: pytest.MonkeyPatch, fake: SimpleNamespace) -> mt5_adapter.MT5Broker:
    monkeypatch.setattr(mt5_adapter, "mt5", fake)
    return mt5_adapter.MT5Broker(terminal_path="", login=None, password=None, server="s", server_tz="Europe/Athens",
                                 symbol="XAUUSD", account_label="icm-demo")


def test_mt5_margin_comes_from_order_calc_margin_with_the_order_type_and_none_on_error(monkeypatch):
    seen: list[tuple[Any, ...]] = []

    def calc(action: int, symbol: str, volume: float, price: float) -> float | None:
        seen.append((action, symbol, volume, price))
        return 2_880.0

    b = _adapter(monkeypatch, _fake_mt5(order_calc_margin=calc))
    assert b.margin_required("XAUUSD", 1, 0.12, 2400.0) == 2_880.0
    assert b.margin_required("XAUUSD", -1, 0.12, 2399.8) == 2_880.0
    assert seen == [(0, "XAUUSD", 0.12, 2400.0), (1, "XAUUSD", 0.12, 2399.8)]
    assert _adapter(monkeypatch, _fake_mt5(order_calc_margin=lambda *a: None)).margin_required("XAUUSD", 1, 0.1, 2400.0) is None


def test_mt5_ticks_carry_flags_and_the_dedup_keeps_ticks_that_differ_only_by_flags(monkeypatch):
    ms = 1_741_168_800_000
    raw = np.array([(ms, 2400.0, 2400.2, 2), (ms, 2400.0, 2400.2, 4), (ms, 2400.0, 2400.2, 4), (ms + 1, 2400.0, 2400.2, 2)],
                   dtype=[("time_msc", "i8"), ("bid", "f8"), ("ask", "f8"), ("flags", "u4")])
    b = _adapter(monkeypatch, _fake_mt5(copy_ticks_from=lambda *a: raw))
    df = b.copy_ticks("XAUUSD", T0)
    assert list(df.columns) == ["ts_utc", "bid", "ask", "flags"] and df["flags"].tolist() == [2, 4, 2]
    t = b.last_tick("XAUUSD")
    assert t.flags == 6 and t.ts_utc.tzinfo is not None
    # the live stream's dedup key: same time, bid and ask but different flags are two ticks
    a = Tick(ts_utc=T0, bid=2400.0, ask=2400.2, flags=2)
    assert mt5_adapter.tick_key(a) != mt5_adapter.tick_key(a.model_copy(update={"flags": 4}))
    assert mt5_adapter.tick_key(a) == mt5_adapter.tick_key(a.model_copy())


def test_the_tick_record_stays_backward_compatible():
    t = Tick(ts_utc=T0, bid=1.0, ask=2.0)                   # older callers and stored payloads have no flags
    assert t.flags == 0
    assert Tick.model_validate({"ts_utc": T0, "bid": 1.0, "ask": 2.0}) == t
