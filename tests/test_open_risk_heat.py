"""BACKLOG 19 (playbook G-4) and 35 (trading-safety review MEDIUM-4): the open-risk heat cap, the same-direction cap and
the open-risk daily projection. All off by default; the owner sets the values.

* `risk.heat` (enabled): an entry is refused `heat_cap` when the open positions' remaining stop risk plus this trade's
  exceeds `max_open_risk` x equity, and `same_direction_cap` once `max_same_direction` positions on its side are open
  (whatever agent opened them: same-side positions of different agents are one bet).
* `risk.project_open_risk` (with the account's absolute daily limit): the projection is loss so far + the open
  positions' remaining stop risk + this trade's risk, so two open trades cannot carry the day past the limit.
* Remaining stop risk of a position: max(0, loss from the current close price to its SL) x lots x contract x fx, in
  the account currency. No SL or an unknown conversion makes the total unknown, which refuses (`open_risk_unknown`)
  when either rule is on (fail closed). Entries only: exits never consult any of it."""
import itertools
import json
from typing import Any

import pandas as pd
import pytest
from pydantic import ValidationError

from goldbot.config import HeatSettings, RiskSettings, load_settings
from goldbot.execution.broker import OrderIntent
from goldbot.execution.paper import PaperBroker
from goldbot.risk import AccountState, Intent, RiskGate, RiskLimits
from goldbot.risk.open_risk import remaining_stop_risk
from goldbot.specialists.trend import TrendSpecialist
from goldbot.telegram.approvals import Proposal
from tests.test_currency_sizing import GBP, GbpPaper
from tests.test_engine_clock import WED, _engine, _tick

ON = HeatSettings(enabled=True, max_open_risk=0.02, max_same_direction=2)


def _state(equity: float = 10_000.0, day0: float | None = None, **kw: Any) -> AccountState:
    d0 = equity if day0 is None else day0
    base: dict[str, Any] = dict(equity=equity, balance_closed_hwm=max(equity, d0), day_start_equity=d0,
                                week_start_equity=d0, open_positions=0, margin_used=0.0, last_tick_age_s=1.0,
                                spread_points=25.0)
    base.update(kw)
    return AccountState(**base)


def _usd(stop: float = 5.0, mult: float = 1.0, side: int = 1) -> Intent:
    """At 10,000 equity, 0.5% risk and m = 1 a $5 stop sizes 0.10 lot = 50 at the stop."""
    return Intent(agent_id="x", side=side, p=0.7, target_atr=3.0, stop_atr=1.0, atr_usd=stop, cost_atr=0.01,
                  multiplier=mult, price=100.0, account_currency="USD")


# ---------------------------------------------------------------------------------------------- off by default
def test_settings_ship_the_heat_cap_and_the_projection_off():
    risk = load_settings().risk
    assert risk.heat.enabled is False and risk.project_open_risk is False
    assert (risk.heat.max_open_risk, risk.heat.max_same_direction) == (0.02, 2)
    lim = RiskLimits.from_settings(risk, tiny_live=False)
    assert lim.heat == risk.heat and lim.project_open_risk is False
    assert RiskLimits.from_settings(risk.model_copy(update={"heat": ON, "project_open_risk": True}),
                                    tiny_live=True).heat.enabled                 # every phase, tiny-live included


@pytest.mark.parametrize("bad", [{"max_open_risk": 0}, {"max_open_risk": -0.01}, {"max_open_risk": 1.0},
                                 {"max_same_direction": 0}])
def test_heat_settings_refuse_values_that_make_no_sense(bad):
    with pytest.raises(ValidationError):
        HeatSettings(**bad)
    assert "project_open_risk" in RiskSettings.model_fields


