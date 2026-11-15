"""ADR 0085 power-first rule (goldbot/research/power_first.py), as re-ruled by the quant-reviewer on 2026-11-15: "a
card starts only if every power_g >= 0.5, simulated joint power >= 0.25, and n_eff >= n_eff_floor (200) at each
fresh-data gate"; delta = 0.10 R fixed, the goal's edge an info field that never gates."""
from __future__ import annotations

import logging
import math

import pytest
from pydantic import ValidationError
from scipy import stats

from goldbot.config import PowerFirstSettings, Settings, load_settings
from goldbot.research import power_first as P
from goldbot.research.registry import TrialRegistry, n_trials_effective

SETTINGS = load_settings()
PF = SETTINGS.research.power_first
METALS10 = ["XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD"] * 2 + ["XAUEUR", "XAGEUR"]
HOLM_Z = P.holm_first_z(0.00625, 3)


def card(*, learn_signals: int = 2_960, clusters: int | None = None, learn_years: float = 8.0,
         instruments: list[str] | None = None, tf: str = "1h", test_old_years: float = 2.0,
         gates: list[P.Gate] | None = None, segments: list[P.Segment] | None = None) -> P.PowerCard:
    return P.PowerCard(
        card_id="C-1", family="fam", timeframe=tf, learn_signals=learn_signals,
        learn_signal_clusters=clusters if clusters is not None else learn_signals, learn_years=learn_years,
        instruments=instruments or ["XAUUSD"],
        segments=segments or [P.Segment(name="learn", years=learn_years),
                              P.Segment(name="test_old", years=test_old_years)],
        gates=gates or [P.Gate(name="explore", segments=("learn",), z=2.0),
                        P.Gate(name="confirm", segments=("test_old",), z=HOLM_Z, fresh=True)])


def with_pf(**over: object) -> Settings:
    research = SETTINGS.research.model_copy(update={"power_first": PF.model_copy(update=over)})
    return SETTINGS.model_copy(update={"research": research})


def with_goal(**over: float) -> Settings:
    research = SETTINGS.research.model_copy(update=over)
    return SETTINGS.model_copy(update={"research": research})


# ------------------------------------------------------------------------------------------------ the closing test
def test_a_1h_card_pooled_over_10_metals_with_740_test_old_trades_is_refused_at_delta_0_10():
    v = P.evaluate(card(instruments=METALS10), SETTINGS)
    confirm = next(g for g in v.gates if g.name == "confirm")
    assert confirm.n_expected == pytest.approx(740.0)
    assert not v.started and confirm.power < 0.5


def test_the_same_card_at_delta_0_20_is_accepted_only_if_its_fresh_n_eff_passes():
    s = with_pf(delta_r=0.20)
    short = P.evaluate(card(instruments=METALS10), s)              # n_eff 740 / 7.3 ~ 101 < 200
    assert not short.started and any("n_eff" in r for r in short.reasons)
    long = P.evaluate(card(instruments=METALS10, test_old_years=6.0), s)    # n_eff ~ 304
    assert all(g.n_eff >= PF.n_eff_floor for g in long.gates if g.fresh) and long.started


def test_changing_the_goal_edge_never_changes_the_verdict():
    base = P.evaluate(card(instruments=METALS10), SETTINGS)
    for over in ({"north_star_daily_gbp": 5_000.0}, {"lot_fit_equity": 1e9}, {"north_star_daily_gbp": 0.01}):
        v = P.evaluate(card(instruments=METALS10), with_goal(**over))
        assert v.goal_edge_r != base.goal_edge_r
        assert (v.started, v.reasons, v.gates, v.joint_power) == (base.started, base.reasons, base.gates,
                                                                   base.joint_power)


# ------------------------------------------------------------------------------------------------ each condition
def test_delta_is_fixed_and_the_goal_edge_is_recorded_as_information_only():
    risk_gbp = SETTINGS.research.lot_fit_equity * SETTINGS.risk.risk_per_trade
    per_day = 2_960 / 8.0 / PF.trading_days_per_year
    v = P.evaluate(card(), SETTINGS)
    assert v.delta_r == 0.10
    assert v.goal_edge_r == pytest.approx(SETTINGS.research.north_star_daily_gbp / (per_day * risk_gbp))
    assert "power_at_floor" not in P.GatePower.model_fields and "min_edge_r" not in P.PowerVerdict.model_fields


