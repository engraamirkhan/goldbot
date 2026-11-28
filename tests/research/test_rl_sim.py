"""The family-D simulator (goldbot/research/rl_sim.py, ADR 0106 3 "RL (D) does sizing and execution only", "trained in
a simulator with realistic costs"): costs (spread + commission, ub95 slippage, stop overshoot, swaps) in R, and the
sizing scaffold size-v1, a fraction of equity scaled by volatility and capped at 1 % risk per trade, with the
real-money limits (2 % daily stop, 15 % drawdown halt) as plain code."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from goldbot.research import decision_cost as DC
from goldbot.research import rl_sim as RS


def test_the_w2_costs_are_the_cost_of_record_and_slippage_is_charged_on_every_exit():
    c = RS.SimCosts.w2()
    assert c.rt_bp == DC.RT_BP and c.overshoot_bp == DC.OVERSHOOT_BP
    k = np.array([0.01, 0.02])
    np.testing.assert_allclose(c.exit_cost_r(k, np.array([False, True])),
                               k * (c.rt_bp + c.slippage_ub95_bp + np.array([0.0, c.overshoot_bp])))
    s = c.model_copy(update={"slippage_ub95_bp": 1.0})
    assert (s.exit_cost_r(k, np.array([False, False])) > c.exit_cost_r(k, np.array([False, False]))).all()


def test_a_per_row_round_trip_replaces_the_simulator_constant_and_keeps_slippage_and_overshoot():
    c = RS.SimCosts.w2().model_copy(update={"slippage_ub95_bp": 0.5})
    k, stop = np.array([0.01, 0.02]), np.array([False, True])
    rt = np.array([20.0, 30.0])
    np.testing.assert_allclose(c.exit_cost_r(k, stop, rt_bp=rt), k * (rt + 0.5 + np.array([0.0, c.overshoot_bp])))


def test_a_swap_is_charged_per_night_and_never_credited():
    c = RS.SimCosts.w2()
    k = np.array([0.01, 0.01])
    long_, short = c.swap_r(k, np.array([1, -1]), np.array([2.0, 2.0]))
    assert long_ > 0 and short >= 0                   # the research spec: longs pay, shorts are not credited
    assert c.swap_r(k, np.array([1]), np.array([0.0]))[0] == 0


def test_the_size_is_scaled_by_volatility_and_never_above_one_percent_of_equity():
    sp = RS.SIZING
    assert sp.max_risk == 0.01
    calm, wild = RS.risk_fraction(np.array([0.5, 4.0]), sp)
    assert calm == sp.max_risk and wild == pytest.approx(sp.base_risk / 4.0)
    assert (RS.risk_fraction(np.geomspace(1e-4, 1e4, 50), sp) <= sp.max_risk).all()


def test_the_volatility_ratio_reads_only_past_atr():
    atr = pd.Series(np.r_[np.ones(50), np.full(10, 3.0)])
    r = RS.vol_ratio(atr.to_numpy(), lookback=20)
    r2 = RS.vol_ratio(np.r_[atr.to_numpy()[:55], np.full(5, 100.0)], lookback=20)
    np.testing.assert_array_equal(r[:55], r2[:55])
    assert r[49] == pytest.approx(1.0) and r[52] > 1


def _trades(n: int, net: float, days: int | None = None) -> pd.DataFrame:
    d = pd.date_range("2014-01-06", periods=days or n, freq="D", tz="UTC")
    day = np.resize(d.to_numpy(), n)
    return pd.DataFrame({"day": np.sort(day), "gross_r": np.full(n, net + 0.05), "cost_r": 0.05,
                         "vol_ratio": 1.0})


def test_the_daily_loss_stop_reserves_a_gapped_stop_plus_costs_before_a_trade():
    t = _trades(10, -1.0, days=1)                     # ten losing trades the same day at 1 % of equity each
    sp = RS.SIZING.model_copy(update={"base_risk": 0.01})
    res = RS.simulate(t, sp)
    # after one the day is down 1 %; the next reserves (1 + cost 0.05 + gap 0.25) x 1 % = 1.3 %: 2.3 % > 2 %, skipped
    assert res.n_trades == 1 and res.n_skipped_daily == 9 and res.n_daily_breaches == 0


def test_a_stop_gapping_past_the_reserve_is_counted_as_a_daily_breach():
    t = _trades(3, -1.0, days=1).assign(gross_r=[-0.95, -0.95, -3.0])
    res = RS.simulate(t, RS.SIZING.model_copy(update={"base_risk": 0.005}))
    assert res.n_trades == 3 and res.n_daily_breaches == 1      # 0.5 + 0.5 + 1.525 % = 2.5 % > 2 %


def test_the_weekly_cap_skips_the_rest_of_the_week_at_five_percent():
    t = _trades(5, -1.0, days=5)                       # Mon-Fri, one 1 % loss a day
    res = RS.simulate(t, RS.SIZING.model_copy(update={"base_risk": 0.01}))
    assert res.n_trades == 4 and res.n_skipped_weekly == 1     # 3 % + 1.3 % reserve fits, 4 % + 1.3 % does not


def test_under_live_limits_the_sim_sizes_down_at_eight_percent_and_halts_at_twelve():
    t = _trades(120, -1.0)                             # one loss a day
    res = RS.simulate(t, RS.SIZING.model_copy(update={"base_risk": 0.01}))
    assert res.halted and 0.12 <= res.max_drawdown < 0.13 and res.n_trades < 120
    assert res.size_down_trades > 0


def test_no_sizing_spec_can_be_looser_than_the_live_limits():
    from goldbot.config import load_settings
    from goldbot.risk.gate import RiskLimits
    live, r = RiskLimits(), load_settings().risk
    assert RS.MAX_RISK == live.max_risk_per_trade and RS.DRAWDOWN_HALT == r.drawdown_stage2 == live.dd_stage2
    assert RS.DRAWDOWN_STAGE1 == r.drawdown_stage1 and RS.DAILY_LOSS_STOP == r.daily_cap
    assert RS.WEEKLY_CAP == r.weekly_cap and RS.STAGE1_CLEAR == r.drawdown_stage1_clear
    for field, value in (("max_risk", 0.05), ("base_risk", 0.02), ("daily_loss_stop", 0.03),
                         ("weekly_cap", 0.06), ("drawdown_halt", 0.15), ("drawdown_stage1", 0.1)):
        with pytest.raises(ValueError):
            RS.SizingSpec.model_validate({**RS.SIZING.model_dump(), field: value})
    loose = RS.SIZING.model_copy(update={"max_risk": 0.05})     # model_copy skips validation: simulate re-checks
    with pytest.raises(ValueError, match="max_risk"):
        RS.simulate(_trades(5, 0.1), loose)
    with pytest.raises(ValueError, match="max_risk"):
        RS.risk_fraction(np.array([1.0]), loose)


@pytest.mark.parametrize("col,bad", [("vol_ratio", 0.0), ("vol_ratio", np.nan), ("gross_r", np.inf),
                                     ("cost_r", -0.01), ("cost_r", np.nan)])
def test_the_simulation_refuses_non_finite_or_impossible_inputs(col: str, bad: float):
    t = _trades(5, 0.1)
    t.loc[2, col] = bad
    with pytest.raises(ValueError, match=col):
        RS.simulate(t)


def test_the_simulation_reports_cost_share_of_gross_and_compares_with_fixed_fraction():
    rng = np.random.default_rng(1)
    t = _trades(300, 0.0).assign(gross_r=rng.normal(0.1, 1.0, 300), vol_ratio=rng.uniform(0.5, 2.0, 300))
    rep = RS.compare(t)
    assert rep.policy.cost_share is not None and rep.fixed.n_trades > 0
    assert rep.policy.spec_id == RS.SIZING.spec_id and rep.fixed.max_risk_used <= RS.SIZING.max_risk
    assert rep.policy.max_risk_used <= 0.01


def test_the_simulator_imports_nothing_on_the_order_path():
    import ast
    from pathlib import Path
    src = Path(RS.__file__).read_text()
    mods = {getattr(n, "module", None) or "" for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ImportFrom)}
    assert not any(m.startswith(("goldbot.engine", "goldbot.execution", "goldbot.risk", "goldbot.ops")) for m in mods)
