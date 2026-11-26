"""ADR 0085 power-first rule (goldbot/research/power_first.py), as re-ruled by the quant-reviewer on 2026-11-15: "a
card starts only if every power_g >= 0.5, simulated joint power >= 0.25, and n_eff >= n_eff_floor (200) at each
fresh-data gate"; delta = 0.10 R fixed, the goal's edge an info field that never gates."""
from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd
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
         gates: list[P.Gate] | None = None, segments: list[P.Segment] | None = None,
         hold_days: float = 1.0 / 24) -> P.PowerCard:
    return P.PowerCard(
        card_id="C-1", family="fam", timeframe=tf, learn_signals=learn_signals, hold_days=hold_days,
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
    assert P.design_effect(1.0, 0.25, 1, 0.5, concurrency=1.0) == 1.0
    assert P.design_effect(0.4, 0.25, 1, 0.5, concurrency=0.3) == 1.0
    assert P.design_effect(3.0, 0.25, 10, 0.5, concurrency=1.0) == pytest.approx(1.5 * 5.5)
    fx = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "US500", "GER40", "XAUUSD", "XAGUSD", "WTI", "UK100"]
    v = P.evaluate(card(learn_signals=3_000, clusters=1_000, instruments=fx), SETTINGS)
    assert v.rho_instrument == PF.rho_instrument == 0.5
    assert v.design_effect == pytest.approx((1 + 2 * PF.rho_day) * (1 + 9 * 0.5))
    assert next(g for g in v.gates if g.name == "explore").n_eff == pytest.approx(3_000 / v.design_effect)


# ------------------------------------------------------------------------------------------------ hold overlap
DAYS8 = round(8 * PF.trading_days_per_year)         # a signal every trading day of an 8-year learn window


def test_n_eff_also_divides_by_the_mean_concurrency_of_overlapping_holds():
    """A decision every day held H days: lag-j autocorrelation 1 - j/H, so n_eff ~ n / H (Hansen & Hodrick 1980)."""
    assert P.design_effect(1.0, 0.25, 1, 0.5, concurrency=20.0) == pytest.approx(20.0)
    assert P.design_effect(3.0, 0.25, 10, 0.5, concurrency=4.0) == pytest.approx(1.5 * 4.0 * 5.5)
    flat = P.evaluate(card(learn_signals=DAYS8, tf="1d", hold_days=1.0), SETTINGS)
    held = P.evaluate(card(learn_signals=DAYS8, tf="1d", hold_days=20.0), SETTINGS)
    assert held.overlap_effect == pytest.approx(20.0) and flat.overlap_effect == 1.0
    assert held.design_effect == pytest.approx(20.0 * flat.design_effect)
    for g, f in zip(held.gates, flat.gates, strict=True):
        assert g.n_expected == f.n_expected and g.n_eff == pytest.approx(f.n_eff / 20.0)


def test_a_daily_card_with_20_day_overlapping_holds_is_refused_where_counting_each_hold_once_would_start_it():
    """The defect (daily researcher, 2026-11-25): m_day is 1 for a daily card, so 20-day holds were credited n_eff ~ 20
    times too large and the card started."""
    segs = [P.Segment(name="learn", years=8.0), P.Segment(name="test_old", years=8.0)]
    one = P.evaluate(card(learn_signals=DAYS8, tf="1d", segments=segs, hold_days=1.0), SETTINGS)
    assert one.started
    v = P.evaluate(card(learn_signals=DAYS8, tf="1d", segments=segs, hold_days=20.0), SETTINGS)
    assert not v.started and any("n_eff" in r for r in v.reasons)


def test_holds_short_or_sparse_enough_that_under_one_label_is_open_change_nothing():
    """Little's law on signals (quant C1): c = signals per instrument per trading day x hold_days. Below one open
    label on average the overlap term is 1; at 1.49 signals a day a 1-day hold is no longer free."""
    base = P.evaluate(card(learn_signals=3_000, clusters=1_000), SETTINGS)
    for hold in (1.0 / 24, 0.5):
        assert P.evaluate(card(learn_signals=3_000, clusters=1_000, hold_days=hold), SETTINGS) == base
    day = P.evaluate(card(learn_signals=3_000, clusters=1_000, hold_days=1.0), SETTINGS)
    c = 3_000 / (8 * PF.trading_days_per_year)
    assert day.overlap_effect == pytest.approx(c)
    assert day.design_effect == pytest.approx((1 + (3 / c - 1) * PF.rho_day) * c)
    quarter = DAYS8 // 4                                                    # a signal day in four
    assert P.evaluate(card(learn_signals=quarter, tf="1d", hold_days=3.0), SETTINGS).overlap_effect == 1.0
    assert P.evaluate(card(learn_signals=quarter, tf="1d", hold_days=8.0), SETTINGS).overlap_effect == \
        pytest.approx(2.0, rel=1e-3)


