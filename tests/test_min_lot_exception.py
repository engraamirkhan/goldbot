"""BACKLOG 25 / ADR 0004 decision 2: the minimum-lot exception inside RiskGate.

`risk.min_lot_risk_cap` (null = off, today's behaviour): when the sized lots fall below the minimum lot, the gate
places exactly the minimum lot only if its realised risk in the account currency is within the cap (half the cap in
the 8% size-down stage or the supervisor's combined size-down); every other check still runs after it."""
import itertools
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from goldbot.config import RiskSettings, load_settings
from goldbot.risk import AccountState, Intent, RiskGate, RiskLimits
from goldbot.risk.gate import Stage

GBP = 1 / 1.33
RISK = 0.005                          # the demo's normal risk per trade (tiny-live never takes the exception)
CAP = 0.01


def _limits(cap: float | None = CAP, risk: float = RISK) -> RiskLimits:
    return RiskLimits(risk_per_trade=risk, min_lot_risk_cap=cap)


def _state(equity: float, **kw: Any) -> AccountState:
    base: dict[str, Any] = dict(equity=equity, balance_closed_hwm=equity, day_start_equity=equity,
                                week_start_equity=equity, open_positions=0, margin_used=0.0, last_tick_age_s=1.0,
                                spread_points=25.0)
    base.update(kw)
    return AccountState(**base)


def _intent(stop: float = 10.90, *, gbp: bool = True, mult: float = 1.0, price: float = 4_150.0) -> Intent:
    cur: dict[str, Any] = {"account_currency": "GBP", "acct_per_usd": GBP} if gbp else {"account_currency": "USD"}
    return Intent(agent_id="x", side=1, p=0.7, target_atr=3.0, stop_atr=1.0, atr_usd=stop, cost_atr=0.01,
                  multiplier=mult, price=price, **cur)


# ---------------------------------------------------------------------------------------------- the ADR 0004 cases
def test_a_1000_pound_account_trades_one_minimum_lot_at_0_82_percent_under_a_1_percent_cap():
    d = RiskGate(_limits()).check(_intent(), _state(1_000.0))
    assert d.allowed and d.lots == 0.01
    assert d.reasons == ["min_lot_exception"]
    assert d.risk_fraction == pytest.approx(10.90 / 1.33 / 1_000.0)          # 0.82%


def test_without_the_cap_the_same_trade_is_refused_as_today():
    d = RiskGate(_limits(cap=None)).check(_intent(), _state(1_000.0))
    assert not d.allowed and d.reasons == ["min_lot_exceeds_risk"]


def test_700_pounds_is_refused_because_the_minimum_lot_would_risk_1_17_percent():
    d = RiskGate(_limits()).check(_intent(), _state(700.0))
    assert not d.allowed and d.reasons == ["min_lot_exceeds_risk"]


@pytest.mark.parametrize("combined", [False, True])
def test_the_size_down_stage_halves_the_cap_so_1000_pounds_is_refused(combined):
    st = _state(1_000.0, balance_closed_hwm=1_000.0 / 0.94 if not combined else 1_000.0,
                stage=Stage.SIZE_DOWN if not combined else Stage.NORMAL, combined_size_down=combined)
    d = RiskGate(_limits()).check(_intent(), st)                              # 0.82% > 0.5%
    assert not d.allowed and d.reasons == ["min_lot_exceeds_risk"]
    st2 = _state(2_000.0, balance_closed_hwm=2_000.0 / 0.94 if not combined else 2_000.0,
                 stage=Stage.SIZE_DOWN if not combined else Stage.NORMAL, combined_size_down=combined)
    d2 = RiskGate(_limits()).check(_intent(), st2)                            # 0.41% <= 0.5%
    assert d2.allowed and d2.lots == 0.01 and d2.reasons == ["min_lot_exception"]


def test_a_usd_account_of_1000_dollars_is_refused_at_1_09_percent():
    d = RiskGate(_limits()).check(_intent(gbp=False), _state(1_000.0))
    assert not d.allowed and d.reasons == ["min_lot_exceeds_risk"]


# ---------------------------------------------------------------------------------------------- boundaries
def test_the_cap_boundary_is_inclusive():
    # USD account, $10 stop: one minimum lot risks $10. At $1,000 that is exactly 1.00%: allowed; at $999.99 refused
    gate = RiskGate(_limits())
    at = gate.check(_intent(10.0, gbp=False), _state(1_000.0))
    assert at.allowed and at.lots == 0.01 and at.risk_fraction == pytest.approx(0.01)
    below = gate.check(_intent(10.0, gbp=False), _state(999.99))
    assert not below.allowed and below.reasons == ["min_lot_exceeds_risk"]


def test_the_cap_never_exceeds_the_hard_one_percent_per_trade_maximum():
    with pytest.raises(ValidationError):
        RiskSettings.model_validate({**_risk_section(), "min_lot_risk_cap": 0.011})
    with pytest.raises(ValidationError):
        RiskSettings.model_validate({**_risk_section(), "min_lot_risk_cap": 0.0})
    # a RiskLimits built by hand with a larger cap is still clamped to max_risk_per_trade (1%)
    d = RiskGate(RiskLimits(risk_per_trade=RISK, min_lot_risk_cap=0.05)).check(_intent(10.0, gbp=False), _state(999.99))
    assert not d.allowed


