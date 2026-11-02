"""W3 read statistics: block-bootstrap t, years positive, counts, nulls, BH-FDR and the W3.1 power helpers."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from goldbot.research.position import engine, stats
from goldbot.research.position.costs import PointCosts
from goldbot.research.position.engine import EngineConfig
from goldbot.research.position.financing import Rates
from goldbot.research.position.signals import PositionSpec
from goldbot.research.position.windows import Window
from tests.research.position.helpers import NO_FINANCING, daily_from_returns


def test_bh_fdr_is_step_up_at_q() -> None:
    assert stats.bh_fdr([0.01, 0.02, 0.03, 0.5], q=0.10).tolist() == [True, True, True, False]
    assert stats.bh_fdr([0.06, 0.04], q=0.10).tolist() == [True, True]          # 0.06 <= 2/2 x 0.10 lifts both
    assert stats.bh_fdr([0.2, 0.3], q=0.10).tolist() == [False, False]
    assert stats.bh_fdr([], q=0.10).tolist() == []


def test_the_block_bootstrap_t_is_near_the_iid_t_on_iid_days() -> None:
    rng = np.random.default_rng(1)
    x = rng.normal(0.1, 1.0, 4000)
    t = stats.block_t(x, resamples=2000, block_days=20.0, seed=7)
    assert t == pytest.approx(x.mean() / x.std(ddof=1) * np.sqrt(len(x)), rel=0.15)
    assert stats.block_t(np.zeros(50), resamples=100, block_days=20.0, seed=1) == 0.0


def test_years_positive_needs_sixty_percent_of_available_years_rounded_up() -> None:
    assert stats.years_needed(8) == 5 and stats.years_needed(7) == 5 and stats.years_needed(3) == 2
    days = pd.bdate_range("2010-01-01", "2012-12-31")
    pnl = pd.Series(np.where(days.year == 2011, -1.0, 1.0), index=days)
    assert stats.years_positive(pnl) == (2, 3)


def test_round_trips_count_entries_from_flat_and_flips_and_days_in_market_count_held_days() -> None:
    b = np.array([0, 1, 1, 0, -1, -1, 1, 0], dtype=float)
    held = b.copy()
    ep = stats.episodes(b, held)
    assert ep.tolist() == [-1, 0, 0, -1, 1, 1, 2, -1]
    assert int(ep.max()) + 1 == 3 and int((b != 0).sum()) == 5     # 3 round trips, 5 days in market
    b2 = np.array([0, 1, 1, 1, 0], dtype=float)
    held2 = np.array([0, 1, 0, 1, 0], dtype=float)                 # stopped on day 2, back in on day 3
    assert stats.episodes(b2, held2).tolist() == [-1, 0, 0, 1, -1]


def test_the_power_helpers_reproduce_the_pos1_table() -> None:
    assert stats.p_explore(0.40, 10) == pytest.approx(0.23, abs=0.005)
    assert stats.p_explore(1.036, 10) == pytest.approx(0.90, abs=0.005)
    assert stats.sr_blend(0.467, 16, 0.15) == pytest.approx(1.036, abs=0.002)
    assert stats.sr_blend(0.40, 3, 0.6) == pytest.approx(0.467, abs=0.002)
    assert stats.p_chain(1.036, 10, draws=200_000, seed=1) == pytest.approx(0.627, abs=0.03)
    assert stats.p_chain(0.40, 10, draws=200_000, seed=1) == pytest.approx(0.022, abs=0.006)


def test_a_long_only_spec_is_read_against_the_exposure_matched_null_with_buy_and_hold_beside_it() -> None:
    rng = np.random.default_rng(4)
    bars = daily_from_returns(rng.normal(0.0004, 0.01, 1500))
    sig = (bars["close"] > bars["close"].rolling(50).mean()).astype(float)
    spec = PositionSpec(id="LF", symbols=("XAUUSD",), long_only=True,
                        signal=lambda s, p: sig.reindex(p[s].index))
    w = Window(name="T", start=bars.index[300].date(), end=bars.index[-1].date(), use="test")
    res = engine.run(spec, {"XAUUSD": bars}, w, cfg=EngineConfig(null_draws=100, boot_resamples=200),
                     costs={"XAUUSD": PointCosts(rt_bps=0.4, overshoot_bps=0.3)}, venues={"A": NO_FINANCING},
                     rates=Rates(constant_pct=0.0))
    book = res.venues["A"].book
    assert book.null_kind == "exposure" and book.null_excess is not None and book.null_p is not None
    assert np.isfinite(book.null_excess) and 0 < book.null_p <= 1
    assert book.short_net == 0.0 and book.buy_hold_net is not None
    assert book.days_in_market > 0 and book.n_round_trips > 0


def test_explore_checks_apply_the_w3_explore_legs() -> None:
    good = stats.PositionStats(n_days=2000, mean_daily=1e-4, net_total=0.2, gross_total=0.25, cost_total=0.03,
                               financing_total=-0.02, sharpe=0.6, t_boot=2.3, gross_t=2.5, years_positive=6,
                               n_years=8, years_rule=True, drift_adj_gross=0.05, long_net=0.15, short_net=0.05,
                               n_round_trips=40, days_in_market=900, null_kind="sign", null_mean_net=-0.03,
                               null_excess=0.23, null_p=0.01)
    assert all(stats.explore_checks(good).values())
    bad = good.model_copy(update={"n_round_trips": 29, "t_boot": 1.9})
    checks = stats.explore_checks(bad)
    assert not checks["round_trips>=30"] and not checks["t_boot>=2"] and checks["days_in_market>=500"]
