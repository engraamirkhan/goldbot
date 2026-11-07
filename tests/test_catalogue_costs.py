"""EVAL-1 section 1 measured costs and the C2 financing sign in the catalogue sim (goldbot/research/catalogue/costs.py,
config/catalogue_costs.yaml)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.research.catalogue import costs as C
from goldbot.research.catalogue import sim as SIM
from goldbot.research.catalogue.data import DAY, HOUR
from tests.test_catalogue_lookahead import synth

MEASURED = Path.home() / "tmp" / "measured_costs_1107.json"
T2026 = int(pd.Timestamp("2026-11-02", tz="UTC").value)            # a Monday


def at_rates(**pct: float) -> C.CostModel:
    """The committed table with constant benchmark rates (%/yr)."""
    rates = {k: (np.array([0], np.int64), np.array([v])) for k, v in pct.items()}
    return C.model_from_table(C.load_table(), rates)


MEASURED_BENCH = at_rates(**C.load_table()["benchmarks"])


def pinned(days: int = C.MIN_MEASURED_DAYS) -> C.CostModel:
    """The committed table as if every spread sample covered `days` trading days (benchmark rates 0)."""
    doc = C.load_table()
    return C.model_from_table({**doc, "instruments": {k: {**r, "spread_days": days}
                                                       for k, r in doc["instruments"].items()}})


def test_a_long_hold_on_an_instrument_with_positive_measured_long_swap_earns_financing() -> None:
    """AUDUSD pays the long today (swap_long +0.999/lot-night; long_cost -0.531 %/yr): C2 at the measurement date's
    benchmarks credits a 10-night long, so its cost is below the round trip; the short pays."""
    assert C.load_table()["instruments"]["AUDUSD"]["long_pct"] < 0
    t0, t1 = T2026 + 12 * HOUR, T2026 + 14 * DAY + 12 * HOUR
    fin_long = MEASURED_BENCH.financing("AUDUSD", 1, 0.66, t0, t1)
    assert fin_long < 0
    rt = MEASURED_BENCH.round_trip("AUDUSD", 0.66, float("nan"))
    assert rt == pytest.approx(C.fallback_rt("AUDUSD", 0.66))          # a 5-day spread sample: the fallback stays
    cost = SIM.cost_price("AUDUSD", 1, 0.66, float("nan"), 0, t0, t1, costs=MEASURED_BENCH)
    assert cost == pytest.approx(rt + fin_long) and cost < rt
    assert MEASURED_BENCH.financing("AUDUSD", -1, 0.66, t0, t1) > 0


def test_the_c2_decomposition_reproduces_the_measured_swaps_at_the_measurement_benchmarks() -> None:
    """side x (r_quote - r_base) + markup_side equals the measured long cost / short credit wherever no markup was
    floored (the C2 sign convention matches the broker's)."""
    tab = C.load_table()["instruments"]
    checked = 0
    for inst, r in tab.items():
        if r["kind"] != "benchmark":
            continue
        base, quote = C.legs(inst)
        if quote not in C.BENCH_SERIES or (base and base not in C.BENCH_SERIES):
            continue
        if r["markup_long"] > 0:
            assert MEASURED_BENCH.debit_pct(inst, 1, T2026) == pytest.approx(r["long_pct"], abs=0.01), inst
        div = C.DIV_SHORT.get(inst, 0.0)
        if r["markup_short"] > 0:
            assert MEASURED_BENCH.debit_pct(inst, -1, T2026) - div == pytest.approx(-r["short_credit_pct"], abs=0.01)
        checked += 1
    assert checked >= 20


def test_an_instrument_missing_from_the_cost_table_stops_the_run() -> None:
    """Fail closed: no cost, no trade statistic."""
    with pytest.raises(C.MissingCost, match="FOO"):
        C.default_model().round_trip("FOO", 1.0, float("nan"))
    b = synth("XAUUSD", "1d", n=300, seed=1)
    b.inst = "FOO"
    from goldbot.research.catalogue import dispatcher as D
    from goldbot.research.catalogue.core import Ctx
    ev = D.null_longclose(b, {}, Ctx("explore"))
    with pytest.raises(C.MissingCost):
        SIM.simulate(b, ev, control="random_side", seed_key="x")


def test_a_window_model_without_a_benchmark_rate_at_the_fill_stops_the_run() -> None:
    m = C.model_from_table(C.load_table(), {"USD": (np.array([10**18], np.int64), np.array([1.0]))})
    with pytest.raises(C.MissingCost, match="USD"):
        m.debit_pct("US500", 1, 10**17)
    with pytest.raises(C.MissingCost, match="EUR"):
        m.debit_pct("EURUSD", 1, 2 * 10**18)


def test_the_bps_round_trip_matches_the_measured_file() -> None:
    """rt = median spread + 2 x commission per side (bps of price), the larger of two brokers; once pinned (20 days) the
    sim charges exactly that; gold keeps the cost of record."""
    tab = C.load_table()["instruments"]
    for inst, r in tab.items():
        if inst == "XAUUSD" or r["rt_bps"] is None:
            continue
        assert pinned().round_trip(inst, 100.0, float("nan")) == pytest.approx(r["rt_bps"] * 1e-2)
    assert tab["EURUSD"]["rt_bps"] == pytest.approx(0.09 + 2 * 0.362, abs=2e-3)
    assert C.default_model().round_trip("XAUUSD", 2000.0, float("nan")) == C.GOLD_RT_USD
    if MEASURED.exists():                               # the committed table is the measured file, field by field
        doc = json.loads(MEASURED.read_text())
        assert C.build_table(doc)["instruments"] == tab
        assert C.load_table()["instruments_sha256"] == doc["sha256_of_instruments"]
        for e in doc["instruments"]:
            sp, cm = e.get("spread") or {}, e.get("commission") or {}
            if e["instrument"] in tab and cm.get("per_side_bps") is not None and sp.get("median_bps") is not None:
                assert tab[e["instrument"]]["rt_bps"] >= sp["median_bps"] + 2 * cm["per_side_bps"] - 1e-3


def test_a_spread_sample_under_20_trading_days_is_not_pinned_and_the_fallback_stays() -> None:
    """Quant ruling 2026-11-07: "the fallback cost stays for each instrument and session until 20 trading days have
    been measured". The committed table has 3-11 days per instrument, so every non-gold round trip is the fallback."""
    tab = C.load_table()["instruments"]
    assert all(r["spread_days"] < C.MIN_MEASURED_DAYS for r in tab.values())
    assert all(r["spread_window"] for r in tab.values())                  # dates recorded
    for inst in ("EURUSD", "US500", "XAGUSD"):
        fb = max(C.fallback_rt(inst, 100.0), 0.0)
        assert C.default_model().round_trip(inst, 100.0, float("nan")) == pytest.approx(fb)
        assert pinned(19).round_trip(inst, 100.0, float("nan")) == pytest.approx(fb)
        assert pinned(20).round_trip(inst, 100.0, float("nan")) == pytest.approx(tab[inst]["rt_bps"] * 1e-2)
    assert C.default_model().round_trip("EURUSD", 1.3, 0.0005) == 0.0005    # the fallback keeps the wider bar spread


def test_a_wider_historical_bar_spread_is_never_undercut_by_the_measured_spread() -> None:
    m = pinned()
    assert m.round_trip("EURUSD", 1.3, 0.0003) == pytest.approx(0.0003 + 2 * 0.362e-4 * 1.3, rel=1e-6)


def test_unmeasured_fields_keep_the_frozen_fallback() -> None:
    """BTCUSD: no commission and no swap measured: the fallback round trip and SWAP_FALLBACK per calendar night."""
    m = C.default_model()
    assert m.round_trip("BTCUSD", 20_000.0, float("nan")) == C.FALLBACK_RT["BTCUSD"]
    assert m.debit_pct("BTCUSD", 1, T2026) == m.debit_pct("BTCUSD", -1, T2026) == C.SWAP_FALLBACK
    assert m.swap_nights("BTCUSD", T2026, T2026 + 7 * DAY) == 7


def test_triple_swap_follows_the_brokers_weekday() -> None:
    m = C.default_model()
    mon = T2026 + 12 * HOUR
    assert m.swap_nights("EURUSD", mon, mon + 7 * DAY) == 7          # Mon..Fri, Wed counts 3, Sat/Sun 0
    assert m.swap_nights("EURUSD", mon + 2 * DAY, mon + 3 * DAY) == 3  # Wed night
    assert m.swap_nights("US500", mon + 4 * DAY, mon + 7 * DAY) == 3   # Fri night (index triple Fri), weekend 0
    assert m.swap_nights("EURUSD", mon + 4 * DAY, mon + 7 * DAY) == 1


def test_markups_are_never_a_credit_and_futures_referenced_cfds_never_credit_todays_carry() -> None:
    tab = C.load_table()["instruments"]
    assert all(r["markup_long"] >= 0 and r["markup_short"] >= 0 for r in tab.values())
    m = C.default_model()
    assert tab["XTIUSD"]["long_pct"] < 0                                # the long receives today
    assert m.debit_pct("XTIUSD", 1, T2026) == pytest.approx(0.5 * tab["XTIUSD"]["two_sided"])
    assert m.debit_pct("XTIUSD", -1, T2026) == pytest.approx(-tab["XTIUSD"]["short_credit_pct"])
    assert m.debit_pct("USINDEX", 1, T2026) == 0.0


def test_index_longs_get_no_dividend_credit_and_index_shorts_pay_one() -> None:
    m = at_rates(USD=0.0, EUR=0.0, GBP=0.0)
    tab = C.load_table()["instruments"]
    assert m.debit_pct("US500", 1, T2026) == pytest.approx(tab["US500"]["markup_long"])
    assert m.debit_pct("US500", -1, T2026) == pytest.approx(tab["US500"]["markup_short"] + C.DIV_SHORT["US500"])
    assert m.debit_pct("DE40", -1, T2026) == pytest.approx(tab["DE40"]["markup_short"])


def test_the_screen_passes_the_windows_cost_model_to_the_sim() -> None:
    """dispatcher.unit_trades reads ctx.aux['costs'] (window_model): a context without one stops the run."""
    from goldbot.research.catalogue import dispatcher as D
    from goldbot.research.catalogue.core import Ctx
    u = next(x for x in D.units() if x["unit"] == "NULL-LONGCLOSE-GOLD")
    with pytest.raises(KeyError, match="costs"):
        D.unit_trades(u, "explore", Ctx("explore"))
