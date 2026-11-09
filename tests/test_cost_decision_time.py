"""EVAL-2 deciding cost, stress line and the realised-field lint (method section 4; prereg 2026-11-09 section 4.2;
goldbot/research/catalogue/costs.py `deciding_cost`/`stress_cost`, goldbot/research/catalogue/lint.py).

Pin `atr-pin-2026-11-09.2` (config/catalogue_costs.yaml `deciding`): RT $0.230 all-in, overshoot $0.145, price
4068.7; ATR today 15m 7.004, 1h 14.688; 2010-17 era median ATR 15m 1.52, 1h 3.26 at price 1287.2."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from goldbot.research.catalogue import costs as C
from goldbot.research.catalogue import lint as L

T0 = int(pd.Timestamp("2026-11-03 10:00", tz="UTC").value)          # a Tuesday, intraday: no rollover inside
H = 3_600_000_000_000
ERA_PRICE = 1287.2
ERA_ATR = {"15m": 1.52, "1h": 3.26}
TODAY_ATR = {"15m": 7.004, "1h": 14.688}
REPO_TABLE = C.TABLE_PATH


def dc(tf: str = "15m", *, atr: float, price: float = 4068.7, stop_atr: float = 1.0, side: int = 1,
       t_exit: int = T0 + H, exit_share: float = 0.0, entry_share: float = 0.0, broker: str = "icm",
       costs: C.CostModel | None = None) -> C.DecidingCost:
    return C.deciding_cost("XAUUSD", tf, side=side, price=price, atr_decision=atr, stop_atr=stop_atr,
                           t_fill_ns=T0, t_exit_ns=t_exit, stop_exit_share=exit_share,
                           stop_entry_share=entry_share, window="explore", broker=broker, costs=costs)


# ------------------------------------------------------------------ the pin table
@pytest.mark.parametrize("tf,today,stop_out", [("15m", 0.033, 0.054), ("1h", 0.016, 0.026)])
def test_deciding_cost_at_todays_atr_matches_the_pin_table_round_trip_and_stop_out_columns(tf, today, stop_out):
    """At today's ATR and price the bp engine equals today's level: RT/ATR, and (RT+OS)/ATR when every trade stops."""
    d = dc(tf, atr=TODAY_ATR[tf])
    assert round(d.today_r, 3) == today and round(d.bp_engine_r, 3) == today and round(d.r, 3) == today
    assert round(dc(tf, atr=TODAY_ATR[tf], exit_share=1.0).r, 3) == stop_out
    assert d.pin == C.ATR_PIN == "atr-pin-2026-11-09.2" and C.GATE_COST_MULT == 1.5


@pytest.mark.parametrize("tf,bp,stress", [("15m", 0.048, 0.151), ("1h", 0.022, 0.071)])
def test_deciding_cost_and_stress_at_the_2010_17_era_atr_match_the_pin_stress_table(tf, bp, stress):
    """2010-17 era median ATR: the deciding cost is the bp engine (0.565 bp at the era price), the stress line the
    $-constant round trip. The stress is dearer and is not the deciding cost."""
    d = dc(tf, atr=ERA_ATR[tf], price=ERA_PRICE)
    assert round(d.bp_engine_r, 3) == bp and round(d.r, 3) == bp
    s = C.stress_cost("XAUUSD", tf, atr_decision=ERA_ATR[tf], stop_atr=1.0)
    assert round(s, 3) == stress and s > d.r


def test_cost_in_r_scales_with_the_stop_width_in_atr():
    assert dc(atr=7.004, stop_atr=2.0).r == pytest.approx(dc(atr=7.004).r / 2)
    assert C.stress_cost("XAUUSD", "15m", atr_decision=1.52, stop_atr=2.0) == pytest.approx(0.230 / 3.04)


# ------------------------------------------------------------------ the max rule
def test_deciding_cost_takes_todays_level_when_the_bp_engine_is_cheaper():
    """A quiet-ATR-in-bp era is not the only case: at a wide decision ATR the bp engine falls below today's R floor."""
    d = dc("1h", atr=40.0)
    assert d.bp_engine_r < d.today_r and d.r == pytest.approx(d.today_r)


def test_deciding_cost_takes_the_bp_engine_when_it_is_dearer_than_today():
    d = dc("15m", atr=1.52, price=ERA_PRICE)
    assert d.bp_engine_r > d.today_r and d.r == pytest.approx(d.bp_engine_r)


# ------------------------------------------------------------------ overshoot on both legs
def test_overshoot_is_charged_on_stop_exits_and_on_stop_entry_fills_scaled_with_the_round_trip():
    base = dc(atr=1.52, price=ERA_PRICE)
    per_leg = base.r * 0.145 / 0.230                     # in bp with the round trip (the bp engine won the max)
    exit_only = dc(atr=1.52, price=ERA_PRICE, exit_share=1.0)
    entry_only = dc(atr=1.52, price=ERA_PRICE, entry_share=1.0)
    both = dc(atr=1.52, price=ERA_PRICE, exit_share=0.4, entry_share=1.0)
    assert exit_only.overshoot_r == pytest.approx(per_leg) and entry_only.overshoot_r == pytest.approx(per_leg)
    assert both.overshoot_r == pytest.approx(1.4 * per_leg)
    assert both.r == pytest.approx(base.r + 1.4 * per_leg)