def test_the_exception_places_exactly_the_minimum_lot_never_more():
    for equity in (820.0, 900.0, 1_000.0, 1_200.0):
        d = RiskGate(_limits()).check(_intent(), _state(equity))
        assert d.allowed and d.lots == 0.01
    # a broker minimum of 0.1 lot: the exception places 0.1 when 0.1 fits the cap, never a step above it
    big = Intent(**{**_intent().model_dump(), "volume_min": 0.1, "volume_step": 0.1})
    d = RiskGate(_limits()).check(big, _state(9_000.0))                        # 0.1 lot = £81.95 = 0.91%
    assert d.allowed and d.lots == 0.1 and d.reasons == ["min_lot_exception"]


@pytest.mark.parametrize("equity,stop,mult", list(itertools.product(
    [20_000.0, 50_000.0, 100_000.0], [0.5, 5.0, 10.9], [0.25, 1.0, 1.5])))
def test_sizes_at_or_above_the_minimum_lot_are_unchanged_by_the_cap(equity, stop, mult):
    on = RiskGate(_limits(risk=0.005)).check(_intent(stop, mult=mult, price=100.0), _state(equity))
    off = RiskGate(_limits(cap=None, risk=0.005)).check(_intent(stop, mult=mult, price=100.0), _state(equity))
    assert on.allowed == off.allowed and on.lots == off.lots and on.risk_fraction == off.risk_fraction
    if off.allowed:
        assert on.reasons == []


# ---------------------------------------------------------------------------------------------- other checks still run
def test_the_margin_floor_still_applies_after_the_exception():
    # $600 USD account, $6 stop: 0.01 lot risks 1.00%, inside the cap; 1:20 margin on $4,150 = $207.50 -> 289% < 300%
    d = RiskGate(_limits()).check(_intent(6.0, gbp=False), _state(600.0))
    assert not d.allowed and d.reasons == ["margin_level_floor"]


def test_the_combined_exposure_cap_still_applies_after_the_exception():
    st = _state(1_000.0, open_lots=0.01, open_notional=4_150.0, open_positions=1)
    d = RiskGate(_limits()).check(_intent(), st)
    assert not d.allowed and d.reasons == ["combined_exposure_cap"]


def test_halts_and_caps_win_over_the_exception():
    for kw in ({"supervisor_halt": True}, {"owner_halt": True}, {"in_blackout": True}, {"spread_points": 60.0},
               {"day_start_equity": 1_030.0}):
        d = RiskGate(_limits()).check(_intent(), _state(1_000.0, **kw))
        assert not d.allowed and "min_lot_exception" not in d.reasons


def test_a_missing_conversion_still_refuses_before_the_exception():
    i = Intent(**{**_intent().model_dump(), "acct_per_usd": None})
    assert RiskGate(_limits()).check(i, _state(1_000.0)).reasons == ["fx_conversion_unavailable"]


# ---------------------------------------------------------------------------------------------- settings
def _risk_section() -> dict[str, Any]:
    raw = yaml.safe_load(Path("config/settings.yaml").read_text())["risk"]
    return dict(raw)


def test_the_setting_is_off_by_default_and_reaches_the_gate():
    assert load_settings().risk.min_lot_risk_cap is None
    assert RiskLimits().min_lot_risk_cap is None
    risk = RiskSettings.model_validate({**_risk_section(), "min_lot_risk_cap": 0.01})
    assert RiskLimits.from_settings(risk, tiny_live=False).min_lot_risk_cap == 0.01
    assert RiskLimits.from_settings(risk, tiny_live=True).min_lot_risk_cap is None          # review MEDIUM-2
    assert RiskLimits.from_settings(load_settings().risk, tiny_live=False).min_lot_risk_cap is None


# ---------------------------------------------------------------------------------------------- the engine journal
@pytest.mark.integration
@pytest.mark.parametrize("cap", [None, 0.01])
def test_an_exception_entry_reaches_the_decisions_journal_with_its_realised_risk(tmp_path, cap):
    import pandas as pd

    from goldbot.data.synthetic import synthetic_ticks
    from goldbot.engine import ConstantModel, Engine, EngineConfig
    from goldbot.execution.broker import Tick
    from goldbot.execution.paper import PaperBroker
    from goldbot.specialists import SPECIALISTS

    ticks = synthetic_ticks("2025-03-03", "2025-03-06", ticks_per_minute=1, seed=5)
    spec = SPECIALISTS["session_open"](min_body_pct=0.0, asia_range_max_atr_d=99.0)
    # a risk per trade so small that every trade sizes below the minimum lot
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", approval_mode="auto", state_dir=str(tmp_path),
                              owner_user_id=111), PaperBroker(equity=10_000), [spec],
                 {"session_open": ConstantModel(p=0.65)}, limits=RiskLimits(risk_per_trade=0.0001, min_lot_risk_cap=cap))
    decisions: list[dict] = []
    for ts, bid, ask in zip(ticks["ts_utc"], ticks["bid"].to_numpy(float), ticks["ask"].to_numpy(float)):
        decisions += eng.on_tick(Tick(ts_utc=pd.Timestamp(ts), bid=float(bid), ask=float(ask)))
    executed = [d for d in decisions if d["action"] == "executed:auto"]
    if cap is None:
        assert not executed and any("min_lot_exceeds_risk" in str(d["action"]) for d in decisions)
        return
    assert executed
    assert all(d["sizing"] == ["min_lot_exception"] and 0 < d["risk_fraction"] <= 0.01 and d["risk_per_trade"] == 0.0001
               for d in executed)
    orders = [d for d in eng.decisions if d.get("action") == "order"]
    assert orders and all(o["lots"] == 0.01 for o in orders)
