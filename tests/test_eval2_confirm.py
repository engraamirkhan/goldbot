"""EVAL-2 L3 confirm (prereg 2026-11-09 sections 1, 3 and 4.6; method v6 section 2, L3).

BatchPRDS (Zrnic, Jiang, Ramdas and Jordan, AISTATS 2020, Algorithm 1) under the quant ruling of 2026-11-09: "both
windows use pure alpha-spending: alpha_j = q * gamma_j, BH at alpha_j within batch j (PRDS within a batch), no
rejection credit ever." gamma_1 = 0.5; gamma_j = 0.5 x (6 / pi^2) / (j - 1)^2 for j >= 2. "L3 p: one-sided
stationary day-block bootstrap (mean block = E1 lag), instances resampled with their twins." Lane 3 condition: "the
frozen helper confirm_resamples(m_j, alpha_j) is called by l3_read itself, with m_j = len(batch_p) and alpha_j =
batch.level(j, m_j)"."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from goldbot.research.catalogue import stats as S
from goldbot.research.catalogue.data import DAY
from goldbot.research.eval2 import confirm as C

PREREG_GAMMA = {1: 0.500000, 2: 0.303964, 3: 0.075991, 4: 0.033774, 5: 0.018998, 6: 0.012159, 7: 0.008443,
                8: 0.006203, 9: 0.004749, 10: 0.003753, 11: 0.003040, 12: 0.002512, 13: 0.002111, 14: 0.001799,
                15: 0.001551, 16: 0.001351}


def test_gamma_matches_the_prereg_table_and_sums_to_one():
    for j, g in PREREG_GAMMA.items():
        assert C.gamma(j) == pytest.approx(g, abs=5e-7), j
    assert C.GAMMA_FIRST == 0.5 and C.Q_CONFIRM == 0.10
    assert sum(C.gamma(j) for j in range(1, 200_001)) == pytest.approx(1.0, abs=1e-5)
    with pytest.raises(ValueError):
        C.gamma(0)


def test_batch_level_is_pure_alpha_spending_with_no_rejection_credit():
    """alpha_j = q gamma_j whatever n_j and whatever earlier batches rejected (Algorithm 1's reward is not used)."""
    b = C.BatchPRDS("test_old")
    assert b.level(1, 3) == pytest.approx(0.05) and b.level(1, 30) == pytest.approx(0.05)
    assert C.BatchPRDS("confirm").level(15, 3) == pytest.approx(0.1 * C.gamma(15))
    assert C.BatchPRDS("confirm").level(15, 3) == pytest.approx(0.000155, abs=1e-6)
    # anytime: the levels spent over any number of batches never exceed q (FDR <= sum_j FDR_j <= q)
    assert sum(b.level(j, 5) for j in range(1, 10_000)) <= b.q


def test_batch_test_is_benjamini_hochberg_at_the_batch_level():
    b = C.BatchPRDS("test_old")
    # m = 3 at alpha 0.05: BH thresholds 0.0167, 0.0333, 0.05
    assert b.test([0.010, 0.030, 0.20], j=1).tolist() == [True, True, False]
    assert b.test([0.020, 0.040, 0.049], j=1).tolist() == [True, True, True]      # step-up
    assert b.test([0.020, 0.040, 0.060], j=1).tolist() == [False, False, False]
    assert b.test([np.nan, 0.001], j=1).tolist() == [False, True]
    assert b.test([0.0002], j=2).tolist() == [True] and b.test([0.04], j=2).tolist() == [False]


def test_holm_is_step_down_at_the_family_level():
    assert C.holm([0.01, 0.02, 0.03], 0.05).tolist() == [True, True, True]
    assert C.holm([0.01, 0.04, 0.03], 0.05).tolist() == [True, False, False]       # 0.03 > 0.05/2 stops it
    assert C.holm([0.02, 0.01], 0.05).tolist() == [True, True]
    assert C.holm([0.03, 0.01], 0.05).tolist() == [True, True]                     # 0.01 <= 0.025, then 0.03 <= 0.05
    assert C.holm([0.06, 0.01], 0.05).tolist() == [False, True]
    assert C.holm([], 0.05).tolist() == []


def test_confirm_resamples_is_the_frozen_helper():
    assert C.confirm_resamples(3, 0.05) == 10_000
    assert C.confirm_resamples(3, 0.1 * C.gamma(15)) == math.ceil(20 * 3 / (0.1 * C.gamma(15)))
    assert C.confirm_resamples(1, 0.0001) == 200_000


TEST_OLD_DAY0 = 13_910          # 2008-01-31: TEST-OLD intraday 2008-01..2009-12
CONFIRM_DAY0 = 17_600           # 2018-03-10: 2018-01..2025-09


def _units(n_days: int = 300, per_day: int = 2, mu: float = 0.0, seed: int = 0, cells: tuple[str, ...] = ("a",),
           cost: float = 0.05, year0: int = 2008, day0: int = TEST_OLD_DAY0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = n_days * per_day
    day = np.repeat(np.arange(n_days), per_day) + day0
    twin = rng.normal(-0.03, 0.2, n)
    net = twin + mu + rng.normal(0.0, 1.0, n)
    df = pd.DataFrame({"cell_id": np.resize(np.array(cells), n), "family": "C03", "inst": "XAUUSD", "tf": "1h",
                       "side": 1, "day": day, "decision_ns": day * DAY, "net_r": net, "twin_mean_net_r": twin,
                       "excess_r": net - twin, "cost_r": cost})
    df["year"] = year0 + (np.arange(n) * 4 // n)
    return df


def test_the_stationary_day_block_bootstrap_resamples_whole_session_days():
    u = _units(n_days=50, per_day=3)
    seen: list[pd.DataFrame] = []

    def stat(d: pd.DataFrame) -> float:
        seen.append(d)
        return float(d["net_r"].mean())
    out = C.stationary_block_bootstrap(u, stat, mean_block=3.0, resamples=20, seed_key="k")
    assert out.shape == (20,) and np.isfinite(out).all()
    for d in seen:
        assert (d.groupby("day").size() % 3 == 0).all()                   # every day's instances travel together
        # instances keep their twins: the row is resampled whole
        assert np.allclose(d["excess_r"], d["net_r"] - d["twin_mean_net_r"])
    again = C.stationary_block_bootstrap(u, stat, mean_block=3.0, resamples=20, seed_key="k")
    assert np.array_equal(out, again)                                      # keyed by seed_key
    other = C.stationary_block_bootstrap(u, stat, mean_block=3.0, resamples=20, seed_key="other")
    assert not np.array_equal(out, other)


def test_bootstrap_blocks_have_the_requested_mean_length():
    u = _units(n_days=400, per_day=1)
    runs: list[float] = []

    def stat(d: pd.DataFrame) -> float:
        dd = d["day"].to_numpy()
        breaks = np.count_nonzero(np.diff(dd) != 1) + 1
        runs.append(len(dd) / breaks)
        return 0.0
    C.stationary_block_bootstrap(u, stat, mean_block=5.0, resamples=200, seed_key="b")
    assert np.mean(runs) == pytest.approx(5.0, rel=0.15)


def test_boot_p_is_small_for_a_real_edge_and_near_uniform_under_the_null():
    edge = _units(mu=0.25, seed=1)
    stat = lambda d: float(d["net_r"].mean())  # noqa: E731
    assert C.boot_p_one_sided(edge, stat, mean_block=2.0, resamples=2_000, seed_key="e") < 0.01
    ps = [C.boot_p_one_sided(_units(mu=0.0, seed=s, n_days=120), stat, mean_block=2.0, resamples=300,
                             seed_key=f"n{s}") for s in range(40)]
    assert 0.3 < float(np.mean(ps)) < 0.7
    assert np.mean(np.array(ps) <= 0.10) < 0.25


def test_boot_lower_bound_is_below_the_estimate_and_tightens_with_data():
    u = _units(mu=0.2, seed=3)
    stat = lambda d: float(d["net_r"].mean())  # noqa: E731
    lb = C.boot_lower_bound(u, stat, level=0.80, mean_block=2.0, resamples=2_000, seed_key="l")
    assert lb < stat(u) and lb > 0
    lb95 = C.boot_lower_bound(u, stat, level=0.95, mean_block=2.0, resamples=2_000, seed_key="l")
    assert lb95 < lb


@pytest.fixture
def fast(monkeypatch):
    """Behaviour tests read l3_read at 400 resamples (the real count, >= 10,000, is pinned by
    test_l3_read_resamples_follow_alpha); the cap is applied inside the helper l3_read calls, so the family's own p
    in batch_p is computed exactly as l3_read computes it."""
    real = C.confirm_resamples
    monkeypatch.setattr(C, "confirm_resamples", lambda m_j, alpha_j: min(400, real(m_j, alpha_j)))


def _years(df: pd.DataFrame, years: tuple[int, ...]) -> pd.DataFrame:
    k = np.arange(len(df)) * len(years) // len(df)
    return df.assign(year=np.array(years)[k])


def _corr(**kw) -> pd.DataFrame:
    """2018-25 corroboration units (decision instants inside 2018-01..2025-09)."""
    return _units(day0=CONFIRM_DAY0, **kw)


def _read(units: pd.DataFrame, corr: pd.DataFrame | None, *, j: int = 1, batch_p=None, window="test_old",
          cells=("a",), weights=None, confirm_seen=(), holds_multi_day=False, e9=None):
    w = weights or {c: 1.0 for c in cells}
    if batch_p is None:
        batch_p = [C.family_cv_p(units, family="C03", carried=list(cells), weights=w, m_j=1,
                                 alpha_j=C.BatchPRDS(window).level(j, 1), mean_block=2.0, seed_key="seed")]
    return C.l3_read(units, family="C03", carried=list(cells), weights=w, batch=C.BatchPRDS(window), j=j,
                     batch_p=batch_p, corroboration=corr, mean_block=2.0, seed_key="seed",
                     confirm_seen=confirm_seen, holds_multi_day=holds_multi_day, e9=e9)


def test_l3_read_resamples_follow_alpha(monkeypatch):
    """Quant condition: at j = 15, m = 3 the resamples l3_read uses are >= ceil(20 * 3 / (0.1 * gamma(15)))
    (386,889 at the exact alpha_15); the number comes from gamma(15), never hard-coded."""
    used: list[int] = []

    def fake_counts(blocks, *, mean_block, resamples, seed_key):
        used.append(resamples)
        yield np.ones((4, blocks.n))                                     # four copies of the sample itself
    monkeypatch.setattr(C, "_day_count_chunks", fake_counts)
    u = _years(_units(mu=0.3, day0=CONFIRM_DAY0), (2021, 2022, 2023))
    alpha = C.BatchPRDS("confirm").level(15, 3)
    own_p = C.family_cv_p(u, family="C03", carried=["a"], weights={"a": 1.0}, m_j=3, alpha_j=alpha, mean_block=2.0,
                          seed_key="s")
    r = C.l3_read(u, family="C03", carried=["a"], weights={"a": 1.0}, batch=C.BatchPRDS("confirm"), j=15,
                  batch_p=[own_p, 0.6, 0.7], corroboration=None, mean_block=2.0, seed_key="s", confirm_seen=(),
                  holds_multi_day=False, e9=None)
    bound = math.ceil(20 * 3 / (0.1 * C.gamma(15)))
    assert used and min(used) >= bound
    assert r.batch_j == 15 and r.alpha_j == pytest.approx(0.1 * C.gamma(15)) and r.window == "confirm"


def test_l3_read_refuses_a_batch_that_does_not_hold_the_familys_own_p(fast):
    u = _years(_units(mu=0.3), (2008, 2009))
    with pytest.raises(ValueError, match="batch_p"):
        _read(u, None, batch_p=[0.9])


def test_l3_read_confirms_a_strong_family_on_every_leg(fast):
    u = _years(_units(mu=0.35, n_days=400, seed=11), (2006, 2007, 2008, 2009))
    corr = _years(_corr(mu=0.35, n_days=400, seed=12), (2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025))
    r = _read(u, corr)
    assert r.p_cv_net < 0.05 and r.p_excess <= 0.10 and r.lb80_net_gate > 0
    assert r.years == {"years_pos_ge3": True, "chop_year_pos": True, "no_2y_t_le_m2": True}
    assert r.corroboration_p is not None and r.corroboration_p <= 0.10
    assert r.e9 is None and r.rejected is True and r.cells_forwarded == ("a",)


def test_l3_read_does_not_reject_without_the_2018_25_corroboration(fast):
    u = _years(_units(mu=0.35, n_days=400, seed=11), (2006, 2007, 2008, 2009))
    r = _read(u, None)
    assert r.corroboration_p is None and r.rejected is False and r.cells_forwarded == ()


def test_l3_read_does_not_reject_a_null_family(fast):
    u = _years(_units(mu=0.0, n_days=300, seed=21), (2008, 2009))
    corr = _years(_corr(mu=0.0, n_days=300, seed=22), (2018, 2019, 2020, 2021, 2022))
    r = _read(u, corr)
    assert r.rejected is False and r.cells_forwarded == ()


def test_gate_net_uses_deciding_cost_once(fast):
    """Prereg: the L3 gate is the 'bootstrap 80 % LB of net at 1.5x deciding cost'. units.net_r already pays the
    deciding cost (costs.deciding_cost, 1.0x) once, so the gate subtracts only 0.5x more: gross 2.5 dc -> gate net
    2.5 dc - 1.5 dc = +1.0 dc > 0. Charging 1.5x on top of net (2.5 dc - 2.5 dc = 0) would fail it."""
    from goldbot.research.catalogue.costs import deciding_cost, default_model
    dc = deciding_cost("XAUUSD", "1h", side=1, price=1500.0, atr_decision=4.0, stop_atr=0.5, t_fill_ns=0,
                       t_exit_ns=3_600_000_000_000, stop_exit_share=0.3, stop_entry_share=0.0, window="test_old",
                       costs=default_model()).r
    u = _years(_units(mu=0.0, n_days=400, seed=31, cost=dc), (2006, 2007, 2008, 2009))
    gross = 2.5 * dc
    u = u.assign(net_r=gross - dc, twin_mean_net_r=0.0, excess_r=gross - dc)       # no noise: the gate is exact
    lb = C.boot_lower_bound(u, lambda d: S.control_variate_net_at(d, {"a": 1.0}, cost_mult=C.GATE_COST_MULT),
                            level=0.80, mean_block=2.0, resamples=200, seed_key="x")
    assert lb == pytest.approx(gross - 1.5 * dc)
    corr = _years(u.assign(day=u["day"] - TEST_OLD_DAY0 + CONFIRM_DAY0,
                           decision_ns=(u["day"] - TEST_OLD_DAY0 + CONFIRM_DAY0) * DAY), (2018, 2019, 2020, 2021, 2022))
    r = _read(u, corr)
    assert r.lb80_net_gate == pytest.approx(gross - 1.5 * dc) and r.lb80_net_gate > 0


def test_holm_forwards_only_the_cells_that_survive_at_the_familys_level(fast):
    strong = _units(mu=0.45, n_days=400, seed=41, cells=("a",))
    weak = _units(mu=-0.10, n_days=400, seed=42, cells=("b",)).assign(day=lambda d: d["day"] + 0)
    u = _years(pd.concat([strong, weak], ignore_index=True).sort_values("day", kind="stable"),
               (2006, 2007, 2008, 2009))
    corr = _years(_corr(mu=0.45, n_days=400, seed=43, cells=("a", "b")), (2018, 2019, 2020, 2021, 2022))
    w = {"a": 1.0, "b": 1.0}
    r = _read(u, corr, cells=("a", "b"), weights=w)
    assert r.rejected is True and r.cells_forwarded == ("a",)


def _strong(cells=("a",), seed=11) -> tuple[pd.DataFrame, pd.DataFrame]:
    u = _years(_units(mu=0.35, n_days=400, seed=seed, cells=cells), (2008, 2009))
    corr = _years(_corr(mu=0.35, n_days=400, seed=seed + 1, cells=cells), (2018, 2019, 2020, 2021, 2022, 2023))
    return u, corr


# ------------------------------------------------------------------ amendment 10.2 (A16): E9 in the L3 read
def test_multi_day_hold_needs_e9_true(fast):
    u, corr = _strong()
    u, corr = u.assign(tf="4h"), corr.assign(tf="4h")
    assert _read(u, corr, holds_multi_day=True, e9=None).rejected is False
    assert _read(u, corr, holds_multi_day=True, e9=False).rejected is False
    r = _read(u, corr, holds_multi_day=True, e9=True)
    assert r.rejected is True and r.e9 is True


def test_daily_cell_with_holds_multi_day_false_raises(fast):
    u, corr = _strong()
    with pytest.raises(ValueError, match="holds_multi_day"):
        _read(u.assign(tf="1d"), corr.assign(tf="1d"), holds_multi_day=False, e9=True)


def test_no_time_exit_counts_as_multi_day():
    """RD countersignature condition 1: a carried cell with no registered time exit holds >= 24 h."""
    assert C.card_holds_multi_day({"a": ("1h", 12)}) is False                 # 12 h
    assert C.card_holds_multi_day({"a": ("1h", 24)}) is True                  # 24 h
    assert C.card_holds_multi_day({"a": ("4h", 6), "b": ("15m", None)}) is True
    assert C.card_holds_multi_day({"a": ("1d", 1)}) is True and C.card_holds_multi_day({"a": ("1w", 1)}) is True
    assert C.card_holds_multi_day({"a": ("30m", 47)}) is False and C.card_holds_multi_day({"a": ("30m", 48)}) is True


# ------------------------------------------------------------------ amendment 10.1 (A15): CONFIRM-SEEN in the read
def test_all_seen_family_rejects_on_test_old_with_the_seen_and(fast):
    u, corr = _strong()
    r = _read(u, corr, confirm_seen={"a"})
    assert r.corroboration_p is None and r.corroboration_seen_p is not None and r.corroboration_seen_p <= 0.10
    assert r.rejected is True and r.cells_forwarded == ("a",)


def test_a_seen_cell_failing_2018_25_blocks_the_family(fast):
    u, corr = _strong(cells=("a", "b"))
    bad = corr["cell_id"] == "b"
    corr = corr.assign(net_r=np.where(bad, corr["net_r"] - 1.0, corr["net_r"]),
                       excess_r=np.where(bad, corr["excess_r"] - 1.0, corr["excess_r"]))
    r = _read(u, corr, cells=("a", "b"), confirm_seen={"b"})
    assert r.corroboration_p is not None and r.corroboration_p <= 0.10
    assert r.corroboration_seen_p is not None and r.corroboration_seen_p > 0.10
    assert r.rejected is False


def test_fresh_corroboration_p_ignores_seen_cells(fast):
    u, corr = _strong(cells=("a", "b"))
    base = _read(u, corr, cells=("a", "b"), confirm_seen={"b"})
    bad = corr["cell_id"] == "b"
    moved = corr.assign(net_r=np.where(bad, corr["net_r"] - 3.0, corr["net_r"]),
                        excess_r=np.where(bad, corr["excess_r"] - 3.0, corr["excess_r"]))
    assert _read(u, moved, cells=("a", "b"), confirm_seen={"b"}).corroboration_p == base.corroboration_p


def test_a_carried_cell_with_no_2018_25_units_blocks_the_family(fast):
    """10.1(f): F non-empty with no F units in the corroboration frame: not rejected."""
    u, corr = _strong(cells=("a", "b"))
    r = _read(u, corr[corr["cell_id"] == "b"], cells=("a", "b"), confirm_seen={"b"})
    assert r.corroboration_p is None and r.rejected is False


def test_confirm_window_refuses_seen_cells(fast):
    u = _years(_units(mu=0.3, day0=CONFIRM_DAY0), (2018, 2019))
    with pytest.raises(ValueError, match="CONFIRM-SEEN"):
        _read(u, None, window="confirm", j=15, batch_p=[0.5], confirm_seen={"a"})


def test_confirm_seen_has_no_default():
    u, corr = _strong()
    with pytest.raises(TypeError):
        C.l3_read(u, family="C03", carried=["a"], weights={"a": 1.0}, batch=C.BatchPRDS("test_old"),  # type: ignore[call-arg]
                  j=1, batch_p=[0.5], corroboration=corr, mean_block=2.0, seed_key="s", holds_multi_day=False,
                  e9=None)


# ------------------------------------------------------------------ amendment 10.3 (A17): the L3 point and interval
def test_l3_result_carries_the_cv_net_at_1x_and_its_80_interval(fast):
    u, corr = _strong()
    r = _read(u, corr)
    assert r.cv_net == pytest.approx(S.control_variate_net(u, {"a": 1.0}))
    t = C._fast_stats(u, {"a": 1.0}, mean_block=2.0, resamples=C.confirm_resamples(1, 0.05), seed_key="seed|ci80")
    q10, q90 = np.quantile(t["cv"], [0.10, 0.90])
    assert (r.cv_net_lo80, r.cv_net_hi80) == pytest.approx((2 * r.cv_net - q90, 2 * r.cv_net - q10))
    assert r.cv_net_lo80 < r.cv_net < r.cv_net_hi80


# ------------------------------------------------------------------ code review: speed, windows (quant M5)
def test_fast_bootstrap_equals_the_generic_one_on_a_fixed_seed():
    """Code review: per-session-day sufficient statistics, one day draw for the three L3 statistics; same numbers as
    the generic DataFrame bootstrap on the same seed."""
    u = _units(mu=0.2, n_days=150, per_day=3, seed=8, cells=("a", "b"))
    u.loc[u.index[::17], "excess_r"] = np.nan                            # instances without a twin drop out alike
    w = {"a": 2.0, "b": 1.0}
    fast_ = C._fast_stats(u, w, mean_block=2.5, resamples=300, seed_key="eq")
    for key, stat in (("cv", lambda d: S.control_variate_net(d, w)),
                      ("excess", lambda d: S.weighted_mean_excess(d, w)),
                      ("gate", lambda d: S.control_variate_net_at(d, w, cost_mult=C.GATE_COST_MULT))):
        slow = C.stationary_block_bootstrap(u, stat, mean_block=2.5, resamples=300, seed_key="eq")
        np.testing.assert_allclose(fast_[key], slow, rtol=1e-10, atol=1e-12)


def test_l3_read_refuses_holdout_and_out_of_window_units(fast):
    """Quant M5: nothing at or after 2025-10-01; TEST-OLD intraday 2008-01..2009-12, 1d 2006-02..2009-12; the
    corroboration 2018-01..2025-09."""
    u, corr = _strong()
    hold = corr.assign(decision_ns=corr["decision_ns"] + (20_362 - CONFIRM_DAY0) * DAY)      # 2025-10 onwards
    with pytest.raises(ValueError, match="holdout"):
        _read(u, hold)
    early = u.assign(decision_ns=u["decision_ns"] - 400 * DAY)                                 # 2006-12: intraday
    with pytest.raises(ValueError, match="TEST-OLD"):
        _read(early, corr)
    _read(early.assign(tf="1d"), corr.assign(tf="1d"), holds_multi_day=True, e9=True)          # 1d from 2006-02
    with pytest.raises(ValueError, match="2018"):
        _read(u, corr.assign(decision_ns=corr["decision_ns"] - 300 * DAY))                     # 2017


def test_l3_read_refuses_overlapping_units_and_corroboration(fast):
    """Code review LOW: the read window and the corroboration window are disjoint."""
    u = _years(_units(mu=0.3, day0=CONFIRM_DAY0), (2018, 2019))
    with pytest.raises(ValueError, match="disjoint"):
        _read(u, _corr(mu=0.3, seed=3), window="confirm", j=15, batch_p=None)
