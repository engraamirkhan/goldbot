"""EVAL-2 ladder statistics (prereg 2026-11-09 section 4.3; method v6 sections 1-2).

"Standard errors clustered by session day." "Power floor n_min = ((z(1 - q/m) + 0.84) x sd_card / delta_card)^2."
"L3 statistic: control-variate net: family precision-weighted mean excess + mean net of all twins in the carried
cells, at 1.0x deciding cost." "Over 2006-09 + 2018-25: net > 0 in >= 3 calendar years, at least one of 2021/2022, no
2-year sub-period with net t <= -2.\""""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from goldbot.research.catalogue import stats as S
from goldbot.research.catalogue.costs import rollover_table
from goldbot.research.catalogue.data import DAY, HOUR
from goldbot.research.catalogue.sim import session_day


def units(rows: list[tuple[str, int, float, float, float]], *, family: str = "F", year: int = 2008) -> pd.DataFrame:
    """(cell_id, day, net_r, twin_mean_net_r, cost_r) -> the frozen `units` frame (excess = net - twin mean)."""
    df = pd.DataFrame(rows, columns=["cell_id", "day", "net_r", "twin_mean_net_r", "cost_r"])
    df["excess_r"] = df["net_r"] - df["twin_mean_net_r"]
    df["family"], df["inst"], df["tf"], df["side"] = family, "XAUUSD", "1h", 1
    df["decision_ns"] = df["day"].astype(np.int64) * DAY
    df["year"] = year
    return df


def test_cluster_se_of_iid_singletons_is_the_plain_standard_error_with_the_small_cluster_factor():
    x = np.random.default_rng(1).normal(0, 1, 400)
    day = np.arange(400)
    plain = x.std(ddof=1) / math.sqrt(400)
    assert S.cluster_se(x, day) == pytest.approx(plain, rel=1e-9)


def test_cluster_se_grows_when_instances_on_one_session_day_move_together():
    rng = np.random.default_rng(2)
    day = np.repeat(np.arange(200), 5)
    x = np.repeat(rng.normal(0, 1, 200), 5) + rng.normal(0, 0.1, 1000)       # one shock per day, five instances
    naive = x.std(ddof=1) / math.sqrt(len(x))
    assert S.cluster_se(x, day) > 2.0 * naive
    assert S.cluster_se(x, day) == pytest.approx(1 / math.sqrt(200), rel=0.15)


def test_cluster_se_leaves_out_nan_instances_and_needs_two_days():
    assert math.isnan(S.cluster_se(np.array([1.0, 2.0]), np.array([3, 3])))
    a = S.cluster_se(np.array([1.0, np.nan, 2.0, 0.5]), np.array([1, 2, 3, 4]))
    assert a == pytest.approx(S.cluster_se(np.array([1.0, 2.0, 0.5]), np.array([1, 3, 4])))


def test_session_days_come_from_the_17_00_new_york_rollover_clock():
    """Clustering uses the #343 rollover table (costs.rollover_table): an instant at the rollover opens the next day."""
    d0, roll = rollover_table()
    k = 6_000
    r = int(roll[k])
    assert session_day(np.array([r - 1, r, r + HOUR])).tolist() == [d0 + k, d0 + k + 1, d0 + k + 1]


def test_cell_day_means_reads_the_excess_column_of_the_frozen_units_schema():
    u = units([("a", 1, 1.0, 0.0, 0.1), ("a", 1, 3.0, 0.0, 0.1), ("a", 2, -1.0, 0.0, 0.1), ("b", 1, 0.5, 0.5, 0.1)])
    out = S.cell_day_means(u)
    assert list(out.columns) == ["cell_id", "day", "mean", "n"]
    rows = {(r.cell_id, r.day): (r.mean, r.n) for r in out.itertuples()}
    assert rows == {("a", 1): (2.0, 2), ("a", 2): (-1.0, 1), ("b", 1): (0.0, 1)}
    net = S.cell_day_means(u, col="net_r")
    assert net.loc[net["cell_id"] == "b", "mean"].item() == 0.5


def test_power_floor_n_is_the_preregistered_formula():
    """n_min = ((z(1 - q/m) + z(power)) x sd / delta)^2, rounded up."""
    from scipy.stats import norm
    n = S.power_floor_n(q=0.10, m=3, sd=1.56, delta=0.2655)
    expect = ((norm.ppf(1 - 0.10 / 3) + norm.ppf(0.8)) * 1.56 / 0.2655) ** 2
    assert n == math.ceil(expect) and 140 < n < 260


def test_power_at_reproduces_the_prereg_c03_30m_figures():
    """Prereg 3.3: SE 1.56 / sqrt(218), alpha_1 / m = 0.05 / 3: full excess 0.2655 R -> 0.650; half -> 0.192;
    2008-09 only (n 109): 0.363 / 0.108."""
    a = 0.05 / 3
    assert S.power_at(n=218, sd=1.56, delta=0.2655, alpha=a) == pytest.approx(0.650, abs=0.005)
    assert S.power_at(n=218, sd=1.56, delta=0.2655 / 2, alpha=a) == pytest.approx(0.192, abs=0.005)
    assert S.power_at(n=109, sd=1.56, delta=0.2655, alpha=a) == pytest.approx(0.363, abs=0.01)
    assert S.power_at(n=109, sd=1.56, delta=0.2655 / 2, alpha=a) == pytest.approx(0.108, abs=0.01)


