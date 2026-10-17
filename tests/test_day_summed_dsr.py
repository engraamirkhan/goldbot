"""E-6 (BACKLOG 76; docs/research/preregistration-2027Q1.md O-5/O-6): the day-summed deflated Sharpe of discovery
survivors. Reading rule: "computed on one summed net R per entry UTC day, over days with at least one model-filtered
trade only (n = G such days), with var(SR) = 1/G, the skew and kurtosis of those daily sums, and the same
K = `n_trials_effective` as the gate (both discoveries' K_eff included)"; pass needs it >= 0.95 as well."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from goldbot.research.gates import DSR_BAR, day_summed_check
from goldbot.research.metrics import (
    MIN_TRADES_FOR_DSR,
    DaySummedDsr,
    DsrInputs,
    daily_sums,
    day_summed_dsr,
    summarize,
)
from goldbot.research.registry import DISCOVERY, n_trials_effective

DAY0 = pd.Timestamp("2022-01-03 00:00", tz="UTC")


def _clustered(seed: int, days: int = 600, day_sd: float = 0.4) -> tuple[np.ndarray, pd.DatetimeIndex]:
    """1-3 trades a day sharing a day shock (sd `day_sd`) on unit trade noise, mean 0.15 R: the same-day clustering
    the reading rule guards against (several agents and timeframes trading one move)."""
    rng = np.random.default_rng(seed)
    per_day = rng.integers(1, 4, days)
    day = np.repeat(np.arange(days), per_day)
    r = 0.15 + rng.normal(0, day_sd, days)[day] + rng.normal(0, 1, len(day))
    hours = rng.integers(0, 24, len(day))
    ts = pd.DatetimeIndex(DAY0 + pd.to_timedelta(day, unit="D") + pd.to_timedelta(hours, unit="h"))
    return r, ts


def _one_a_day(n: int = 400, seed: int = 3) -> tuple[np.ndarray, pd.DatetimeIndex]:
    rng = np.random.default_rng(seed)
    r = 0.1 + rng.standard_t(5, n) * 0.8                         # fat-tailed sample: the kurtosis term matters
    gaps = rng.integers(1, 4, n)                                  # weekends and quiet days in between
    ts = pd.DatetimeIndex(DAY0 + pd.to_timedelta(np.cumsum(gaps), unit="D") + pd.Timedelta(hours=13, minutes=30))
    return r, ts


def test_one_trade_a_day_gives_exactly_the_per_trade_dsr():
    r, ts = _one_a_day()
    res = day_summed_dsr(r, ts, n_trials=60)
    assert res is not None
    assert res.day_summed == res.per_trade == DsrInputs.of(r)
    assert res.dsr_day_summed == res.dsr_per_trade == DsrInputs.of(r).dsr(60)
    assert res.t_day_summed == res.t_per_trade


def test_the_per_trade_side_is_the_gates_own_dsr_inputs_from_summarize():
    r, _ = _one_a_day()
    stored = summarize(pd.DataFrame({"ret": r}), trades_per_year=250, n_trials=60)
    assert DsrInputs(**stored["dsr_inputs"]) == DsrInputs.of(r)
    assert stored["dsr"] == DsrInputs.of(r).dsr(60)


def test_same_day_clustering_lowers_the_effective_t_by_about_5_to_12_percent():
    falls = []
    for seed in range(20):
        r, ts = _clustered(seed)
        res = day_summed_dsr(r, ts, n_trials=60)
        assert res is not None
        falls.append(1 - res.t_day_summed / res.t_per_trade)
        assert res.dsr_day_summed < res.dsr_per_trade
    assert 0.05 <= float(np.median(falls)) <= 0.12
    assert all(f > 0 for f in falls)


def test_the_day_summed_inputs_use_var_sr_one_over_g_and_the_moments_of_the_daily_sums():
    r, ts = _clustered(0)
    res = day_summed_dsr(r, ts, n_trials=60)
    assert res is not None
    sums = daily_sums(r, ts)
    g = len(np.unique(ts.normalize()))
    assert res.n_days == len(sums) == g and res.n_trades == len(r)
    assert res.day_summed.n == g and res.day_summed.var_sr_trials == 1 / g
    assert res.day_summed.skew == float(stats.skew(sums))
    assert res.day_summed.kurtosis == float(stats.kurtosis(sums, fisher=False))
    assert res.day_summed.sr == float(np.mean(sums) / np.std(sums, ddof=1))


def test_days_without_a_trade_are_not_observations():
    r, ts = _one_a_day()
    sums = daily_sums(r, ts)
    calendar_days = (ts[-1].normalize() - ts[0].normalize()).days + 1
    assert len(sums) == len(r) < calendar_days                   # gaps of 1-3 days: zero-trade days exist
    assert not np.any(sums == 0.0)
    res = day_summed_dsr(r, ts, n_trials=60)
    assert res is not None and res.day_summed.var_sr_trials == 1 / len(r)


def test_a_trade_counts_on_its_utc_entry_day_whatever_the_timezone_it_is_stamped_in():
    ts = pd.DatetimeIndex(["2024-03-04 23:30", "2024-03-05 00:30"], tz="UTC")
    assert list(daily_sums(np.array([1.0, 2.0]), ts)) == [1.0, 2.0]
    ny = ts.tz_convert("America/New_York")                        # both 4 March in New York, two UTC days
    assert list(daily_sums(np.array([1.0, 2.0]), ny)) == [1.0, 2.0]
    same = pd.DatetimeIndex(["2024-03-05 00:30", "2024-03-05 23:30"], tz="UTC")
    assert list(daily_sums(np.array([1.0, 2.0]), same)) == [3.0]


def test_the_result_is_deterministic_and_independent_of_row_order():
    r, ts = _clustered(5)
    a, b = day_summed_dsr(r, ts, n_trials=60), day_summed_dsr(r.copy(), ts.copy(), n_trials=60)
    assert a == b
    perm = np.random.default_rng(1).permutation(len(r))
    c = day_summed_dsr(r[perm], ts[perm], n_trials=60)
    assert c is not None and a is not None
    assert c.day_summed.sr == a.day_summed.sr and c.day_summed.n == a.day_summed.n
    assert np.isclose(c.dsr_day_summed, a.dsr_day_summed, rtol=0, atol=1e-12)


def test_too_few_days_reports_no_day_summed_dsr():
    r = np.full(MIN_TRADES_FOR_DSR + 50, 0.2) + np.random.default_rng(0).normal(0, 1, MIN_TRADES_FOR_DSR + 50)
    ts = pd.DatetimeIndex(DAY0 + pd.to_timedelta(np.arange(len(r)) % 50, unit="D"))   # 50 days only
    assert day_summed_dsr(r, ts, n_trials=60) is None


def test_recomputing_on_a_later_trial_count_lowers_both_dsrs_and_keeps_the_ts():
    r, ts = _clustered(2)
    res = day_summed_dsr(r, ts, n_trials=10)
    assert res is not None
    later = res.at(80)
    assert later.n_trials == 80
    assert later.dsr_day_summed < res.dsr_day_summed and later.dsr_per_trade < res.dsr_per_trade
    assert later.t_day_summed == res.t_day_summed and later.t_per_trade == res.t_per_trade
    assert later == day_summed_dsr(r, ts, n_trials=80)


# ---------------------------------------------------------------------------------------------- the reading
def _rows(stored: dict[str, Any] | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    survivor = {"trial": 40, "family": "scalp", "status": "evaluated",
                "results": {"model_filtered": {"n": 1200, "day_summed": stored}}}
    trials = [{"trial": i, "status": "evaluated", "results": {}} for i in range(1, 38)]
    discoveries = [{"trial": 38, "status": DISCOVERY, "results": {"discovery": {"k_eff": 12}}},
                   {"trial": 39, "status": DISCOVERY, "results": {"discovery": {"k_eff": 14}}}]
    prereg = {"status": "preregistered", "results": {}}
    return survivor, [*trials, *discoveries, prereg, survivor]


def test_the_reading_uses_the_gates_trial_count_with_both_discoveries_k_eff():
    r, ts = _clustered(0)
    res = day_summed_dsr(r, ts, n_trials=5)                      # the count at run time is stale by the reading
    assert res is not None
    row, rows = _rows(res.model_dump())
    k = n_trials_effective(rows)
    assert k == 40 + 12 + 14
    check = day_summed_check(row, rows)
    assert check["n_trials"] == k
    assert check["dsr_day_summed"] == res.at(k).dsr_day_summed
    assert check["passed"] == (res.at(k).dsr_day_summed >= DSR_BAR)
    assert check["name"] == "day_summed_dsr"


def test_the_reading_passes_a_strong_sample_and_fails_a_clustered_one_the_per_trade_dsr_passes():
    rng = np.random.default_rng(11)
    strong_r, strong_ts = _one_a_day(n=900, seed=11)
    strong_r = strong_r + 0.15
    strong = day_summed_dsr(strong_r, strong_ts, n_trials=5)
    assert strong is not None
    row, rows = _rows(strong.model_dump())
    assert day_summed_check(row, rows)["passed"]
    # a marginal per-trade pass made of same-day duplicates: the days carry less evidence than the trades
    days = 450
    base = 0.15 + rng.normal(0, 1, days)
    r = np.repeat(base, 2) + rng.normal(0, 0.3, 2 * days)
    ts = pd.DatetimeIndex(DAY0 + pd.to_timedelta(np.repeat(np.arange(days), 2), unit="D"))
    res = day_summed_dsr(r, ts, n_trials=5)
    assert res is not None
    row, rows = _rows(res.model_dump())
    k = n_trials_effective(rows)
    assert res.at(k).dsr_per_trade >= DSR_BAR > res.at(k).dsr_day_summed
    assert not day_summed_check(row, rows)["passed"]


def test_a_row_without_stored_day_summed_inputs_fails_closed():
    row, rows = _rows(None)
    check = day_summed_check(row, rows)
    assert not check["passed"] and "re-run" in check["detail"]
    row, rows = _rows({"per_trade": "garbage"})
    assert not day_summed_check(row, rows)["passed"]


def test_the_stored_record_round_trips_through_json():
    r, ts = _clustered(4)
    res = day_summed_dsr(r, ts, n_trials=60)
    assert res is not None
    assert DaySummedDsr.model_validate_json(res.model_dump_json()) == res


# ---------------------------------------------------------------------------------------------- recorded and reported
def test_the_walk_forward_records_the_day_summed_dsr_on_net_r_of_the_model_filtered_trades():
    from goldbot.research.pipeline import model_filtered_day_summed
    r, ts = _clustered(6)
    risk = np.full(len(r), 0.004)
    taken = pd.DataFrame({"ts_utc": ts, "ret": r * risk, "risk": risk})
    stored = model_filtered_day_summed(taken, n_trials=60)
    expected = day_summed_dsr(r, ts, n_trials=60)
    assert expected is not None and stored is not None
    got = DaySummedDsr(**stored)
    assert got.n_trades == expected.n_trades and got.n_days == expected.n_days and got.n_trials == 60
    assert np.isclose(got.t_day_summed, expected.t_day_summed) and np.isclose(got.t_per_trade, expected.t_per_trade)
    bad = pd.concat([taken, pd.DataFrame({"ts_utc": [ts[0]], "ret": [0.01], "risk": [0.0]})], ignore_index=True)
    kept = model_filtered_day_summed(bad, n_trials=60)
    assert kept is not None and DaySummedDsr(**kept).n_trades == len(r)    # no risk: not a trade in R
    assert model_filtered_day_summed(taken.iloc[:50], n_trials=60) is None


def test_the_research_report_shows_both_ts_and_the_day_summed_dsr():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "scripts" / "research_pass.py"
    spec = importlib.util.spec_from_file_location("research_pass_e6", path)
    assert spec is not None and spec.loader is not None
    rp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rp)
    r, ts = _clustered(0)
    res = day_summed_dsr(r, ts, n_trials=60)
    assert res is not None
    text = "\n".join(rp.day_summed_lines({"day_summed": res.model_dump()}))
    assert "day-summed" in text and f"{res.dsr_day_summed:.3f}" in text and f"{res.dsr_per_trade:.3f}" in text
    assert f"{res.t_day_summed:.2f}" in text and f"{res.t_per_trade:.2f}" in text and str(res.n_days) in text
    assert "O-5/O-6" in text
    assert "n/a" in "\n".join(rp.day_summed_lines({"n": 120, "day_summed": None}))
    assert rp.day_summed_lines(None) == []