def test_disabled_rules_give_identical_decisions_whatever_the_open_risk_facts():
    """Off (the default), the new facts are never read: unknown or huge open risk and many same-side positions change
    nothing, on a grid of equities, stops, sides, day losses and absolute limits."""
    for equity, stop, side, lost, abs_limit in itertools.product((1_000.0, 5_000.0, 10_000.0), (2.0, 5.0, 12.0),
                                                                 (1, -1), (0.0, 40.0), (None, 50.0)):
        gate = RiskGate(RiskLimits(daily_loss_limit_abs=abs_limit))
        plain = gate.check(_usd(stop, side=side), _state(equity, equity + lost))
        for facts in ({"open_risk": None}, {"open_risk": 1e9}, {"open_long": 9, "open_short": 9}):
            assert gate.check(_usd(stop, side=side), _state(equity, equity + lost, **facts)) == plain


# ---------------------------------------------------------------------------------------------- heat cap (19)
def test_the_heat_cap_allows_up_to_the_cap_and_refuses_past_it():
    gate = RiskGate(RiskLimits(heat=ON))                       # 2% of 10,000 = 200
    at = gate.check(_usd(), _state(open_risk=150.0))           # 150 open + 50 new = 200: allowed (not exceeded)
    assert at.allowed and at.risk_amount == pytest.approx(50.0)
    past = gate.check(_usd(), _state(open_risk=150.5))
    assert not past.allowed and past.reasons == ["heat_cap"]
    assert RiskGate(RiskLimits()).check(_usd(), _state(open_risk=150.5)).allowed     # off: as before


def test_the_heat_cap_counts_a_minimum_lot_exception_trade_in_full():
    # BACKLOG 25: £1,000 GBP, 15m stop $10.90, 0.01 lot = £8.20 under the exception; cap 2% = £20
    i = Intent(agent_id="x", side=1, p=0.7, target_atr=3.0, stop_atr=1.0, atr_usd=10.90, cost_atr=0.01, multiplier=1.0,
               price=4_150.0, account_currency="GBP", acct_per_usd=GBP)
    gate = RiskGate(RiskLimits(min_lot_risk_cap=0.01, heat=ON))
    ok = gate.check(i, _state(1_000.0, open_risk=11.0))
    assert ok.allowed and "min_lot_exception" in ok.reasons                  # 11 + 8.20 = 19.20 <= 20
    assert gate.check(i, _state(1_000.0, open_risk=12.0)).reasons == ["heat_cap"]   # 20.20 > 20


def test_the_same_direction_cap_refuses_a_third_same_side_position_only():
    gate = RiskGate(RiskLimits(heat=ON, max_positions=5))
    assert gate.check(_usd(side=1), _state(open_long=1)).allowed
    refused = gate.check(_usd(side=1), _state(open_long=2))
    assert not refused.allowed and refused.reasons == ["same_direction_cap"]
    assert gate.check(_usd(side=-1), _state(open_long=2, open_short=1)).allowed     # the other side is not stacking
    assert gate.check(_usd(side=-1), _state(open_short=2)).reasons == ["same_direction_cap"]


def test_unknown_open_risk_refuses_when_the_heat_cap_is_on():
    d = RiskGate(RiskLimits(heat=ON)).check(_usd(), _state(open_risk=None))
    assert not d.allowed and d.reasons == ["open_risk_unknown"]


# ---------------------------------------------------------------------------------------------- projection (35)
def test_the_projection_counts_two_open_trades_remaining_stop_risk():
    # £5,000, nothing lost yet, limit min(2% = 100, 50) = 50; two open trades could still lose 20 + 20 = 40
    lim: dict[str, Any] = dict(daily_loss_limit_abs=50.0, risk_per_trade=0.003, max_positions=3)   # 0.3% of 5,000 = 15 at the stop
    st: dict[str, Any] = dict(open_positions=2, open_long=2, open_risk=40.0)
    assert RiskGate(RiskLimits(**lim)).check(_usd(), _state(5_000.0, **st)).allowed           # off: 0 + 15 <= 50
    on = RiskGate(RiskLimits(project_open_risk=True, **lim))
    refused = on.check(_usd(), _state(5_000.0, **st))
    assert not refused.allowed and refused.reasons == ["daily_loss_limit_abs"]                 # 40 + 15 = 55 > 50
    assert on.check(_usd(), _state(5_000.0, **{**st, "open_risk": 35.0})).allowed            # 35 + 15 = 50: allowed
    assert on.check(_usd(), _state(5_000.0, 5_010.0, **{**st, "open_risk": 26.0})).reasons == \
        ["daily_loss_limit_abs"]                                                 # 10 lost + 26 + 15 = 51 > 50


