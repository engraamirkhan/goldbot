"""Backtest parity of the `smc_replica` indicators (scripts/replica_backtest_parity.py): the same simple rule strategies
run once on the chart's plotted events and once on our replica, on the same bars, must give the same trade list.
Fixtures are synthetic bars only: the "plotted" columns are written from the replica itself, then perturbed by hand,
so no vendor output is stored in the repo."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pandas as pd
import pytest


def _load() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "replica_backtest_parity.py"
    spec = importlib.util.spec_from_file_location("replica_backtest_parity", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


P = _load()


def _walk(n: int = 1400, seed: int = 11, sd: float = 0.003) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    c = 2000.0 * np.exp(np.cumsum(rng.normal(0, sd, n)))
    o = np.concatenate(([2000.0], c[:-1])) * np.exp(rng.normal(0, sd / 4, n))
    h = np.maximum(o, c) * np.exp(np.abs(rng.normal(0, sd / 2, n)))
    lo = np.minimum(o, c) * np.exp(-np.abs(rng.normal(0, sd / 2, n)))
    ts = pd.date_range("2024-01-02", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"bar_time": ts, "open": o, "high": h, "low": lo, "close": c,
                         "volume": rng.integers(50, 500, n).astype(float)})


def _with_plots(d: pd.DataFrame, tf: str = "60") -> pd.DataFrame:
    """The chart's plot columns as if the vendor computed exactly what the replica computes."""
    rep = P.replica_inputs(d, tf)
    d = d.copy()
    for name, col in {**P.TV_EVENTS, **P.TV_LINES}.items():
        d[col] = rep[name].astype(float)
    return d


@pytest.fixture(scope="module")
def plotted() -> pd.DataFrame:
    return _with_plots(_walk())


def _by_name(results: list, strategy: str) -> Any:
    return next(r for r in results if r.strategy == strategy)


def test_plots_equal_to_the_replica_give_identical_trades_on_every_strategy(plotted):
    res = P.parity(plotted, "60", holdout=None)
    assert {r.strategy for r in res} == set(P.STRATEGIES)
    assert sum(r.trades_a for r in res) > 20                 # the fixture does trade
    for r in res:
        assert r.verdict == "exact", r
        assert r.trades_a == r.trades_b and r.mismatched == 0 and r.abs_dr == 0.0 and r.stats_equal
        assert not r.signal_diffs


def test_a_flipped_sr_break_on_the_chart_is_an_explained_mismatch_and_listed(plotted):
    d = plotted.copy()
    rep_up = P.replica_inputs(d, "60")["sr_up"]
    bar = int(np.flatnonzero(~rep_up)[700])
    d.loc[bar, P.TV_EVENTS["sr_up"]] = 1.0
    sr_bars = P.sr_disagreements(P.tv_inputs(d), P.replica_inputs(d, "60"), np.ones(len(d), dtype=bool))
    assert [(b, k) for b, k, _ in sr_bars] == [(bar, "sr_up")]
    r = _by_name(P.parity(d, "60", holdout=None), "sr_break")
    assert r.signal_diffs == [(bar, 1, "chart only", P.SR_RESIDUAL)]
    assert r.verdict == "explained"
    assert r.signals_a == r.signals_b + 1                     # a trade only if no position was open then


def test_a_flipped_structure_event_is_an_unexplained_mismatch(plotted):
    d = plotted.copy()
    rep = P.replica_inputs(d, "60")["choch_bull_internal"]
    bar = int(np.flatnonzero(~rep)[500])
    d.loc[bar, P.TV_EVENTS["choch_bull_internal"]] = 1.0
    res = P.parity(d, "60", holdout=None)
    r = _by_name(res, "internal_choch")
    assert r.verdict == "UNEXPLAINED" and r.mismatched >= 1 and r.abs_dr > 0
    assert (bar, 1, "chart only", "") in r.signal_diffs
    assert _by_name(res, "swing_bos").verdict == "exact"


