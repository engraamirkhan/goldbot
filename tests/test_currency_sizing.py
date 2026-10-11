"""BACKLOG 28 / ADR 0004 A3-A8: RiskGate sizes, checks margin and the combined-notional cap in the ACCOUNT currency.

The stop of an XAUUSD trade is a USD amount (stop x contract per lot); equity, broker margin and P&L are in the deposit
currency (GBP on the IC Markets demo). The broker converts: MT5 `order_calc_profit` for the stop move on one lot, else
`trade_tick_value / trade_tick_size`. A non-USD account with no conversion refuses entries (`fx_conversion_unavailable`);
exits never consult the gate."""
import itertools
import json
import math
import time
from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from goldbot.engine import Engine, EngineConfig
from goldbot.execution.bridge import METHODS, GuardedBroker, RemoteBroker
from goldbot.execution.broker import AccountInfo, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.risk import AccountState, Intent, RiskGate, RiskLimits
from goldbot.risk.gate import Stage
from goldbot.risk.supervisor import Supervisor
from goldbot.specialists.trend import TrendSpecialist
from goldbot.telegram.approvals import Proposal
from tests.test_margin_and_tick_dedup import _adapter, _fake_mt5

GBPUSD = 1.33
GBP = 1 / GBPUSD                     # account currency per USD of P&L on a GBP account
T0 = pd.Timestamp("2025-03-03 08:00", tz="UTC")


def _state(equity: float = 1_000.0, **kw: Any) -> AccountState:
    base: dict[str, Any] = dict(equity=equity, balance_closed_hwm=equity, day_start_equity=equity,
                                week_start_equity=equity, open_positions=0, margin_used=0.0, last_tick_age_s=1.0,
                                spread_points=25.0)
    base.update(kw)
    return AccountState(**base)


def _intent(*, stop: float = 10.90, mult: float = 1.5, price: float = 4_150.0, **kw: Any) -> Intent:
    base: dict[str, Any] = dict(agent_id="x", side=1, p=0.7, target_atr=3.0, stop_atr=1.0, atr_usd=stop, cost_atr=0.01,
                                multiplier=mult, price=price)
    base.update(kw)
    return Intent(**base)


def _gbp(**kw: Any) -> Intent:
    return _intent(account_currency="GBP", acct_per_usd=GBP, **kw)


# ---------------------------------------------------------------------------------------------- the gate
def test_a_gbp_account_sizes_the_15m_min_lot_at_its_true_risk_of_about_0_82_percent():
    """£1,000 at GBPUSD 1.33, $10.90 stop: one 0.01 lot loses $10.90 = £8.20, 0.82% (inside 1.2 x 0.75% target)."""
    d = RiskGate().check(_gbp(), _state(1_000.0))
    assert d.allowed, d.reasons
    assert d.lots == 0.01
    assert d.risk_fraction == pytest.approx(10.90 * 100 * 0.01 / GBPUSD / 1_000.0)
    assert d.risk_fraction == pytest.approx(0.0082, abs=5e-5)
    assert d.risk_amount == pytest.approx(10.90 / GBPUSD)        # £8.20 at the stop, account currency


def test_without_the_conversion_the_same_trade_would_read_1_09_percent_and_be_refused():
    # the pre-fix arithmetic (USD stop against GBP equity) is what a USD account of the same size sees
    d = RiskGate().check(_intent(), _state(1_000.0))
    assert not d.allowed and d.reasons == ["min_lot_exceeds_risk"]


def test_gbp_lots_scale_by_the_conversion_factor():
    gbp = RiskGate().check(_gbp(stop=5.0, mult=1.0), _state(100_000.0))
    usd = RiskGate().check(_intent(stop=5.0, mult=1.0), _state(100_000.0))
    assert gbp.allowed and usd.allowed
    # 0.5% of 100,000 = 500 in account currency; per lot 5 x 100 = $500 = £375.9 -> 1.33 lots vs 1.00 lot (USD)
    assert usd.lots == 1.0 and gbp.lots == 1.33


@pytest.mark.parametrize("fx", [None, 0.0, -0.75, math.nan, math.inf])
def test_a_non_usd_account_without_a_usable_conversion_refuses_entries(fx):
    d = RiskGate().check(_intent(account_currency="GBP", acct_per_usd=fx), _state(10_000.0))
    # a non-finite figure is refused first, by name (invalid_state); a None or non-positive one at sizing
    want = ["fx_conversion_unavailable"] if fx is None or math.isfinite(fx) else ["invalid_state", "invalid:acct_per_usd"]
    assert not d.allowed and d.reasons == want and d.lots == 0.0