def test_the_projection_refuses_on_unknown_open_risk_only_when_on_and_a_limit_is_set():
    st = _state(5_000.0, open_risk=None)
    assert RiskGate(RiskLimits(project_open_risk=True, daily_loss_limit_abs=50.0)).check(
        _usd(3.0), st).reasons == ["open_risk_unknown"]
    assert RiskGate(RiskLimits(project_open_risk=True)).check(_usd(3.0), st).allowed      # no absolute limit: inert
    assert RiskGate(RiskLimits(daily_loss_limit_abs=50.0)).check(_usd(3.0), st).allowed   # off


# ---------------------------------------------------------------------------------------------- remaining stop risk
def test_remaining_stop_risk_is_the_loss_from_the_close_price_to_the_stop():
    # a long closes at the bid, a short at the ask; 0.1 lot x 100 oz
    assert remaining_stop_risk(side=1, lots=0.1, sl=1_990.0, bid=1_995.0, ask=1_995.2, contract=100.0,
                               fx=1.0) == pytest.approx(50.0)
    assert remaining_stop_risk(side=-1, lots=0.1, sl=2_010.0, bid=2_000.0, ask=2_000.2, contract=100.0,
                               fx=GBP) == pytest.approx(98.0 * GBP)
    # a stop already in profit still counts what is left to it; a price through the stop counts nothing more
    assert remaining_stop_risk(side=1, lots=0.1, sl=2_001.0, bid=2_005.0, ask=2_005.2, contract=100.0,
                               fx=1.0) == pytest.approx(40.0)
    assert remaining_stop_risk(side=1, lots=0.1, sl=1_990.0, bid=1_989.0, ask=1_989.2, contract=100.0, fx=None) == 0.0


@pytest.mark.parametrize("kw", [{"sl": None}, {"sl": 0.0}, {"sl": float("nan")}, {"fx": None}, {"fx": float("inf")},
                                {"lots": float("nan")}, {"contract": 0.0}])
def test_remaining_stop_risk_is_unknown_without_a_stop_or_a_conversion(kw):
    args: dict[str, Any] = dict(side=1, lots=0.1, sl=1_990.0, bid=1_995.0, ask=1_995.2, contract=100.0, fx=1.0)
    assert remaining_stop_risk(**{**args, **kw}) is None


# ---------------------------------------------------------------------------------------------- engine facts
def _open(pb: PaperBroker, side: int, sl: float | None, cid: str, lots: float = 0.1) -> int:
    res = pb.place_order(OrderIntent(client_order_id=cid, symbol="XAUUSD", side=side, lots=lots, sl=sl or 0.0,
                                     tp=0.0, magic=1))
    assert res.position_id is not None
    if sl is None:
        pb._positions[res.position_id].sl = None
    return res.position_id


def _published(tmp_path: Any) -> dict:
    return json.loads((tmp_path / "engine_icm-demo.json").read_text())


def test_the_engine_publishes_each_positions_remaining_stop_risk_and_the_side_counts(tmp_path):
    eng, pb, _ = _engine(tmp_path)                             # tick bid 2400.0 / ask 2400.2
    a = _open(pb, 1, 2_390.0, "a")                             # long: (2400.0 - 2390) x 0.1 x 100 = 100
    b = _open(pb, 1, 2_395.0, "b", lots=0.2)                   # long: 5 x 0.2 x 100 = 100
    c = _open(pb, -1, 2_410.2, "c")                            # short: (2410.2 - 2400.2) x 0.1 x 100 = 100
    eng._refresh_account(_tick(WED))
    st = eng.state
    assert st.open_risk == pytest.approx(300.0) and (st.open_long, st.open_short) == (2, 1)
    eng._write_state()
    e = _published(tmp_path)
    assert e["open_risk"] == pytest.approx(300.0) and (e["open_long"], e["open_short"]) == (2, 1)
    assert {p["position_id"]: p["risk"] for p in e["open_risk_positions"]} == \
        pytest.approx({a: 100.0, b: 100.0, c: 100.0})
    assert e["limits"]["heat"] == {"enabled": False, "max_open_risk": 0.02, "max_same_direction": 2}
    assert e["limits"]["project_open_risk"] is False


