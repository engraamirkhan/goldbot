"""H-01 slow TSMOM (docs/research/preregistration-2027Q1.md): a daily signal traded on 4h bars, exactly as
pre-registered, added as the tsmom preset `slow` without touching tsmom's default configuration or labels."""
import hashlib
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.data.resample import mid, resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.features.mtf import TF_LABEL, context_tfs
from goldbot.features.technical import atr
from goldbot.labels import SwapSpec, triple_barrier
from goldbot.research.pipeline import build_decision_frame, prepare
from goldbot.research.walkforward import WINDOWS, splits_for, window_for
from goldbot.specialists import SPECIALISTS
from goldbot.specialists.time_series_momentum import TimeSeriesMomentumSpecialist

SWAP = SwapSpec(long_usd_per_lot=-60.0, short_usd_per_lot=20.0)


def slow(**overrides) -> TimeSeriesMomentumSpecialist:
    return TimeSeriesMomentumSpecialist(**{**TimeSeriesMomentumSpecialist.presets["slow"], **overrides})


def _context(b1: pd.DataFrame, tf: str) -> dict[str, pd.DataFrame]:
    return {TF_LABEL[x]: resample_bars(b1, x) for x in context_tfs(tf)}


@pytest.fixture(scope="module")
def two_years() -> pd.DataFrame:
    return ticks_to_1m(synthetic_ticks("2022-01-01", "2024-01-01", ticks_per_minute=1, seed=21))


@pytest.fixture(scope="module")
def prepared(two_years):
    dec = resample_bars(two_years, "4h").reset_index(drop=True)
    context = _context(two_years, "4h")
    return dec, context, prepare(slow(), dec, context, extra_cost_usd=0.3, swap=SWAP)


def _digest(df: pd.DataFrame) -> str:
    return hashlib.sha256(df[sorted(df.columns)].to_csv(index=False, float_format="%.9g").encode()).hexdigest()


def test_default_tsmom_labels_and_agent_ids_are_byte_identical_to_before_the_slow_option():
    # digests taken on the commit before the slow option existed (same data, costs and swap)
    expected = {
        "{}": ("tsmom-g0-d5e1b0ac6f", 52, "4682112e10578185679e96f3c63b0e7f8462e1ab14356a9588eec0d9ccd2bb1e",
               "e8ad63791841b8973c6768480827a55e89f02b2fce75a130fac107bd4f71c053"),
        "4h": ("tsmom-g0-76d7b19171", 29, "9967a5cb2e6ad55e400d18684205fe58f77d725e94c26781a3adfb2de6998543",
               "1162aff69b7ae5eaae0d063186ea30f31d97a78195b4e5a4a6359019449e653b"),
    }
    b1 = ticks_to_1m(synthetic_ticks("2024-01-01", "2024-04-01", ticks_per_minute=1, seed=21))
    cases: tuple[tuple[str, dict[str, Any]], ...] = (("{}", {}), ("4h", {"timeframe": "4h", "max_bars": 12}))
    for key, overrides in cases:
        spec = SPECIALISTS["tsmom"](**overrides)
        dec = resample_bars(b1, spec.timeframe).reset_index(drop=True)
        p = prepare(spec, dec, _context(b1, spec.timeframe), extra_cost_usd=0.3, swap=SWAP)
        assert (spec.agent_id, len(p.labels), _digest(p.labels), _digest(p.gross)) == expected[key], key
        assert spec.walkforward == {} and "signal_tf" not in spec.config
    assert SPECIALISTS["tsmom"]().config == SPECIALISTS["tsmom"].default_config


