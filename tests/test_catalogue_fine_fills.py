"""Catalogue harness fill bar from finer bars (quant C10, eval1_signoff_1107, 2026-11-07): "Every unit with intrabar
stop or limit entries (all B04 variants, S20, S21 and the other OCO units) is screened only after D5 is fixed: the fill
bar is resolved from 1m bars wherever they exist; otherwise the unit is excluded and listed; stop-entry fills pay the
measured stop overshoot of the cost of record, the same as stop exits." D5: a stop/limit entry filled inside a bar
had its stop checked against the whole bar, including the part before the fill (B04: -0.49 R/trade on zero-edge data).

Flat 1d gold bars (opening 21:00 UTC in January: New York's session on EST): ATR20 1.5, round trip the newyork
cost of record cell $0.263 (backlog 146), risk floor max(0.375, 0.789) = 0.789."""
from __future__ import annotations

import numpy as np
import pytest

from goldbot.research.catalogue import dispatcher as D
from goldbot.research.catalogue import sim as SIM
from goldbot.research.catalogue.core import EB, Ctx
from goldbot.research.catalogue.costs import default_model, load_table
from goldbot.research.catalogue.data import MIN, Bars
from tests.test_catalogue_fill_rules import COL, OVER, _bar, rt
from tests.test_catalogue_random_walk import flat_bars


def _one(b: Bars, eb: EB, *, fine: Bars | None = None, counts: dict[str, int] | None = None) -> np.ndarray:
    tr = SIM.simulate(b, eb.build(), control="random_side", seed_key="x", fine=fine, counts=counts)
    assert len(tr) == 1, tr
    return tr[0]


def fine_bars(b: Bars, j: int, rows: list[tuple[float, float, float, float]]) -> Bars:
    """Finer bars inside parent bar j only (one per row, 1 minute apart from the parent's open)."""
    n = len(rows)
    t = int(b.t[j]) + np.arange(n, dtype=np.int64) * MIN
    o, h, lo, c = (np.array([r[k] for r in rows], float) for k in range(4))
    return Bars("XAUUSD", "1m", t, t + MIN, o, h, lo, c, np.full(n, np.nan), np.zeros(n, bool),
                np.zeros(n, np.int32), np.zeros(n))


def _stop_entry_bars() -> tuple[Bars, Bars]:
    """Bar 41 opens 100, dips to 98 BEFORE rising through the buy stop at 101 (whole-bar low 98 < stop 99.5), then
    holds above 100.4 after the fill and closes at 101."""
    b = flat_bars()
    _bar(b, 41, 100.0, 101.5, 98.0, 101.0)
    fine = fine_bars(b, 41, [(100.0, 100.0, 98.0, 99.0), (100.5, 101.2, 100.4, 101.1), (101.1, 101.5, 100.8, 101.0)])
    return b, fine


def _stop_entry() -> EB:
    eb = EB()
    eb.add(40, 1, kind=1, px=101.0, valid=1, stop=99.5, hold=1)
    return eb



def _swap(r: np.ndarray) -> float:
    """One long gold night of C2 financing at the fill (default model: benchmark 0, the measured markup)."""
    return float(r[COL["swap_nights"]]) * load_table()["instruments"]["XAUUSD"]["markup_long"] / 100 / 365 * float(
        r[COL["fill"]])

def test_a_stop_entry_inside_a_bar_is_walked_from_its_fill_minute() -> None:
    b, fine = _stop_entry_bars()
    assert _one(b, _stop_entry())[COL["gross"]] == pytest.approx(-1.0)      # no finer bars: the pre-fill low stops it
    r = _one(b, _stop_entry(), fine=fine)
    assert r[COL["fill"]] == pytest.approx(101.0)
    assert r[COL["gross"]] == pytest.approx(0.0)                             # time exit at the close, 101
    assert r[COL["t_fill"]] == int(fine.t[1])


def test_stop_entry_fills_pay_the_stop_overshoot() -> None:
    b, fine = _stop_entry_bars()
    r = _one(b, _stop_entry(), fine=fine)
    assert r[COL["swap_nights"]] == 1                                        # the bar spans one 17:00 NY rollover
    assert r[COL["cost"]] == pytest.approx((rt(r) + OVER + _swap(r)) / 1.5)


def test_a_stop_entry_fills_at_the_minute_open_when_that_minute_gaps_past_the_price() -> None:
    b, fine = _stop_entry_bars()
    fine.o[1] = 101.3
    fine.h[1] = 101.4
    r = _one(b, _stop_entry(), fine=fine)
    assert r[COL["fill"]] == pytest.approx(101.3)
    assert r[COL["gross"]] == pytest.approx((101.0 - 101.3) / 1.5)


def test_a_fill_bar_without_the_trigger_in_its_finer_bars_falls_back_to_the_whole_bar_and_is_counted() -> None:
    b, fine = _stop_entry_bars()
    fine.h[:] = 100.9
    counts: dict[str, int] = {}
    r = _one(b, _stop_entry(), fine=fine, counts=counts)
    assert r[COL["gross"]] == pytest.approx(-1.0)
    assert counts == {"fine_miss": 1}


