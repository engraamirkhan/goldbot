"""H-27 lot-fit overlay (playbook small-account-playbook-2026-10-18, section 3): research claims no money from trades
the account cannot take. Each trade is sized by the gate's own function (`risk.sizing.size_lots`); a trade the gate
would refuse `min_lot_exceeds_risk` is `unfit` and excluded from P&L; fitting trades are scaled by the actual lot."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.research.lot_fit import LotFitSpec, lot_fit_overlay
from goldbot.risk import sizing


def _spec(**kw) -> LotFitSpec:
    base = dict(equity=1000.0, acct_per_usd=1.0, risk_per_trade=0.005)
    return LotFitSpec(**{**base, **kw})


def _trades(stops_usd: list[float], rets_usd: list[float], entry: float = 4000.0) -> pd.DataFrame:
    """Rows as the pipeline's labels: `ret` and `risk` are fractions of the entry price."""
    return pd.DataFrame({"entry": entry, "ret": np.array(rets_usd) / entry, "risk": np.array(stops_usd) / entry})


def test_trade_whose_minimum_lot_risks_more_than_the_gate_allows_is_unfit_and_earns_nothing():
    # 0.5% of 1000 = 5 USD; a 20 USD stop needs 0.0025 lot: below the 0.01 minimum, 0.01 lot risks 20 = 4x: refused
    out = lot_fit_overlay(_trades([20.0], [40.0]), _spec())
    assert out["n"] == 1 and out["n_unfit"] == 1 and out["n_fit"] == 0
    assert out["fit_fraction"] == 0.0 and out["pnl_gbp"] == 0.0


def test_fitting_trade_is_scaled_by_the_lot_the_gate_would_size():
    # a 2.5 USD stop: 5 / (2.5 x 100) = 0.02 lot; a 5 USD per oz win is 5 x 100 x 0.02 = 10 USD
    out = lot_fit_overlay(_trades([2.5], [5.0]), _spec())
    assert out["n_fit"] == 1 and out["mean_lots"] == pytest.approx(0.02)
    assert out["pnl_gbp"] == pytest.approx(10.0)
    assert out["r_at_lots"] == pytest.approx(10.0 / 5.0)          # in units of the intended 5 USD risk
    assert out["net_r_fit"] == pytest.approx(2.0)                 # ret / risk, unchanged by the lot


def test_lot_matches_the_gates_own_sizing_function_including_step_rounding_and_the_tolerance():
    spec = _spec()
    stops = [1.9, 3.3, 4.2, 4.5]            # 0.0263 -> 0.02; 0.0151 -> 0.01; 0.0119 -> 0.01; 0.0111 -> 0.01
    out = lot_fit_overlay(_trades(stops, [1.0] * 4), spec)
    eff = sizing.effective_risk(spec.limits(), False, 1.0)
    want = [sizing.size_lots(equity=1000.0, risk=eff, stop_distance=s, contract_oz=100.0, volume_min=0.01,
                             volume_step=0.01, volume_max=50.0, acct_per_usd=1.0) for s in stops]
    assert out["n_fit"] == sum(not w.refused for w in want) == 4
    assert out["mean_lots"] == pytest.approx(np.mean([w.lots for w in want]))


def test_gbp_account_converts_the_usd_stop_and_the_usd_pnl():
    # at 0.75 GBP per USD one lot loses 2.5 x 100 x 0.75 = 187.5 GBP at the stop: 5 / 187.5 = 0.0267 -> 0.02 lot
    out = lot_fit_overlay(_trades([2.5], [5.0]), _spec(acct_per_usd=0.75))
    assert out["mean_lots"] == pytest.approx(0.02)
    assert out["pnl_gbp"] == pytest.approx(5.0 * 100 * 0.02 * 0.75)


def test_report_gives_fit_fraction_and_unfit_count_over_a_mixed_set():
    out = lot_fit_overlay(_trades([2.5, 20.0, 2.5, 30.0], [5.0, 40.0, -2.5, 60.0]), _spec())
    assert (out["n"], out["n_fit"], out["n_unfit"]) == (4, 2, 2)
    assert out["fit_fraction"] == pytest.approx(0.5)
    assert out["pnl_gbp"] == pytest.approx(10.0 - 5.0)


