"""M25: the CUSUM decision interval is tuned so in-control residuals alarm within one quarter's expected trades with
probability at most 5% (design: "tuned to a 5% quarterly false-alarm rate"), as a function of the backtest's trade
rate. In control a trade's standardised residual is two-point (win +sqrt((1-p)/p), loss -sqrt(p/(1-p))), so h is
simulated on those values at the mean taken p, and the largest attainable rate <= 5% is chosen."""
import math

import numpy as np
import pytest

from goldbot.config import load_settings
from goldbot.research import cusum
from goldbot.research.drift import residual_cusum
from goldbot.research.promotion import cusum_alarm


def _n(trades_per_week: float, weeks: float = cusum.QUARTER_WEEKS) -> int:
    n = cusum.trades_per_window(trades_per_week, weeks)
    assert n is not None
    return n


@pytest.mark.parametrize("p", [0.4, 0.5, 0.6])
@pytest.mark.parametrize("trades_per_week", [2.0, 5.0, 15.0])
def test_realised_quarterly_false_alarm_rate_on_trade_residuals_is_between_three_and_five_percent(p, trades_per_week):
    h = cusum.calibrated_h(trades_per_week, k=0.5, p=p)
    # fresh paths (a different seed than the calibration's) of two-point residuals at p
    fa = cusum.alarm_rate(h, 0.5, _n(trades_per_week), p=p)
    assert 0.03 <= fa <= 0.05


def test_normal_calibration_was_far_too_conservative_for_trade_residuals_at_p_0_4():
    # the review's finding, kept as a regression check: the normal-based h alarms 0.2-0.7% of quarters at p 0.4
    for tpw in (2.0, 5.0, 15.0):
        assert cusum.alarm_rate(cusum.calibrated_h(tpw), 0.5, _n(tpw), p=0.4) < 0.01


def test_a_drop_from_p_0_4_to_0_3_is_caught_within_a_quarter_far_more_often_than_with_the_normal_h():
    for tpw in (2.0, 5.0, 15.0):
        n = _n(tpw)
        bern = cusum.alarm_rate(cusum.calibrated_h(tpw, p=0.4), 0.5, n, p=0.4, p_true=0.3)
        normal = cusum.alarm_rate(cusum.calibrated_h(tpw), 0.5, n, p=0.4, p_true=0.3)
        assert bern > 2.5 * normal and bern - normal > 0.1


def test_h_is_the_most_sensitive_attainable_value_at_or_under_the_rate():
    peaks = np.array([0.0] * 90 + [1.0] * 4 + [2.0] * 3 + [3.0] * 3)      # P(>0)=10%, P(>1)=6%, P(>2)=3%, P(>3)=0
    assert cusum.smallest_h_at_rate(peaks, 0.05, z=0.0) == 2.0           # not 1.0 (6% > 5%), not 3.0 (needlessly deaf)
    assert cusum.smallest_h_at_rate(peaks, 0.06, z=0.0) == 1.0


def test_trade_residuals_are_two_point_and_standardised():
    for p in (0.3, 0.45, 0.6):
        win, loss = cusum.residual_values(p)
        assert p * win + (1 - p) * loss == pytest.approx(0.0)
        assert p * win ** 2 + (1 - p) * loss ** 2 == pytest.approx(1.0)
        assert win == pytest.approx(math.sqrt((1 - p) / p)) and loss == pytest.approx(-math.sqrt(p / (1 - p)))


def test_h_grows_with_the_trade_rate_so_busier_agents_do_not_alarm_more_often():
    for p in (None, 0.4, 0.6):
        hs = [cusum.calibrated_h(tpw, p=p) for tpw in (1.0, 4.0, 15.0)]
        assert hs[0] < hs[1] < hs[2]


