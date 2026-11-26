"""sr-v3 Stage 0 (card 2.3, quant Q1/Q2, rev 4 A-C): the label-blind Riley ladder, the orders and the tie rule.
Card test 18 (i)-(iii), (v), (vi) here; (iv) (no label read) in tests/research/test_sr_v3_train.py."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from goldbot.research import self_retrain as SR
from goldbot.research import sr_v3 as V

ROOT = Path(__file__).resolve().parents[2]
RHO_FILE = ROOT / "docs" / "research" / "rho-measurement-2026-11.json"


def _rev3_counts(tf_minutes_hold: float, days: int) -> V.InstCounts:
    """Card 2.2's method at a hold: S_d = 1,380 / hold minutes a day (holds of a day or more: 1 a day), m̄ = max(S_d, 1),
    the Kish efficiency at KISH_LADDER."""
    s_d = 1.0 if tf_minutes_hold >= 1_440 else 1_380 / tf_minutes_hold
    return V.InstCounts(instrument="XAUUSD", rows=days, days=days, sum_u=s_d * days, kish_eff=V.KISH_LADDER,
                        m_bar=max(1.0, s_d))


REV3 = {  # unit: (hold minutes, fold 2010-11 budget, fold 2010-13 budget); None = "<= 0" in the card's table
    "1m": (60, 2.4, 5.3), "5m": (240, 1.6, 3.9), "15m": (480, 0.9, 2.8), "30m": (960, None, 1.5),
    "1h": (1_440, None, 0.9),
}


@pytest.mark.parametrize("unit", list(REV3))
def test_rev3_gold_ladder_is_reproduced_with_every_rho_x_one(unit: str) -> None:
    hold, a, b = REV3[unit]
    for days, want in ((518, a), (1_036, b)):
        lad = V.ladder([_rev3_counts(hold, days)], V.level_order(), V.rev3_inputs(), body="gold")
        if want is None:
            assert lad.budget_equal_n <= 0
        else:
            assert lad.budget_equal_n == pytest.approx(want, abs=0.05)
        assert lad.prefix == max(0, int(np.floor(lad.budget_equal_n + 1e-9)))
        assert lad.level == "below L3"


def test_iid_features_give_column_n_equal_to_kish_n_eff() -> None:
    c = V.InstCounts(instrument="XAUUSD", rows=10_000, days=500, sum_u=12_000.0, kish_eff=0.7, m_bar=24.0)
    rho = V.rev3_inputs().model_copy(update={"rho_x_day": dict.fromkeys(V.P30, 0.0), "defaults": ()})
    lad = V.ladder([c], V.level_order(), rho, body="gold")
    assert lad.columns, "a large fold affords a column"
    assert all(ch.n == pytest.approx(c.kish_n) for ch in lad.columns)
    assert lad.fixed[0].n == pytest.approx(c.kish_n / (1 + 23 * V.RHO_DAY_PRIOR))


def test_budget_is_a_prefix_of_the_level_order_and_never_skips_a_column() -> None:
    c = V.InstCounts(instrument="XAUUSD", rows=10_000, days=2_000, sum_u=40_000.0, kish_eff=0.62, m_bar=20.0)
    order = V.level_order()
    # a cheap late column must not enter ahead of an expensive early one
    rho = V.rev3_inputs().model_copy(update={"rho_x_day": {**dict.fromkeys(V.P30, 0.2), "ret_16": 1.0},
                                             "defaults": ()})
    lad = V.ladder([c], order, rho, body="gold")
    assert [ch.name for ch in lad.columns] == list(order[:lad.prefix])
    assert order[:7] == V.CORE_12[:7] and V.CORE_12 == V.P30[:12]


def test_charges_sum_to_at_most_one() -> None:
    c = V.InstCounts(instrument="XAUUSD", rows=10_000, days=2_000, sum_u=60_000.0, kish_eff=0.62, m_bar=20.0)
    lad = V.ladder([c], V.level_order(), V.rev3_inputs().model_copy(update={"rho_x_day": dict.fromkeys(V.P30, 0.1)}),
                   body="gold")
    assert lad.total_fixed + sum(ch.charge for ch in lad.columns) <= 1.0 + 1e-12


def test_pooled_body_charges_instrument_levels_at_lambda_a() -> None:
    cs = [V.InstCounts(instrument=i, rows=1_000, days=500, sum_u=5_000.0, kish_eff=0.62, m_bar=10.0)
          for i in ("XAUUSD", "EURUSD", "USDJPY")]
    rho = V.rev3_inputs().model_copy(update={"lambda_a": 820.0})
    lad = V.ladder(cs, V.level_order(), rho, body="pooled")
    levels = [f for f in lad.fixed if f.name.startswith("level:")]
    assert len(levels) == 3 and all(f.lam == 820.0 for f in levels)
    n_i = 5_000 * 0.62 / (1 + 9 * V.RHO_DAY_PRIOR)
    assert lad.fixed[0].n == pytest.approx(3 * n_i / (1 + 2 * V.RHO_INST))
    assert levels[0].charge == pytest.approx(430 / (n_i + 820))


@pytest.mark.parametrize(("g", "p", "want"), [(0, 0, "gold"), (6, 6, "gold"), (5, 6, "pooled"), (12, 7, "gold"),
                                              (12, 13, "gold"), (11, 12, "pooled")])
def test_tie_rule_takes_the_higher_level_and_gold_on_a_tie(g: int, p: int, want: str) -> None:
    def lad(body: str, prefix: int) -> V.Ladder:
        return V.Ladder(body=body, order=(), fixed=(), columns=(), prefix=prefix, level=V.level_name(prefix),
                        budget_equal_n=float(prefix), total_fixed=0.0)
    assert V.tie_rule(lad("gold", g), lad("pooled", p)) == want
    assert V.tie_rule(lad("gold", g), None) == "gold"


def _big(n: float) -> list[V.InstCounts]:
    return [V.InstCounts(instrument="XAUUSD", rows=10_000, days=2_000, sum_u=n, kish_eff=1.0, m_bar=1.0)]


def test_block_admission_never_moves_the_level_and_blocks_are_served_whole() -> None:
    rho = V.rev3_inputs()
    counts = _big(4_000.0)       # n 4,000: each column costs 430/4,540, 9 columns at most after the intercept
    lad = V.ladder(counts, V.level_order(), rho, body="gold")
    assert lad.level == "L3"
    x1, x2 = ("X1", ("x1a", "x1b")), ("X2", ("x2a", "x2b", "x2c", "x2d"))
    none = V.served_body(counts, rho, body="gold")
    one = V.served_body(counts, rho, body="gold", admitted=[x1])
    both = V.served_body(counts, rho, body="gold", admitted=[x2, x1])
    assert none[:6] == V.CORE_6 and one[:8] == (*V.CORE_6, "x1a", "x1b")
    # X2 (4 columns) does not fit after X1: it ends the block section, whole, and the core continues as a prefix
    assert "x2a" not in both and both[:8] == (*V.CORE_6, "x1a", "x1b")
    assert len(one) == len(none) == lad.prefix
    assert V.ladder(counts, V.level_order(), rho, body="gold").level == lad.level   # no input of the level changed


def test_a_block_that_does_not_fit_ends_the_block_section() -> None:
    rho = V.rev3_inputs()
    counts = _big(4_000.0)
    big = ("X1", tuple(f"x1_{i}" for i in range(6)))
    small = ("X3", ("x3a",))
    served = V.served_body(counts, rho, body="gold", admitted=[small, big])
    assert not any(c.startswith("x1_") for c in served)
    assert "x3a" not in served, "a later block never skips ahead of one that did not fit"
    assert served[:7] == V.CORE_12[:7]


def test_p30_is_sr_v2_inputs_minus_the_cost_and_dead_d1_columns() -> None:
    rest = [c for c in SR.INPUTS if c not in V.DROPPED_COST and c not in V.DROPPED_D1]
    assert len(V.P30) == 30 and set(V.P30) == set(rest)
    assert list(V.P30_REST) == [c for c in SR.FEATURES if c in V.P30_REST]
    assert V.CORE_6 == ("side", "tsmom_score", "dist_vwap48_atr", "rv_ratio", "er_30", "bb_z_20")
    assert "atr_ratio_14_100" in V.P30_REST and V.P30_REST[0] == "atr_ratio_14_100", "quant ruling 2026-11-26"


def test_constants_are_pinned() -> None:
    assert (V.LAMBDA_ROWS, V.RILEY_PER_PARAM, V.CLIP_R, V.KISH_LADDER, V.RHO_INST) == (540, 430, 10, 0.62, 0.5)
    assert (V.FIRST_OOF, V.FIRST_OOF_B, V.MIN_LEARN_CANDIDATES, V.MIN_PER_FOLD) == (2012, 2014, 1_500, 60)
    assert dict(V.LEVELS) == {"L1": 40, "L2": 12, "L3": 6}
    assert V.UNITS["1d+1w"] == ("1d", "1w") and V.JUDGED_HEAD == "T2"
    assert {tf: V.hold_bucket(tf) for tf in ("1m", "5m", "15m", "4h")} == {"1m": "le60m", "5m": "1h-4h",
                                                                            "15m": "gt4h", "4h": "gt4h"}


def test_rho_inputs_read_the_committed_d12_file() -> None:
    g = V.rho_inputs(RHO_FILE, body="gold", members=["XAUUSD"], tf="1m")
    assert g.rho_u_day == pytest.approx(0.14771) and g.rho_class == "gold" and g.source_sha256
    assert 0 <= g.x_day("adx14") <= 1 and "side" in g.defaults and g.x_day("side") == 1.0
    p = V.rho_inputs(RHO_FILE, body="pooled", members=[*V.POOLED_MEMBERS], tf="15m")
    assert p.rho_class == "pooled" and p.lambda_a == pytest.approx(1.35 / 0.06293 ** 2)
    assert p.rho_u_day == pytest.approx(V.RHO_DAY_PRIOR), "holds over 60 min keep the 0.25 floor"
    two = V.rho_inputs(RHO_FILE, body="pooled", members=["XAUUSD", "EURUSD"], tf="1m")
    assert two.rho_class == "gold+fx10" and two.lambda_a == pytest.approx(1.35 / 0.04057 ** 2)
    d = V.rho_inputs(RHO_FILE, body="gold", members=["XAUUSD"], tf="1d")
    assert set(d.defaults) == set(V.P30), "no tf-1d feature ICC is measured: the rev-3 floor"


def test_a_unit_below_l3_tries_remedy_b_and_otherwise_serves_intercept_only() -> None:
    rho = {"gold": V.rev3_inputs(), "pooled": V.rev3_inputs()}
    av = {"gold": V.P30, "pooled": V.P30}
    small = {"gold": _big(1_000.0), "pooled": []}
    larger = {"gold": _big(4_000.0), "pooled": []}
    u = V.decide_unit("15m", ["15m"], counts_a=small, counts_b=larger, rho=rho, available=av)
    assert u.remedy_b and u.first_oof == V.FIRST_OOF_B and u.level == "L3" and u.judged and u.served[:6] == V.CORE_6
    u2 = V.decide_unit("15m", ["15m"], counts_a=small, counts_b=small, rho=rho, available=av)
    assert not u2.judged and u2.served == () and u2.first_oof == V.FIRST_OOF and u2.body == "gold"
    u3 = V.decide_unit("2h", ["2h"], counts_a={"gold": [], "pooled": []}, counts_b=None, rho=rho, available=av)
    assert u3.status == "not_started_data"


def test_x6_price_action_is_the_last_block_of_the_served_order() -> None:
    assert V.BLOCK_ORDER[-1] == "X6" and len(V.X6_COLUMNS) == 8
    rho, counts = V.rev3_inputs(), _big(8_000.0)       # room for core-6, X3 (1) and X6 (8) at most
    served = V.served_body(counts, rho, body="gold", admitted=[("X6", V.X6_COLUMNS), ("X3", ("x3a",))])
    assert served[:15] == (*V.CORE_6, "x3a", *V.X6_COLUMNS), "X6 enters after every earlier admitted block, whole"
    assert V.served_body(counts, rho, body="gold") == V.served_body(counts, rho, body="gold", admitted=())