@pytest.mark.parametrize("hold_min", [60, 240])
@pytest.mark.parametrize("rho", [0.25, 0.05])
def test_q3_count_only_matches_measured_uniqueness_on_even_arrivals(hold_min, rho):
    """Quant C1: on even arrivals of signals within and across days the count-only expectation is Q3's measured
    n_eff within 10 % (77 labels a day, every day; holds of 60 and 240 min; rho_day 0.25 and 0.05)."""
    days = pd.bdate_range("2012-01-02", periods=250, tz="UTC")
    offs = pd.to_timedelta(np.arange(77) * (1440 / 77), unit="min")
    entries = pd.DatetimeIndex([d + o for d in days for o in offs])
    hold = pd.Timedelta(minutes=hold_min)
    u = P.uniqueness(_events(entries, hold))
    pf = PF.model_copy(update={"rho_day": rho, "rho_day_le_60m": rho})     # unvalidated copy: rho at both horizons
    s = SETTINGS.model_copy(update={"research": SETTINGS.research.model_copy(update={"power_first": pf})})
    years = len(days) / PF.trading_days_per_year
    measured = P.evaluate(_ucard(u, hold_days=hold_min / 1440, years=years), s)
    count = P.evaluate(card(learn_signals=u.n, clusters=u.days, learn_years=years, tf="1d",
                            hold_days=hold_min / 1440), s)
    for gm, gc in zip(measured.gates, count.gates, strict=True):
        assert gc.n_eff == pytest.approx(gm.n_eff, rel=0.10)


def test_concurrency_is_counted_per_instrument_so_pooling_instruments_does_not_inflate_it():
    """Overlap between instruments is rho_inst's; the overlap term reads signal days per instrument."""
    metals4 = ["XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD"]
    v = P.evaluate(card(learn_signals=4 * DAYS8, tf="1d", instruments=metals4, hold_days=5.0), SETTINGS)
    assert v.overlap_effect == pytest.approx(5.0)


def test_a_card_must_state_its_hold():
    with pytest.raises(ValidationError, match="hold_days"):
        P.PowerCard(**{k: v for k, v in card().model_dump().items() if k != "hold_days"})
    with pytest.raises(ValidationError):
        card(hold_days=0.0)


def test_a_given_verdict_that_credits_less_overlap_than_its_card_holds_is_refused(tmp_path):
    reg = TrialRegistry(tmp_path / "reg.jsonl")
    held = card(learn_signals=DAYS8, tf="1d", hold_days=20.0)
    v = P.evaluate(held, SETTINGS)
    with pytest.raises(ValueError, match="overlap"):
        P.start_or_refuse(reg, held, SETTINGS, agent_id="rd", verdict=v.model_copy(update={"design_effect": 1.0}))
    assert not P.start_or_refuse(reg, held, SETTINGS, agent_id="rd", verdict=v).started


# ------------------------------------------------------------------- measured uniqueness (quant Q3, 2026-11-25)
def _events(entries, hold: pd.Timedelta, instrument: str = "XAUUSD") -> pd.DataFrame:
    e = pd.DatetimeIndex(entries)
    return pd.DataFrame({"instrument": instrument, "entry_utc": e, "exit_utc": e + hold})


def _ucard(u: P.Uniqueness, *, hold_days: float, tf: str = "1d", years: float = 8.0) -> P.PowerCard:
    return card(learn_signals=u.n, clusters=u.days, learn_years=years, tf=tf, hold_days=hold_days).model_copy(
        update={"learn_uniqueness": u})


