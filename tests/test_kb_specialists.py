"""H-48 `sweep_absorb` and H-49 stage 2 `eq_liquidity_run` (docs/research/prereg-kb-2026-10-18.md sections 1-2): the
candidates equal the written definitions on crafted bars, nothing after a signal's close is read (a prefix of the bars
gives the same candidates as the full series), the barriers are the candidate's own (stop = R, target 2 R or the level
plus 0.25 ATR), the stop counts first, and both stay out of the loop grid, the founders and the pools."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.data.resample import mid, resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.features.technical import atr
from goldbot.labels.triple_barrier import one_at_a_time, triple_barrier
from goldbot.ops.jobs import research_families
from goldbot.research.generator import GRID_V1, _eligible
from goldbot.research.pipeline import pooled_members
from goldbot.research.preregistration import resolve_variant
from goldbot.specialists import SPECIALISTS
from goldbot.specialists import eq_liquidity_run as eq
from goldbot.specialists import sweep_absorb as sa

NEW = ("sweep_absorb", "eq_liquidity_run")
LONDON = pd.Timestamp("2024-01-02 07:30", tz="UTC")       # bar 20 starts 12:30 UTC, inside the London window
ASIA = pd.Timestamp("2024-01-02 00:30", tz="UTC")


def _flat(n: int = 40, start: pd.Timestamp = LONDON) -> pd.DataFrame:
    """Mid bars with a constant range of 2 around 100, so ATR(14) is 2 from bar 13 on."""
    ts = pd.date_range(start, periods=n, freq="15min")
    return pd.DataFrame({"ts_utc": ts, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0})


def _set(m: pd.DataFrame, i: int, high: float, low: float, close: float) -> None:
    m.loc[i, ["high", "low", "close"]] = [high, low, close]


def _levels(monkeypatch: pytest.MonkeyPatch, n: int, **kw: float) -> None:
    """Pin the session levels the rule reads (constant per key; the others undefined)."""
    keys = ("pdh", "pdl", "prev_sess_high", "prev_sess_low")
    monkeypatch.setattr(sa, "_session_levels",
                        lambda bars: {k: np.full(len(bars), kw.get(k, np.nan)) for k in keys})


def _r(spec, m) -> pd.Series:
    r = spec.barrier_atr(m, None)
    assert r is not None
    return r


def _sweep(m: pd.DataFrame) -> pd.DataFrame:
    return SPECIALISTS["sweep_absorb"]().candidates(m, pd.DataFrame(index=m.index))


# ----------------------------------------------------------------------------------------------- registration
def test_both_specialists_offer_only_their_one_preregistered_configuration():
    for fam in NEW:
        cls = SPECIALISTS[fam]
        assert cls.timeframe == "15m" and cls.screening and not cls.requires_tv
        assert tuple(cls.presets) == ("prereg",) and cls.default_config == {"rung": "prereg"}
        assert cls(**resolve_variant(fam, "prereg")).config == {"rung": "prereg"}
        with pytest.raises(ValueError):
            cls(rung="deeper")
        with pytest.raises(ValueError, match="unknown"):
            resolve_variant(fam, {"rung": "prereg", "depth": 0.3})


def test_both_stay_out_of_the_loop_grid_the_founders_and_the_pools():
    new = set(NEW)
    assert not new & set(_eligible(load_settings()))
    assert not new & set(research_families())
    assert not any(f in new for f, _ in GRID_V1)
    assert not new & set(pooled_members("15m"))


# ----------------------------------------------------------------------------------------------- H-48
def test_an_absorbed_shallow_high_sweep_is_faded_short_with_the_stop_beyond_the_extreme(monkeypatch):
    m = _flat()
    _levels(monkeypatch, len(m), pdh=101.0)
    _set(m, 20, 101.4, 99.8, 100.2)                  # pierces 0.4 < 0.5 x ATR(2) and closes back inside
    a = atr(m, 14).to_numpy()[20]
    c = _sweep(m)
    assert c["idx"].tolist() == [20] and c["side"].tolist() == [-1] and c["max_bars"].tolist() == [16]
    assert c["risk_usd"].iloc[0] == pytest.approx(101.4 + 0.25 * a - 100.2)
    assert 0.6 <= c["risk_usd"].iloc[0] / a <= 1.0
    spec = SPECIALISTS["sweep_absorb"]()
    assert _r(spec, m).iloc[20] == pytest.approx(c["risk_usd"].iloc[0])
    assert _r(spec, m).drop(20).isna().all()


def test_an_absorbed_low_sweep_is_faded_long(monkeypatch):
    m = _flat()
    _levels(monkeypatch, len(m), pdl=99.0)
    _set(m, 20, 100.2, 98.6, 99.8)
    c = _sweep(m)
    a = atr(m, 14).to_numpy()[20]
    assert c["idx"].tolist() == [20] and c["side"].tolist() == [1]
    assert c["risk_usd"].iloc[0] == pytest.approx(99.8 - (98.6 - 0.25 * a))


def test_a_sweep_of_half_an_atr_or_more_is_not_absorbed(monkeypatch):
    m = _flat()
    _levels(monkeypatch, len(m), pdh=101.0)
    _set(m, 20, 102.0, 99.8, 100.2)                  # 1.0 = 0.5 x ATR: the search ends
    assert _sweep(m).empty


def test_the_reclaim_may_come_two_bars_after_the_pierce_but_not_three(monkeypatch):
    for reclaim_at, expected in ((22, [22]), (23, [])):
        m = _flat()
        _levels(monkeypatch, len(m), pdh=101.0)
        _set(m, 20, 101.4, 100.8, 101.3)             # pierce, still above the level
        for i in range(21, reclaim_at):
            _set(m, i, 101.4, 100.8, 101.2)
        _set(m, reclaim_at, 101.0, 99.9, 100.5)
        assert _sweep(m)["idx"].tolist() == expected


def test_stops_below_the_cost_floor_or_beyond_one_atr_are_skipped(monkeypatch):
    m = _flat()
    _levels(monkeypatch, len(m), pdh=101.0)
    _set(m, 20, 101.4, 100.7, 100.9)                 # stop 101.9: 1.0 away < 0.6 x ATR
    assert _sweep(m).empty
    m = _flat()
    _set(m, 20, 101.4, 98.0, 98.9)                   # stop 2.5 away > 1.0 x ATR
    assert _sweep(m).empty


def test_a_sweep_outside_london_and_new_york_is_skipped(monkeypatch):
    m = _flat(start=ASIA)
    _levels(monkeypatch, len(m), pdh=101.0)
    _set(m, 20, 101.4, 99.8, 100.2)
    assert _sweep(m).empty


def test_one_pierce_per_level_per_four_bars(monkeypatch):
    m = _flat()
    _levels(monkeypatch, len(m), pdh=101.0)
    _set(m, 20, 101.4, 99.8, 100.2)
    _set(m, 22, 101.4, 99.8, 100.2)                  # a second pierce 2 bars later: ignored
    assert _sweep(m)["idx"].tolist() == [20]


def test_the_news_blackout_drops_the_candidate(monkeypatch):
    m = _flat()
    _levels(monkeypatch, len(m), pdh=101.0)
    _set(m, 20, 101.4, 99.8, 100.2)
    blk = pd.DataFrame({"in_blackout": np.where(np.arange(len(m)) == 20, 1.0, 0.0)})
    assert SPECIALISTS["sweep_absorb"]().candidates(m, blk).empty


def test_the_label_uses_the_candidates_stop_as_r_a_2r_target_and_the_stop_first(monkeypatch):
    m = _flat()
    _levels(monkeypatch, len(m), pdh=101.0)
    _set(m, 20, 101.4, 99.8, 100.2)
    spec = SPECIALISTS["sweep_absorb"]()
    c = spec.candidates(m, pd.DataFrame(index=m.index))
    r = c["risk_usd"].iloc[0]
    bars = pd.DataFrame({"ts_utc": m["ts_utc"], **{f"{s}_{k}": m[k] for s in ("bid", "ask")
                                                  for k in ("high", "low", "close")}})
    bars.loc[21:, ["bid_low", "ask_low"]] = 100.2 - 2 * r - 0.01        # past the target...
    bars.loc[21, ["bid_high", "ask_high"]] = 100.2 + r + 0.01           # ...and the stop on the same bar
    lab = triple_barrier(bars, c, spec.label_spec, _r(spec, m))
    assert lab["barrier_hit"].tolist() == ["stop"]
    bars.loc[21, ["bid_high", "ask_high"]] = 101.0
    lab = triple_barrier(bars, c, spec.label_spec, _r(spec, m))
    assert lab["barrier_hit"].tolist() == ["target"] and lab["exit"].iloc[0] == pytest.approx(100.2 - 2 * r)


# ----------------------------------------------------------------------------------------------- H-49 stage 2
def _eq(m: pd.DataFrame, eqh: tuple[int, ...] = (), eql: tuple[int, ...] = ()) -> pd.DataFrame:
    flags = lambda ix: np.isin(np.arange(len(m)), list(ix))      # noqa: E731
    eq.smc.equal_levels = lambda h, lo, c, length=3, **kw: (flags(eqh), flags(eql))
    return SPECIALISTS["eq_liquidity_run"]().candidates(m, pd.DataFrame(index=m.index))


@pytest.fixture(autouse=True)
def _restore_equal_levels():
    real = eq.smc.equal_levels
    yield
    eq.smc.equal_levels = real


def test_the_first_approach_to_an_equal_high_is_bought_toward_it_with_its_own_target():
    m = _flat()                                        # level = high of bar 7 = 101; closes sit 1.0 below it
    c = _eq(m, eqh=(10,))
    a = atr(m, 14).to_numpy()
    assert c["idx"].tolist() == [14] and c["side"].tolist() == [1]            # age 4, the first allowed bar
    assert c["risk_usd"].iloc[0] == pytest.approx(0.75 * a[14])
    assert c["target_atr"].iloc[0] == pytest.approx((1.0 + 0.25 * a[14]) / (0.75 * a[14]))
    assert (0.3 + 0.25) / 0.75 <= c["target_atr"].iloc[0] <= (1.0 + 0.25) / 0.75    # 0.73-1.67 R (the prereg text says 1.1-2.3)


def test_an_equal_low_is_sold_toward_it():
    m = _flat()
    c = _eq(m, eql=(10,))
    assert c["idx"].tolist() == [14] and c["side"].tolist() == [-1]


def test_a_level_traded_through_before_the_approach_is_dead():
    m = _flat()
    _set(m, 12, 101.5, 99.0, 100.0)                   # trades through the equal high (101) before age 4
    assert _eq(m, eqh=(10,)).empty


def test_the_approach_must_be_inside_the_band_and_inside_the_age_window():
    m = _flat()
    m["close"] = 100.8                                 # 0.2 below the level < 0.3 x ATR: too close
    assert _eq(m, eqh=(10,)).empty
    m = _flat()
    m["close"] = 98.0                                  # 3.0 below: too far
    assert _eq(m, eqh=(10,)).empty
    m = _flat()
    m["close"] = 98.0
    _set(m, 12, 101.0, 99.0, 100.0)                    # in the band at age 2: too young
    assert _eq(m, eqh=(10,)).empty
    m = _flat(n=130)
    m["close"] = 98.0
    _set(m, 107, 101.0, 99.0, 100.0)                   # in the band at age 97: too old
    assert _eq(m, eqh=(10,)).empty
    _set(m, 106, 101.0, 99.0, 100.0)                   # age 96: allowed
    assert _eq(m, eqh=(10,))["idx"].tolist() == [106]


def test_the_label_target_is_the_candidates_own_and_a_reached_level_plus_overshoot_wins():
    m = _flat()
    spec = SPECIALISTS["eq_liquidity_run"]()
    c = _eq(m, eqh=(10,))
    r, tgt = c["risk_usd"].iloc[0], c["target_atr"].iloc[0]
    bars = pd.DataFrame({"ts_utc": m["ts_utc"], **{f"{s}_{k}": m[k] for s in ("bid", "ask")
                                                  for k in ("high", "low", "close")}})
    bars.loc[15, ["bid_high", "ask_high"]] = 100.0 + tgt * r + 0.01
    lab = triple_barrier(bars, c, spec.label_spec, _r(spec, m))
    assert lab["barrier_hit"].tolist() == ["target"] and lab["target_atr"].tolist() == [pytest.approx(tgt)]
    assert lab["exit"].iloc[0] == pytest.approx(101.0 + 0.25 * atr(m, 14).to_numpy()[14])


# ----------------------------------------------------------------------------------------------- point in time
@pytest.fixture(scope="module")
def synthetic() -> pd.DataFrame:
    dec = resample_bars(ticks_to_1m(synthetic_ticks("2024-01-01", "2024-07-01", ticks_per_minute=1, seed=23)),
                        "15m").reset_index(drop=True)
    return mid(dec)


@pytest.mark.parametrize("family", NEW)
def test_appending_bars_never_changes_a_past_candidate(synthetic, family):
    spec = SPECIALISTS[family]()
    full = spec.candidates(synthetic, pd.DataFrame(index=synthetic.index))
    assert len(full) > 5, "the synthetic series must exercise the rule"
    for cut in (len(synthetic) // 3, len(synthetic) // 2, len(synthetic) - 7):
        part = synthetic.iloc[:cut].reset_index(drop=True)
        got = spec.candidates(part, pd.DataFrame(index=part.index))
        want = full[full["idx"] < cut].reset_index(drop=True)
        pd.testing.assert_frame_equal(got, want)
        pd.testing.assert_series_equal(_r(spec, part).dropna(),
                                       _r(spec, synthetic).iloc[:cut].dropna())


@pytest.mark.parametrize("family", NEW)
def test_one_trade_at_a_time_leaves_no_overlap(synthetic, family):
    spec = SPECIALISTS[family]()
    c = spec.candidates(synthetic, pd.DataFrame(index=synthetic.index))
    bars = pd.DataFrame({"ts_utc": synthetic["ts_utc"], **{f"{s}_{k}": synthetic[k] for s in ("bid", "ask")
                                                           for k in ("high", "low", "close")}})
    lab = one_at_a_time(triple_barrier(bars, c, spec.label_spec, _r(spec, synthetic)))
    assert len(lab) > 1 and (lab["t_entry"].to_numpy()[1:] > lab["t_exit"].to_numpy()[:-1]).all()


# ----------------------------------------------------------------------------------------------- the diagnostics
def _script(name: str):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _cell_frame(cross: list[float]) -> pd.DataFrame:
    n = len(cross)
    return pd.DataFrame({"cross": cross, "fwd4": 0.0, "atr": 2.0, "hour": 9, "dist_atr": 0.5,
                         "time": pd.Timestamp("2012-01-03", tz="UTC")}, index=range(n))


def test_stage_1_lift_is_the_equal_rate_minus_the_matched_control_rate_and_needs_a_control_in_the_cell():
    k = _script("kb5_magnet_base_rate")
    out = k.matched_lift(_cell_frame([1.0] * 20), _cell_frame([1.0, 0.0] * 10), "cross", np.random.default_rng(1))
    assert out["lift"] == pytest.approx(0.5) and out["matched"] == 20 and out["ci95"][0] > 0
    other = _cell_frame([0.0] * 5).assign(hour=3)                      # no control in the cell: nothing matched
    assert k.matched_lift(other, _cell_frame([1.0] * 5), "cross", np.random.default_rng(1))["matched"] == 0


def test_stage_1_runs_on_bars_reports_counts_and_a_verdict_and_never_prices_a_trade(synthetic):
    k = _script("kb5_magnet_base_rate")
    r = k.diagnose(synthetic)
    assert set(r["periods"]) == {"2010-2017", "2018-2025-09"} and isinstance(r["stage2_runs"], bool)
    assert r["periods"]["2010-2017"]["equal_approaches"] == 0 and r["stage2_runs"] is False   # 2024 bars only
    assert "PASS" not in k.markdown(r) and "FAIL" in k.markdown(r)


def test_the_stage_1_control_is_confirmed_pivots_that_are_not_equal_levels(synthetic):
    h, lo = synthetic["high"].to_numpy(), synthetic["low"].to_numpy()
    eqh, eql = eq.smc.equal_levels(h, lo, synthetic["close"].to_numpy(), length=3)
    ph, pl = eq.confirmed_pivots(synthetic)
    assert (ph & eqh).sum() == eqh.sum() and (pl & eql).sum() == eql.sum()       # equal levels are pivots
    ctl = eq.approaches(synthetic, (ph & ~eqh, pl & ~eql))
    rule = eq.approaches(synthetic)
    assert len(ctl) > len(rule) > 0


def test_retest_selection_reports_groups_and_the_pre_registered_reading():
    k = _script("kb1_retest_selection")
    m = pd.DataFrame({"retested": [True] * 6 + [False] * 4, "fwd16": [0.5] * 6 + [0.9] * 4,
                      "fwd32": [0.1] * 10})
    r = k.summarise(m)
    assert (r["breaks"], r["retested"], r["never_retested"]) == (10, 6, 4)
    assert r["horizons"][16]["difference"] == pytest.approx(-0.4) and r["p95_share_never_retested"] == 1.0
    assert r["reading"] == "adverse-selected"
    flat = k.summarise(pd.DataFrame({"retested": [True, False] * 30, "fwd16": 0.2, "fwd32": 0.3}))
    assert flat["reading"] == "retest neutral"


def test_retest_selection_runs_end_to_end_on_synthetic_bars():
    k = _script("kb1_retest_selection")
    b1 = ticks_to_1m(synthetic_ticks("2024-01-01", "2024-09-01", ticks_per_minute=1, seed=5))
    r = k.diagnose(b1)
    assert r["breaks"] == r["retested"] + r["never_retested"] and "Reading" in k.markdown(r)
