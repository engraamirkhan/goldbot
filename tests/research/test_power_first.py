"""ADR 0085 power-first rule (goldbot/research/power_first.py): "a card starts only with >= 0.5 power at every gate it
must pass and >= 0.25 joint power; else a 'NOT STARTED (power)' registry row with no K and no data read"."""
from __future__ import annotations

import logging
import math

import pytest
from pydantic import ValidationError
from scipy import stats

from goldbot.config import Settings, load_settings
from goldbot.research import power_first as P
from goldbot.research.registry import TrialRegistry, n_trials_effective

SETTINGS = load_settings()
PF = SETTINGS.research.power_first


def card(*, learn_signals: int = 2_960, clusters: int | None = None, learn_years: float = 8.0, instruments: int = 1,
         tf: str = "1h", gates: list[P.Gate] | None = None, segments: list[P.Segment] | None = None) -> P.PowerCard:
    return P.PowerCard(
        card_id="C-1", family="fam", timeframe=tf, learn_signals=learn_signals,
        learn_signal_clusters=clusters if clusters is not None else learn_signals, learn_years=learn_years,
        instruments=instruments,
        segments=segments or [P.Segment(name="learn", years=learn_years), P.Segment(name="test_old", years=2.0)],
        gates=gates or [P.Gate(name="explore", segments=("learn",), z=2.0),
                        P.Gate(name="confirm", segments=("test_old",), z=P.holm_first_z(0.00625, 3))])


def edge_only(**over: float) -> Settings:
    """Settings whose goal edge never binds (huge risk budget), so the 0.10 R floor is the min edge."""
    research = SETTINGS.research.model_copy(update={"lot_fit_equity": 1e9, **over})
    return SETTINGS.model_copy(update={"research": research})


def test_the_min_edge_is_the_larger_of_the_floor_and_the_edge_the_daily_goal_needs():
    """(a) min edge = max(0.10 R, the edge the GBP 50/day goal needs at the card's trade rate)."""
    risk_gbp = SETTINGS.research.lot_fit_equity * SETTINGS.risk.risk_per_trade
    per_day = 2_960 / 8.0 / PF.trading_days_per_year
    v = P.evaluate(card(), SETTINGS)
    assert v.goal_edge_r == pytest.approx(SETTINGS.research.north_star_daily_gbp / (per_day * risk_gbp))
    assert v.min_edge_r == max(0.10, v.goal_edge_r) and v.floor_edge_r == 0.10
    floor_only = P.evaluate(card(), edge_only())
    assert floor_only.goal_edge_r < 0.10 and floor_only.min_edge_r == 0.10


def test_the_sd_comes_from_the_pre_set_timeframe_table_never_from_the_card():
    """(a) sd from a pre-set per-timeframe table; a card cannot carry one, and an unlisted timeframe is refused."""
    assert P.evaluate(card(), SETTINGS).sd_r == PF.sd_r_by_tf["1h"]
    with pytest.raises(ValidationError):
        P.PowerCard(**{**card().model_dump(), "sd_r": 0.5})
    with pytest.raises(ValueError, match="pre-set sd"):
        P.evaluate(card(tf="2h"), SETTINGS)


def test_expected_trades_on_later_gates_are_extrapolated_from_the_learn_signal_rate():
    """(b) n on a fresh window = learn signals a year x the window's years; the card has no field for counted ones."""
    v = P.evaluate(card(learn_signals=800, learn_years=8.0), edge_only())
    by = {g.name: g for g in v.gates}
    assert by["explore"].n_expected == pytest.approx(800.0) and by["confirm"].n_expected == pytest.approx(200.0)
    assert "test_old_signals" not in P.PowerCard.model_fields


def test_clustered_effective_n_charges_same_day_trades_and_pooled_instruments():
    """(c) n_eff = n / ((1 + (c - 1) rho_day) (1 + (m - 1) rho_instrument)), c = learn signals per (instrument, day)."""
    assert P.design_effect(1.0, 0.25, 1, 0.5) == 1.0
    assert P.design_effect(3.0, 0.25, 10, 0.5) == pytest.approx(1.5 * 5.5)
    v = P.evaluate(card(learn_signals=3_000, clusters=1_000, instruments=10), edge_only())
    by = {g.name: g for g in v.gates}
    de = P.design_effect(3.0, PF.rho_day, 10, PF.rho_instrument)
    assert v.design_effect == pytest.approx(de)
    assert by["explore"].n_eff == pytest.approx(3_000 / de)


def test_gate_power_is_the_one_sided_normal_power_at_the_min_edge():
    v = P.evaluate(card(), edge_only())
    g = next(x for x in v.gates if x.name == "confirm")
    assert g.power == pytest.approx(stats.norm.cdf(0.10 * math.sqrt(g.n_eff) / 1.2 - g.z))
    assert g.power_at_floor == g.power


