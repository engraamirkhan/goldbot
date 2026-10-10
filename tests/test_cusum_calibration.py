"""M25: the CUSUM decision interval is tuned so in-control residuals alarm within one quarter's expected trades with
probability 5% (design: "tuned to a 5% quarterly false-alarm rate"), as a function of the backtest's trade rate."""
import numpy as np
import pytest

from goldbot.config import load_settings
from goldbot.research import cusum
from goldbot.research.drift import residual_cusum
from goldbot.research.promotion import cusum_alarm


def _n(trades_per_week: float) -> int:
    n = cusum.trades_per_quarter(trades_per_week)
    assert n is not None
    return n


@pytest.mark.parametrize("trades_per_week", [1.0, 4.0, 15.0])
def test_simulated_quarterly_false_alarm_rate_is_five_percent_at_several_trade_rates(trades_per_week):
    h = cusum.calibrated_h(trades_per_week, k=0.5)
    n = _n(trades_per_week)
    # fresh paths (a different seed than the calibration's); 100,000 paths each give a standard error of ~0.07 pp
    assert cusum.alarm_rate(h, 0.5, n) == pytest.approx(0.05, abs=0.004)


def test_h_grows_with_the_trade_rate_so_busier_agents_do_not_alarm_more_often():
    hs = [cusum.calibrated_h(tpw) for tpw in (1.0, 4.0, 15.0)]
    assert hs[0] < hs[1] < hs[2]


def test_a_one_sd_drop_is_still_caught_quickly():
    for tpw in (2.0, 5.0, 15.0):
        h, n = cusum.calibrated_h(tpw), _n(tpw)
        assert cusum.alarm_rate(h, 0.5, n, shift=-1.0) > 0.85            # caught within the quarter
        assert np.median(cusum.run_lengths(h, 0.5, n, shift=-1.0)) <= 12  # typically within about a dozen trades


def test_k_stays_configurable_and_changes_h():
    assert cusum.calibrated_h(5.0, k=0.25) > cusum.calibrated_h(5.0, k=0.5) > cusum.calibrated_h(5.0, k=1.0)
    h = cusum.calibrated_h(5.0, k=1.0)
    assert cusum.alarm_rate(h, 1.0, _n(5.0)) == pytest.approx(0.05, abs=0.004)


def test_calibration_is_cached_and_falls_back_without_a_trade_rate():
    cusum._h.cache_clear()
    cusum.calibrated_h(3.0)
    cusum.calibrated_h(3.0)
    assert cusum._h.cache_info().hits == 1
    assert cusum.calibrated_h(None) == cusum.calibrated_h(0.0) == cusum.FALLBACK_H
    assert cusum.calibrated_h(float("nan")) == cusum.FALLBACK_H


def test_residual_cusum_and_cusum_alarm_use_the_calibrated_h():
    # one trade with S = 3.7: above h at 1 trade a week (h ~3.46, 13 trades a quarter), below the old fixed 4.0
    assert cusum.calibrated_h(1.0) < 3.7 < cusum.FALLBACK_H
    assert residual_cusum([-4.2], trades_per_week=1.0)[0] and not residual_cusum([-4.2])[0]
    assert cusum_alarm([-0.042], 0.0, 0.01, trades_per_week=1.0) and not cusum_alarm([-0.042], 0.0, 0.01)
    # S = 4.5: above the old 4.0, but at 5 trades a week (h ~5.2) that is within a quarter's luck
    assert cusum.FALLBACK_H < 4.5 < cusum.calibrated_h(5.0)
    assert not residual_cusum([-5.0], trades_per_week=5.0)[0] and residual_cusum([-5.0])[0]
    assert not cusum_alarm([-0.05], 0.0, 0.01, trades_per_week=5.0) and cusum_alarm([-0.05], 0.0, 0.01)
    # an explicit h still wins
    assert not residual_cusum([-5.0], h=10.0, trades_per_week=1.0)[0]


def test_settings_carry_k_and_the_design_false_alarm_rate():
    d = load_settings().drift
    assert d.cusum_k == 0.5 and d.cusum_false_alarm == 0.05