def test_mde_is_the_inverse_of_power_at():
    """Method section 2: '1.68 sd/sqrt(n) gives 0.13 R at n 300' (L2's 0.80 carry at 80 % power: alpha 0.20)."""
    m = S.mde(n=300, sd=1.3, alpha=0.20)
    assert m == pytest.approx(0.126, abs=0.002)
    assert S.power_at(n=300, sd=1.3, delta=m, alpha=0.20) == pytest.approx(0.80, abs=1e-9)


def test_control_variate_net_is_the_weighted_mean_excess_plus_the_pooled_twin_mean_net():
    u = units([("a", 1, 1.0, 0.2, 0.1), ("a", 2, 0.4, -0.2, 0.1),        # a: excess 0.8, 0.6 -> 0.7
               ("b", 1, 0.0, 0.1, 0.1),                                  # b: excess -0.1
               ("z", 3, 9.0, 0.0, 0.1)])                                  # not carried: ignored
    w = {"a": 3.0, "b": 1.0}
    twin_mean = np.mean([0.2, -0.2, 0.1])
    assert S.control_variate_net(u, w) == pytest.approx((3 * 0.7 + 1 * -0.1) / 4 + twin_mean)


def test_control_variate_net_equals_the_plain_mean_net_for_one_cell():
    rng = np.random.default_rng(4)
    rows = [("a", int(d), float(n), float(t), 0.1) for d, n, t in zip(range(50), rng.normal(0.1, 1, 50),
                                                                         rng.normal(0, 0.3, 50))]
    u = units(rows)
    assert S.control_variate_net(u, {"a": 1.0}) == pytest.approx(u["net_r"].mean())


def test_control_variate_net_at_a_cost_multiple_charges_only_the_extra_cost():
    """Deciding cost is applied once: net_r already pays 1.0x, so 1.5x subtracts 0.5 x cost_r more."""
    u = units([("a", 1, 0.5, 0.0, 0.2), ("a", 2, 0.3, 0.0, 0.2)])
    base = S.control_variate_net(u, {"a": 1.0})
    assert S.control_variate_net_at(u, {"a": 1.0}, cost_mult=1.0) == pytest.approx(base)
    assert S.control_variate_net_at(u, {"a": 1.0}, cost_mult=1.5) == pytest.approx(base - 0.5 * 0.2)


def _yr(year: int, nets: list[float], day0: int) -> pd.DataFrame:
    df = units([("a", day0 + k, x, 0.0, 0.1) for k, x in enumerate(nets)])
    df["year"] = year
    return df


def test_year_rule_counts_positive_years_the_chop_year_and_two_year_t():
    good = pd.concat([_yr(2008, [1.0, -0.2], 0), _yr(2009, [0.5, 0.1], 10), _yr(2021, [0.4, 0.0], 20),
                      _yr(2022, [-0.3, 0.1], 30)], ignore_index=True)
    r = S.year_rule(good, chop_years=(2021, 2022))
    assert r == {"years_pos_ge3": True, "chop_year_pos": True, "no_2y_t_le_m2": True}
    two = pd.concat([_yr(2008, [1.0], 0), _yr(2009, [0.5], 10), _yr(2018, [-0.1], 20)], ignore_index=True)
    assert S.year_rule(two, chop_years=(2021, 2022))["years_pos_ge3"] is False
    assert S.year_rule(two, chop_years=(2021, 2022))["chop_year_pos"] is False


def test_year_rule_fails_a_two_year_sub_period_with_net_t_at_or_below_minus_two():
    rng = np.random.default_rng(9)
    bad = list(rng.normal(-0.5, 0.5, 60))
    df = pd.concat([_yr(2018, bad[:30], 0), _yr(2019, bad[30:], 100), _yr(2021, [1.0] * 5, 200),
                    _yr(2022, [1.0] * 5, 300), _yr(2023, [1.0] * 5, 400)], ignore_index=True)
    r = S.year_rule(df, chop_years=(2021, 2022))
    assert r["years_pos_ge3"] and r["chop_year_pos"] and r["no_2y_t_le_m2"] is False


def test_year_rule_never_tests_a_year_alone():
    """Quant M4 / amendment 10.1(e): the 2-year sub-periods are consecutive pairs in which both years hold data. 2018
    alone at t -2.5 next to unread 2017 is not a sub-period; the 2018-19 pair has t above -2."""
    rng = np.random.default_rng(3)
    bad18 = list(-0.5 + 0.2 * rng.standard_normal(10))                  # 2018 alone: t far below -2
    frames = [_yr(2008, [0.3] * 5, 0), _yr(2009, [0.3] * 5, 10), _yr(2018, bad18, 20),
              _yr(2019, [1.0, 0.9, 1.1, 0.8, 1.2, 1.0, 0.7, 1.3, 0.9, 1.0], 40)]
    frames += [_yr(y, [0.2] * 3, 60 + 5 * k) for k, y in enumerate(range(2020, 2026))]
    df = pd.concat(frames, ignore_index=True)
    t18 = S._t_clustered(df.loc[df["year"] == 2018, "net_r"].to_numpy(), df.loc[df["year"] == 2018, "day"].to_numpy())
    pair = df["year"].isin([2018, 2019])
    assert t18 < -2.5 and S._t_clustered(df.loc[pair, "net_r"].to_numpy(), df.loc[pair, "day"].to_numpy()) > -2
    assert S.year_rule(df, chop_years=(2021, 2022))["no_2y_t_le_m2"] is True