def test_slow_preset_is_h01_exactly_as_preregistered():
    s = slow()
    assert s.timeframe == "4h" and s.config["signal_tf"] == "1d" and s.config["atr_tf"] == "1d"
    # 20 / 60 / 120-day vol-scaled returns with 60-day volatility, counted in daily bars
    assert [s._bars(s.config[k], "1d") for k in ("lb_fast_h", "lb_mid_h", "lb_slow_h", "vol_window_h")] == [20, 60, 120, 60]
    ls = s.label_spec
    assert (ls.target_atr, ls.stop_atr, ls.max_bars) == (3.0, 1.5, 124)     # 20 trading days x 31/5 four-hour bars
    w = window_for(s.timeframe, **s.walkforward)
    assert (w["train_months"], w["test_months"], w["step_months"], w["embargo_days"]) == (48, 6, 6, 4)
    assert WINDOWS["4h"]["purge_days"] == 10 < s.hold_calendar_days() <= w["purge_days"] == 31
    # a clone rebuilt from its identity keeps the configuration, the purge and the agent id
    again = TimeSeriesMomentumSpecialist(identity=s.identity)
    assert again.config == s.config and again.agent_id == s.agent_id and again.walkforward == s.walkforward


def test_time_barrier_is_twenty_trading_days_of_4h_bars(two_years):
    ts = pd.DatetimeIndex(resample_bars(two_years, "4h")["ts_utc"])
    mondays = np.flatnonzero((ts.dayofweek == 0) & (ts.hour == 0))[:-6]
    spans = ts[mondays + slow().label_spec.max_bars] - ts[mondays]
    assert (spans == pd.Timedelta(days=28)).all()                # 124 bars from a Monday open = four weeks = 20 days


def test_purge_covers_every_label_life(prepared):
    dec, _, p = prepared
    life = pd.to_datetime(p.labels["ts_exit"], utc=True) - pd.to_datetime(p.labels["ts_utc"], utc=True)
    timed_out = (p.labels["barrier_hit"] == "time") & (p.labels["t_exit"] < len(dec) - 1)   # not cut by the data end
    assert timed_out.any() and (p.labels.loc[timed_out, "bars_held"] == 125).all()          # entry bar + 124
    assert life.max() <= pd.Timedelta(days=slow().walkforward["purge_days"])
    folds = splits_for(p.labels, "4h", **slow().walkforward, min_train=1)
    te = pd.DatetimeIndex(pd.to_datetime(p.labels["ts_exit"], utc=True))
    for f in folds:
        assert (te[f.train_idx] < f.test_start - pd.Timedelta(days=31)).all()


def test_daily_signal_is_read_only_after_the_feature_day_close(two_years):
    s = slow()
    dec = resample_bars(two_years, "4h").reset_index(drop=True)
    context = _context(two_years, "4h")
    m, X = build_decision_frame(dec, context)
    full = s.candidates_in_context(m, X, context)
    assert len(full) > 50
    d1_vis = pd.DatetimeIndex(context["d1"]["visible_at"])
    close = pd.DatetimeIndex(m["visible_at"])
    for i in full["idx"]:
        used = d1_vis[d1_vis <= close[i]].max()
        assert close[i - 1] < used <= close[i]                    # the first 4h bar whose close sees the settlement
    # truncation: cut the 1m history mid-session (between a 4h close and the next settlement); recomputed on what was
    # visible at the cut, the signals up to it are the same
    for cut in (pd.Timestamp("2023-06-14 16:00", tz="UTC"), pd.Timestamp("2023-09-20 20:00", tz="UTC")):
        b_cut = two_years[two_years["visible_at"] <= cut]
        dec_c = resample_bars(b_cut, "4h").reset_index(drop=True)
        ctx_c = {k: v[pd.to_datetime(v["visible_at"], utc=True) <= cut] for k, v in _context(b_cut, "4h").items()}
        m_c, X_c = build_decision_frame(dec_c, ctx_c)
        part = s.candidates_in_context(m_c, X_c, ctx_c)
        upto = full[full["idx"] < len(m_c)].reset_index(drop=True)
        pd.testing.assert_frame_equal(part.reset_index(drop=True), upto, check_dtype=False)
        a_full, a_cut = s.barrier_atr(m, context), s.barrier_atr(m_c, ctx_c)
        assert a_full is not None and a_cut is not None
        np.testing.assert_allclose(a_cut.to_numpy(), a_full.to_numpy()[: len(m_c)], equal_nan=True)


