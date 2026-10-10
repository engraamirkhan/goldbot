"""asia_drift (H-04, docs/research/preregistration-2027Q1.md): long at 00:00 UTC, time exit at 07:00 UTC in 1h bars,
stop 1.5 x ATR(1h), every trading day, no entry while the market is closed, no rollover held, one position at a time,
no look-ahead, and labels that the shadow book reproduces."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.data.resample import resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine.shadow import ShadowBook
from goldbot.features.mtf import TF_LABEL, context_tfs
from goldbot.features.technical import atr
from goldbot.labels.triple_barrier import SwapSpec, one_at_a_time, rollover_nights, triple_barrier
from goldbot.research.pipeline import build_decision_frame, pooled_members, prepare
from goldbot.research.population import Population
from goldbot.specialists import SPECIALISTS
from goldbot.specialists.asia_drift import NO_TARGET_ATR, AsiaDriftSpecialist

SPEC = AsiaDriftSpecialist()


def _bars_1m(start: str, end: str, seed: int = 4) -> pd.DataFrame:
    return ticks_to_1m(synthetic_ticks(start, end, ticks_per_minute=1, seed=seed))


@pytest.fixture(scope="module")
def winter() -> pd.DataFrame:          # Europe/Athens UTC+2, London GMT
    return _bars_1m("2024-01-01", "2024-01-27")


@pytest.fixture(scope="module")
def summer() -> pd.DataFrame:          # Europe/Athens UTC+3, London BST
    return _bars_1m("2024-07-01", "2024-07-27")


def _frame(bars_1m: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, pd.DataFrame]]:
    dec = resample_bars(bars_1m, "1h").reset_index(drop=True)
    context = {TF_LABEL[x]: resample_bars(bars_1m, x) for x in context_tfs("1h")}
    m, X = build_decision_frame(dec, context)
    return dec, m, X, context


def _labels(dec: pd.DataFrame, m: pd.DataFrame, X: pd.DataFrame, spec: AsiaDriftSpecialist = SPEC,
            swap: SwapSpec | None = None) -> pd.DataFrame:
    return triple_barrier(dec, spec.candidates(m, X), spec.label_spec, atr(m, 14), swap=swap, policy=spec.exit_spec)


def test_the_rule_is_the_preregistered_one():
    ls = SPEC.label_spec
    assert SPEC.timeframe == "1h" and SPEC.timeframes == ()
    assert SPEC.config == {"stop_atr": 1.5, "target_atr": NO_TARGET_ATR, "max_bars": 6}
    assert AsiaDriftSpecialist(max_bars=8).label_spec.max_bars == 6       # never held past 07:00 UTC
    assert ls.stop_atr == 1.5 and ls.max_bars == 6 and ls.target_atr == NO_TARGET_ATR
    assert SPEC.exit_spec is None                       # a plain time barrier: no policy, the labels count bars
    assert SPECIALISTS["asia_drift"] is AsiaDriftSpecialist
    assert 0 < len(SPEC.model_features) <= 39


@pytest.mark.parametrize("regime, server_hour, london_hour", [("winter", 2, 7), ("summer", 3, 8)])
def test_entry_is_at_00_utc_and_the_time_exit_at_07_utc_in_both_dst_regimes(request, regime, server_hour, london_hour):
    dec, m, X, _ = _frame(request.getfixturevalue(regime))
    lab = _labels(dec, m, X, AsiaDriftSpecialist(stop_atr=1e6))           # no stop: every trade reaches the time exit
    close = pd.DatetimeIndex(dec["visible_at"])
    entry, exit_ = close[lab["idx"].to_numpy()], close[lab["t_exit"].to_numpy()]
    assert len(lab) >= 15 and (lab["side"] == 1).all() and (lab["barrier_hit"] == "time").all()
    assert set(entry.strftime("%H:%M")) == {"00:00"} and set(exit_.strftime("%H:%M")) == {"07:00"}
    assert (exit_ - entry == pd.Timedelta(hours=7)).all() and (lab["bars_held"] == 7).all()
    assert set(entry.dayofweek) == {0, 1, 2, 3, 4}                         # every weekday, never Saturday or Sunday
    # every weekday after the ATR warm-up (the first Monday) trades
    days = pd.bdate_range(entry[0].normalize().tz_localize(None), entry[-1].normalize().tz_localize(None))
    assert len(entry) == len(days)
    # the broker's clock and London's: 02:00 / 03:00 server at entry; flat at 07:00 UTC, before or at 08:00 London
    assert set(entry.tz_convert("Europe/Athens").hour) == {server_hour}
    assert set(exit_.tz_convert("Europe/London").hour) == {london_hour}
    # the entry fill is the decision bar's close, the open of the first bar at or after 00:00 UTC
    assert set(pd.DatetimeIndex(dec["ts_utc"]).take(lab["t_entry"].to_numpy()).strftime("%H:%M")) == {"00:00"}


def test_no_entry_while_the_market_is_closed():
    # bars every hour of every day (as a feed with junk weekend prints might give): only weekday 00:00 entries survive
    ts = pd.date_range("2023-12-18", "2024-01-08", freq="1h", tz="UTC", inclusive="left")
    m = pd.DataFrame({"ts_utc": ts, "visible_at": ts + pd.Timedelta(hours=1), "open": 2000.0, "high": 2001.0,
                      "low": 1999.0, "close": 2000.0})
    c = AsiaDriftSpecialist().candidates(m, pd.DataFrame({"atr14": np.full(len(m), 1.0)}))
    entry = pd.DatetimeIndex(m["visible_at"]).take(c["idx"].to_numpy())
    assert set(entry.dayofweek) <= {0, 1, 2, 3, 4}
    assert not ({(d.month, d.day) for d in entry} & {(12, 25), (1, 1)})   # Christmas Day and New Year's Day
    assert pd.Timestamp("2023-12-26 00:00", tz="UTC") in entry and pd.Timestamp("2024-01-02 00:00", tz="UTC") in entry
    weekdays = pd.bdate_range("2023-12-19", "2024-01-08")             # first close 01:00 Dec 18, last 00:00 Jan 8
    assert len(entry) == len(weekdays) - 2
    # 00:00 UTC is open on the session calendar every weekday in both regimes (outside the server's daily break)
    for day in ("2024-01-08", "2024-07-08"):
        at = pd.date_range(day, periods=5, freq="1D", tz="UTC")
        assert DEFAULT_SESSIONS.is_open(at).all()
    # a missing decision bar (no 23:00 bar: the market did not trade into midnight) gives no entry that day
    gap = m[~((pd.DatetimeIndex(m["ts_utc"]).hour == 23) & (pd.DatetimeIndex(m["ts_utc"]).day == 27))]
    c2 = AsiaDriftSpecialist().candidates(gap.reset_index(drop=True), pd.DataFrame({"atr14": np.full(len(gap), 1.0)}))
    assert len(c2) == len(c) - 1
    # an unusable ATR (warm-up) gives no entry either
    assert AsiaDriftSpecialist().candidates(m, pd.DataFrame({"atr14": np.full(len(m), np.nan)})).empty


def test_candidates_and_their_atr_use_no_future_bars(winter):
    dec, m, X, _ = _frame(winter)
    full = SPEC.candidates(m, X)
    a_full = atr(m, 14)
    for cut_ts in ("2024-01-10 00:00", "2024-01-16 23:00", "2024-01-23 03:00"):
        cut = pd.Timestamp(cut_ts, tz="UTC")
        dm, mm, Xm, _ = _frame(winter[winter["visible_at"] <= cut])
        part = SPEC.candidates(mm, Xm)
        upto = full[full["idx"] < len(mm)].reset_index(drop=True)
        pd.testing.assert_frame_equal(part.reset_index(drop=True), upto, check_dtype=False)
        np.testing.assert_allclose(atr(mm, 14).to_numpy(), a_full.to_numpy()[:len(mm)])
    # rewriting every bar after a decision bar changes neither the signal nor the ATR frozen at it
    i = int(full["idx"].iloc[5])
    shocked = m.copy()
    shocked.loc[i + 1:, ["open", "high", "low", "close"]] *= 1.5
    assert int(i) in set(SPEC.candidates(shocked, X)["idx"])
    assert atr(shocked, 14).iloc[i] == pytest.approx(a_full.iloc[i])


@pytest.mark.parametrize("regime", ["winter", "summer"])
def test_no_trade_is_held_through_the_server_rollover(request, regime):
    dec, m, X, _ = _frame(request.getfixturevalue(regime))
    swap = SwapSpec(long_usd_per_lot=-60.0, short_usd_per_lot=0.0)       # Europe/Athens server midnight
    lab = _labels(dec, m, X, AsiaDriftSpecialist(stop_atr=1e6), swap=swap)
    assert len(lab) >= 15 and (lab["swap_nights"] == 0).all() and (lab["swap_ret"] == 0).all()
    # positive control: the same clock charges a position held from 21:00 UTC to 07:00 UTC one night
    entry = pd.DatetimeIndex([pd.Timestamp("2024-01-09 00:00", tz="UTC"), pd.Timestamp("2024-07-09 00:00", tz="UTC"),
                              pd.Timestamp("2024-01-08 21:00", tz="UTC")])
    out = pd.DatetimeIndex([pd.Timestamp("2024-01-09 07:00", tz="UTC"), pd.Timestamp("2024-07-09 07:00", tz="UTC"),
                            pd.Timestamp("2024-01-09 07:00", tz="UTC")])
    assert rollover_nights(entry, out, "Europe/Athens", 2).tolist() == [0, 0, 1]


def test_one_position_at_a_time_one_trade_a_day(winter):
    dec, m, X, context = _frame(winter)
    c = SPEC.candidates(m, X)
    days = pd.DatetimeIndex(dec["visible_at"]).take(c["idx"].to_numpy()).normalize()
    assert days.is_unique                                                  # at most one entry a UTC day
    p = prepare(SPEC, dec, context)
    lab = p.labels
    assert len(lab) == len(c) and len(p.gross) == len(c)                  # nothing overlaps, so nothing is dropped
    assert (lab["t_entry"].to_numpy()[1:] > lab["t_exit"].to_numpy()[:-1]).all()
    raw = _labels(dec, m, X)
    pd.testing.assert_frame_equal(one_at_a_time(raw), raw.sort_values("t_entry").reset_index(drop=True))


@pytest.mark.parametrize("regime", ["winter", "summer"])
def test_shadow_book_reproduces_the_labels_time_barrier(tmp_path, request, regime):
    dec, m, X, _ = _frame(request.getfixturevalue(regime))
    a = atr(m, 14)
    lab = _labels(dec, m, X).set_index("idx")
    ls = SPEC.label_spec
    book = ShadowBook(tmp_path)
    book.track("v1", dec["ts_utc"].iloc[0])
    for i in range(len(dec)):
        bar = dec.iloc[i]
        book.on_bar(bar, timeframe="1h")
        if i in lab.index:
            book.open_trade(version="v1", agent_id=SPEC.agent_id, side=1, bar_ts=bar["ts_utc"],
                            entry=float(bar["ask_close"]), atr_usd=float(a.iloc[i]), target_atr=ls.target_atr,
                            stop_atr=ls.stop_atr, max_bars=ls.max_bars, p=0.5, timeframe="1h")
    closed = {t.entry_ts: t for t in book.books["v1"].closed}
    assert len(closed) >= 15
    for i in lab.index.to_numpy(dtype=int):
        row, t = lab.loc[i], closed[dec["ts_utc"].iloc[i]]
        assert t.exit_ts == row["ts_exit"] and t.barrier == row["barrier_hit"]
        assert t.exit == pytest.approx(row["exit"]) and t.ret == pytest.approx(row["ret"])
    assert {"time"} <= {t.barrier for t in closed.values()}


def test_registered_for_research_but_not_a_founder_or_pool_member(tmp_path):
    import importlib.util
    from pathlib import Path

    from goldbot.config import load_settings
    path = Path(__file__).resolve().parents[1] / "scripts" / "research_pass.py"
    spec = importlib.util.spec_from_file_location("research_pass_asia", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.parse_variants("asia_drift", "[{}]") == [{}]             # `--specialist asia_drift` runs
    assert "asia_drift" not in pooled_members("1h")
    assert {"breakout", "trend", "tsmom"} <= set(pooled_members("1h"))       # the existing pools are unchanged
    pop = Population(tmp_path / "population.json")
    added = pop.ensure_founders(pd.Timestamp("2026-10-10", tz="UTC"))
    assert added and not any(aid.startswith("asia_drift-") for aid in added)
    retired = {r.family for r in load_settings().research.retired_families}
    assert "asia_drift" not in retired                                       # active, not retired
