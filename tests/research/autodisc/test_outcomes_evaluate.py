"""Outcome tensor and explore evaluator (ADR 0058 phase 1), synthetic data only."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from goldbot.labels.triple_barrier import SwapSpec, one_at_a_time, triple_barrier
from goldbot.research.autodisc import (
    ExitTemplate,
    ExploreFrame,
    Leaf,
    LedgerError,
    SearchLedger,
    StrategySpec,
    WindowViolation,
    spec_hash,
)
from goldbot.research.autodisc import evaluate as ev
from goldbot.research.autodisc import outcomes as oc


def make_bars(n: int = 6000, start: str = "2012-01-02", drift: float = 0.0, seed: int = 1,
              freq: str = "15min") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ts = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    mid = 1500 + np.cumsum(rng.normal(drift, 0.6, n))
    hi, lo = mid + rng.uniform(0.1, 0.8, n), mid - rng.uniform(0.1, 0.8, n)
    half = 0.15
    return pd.DataFrame({"ts_utc": ts, "bid_high": hi - half, "bid_low": lo - half, "bid_close": mid - half,
                         "ask_high": hi + half, "ask_low": lo + half, "ask_close": mid + half})


def make_atr(bars: pd.DataFrame) -> pd.Series:
    return pd.Series(np.full(len(bars), 1.5), index=bars.index)


MENU = oc.default_menu(16)


def build(bars: pd.DataFrame, atr: pd.Series, menu=MENU, **kw) -> oc.OutcomeTensor:
    return oc.build_tensor(ExploreFrame.from_frame(bars, "ts_utc"), atr, menu, **kw)


def test_tensor_matches_triple_barrier_net_of_costs():
    bars = make_bars()
    atr = make_atr(bars)
    swap = SwapSpec.shipped()
    ten = build(bars, atr, extra_cost_usd=0.2, swap=swap)
    k = 1
    sig = pd.DataFrame({"idx": [10, 10, 500, 1234], "side": [1, -1, -1, 1]})
    lab = triple_barrier(bars, sig, MENU[k].barrier_spec(), atr, swap=swap)
    for _, row in lab.iterrows():
        s = 0 if row["side"] > 0 else 1
        i = int(row["idx"])
        assert ten.ret[i, s, k] == pytest.approx(row["ret"] - 0.2 / row["entry"])
        assert ten.risk[i, s, k] == pytest.approx(MENU[k].stop_atr * 1.5 / row["entry"])
        assert ten.t_entry[i, s, k] == row["t_entry"] and ten.t_exit[i, s, k] == row["t_exit"]


def test_last_bar_has_no_outcome():
    bars = make_bars(300)
    ten = build(bars, make_atr(bars))
    assert np.isnan(ten.ret[-1]).all() and (ten.t_entry[-1] == -1).all()


def test_select_is_one_at_a_time_like_the_pipeline():
    bars = make_bars()
    atr = make_atr(bars)
    ten = build(bars, atr)
    rng = np.random.default_rng(3)
    mask = rng.random(len(bars)) < 0.2
    side = np.where(rng.random(len(bars)) < 0.5, 1, -1)
    got = ten.select(mask, side, 0)
    cands = pd.DataFrame({"idx": np.flatnonzero(mask), "side": side[mask]})
    lab = one_at_a_time(triple_barrier(bars, cands, MENU[0].barrier_spec(), atr))
    assert len(got) == len(lab) and list(got.idx) == list(lab["idx"])
    assert got.ret == pytest.approx(lab["ret"].to_numpy())


def test_guard_refuses_a_2018_bar_and_digest_is_the_truncated_digest():
    ok = make_bars(100, start="2017-12-30")
    ten = build(ok, make_atr(ok))
    assert ten.digest == ExploreFrame.from_frame(ok, "ts_utc").digest
    bad = make_bars(300, start="2017-12-31 12:00")       # crosses 2018-01-01 00:00
    with pytest.raises(WindowViolation):
        build(bad, make_atr(bad))


def test_time_exit_template_has_no_target():
    t = [e for e in MENU if e.target_atr is None][0]
    assert t.barrier_spec().has_target is False and oc.BarrierExit.from_barrier_spec(t.barrier_spec()) == t


def test_menu_exit_maps_to_barriers():
    b = oc.barrier_exit(ExitTemplate(stop="atr_1.5", target="2R"), 16)
    assert (b.stop_atr, b.target_atr, b.max_bars) == (1.5, 3.0, 16)
    t = oc.barrier_exit(ExitTemplate(stop="atr_1.0", target="time", time_bars=8), 16)
    assert (t.stop_atr, t.target_atr, t.max_bars) == (1.0, None, 8)
    with pytest.raises(ValueError):
        oc.barrier_exit(ExitTemplate(stop="structural", target="1R"), 16)


@pytest.fixture(scope="module")
def noise_case():
    bars = make_bars(60000, start="2010-01-04", seed=7)
    atr = make_atr(bars)
    mask = np.random.default_rng(11).random(len(bars)) < 0.02
    return bars, atr, mask


def test_floors_and_unledgered_refusal(noise_case, tmp_path):
    bars, atr, mask = noise_case
    ten = build(bars, atr)
    spec = StrategySpec(timeframe="15m", side_rule="long",
                        entry=Leaf(kind="oscillator", name="rsi_bucket", params={"low": 30, "high": 70}),
                        exit=ExitTemplate(stop="atr_1.5", target="2R"))
    h = spec_hash(spec)
    ledger = SearchLedger(tmp_path / "d.db")
    with pytest.raises(LedgerError):
        ev.evaluate(ten, mask, 1, 0, timeframe="15m", spec_hash=h, ledger=ledger)
    ledger.log_spec(spec, "test", "1")
    e = ev.evaluate(ten, mask, 1, 0, timeframe="15m", spec_hash=h, ledger=ledger)
    assert e.spec_hash == h and e.input_digest == ten.digest
    ledger.record_result(h, e.model_dump(mode="json"))
    assert ledger.verify() >= 2
    few = ev.evaluate(ten, np.arange(len(bars)) % 10_000 == 0, 1, 0, timeframe="15m")
    assert not few.floors_passed and any("events" in f for f in few.floor_failures)
    always = ev.evaluate(ten, np.ones(len(bars), bool), 1, 2, timeframe="15m")   # time exit, always in market
    assert always.time_in_market > 0.40 and not always.floors_passed


def test_noise_has_no_edge_and_stress_is_worse(noise_case):
    bars, atr, mask = noise_case
    ten = build(bars, atr, extra_cost_usd=0.3)
    e = ev.evaluate(ten, mask, 1, 1, timeframe="15m")
    assert e.n_events >= 300 and e.mean_r is not None and e.stress_mean_r is not None
    assert e.stress_mean_r < e.mean_r                       # costs are positive: 1.5x is worse
    assert e.mean_r < 0.05 and not e.explore_checks_passed
    assert e.long.n + e.short.n == e.n_events


def test_injected_edge_is_flagged():
    """A rule whose side is the sign of the next 8 bars' move has a large positive edge, flagged by every check."""
    bars = make_bars(60000, start="2010-01-04", seed=21)
    atr = make_atr(bars)
    mid = (bars["bid_close"] + bars["ask_close"]).to_numpy() / 2
    fwd = np.roll(mid, -8) - mid
    fwd[-8:] = 0
    side = np.where(fwd > 0, 1, -1)
    mask = (np.random.default_rng(5).random(len(bars)) < 0.03) & (np.abs(fwd) > 1.0)
    e = ev.evaluate(build(bars, atr), mask, side, 1, timeframe="15m")
    assert e.mean_r is not None and e.mean_r > 0.15
    assert e.fitness is not None and e.fitness > 0 and e.stress_passed and e.explore_checks_passed
    assert e.excess_r is not None and e.excess_r > 0.15


