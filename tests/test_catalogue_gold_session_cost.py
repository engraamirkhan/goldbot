"""Backlog 146: the catalogue sim charges gold the cost-of-record cell of the fill's session (costs.CostModel.round_trip).

Design rule (costs module docstring): "gold: the cost of record cell of the fill instant's session, the worse of the
brokers; a cell on fewer than MIN_COST_CELL_FILLS fills reads no lower than its broker's pooled cell, a missing one
reads the pooled cell, and no session is cheaper than the flat GOLD_RT_USD"."""
from __future__ import annotations

import copy

import numpy as np
import pandas as pd
import pytest
import yaml

from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.research.arms import MIN_COST_CELL_FILLS
from goldbot.research.catalogue import costs as C
from goldbot.research.catalogue import sim as SIM


def ns(stamp: str) -> int:
    return int(pd.Timestamp(stamp, tz="UTC").value)


ASIA = ns("2026-11-10 02:00")              # Tokyo 11:00
LONDON = ns("2026-11-10 10:00")            # London 10:00 (GMT)
NEW_YORK = ns("2026-11-10 15:00")          # New York 10:00 (EST)


def cor() -> dict:
    return copy.deepcopy(C.load_cost_of_record())


def cell(rt: float, n: int = 400) -> dict:
    """A cost-of-record cell whose round trip is all spread (the record checks the parts add up)."""
    return {"n_fills": n, "spread_usd": rt, "spread_p90_usd": rt, "spread_ci90": [rt, rt], "entry_slip_usd": 0.0,
            "entry_slip_ci90": [0.0, 0.0], "n_exit_legs": 0, "exit_slip_usd": 0.0, "exit_slip_ci90": [0.0, 0.0],
            "exit_slip_source": "none", "commission_usd": 0.0, "round_trip_usd": rt, "round_trip_ci90": [rt, rt],
            "n_stop_exits": 0, "overshoot_usd": 0.0, "overshoot_ci90": [0.0, 0.0], "overshoot_trusted": True}


def table(**brokers: dict[str, dict]) -> dict:
    doc = cor()
    doc["cells"] = brokers
    return doc


# ------------------------------------------------------------------ the committed table (cor-2026-10-31.2)
def test_an_asia_gold_fill_is_charged_the_asia_cost_of_record_of_0_204() -> None:
    assert C.default_model().round_trip("XAUUSD", 4000.0, 0.05, ASIA) == pytest.approx(0.204)


def test_london_and_new_york_gold_fills_take_the_worse_of_the_two_brokers() -> None:
    """ICM London 0.164 and New York 0.166 against Vantage 0.293 and 0.263 (both on >= 30 fills): Vantage's."""
    m = C.default_model()
    assert m.round_trip("XAUUSD", 4000.0, 0.05, LONDON) == pytest.approx(0.293)
    assert m.round_trip("XAUUSD", 4000.0, 0.05, NEW_YORK) == pytest.approx(0.263)


def test_no_gold_session_is_charged_less_than_the_flat_cost_of_record() -> None:
    assert min(C.default_model().gold_session_rt.values()) >= C.GOLD_RT_USD


def test_a_gold_fill_without_an_instant_pays_the_dearest_session() -> None:
    m = C.default_model()
    assert m.round_trip("XAUUSD", 4000.0, 0.05) == pytest.approx(max(m.gold_session_rt.values()))


def test_the_instant_never_changes_another_instruments_round_trip() -> None:
    m = C.default_model()
    assert m.round_trip("EURUSD", 1.3, 0.0005, ASIA) == m.round_trip("EURUSD", 1.3, 0.0005, LONDON) \
        == m.round_trip("EURUSD", 1.3, 0.0005)


# ------------------------------------------------------------------ the reading rule on synthetic tables
def test_measured_session_cells_cheaper_than_the_flat_cost_are_floored_at_it() -> None:
    doc = table(icm={s: cell(0.10) for s in ("asia", "london", "newyork", "all")})
    assert C.gold_session_rt(doc, doc["version"]) == {s: C.GOLD_RT_USD for s in C.GOLD_SESSIONS}


def test_a_dearer_measured_session_cell_raises_the_charge_above_the_flat_cost() -> None:
    doc = table(icm={"asia": cell(0.31), "london": cell(0.17), "newyork": cell(0.18), "all": cell(0.2)})
    assert C.gold_session_rt(doc, doc["version"]) == pytest.approx({"asia": 0.31, "london": 0.17, "newyork": 0.18})