def test_empty_trades_report_zero_without_dividing():
    out = lot_fit_overlay(_trades([], []), _spec())
    assert out["n"] == 0 and out["fit_fraction"] is None and out["pnl_gbp"] == 0.0


def test_spec_defaults_come_from_settings_and_the_gate_limits_read_the_risk_section():
    s = load_settings()
    spec = LotFitSpec.from_settings(s)
    assert spec.equity == s.research.lot_fit_equity == 1000.0
    assert spec.risk_per_trade == s.risk.risk_per_trade
    assert spec.limits().max_risk_per_trade == pytest.approx(0.01)


def test_default_walk_forward_output_is_unchanged_and_the_overlay_only_adds_a_metrics_key():
    from goldbot.data.resample import resample_bars, ticks_to_1m
    from goldbot.data.synthetic import synthetic_ticks
    from goldbot.research.pipeline import run_specialist
    from goldbot.specialists import SPECIALISTS

    b1 = ticks_to_1m(synthetic_ticks("2023-01-01", "2023-09-01", ticks_per_minute=1, seed=5))
    dec = resample_bars(b1, "15m")
    spec = SPECIALISTS["session_open"](asia_range_max_atr_d=1.2)
    plain = run_specialist(spec, dec)
    with_fit = run_specialist(spec, dec, lot_fit=_spec(equity=100_000.0))
    assert "lot_fit" not in plain.metrics
    pd.testing.assert_frame_equal(plain.oof, with_fit.oof)
    rest = {k: v for k, v in with_fit.metrics.items() if k != "lot_fit"}
    assert repr(rest) == repr(plain.metrics)
    if "model_filtered" in plain.metrics:
        lf = with_fit.metrics["lot_fit"]
        assert lf["model_filtered"]["n"] == plain.metrics["model_filtered"]["n"]
        assert lf["all_candidates"]["n"] == plain.metrics["all_candidates"]["n"]


# ------------------------------------------------------------------------ "unfit" is exactly what the live gate refuses
import itertools  # noqa: E402

from goldbot.risk.gate import AccountState, Intent, RiskGate, RiskLimits, Stage  # noqa: E402


def _gate_allows(*, stop: float, equity: float, fx: float, risk: float, cap: float | None, size_down: bool,
                 mult: float, spread_points: float, stops_level: float) -> bool:
    """RiskGate.check on an intent whose planned stop is `stop` USD/oz (atr 1, stop_atr = stop), sizing-only."""
    limits = RiskLimits(risk_per_trade=risk, min_lot_risk_cap=cap)
    hwm = equity / 0.94 if size_down else equity
    st = AccountState(equity=equity, balance_closed_hwm=hwm, day_start_equity=equity, week_start_equity=equity,
                      open_positions=0, margin_used=0.0, last_tick_age_s=1.0, spread_points=spread_points,
                      stage=Stage.SIZE_DOWN if size_down else Stage.NORMAL)
    it = Intent(agent_id="x", side=1, p=0.7, target_atr=3.0 * stop, stop_atr=stop, atr_usd=1.0, cost_atr=0.0,
                multiplier=mult, price=100.0, stops_level_points=stops_level,
                account_currency="USD" if fx == 1.0 else "GBP", acct_per_usd=None if fx == 1.0 else fx)
    d = RiskGate(limits).check(it, st)
    return "min_lot_exceeds_risk" not in d.reasons


GRID = list(itertools.product([0.3, 1.0, 2.5, 4.2, 6.0, 12.0, 30.0], [500.0, 1000.0, 5000.0], [1.0, 0.75],
                              [None, 0.01, 0.004], [False, True], [0.5, 1.0, 1.5], [(25.0, 0.0), (0.0, 800.0)]))


@pytest.mark.parametrize("stop,equity,fx,cap,size_down,mult,floor", GRID)
def test_unfit_is_exactly_what_the_gate_refuses_min_lot_exceeds_risk(stop, equity, fx, cap, size_down, mult, floor):
    spread, level = floor
    spec = _spec(equity=equity, acct_per_usd=fx, min_lot_risk_cap=cap, size_down=size_down, multiplier=mult,
                 spread_points=spread, stops_level_points=level)
    out = lot_fit_overlay(_trades([stop], [1.0], entry=100.0), spec)
    assert (out["n_fit"] == 1) == _gate_allows(stop=stop, equity=equity, fx=fx, risk=0.005, cap=cap,
                                               size_down=size_down, mult=mult, spread_points=spread,
                                               stops_level=level)