def test_normal_values_still_calibrate_when_p_is_unknown():
    for tpw in (1.0, 4.0, 15.0):
        fa = cusum.alarm_rate(cusum.calibrated_h(tpw), 0.5, _n(tpw))
        assert 0.045 <= fa <= 0.05
    h, n = cusum.calibrated_h(5.0), _n(5.0)
    assert cusum.alarm_rate(h, 0.5, n, shift=-1.0) > 0.85                 # a 1-sd drop is still caught in the quarter
    assert np.median(cusum.run_lengths(h, 0.5, n, shift=-1.0)) <= 12


def test_k_stays_configurable_and_changes_h():
    assert cusum.calibrated_h(5.0, k=0.25, p=0.5) > cusum.calibrated_h(5.0, k=0.5, p=0.5) > cusum.calibrated_h(5.0, k=1.0, p=0.5)
    assert cusum.alarm_rate(cusum.calibrated_h(5.0, k=1.0, p=0.5), 1.0, _n(5.0), p=0.5) <= 0.05


def test_calibration_is_cached_per_p_bucket_and_falls_back_without_a_trade_rate():
    cusum._h.cache_clear()
    cusum.calibrated_h(3.0, p=0.401)
    cusum.calibrated_h(3.0, p=0.399)                                       # same 0.01 bucket: a cache hit
    assert cusum._h.cache_info().hits == 1
    cusum.calibrated_h(3.0, p=0.45)                                        # another bucket: a new calibration
    cusum.calibrated_h(3.0)                                                # normal: another key
    assert cusum._h.cache_info().misses == 3
    assert cusum.p_bucket(0.401) == cusum.p_bucket(0.399) == 0.4 and cusum.p_bucket(None) is None
    assert cusum.calibrated_h(None, p=0.4) == cusum.calibrated_h(0.0) == cusum.FALLBACK_H
    assert cusum.calibrated_h(float("nan")) == cusum.FALLBACK_H


@pytest.mark.parametrize("p", [0.4, 0.5, 0.6])
def test_two_week_champion_watch_is_calibrated_on_its_own_window(p):
    for tpw in (5.0, 15.0):
        h_watch = cusum.calibrated_h(tpw, p=p, weeks=cusum.WATCH_WEEKS)
        h_quarter = cusum.calibrated_h(tpw, p=p)
        n = _n(tpw, cusum.WATCH_WEEKS)
        assert n == round(tpw * 2)
        assert h_watch < h_quarter                                         # fewer trades: a lower bar for 5%
        assert cusum.alarm_rate(h_watch, 0.5, n, p=p) <= 0.05
        # the quarterly h wasted the window: it almost never alarms within two weeks, even on a real drop
        assert cusum.alarm_rate(h_quarter, 0.5, n, p=p) < 0.01
        assert cusum.alarm_rate(h_watch, 0.5, n, p=p, p_true=p - 0.15) > \
            2 * cusum.alarm_rate(h_quarter, 0.5, n, p=p, p_true=p - 0.15)


def test_residual_cusum_and_cusum_alarm_use_the_calibrated_h():
    h_q = cusum.calibrated_h(5.0, p=0.4)                                   # quarterly, two-point at p 0.4
    h_n = cusum.calibrated_h(5.0)                                          # the old normal-based value
    s = (h_q + h_n) / 2
    assert residual_cusum([-(s + 0.5)], trades_per_week=5.0, p=0.4)[0]
    assert not residual_cusum([-(s + 0.5)], trades_per_week=5.0)[0]
    h_w = cusum.calibrated_h(4.0, p=0.45, weeks=cusum.WATCH_WEEKS)        # the champion watch: two weeks
    h_wq = cusum.calibrated_h(4.0, p=0.45)
    s = (h_w + h_wq) / 2
    r = -(s + 0.5) * 0.01                                                  # one return s + k sd below a mean of 0
    assert cusum_alarm([r], 0.0, 0.01, trades_per_week=4.0, p=0.45)
    assert not cusum_alarm([r], 0.0, 0.01, trades_per_week=4.0, p=0.45, weeks=cusum.QUARTER_WEEKS)
    assert not residual_cusum([-5.0], h=10.0, trades_per_week=1.0)[0]      # an explicit h still wins