def test_q3_a_daily_decision_held_20_days_has_n_eff_within_5_percent_of_n_over_20():
    """Q3: n_eff = sum(u_i) / DEFF_day, u_i the average uniqueness of label i over its horizon (Lopez de Prado 2018,
    ch. 4); acceptance: daily H = 20 within +-5 % of n / 20."""
    days = pd.bdate_range("2010-01-04", "2017-12-29", tz="UTC")
    u = P.uniqueness(_events(days, pd.Timedelta(days=28)))     # 20 trading days ~ 28 calendar days
    assert u.n == len(days) and u.sum_u == pytest.approx(len(days) / 20, rel=0.05)
    v = P.evaluate(_ucard(u, hold_days=20.0), SETTINGS)
    assert v.overlap_effect == pytest.approx(20.0, rel=0.05) and v.design_effect == pytest.approx(u.n / u.sum_u)
    for g in v.gates:                                   # every gate's n_eff is its expected trades / 20, +-5 %
        assert g.n_eff == pytest.approx(g.n_expected / 20, rel=0.05)


def test_q3_one_position_at_a_time_max_open_1_leaves_n_eff_unchanged():
    """max_open = 1: no two labels overlap, every u_i = 1, so the verdict equals the count-only one."""
    days = pd.bdate_range("2010-01-04", "2017-12-29", tz="UTC")[::3]
    ev = _events(days, pd.Timedelta(hours=20))
    u = P.uniqueness(ev)
    assert u.sum_u == pytest.approx(u.n) and u.m_w == pytest.approx(1.0)
    plain = P.evaluate(card(learn_signals=u.n, clusters=u.days, tf="1d", hold_days=20 / 24), SETTINGS)
    assert P.evaluate(_ucard(u, hold_days=20 / 24), SETTINGS).gates == plain.gates


def test_q3_pooling_15m_and_30m_arms_counts_at_most_15m_alone_times_one_plus_the_30m_unique_share():
    """Q3 acceptance: pooled (15m + 30m arms on one instrument) <= 15m alone x (1 + unique share), the unique share =
    the 30m labels' time no 15m label covers, in 30m horizons, over the 15m arm's sum of uniqueness."""
    rng = np.random.default_rng(3)
    base = pd.Timestamp("2012-01-02", tz="UTC")
    m15 = np.sort(rng.choice(20_000, 3_000, replace=False)) * 15            # entry minute offsets
    m30 = np.sort(rng.choice(10_000, 2_000, replace=False)) * 30
    a = _events(base + pd.to_timedelta(m15, unit="min"), pd.Timedelta(hours=4))
    b = _events(base + pd.to_timedelta(m30, unit="min"), pd.Timedelta(hours=8))
    covered = np.zeros(400_000, bool)
    for m in m15:
        covered[m:m + 240] = True
    b_only = sum(int((~covered[m:m + 480]).sum()) for m in m30) / 480       # in 30m-arm horizons
    ua, pooled = P.uniqueness(a), P.uniqueness(pd.concat([a, b], ignore_index=True))
    share = b_only / ua.sum_u
    assert 0 < share < 1
    assert pooled.sum_u <= ua.sum_u * (1 + share) * (1 + 1e-9)
    assert pooled.n_eff_day(0.0) == pytest.approx(pooled.sum_u)
    assert pooled.sum_u < ua.sum_u + P.uniqueness(b).sum_u


def test_q3_a_clone_pair_and_a_duplicated_bet_count_once():
    days = pd.bdate_range("2012-01-02", periods=400, tz="UTC")
    one = _events(days, pd.Timedelta(hours=6))
    twice = pd.concat([one, one], ignore_index=True)                  # two arms, the same bets
    assert P.uniqueness(twice).sum_u == pytest.approx(P.uniqueness(one).sum_u)
    assert P.uniqueness(twice).n_eff_day(0.25) == pytest.approx(P.uniqueness(one).n_eff_day(0.25))


def test_q3_labels_on_different_instruments_never_share_uniqueness_rho_inst_prices_them():
    days = pd.bdate_range("2012-01-02", periods=400, tz="UTC")
    both = pd.concat([_events(days, pd.Timedelta(hours=6)), _events(days, pd.Timedelta(hours=6), "XAGUSD")])
    assert P.uniqueness(both).sum_u == pytest.approx(800)