def test_an_unknown_account_currency_refuses_entries():
    d = RiskGate().check(_intent(account_currency="", acct_per_usd=None), _state(10_000.0))
    assert d.reasons == ["fx_conversion_unavailable"]


def test_a_usd_account_refuses_a_broker_conversion_that_is_not_one():
    # XAUUSD P&L is USD: a USD account's factor is exactly 1; anything else is a wrong symbol or contract, never sized
    assert RiskGate().check(_intent(account_currency="USD", acct_per_usd=0.5), _state(10_000.0)).reasons == \
        ["fx_conversion_unavailable"]
    assert RiskGate().check(_intent(account_currency="USD", acct_per_usd=1.0), _state(10_000.0)).allowed
    assert RiskGate().check(_intent(account_currency="USD", acct_per_usd=None), _state(10_000.0)).allowed


def _old_gate_lots(equity: float, mult: float, risk: float, stop: float) -> tuple[bool, float]:
    """The pre-BACKLOG-28 sizing (gate.py before the fix), for the USD no-change grid."""
    lo, hi = (0.25, 1.5)
    m = min(max(mult, lo), hi)
    if risk * m > 0.01:
        m = 0.01 / risk
    stop = max(stop, 0.25)
    lots_raw = equity * risk * m / (stop * 100.0)
    lots = min(max(0.01, math.floor(lots_raw / 0.01 + 1e-9) * 0.01), 2.0)
    realised = lots * stop * 100.0 / equity
    if realised > 1.2 * risk * m and lots_raw < 0.01:
        return False, 0.0
    return True, round(lots, 2)


@pytest.mark.parametrize("equity,mult,risk,stop", list(itertools.product(
    [1_000.0, 9_167.0, 20_000.0, 100_000.0], [0.25, 1.0, 1.5], [0.001, 0.005], [0.5, 10.9, 55.0])))
def test_a_usd_account_decides_exactly_as_before(equity, mult, risk, stop):
    d = RiskGate(RiskLimits(risk_per_trade=risk)).check(_intent(stop=stop, mult=mult, price=100.0), _state(equity))
    allowed, lots = _old_gate_lots(equity, mult, risk, stop)
    assert d.allowed == allowed
    if allowed:
        assert d.lots == lots


def test_the_one_to_twenty_margin_figure_is_in_the_account_currency():
    d = RiskGate().check(_gbp(), _state(1_000.0))
    notional_gbp = 0.01 * 100 * 4_150.0 / GBPUSD                 # $4,150 = £3,120
    assert d.margin_needed == pytest.approx(notional_gbp / 20)    # £156, level 641% >= 300%


def test_the_broker_margin_is_already_in_the_account_currency_and_compared_like_with_like():
    # broker says £200 (> £156 at 1:20): the broker figure wins, untouched by the conversion
    d = RiskGate().check(_gbp(), _state(1_000.0), margin_required=lambda side, lots, price: 200.0)
    assert d.margin_needed == pytest.approx(200.0) and d.margin_note.startswith("broker")


def test_the_combined_notional_cap_compares_account_currency_with_account_equity():
    # £1,000: cap 6 x £1,000 = £6,000. One open 0.01 lot ($4,150 = £3,120) plus a new one (£3,120) = £6,241 > £6,000
    st = _state(1_000.0, open_lots=0.01, open_notional=4_150.0, open_positions=1)
    assert RiskGate().check(_gbp(), st).reasons == ["combined_exposure_cap"]
    # at £1,100 the cap is £6,600: two lots fit in GBP (the pre-fix USD comparison, $8,300 > 6,600, refused it)
    st = _state(1_100.0, open_lots=0.01, open_notional=4_150.0, open_positions=1)
    d = RiskGate().check(_gbp(), st)
    assert d.allowed and d.lots == 0.01, d.reasons


def test_a_10_oz_contract_changes_the_lots():
    big = RiskGate().check(_gbp(stop=5.0, mult=1.0), _state(10_000.0))
    small = RiskGate().check(_gbp(stop=5.0, mult=1.0, contract_oz=10.0), _state(10_000.0))
    assert big.lots == 0.13 and small.lots == 1.33