def test_trades_never_enter_or_exit_inside_the_holdout_but_its_signals_are_still_compared(plotted):
    d = plotted.copy()
    h0 = d["bar_time"].iloc[900]
    window = (h0, d["bar_time"].iloc[-1] + pd.Timedelta(days=1))
    rep = P.replica_inputs(d, "60")["choch_bull_internal"]
    bar = int(np.flatnonzero(~rep & (np.arange(len(d)) > 1000))[0])
    d.loc[bar, P.TV_EVENTS["choch_bull_internal"]] = 1.0
    r = _by_name(P.parity(d, "60", holdout=window), "internal_choch")
    assert (bar, 1, "chart only", "") in r.signal_diffs           # compared as a signal
    assert r.mismatched == 0 and r.trades_a == r.trades_b     # but never simulated
    tr = P.trades(d, P.signals(P.STRATEGIES["internal_choch"](P.tv_inputs(d)), np.ones(len(d), dtype=bool)),
                  holdout=window)
    assert len(tr) and int(tr["t_exit"].max()) < 900


def test_compare_trades_counts_missing_and_changed_trades_and_sums_abs_delta_r():
    a = pd.DataFrame({"t_entry": [5, 9, 20], "side": [1, -1, 1], "t_exit": [7, 12, 25], "r": [2.0, -1.0, 0.5]})
    b = pd.DataFrame({"t_entry": [5, 9, 30], "side": [1, -1, -1], "t_exit": [7, 13, 31], "r": [2.0, -1.0, 1.5]})
    mism, adr = P.compare_trades(a, b)
    assert mism == 3                                          # 9 changed exit, 20 chart only, 30 replica only
    assert adr == pytest.approx(0.0 + 0.0 + 0.5 + 1.5)


def test_long_and_short_on_the_same_bar_is_no_trade():
    long_ = np.array([False, True, True, False])
    short = np.array([False, True, False, True])
    s = P.signals((long_, short), np.array([True, True, True, False]))
    assert list(zip(s["idx"], s["side"])) == [(2, 1)]          # bar 1 ambiguous, bar 3 outside the mask


def test_trend_follows_the_last_break_and_a_same_bar_bear_break_wins():
    bull = np.array([False, True, False, True, False])
    bear = np.array([False, False, True, True, False])
    assert P.trend_from_events(bull, bear).tolist() == [0, 1, -1, -1, -1]


def test_the_table_prints_parity_columns_only_never_profit_or_loss(plotted):
    text = P.render({"60": P.parity(plotted, "60", holdout=None)})
    header = text.splitlines()[0].lower()
    for word in ("sum r", "mean r", "win", "p&l", "pnl", "expectancy"):
        assert word not in header
    assert "|dr|" in header and "mismatched" in header


def _cross_bar(rep: dict) -> int:
    up = np.flatnonzero(P.R.cross_over(rep["sma10"], rep["sma20"]))
    return int(up[len(up) // 2])


def test_a_line_cross_lost_to_float_rounding_at_a_tie_is_explained(plotted):
    d = plotted.copy()
    i = _cross_bar(P.replica_inputs(d, "60"))
    s20 = d.loc[i - 1, P.TV_LINES["sma20"]]
    d.loc[i - 1, P.TV_LINES["sma10"]] = s20 * (1 + 1e-12)       # a tie the chart rounded to "above": the cross moves
    r = _by_name(P.parity(d, "60", holdout=None), "sma_cross_a")    # one bar earlier on the chart
    assert r.signal_diffs == [(i - 1, 1, "chart only", P.LINE_TIE), (i, 1, "replica only", P.LINE_TIE)]
    assert r.verdict == "explained"


def test_a_line_cross_moved_by_a_real_difference_is_unexplained(plotted):
    d = plotted.copy()
    i = _cross_bar(P.replica_inputs(d, "60"))
    d.loc[i - 1, P.TV_LINES["sma10"]] = d.loc[i - 1, P.TV_LINES["sma20"]] * 1.001
    r = _by_name(P.parity(d, "60", holdout=None), "sma_cross_a")
    assert (i, 1, "replica only", "") in r.signal_diffs
    assert r.verdict == "UNEXPLAINED"