def test_q3_the_day_term_weights_days_by_their_unique_mass():
    """DEFF_day = 1 + (m_w - 1) rho_day, m_w = sum M_d^2 / sum M_d, M_d = sum of u_i decided that day."""
    t = pd.Timestamp("2012-01-03 08:00", tz="UTC")
    ev = _events([t, t + pd.Timedelta(hours=2), t + pd.Timedelta(hours=4), t + pd.Timedelta(days=1)],
                 pd.Timedelta(hours=1))
    u = P.uniqueness(ev)
    assert (u.n, u.days) == (4, 2) and u.sum_u == pytest.approx(4.0)
    assert u.m_w == pytest.approx((9 + 1) / 4) and u.n_eff_day(0.25) == pytest.approx(4 / (1 + 1.5 * 0.25))


def test_a_card_whose_measured_uniqueness_disagrees_with_its_counts_is_invalid():
    u = P.uniqueness(_events(pd.bdate_range("2012-01-02", periods=50, tz="UTC"), pd.Timedelta(hours=1)))
    raw = card(learn_signals=60, clusters=50, hold_days=0.1).model_dump()
    with pytest.raises(ValidationError, match="uniqueness"):
        P.PowerCard.model_validate(raw | {"learn_uniqueness": u.model_dump()})
    ok = card(learn_signals=50, clusters=50, hold_days=0.1).model_dump() | {"learn_uniqueness": u.model_dump()}
    assert P.PowerCard.model_validate(ok).learn_uniqueness == u


def test_a_label_must_end_after_it_starts():
    t = pd.Timestamp("2012-01-03", tz="UTC")
    with pytest.raises(ValueError, match="exit"):
        P.uniqueness(pd.DataFrame({"instrument": ["XAUUSD"], "entry_utc": [t], "exit_utc": [t]}))


# ------------------------------------------------------------------- rho_day per horizon (quant Q1, 2026-11-25)
def test_q1_rho_day_for_horizons_up_to_60_minutes_may_go_down_to_0_05_longer_keep_the_0_25_floor(tmp_path):
    ok, sha = rho_file(tmp_path)
    assert _settings(0.05, str(ok), sha).rho_day_le_60m == 0.05
    for bad in ({"rho_day_le_60m": 0.04, "rho_day_measurement": str(ok), "rho_day_measurement_sha256": sha},
                {"rho_day": 0.2}):
        with pytest.raises(ValidationError):
            PowerFirstSettings.model_validate(bad)
    assert PF.rho_day_le_60m == PF.rho_day == 0.25          # shipped: the prior until the Q1 measurement lands
    pf = PF.model_copy(update={"rho_day_le_60m": 0.05})
    assert pf.rho_day_for(60 / 1440) == 0.05 and pf.rho_day_for(61 / 1440) == 0.25 and pf.rho_day_for(1.0) == 0.25
    s = SETTINGS.model_copy(update={"research": SETTINGS.research.model_copy(update={"power_first": pf})})
    short = card(learn_signals=3_000, clusters=1_000, hold_days=1 / 24)
    assert P.evaluate(short, s).design_effect == pytest.approx(1 + 2 * 0.05)
    assert P.evaluate(card(learn_signals=3_000, clusters=1_000, hold_days=2 / 24), s).design_effect == \
        pytest.approx(1 + 2 * 0.25)


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


# ------------------------------------------------------------- D-12 measurement file (quant ruling (c) on #466)
MEASURE_SCRIPT = "scripts/research/measure_rho.py"
MEASURE_JSON = "docs/research/rho-measurement-2026-11.json"
CLASSES = ("gold", "metals", "fx10", "pooled", "gold+fx10")