# ---------------------------------------------------------------------------------------------- brokers
def test_the_paper_broker_is_a_usd_account_with_factor_one():
    pb = PaperBroker(contract=100.0)
    assert pb.account().currency == "USD"
    assert pb.loss_per_lot("XAUUSD", 1, 4_150.0, 10.90) == pytest.approx(1_090.0)
    assert pb.loss_per_lot("XAUUSD", -1, 4_150.0, 10.90) == pytest.approx(1_090.0)


def test_mt5_loss_per_lot_comes_from_order_calc_profit_for_the_stop_move(monkeypatch):
    seen: list[tuple[Any, ...]] = []

    def calc(action: int, symbol: str, volume: float, open_: float, close: float) -> float:
        seen.append((action, symbol, volume, open_, close))
        return -(abs(open_ - close) * 100 * volume) / GBPUSD

    b = _adapter(monkeypatch, _fake_mt5(order_calc_profit=calc))
    assert b.loss_per_lot("XAUUSD", 1, 4_150.0, 10.90) == pytest.approx(1_090.0 / GBPUSD)
    assert b.loss_per_lot("XAUUSD", -1, 4_150.0, 10.90) == pytest.approx(1_090.0 / GBPUSD)
    assert seen[0] == (0, "XAUUSD", 1.0, 4_150.0, pytest.approx(4_139.10))
    assert seen[1] == (1, "XAUUSD", 1.0, 4_150.0, pytest.approx(4_160.90))


SymTV = namedtuple("SymTV", "visible trade_tick_value trade_tick_value_loss trade_tick_size")


def test_mt5_loss_per_lot_falls_back_to_tick_value_over_tick_size(monkeypatch):
    info = SymTV(True, 0.75, 0.7519, 0.01)       # per 0.01 move on one lot, in GBP (the loss value preferred)
    b = _adapter(monkeypatch, _fake_mt5(order_calc_profit=lambda *a: None, symbol_info=lambda s: info))
    assert b.loss_per_lot("XAUUSD", 1, 4_150.0, 10.90) == pytest.approx(10.90 / 0.01 * 0.7519)
    info0 = SymTV(True, 0.75, 0.0, 0.01)         # no loss-side value: the plain tick value
    b = _adapter(monkeypatch, _fake_mt5(order_calc_profit=lambda *a: None, symbol_info=lambda s: info0))
    assert b.loss_per_lot("XAUUSD", 1, 4_150.0, 10.90) == pytest.approx(10.90 / 0.01 * 0.75)


@pytest.mark.parametrize("info", [SymTV(True, 0.0, 0.0, 0.01), SymTV(True, 0.75, 0.75, 0.0),
                                  SymTV(True, math.nan, math.nan, 0.01)])
def test_mt5_loss_per_lot_is_none_when_neither_source_answers(monkeypatch, info):
    b = _adapter(monkeypatch, _fake_mt5(order_calc_profit=lambda *a: None, symbol_info=lambda s: info))
    assert b.loss_per_lot("XAUUSD", 1, 4_150.0, 10.90) is None


def test_mt5_loss_per_lot_survives_a_raising_terminal(monkeypatch):
    def boom(*a: Any) -> Any:
        raise RuntimeError("terminal down")
    b = _adapter(monkeypatch, _fake_mt5(order_calc_profit=boom, symbol_info=lambda s: SymTV(True, 0.75, 0.75, 0.01)))
    assert b.loss_per_lot("XAUUSD", 1, 4_150.0, 10.90) == pytest.approx(10.90 / 0.01 * 0.75)


def test_the_bridge_exposes_loss_per_lot_read_only_and_retry_safe():
    assert METHODS["loss_per_lot"] == ("float", True)
    pb = PaperBroker()
    assert GuardedBroker(pb, magic_base=260100).loss_per_lot("XAUUSD", 1, 4_150.0, 1.0) == pytest.approx(100.0)
    n = {"calls": 0}

    def flaky(*a: Any, **kw: Any) -> Any:
        import requests
        n["calls"] += 1
        if n["calls"] == 1:
            raise requests.ConnectionError("blip")
        return SimpleNamespace(status_code=200, json=lambda: {"result": 819.5})

    assert RemoteBroker("http://x", "t" * 40, post=flaky).loss_per_lot("XAUUSD", 1, 4_150.0, 10.9) == 819.5
    assert n["calls"] == 2