def test_the_power_at_the_floor_is_reported_beside_the_deciding_power_when_the_goal_edge_binds():
    """ADR 0085 open item 1: at GBP 1,000 x 0.5 % risk the goal's edge (68 R a trade at 1h) binds, so the deciding
    power is ~1; the 0.10 R floor's power is recorded so the quant can see what the goal edge hides."""
    v = P.evaluate(card(), SETTINGS)
    g = next(x for x in v.gates if x.name == "confirm")
    assert v.min_edge_r == v.goal_edge_r > 1.0 and g.power > 0.99 > 0.5 > g.power_at_floor


def test_joint_power_shares_data_as_the_real_gates_do():
    """(d) two gates on the same data are one draw (joint = the stricter); on disjoint data they multiply."""
    same = card(gates=[P.Gate(name="a", segments=("learn",), z=2.0), P.Gate(name="b", segments=("learn",), z=2.0)])
    v = P.evaluate(same, edge_only())
    assert v.joint_power == pytest.approx(v.gates[0].power, abs=0.01)
    apart = card(segments=[P.Segment(name="learn", years=8.0), P.Segment(name="fresh", years=8.0)],
                 gates=[P.Gate(name="a", segments=("learn",), z=2.0), P.Gate(name="b", segments=("fresh",), z=2.0)])
    w = P.evaluate(apart, edge_only())
    assert w.joint_power == pytest.approx(w.gates[0].power * w.gates[1].power, abs=0.015)
    pooled = card(segments=[P.Segment(name="learn", years=8.0), P.Segment(name="fresh", years=8.0)],
                  gates=[P.Gate(name="a", segments=("learn",), z=2.0),
                         P.Gate(name="b", segments=("learn", "fresh"), z=2.0)])
    assert P.evaluate(pooled, edge_only()).gates[1].n_expected == pytest.approx(2 * 2_960)


def test_a_card_starts_with_enough_power_at_every_gate_and_jointly():
    v = P.evaluate(card(learn_signals=20_000), edge_only())
    assert v.started and v.reasons == [] and all(g.power >= 0.5 for g in v.gates) and v.joint_power >= 0.25


def test_a_gate_below_half_power_refuses_the_card_and_names_the_gate():
    """The memo's pooled 1h family: 740 TEST-OLD trades at 0.10 R has about 0.29 power at the confirm gate."""
    v = P.evaluate(card(learn_signals=2_960), edge_only())
    assert not v.started
    assert any("confirm" in r and "power" in r for r in v.reasons)


def test_every_gate_above_half_but_joint_below_a_quarter_refuses_the_card():
    gates = [P.Gate(name=f"g{i}", segments=(f"s{i}",), z=2.0) for i in range(3)]
    segs = [P.Segment(name=f"s{i}", years=8.0) for i in range(3)]
    # each gate near 0.55 power on independent data: joint ~0.17
    n = ((2.0 + stats.norm.ppf(0.55)) * 1.2 / 0.10) ** 2
    v = P.evaluate(card(learn_signals=round(n), segments=segs, gates=gates), edge_only())
    assert all(g.power >= 0.5 for g in v.gates) and v.joint_power < 0.25
    assert not v.started and any("joint" in r for r in v.reasons)


def test_a_refused_card_writes_a_not_started_row_with_no_k_and_logs_it(tmp_path, caplog):
    """(e) every refusal is logged, and the registry row costs no K: no look happened."""
    reg = TrialRegistry(tmp_path / "reg.jsonl")
    reg.record(agent_id="a", family="other", config={"x": 1}, feature_version="f", rationale="r", results={})
    k0 = reg.n_trials_effective
    with caplog.at_level(logging.WARNING, logger="goldbot.research.power_first"):
        v = P.start_or_refuse(reg, card(learn_signals=2_960), edge_only(), agent_id="research-director")
    assert not v.started
    rows = reg._rows()
    assert rows[-1]["status"] == P.NOT_STARTED_POWER and rows[-1]["family"] == "fam"
    assert rows[-1]["results"]["power_first"]["started"] is False
    assert reg.n_trials_effective == k0 == n_trials_effective(rows)
    assert "NOT STARTED (power)" in caplog.text and "C-1" in caplog.text


def test_a_started_card_writes_nothing(tmp_path):
    reg = TrialRegistry(tmp_path / "reg.jsonl")
    assert P.start_or_refuse(reg, card(learn_signals=20_000), edge_only(), agent_id="rd").started
    assert reg._rows() == []


def test_the_thresholds_can_only_tighten():
    from goldbot.config import PowerFirstSettings
    for bad in ({"min_gate_power": 0.4}, {"min_joint_power": 0.2}, {"min_edge_floor_r": 0.05}):
        with pytest.raises(ValidationError):
            PowerFirstSettings.model_validate(bad)


def test_a_card_with_no_learn_signals_is_refused():
    v = P.evaluate(card(learn_signals=0, clusters=0), SETTINGS)
    assert not v.started and any("no learn signals" in r for r in v.reasons)


def test_a_gate_naming_an_unknown_window_is_invalid():
    with pytest.raises(ValidationError, match="unknown segment"):
        card(gates=[P.Gate(name="x", segments=("holdout",), z=2.0)])