def test_settings_carry_k_and_the_design_false_alarm_rate():
    d = load_settings().drift
    assert d.cusum_k == 0.5 and d.cusum_false_alarm == 0.05


# ---------------------------------------------------------------------------------------------- re-verify (M25)
def test_h_sits_halfway_between_the_chosen_reachable_value_and_the_next_higher_one():
    peaks = np.array([0.0] * 90 + [1.0] * 4 + [2.0] * 3 + [3.0] * 3)      # P(>0)=10%, P(>1)=6%, P(>2)=3%, P(>3)=0
    assert cusum.decision_interval(peaks, 0.05, z=0.0) == 2.5             # same 3% rate as 2.0, off the lattice
    assert cusum.decision_interval(np.array([0.0] * 99 + [1.0]), 0.0, z=0.0) == 1.5   # above the top value
    for p in (0.4, 0.5):
        h = cusum.calibrated_h(5.0, p=p)
        win, loss = cusum.residual_values(p)
        # no reachable value of S within a tiny perturbation of h (S moves in steps of -loss - k and -win - k)
        steps = {round(a * (-loss - 0.5) - b * (win + 0.5), 6) for a in range(80) for b in range(80)}
        assert min(abs(h - s) for s in steps if s >= 0) > 1e-3


def _agent_ps(lo: float, hi: float) -> list[float]:
    """An agent's taken p values spread evenly over [lo, hi] in 0.001 steps (off the 0.01 calibration buckets)."""
    return [float(x) for x in np.linspace(lo, hi, 201)]


LIVE_PATHS = 200_000        # rate estimate: standard error ~0.05 pp at 5%
LIVE_CHECKED = 4_000        # paths also run one by one through the production function, decision for decision
T_R = 1.5                   # target distance in stop units (R): win +1.5 R, loss -1 R


def _live_rate(ps: list[float], n: int, h: float, watch: bool = False, seed: int = 31) -> float:
    """In-control false-alarm rate of the LIVE code, production float arithmetic (no rounding), each trade's p drawn
    from the agent's taken p values and its outcome a Bernoulli(p) barrier hit. Drift watch (watch False): residuals
    standardised exactly as drift.trade_residuals does, through drift.residual_cusum with h. Champion watch (watch
    True): R returns standardised by the backtest's moments at the marginal hit rate, through
    promotion.cusum_alarm with h. The first LIVE_CHECKED paths go through the production function itself and must
    give the same decision as the vectorised replica, which then measures the rate on LIVE_PATHS paths."""
    rng = np.random.default_rng(seed)
    arr = np.asarray(ps)
    hit = float(arr.mean())
    mean, std = hit * T_R - (1 - hit), float(np.sqrt(hit * (1 - hit)) * (T_R + 1))
    s = np.zeros(LIVE_PATHS)
    alarm = np.zeros(LIVE_PATHS, dtype=bool)
    kept = np.zeros((LIVE_CHECKED, n))
    for t in range(n):
        p = arr[rng.integers(0, len(arr), LIVE_PATHS)]
        r = np.where(rng.random(LIVE_PATHS) < p, T_R, -1.0)
        if watch:
            x = r
            s = np.maximum(0.0, s + (mean - r) / std - 0.5)
        else:
            x = (r - (p * T_R - (1 - p))) / (np.sqrt(p * (1 - p)) * (T_R + 1))
            s = np.maximum(0.0, s - x - 0.5)
        kept[:, t] = x[:LIVE_CHECKED]
        alarm |= s > h
    for i in range(LIVE_CHECKED):
        row = [float(v) for v in kept[i]]
        live = cusum_alarm(row, mean, std, h=h) if watch else residual_cusum(row, 0.5, h)[0]
        assert live == bool(alarm[i])
    return float(alarm.mean())