# ---------------------------------------------------------------------------------------------- the engine
class GbpPaper(PaperBroker):
    """A paper book whose deposit currency is GBP: deals carry profit and commission in GBP (as MT5 reports them)."""

    def __init__(self, fx: float | None = GBP, **kw: Any) -> None:
        super().__init__(**kw)
        self.fx = fx

    def account(self) -> AccountInfo:
        a = super().account()
        return a.model_copy(update={"currency": "GBP", "equity": a.equity * GBP, "balance": a.balance * GBP})

    def loss_per_lot(self, symbol: str, side: int, price: float, distance: float) -> float | None:
        return None if self.fx is None else distance * self.contract * self.fx

    def deals_since(self, since_utc: pd.Timestamp) -> pd.DataFrame:
        d = super().deals_since(since_utc)
        if d.empty:
            return d
        d = d.copy()
        for c in ("profit", "commission", "swap", "pnl"):
            if c in d.columns:
                d[c] = d[c] * GBP
        return d


def _engine(tmp_path: Path, pb: PaperBroker) -> Engine:
    return Engine(EngineConfig(account_id="icm-demo", broker_name="icm", mode="demo", state_dir=str(tmp_path),
                               owner_user_id=111), pb, [TrendSpecialist()], {})


def test_the_engine_reads_contract_terms_and_the_conversion_from_the_terminal(tmp_path):
    pb = GbpPaper(contract=10.0)
    pb.on_tick(Tick(ts_utc=T0, bid=4_150.0, ask=4_150.2))
    terms = _engine(tmp_path, pb)._sizing_terms(1, 4_150.2, 10.90)
    assert terms["contract_oz"] == 10.0 and terms["volume_min"] == 0.01 and terms["volume_step"] == 0.01
    assert terms["volume_max"] == 2.0                    # the engine's per-order ceiling stays (broker allows 50)
    assert terms["stops_level_points"] == 0.0 and terms["account_currency"] == "GBP"
    assert terms["acct_per_usd"] == pytest.approx(GBP)
    intent = _intent(**terms)
    assert intent.contract_oz == 10.0 and intent.acct_per_usd == pytest.approx(GBP)


def test_the_engine_leaves_the_conversion_out_when_the_broker_has_none(tmp_path):
    pb = GbpPaper(fx=None)
    pb.on_tick(Tick(ts_utc=T0, bid=4_150.0, ask=4_150.2))
    terms = _engine(tmp_path, pb)._sizing_terms(1, 4_150.2, 10.90)
    assert terms["acct_per_usd"] is None and terms["account_currency"] == "GBP"
    assert RiskGate().check(_intent(**terms), _state(10_000.0)).reasons == ["fx_conversion_unavailable"]


def test_an_unreadable_terminal_leaves_the_currency_unknown_and_the_entry_refused(tmp_path):
    class Broken(PaperBroker):
        def symbol_info(self, symbol: str) -> Any:
            raise RuntimeError("terminal down")
    pb = Broken()
    terms = _engine(tmp_path, pb)._sizing_terms(1, 4_150.2, 10.90)
    assert terms == {"account_currency": "", "acct_per_usd": None}
    assert RiskGate().check(_intent(**terms), _state(10_000.0)).reasons == ["fx_conversion_unavailable"]


def _enter(eng: Engine, pb: PaperBroker, price: float = 4_150.0, lots: float = 0.01) -> int:
    agent = TrendSpecialist()
    pb.on_tick(Tick(ts_utc=T0, bid=price, ask=price))
    stop, target = price - 10.90, price + 30.0
    prop = Proposal(proposal_id=f"icm-demo-{int(T0.timestamp())}-x", account_id="icm-demo", agent_id=agent.agent_id,
                    side=1, lots=lots, entry=price, stop=stop, target=target, p=0.6, ev_r=0.1, spread_points=0.0,
                    top_features=[])
    before = set(eng.open)
    eng._execute(prop, agent, lots, stop, target, requested=price, atr_usd=10.90)
    (pid,) = set(eng.open) - before
    return pid


def _records(tmp_path: Path) -> list[Any]:
    from goldbot.ops.gates_phase import load_closed_trades
    trades, err = load_closed_trades(tmp_path)
    assert err is None
    return trades


def test_closed_trade_r_and_return_use_the_account_currency(tmp_path):
    pb = GbpPaper(commission_per_lot_side=0.0, adverse_slip_points=0.0)
    eng = _engine(tmp_path, pb)
    _enter(eng, pb)
    pb.on_tick(Tick(ts_utc=T0 + pd.Timedelta(minutes=30), bid=4_150.0 - 10.90, ask=4_150.0 - 10.90))   # the stop
    eng._manage_open(pd.DataFrame())
    eng._save_orders()                                          # closes waiting for their record are written here
    (t,) = _records(tmp_path)
    assert t.pnl == pytest.approx(-10.90 / GBPUSD)              # £8.20 lost (deal profit in GBP)
    assert t.r == pytest.approx(-1.0)                           # was -0.75 when the risk stayed in USD
    assert t.ret == pytest.approx(-10.90 / 4_150.0)             # price return, not 1/1.33 of it


