"""goldbot/risk/sizing.py must equal RiskGate's own sizing and stage machine (gate.py `check`, `update_stage`): the
drawdown Monte Carlo and the min-lot report rely on it, so any drift between the two fails here."""
from __future__ import annotations

import itertools

import numpy as np
import pytest

from goldbot.research.ruin import next_stage_vec
from goldbot.risk.gate import AccountState, Intent, RiskGate, RiskLimits, Stage
from goldbot.risk.sizing import effective_risk, next_stage, size_lots, stop_distance


def _state(equity: float, stage: Stage, combined: bool) -> AccountState:
    # check() runs update_stage first: a size-down account keeps its stage only between the 5% clear and 8%
    hwm = equity / 0.94 if stage == Stage.SIZE_DOWN else equity
    return AccountState(equity=equity, balance_closed_hwm=hwm, day_start_equity=equity, week_start_equity=equity,
                        open_positions=0, margin_used=0.0, last_tick_age_s=1.0, spread_points=25.0, stage=stage,
                        combined_size_down=combined)


def _intent(atr: float, mult: float, stops_level: float = 0.0) -> Intent:
    # price 100 keeps margin and the combined-notional cap out of the way: only the sizing decides
    return Intent(agent_id="x", side=1, p=0.7, target_atr=3.0, stop_atr=1.0, atr_usd=atr, cost_atr=0.01,
                  multiplier=mult, price=100.0, stops_level_points=stops_level)


CASES = list(itertools.product(
    [(Stage.NORMAL, False), (Stage.SIZE_DOWN, False), (Stage.NORMAL, True)],
    [0.1, 0.25, 0.7, 1.0, 1.5, 3.0],
    [0.001, 0.005, 0.008],
    [5_000.0, 9_166.0, 9_167.0, 20_000.0, 100_000.0],
    [0.5, 5.0, 55.0],
))


@pytest.mark.parametrize("stage_combined,mult,risk,equity,atr", CASES)
def test_sizing_module_matches_the_gate(stage_combined: tuple[Stage, bool], mult: float, risk: float, equity: float,
                                        atr: float):
    stage, combined = stage_combined
    limits = RiskLimits(risk_per_trade=risk)
    st = _state(equity, stage, combined)
    d = RiskGate(limits).check(_intent(atr, mult), st)
    assert st.stage == stage                       # the case tests the stage it names
    eff = effective_risk(limits, stage == Stage.SIZE_DOWN or combined, mult)
    stop = stop_distance(1.0, atr, spread_points=25.0, point=limits.point)
    sz = size_lots(equity=equity, risk=eff, stop_distance=stop, contract_oz=100.0, volume_min=0.01, volume_step=0.01,
                   volume_max=2.0)
    assert d.stop_distance == pytest.approx(stop)
    if sz.refused:
        assert not d.allowed and d.reasons == ["min_lot_exceeds_risk"]
    else:
        assert d.allowed, d.reasons
        assert d.lots == round(sz.lots, 2)
        assert d.risk_fraction == sz.realised_risk


def test_size_down_is_a_quarter_of_the_normal_risk_at_multiplier_one():
    eff = effective_risk(RiskLimits(), True, 1.0)
    assert eff.target == pytest.approx(0.25 * RiskLimits().risk_per_trade)


def test_stop_floor_matches_the_gate():
    limits = RiskLimits()
    d = RiskGate(limits).check(_intent(0.1, 1.0, stops_level=50.0), _state(100_000.0, Stage.NORMAL, False))
    assert d.stop_distance == pytest.approx(stop_distance(1.0, 0.1, stops_level_points=50.0, spread_points=25.0))
    assert d.stop_distance == pytest.approx(0.75)


@pytest.mark.parametrize("start", [Stage.NORMAL, Stage.SIZE_DOWN, Stage.HALTED])
@pytest.mark.parametrize("dd", [0.0, 0.03, 0.049, 0.05, 0.07, 0.08, 0.1, 0.12, 0.2])
def test_stage_machine_matches_update_stage(start: Stage, dd: float):
    limits = RiskLimits()
    st = _state(100.0, start, False)
    st.balance_closed_hwm = 100.0
    st.equity = 100.0 * (1 - dd)
    want = RiskGate(limits).update_stage(st)
    got = next_stage(start.value, 1 - st.equity / st.balance_closed_hwm, limits.dd_stage1, limits.dd_stage2,
                     limits.dd_stage1_clear)
    assert got == want.value
    size_down, halted = next_stage_vec(np.array([start == Stage.SIZE_DOWN]), np.array([start == Stage.HALTED]),
                                       np.array([1 - st.equity / st.balance_closed_hwm]), limits.dd_stage1,
                                       limits.dd_stage2, limits.dd_stage1_clear)
    vec = "halted" if halted[0] else "size_down" if size_down[0] else "normal"
    assert vec == want.value