@pytest.mark.parametrize("spread", [(0.35, 0.55), (0.30, 0.70)])
@pytest.mark.parametrize("trades_per_week", [2.0, 5.0, 15.0])
def test_live_residual_cusum_false_alarm_rate_with_per_trade_p_is_at_most_five_percent(spread, trades_per_week):
    ps = _agent_ps(*spread)
    h = cusum.calibrated_h(trades_per_week, 0.5, p=float(np.mean(ps)), ps=ps)      # what drift.assess passes
    rate = _live_rate(ps, _n(trades_per_week), h)
    assert 0.04 <= rate <= 0.05


@pytest.mark.parametrize("spread", [(0.35, 0.55), (0.30, 0.70)])
@pytest.mark.parametrize("trades_per_week", [2.0, 6.0])
def test_live_champion_watch_false_alarm_rate_with_per_trade_p_is_at_most_five_percent(spread, trades_per_week):
    ps = _agent_ps(*spread)
    n = cusum.watch_trades(trades_per_week)
    assert n is not None
    h = cusum.calibrated_h(trades_per_week, 0.5, p=float(np.mean(ps)), trades=n)   # what model_watch passes
    assert _live_rate(ps, n, h, watch=True, seed=37) <= 0.05


def test_calibration_resamples_the_taken_p_values_not_their_mean():
    ps = _agent_ps(0.35, 0.55)
    for tpw in (2.0, 5.0, 15.0):
        n = _n(tpw)
        h_mean = cusum.calibrated_h(tpw, p=0.45)                          # the re-verify's finding: 5.7-6.3% live
        h_ps = cusum.calibrated_h(tpw, p=0.45, ps=ps)
        assert h_ps > h_mean
        assert _live_rate(ps, n, h_mean) > 0.05 >= _live_rate(ps, n, h_ps)
    # p values in one 0.01 bucket are the single-p calibration exactly; the histogram is the cache key
    assert cusum.calibrated_h(5.0, p=0.4, ps=[0.401, 0.399, 0.4]) == cusum.calibrated_h(5.0, p=0.4)
    assert cusum.p_histogram([0.401, 0.399, 0.452]) == ((0.4, 2), (0.45, 1)) and cusum.p_histogram([]) is None
    assert cusum.alarm_rate(cusum.calibrated_h(5.0, p=0.5, ps=_agent_ps(0.3, 0.7)), 0.5, 65, ps=_agent_ps(0.3, 0.7)) < 0.052


def test_champion_watch_runs_two_weeks_or_twelve_trades_whichever_is_later_capped_at_eight_weeks():
    assert cusum.watch_trades(10.0) == 20                 # two weeks already hold more than 12 trades
    assert cusum.watch_trades(2.0) == 12                  # 2 a week: 4 in two weeks, so the first 12 trades
    assert cusum.watch_trades(2.5) == 12
    assert cusum.watch_trades(1.0) == 8                   # 8-week cap: 8 trades
    assert cusum.watch_trades(None) is None and cusum.watch_trades(0.0) is None
    # at 2-2.5 trades a week a 12-trade watch can alarm; the old two-week window (4-5 trades) could not
    for tpw, p in ((2.0, 0.45), (2.5, 0.4)):
        n12, n2 = cusum.watch_trades(tpw), _n(tpw, cusum.WATCH_WEEKS)
        assert n12 is not None and n2 <= 5
        h12 = cusum.calibrated_h(tpw, p=p, trades=n12)
        assert cusum.can_alarm(h12, n12, 0.5, p)
        assert not cusum.can_alarm(cusum.calibrated_h(tpw, p=p, weeks=cusum.WATCH_WEEKS), n2, 0.5, p)
        assert cusum.alarm_rate(h12, 0.5, n12, p=p) <= 0.05
        assert cusum.alarm_rate(h12, 0.5, n12, p=p, p_true=p - 0.2) > 0.2      # a real decay is now catchable
    # too few trades even at the cap: no alarm is reachable, which the watch must report, not hide
    n = cusum.watch_trades(0.5)
    assert n == 4 and not cusum.can_alarm(cusum.calibrated_h(0.5, p=0.4, trades=n), n, 0.5, 0.4)
    assert cusum.can_alarm(4.0, 4, 0.5, None)             # normal values: always reachable