def test_a_position_without_a_stop_makes_the_open_risk_unknown_and_refuses_when_on(tmp_path):
    eng, pb, _ = _engine(tmp_path)
    _open(pb, 1, 2_390.0, "a")
    _open(pb, -1, None, "manual")                              # a manual trade without SL: unbounded
    eng._refresh_account(_tick(WED))
    assert eng.state.open_risk is None
    eng._write_state()
    e = _published(tmp_path)
    assert e["open_risk"] is None and [p["risk"] for p in e["open_risk_positions"]] == [pytest.approx(100.0), None]
    eng.gate.limits = RiskLimits(heat=ON, max_positions=3)
    assert eng.gate.check(_usd(), eng.state).reasons == ["open_risk_unknown"]


def test_an_unknown_conversion_on_a_gbp_account_makes_the_open_risk_unknown(tmp_path):
    pb = GbpPaper(fx=None, equity=10_000)
    pb.on_tick(_tick(WED))
    eng, _, _ = _engine(tmp_path, pb=pb)
    _open(pb, 1, 2_390.0, "a")
    eng._refresh_account(_tick(WED))
    assert eng.state.open_risk is None
    pb.fx = GBP
    eng._refresh_account(_tick(WED + pd.Timedelta(minutes=1)))     # the conversion is read again the next minute
    assert eng.state.open_risk == pytest.approx(100.0 * GBP)


def test_two_open_trades_through_the_engine_block_an_entry_past_the_daily_limit(tmp_path):
    eng, pb, _ = _engine(tmp_path)
    _open(pb, 1, 2_398.0, "a")                                 # 2.0 x 0.1 x 100 = 20 each
    _open(pb, 1, 2_398.0, "b")
    eng._refresh_account(_tick(WED))
    eng.state.day_start_equity = eng.state.equity              # nothing lost yet today
    i = _usd(stop=3.0, mult=0.3)                               # 0.15% of ~10,000 = ~15
    assert RiskGate(RiskLimits(daily_loss_limit_abs=50.0, max_positions=3)).check(i, eng.state).allowed
    d = RiskGate(RiskLimits(daily_loss_limit_abs=50.0, max_positions=3, project_open_risk=True)).check(i, eng.state)
    assert not d.allowed and d.reasons == ["daily_loss_limit_abs"]   # 40 open + 15 new > 50


def test_exits_never_consult_the_heat_cap_or_the_projection(tmp_path):
    eng, pb, _ = _engine(tmp_path)
    eng.gate.limits = RiskLimits(heat=ON, project_open_risk=True, daily_loss_limit_abs=1.0)
    agent = TrendSpecialist()
    prop = Proposal(proposal_id="icm-demo-1-x", account_id="icm-demo", agent_id=agent.agent_id, side=1, lots=0.1,
                    entry=2_400.2, stop=2_390.0, target=2_430.0, p=0.6, ev_r=0.1, spread_points=20.0, top_features=[])
    eng._execute(prop, agent, 0.1, 2_390.0, 2_430.0, requested=2_400.2, atr_usd=10.0)
    (pid,) = eng.open
    _open(pb, -1, None, "manual")                              # unknown open risk: every entry would be refused
    eng._refresh_account(_tick(WED))
    assert eng.state.open_risk is None
    calls: list[Any] = []
    real = eng.gate.check

    def spy(*a: Any, **k: Any) -> Any:
        calls.append(a)
        return real(*a, **k)

    eng.gate.check = spy                                       # type: ignore[method-assign]
    later = _tick(WED + pd.Timedelta(minutes=1), bid=2_389.0)  # the long's stop is hit
    pb.on_tick(later)
    eng._refresh_account(later)
    eng._manage_open(pd.DataFrame())
    assert pid not in eng.open and pid not in {p.position_id for p in pb.positions()} and calls == []
