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


def test_the_daily_loss_stop_skips_the_rest_of_the_day_at_two_percent():
    t = _trades(10, -1.0, days=1)                     # ten losing trades the same day at 1 % of equity each
    res = RS.simulate(t, RS.SIZING.model_copy(update={"base_risk": 0.01}))
    # after two the day is down 1.99 %; a third full stop would reach 2.97 %, past the limit: never taken
    assert res.n_trades == 2 and res.n_skipped_daily == 8


def test_the_drawdown_halt_stops_all_trading_at_fifteen_percent():
    t = _trades(60, -1.0)                              # one loss a day
    res = RS.simulate(t, RS.SIZING.model_copy(update={"base_risk": 0.01}))
    assert res.halted and 0.15 <= res.max_drawdown < 0.17 and res.n_trades < 60


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
