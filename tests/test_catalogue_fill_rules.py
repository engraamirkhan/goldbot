"""Catalogue harness fill rules after the quant re-review of D1-D4 (2026-11-07, eval1_signoff_1107):

C7  "The floor is decided once, at the signal bar, on the nominal order price (market orders: the decision close).
    A filled order is never dropped. A fill at or beyond its stop exits at the fill bar's price at once, as a loss of
    at least -1 R." R is measured on that nominal risk (the risk the position is sized on when the order is placed).
    Item (4): "When no twin bar exists, keep the real trade, set its control to NaN, and leave it out of the excess
    statistic only."

Flat 1d gold bars (opening 21:00 UTC in January: New York's session on EST): ATR20 1.5, round trip the newyork
cost of record cell $0.263 (backlog 146), risk floor max(0.375, 0.789) = 0.789."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from goldbot.research.catalogue import costs as C
from goldbot.research.catalogue import sim as SIM
from goldbot.research.catalogue import stats as S
from goldbot.research.catalogue.core import EB
from goldbot.research.catalogue.data import Bars
from tests.test_catalogue_random_walk import flat_bars

COL = {k: j for j, k in enumerate(SIM.TRADE_COLS)}
NY_RT = 0.263                    # cor-2026-10-31.2 newyork, the worse broker (Vantage): the flat bars' session
OVER = SIM.GOLD_OVERSHOOT_USD


def _bar(b: Bars, j: int, o: float, h: float, lo: float, c: float) -> None:
    b.o[j], b.h[j], b.l[j], b.c[j] = o, h, lo, c


def rt(r: np.ndarray) -> float:
    """Gold's round trip at the trade's fill instant (costs.CostModel.round_trip, backlog 146)."""
    return C.default_model().round_trip("XAUUSD", float(r[COL["fill"]]), float("nan"), int(r[COL["t_fill"]]))


def _one(b: Bars, eb: EB, *, control: str = "random_side", counts: dict[str, int] | None = None) -> np.ndarray:
    tr = SIM.simulate(b, eb.build(), control=control, seed_key="x", counts=counts)
    assert len(tr) == 1, tr
    return tr[0]


# ------------------------------------------------------------------ C7
def test_a_limit_fill_that_gaps_toward_its_stop_is_traded_and_measured_on_the_order_risk() -> None:
    """Order risk 1.0 clears the floor at the signal bar; the open at 99.3 leaves 0.3 (< floor) at the fill. The
    frozen fix dropped it; the trade is kept and R uses the order's risk: (100 - 99.3) / 1.0."""
    b = flat_bars()
    _bar(b, 41, 99.3, 100.5, 99.2, 100.0)
    eb = EB()
    eb.add(40, 1, kind=2, px=100.0, valid=1, stop=99.0, hold=1)
    counts: dict[str, int] = {}
    r = _one(b, eb, counts=counts)
    assert r[COL["fill"]] == pytest.approx(99.3)
    assert r[COL["risk"]] == pytest.approx(1.0)
    assert r[COL["gross"]] == pytest.approx(0.7)
    assert counts.get("risk_floor", 0) == 0


def test_a_limit_fill_through_its_stop_exits_at_once_as_a_full_loss() -> None:
    b = flat_bars()
    _bar(b, 41, 98.5, 100.5, 98.0, 100.0)
    eb = EB()
    eb.add(40, 1, kind=2, px=100.0, valid=1, stop=99.0, hold=3)
    r = _one(b, eb)
    assert r[COL["fill"]] == pytest.approx(98.5)
    assert r[COL["gross"]] == pytest.approx(-1.0)
    assert r[COL["kind"]] == 1 and r[COL["x"]] == 41
    assert r[COL["cost"]] == pytest.approx((rt(r) + OVER) / 1.0)


def test_a_market_order_whose_open_gaps_through_an_absolute_stop_is_a_full_loss_not_dropped() -> None:
    """Nominal risk on the decision close: 100.0 - 99.1 = 0.9 (clears the floor); the open at 99.0 is through it."""
    b = flat_bars()
    _bar(b, 41, 99.0, 100.5, 98.9, 100.0)
    eb = EB()
    eb.add(40, 1, stop=99.1, hold=2)
    r = _one(b, eb)
    assert rt(r) == pytest.approx(NY_RT)
    assert r[COL["risk"]] == pytest.approx(0.9)
    assert r[COL["gross"]] == pytest.approx(-1.0)


def test_the_risk_floor_is_judged_on_the_order_price_not_on_the_fill() -> None:
    """Order risk 0.45 (< 0.789) is not placed even though a gap would have filled it with a risk of 1.0."""
    b = flat_bars()
    _bar(b, 41, 99.0, 100.5, 98.9, 100.0)
    eb = EB()
    eb.add(40, 1, kind=2, px=100.0, valid=1, stop=99.55, hold=1)          # order risk 0.45
    counts: dict[str, int] = {}
    assert len(SIM.simulate(b, eb.build(), control="random_side", seed_key="x", counts=counts)) == 0
    assert counts == {"risk_floor": 1}


def test_a_stop_entry_that_gaps_past_its_price_loses_more_than_1r_at_the_stop() -> None:
    """Sized on 1.0 at px 100.2, filled at the 100.4 open, stopped at 99.2: (99.2 - 100.4) / 1.0 = -1.2 R."""
    b = flat_bars()
    _bar(b, 41, 100.4, 100.6, 99.0, 100.0)
    eb = EB()
    eb.add(40, 1, kind=1, px=100.2, valid=1, stop=99.2, hold=1)
    r = _one(b, eb)
    assert r[COL["gross"]] == pytest.approx(-1.2)


def test_a_fill_past_its_target_exits_at_once_at_the_fill() -> None:
    b = flat_bars()
    _bar(b, 41, 100.4, 100.6, 99.0, 100.0)
    eb = EB()
    eb.add(40, 1, kind=1, px=100.2, valid=1, stop=99.2, tgt=100.3, hold=3)
    r = _one(b, eb)
    assert r[COL["gross"]] == pytest.approx(0.0)
    assert r[COL["kind"]] == 2 and r[COL["x"]] == 41


def test_a_trade_without_room_for_a_random_time_twin_is_kept_with_a_nan_control() -> None:
    b = flat_bars()
    eb = EB()
    eb.add(78, 1, stop_d=1.0, hold=1)             # fills on the last bar: no later bar for the twin
    r = _one(b, eb, control="random_time")
    assert np.isnan(r[COL["ctrl_gross"]]) and np.isnan(r[COL["excess"]])
    assert np.isfinite(r[COL["gross"]])


def test_a_trade_with_a_nan_control_is_left_out_of_the_excess_statistic_only() -> None:
    day = 86_400 * 10**9
    tr = pd.DataFrame({"t_fill": [10 * day, 11 * day, 11 * day], "excess": [0.5, np.nan, 0.25],
                       "gross": [1.0, 2.0, 0.5]})
    assert S.daily(tr).to_dict() == {10: 0.5, 11: 0.25}
    assert S.daily(tr, "gross").to_dict() == {10: 1.0, 11: 2.5}
