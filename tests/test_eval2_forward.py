"""EVAL-2 Lane 5A, L4 forward demo conformance (docs/research/eval2-method-2026-11-09.md section 2, L4 row; prereg
section 4.9) and the S5 read (section 6).

"(i) Execution e-process: realised cost <= modelled + 0.02 R, paired fill vs replay v2, per broker and leg.
Kill-only. (ii) Exit-mix check: stop/target/time shares vs the history Dirichlet, ~50 trades. (iii) Futility
e-process: e >= 10 against H1: excess >= max(0.05 R, 1/2 x L3 shrunk estimate), after >= 100 signals, gives KILLED.
(iv) Edge e-process vs H0: excess <= 0. Reported. e >= 20 = 'proven forward'. ON TRACK = (i)-(iii) green after >= 50
fills per family." "Ville bound <= 5 %." "`ForwardState` reads count only when `s5_passed` is true for the broker and
TF band." S5: "|bias| <= 0.02 R", per broker and timeframe band, gold only. Quant vantage ruling 2026-11-09: "S5 on
vantage is read on the entry leg (fill vs replay, spread) only ... Vantage S5 cannot be green on the exit leg from
demo data.\""""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.research.eval2 import forward as fw

RNG_SEED = 20261109
PATHS = 2000


def _paths(mean: float, sd: float, n: int, *, heavy: bool = False, seed: int = RNG_SEED) -> np.ndarray:
    rng = np.random.default_rng(seed)
    if heavy:                                   # Student t, 3 df, scaled to the same sd: fills cluster on news
        z = rng.standard_t(3, size=(PATHS, n)) / np.sqrt(3.0)
    else:
        z = rng.standard_normal((PATHS, n))
    return mean + sd * z


# The public e-functions take one stream; the crossing rates run thousands of paths at once through the same wealth
# process (`_wealth`), each with its public function's own transform (pinned by the parity test below).
def _sup_exec(x: np.ndarray) -> np.ndarray:
    return np.asarray(fw._wealth(-x - fw.TOL_EXEC_R, sd0=fw.SD_EXEC_R).max(axis=1))


def _sup_edge(x: np.ndarray) -> np.ndarray:
    return np.asarray(fw._wealth(x, sd0=fw.SD_EXCESS_R).max(axis=1))


def _sup_futility(x: np.ndarray, h1: float) -> np.ndarray:
    return np.asarray(fw._wealth(h1 - x, sd0=fw.SD_EXCESS_R)[:, fw.MIN_SIGNALS_FUTILITY - 1:].max(axis=1))


def test_the_vectorised_crossing_check_runs_the_public_e_functions_own_process():
    x = _paths(-0.05, 0.08, 150)[:5]
    assert np.allclose(np.maximum(1.0, _sup_exec(x)), [fw.e_execution(r) for r in x])
    assert np.allclose(np.maximum(1.0, _sup_edge(10 * x)), [fw.e_edge(10 * r) for r in x])
    assert np.allclose(np.maximum(1.0, _sup_futility(x, 0.1)), [fw.e_futility(r, h1_r=0.1) for r in x])


# ---------------------------------------------------------------------------------------------- Ville
@pytest.mark.parametrize("heavy", [False, True])
@pytest.mark.parametrize("sd", [0.03, 0.08, 0.3, 1.0])   # the prior sd is 0.1 R: up to 10x wider
def test_the_execution_e_process_crosses_its_kill_level_at_most_5_percent_of_the_time_under_h0(heavy, sd):
    # H0 boundary: fills cost exactly the modelled cost + 0.02 R (fill - replay = -0.02 R)
    x = _paths(-0.02, sd, 300, heavy=heavy)
    assert float(np.mean(_sup_exec(x) >= fw.E_KILL_EXEC)) <= 0.05


@pytest.mark.parametrize("heavy", [False, True])
def test_the_edge_e_process_proves_nothing_more_than_5_percent_of_the_time_when_excess_is_zero(heavy):
    x = _paths(0.0, 1.3, 300, heavy=heavy, seed=RNG_SEED + 1)
    assert float(np.mean(_sup_edge(x) >= fw.E_PROVEN)) <= 0.05


