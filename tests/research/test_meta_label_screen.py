"""Meta-label screen logic (docs/research/prereg-ml-meta-label-2026-11-01.md): walk, costs, folds, selection, control."""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.research import meta_label_screen as M
from goldbot.research.model import check_feature_cap


def ns(s: str) -> int:
    return int(pd.Timestamp(s, tz="UTC").value)


def test_model_columns_fit_the_live_cap_and_are_unique() -> None:
    cols = M.model_columns()
    assert len(cols) == 40 and len(set(cols)) == 40 and cols[-1] == "side"
    check_feature_cap(cols)


def _bars(path: list[tuple[float, float, float, float]]) -> tuple[np.ndarray, ...]:
    a = np.array(path, dtype=float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


def test_walk_target_stop_time_and_stop_first_in_one_minute() -> None:
    o, h, lo, c = _bars([(100, 100, 100, 100), (100, 103, 99.5, 102), (102, 102, 98, 99), (99, 99, 99, 99.5)])
    k0, k1 = np.array([0, 0, 0, 2]), np.array([4, 4, 4, 4])
    side = np.array([1, -1, 1, 1])
    risk = np.array([1.0, 1.0, 5.0, 2.0])
    tgt = np.array([2.0, 2.0, 2.0, 2.0])
    g, ek, st = M.walk(o, h, lo, c, k0, k1, side, risk, tgt)
    assert g[0] == 2.0 and ek[0] == 1 and not st[0]          # long: +3 >= 2R before -1R (low 99.5)
    assert g[1] == -1.0 and ek[1] == 1 and st[1]             # short: high 103 is the stop in minute 1
    assert np.isclose(g[2], (99.5 - 100) / 5.0) and not st[2] and ek[2] == 3   # time exit at close[k1-1]
    o2, h2, l2, c2 = _bars([(100, 104, 96, 100)])
    g2, _, st2 = M.walk(o2, h2, l2, c2, np.array([0]), np.array([1]), np.array([1]), np.array([2.0]), np.array([2.0]))
    assert g2[0] == -1.0 and st2[0]                           # both barriers in one minute: stop first


def test_rollovers_count_weekdays_and_triple_wednesday() -> None:
    # Tue 2015-06-02 12:00 -> Thu 2015-06-04 12:00 holds Tue 22:00 (1) and Wed 22:00 (3)
    assert M.rollovers(np.array([ns("2015-06-02 12:00")]), np.array([ns("2015-06-04 12:00")]))[0] == 4.0
    # Fri 23:00 -> Sun 23:00: Sat and Sun rollovers are free
    assert M.rollovers(np.array([ns("2015-06-05 23:00")]), np.array([ns("2015-06-07 23:00")]))[0] == 0.0
    assert M.rollovers(np.array([ns("2015-06-02 21:00")]), np.array([ns("2015-06-02 23:00")]))[0] == 1.0


def test_net_r_charges_costs_overshoot_and_long_swap_only() -> None:
    g = np.array([0.5, -1.0, 0.5])
    out = M.net_r(g, np.array([False, True, False]), np.array([1, -1, -1]), np.array([4.0, 4.0, 4.0]),
                  np.array([0.2, 0.2, 0.2]), np.array([0.1, 0.1, 0.1]), np.array([1.0, 1.0, 1.0]))
    assert np.allclose(out, [0.5 - 0.8 / 4, -1.0 - 0.3 / 4, 0.5 - 0.2 / 4])


def test_purged_folds_drop_overlap_and_embargo_and_never_train_on_test() -> None:
    dec = np.array([ns("2011-12-31 12:00"), ns("2012-06-01"), ns("2013-01-02"), ns("2013-02-01"), ns("2011-06-01")])
    ex = dec + 20 * 3600 * 10**9
    yrs = pd.DatetimeIndex(pd.to_datetime(dec, utc=True)).year.to_numpy()
    folds = {y: (tr, te) for y, tr, te in M.purged_year_folds(dec, ex, yrs)}
    tr, te = folds[2012]
    assert te.tolist() == [False, True, False, False, False]
    assert not tr[0]          # exits within 26 h of the test start: purged
    assert not tr[2]          # starts within the 5-day embargo after the test year
    assert tr[3] and tr[4]
    assert not (tr & te).any()


def test_forward_folds_train_only_on_resolved_past() -> None:
    dec = np.array([ns("2011-12-31 20:00"), ns("2011-06-01"), ns("2012-03-01")])
    ex = dec + 20 * 3600 * 10**9
    yrs = pd.DatetimeIndex(pd.to_datetime(dec, utc=True)).year.to_numpy()
    y, tr, te = M.forward_folds(ex, yrs)[0]
    assert y == 2012 and tr.tolist() == [False, True, False] and te.tolist() == [False, False, True]


def test_top_decile_is_per_year() -> None:
    pred = np.r_[np.arange(100.0), np.arange(100.0) - 1000]
    yrs = np.r_[np.full(100, 2010), np.full(100, 2011)]
    sel = M.top_decile(pred, yrs)
    assert sel[:100].sum() == 10 and sel[100:].sum() == 10


def test_matched_control_keeps_year_side_counts_and_centres_on_cell_means() -> None:
    rng = np.random.default_rng(0)
    n = 4000
    yrs = rng.choice([2010, 2011], n)
    side = rng.choice([-1, 1], n)
    y = np.where(side > 0, 1.0, -1.0) + rng.normal(0, 0.1, n)
    sel = np.zeros(n, dtype=bool)
    sel[np.flatnonzero(side > 0)[:300]] = True                 # all longs
    ctrl = M.matched_control(y, yrs, side, sel, np.ones(n, dtype=bool), 200, 1)
    assert abs(ctrl.mean() - 1.0) < 0.02                       # side-matched: a long-only pick is not "skill"


def test_week_block_bootstrap_centres_on_mean() -> None:
    rng = np.random.default_rng(3)
    y = rng.normal(0.2, 1.0, 5000)
    week = np.repeat(np.arange(500), 10)
    b = M.week_block_bootstrap(y, week, 2000, 7)
    assert abs(b.mean() - y.mean()) < 0.01 and np.quantile(b, 0.05) < y.mean()


def test_one_at_a_time_skips_overlaps() -> None:
    keep = M.one_at_a_time(np.array([0, 5, 10, 20]), np.array([10, 15, 12, 30]))
    assert keep.tolist() == [True, False, True, True]


def test_verdict_legs() -> None:
    ym = {y: (0.05, 50) for y in range(2010, 2018)}
    assert M.verdict(5000, np.zeros(400), 0.01, 0.01, ym, 0.02)["verdict"] == "PASS"
    assert M.verdict(5000, np.zeros(400), -0.01, 0.01, ym, 0.02)["verdict"] == "FAIL"
    assert M.verdict(5000, np.zeros(400), 0.01, 0.03, ym, 0.02)["verdict"] == "FAIL"
    assert M.verdict(2000, np.zeros(400), 0.01, 0.01, ym, 0.02)["verdict"] == "UNDERPOWERED"
    few = {y: (0.05, 10) for y in range(2010, 2018)}
    assert M.verdict(5000, np.zeros(400), 0.01, 0.01, few, 0.02)["verdict"] == "FAIL"


def test_m1c_columns_swap_h1_adx_for_h4_adx_and_keep_the_cap() -> None:
    cols = M.model_columns(M.CONTEXT_FEATURES_1H)
    assert len(cols) == 40 and len(set(cols)) == 40 and "h4_adx14" in cols and "h1_adx14" not in cols
    check_feature_cap(cols)


def test_side_leg_fails_a_one_sided_or_losing_side_book() -> None:
    side = np.r_[np.full(40, 1), np.full(40, -1)]
    sel = np.ones(80, dtype=bool)
    assert M.side_leg(np.r_[np.full(40, 0.1), np.full(40, 0.0)], side, sel)["ok"]
    assert not M.side_leg(np.r_[np.full(40, -0.01), np.full(40, 0.3)], side, sel)["ok"]
    short_only = sel & (side < 0)
    assert not M.side_leg(np.full(80, 0.2), side, short_only | (np.arange(80) < 10))["ok"]   # 10 longs < 30


def test_verdict_side_leg_and_single_alpha() -> None:
    ym = {y: (0.05, 50) for y in range(2010, 2018)}
    assert M.verdict(5000, np.zeros(400), 0.01, 0.04, ym, 0.02, alpha=M.ALPHA_SINGLE, side_ok=True)["verdict"] == "PASS"
    assert M.verdict(5000, np.zeros(400), 0.01, 0.04, ym, 0.02, alpha=M.ALPHA_SINGLE, side_ok=False)["verdict"] == "FAIL"
