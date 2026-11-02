"""Amendment C1 (docs/research/rethink/audit.md, ADR 0069): the shared research cost in R. Costs are charged in basis
points of the entry price (the cost of record's ICM point estimates at the reference price), the measured swap per side
per night (shorts credited), and the stop overshoot only where the simulated fill has not already gapped past it."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from goldbot.research import arms
from goldbot.research import cost_of_record as C

ROOT = Path(__file__).resolve().parents[2]
COSTS = ROOT / "config" / "arm_costs.yaml"


def _c1() -> C.C1Costs:
    rec = arms.load_costs(COSTS).of_record
    assert rec is not None
    return C.C1Costs.of_record(rec, "icm")


def test_the_repo_table_carries_the_measured_icm_swap_and_no_account() -> None:
    rec = arms.load_costs(COSTS).of_record
    assert rec is not None and "icm" in rec.swap_measured
    sw = rec.swap_measured["icm"]
    assert sw.long_usd_per_lot < 0 < sw.short_usd_per_lot          # longs pay, shorts are credited (11-02 terms)
    assert sw.triple_weekday == 2 and sw.version.startswith("swap-icm-")
    text = (ROOT / "config" / "cost_of_record.yaml").read_text()
    assert "account" not in yaml.safe_load(text)["swap_measured"]["icm"]
    assert "icm-demo" not in text                                     # numbers only: no account identifier


def test_c1_reads_point_estimates_in_bps_at_the_reference_price() -> None:
    rec = arms.load_costs(COSTS).of_record
    assert rec is not None
    c = _c1()
    cell = rec.cells["icm"]["all"]
    assert c.rt_bps["all"] == pytest.approx(cell.round_trip_usd / C.REFERENCE_PRICE_USD * 1e4)   # the point, not ci90
    assert c.os_bps["all"] == pytest.approx(cell.overshoot_usd / C.REFERENCE_PRICE_USD * 1e4)
    assert c.swap_long_bps == pytest.approx(rec.swap_measured["icm"].long_usd_per_lot / 100 / 4000 * 1e4)
    assert "cor-2026-10-31.2" in c.version and "swap-icm-" in c.version


def test_the_same_atr_multiple_stop_costs_the_same_r_in_2012_and_2025() -> None:
    """1h ATR in bps is stable (15-26 bps every year): a 1.5 ATR stop at $1,650 and at $3,300 is the same trade."""
    c = _c1()
    atr_bps = 20.0
    out = {}
    for year, px in ((2012, 1650.0), (2025, 3300.0)):
        stop = 1.5 * atr_bps * 1e-4 * px
        out[year] = c.cost_r(entry_px=np.array([px, px]), stop_usd=np.array([stop, stop]),
                             stop_exit=np.array([1.0, 0.0]), nights=np.array([1, 0]), side=np.array([1, -1]))
    assert out[2012] == pytest.approx(out[2025], rel=0.05)
    # the dollar model this replaces charged the 2012 trade about twice the R of the 2025 one
    legacy = {y: 0.23 / (1.5 * atr_bps * 1e-4 * px) for y, px in ((2012, 1650.0), (2025, 3300.0))}
    assert legacy[2012] > 1.9 * legacy[2025]


def test_an_overnight_short_gets_a_positive_swap_and_a_long_pays() -> None:
    c = _c1()
    px, stop = np.array([2000.0]), np.array([10.0])
    flat = c.cost_r(entry_px=px, stop_usd=stop, stop_exit=np.array([0.0]), nights=np.array([0]), side=np.array([-1]))
    short = c.cost_r(entry_px=px, stop_usd=stop, stop_exit=np.array([0.0]), nights=np.array([1]), side=np.array([-1]))
    long_ = c.cost_r(entry_px=px, stop_usd=stop, stop_exit=np.array([0.0]), nights=np.array([1]), side=np.array([1]))
    assert c.swap_usd(px, np.array([1]), np.array([-1]))[0] > 0                  # credited
    assert short[0] < flat[0] < long_[0]
    assert flat[0] - short[0] == pytest.approx(c.swap_short_bps * 1e-4 * 2000.0 / 10.0)


def test_the_overshoot_is_never_double_counted_with_a_gapped_fill() -> None:
    """A simulated stop that already filled at a gapped open has paid that much overshoot: only the measured residual
    is added, and a gap past the measured overshoot adds nothing."""
    c = _c1()
    px, stop = np.full(3, 4000.0), np.full(3, 8.0)
    os_usd = c.os_bps["all"] * 1e-4 * 4000.0
    gaps = np.array([0.0, 0.5 * os_usd, 3.0 * os_usd])
    r = c.cost_r(entry_px=px, stop_usd=stop, stop_exit=np.ones(3), nights=np.zeros(3), side=np.ones(3),
                 sim_overshoot_usd=gaps)
    rt = c.rt_bps["all"] * 1e-4 * 4000.0
    assert r * 8.0 == pytest.approx([rt + os_usd, rt + 0.5 * os_usd, rt])
    # total adverse at the stop (gap already in the gross + charged residual) is max(gap, measured overshoot)
    assert (gaps + (r * 8.0 - rt)) == pytest.approx(np.maximum(gaps, os_usd))
    # a target or time exit pays no overshoot at all
    t = c.cost_r(entry_px=px[:1], stop_usd=stop[:1], stop_exit=np.zeros(1), nights=np.zeros(1), side=np.ones(1))
    assert t[0] * 8.0 == pytest.approx(rt)


def test_sessions_use_their_own_point_and_outside_any_session_the_pooled_one() -> None:
    c = _c1()
    px, stop = np.full(3, 4000.0), np.full(3, 8.0)
    r = c.cost_r(entry_px=px, stop_usd=stop, stop_exit=np.zeros(3), nights=np.zeros(3), side=np.ones(3),
                 sessions=np.array(["asia", "london", ""]))
    assert r * 8.0 == pytest.approx([c.rt_bps[s] * 0.4 for s in ("asia", "london", "all")])
    with pytest.raises(ValueError, match="session"):
        c.cost_r(entry_px=px[:1], stop_usd=stop[:1], stop_exit=np.zeros(1), nights=np.zeros(1), side=np.ones(1),
                 sessions=np.array(["London"]))


def test_nights_follow_the_measured_triple_day() -> None:
    c = _c1()
    tue = pd.DatetimeIndex(["2026-10-27 12:00"], tz="UTC")                 # Tuesday -> Thursday: 1 + 3 (Wed triple)
    n = c.nights(tue, tue + pd.Timedelta(days=2))
    assert n.tolist() == [4]


def test_another_brokers_swap_is_never_used() -> None:
    rec = arms.load_costs(COSTS).of_record
    assert rec is not None
    with pytest.raises(KeyError, match="swap"):
        C.C1Costs.of_record(rec, "vantage")


def test_swap_block_from_broker_terms_keeps_numbers_only() -> None:
    terms = {"account_id": "icm-demo", "measured_utc": "2026-11-02T05:02:00+00:00", "swap_long_usd_per_lot": -52.2,
             "swap_short_usd_per_lot": 36.0, "swap_triple_weekday": 2, "swap_mode": 1,
             "commission_per_lot_round_trip_usd": 8.0, "notes": ["converted at 0.7466 GBP per USD"]}
    block = C.swap_block(terms, version="swap-icm-test")
    assert block == {"version": "swap-icm-test", "measured_utc": "2026-11-02T05:02:00+00:00",
                     "source": "broker terms (symbol swap, points mode, broker conversion)",
                     "long_usd_per_lot": -52.2, "short_usd_per_lot": 36.0, "triple_weekday": 2}
    rec = arms.SwapMeasured.model_validate(block)
    assert rec.short_usd_per_lot == 36.0


def test_the_swap_block_renders_and_reads_back() -> None:
    table = yaml.safe_load((ROOT / "config" / "cost_of_record.yaml").read_text())
    for row in table["cells"].values():
        for cell in row.values():
            for k, v in list(cell.items()):
                if isinstance(v, list):
                    cell[k] = tuple(v)
    text = C.render_yaml(table)
    assert text == (ROOT / "config" / "cost_of_record.yaml").read_text()       # the committed file is the builder's
    back = arms.CostOfRecord.model_validate(yaml.safe_load(text))
    assert back.swap_measured["icm"].long_usd_per_lot == table["swap_measured"]["icm"]["long_usd_per_lot"]