def test_a_thin_session_cell_reads_no_lower_than_its_brokers_pooled_cell() -> None:
    thin = MIN_COST_CELL_FILLS - 1
    doc = table(icm={"asia": cell(0.20), "london": cell(0.10, n=thin), "newyork": cell(0.40, n=thin),
                     "all": cell(0.22)})
    rt = C.gold_session_rt(doc, doc["version"])
    assert rt["london"] == pytest.approx(0.22)             # a thin cheap cell never lowers the pooled read
    assert rt["newyork"] == pytest.approx(0.40)            # a thin dear cell can raise it


def test_a_missing_session_cell_reads_its_brokers_pooled_cell() -> None:
    doc = table(icm={"asia": cell(0.20), "all": cell(0.25)})
    assert C.gold_session_rt(doc, doc["version"])["london"] == pytest.approx(0.25)


def test_another_instruments_cells_never_price_gold() -> None:
    doc = table(icm={s: cell(0.17) for s in ("asia", "london", "newyork", "all")},
                **{"icm:EURUSD": {s: cell(5.0) for s in ("asia", "london", "newyork", "all")}})
    assert max(C.gold_session_rt(doc, doc["version"]).values()) == pytest.approx(0.17)


def test_a_cost_of_record_other_than_the_pinned_version_stops_the_run() -> None:
    doc = cor()
    with pytest.raises(C.MissingCost, match="pinned"):
        C.gold_session_rt(doc, "cor-1999-01-01.1")


def test_the_pinned_cost_of_record_version_is_the_deciding_blocks() -> None:
    assert C.load_table()["deciding"]["cost_of_record"] == C.load_cost_of_record()["version"]


def test_a_gold_model_without_session_cells_fails_closed() -> None:
    bare = C.CostModel(version="x", table=C.default_model().table)
    with pytest.raises(C.MissingCost):
        bare.round_trip("XAUUSD", 4000.0, 0.05, ASIA)


# ------------------------------------------------------------------ the session boundaries
@pytest.mark.parametrize(("stamp", "session"), [
    ("2026-07-15 06:59", "asia"), ("2026-07-15 07:00", "london"),           # London 08:00 BST
    ("2026-07-15 12:29", "london"), ("2026-07-15 12:30", "newyork"),        # New York 08:30 EDT
    ("2026-07-15 20:59", "newyork"), ("2026-07-15 21:00", "asia"),          # New York 17:00 EDT, then thin hours
    ("2026-01-15 07:59", "asia"), ("2026-01-15 08:00", "london"),           # London 08:00 GMT
    ("2026-01-15 13:29", "london"), ("2026-01-15 13:30", "newyork"),        # New York 08:30 EST
    ("2026-01-15 21:59", "newyork"), ("2026-01-15 22:00", "asia"),          # New York 17:00 EST
    ("2026-03-10 12:30", "newyork"),                                        # US on EDT, UK still on GMT
    ("2026-01-15 23:00", "asia"),                                           # Tokyo 08:00
])
def test_the_session_boundaries_follow_each_citys_own_clock(stamp: str, session: str) -> None:
    assert C.gold_session(ns(stamp)) == session


def test_the_session_lookup_agrees_with_the_calendars_session_label() -> None:
    rng = np.random.default_rng(146)
    lo, hi = ns("2000-01-01"), ns("2030-12-31")
    stamps = rng.integers(lo, hi, size=5000, dtype=np.int64) // 60_000_000_000 * 60_000_000_000
    want = DEFAULT_SESSIONS.session_label(pd.DatetimeIndex(pd.to_datetime(stamps, utc=True)))
    assert [C.gold_session(int(t)) for t in stamps] == list(want)


def test_an_instant_outside_the_session_clock_stops_the_run() -> None:
    with pytest.raises(C.MissingCost):
        C.gold_session(ns("1950-06-01 10:00"))


# ------------------------------------------------------------------ the sim charges the fill's session
def test_the_sim_charges_a_gold_trade_its_fill_sessions_round_trip() -> None:
    m = C.default_model()
    asia = SIM.cost_price("XAUUSD", 1, 4000.0, 0.05, 2, ASIA, ASIA + 60_000_000_000, costs=m)
    london = SIM.cost_price("XAUUSD", 1, 4000.0, 0.05, 2, LONDON, LONDON + 60_000_000_000, costs=m)
    assert asia == pytest.approx(0.204) and london == pytest.approx(0.293)


def test_the_risk_floor_uses_the_decision_instants_session() -> None:
    m = C.default_model()
    assert SIM.risk_floor("XAUUSD", 4000.0, 0.05, 0.0, m, ASIA) == pytest.approx(3 * 0.204)
    assert SIM.risk_floor("XAUUSD", 4000.0, 0.05, 0.0, m, LONDON) == pytest.approx(3 * 0.293)


def test_the_committed_cost_of_record_is_the_one_the_reading_rule_reads() -> None:
    raw = yaml.safe_load(C.COR_PATH.read_text())
    assert raw == C.load_cost_of_record()