def test_barriers_are_daily_atr_frozen_at_entry(prepared, two_years):
    dec, context, p = prepared
    lab = p.labels
    d1 = mid(context["d1"].reset_index(drop=True))
    d1_atr = atr(d1, 14).to_numpy()
    close = pd.DatetimeIndex(dec["visible_at"])
    vis = pd.DatetimeIndex(d1["visible_at"])
    expected = [d1_atr[vis <= close[i]][-1] for i in lab["idx"]]
    np.testing.assert_allclose(lab["atr_sig"], expected)          # ATR(1d) last visible at the signal bar
    four_hour = atr(mid(dec), 14).to_numpy()[lab["idx"]]
    assert np.median(lab["atr_sig"] / four_hour) > 2              # not the 4h bars' own ATR
    side, entry, a = lab["side"], lab["entry"], lab["atr_sig"]
    tgt, stp = lab["barrier_hit"] == "target", lab["barrier_hit"] == "stop"
    assert tgt.any() and stp.any()
    np.testing.assert_allclose(lab.loc[tgt, "exit"], (entry + side * 3.0 * a)[tgt])
    np.testing.assert_allclose(lab.loc[stp, "exit"], (entry - side * 1.5 * a)[stp])
    # the daily ATR moves during the hold, the barriers do not
    a_bar = slow().barrier_atr(mid(dec), context)
    assert a_bar is not None
    assert (a_bar.to_numpy()[lab["t_exit"]] != lab["atr_sig"].to_numpy()).mean() > 0.9
    np.testing.assert_allclose(lab["risk"], 1.5 * a / entry)      # R is one daily-ATR stop


def test_slow_variant_trades_both_sides_one_at_a_time_and_pays_swap(prepared):
    dec, context, p = prepared
    lab = p.labels
    assert set(lab["side"]) == {-1, 1}
    assert (lab["t_entry"].to_numpy()[1:] > lab["t_exit"].to_numpy()[:-1]).all()      # one position at a time
    assert (lab["swap_nights"] > 0).all()                         # every daily-signal trade is held overnight
    longs, shorts = lab["side"] == 1, lab["side"] == -1
    assert (lab.loc[longs, "swap_ret"] < 0).all() and (lab.loc[shorts, "swap_ret"] > 0).all()
    plain = prepare(slow(), dec, context, extra_cost_usd=0.3)
    np.testing.assert_allclose(lab["ret"] - plain.labels["ret"], lab["swap_ret"])
    pd.testing.assert_frame_equal(plain.gross, p.gross)           # the gross screen carries no cost


def test_slow_option_without_daily_bars_proposes_nothing_and_research_refuses(two_years):
    s = slow()
    dec = resample_bars(two_years, "4h").reset_index(drop=True)
    m, X = build_decision_frame(dec, _context(two_years, "4h"))
    assert s.candidates(m, X).empty                               # the live engine's call: fail closed
    with pytest.raises(ValueError, match="needs the d1 context bars"):
        prepare(s, dec, {}, frame=(m, X))
    with pytest.raises(ValueError, match="context timeframe"):
        slow(signal_tf="1h")                                      # 1h is not a context of 4h decisions


def test_triple_barrier_reads_the_atr_only_at_the_signal_bar():
    n = 30
    px = np.linspace(100.0, 101.0, n)
    bars = pd.DataFrame({"ts_utc": pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC"),
                         **{f"{s}_{k}": px for s in ("bid", "ask") for k in ("high", "low", "close")}})
    bars.loc[20, "bid_high"] = bars.loc[20, "ask_high"] = 101.0 + 2.9
    sig = pd.DataFrame({"idx": [2], "side": [1]})
    flat = triple_barrier(bars, sig, slow().label_spec, pd.Series(1.0, index=bars.index))
    moving = pd.Series(1.0, index=bars.index)
    moving.iloc[3:] = 5.0                                         # ATR jumps after the signal bar
    pd.testing.assert_frame_equal(flat, triple_barrier(bars, sig, slow().label_spec, moving))
    assert flat["barrier_hit"].iloc[0] == "target"