@pytest.mark.parametrize("heavy", [False, True])
def test_the_futility_e_process_kills_a_family_at_h1_at_most_10_percent_of_the_time(heavy):
    x = _paths(0.10, 1.3, 400, heavy=heavy, seed=RNG_SEED + 2)        # true excess exactly at H1
    assert float(np.mean(_sup_futility(x, 0.10) >= fw.E_KILL_FUTILITY)) <= 0.10


# ---------------------------------------------------------------------------------------------- power
def test_the_execution_e_process_catches_a_5_cent_r_cost_overrun_within_100_fills():
    x = _paths(-0.07, 0.08, 100, seed=RNG_SEED + 3)                  # 0.05 R above the tolerance
    assert float(np.mean(_sup_exec(x) >= fw.E_KILL_EXEC)) >= 0.8


def test_the_futility_e_process_kills_a_null_family_within_300_signals_when_h1_is_far():
    x = _paths(0.0, 0.6, 300, seed=RNG_SEED + 4)
    assert float(np.mean(_sup_futility(x, 0.17) >= fw.E_KILL_FUTILITY)) >= 0.8


def test_futility_never_counts_before_its_minimum_signals():
    assert fw.e_futility(np.full(99, -5.0), h1_r=0.05) == 1.0
    assert fw.e_futility(np.full(100, -5.0), h1_r=0.05) > fw.E_KILL_FUTILITY


def test_fills_better_than_the_replay_never_raise_the_execution_e_process():
    assert fw.e_execution(np.full(200, 0.05)) <= 1.0


# ---------------------------------------------------------------------------------------------- exit mix
PRIOR_MIX = {"stop": 50.0, "target": 35.0, "time": 15.0}


def test_the_exit_mix_holds_when_shares_match_the_history_and_fails_when_stops_dominate():
    assert fw.exit_mix_ok({"stop": 30, "target": 21, "time": 9}, PRIOR_MIX)
    assert not fw.exit_mix_ok({"stop": 55, "target": 3, "time": 2}, PRIOR_MIX)


def test_the_exit_mix_is_not_judged_before_50_trades():
    assert fw.exit_mix_ok({"stop": 40, "target": 0, "time": 0}, PRIOR_MIX)


def test_an_exit_kind_the_history_never_had_is_refused():
    with pytest.raises(ValueError, match="prior"):
        fw.exit_mix_ok({"stop": 30, "target": 20, "broker_close": 10}, PRIOR_MIX)


# ---------------------------------------------------------------------------------------------- day clustering (C2)
def _clustered(mean: float, within: float, n: int, *, per: int = 5, day_ratio: float = 1.0,
               seed: int = RNG_SEED) -> np.ndarray:
    """Paths of n values, `per` per session day, each day sharing a shock of sd day_ratio x within."""
    rng = np.random.default_rng(seed)
    days = n // per
    return rng.normal(0, day_ratio * within, (PATHS, days)).repeat(per, axis=1) + rng.normal(mean, within, (PATHS, n))


def _day_means(x: np.ndarray, per: int = 5) -> np.ndarray:
    return x.reshape(x.shape[0], -1, per).mean(axis=2)


def test_the_e_functions_take_one_term_per_session_day():
    x = _clustered(-0.05, 0.08, 150)[0]
    day = np.repeat(np.arange(30), 5)
    terms = -_day_means(x[None, :]) - fw.TOL_EXEC_R
    assert fw.e_execution(x, day=day) == pytest.approx(max(1.0, fw._wealth(terms, sd0=fw.SD_EXEC_R).max()))
    assert fw.day_terms(np.array([1.0, 3.0, 5.0]), np.array(["b", "b", "a"]))[0].tolist() == [2.0, 5.0]


def test_day_clustered_execution_e_process_crosses_20_at_most_5_percent_under_h0():
    # day sd = within-day sd (quant C2); 5 fills a day, 300 fills; H0 boundary: fill - replay = -0.02 R
    x = _clustered(-0.02, 0.1, 300)
    terms = -_day_means(x) - fw.TOL_EXEC_R
    assert float(np.mean(fw._wealth(terms, sd0=fw.SD_EXEC_R).max(axis=1) >= fw.E_KILL_EXEC)) <= 0.05