def test_the_sd_is_the_larger_of_1_2_and_the_demo_measured_sd_never_the_cards_own():
    assert P.evaluate(card(), SETTINGS).sd_r == max(1.2, PF.sd_r_measured_by_tf.get("1h") or 0.0)
    assert P.evaluate(card(), with_pf(sd_r_measured_by_tf={"1h": 1.51})).sd_r == 1.51
    assert P.evaluate(card(), with_pf(sd_r_measured_by_tf={"1h": 1.10})).sd_r == 1.2
    with pytest.raises(ValidationError):
        P.PowerCard(**{**card().model_dump(), "sd_r": 0.5})
    with pytest.raises(ValueError, match="pre-set sd"):
        P.evaluate(card(tf="2h"), SETTINGS)


def test_expected_trades_on_later_gates_are_extrapolated_from_the_learn_signal_rate():
    v = P.evaluate(card(learn_signals=800, learn_years=8.0), SETTINGS)
    by = {g.name: g for g in v.gates}
    assert by["explore"].n_expected == pytest.approx(800.0) and by["confirm"].n_expected == pytest.approx(200.0)
    assert "test_old_signals" not in P.PowerCard.model_fields


def test_n_eff_divides_by_the_day_cluster_and_the_instrument_design_effects():
    """n_eff = n / (1 + (m_day - 1) rho_day) / (1 + (k - 1) rho_inst); m_day floored at 1."""
    assert P.design_effect(1.0, 0.25, 1, 0.5) == 1.0
    assert P.design_effect(0.4, 0.25, 1, 0.5) == 1.0
    assert P.design_effect(3.0, 0.25, 10, 0.5) == pytest.approx(1.5 * 5.5)
    fx = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "US500", "GER40", "XAUUSD", "XAGUSD", "WTI", "UK100"]
    v = P.evaluate(card(learn_signals=3_000, clusters=1_000, instruments=fx), SETTINGS)
    assert v.rho_instrument == PF.rho_instrument == 0.5
    assert v.design_effect == pytest.approx((1 + 2 * PF.rho_day) * (1 + 9 * 0.5))
    assert next(g for g in v.gates if g.name == "explore").n_eff == pytest.approx(3_000 / v.design_effect)


def test_rho_inst_is_0_7_when_at_least_half_the_pooled_instruments_are_precious_metals():
    half = ["XAUUSD", "XAGUSD", "EURUSD", "US500"]
    assert P.evaluate(card(instruments=half), SETTINGS).rho_instrument == 0.7
    assert P.evaluate(card(instruments=["XPTUSD", "EURUSD", "US500"]), SETTINGS).rho_instrument == 0.5
    assert P.evaluate(card(instruments=METALS10), SETTINGS).design_effect == pytest.approx(1 + 9 * 0.7)


def test_gate_power_is_phi_of_delta_root_n_eff_over_sd_minus_the_gates_holm_rank_1_z():
    v = P.evaluate(card(), SETTINGS)
    g = next(x for x in v.gates if x.name == "confirm")
    assert g.z == pytest.approx(stats.norm.isf(0.00625 / 3))
    assert g.power == pytest.approx(stats.norm.cdf(0.10 * math.sqrt(g.n_eff) / v.sd_r - g.z))


def test_joint_power_shares_data_as_the_real_gates_do():
    same = card(gates=[P.Gate(name="a", segments=("learn",), z=2.0), P.Gate(name="b", segments=("learn",), z=2.0)])
    v = P.evaluate(same, SETTINGS)
    assert v.joint_power == pytest.approx(v.gates[0].power, abs=0.01)
    apart = card(segments=[P.Segment(name="learn", years=8.0), P.Segment(name="fresh", years=8.0)],
                 gates=[P.Gate(name="a", segments=("learn",), z=2.0), P.Gate(name="b", segments=("fresh",), z=2.0)])
    w = P.evaluate(apart, SETTINGS)
    assert w.joint_power == pytest.approx(w.gates[0].power * w.gates[1].power, abs=0.015)
    pooled = card(segments=[P.Segment(name="learn", years=8.0), P.Segment(name="fresh", years=8.0)],
                  gates=[P.Gate(name="a", segments=("learn",), z=2.0),
                         P.Gate(name="b", segments=("learn", "fresh"), z=2.0)])
    assert P.evaluate(pooled, SETTINGS).gates[1].n_expected == pytest.approx(2 * 2_960)


def test_a_card_starts_with_enough_power_at_every_gate_jointly_and_enough_fresh_n_eff():
    v = P.evaluate(card(learn_signals=20_000), SETTINGS)
    assert v.started and v.reasons == [] and all(g.power >= 0.5 for g in v.gates) and v.joint_power >= 0.25


def test_the_n_eff_floor_binds_only_at_fresh_data_gates():
    """At delta 0.50 every gate is powered, but 400 TEST-OLD trades over 4 metals (n_eff ~ 129) fail the floor; the
    learn gate (n_eff ~ 516) is not a fresh-data test."""
    s = with_pf(delta_r=0.50)
    v = P.evaluate(card(learn_signals=1_600, instruments=["XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD"]), s)
    assert all(g.power >= 0.5 for g in v.gates)
    assert not v.started and v.reasons == [r for r in v.reasons if "confirm" in r and "n_eff" in r]