def test_closed_trade_r_is_left_empty_when_no_conversion_exists(tmp_path):
    pb = GbpPaper(fx=None, commission_per_lot_side=0.0, adverse_slip_points=0.0)
    eng = _engine(tmp_path, pb)
    _enter(eng, pb)
    pb.on_tick(Tick(ts_utc=T0 + pd.Timedelta(minutes=30), bid=4_150.0 - 10.90, ask=4_150.0 - 10.90))
    eng._manage_open(pd.DataFrame())
    eng._save_orders()                                          # closes waiting for their record are written here
    (t,) = _records(tmp_path)                                   # the exit and its record are never blocked
    assert t.r is None and t.ret == pytest.approx(-10.90 / 4_150.0)


def test_exits_are_unaffected_by_a_missing_conversion(tmp_path):
    """No conversion: entries are refused, but an open position's stop still closes and nothing asks the gate."""
    pb = GbpPaper(fx=None, adverse_slip_points=0.0)
    eng = _engine(tmp_path, pb)
    pid = _enter(eng, pb)
    calls: list[Any] = []
    real = eng.gate.check

    def spy(*a: Any, **k: Any) -> Any:
        calls.append(a)
        return real(*a, **k)

    eng.gate.check = spy                                                    # type: ignore[method-assign]
    pb.on_tick(Tick(ts_utc=T0 + pd.Timedelta(minutes=30), bid=4_150.0 - 11.0, ask=4_150.0 - 11.0))
    eng._manage_open(pd.DataFrame())
    assert pid not in eng.open and not pb.positions() and calls == []


def test_the_engine_publishes_its_account_currency(tmp_path):
    pb = GbpPaper()
    pb.on_tick(Tick(ts_utc=T0, bid=4_150.0, ask=4_150.2))
    eng = _engine(tmp_path, pb)
    eng._write_state()
    assert json.loads((tmp_path / "engine_icm-demo.json").read_text())["currency"] == "GBP"


# ---------------------------------------------------------------------------------------------- the supervisor (A8)
def _engine_file(d: Path, account: str, currency: str | None, equity: float = 1_000.0) -> None:
    payload: dict[str, Any] = {"account": account, "ts": time.time(), "equity": equity, "day_start_equity": equity,
                               "week_start_equity": equity, "balance_closed_hwm": equity}
    if currency is not None:
        payload["currency"] = currency
    (d / f"engine_{account}.json").write_text(json.dumps(payload))


def test_the_supervisor_refuses_to_combine_accounts_in_different_currencies(tmp_path):
    _engine_file(tmp_path, "icm-demo", "GBP")
    _engine_file(tmp_path, "vantage-demo", "USD")
    s = Supervisor(tmp_path).evaluate()
    assert s["halt"] and "mixed_account_currencies" in s["reasons"]
    assert s["currencies"] == {"icm-demo": "GBP", "vantage-demo": "USD"}
    assert Supervisor.engine_should_halt(tmp_path)[0]


def test_the_supervisor_treats_an_engine_without_a_currency_as_unknown_when_combining(tmp_path):
    _engine_file(tmp_path, "icm-demo", "GBP")
    _engine_file(tmp_path, "vantage-demo", None)
    assert "mixed_account_currencies" in Supervisor(tmp_path).evaluate()["reasons"]


def test_one_currency_combines_as_before(tmp_path):
    _engine_file(tmp_path, "icm-demo", "GBP")
    _engine_file(tmp_path, "vantage-demo", "gbp")
    s = Supervisor(tmp_path).evaluate()
    assert not s["halt"] and s["combined_equity"] == pytest.approx(2_000.0)
    _engine_file(tmp_path, "vantage-demo", None)
    (tmp_path / "engine_vantage-demo.json").unlink()
    assert not Supervisor(tmp_path).evaluate()["halt"]           # one engine without a currency mixes nothing


def test_stage_is_unaffected_by_the_currency():
    st = _state(10_000.0, balance_closed_hwm=10_000.0 / 0.94, stage=Stage.SIZE_DOWN)
    d = RiskGate().check(_gbp(stop=2.0, mult=1.0), st)
    assert d.allowed and st.stage == Stage.SIZE_DOWN