def test_day_clustered_futility_e_process_crosses_10_at_most_10_percent_under_h0():
    x = _clustered(0.10, 1.3, 400, seed=RNG_SEED + 5)              # true excess exactly at H1 = 0.10
    terms = 0.10 - _day_means(x)
    start = fw.MIN_SIGNALS_FUTILITY // 5 - 1
    assert float(np.mean(fw._wealth(terms, sd0=fw.SD_EXCESS_R)[:, start:].max(axis=1) >= fw.E_KILL_FUTILITY)) <= 0.10


def test_with_day_terms_a_5_cent_overrun_is_caught_within_about_300_fills():
    x = _clustered(-0.07, 0.1, 300, day_ratio=0.5, seed=RNG_SEED + 6)    # 0.05 R above the tolerance
    terms = -_day_means(x) - fw.TOL_EXEC_R
    assert float(np.mean(fw._wealth(terms, sd0=fw.SD_EXEC_R).max(axis=1) >= fw.E_KILL_EXEC)) >= 0.9


# ---------------------------------------------------------------------------------------------- forward state
def _units(n: int, *, excess: float, fills: int = 0, fill_gap: float = 0.0, exit_gap: float = 0.0,
           kinds: tuple[str, ...] = ("stop", "target", "stop", "time", "target", "stop", "target", "stop", "stop",
                                     "target"), broker: str = "icm", seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    t0 = pd.Timestamp("2026-11-12", tz="UTC")
    u = pd.DataFrame({"cell_id": "C03|XAUUSD|30m|x", "family": "C03", "inst": "XAUUSD", "tf": "30m",
                      "side": 1, "day": [str((t0 + pd.Timedelta(hours=6 * i)).date()) for i in range(n)],
                      "decision_ns": [int((t0 + pd.Timedelta(hours=6 * i)).value) for i in range(n)],
                      "net_r": excess + rng.normal(0, 0.05, n), "twin_mean_net_r": 0.0,
                      "excess_r": excess + rng.normal(0, 0.05, n), "cost_r": 0.02, "year": 2026, "broker": broker})
    u["entry_fill_minus_replay_r"] = np.nan
    u["fill_minus_replay_r"] = np.nan
    u["exit_kind"] = None
    idx = u.index[:fills]
    entry = fill_gap + rng.normal(0, 0.01, len(idx))
    u.loc[idx, "entry_fill_minus_replay_r"] = entry
    u.loc[idx, "fill_minus_replay_r"] = entry + exit_gap
    u.loc[idx, "exit_kind"] = [kinds[i % len(kinds)] for i in range(len(idx))]
    return fw.apply_s5(u, {(broker, "15m-30m"): True})


MIX = {"stop": 50.0, "target": 40.0, "time": 10.0}
MIX_HASH = fw.prior_hash(MIX)


def _state(u: pd.DataFrame, **kw: Any) -> fw.ForwardState:
    args: dict[str, Any] = dict(family="C03", l3_shrunk_r=0.2, s5_passed=True, exit_prior=MIX,
                                exit_prior_hash=MIX_HASH)
    return fw.forward_state(u, **{**args, **kw})


def test_forward_reads_wait_until_s5_passes_even_when_futility_has_crossed():
    s = _state(_units(200, excess=-0.5, fills=60), s5_passed=False)
    assert s.status == "WAITING" and s.e_futility >= fw.E_KILL_FUTILITY


def test_a_family_whose_excess_falls_far_short_of_h1_is_killed():
    s = _state(_units(200, excess=-0.5, fills=10))
    assert s.status == "KILLED" and (s.n_signals, s.n_fills) == (200, 10)


def test_a_family_with_fills_costing_more_than_modelled_is_killed_by_the_execution_e_process():
    s = _state(_units(120, excess=0.3, fills=100, fill_gap=-0.10))
    assert s.status == "KILLED" and s.e_exec >= fw.E_KILL_EXEC and set(s.exec_streams) == {"icm:entry", "icm:exit"}


def test_a_vantage_entry_overrun_is_killed_even_when_favourable_exits_offset_it():
    """Quant C3: one stream per (broker, leg); no exit stream for a demo-exit-at-level broker."""
    s = _state(_units(160, excess=0.3, fills=160, fill_gap=-0.07, exit_gap=+0.30, broker="vantage"))
    assert set(s.exec_streams) == {"vantage:entry"}
    assert s.status == "KILLED" and s.exec_streams["vantage:entry"] >= fw.E_KILL_EXEC * len(s.exec_streams)


def test_the_kill_level_rises_with_the_number_of_streams_formed():
    s = _state(_units(160, excess=0.3, fills=160, fill_gap=0.0))
    assert len(s.exec_streams) == 2 and s.e_exec == pytest.approx(max(s.exec_streams.values()) / 2)


def test_on_track_needs_50_fills_with_execution_futility_and_exit_mix_green():
    few = _state(_units(120, excess=0.3, fills=49))
    ok = _state(_units(120, excess=0.3, fills=60))
    assert few.status == "WAITING" and ok.status == "ON TRACK" and ok.exit_mix_ok


def test_a_family_waits_until_100_signals_however_many_fills_it_has():
    """Quant C9."""
    assert _state(_units(99, excess=0.3, fills=99)).status == "WAITING"
    assert _state(_units(100, excess=0.3, fills=100)).status == "ON TRACK"


def test_an_exit_mix_off_the_history_reads_drifting():
    s = _state(_units(120, excess=0.3, fills=60, kinds=("stop",)))
    assert s.status == "DRIFTING" and not s.exit_mix_ok


def test_without_a_history_exit_prior_a_family_is_never_on_track():
    s = _state(_units(120, excess=0.3, fills=60), exit_prior=None, exit_prior_hash=None)
    assert s.status == "WAITING" and not s.exit_mix_ok


def test_the_exit_prior_comes_from_shadow_labels_before_forward_start_and_must_match_the_cards_hash():
    """Quant C9: frozen from the family's r2 shadow labels decided before forward start, hashed in the card."""
    start = pd.Timestamp("2026-11-12", tz="UTC")
    labels = pd.DataFrame({"label_barrier": ["stop"] * 6 + ["target"] * 3 + ["time"] + ["stop"] * 50,
                           "decision_utc": [start - pd.Timedelta(days=1)] * 10 + [start] * 50})
    prior, h = fw.exit_prior_from_shadow(labels, forward_start=start)
    assert prior == {"stop": 6.5, "target": 3.5, "time": 1.5} and h == fw.prior_hash(prior)
    with pytest.raises(ValueError, match="hash"):
        _state(_units(120, excess=0.3, fills=60), exit_prior=prior, exit_prior_hash=MIX_HASH)


def test_forward_state_reads_only_its_own_family():
    u = pd.concat([_units(50, excess=0.3), _units(70, excess=-0.5).assign(family="S21")], ignore_index=True)
    assert _state(u).n_signals == 50


def test_unfiltered_forward_units_raise():
    """Quant C8: units carry broker and tf, and no fill where S5 has not passed for that (broker, band)."""
    u = _units(120, excess=0.3, fills=60)
    with pytest.raises(ValueError, match="s5_ok"):
        _state(u.drop(columns=["s5_ok"]))
    with pytest.raises(ValueError, match="broker"):
        _state(u.drop(columns=["broker"]))
    with pytest.raises(ValueError, match="apply_s5"):
        _state(u.assign(s5_ok=False))
    blanked = fw.apply_s5(u.drop(columns=["s5_ok"]), {("icm", "1h-4h"): True})      # 30m's band never passed
    assert not blanked["s5_ok"].any() and blanked["fill_minus_replay_r"].isna().all()
    assert _state(blanked).n_fills == 0


# ---------------------------------------------------------------------------------------------- S5
def _pairs(broker: str, tf: str, n: int, *, gap: float, entry_gap: float = 0.0, days: int = 20,
           sd: float = 0.01) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    return pd.DataFrame({"candidate_id": [f"{broker}{tf}{i}" for i in range(n)], "broker": broker, "tf": tf,
                         "session_day": [f"2026-11-{1 + i % days:02d}" for i in range(n)],
                         "replay_r": 0.0, "fill_r": gap + rng.normal(0, sd, n),
                         "entry_fill_minus_replay_r": entry_gap + rng.normal(0, 0.001, n),
                         "exit_kind": ["stop", "target"] * (n // 2) + ["stop"] * (n % 2)})


def test_s5_passes_per_broker_and_tf_band_when_the_fill_vs_replay_bias_is_within_2_cents_r():
    p = pd.concat([_pairs("icm", "15m", 60, gap=0.01), _pairs("icm", "30m", 60, gap=0.01),
                   _pairs("icm", "1h", 60, gap=0.09)])
    ok = fw.s5_read(p, broker="icm", tf_band="15m-30m")
    assert ok.passed and ok.status == "PASS" and ok.n == 120 and ok.legs == "all"
    assert ok.bias_r == pytest.approx(0.01, abs=0.005) and set(ok.bias_by_exit_kind) == {"stop", "target"}
    bad = fw.s5_read(p, broker="icm", tf_band="1h-4h")
    assert not bad.passed and bad.status == "FAIL" and bad.bias_r == pytest.approx(0.09, abs=0.005)


def test_s5_needs_50_pairs_over_10_session_days_and_a_lower_bound_inside_the_tolerance():
    """Quant C4."""
    assert fw.s5_read(_pairs("icm", "1h", 20, gap=0.0), broker="icm", tf_band="1h-4h").status == "INSUFFICIENT"
    nine = fw.s5_read(_pairs("icm", "1h", 90, gap=0.0, days=9), broker="icm", tf_band="1h-4h")
    assert nine.status == "INSUFFICIENT" and "9 session days" in nine.reason
    spread = _pairs("icm", "1h", 60, gap=0.0)                         # 3 pairs on each of 20 days
    day_no = spread["session_day"].str[-2:].astype(int)
    spread["fill_r"] = -0.015 + 0.2 * np.where(day_no % 2 == 0, 1.0, -1.0)   # mean exactly -0.015, days far apart
    wide = fw.s5_read(spread, broker="icm", tf_band="1h-4h")
    assert abs(wide.bias_r) <= 0.02 < -wide.lb95_r and wide.status == "FAIL"     # mean inside, bound outside


def test_the_1m_5m_band_is_informational_and_never_passes():
    s = fw.s5_read(_pairs("icm", "5m", 80, gap=0.0), broker="icm", tf_band="1m-5m")
    assert s.status == "INFORMATIONAL" and not s.passed


def test_vantage_s5_reads_the_entry_leg_only_and_is_never_green_on_the_exit_leg():
    p = _pairs("vantage", "15m", 80, gap=0.30, entry_gap=0.005)        # exits filled at the level: not evidence
    s = fw.s5_read(p, broker="vantage", tf_band="15m-30m", entry_at_quote_share=0.4)
    assert s.legs == "entry" and s.passed and s.bias_r == pytest.approx(0.005, abs=0.001)
    assert s.exit_leg_read is False
    assert fw.s5_read(_pairs("icm", "15m", 80, gap=0.0), broker="icm", tf_band="15m-30m").exit_leg_read is True


def test_vantage_s5_reads_not_available_when_its_entries_fill_at_the_quote():
    """Quant C7."""
    s = fw.s5_read(_pairs("vantage", "15m", 80, gap=0.0), broker="vantage", tf_band="15m-30m",
                   entry_at_quote_share=0.97)
    assert s.status == "NOT AVAILABLE" and not s.passed


def test_s5_reports_the_paired_rows_on_unknown_arms():
    p = _pairs("icm", "15m", 60, gap=0.0)
    p.attrs["unknown_arm_labels"] = 4
    assert fw.s5_read(p, broker="icm", tf_band="15m-30m").unknown_arm_labels == 4


def test_tf_bands_cover_every_traded_timeframe_and_an_unknown_band_is_refused():
    assert {fw.TF_BAND[t] for t in ("1m", "5m", "15m", "30m", "1h", "4h", "1d")} == set(fw.TF_BANDS)
    with pytest.raises(ValueError, match="band"):
        fw.s5_read(_pairs("icm", "5m", 80, gap=0.0), broker="icm", tf_band="2h")