@pytest.mark.parametrize("bad", [-0.1, 1.2, float("nan")])
def test_a_stop_share_outside_zero_to_one_is_refused(bad):
    with pytest.raises(ValueError):
        dc(atr=7.0, exit_share=bad)


# ------------------------------------------------------------------ no favourable credit
def _rates(usd: float) -> C.CostModel:
    return C.model_from_table(C.load_table(), {"USD": (np.array([0], np.int64), np.array([usd]))})


def test_a_swap_credit_never_lowers_the_deciding_cost():
    """A short over 5 rollovers at a 20 %/yr USD rate is credited by C2; the deciding cost charges 0, not a credit."""
    cm = _rates(20.0)
    t_exit = T0 + 5 * 24 * H
    assert cm.financing("XAUUSD", -1, 4068.7, T0, t_exit) < 0
    d = dc(atr=7.004, side=-1, t_exit=t_exit, costs=cm)
    assert d.swap_r == 0.0 and d.r == pytest.approx(max(d.today_r, d.bp_engine_r))


def test_a_paid_swap_over_rollovers_is_added_in_r():
    cm = _rates(3.92)
    t_exit = T0 + 2 * 24 * H
    fin = cm.financing("XAUUSD", 1, 4068.7, T0, t_exit)
    d = dc(atr=7.004, side=1, t_exit=t_exit, costs=cm)
    assert fin > 0 and d.swap_r == pytest.approx(fin / 7.004)


def test_a_fill_better_than_replay_earns_no_s5_credit_and_a_worse_one_is_charged_at_decision_atr():
    """icm's measured gap net of the slippage prior is favourable (-$0.008): charged 0. Vantage's (+$0.031) is
    charged in $ at the decision ATR until its S5 passes."""
    assert C.load_table()["deciding"]["s5_gap_usd"]["icm"]["15m"] < 0
    assert dc(atr=7.004).s5_gap_r == 0.0
    v = dc(atr=1.52, price=ERA_PRICE, broker="vantage")
    assert v.s5_gap_r == pytest.approx(0.031 / 1.52)
    assert v.r == pytest.approx(v.bp_engine_r + v.s5_gap_r)


def test_the_s5_gap_drops_out_once_s5_passes_for_that_broker_and_band(monkeypatch):
    doc = C.load_table()
    passed = {**doc, "deciding": {**doc["deciding"], "s5_passed": ["vantage:15m"]}}
    monkeypatch.setattr(C, "load_table", lambda: passed)
    assert dc("15m", atr=1.52, price=ERA_PRICE, broker="vantage").s5_gap_r == 0.0
    assert dc("1h", atr=3.26, price=ERA_PRICE, broker="vantage").s5_gap_r > 0


def test_every_term_of_the_deciding_cost_is_non_negative_whatever_the_inputs():
    cm = _rates(25.0)
    for side in (1, -1):
        for broker in ("icm", "vantage"):
            d = dc(atr=7.0, side=side, t_exit=T0 + 9 * 24 * H, broker=broker, costs=cm, exit_share=1.0)
            assert min(d.today_r, d.bp_engine_r, d.overshoot_r, d.swap_r, d.s5_gap_r) >= 0
            assert d.r >= max(d.today_r, d.bp_engine_r)


# ------------------------------------------------------------------ fail closed
def test_the_deciding_cost_fails_closed_on_unpinned_inputs():
    with pytest.raises(C.MissingCost):
        C.deciding_cost("XAGUSD", "15m", side=1, price=30.0, atr_decision=0.1, stop_atr=1.0, t_fill_ns=T0,
                        t_exit_ns=T0 + H, stop_exit_share=0.0, stop_entry_share=0.0, window="explore")
    with pytest.raises(C.MissingCost):
        dc("1m", atr=2.0)
    with pytest.raises(C.MissingCost):
        dc(atr=7.0, broker="unknown-broker")
    with pytest.raises(ValueError):
        dc(atr=0.0)
    with pytest.raises(ValueError):
        C.deciding_cost("XAUUSD", "15m", side=1, price=4068.7, atr_decision=7.0, stop_atr=1.0, t_fill_ns=T0,
                        t_exit_ns=T0 + H, stop_exit_share=0.0, stop_entry_share=0.0, window="holdout")  # type: ignore[arg-type]


def test_a_pin_version_mismatch_between_code_and_config_stops_the_run(monkeypatch):
    doc = C.load_table()
    stale = {**doc, "deciding": {**doc["deciding"], "pin": "atr-pin-2026-11-09.1"}}
    monkeypatch.setattr(C, "load_table", lambda: stale)
    with pytest.raises(C.MissingCost, match="atr-pin"):
        dc(atr=7.0)