def test_drift_baseline_removes_the_market_drift():
    bars = make_bars(60000, start="2010-01-04", drift=0.2, seed=4)
    ten = build(bars, make_atr(bars))
    mask = np.random.default_rng(2).random(len(bars)) < 0.02
    e = ev.evaluate(ten, mask, 1, 1, timeframe="15m")
    assert e.mean_r is not None and e.mean_r > 0.1           # the drift is in the raw R
    assert e.excess_r is not None and abs(e.excess_r) < 0.1  # and out of the excess


def test_fold_fitness_has_four_folds_and_is_min(noise_case):
    bars, atr, mask = noise_case
    e = ev.evaluate(build(bars, atr), mask, 1, 0, timeframe="15m")
    assert len(e.fold_t) == ev.N_FOLDS
    assert e.fitness == min(f for f in e.fold_t if f is not None)


def test_clustered_t():
    rng = np.random.default_rng(0)
    r = rng.normal(0.1, 1.0, 400)
    plain = r.mean() / (r.std(ddof=1) / np.sqrt(400))
    assert ev.clustered_t(r, np.arange(400)) == pytest.approx(plain, rel=0.02)
    shared = r + np.repeat(rng.normal(0, 1, 40), 10)
    assert abs(ev.clustered_t(shared, np.repeat(np.arange(40), 10))) < abs(ev.clustered_t(shared, np.arange(400)))
    assert ev.clustered_t(np.array([1.0]), np.array([0])) is None


def test_plateau_test():
    assert ev.plateau_test([0.5, 0.2, 0.1, -0.1, None]) is True      # 3/5 = 60%: the boundary passes
    assert ev.plateau_test([1.0, -1.0, -1.0, -1.0]) is False
    assert ev.plateau_test([]) is False


def test_cost_stress_uses_the_tensor_cost():
    bars = make_bars()
    ten = build(bars, make_atr(bars), extra_cost_usd=0.5)
    r1, r15 = ten.r(0), ten.r(0, 1.5)
    ok = np.isfinite(r1)
    assert np.all(r15[ok] < r1[ok])