def test_a_limit_entry_random_side_twin_is_walked_from_the_fill_minute() -> None:
    """Bar 41 opens 101 and rallies to 104 BEFORE falling to the buy limit at 100. The opposite twin (short from 100,
    stop 101.5) is stopped by that pre-fill high on the whole bar; from the fill minute it exits at the close."""
    b = flat_bars()
    _bar(b, 41, 101.0, 104.0, 99.8, 101.0)
    fine = fine_bars(b, 41, [(101.0, 104.0, 101.0, 102.0), (100.3, 100.4, 99.8, 100.2), (100.2, 101.2, 100.0, 101.0)])
    eb = EB()
    eb.add(40, 1, kind=2, px=100.0, valid=1, stop=98.5, hold=1)
    gross = (101.0 - 100.0) / 1.5
    whole = _one(b, eb)
    assert whole[COL["ctrl_gross"]] == pytest.approx(0.5 * (gross - 1.0))
    r = _one(b, eb, fine=fine)
    assert r[COL["gross"]] == pytest.approx(gross)
    assert r[COL["ctrl_gross"]] == pytest.approx(0.0)
    assert r[COL["cost"]] == pytest.approx((rt(r) + _swap(r)) / 1.5)          # a limit fill pays no overshoot


# ------------------------------------------------------------------ C10: which cells get finer bars
def _unit_with(inst: str, kind: int, monkeypatch: pytest.MonkeyPatch) -> tuple[dict, list[Bars | None]]:
    b = flat_bars()
    b.inst = inst
    eb = EB()
    eb.add(40, 1, kind=kind, px=100.2, valid=1, stop=99.0, hold=1)
    ev = eb.build()
    monkeypatch.setattr(D, "unit_events", lambda u, win, ctx, **k: {(inst, "1d"): (b, ev)})
    fine = fine_bars(b, 41, [(100.0, 100.5, 99.5, 100.0)])
    monkeypatch.setattr(D, "get_minutes", lambda win, **k: fine)
    seen: list[Bars | None] = []

    def fake(b: Bars, ev: object, **k: object) -> np.ndarray:
        seen.append(k.get("fine"))                    # type: ignore[arg-type]
        return np.zeros((0, len(SIM.TRADE_COLS)))

    monkeypatch.setattr(D, "simulate", fake)
    return {"unit": "T-V0", "control": "random_side"}, seen


def test_a_unit_with_intrabar_entries_on_an_instrument_without_1m_bars_is_excluded_and_listed(
        monkeypatch: pytest.MonkeyPatch) -> None:
    u, seen = _unit_with("XAGUSD", 1, monkeypatch)
    tr, _, counts = D.unit_trades(u, "explore", Ctx(window="explore", aux={"costs": default_model()}))
    assert len(tr) == 0 and seen == []
    assert counts["no_1m_cells"] == 1 and counts["excluded_no_1m"] == 1


def test_gold_cells_with_intrabar_entries_are_simulated_on_the_1m_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    u, seen = _unit_with("XAUUSD", 2, monkeypatch)
    _, _, counts = D.unit_trades(u, "explore", Ctx(window="explore", aux={"costs": default_model()}))
    assert len(seen) == 1 and seen[0] is not None
    assert counts["excluded_no_1m"] == 0


def test_market_entry_units_need_no_1m_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    u, seen = _unit_with("XAGUSD", 0, monkeypatch)
    _, _, counts = D.unit_trades(u, "explore", Ctx(window="explore", aux={"costs": default_model()}))
    assert seen == [None] and counts["excluded_no_1m"] == 0


# ------------------------------------------------------------------ quant re-verify R2: the 1m window stays inside the bar
def test_the_1m_window_of_a_bar_whose_close_lies_past_the_next_open_ends_at_the_next_open() -> None:
    """Bar 41's ta overlaps bar 42 (a source with a late close stamp): a 1m bar after bar 42's open is not bar 41's."""
    b = flat_bars()
    b.ta[41] = b.t[42] + 5 * MIN
    b.h[41] = 101.5
    t42 = int(b.t[42])
    fine = Bars("XAUUSD", "1m", np.array([int(b.t[41]), t42 + MIN]), np.array([int(b.t[41]) + MIN, t42 + 2 * MIN]),
                np.array([100.0, 101.0]), np.array([100.4, 101.2]), np.array([99.9, 100.9]), np.array([100.2, 101.1]),
                np.full(2, np.nan), np.zeros(2, bool), np.zeros(2, np.int32), np.zeros(2))
    assert SIM.fine_fill(fine, b, 41, 1, 1, 101.0) is None


def test_a_1m_fill_outside_the_bar_range_is_a_fine_miss() -> None:
    """The 1m open gaps to 101.8, above the bar's high 101.5 (source mismatch): the whole-bar rule, counted."""
    b, fine = _stop_entry_bars()
    fine.o[1], fine.h[1] = 101.8, 101.9
    assert SIM.fine_fill(fine, b, 41, 1, 1, 101.0) is None
    counts: dict[str, int] = {}
    r = _one(b, _stop_entry(), fine=fine, counts=counts)
    assert counts == {"fine_miss": 1}
    assert r[COL["fill"]] == pytest.approx(101.0)


def test_a_limit_entry_is_not_credited_a_target_touched_in_its_fill_minute() -> None:
    """Buy limit at 100, target 100.3: the fill minute opens 100.4 (touching the target BEFORE the fill) and falls to
    99.9; the next minute closes at 100.1 below the target. No target exit."""
    b = flat_bars()
    _bar(b, 41, 100.6, 100.6, 99.9, 100.1)
    fine = fine_bars(b, 41, [(100.6, 100.6, 100.45, 100.5), (100.4, 100.45, 99.9, 100.0), (100.0, 100.2, 99.95, 100.1)])
    eb = EB()
    eb.add(40, 1, kind=2, px=100.0, valid=1, stop=99.0, tgt=100.3, hold=1)
    r = _one(b, eb, fine=fine)
    assert r[COL["kind"]] == 0
    assert r[COL["gross"]] == pytest.approx(0.1)
