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
