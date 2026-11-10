"""EVAL-2 L1 probe (prereg 2026-11-09 sections 1 and 4.4; method v6 section 2, L1).

"Symmetric first passage at +/-1 ATR (decision-TF ATR), from the fill bar's close, capped at the native horizon;
side-0 OCO bar = loss." "Statistic: excess of the probe over 20 twins, partially pooled per family. Family retire (NO
SIGNAL) if the pooled 90 % UB < the cheapest feasible cell's deciding cost in ATR and power >= 0.8 at that cost.\""""
from __future__ import annotations

import numpy as np
import pytest

from goldbot.research.eval2.probe import ProbeResult, family_no_signal, first_passage, probe_cell
from tests.test_catalogue_random_walk import flat_bars

# flat bars: high 100.5, low 99, close 100: true range 1.5, so the decision-TF ATR (Wilder 14) is 1.5 and the
# +/-1 ATR barriers from a close of 100 are 101.5 and 98.5


def _bars():
    return flat_bars(80, tf="1h")


def test_first_passage_scores_the_barrier_the_side_reaches_first():
    b = _bars()
    b.h[32] = 102.0                                  # up barrier at bar 32
    b.l[35] = 98.0                                   # down barrier later
    out = first_passage(b, fill_bars=np.array([30, 30]), sides=np.array([1, -1]), horizon_bars=10)
    assert out.tolist() == [1.0, -1.0]


def test_first_passage_starts_after_the_fill_bars_close():
    b = _bars()
    b.h[30] = 105.0                                  # the fill bar itself: before the probe starts
    b.l[33] = 98.0
    assert first_passage(b, fill_bars=np.array([30]), sides=np.array([1]), horizon_bars=10).tolist() == [-1.0]


def test_a_bar_that_touches_both_barriers_and_a_side_0_oco_instance_score_as_a_loss():
    b = _bars()
    b.h[31], b.l[31] = 102.0, 98.0
    out = first_passage(b, fill_bars=np.array([30, 30, 40]), sides=np.array([1, -1, 0]), horizon_bars=5)
    assert out.tolist() == [-1.0, -1.0, -1.0]


def test_first_passage_is_capped_at_the_native_horizon():
    b = _bars()
    b.h[45] = 102.0                                  # beyond a 10-bar horizon
    out = first_passage(b, fill_bars=np.array([30, 30]), sides=np.array([1, 1]), horizon_bars=10)
    assert out[0] == 0.0
    assert first_passage(b, fill_bars=np.array([30]), sides=np.array([1]), horizon_bars=15).tolist() == [1.0]


def test_first_passage_barrier_scales_with_atr_multiple():
    b = _bars()
    b.h[32] = 102.0                                  # +2.0 from the close: inside 1 ATR x 1.5? no: 1.5 < 2.0
    assert first_passage(b, fill_bars=np.array([30]), sides=np.array([1]), horizon_bars=5,
                         barrier_atr=1.5).tolist() == [0.0]          # 1.5 ATR = 2.25 > 2.0


def test_first_passage_is_nan_without_atr_or_bars_after_the_fill():
    b = _bars()
    out = first_passage(b, fill_bars=np.array([3, 79]), sides=np.array([1, 1]), horizon_bars=5)
    assert np.isnan(out).all()


def test_probe_cell_reports_mean_excess_its_day_clustered_se_and_the_one_sided_90_upper_bound():
    rng = np.random.default_rng(3)
    n = 400
    sig = np.where(rng.random(n) < 0.55, 1.0, -1.0)
    twins = np.where(rng.random((n, 20)) < 0.5, 1.0, -1.0)
    day = np.repeat(np.arange(200), 2)
    r = probe_cell(sig, twins, day, family="F", cell_id="F|XAUUSD|1h")
    ex = sig - twins.mean(axis=1)
    assert isinstance(r, ProbeResult) and r.n == n and r.family == "F"
    assert r.excess_atr == pytest.approx(ex.mean())
    from goldbot.research.catalogue.stats import cluster_se
    assert r.se_atr == pytest.approx(cluster_se(ex, day))
    assert r.ub90_atr == pytest.approx(r.excess_atr + 1.2815515655446004 * r.se_atr)


