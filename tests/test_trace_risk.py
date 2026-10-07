"""Traceability (docs/TRACEABILITY.md): the design's risk numbers (Execution and risk: Sizing, Hard limits) in
settings.yaml and the code defaults, and the 1% per-trade clamp applied after the model multiplier. Behaviour of
each block and stage is covered in tests/test_cov_risk.py; this file pins the numbers to the design."""
from typing import Any

import pytest

from goldbot.config import load_settings
from goldbot.risk import AccountState, Intent, RiskGate, RiskLimits
from goldbot.risk.supervisor import SupervisorLimits


def _state(**kw: Any) -> AccountState:
    base: dict[str, Any] = dict(equity=10_000, balance_closed_hwm=10_000, day_start_equity=10_000, week_start_equity=10_000,
                                open_positions=0, margin_used=0.0, last_tick_age_s=1.0, spread_points=25.0)
    base.update(kw)
    return AccountState(**base)


def _intent(**kw: Any) -> Intent:
    base: dict[str, Any] = dict(agent_id="a", side=1, p=0.62, target_atr=1.5, stop_atr=1.0, atr_usd=4.0, cost_atr=0.1,
                                multiplier=1.0, price=2400.0)
    base.update(kw)
    return Intent(**base)


# ------------------------------------------------------------------------------------------- numbers vs design
def test_design_numbers_in_settings_and_code_defaults():
    r = load_settings().risk
    design = dict(risk_per_trade=0.005, risk_per_trade_tiny_live=0.001, multiplier_bounds=(0.25, 1.5), daily_cap=0.02,
                  weekly_cap=0.05, supervisor_daily_cap=0.015, supervisor_weekly_cap=0.04, drawdown_stage1=0.08,
                  drawdown_stage2=0.12, drawdown_stage1_clear=0.05, max_positions_per_account=2, max_spread_points=45,
                  stale_tick_seconds=20, min_target_over_cost=2.5, approval_window_seconds=90)
    for k, v in design.items():
        assert getattr(r, k) == pytest.approx(v) if not isinstance(v, tuple) else tuple(getattr(r, k)) == v, k
    assert (r.blackout.before_min, r.blackout.after_min) == (15, 30)
    assert set(r.blackout.events) == {"CPI", "NFP", "FOMC", "PCE"}
    L = RiskLimits()
    assert (L.risk_per_trade, L.max_risk_per_trade, L.daily_cap, L.weekly_cap) == (0.005, 0.01, 0.02, 0.05)
    assert (L.dd_stage1, L.dd_stage2, L.dd_stage1_clear, L.max_positions) == (0.08, 0.12, 0.05, 2)
    assert (L.max_spread_points, L.stale_tick_seconds, L.min_target_over_cost) == (45.0, 20, 2.5)
    assert (L.margin_level_floor, L.leverage_cap) == (3.0, 20.0)
    S = SupervisorLimits()
    assert (S.daily_cap, S.weekly_cap, S.dd_stage1, S.dd_stage2, S.heartbeat_max_age_s) == (0.015, 0.04, 0.08, 0.12, 60)


# ------------------------------------------------------------------------------------------- per-trade risk and sizing
def test_per_trade_risk_is_clamped_at_one_percent_after_the_multiplier():
    # design Hard limits: per-trade risk 1% max, clamped (not rejected) -> 0.8% x 1.5 = 1.2% must come out at <= 1%
    g = RiskGate(RiskLimits(risk_per_trade=0.008))
    d = g.check(_intent(multiplier=1.5, p=0.7), _state())
    assert d.allowed
    assert d.risk_fraction <= 0.01 + 1e-9
    assert d.lots == 0.25           # $100 / ($4 x 100 oz)
