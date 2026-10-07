"""Promotion gate boundaries, the CUSUM watch and the population's scoring and mutation rules."""
import random

import numpy as np
import pandas as pd
import pytest

from goldbot.config import tf_seconds
from goldbot.research.population import (
    LOWER_80_Z,
    calibration_ece,
    correlation_with,
    mutate_agent,
    mutate_config,
    score_agent,
    weekly_returns,
)
from goldbot.research.promotion import PerfStats, cusum_alarm, evaluate_promotion
from goldbot.specialists import SPECIALISTS
from goldbot.specialists.base import FEATURE_SEED_KEY, TIMEFRAME_KEY

BT = PerfStats(n_trades=400, sharpe_ann=1.4, hit_rate=0.55, max_dd=0.06, trades_per_week=10, weeks=52)


def _shadow(**kw) -> PerfStats:
    base = dict(n_trades=60, sharpe_ann=1.2, hit_rate=0.55, max_dd=0.05, trades_per_week=10.0, weeks=6.0)
    base.update(kw)
    return PerfStats(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------------- promotion
@pytest.mark.parametrize("kw, ready", [(dict(weeks=3.99), False), (dict(n_trades=39), False),
                                       (dict(weeks=4.0, n_trades=40), True)])
def test_shadow_needs_four_weeks_and_forty_trades_whichever_is_later(kw, ready):
    d = evaluate_promotion(BT, _shadow(**kw))
    assert d.ready is ready
    if not ready:
        assert not d.promote and d.failed == ["shadow_length"] and len(d.checks) == 1


@pytest.mark.parametrize("kw, gate", [
    (dict(sharpe_ann=0.8), "sharpe_floor"),                    # the floor is strict: 0.8 itself fails
    (dict(max_dd=0.09), "drawdown"),                            # exactly 1.5x the backtest drawdown fails
    (dict(hit_rate=0.55 + 0.07), "hit_rate"),                   # too GOOD is also outside one binomial SE
])
def test_gate_boundaries(kw, gate):
    d = evaluate_promotion(BT, _shadow(**kw))
    assert d.ready and not d.promote and gate in d.failed


def test_sharpe_shortfall_and_turnover_boundaries():
    assert evaluate_promotion(BT, _shadow(sharpe_ann=0.9)).promote                 # exactly 0.5 below backtest
    assert "sharpe_vs_backtest" in evaluate_promotion(BT, _shadow(sharpe_ann=0.89)).failed
    champ = PerfStats(n_trades=100, sharpe_ann=1.0, hit_rate=0.5, max_dd=0.05, trades_per_week=10)
    assert evaluate_promotion(BT, _shadow(trades_per_week=13.0), champ).promote     # +30% allowed
    assert evaluate_promotion(BT, _shadow(trades_per_week=13.1), champ).failed == ["turnover"]
    idle = PerfStats(n_trades=0, sharpe_ann=0, hit_rate=0, max_dd=0, trades_per_week=0)
    assert "turnover" not in [c.name for c in evaluate_promotion(BT, _shadow(), idle).checks]


def test_cusum_alarms_on_a_sustained_drop_but_not_on_the_expected_record():
    rng = np.random.default_rng(0)
    assert not cusum_alarm(list(rng.normal(0.002, 0.01, 30)), 0.002, 0.01)
    assert cusum_alarm([-0.02] * 4, 0.002, 0.01)                                  # ~2.2 sd below per trade
    assert not cusum_alarm([-0.02] * 4, 0.002, 0.0) and not cusum_alarm([], 0.002, 0.01)
    assert not cusum_alarm([0.05] * 50, 0.002, 0.01)                               # one-sided: gains never alarm


# ---------------------------------------------------------------------------------------------- population scoring
def _trades(rets: list[float], p: float = 0.6, risk: float = 0.002) -> pd.DataFrame:
    n = len(rets)
    return pd.DataFrame({"ret": rets, "r": [r / risk for r in rets], "p": p, "target_hit": [int(r > 0) for r in rets],
                         "exit_ts": pd.date_range("2026-06-01", periods=n, freq="8h", tz="UTC")})


def test_calibration_error_is_zero_when_calibrated_and_one_without_data():
    p = np.array([0.25] * 4 + [0.75] * 4)
    y = np.array([1, 0, 0, 0, 1, 1, 1, 0], dtype=float)
    assert calibration_ece(p, y) == pytest.approx(0.0)
    assert calibration_ece(np.array([0.9, 0.9]), np.array([0.0, 0.0])) == pytest.approx(0.9)
    assert calibration_ece(np.array([]), np.array([])) == 1.0


def test_score_uses_the_lower_80_bound_and_needs_two_trades():
    one = score_agent(_trades([0.004]), 0.0, 10)
    assert one.n == 1 and one.expectancy_lower80_r is None and one.fitness == 0.0
    rets = [0.004, -0.002, 0.004, -0.002, 0.004, 0.004]
    s = score_agent(_trades(rets), 0.0, 10)
    r = np.array(rets) / 0.002
    assert s.expectancy_lower80_r == pytest.approx(r.mean() - LOWER_80_Z * r.std(ddof=1) / np.sqrt(len(r)))
    assert s.fitness == max(s.score, 0.0) and s.hit_rate == pytest.approx(4 / 6)


def test_diversity_penalty_and_losing_agents_get_no_capital():
    rets = [0.004, -0.002, 0.004, -0.002, 0.004, 0.004]
    free = score_agent(_trades(rets), 0.0, 10)
    half = score_agent(_trades(rets), 0.5, 10)
    neg = score_agent(_trades(rets), -0.8, 10)
    assert half.score == pytest.approx(free.score * 0.5) and neg.score == pytest.approx(free.score)
    loser = score_agent(_trades([-0.002, 0.001, -0.002, -0.002]), 0.0, 10)
    assert loser.score < 0 and loser.fitness == 0.0


def test_weekly_returns_bucket_by_monday_and_correlation_needs_four_weeks():
    t = pd.DataFrame({"ret": [0.01, 0.02, -0.01],
                      "exit_ts": pd.to_datetime(["2026-10-04 23:00", "2026-10-05 01:00", "2026-10-11 12:00"], utc=True)})
    w = weekly_returns(t)
    assert list(w.index) == list(pd.to_datetime(["2026-09-28", "2026-10-05"], utc=True))
    assert list(w.round(6)) == [0.01, 0.01]
    assert correlation_with(w, [w]) == 0.0                                          # fewer than four weeks
    long = pd.Series([0.01, -0.02, 0.03, 0.0, 0.01], index=pd.date_range("2026-08-03", periods=5, freq="7D", tz="UTC"))
    assert correlation_with(long, [long]) == pytest.approx(1.0)
    assert correlation_with(long, []) == 0.0


# ---------------------------------------------------------------------------------------------- mutations
def test_mutate_config_refuses_configs_without_numeric_values():
    with pytest.raises(ValueError):
        mutate_config({"flag": True, "zero": 0, FEATURE_SEED_KEY: 5, "name": "x"}, random.Random(1))


def test_timeframe_mutations_keep_the_holding_horizon_and_feature_mutations_change_the_seed():
    fam = next(f for f, c in SPECIALISTS.items() if c.timeframes and "max_bars" in c.default_config)
    cls = SPECIALISTS[fam]
    base = dict(cls.default_config)
    seen = set()
    for seed in range(200):
        kind, cfg = mutate_agent(fam, base, random.Random(seed))
        seen.add(kind)
        assert cfg != base
        if kind == "timeframe":
            old, new = cls.timeframe, cfg[TIMEFRAME_KEY]
            assert new != old
            hours_old = base["max_bars"] * tf_seconds(old) / 3600
            hours_new = cfg["max_bars"] * tf_seconds(new) / 3600
            assert hours_new == pytest.approx(hours_old, rel=0.5)
        elif kind == "features":
            assert cfg[FEATURE_SEED_KEY] != base.get(FEATURE_SEED_KEY)
            assert {k: v for k, v in cfg.items() if k != FEATURE_SEED_KEY} == base
    assert seen == {"params", "features", "timeframe"}