def test_probe_cell_leaves_out_instances_without_a_probe_or_a_twin():
    sig = np.array([1.0, np.nan, -1.0, 1.0])
    twins = np.array([[1.0, -1.0], [1.0, 1.0], [np.nan, np.nan], [-1.0, -1.0]])
    r = probe_cell(sig, twins, np.array([1, 2, 3, 4]), family="F", cell_id="c")
    assert r.n == 2 and r.excess_atr == pytest.approx((1.0 + 2.0) / 2)


def _p(ex: float, se: float, cell: str = "c") -> ProbeResult:
    return ProbeResult("F", cell, 1000, ex, se, ex + 1.2815515655446004 * se)


def test_a_family_retires_as_no_signal_only_with_a_tight_bound_below_the_cheapest_cost():
    # pooled UB well below a 0.10 ATR cost, and SE 0.02: power at 0.10 is ~1
    assert family_no_signal([_p(0.0, 0.03, "a"), _p(0.01, 0.03, "b")], cheapest_cost_atr=0.10) is True
    # same estimates, cost below the UB: no retire
    assert family_no_signal([_p(0.0, 0.03, "a"), _p(0.01, 0.03, "b")], cheapest_cost_atr=0.03) is False


def test_an_underpowered_family_is_never_retired_as_no_signal():
    """UB < cost but power < 0.8 at that cost: a wide SE cannot retire (the 'and power >= 0.8' leg)."""
    p = [_p(-0.30, 0.10, "a")]                    # UB -0.17 < 0.10, but power at 0.10 with SE 0.10 is ~0.39
    assert family_no_signal(p, cheapest_cost_atr=0.10) is False
    assert family_no_signal([], cheapest_cost_atr=0.10) is False


def test_disagreeing_cells_widen_the_pooled_bound_so_heterogeneity_cannot_retire_a_family():
    """Partial pooling (random effects): two precise cells far apart pool to a wide bound, not a precise zero."""
    split = [_p(-0.30, 0.01, "a"), _p(0.30, 0.01, "b")]
    assert family_no_signal(split, cheapest_cost_atr=0.10) is False
    agree = [_p(0.0, 0.01, "a"), _p(0.0, 0.01, "b")]
    assert family_no_signal(agree, cheapest_cost_atr=0.10) is True


# ------------------------------------------------------------------ review round 1 (quant M2, low (iii))
def test_pooled_ub_covers_at_least_90_percent_with_three_heterogeneous_cells():
    """Quant M2: DerSimonian-Laird's 90 % bound covered 86.8 % at k = 3, tau = se; HKSJ (variance floored at DL's,
    t with k - 1 df) keeps at least 90 %."""
    from goldbot.research.eval2.probe import pooled_bound
    rng = np.random.default_rng(1109)
    se = np.array([0.03, 0.03, 0.03])
    hits = 0
    n = 20_000
    for _ in range(n):
        est = rng.normal(0.0, 0.03, 3) + rng.normal(0.0, se)            # true pooled mean 0, tau = se
        _, ub = pooled_bound([_p(float(e), float(s), f"c{j}") for j, (e, s) in enumerate(zip(est, se))])
        hits += ub >= 0.0
    assert hits / n >= 0.895


def test_a_single_cell_family_uses_its_own_normal_bound():
    from goldbot.research.eval2.probe import pooled_bound
    mean, ub = pooled_bound([_p(0.02, 0.01)])
    assert mean == pytest.approx(0.02) and ub == pytest.approx(0.02 + 1.2815515655446004 * 0.01)


def test_first_passage_is_unchanged_on_bars_truncated_after_the_horizon():
    """Quant low (iii): the probe for fill f reads bars f+1 .. f+horizon only, and ATR at f is unchanged when every
    bar after f is removed."""
    from goldbot.research.catalogue.core import atr
    from tests.test_catalogue_random_walk import rw_cell
    b = rw_cell("XAUUSD", "1h", 3, 600)[0]
    fills = np.arange(40, 560, 7)
    full = first_passage(b, fill_bars=fills, sides=np.where(fills % 2 == 0, 1, -1), horizon_bars=12)
    for k, f in enumerate(fills.tolist()):
        cut = b.truncate(f + 12 + 1)
        assert first_passage(cut, fill_bars=np.array([f]), sides=np.array([1 if f % 2 == 0 else -1]),
                             horizon_bars=12)[0] == full[k]
        assert atr(b.truncate(f + 1), 14)[f] == atr(b, 14)[f]