def test_every_gate_above_half_but_joint_below_a_quarter_refuses_the_card():
    gates = [P.Gate(name=f"g{i}", segments=(f"s{i}",), z=2.0) for i in range(3)]
    segs = [P.Segment(name=f"s{i}", years=8.0) for i in range(3)]
    n = ((2.0 + stats.norm.ppf(0.55)) * P.evaluate(card(), SETTINGS).sd_r / 0.10) ** 2
    v = P.evaluate(card(learn_signals=round(n), segments=segs, gates=gates), SETTINGS)
    assert all(g.power >= 0.5 for g in v.gates) and v.joint_power < 0.25
    assert not v.started and any("joint" in r for r in v.reasons)


def test_a_refused_card_writes_a_not_started_row_with_no_k_and_logs_it(tmp_path, caplog):
    reg = TrialRegistry(tmp_path / "reg.jsonl")
    reg.record(agent_id="a", family="other", config={"x": 1}, feature_version="f", rationale="r", results={})
    k0 = reg.n_trials_effective
    with caplog.at_level(logging.WARNING, logger="goldbot.research.power_first"):
        v = P.start_or_refuse(reg, card(instruments=METALS10), SETTINGS, agent_id="research-director")
    assert not v.started
    rows = reg._rows()
    assert rows[-1]["status"] == P.NOT_STARTED_POWER and rows[-1]["family"] == "fam"
    assert rows[-1]["results"]["power_first"]["started"] is False
    assert reg.n_trials_effective == k0 == n_trials_effective(rows)
    assert "NOT STARTED (power)" in caplog.text and "C-1" in caplog.text


def test_a_started_card_writes_nothing(tmp_path):
    reg = TrialRegistry(tmp_path / "reg.jsonl")
    assert P.start_or_refuse(reg, card(learn_signals=20_000), SETTINGS, agent_id="rd").started
    assert reg._rows() == []


def test_the_thresholds_can_only_tighten():
    for bad in ({"min_gate_power": 0.4}, {"min_joint_power": 0.2}, {"delta_r": 0.05}, {"n_eff_floor": 150},
                {"sd_r_floor": 1.0}):
        with pytest.raises(ValidationError):
            PowerFirstSettings.model_validate(bad)


def test_a_card_with_no_learn_signals_is_refused():
    v = P.evaluate(card(learn_signals=0, clusters=0), SETTINGS)
    assert not v.started and any("no learn signals" in r for r in v.reasons)


def test_a_gate_naming_an_unknown_window_is_invalid():
    with pytest.raises(ValidationError, match="unknown segment"):
        card(gates=[P.Gate(name="x", segments=("holdout",), z=2.0)])


def test_a_not_started_row_takes_no_trial_number_so_no_trial_lookup_returns_it(tmp_path):
    """Trading-safety review LOW: trial(n) must never return a refusal."""
    reg = TrialRegistry(tmp_path / "reg.jsonl")
    P.start_or_refuse(reg, card(instruments=METALS10), SETTINGS, agent_id="rd")
    trial = reg.record(agent_id="a", family="other", config={"x": 1}, feature_version="f", rationale="r", results={})
    refused = next(r for r in reg._rows() if r["status"] == P.NOT_STARTED_POWER)
    assert refused["trial"] is None and trial["trial"] == 1 and reg.row(1) == trial


def test_the_registry_accepts_a_not_started_row_only_as_written_by_start_or_refuse(tmp_path):
    """Trading-safety review LOW: the status needs results.power_first.started False and the card."""
    reg = TrialRegistry(tmp_path / "reg.jsonl")
    for results in ({}, {"power_first": {"started": True}, "card": {"card_id": "x"}},
                    {"power_first": {"started": False}}):
        with pytest.raises(ValueError, match="start_or_refuse"):
            reg.record(agent_id="a", family="f", config={}, feature_version="f", rationale="r", results=results,
                       status=P.NOT_STARTED_POWER)
    assert reg._rows() == []


def test_a_registry_merge_keeps_a_not_started_row_without_a_trial_number(tmp_path):
    from goldbot.research.registry_sync import merge_rows
    reg = TrialRegistry(tmp_path / "reg.jsonl")
    reg.record(agent_id="a", family="other", config={"x": 1}, feature_version="f", rationale="r", results={})
    P.start_or_refuse(reg, card(instruments=METALS10), SETTINGS, agent_id="rd")
    merged = merge_rows(reg._rows())
    assert [r["trial"] for r in merged if r["status"] == P.NOT_STARTED_POWER] == [None]