def _sha(path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rho_file(tmp_path, hi95=None, **over):
    """A valid D-12 measurement file and its cells file (two replayed non-sr-v3/H-102 families, learn window, every
    class bound 0.05 recomputable from the cells), with `over` replacing top-level keys. Returns (path, sha256)."""
    import gzip
    import json
    hi95 = hi95 or dict.fromkeys(CLASSES, 0.01)
    rows = ["kind,class,band,outcome,within_day.hi95"]
    rows += [f"out,{c},le60m,long_net,{v}" for c, v in hi95.items()]
    rows += ["out,gold,1h-4h,long_net,0.9", "out,gold,le60m,both_gross,0.9", "feat,gold,,,0.9"]   # never binding
    cells = tmp_path / "cells.csv.gz"
    cells.write_bytes(gzip.compress(("\n".join(rows) + "\n").encode(), mtime=0))
    d = {"schema": "rho_day_measurement/v2",
         "window": {"name": "learn", "from": "2010-01-01 00:00:00+00:00", "to_exclusive": "2018-01-01 00:00:00+00:00"},
         "script": {"path": MEASURE_SCRIPT, "sha256": "not checked at load"},
         "candidates": [{"kind": "control", "name": "control_grid"},
                        {"kind": "family", "name": "box_break_close", "arms": ["MB15", "W2"]},
                        {"kind": "family", "name": "sweep_absorb", "arms": ["MSA15"]}],
         "cells_file": {"path": str(cells), "sha256": _sha(cells)},
         "rho_day_le_60m": {c: max(0.05, v) for c, v in hi95.items()}} | over
    f = tmp_path / "rho.json"
    f.write_text(json.dumps(d))
    return f, _sha(f)


def _settings(value, path, sha):
    return PowerFirstSettings.model_validate({"rho_day_le_60m": value, "rho_day_measurement": path,
                                              "rho_day_measurement_sha256": sha})


def _refused(value, path, sha, match):
    with pytest.raises(ValidationError, match=match):
        _settings(value, path, sha)


def test_d12_a_valid_pinned_measurement_admits_rho_day_le_60m_at_0_05(tmp_path):
    f, sha = rho_file(tmp_path)
    pf = _settings(0.05, str(f), sha)
    assert pf.rho_day_for(60 / 1440) == 0.05 and pf.rho_day_for(61 / 1440) == 0.25    # > 60 min keeps 0.25


def test_d12_refused_without_a_file_or_with_a_missing_one(tmp_path):
    with pytest.raises(ValidationError, match="needs rho_day_measurement"):
        PowerFirstSettings.model_validate({"rho_day_le_60m": 0.05})
    _refused(0.05, str(tmp_path / "nope.json"), "0" * 64, "does not exist")


def test_d12_refused_unless_the_file_matches_its_pinned_sha(tmp_path):
    f, sha = rho_file(tmp_path)
    _refused(0.05, str(f), None, "rho_day_measurement_sha256")
    _refused(0.05, str(f), "0" * 64, "rho_day_measurement_sha256")


def test_d12_refused_for_a_control_grid_only_file_or_one_with_judged_families(tmp_path):
    grid = [{"kind": "control", "name": "control_grid"}]
    _refused(0.05, *map(str, rho_file(tmp_path, candidates=grid)), ">= 2 replayed families")
    judged = grid + [{"kind": "family", "name": "box_break_close", "arms": ["MB1H"]},      # an H-102 arm
                     {"kind": "family", "name": "sr-v3", "arms": []},
                     {"kind": "family", "name": "sweep_absorb", "arms": ["MSA15"]}]
    _refused(0.05, *map(str, rho_file(tmp_path, candidates=judged)), ">= 2 replayed families")


def test_d12_refused_below_the_files_binding_for_any_class(tmp_path):
    f, sha = rho_file(tmp_path, hi95=dict.fromkeys(CLASSES, 0.01) | {"fx10": 0.08})
    _refused(0.05, str(f), sha, "below the measured binding")
    assert _settings(0.08, str(f), sha).rho_day_le_60m == 0.08


@pytest.mark.parametrize("bad", [float("nan"), None, "x"])
def test_d12_refused_for_a_null_or_non_finite_class_binding(tmp_path, bad):
    """Code review of #466 (MEDIUM): {"gold": NaN, ...} or {"gold": null, ...} must not admit 0.05."""
    import json
    f, _ = rho_file(tmp_path)
    d = json.loads(f.read_text())
    d["rho_day_le_60m"]["gold"] = bad
    f.write_text(json.dumps(d))            # json writes NaN as the token NaN, which json.loads reads back
    _refused(0.05, str(f), _sha(f), "missing or not finite")


def test_d12_refused_when_a_class_is_missing(tmp_path):
    f, sha = rho_file(tmp_path, rho_day_le_60m={"gold": 0.05, "fx10": 0.05})
    _refused(0.05, str(f), sha, "missing or not finite")


def test_d12_refused_when_a_binding_does_not_match_its_cells(tmp_path):
    f, sha = rho_file(tmp_path, rho_day_le_60m=dict.fromkeys(CLASSES, 0.05) | {"fx10": 0.06})   # hand-edited
    _refused(0.06, str(f), sha, "differ from the cells file")


def test_d12_refused_when_the_cells_file_differs_from_its_sha(tmp_path):
    f, _ = rho_file(tmp_path)
    (tmp_path / "cells.csv.gz").write_bytes(b"changed")
    _refused(0.05, str(f), _sha(f), "cells file")


def test_d12_refused_for_an_old_schema_or_a_window_outside_learn(tmp_path):
    _refused(0.05, *map(str, rho_file(tmp_path, schema="rho_day_measurement/v1")), "v2 or later")
    learn = {"name": "learn", "from": "2010-01-01 00:00:00+00:00", "to_exclusive": "2018-01-01 00:00:00+00:00"}
    for w in (learn | {"to_exclusive": "2018-02-01 00:00:00+00:00"}, learn | {"from": "2006-02-01 00:00:00+00:00"},
              learn | {"name": "confirm"}):
        _refused(0.05, *map(str, rho_file(tmp_path, window=w)), "learn within")


def test_d12_the_committed_measurement_matches_the_committed_script_and_its_cells():
    """CI tie (code review of #466): the committed JSON records the sha256 of the committed measure_rho.py, so an edit
    of the script fails CI here (re-run it, or set rho_day_le_60m back to 0.25 in the same PR), never a service's
    settings load. The JSON is also accepted by the validator at its own binding when pinned by its own sha."""
    import json

    from goldbot.config import ROOT
    f = ROOT / MEASURE_JSON
    d = json.loads(f.read_text())
    assert d["script"]["path"] == MEASURE_SCRIPT
    assert d["script"]["sha256"] == _sha(ROOT / MEASURE_SCRIPT), "measure_rho.py changed: re-run it (ADR 0085 D-12)"
    top = max(d["rho_day_le_60m"].values())
    assert _settings(top, MEASURE_JSON, _sha(f)).rho_day_le_60m == top


def test_d12_a_pinned_setting_names_a_json_from_the_committed_script():
    """When the shipped settings pin a measurement, its recorded script sha is the committed script's."""
    import json

    from goldbot.config import ROOT
    if PF.rho_day_measurement is None:
        assert PF.rho_day_le_60m == 0.25
        return
    d = json.loads((ROOT / PF.rho_day_measurement).read_text())
    assert d["script"]["sha256"] == _sha(ROOT / d["script"]["path"])


def test_d12_the_shipped_setting_stays_the_prior():
    assert PF.rho_day_le_60m == 0.25 and PF.rho_day_measurement is None and PF.rho_day_measurement_sha256 is None


def test_d12_excluded_arms_cover_the_sr_v3_prenamed_arms():
    from goldbot.config import RHO_EXCLUDED_ARMS
    from goldbot.research.self_retrain import PRENAMED
    assert set(PRENAMED) <= RHO_EXCLUDED_ARMS


def test_d12_a_rho_inst_is_the_day_mean_correlation_not_the_pair_icc():
    """Ruling (a): with m = 89 candidates a day, rho_pair 0.0134, rho_day 0.01 and k = 10 instruments, the pooled n_eff
    from the day-mean rho_inst is within 5 % of the three-level design effect
    1 + (m - 1) rho_day + (k - 1) m rho_pair; the pair ICC in 1 + (k - 1) rho_inst overstates n_eff about 4x."""
    m, rho_pair, rho_day, k, days = 89, 0.0134, 0.01, 10, 2_000
    n = m * k * days
    three_level = 1 + (m - 1) * rho_day + (k - 1) * m * rho_pair
    day_mean = m * rho_pair / (1 + (m - 1) * rho_day)        # corr of two instruments' day-mean residuals
    n_eff = n / P.design_effect(m, rho_day, k, day_mean, concurrency=1.0)
    assert n_eff == pytest.approx(n / three_level, rel=0.05)
    n_eff_pair = n / P.design_effect(m, rho_day, k, rho_pair, concurrency=1.0)
    assert n_eff_pair > 4 * n / three_level
