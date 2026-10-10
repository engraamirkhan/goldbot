"""The engine publishes what the dashboard's account view needs (owner request, ADR 0004): the broker's balance,
currency and margin (the margin level is equity / margin), the RiskGate's limits, the price, the symbol's lot terms and ATR(14) per ladder timeframe. The
login and server never reach the state file (public repo, S4)."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.engine import Engine, EngineConfig
from goldbot.execution.broker import AccountInfo, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.risk.supervisor import Supervisor
from goldbot.telegram.approvals import ApprovalCenter

T0 = pd.Timestamp("2025-03-05 10:00", tz="UTC")


class _GbpBroker(PaperBroker):
    def account(self) -> AccountInfo:
        a = super().account()
        return a.model_copy(update={"login": 12345678, "server": "Broker-Demo", "currency": "GBP", "margin": 156.0})


def _engine(tmp_path: Path, pb: PaperBroker) -> Engine:
    pb.on_tick(Tick(ts_utc=T0, bid=4150.0, ask=4150.2))
    return Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), owner_user_id=1),
                  pb, [], {}, ApprovalCenter({1}))


def test_state_file_carries_balance_currency_margin_level_and_limits_but_never_the_login_or_server(tmp_path):
    eng = _engine(tmp_path, _GbpBroker(equity=1000.0))
    eng._refresh_account(Tick(ts_utc=T0, bid=4150.0, ask=4150.2))
    eng._write_state()
    raw = (tmp_path / "engine_icm-demo.json").read_text()
    st = json.loads(raw)
    assert (st["balance"], st["currency"], st["margin"]) == (1000.0, "GBP", 156.0)
    assert st["price"] == pytest.approx(4150.1)
    assert st["limits"]["risk_per_trade"] == eng.gate.limits.risk_per_trade
    assert st["limits"]["daily_cap"] == eng.gate.limits.daily_cap
    assert st["lot_terms"]["volume_min"] == 0.01 and st["lot_terms"]["contract_oz"] == 100.0
    assert "12345678" not in raw and "Broker-Demo" not in raw and "login" not in st and "server" not in st


def test_a_paper_account_publishes_its_own_currency_and_zero_margin(tmp_path):
    eng = _engine(tmp_path, PaperBroker(equity=10_000.0))
    eng._refresh_account(Tick(ts_utc=T0, bid=4150.0, ask=4150.2))
    eng._write_state()
    st = json.loads((tmp_path / "engine_icm-demo.json").read_text())
    assert (st["currency"], st["balance"], st["margin"]) == ("USD", 10_000.0, 0.0)


def _bars(days: int, rng: float) -> pd.DataFrame:
    ts = pd.date_range(T0 - pd.Timedelta(days=days), periods=days * 1440, freq="1min", tz="UTC")
    mid = 4150.0 + np.sin(np.arange(len(ts)) / 50.0) * 5
    half = rng / 2
    return pd.DataFrame({
        "ts_utc": ts, "visible_at": ts + pd.Timedelta(minutes=1),
        "bid_open": mid, "bid_high": mid + half, "bid_low": mid - half, "bid_close": mid,
        "ask_open": mid + 0.2, "ask_high": mid + half + 0.2, "ask_low": mid - half + 0.2, "ask_close": mid + 0.2,
        "tick_count": 10, "spread_mean": 20.0, "spread_max": 25.0})


def test_ladder_atr_is_published_per_timeframe_and_none_where_the_history_is_too_short(tmp_path):
    eng = _engine(tmp_path, PaperBroker(equity=10_000.0))
    eng._update_ladder(_bars(5, 1.0))                     # 5 days: enough for 15m/1h/4h ATR(14), not for 1d
    atr = eng._ladder["ladder_atr"]
    assert set(atr) == {"15m", "1h", "4h", "1d"}
    assert atr["15m"] > 0 and atr["1h"] > 0 and atr["4h"] > 0 and atr["1d"] is None
    wider = _engine(tmp_path / "w", PaperBroker(equity=10_000.0))
    wider._update_ladder(_bars(5, 2.0))
    assert wider._ladder["ladder_atr"]["1h"] > atr["1h"]


def test_supervisor_publishes_its_daily_cap_and_the_combined_day_start_equity(tmp_path):
    for acc, eq, d0 in (("a", 990.0, 1000.0), ("b", 2000.0, 2000.0)):
        (tmp_path / f"engine_{acc}.json").write_text(json.dumps({
            "account": acc, "equity": eq, "day_start_equity": d0, "week_start_equity": d0,
            "balance_closed_hwm": d0, "ts": 9e12}))
    st = Supervisor(tmp_path).evaluate()
    assert st["daily_cap"] == 0.015 and st["combined_day_start_equity"] == 3000.0
    assert st["weekly_cap"] == 0.04 and st["combined_week_start_equity"] == 3000.0


def test_a_failing_ladder_update_or_symbol_info_never_stops_the_bar_close(tmp_path, caplog):
    from goldbot.data.resample import ticks_to_1m
    from goldbot.data.synthetic import synthetic_ticks

    class _NoInfo(PaperBroker):
        def symbol_info(self, symbol):
            raise RuntimeError("terminal busy")

    ticks = synthetic_ticks("2025-03-03", "2025-03-05", ticks_per_minute=1, seed=5)
    last = pd.Timestamp(ticks["ts_utc"].iloc[-1])
    px = float(ticks["bid"].iloc[-1])
    pb = _NoInfo(equity=10_000.0)
    pb.on_tick(Tick(ts_utc=last, bid=px, ask=px + 0.2))
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), owner_user_id=1),
                 pb, [], {}, ApprovalCenter({1}))
    eng.bars_1m = ticks_to_1m(ticks)

    def boom(_complete):
        raise ValueError("bad bars")

    eng._update_ladder = boom  # type: ignore[method-assign]
    close_ts = (last + pd.Timedelta(minutes=15)).floor("15min")
    with caplog.at_level("WARNING", logger="goldbot.engine"):
        eng.on_bar_close(close_ts, pb.last_tick("XAUUSD"))
        eng._refresh_account(pb.last_tick("XAUUSD"))          # symbol_info raises again: no second warning
    st = json.loads((tmp_path / "engine_icm-demo.json").read_text())
    assert st["lot_terms"] is None and st["balance"] == 10_000.0      # the bar close completed and wrote state
    assert sum("milestone ATR not updated" in r.message for r in caplog.records) == 1
    assert sum("symbol info unavailable" in r.message for r in caplog.records) == 1


def test_a_ladder_timeframe_that_failed_is_retried_at_the_next_close(tmp_path, monkeypatch):
    eng = _engine(tmp_path, PaperBroker(equity=10_000.0))
    calls = {"n": 0}
    real = eng._resample

    def flaky(complete, tf):
        calls["n"] += 1
        if tf == "1h" and calls["n"] < 3:
            raise RuntimeError("once")
        return real(complete, tf)

    monkeypatch.setattr(eng, "_resample", flaky)
    bars = _bars(5, 1.0)
    with pytest.raises(RuntimeError):
        eng._update_ladder(bars)
    eng._update_ladder(bars)                                  # same bars: 1h was not marked done, so it is computed
    assert eng._ladder["ladder_atr"]["1h"] is not None