def test_cost_sensitive_tags_a_cell_whose_net_is_negative_under_the_stress_line():
    assert C.cost_sensitive(-0.01) and not C.cost_sensitive(0.0) and not C.cost_sensitive(0.2)


def test_regenerating_the_measured_table_keeps_the_hand_maintained_deciding_block(tmp_path, monkeypatch):
    cfg = tmp_path / "catalogue_costs.yaml"
    cfg.write_text(REPO_TABLE.read_text())
    measured = {"cost_version": "v-test", "rule": "r", "benchmarks": {"USD": {"pct": 4.0}}, "instruments": [
        {"instrument": "XAUUSD", "broker": "icm", "spread": {"median_bps": 0.2, "days": 20, "window": "w"},
         "commission": {"per_side_bps": 0.1}, "financing": {}}]}
    src = tmp_path / "m.json"
    src.write_text(json.dumps(measured))
    monkeypatch.setattr(C, "TABLE_PATH", cfg)
    C.main([str(src)])
    out, committed = cfg.read_text(), REPO_TABLE.read_text()
    assert "version: v-test" in out
    assert out[out.index(C.DECIDING_MARKER):] == committed[committed.index(C.DECIDING_MARKER):]


# ------------------------------------------------------------------ the realised-field lint
def test_filtering_on_realised_cost_manufactures_an_edge_from_a_random_null():
    """The microstructure trap (EVAL-2 section 4: a realised-cost filter turned a random 5m null from -0.28 R into
    +0.21 R). Stop-outs pay the overshoot, so low realised cost selects the winners: a synthetic random null with a
    negative mean turns positive once trades are kept by realised cost."""
    rng = np.random.default_rng(7)
    n = 20_000
    win = rng.random(n) < 0.42                                    # random entries, 1.0 R target vs 1.0 R stop
    gross = np.where(win, 1.0, -1.0)
    cost_r = 0.098 + np.where(win, 0.0, 0.038) + rng.normal(0, 0.005, n)   # 5m RT and stop-out R from the pin
    net = gross - cost_r
    kept = cost_r < np.median(cost_r)
    assert net.mean() < -0.2 and net[kept].mean() > 0.2
    card = {"family": "NULL-RAND", "tf": "5m", "filters": [{"type": "max_cost", "params": {"field": "cost_r",
                                                                                           "max": 0.12}}]}
    with pytest.raises(L.RealisedFieldFilter, match="cost_r"):
        L.assert_decision_time(card)


@pytest.mark.parametrize("card,field", [
    ({"filters": [{"type": "realised_cost_below", "params": {"max_r": 0.1}}]}, "realised_cost_below"),
    ({"gates": {"admission": "overshoot_usd < 0.1"}}, "overshoot_usd"),
    ({"filters": [{"type": "exit_price_above_vwap"}]}, "exit_price_above_vwap"),
    ({"regime": {"field": "pnl_prev_trade", "levels": 3}}, "pnl_prev_trade"),
    ({"cells": [{"where": {"net_r": {"gt": 0}}}]}, "net_r"),
    ({"filters": [{"type": "slippage_cap", "params": {}}]}, "slippage_cap"),
    ({"filters": [{"type": "x", "params": {"label_fill_r": 0}}]}, "label_fill_r"),
])
def test_the_lint_refuses_any_filter_or_gate_on_a_realised_trade_field(card, field):
    problems = L.lint_card(card)
    assert problems and any(field in p for p in problems)
    with pytest.raises(L.RealisedFieldFilter):
        L.assert_decision_time(card)


def test_the_lint_allows_decision_time_cost_and_ordinary_filters():
    card = {
        "family": "C03", "tf": "30m", "statistic": "net_r", "exit": {"type": "target_r", "params": {"r": 1.5}},
        "filters": [{"type": "trend", "params": {"tf": "1d", "ema_n": 50}},
                    {"type": "room_to_level", "params": {"min_r": 2.0}},
                    {"type": "max_cost", "params": {"field": "deciding_cost_r", "max": 0.15}},
                    {"type": "spread_cap", "params": {"field": "decision_spread", "max": 0.3}}],
        "admission": {"rt_over_stop_max": 0.15, "expected_gross_vs_deciding_cost": 3.0},
        "regime": {"field": "atr_pct_rank", "levels": 3},
    }
    assert L.lint_card(card) == []
    L.assert_decision_time(card)


def test_realised_fields_outside_filters_and_gates_are_not_flagged():
    """The outcome is what a card measures and its exit rule defines exits: neither is a filter."""
    card = {"statistic": "excess_r", "report": ["net_r", "cost_r", "overshoot", "exit_price"],
            "exit": {"type": "target_r"}}
    assert L.lint_card(card) == []