def test_pooled_walk_forward_takes_the_overlay_too_and_without_it_is_unchanged():
    from goldbot.data.resample import resample_bars, ticks_to_1m
    from goldbot.data.synthetic import synthetic_ticks
    from goldbot.research.pipeline import build_decision_frame, prepare, run_pool
    from goldbot.specialists import SPECIALISTS

    b1 = ticks_to_1m(synthetic_ticks("2023-01-01", "2023-09-01", ticks_per_minute=1, seed=5))
    dec = resample_bars(b1, "15m").reset_index(drop=True)
    frame = build_decision_frame(dec, {})
    specs = [SPECIALISTS["session_open"](asia_range_max_atr_d=1.2), SPECIALISTS["session_open"]()]
    preps = [prepare(s, dec, {}, frame=frame) for s in specs]
    plain = run_pool(preps, "15m", compare_local=False)
    with_fit = run_pool(preps, "15m", compare_local=False, lot_fit=_spec())
    assert "lot_fit" not in plain.metrics and "lot_fit" in with_fit.metrics
    assert repr({k: v for k, v in with_fit.metrics.items() if k != "lot_fit"}) == repr(plain.metrics)


def test_the_report_lines_show_fit_fraction_unfit_count_pounds_and_r_at_lots():
    from goldbot.research.lot_fit import lot_fit_lines
    out = lot_fit_overlay(_trades([2.5, 20.0, 2.5, 30.0], [5.0, 40.0, -2.5, 60.0]), _spec())
    text = "\n".join(lot_fit_lines({"model_filtered": out, "all_candidates": out}))
    assert "fit fraction 50.0%" in text and "2 unfit" in text and "£5.00" in text and "R at lots" in text
    assert lot_fit_lines(None) == []


def test_settings_spec_applies_the_stop_floor_and_the_report_states_it_and_the_multiplier_assumption():
    from goldbot.research.lot_fit import lot_fit_lines
    s = load_settings()
    spec = LotFitSpec.from_settings(s)
    assert spec.spread_points == s.research.lot_fit_spread_points > 0
    assert spec.stops_level_points == s.research.lot_fit_stops_level_points
    # a 0.05 USD label stop is floored at the 0.20 USD spread: 5 / (0.20 x 100) = 0.25 lot, not 1.0 lot
    out = lot_fit_overlay(_trades([0.05], [0.1]), spec.model_copy(update={"acct_per_usd": 1.0}))
    assert out["mean_lots"] == pytest.approx(0.25)
    text = "\n".join(lot_fit_lines({"model_filtered": out}))
    assert "stop floor: spread prior only (20 points)" in text
    assert "model multiplier fixed at 1.0 (live varies with p; fit fraction optimistic for low-p trades)" in text
    lvl = lot_fit_overlay(_trades([0.05], [0.1]), spec.model_copy(update={"stops_level_points": 50.0}))
    assert "stops level 50 + spread 20 points" in "\n".join(lot_fit_lines({"model_filtered": lvl}))


def test_the_minimum_lot_exception_makes_a_refused_trade_fit_at_exactly_the_minimum_lot():
    # 20 USD stop on 1000: min lot risks 2% > 1% cap: still unfit; 8 USD stop risks 0.8% <= 1% cap: fits at 0.01
    off = lot_fit_overlay(_trades([8.0], [16.0]), _spec())
    on = lot_fit_overlay(_trades([8.0, 20.0], [16.0, 40.0]), _spec(min_lot_risk_cap=0.01))
    assert off["n_fit"] == 0 and on["n_fit"] == 1 and on["mean_lots"] == pytest.approx(0.01)
    assert on["pnl_gbp"] == pytest.approx(16.0)
    assert on["n_min_lot_exception"] == 1


def test_tiny_live_never_takes_the_exception_and_uses_its_own_risk_rate():
    base = load_settings()
    s = base.model_copy(update={"risk": base.risk.model_copy(update={"min_lot_risk_cap": 0.01})})
    assert LotFitSpec.from_settings(s).min_lot_risk_cap == 0.01
    tiny = LotFitSpec.from_settings(s, tiny_live=True)
    assert tiny.min_lot_risk_cap is None and tiny.risk_per_trade == s.risk.risk_per_trade_tiny_live
