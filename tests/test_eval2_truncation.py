"""S6 truncation (EVAL-2 method v6 section 6): "for sampled instances, recompute the signal, ATR, regime level, MTF
context and twin stop on bars truncated at the decision bar's open + tf. Results identical. Higher-timeframe context is
visible only after its close. Regime cut-points (terciles) come from the trailing 250 sessions ending at the prior
session close, never the full window."

Stricter than tests/test_catalogue_lookahead.py, which compares events decided before the cut's LAST bar (a cut of n
bars checks decisions up to bar n - 2, so bar i + 1 is still visible to a decision at i). Here the cut is bar i's close
itself: bars 0..i. A detector may legitimately need a next bar to EXIST (an entry needs a fill bar); then the decisive
check keeps the length and replaces every bar after i with an unrelated path: the row at i must not change.

Fast (CI, synthetic bars, every registered per-cell detector); the real-bar run is the selftest set
(`scripts/eval2_selftest.py --only S6`)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.research.catalogue import core as K
from goldbot.research.catalogue import data as DA
from goldbot.research.catalogue import dispatcher as D
from goldbot.research.catalogue.core import EB
from goldbot.research.catalogue.data import DAY, HOUR, Bars
from tests.test_catalogue_lookahead import INST_FOR, SYNTH_KW, TEST_OVERRIDE, TF_FOR, ctx_for, synth

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = 12                    # events per detector (the earliest, the latest and a spread between)


def _st() -> object:
    if "eval2_selftest" in sys.modules:
        return sys.modules["eval2_selftest"]
    spec = importlib.util.spec_from_file_location("eval2_selftest", ROOT / "scripts" / "eval2_selftest.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["eval2_selftest"] = mod
    spec.loader.exec_module(mod)
    return mod


ST = _st()


def _sample(n_ev: int) -> np.ndarray:
    return np.unique(np.linspace(0, n_ev - 1, min(n_ev, SAMPLES)).astype(int))


def _params(det: str) -> dict:
    us = [u for u in D.units() if u["detector"] == det]
    return {**(us[0]["params"] if us else {}), **TEST_OVERRIDE.get(det, {})}


@pytest.mark.parametrize("det", sorted(D.DETECTORS))
def test_signal_atr_and_twin_geometry_are_identical_on_bars_cut_at_the_decision_close(det: str) -> None:
    tf = TF_FOR.get(det, "1d")
    b = synth(INST_FOR.get(det, "XAUUSD"), tf, n=1200 if tf != "5m" else 1500,
              gap=SYNTH_KW.get(det, {}).get("gap", 0.001))
    ctx = ctx_for(b)
    fn = D.DETECTORS[det]
    p = _params(det)
    ev = fn(b, p, ctx)
    ev = ev.subset(ev.i < len(b) - 20)          # leave room for a twin decided 15 bars later
    if not len(ev):
        pytest.skip(f"{det}: no event on the synthetic series (C08 is too rare)")
    r = ST.truncation_mismatches(b, ev, _sample(len(ev)), detector=lambda bb: fn(bb, p, ctx))  # type: ignore[attr-defined]
    assert r["checked"] > 0
    assert (r["signal"], r["atr14"], r["atr20"], r["twin"]) == (0, 0, 0, 0), r


def _leaky(b: Bars, p: dict, ctx: object) -> K.Events:
    """A detector that peeks one bar ahead: long when the NEXT close is up."""
    a = K.atr(b, 14)
    eb = EB()
    for j in range(20, len(b) - 1):
        if b.c[j + 1] > b.c[j] and j % 7 == 0 and np.isfinite(a[j]):
            eb.add(j, 1, stop_d=a[j], tgt_d=2 * a[j], hold=5)
    return eb.build()


def _leaky_level(b: Bars, p: dict, ctx: object) -> K.Events:
    """A detector whose event exists without look-ahead but whose STOP uses the next bar's low."""
    a = K.atr(b, 14)
    eb = EB()
    for j in range(20, len(b) - 1, 9):
        if np.isfinite(a[j]):
            eb.add(j, 1, stop=min(b.l[j], b.l[j + 1]) - 0.1 * a[j], tgt_d=2 * a[j], hold=5)
    return eb.build()


@pytest.mark.parametrize("leaky", [_leaky, _leaky_level], ids=["next-close entry", "next-low stop"])
def test_a_detector_that_peeks_one_bar_ahead_is_caught(leaky: object) -> None:
    """Mutation check: the S6 comparison flags a one-bar look-ahead the prefix test's n - 2 rule would let through."""
    b = synth("XAUUSD", "1h", n=600)
    ev = leaky(b, {}, None)  # type: ignore[operator]
    r = ST.truncation_mismatches(b, ev, _sample(len(ev)), detector=lambda bb: leaky(bb, {}, None))  # type: ignore[attr-defined,operator]
    assert r["signal"] > 0, r


def test_a_twin_whose_stop_read_the_bar_after_its_decision_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation check on the twin geometry: an ATR that leaks one bar ahead changes the twin's stop on the cut."""
    b = synth("XAUUSD", "1h", n=600)
    ev = _leaky_level(b, {}, None)
    ev = ev.subset(ev.i < len(b) - 20)
    real = K.atr
    monkeypatch.setattr(K, "atr", lambda bb, n: np.append(real(bb, n)[1:], np.nan))   # ATR[i] = true ATR[i + 1]
    r = ST.truncation_mismatches(b, ev, _sample(len(ev)))  # type: ignore[attr-defined]
    assert r["twin"] > 0 and r["atr14"] > 0, r


def test_signals_in_the_last_bars_are_checked_without_a_twin_and_never_crash() -> None:
    """Boundary (seen on real 1h bars, selftest seed): a signal at the data end has no later bar for a twin; the
    truncation check of its signal and ATR still runs, the twin check is skipped and counted apart."""
    b = synth("XAUUSD", "1h", n=300)
    a = K.atr(b, 14)
    eb = EB()
    for j in (len(b) - 3, len(b) - 2, len(b) - 1):
        eb.add(j, 1, stop_d=a[j], tgt_d=2 * a[j], hold=5)
    ev = eb.build()
    r = ST.truncation_mismatches(b, ev, list(range(len(ev))))  # type: ignore[attr-defined]
    assert r["checked"] == 3 and r["twin_checked"] == 1 and r["atr14"] == 0 and r["twin"] == 0, r


def test_higher_timeframe_bars_are_visible_only_after_their_close() -> None:
    lo = synth("XAUUSD", "1h", n=2000)
    hi = DA._frame_to_bars("XAUUSD", "4h", pd.DataFrame({
        "t": pd.to_datetime(lo.t[::4], utc=True), "ta": pd.to_datetime(lo.t[::4] + 4 * HOUR, utc=True),
        "o": lo.o[::4], "h": lo.h[::4], "l": lo.l[::4], "c": lo.c[::4]}))
    assert ST.htf_visibility_violations(lo, hi) == 0  # type: ignore[attr-defined]
    early = Bars(hi.inst, hi.tf, hi.t, hi.t + 2 * HOUR, hi.o, hi.h, hi.l, hi.c, hi.spr, hi.co, hi.seg, hi.vol)
    stamped = DA.asof_ns(lo.ta, early.ta, np.arange(len(early), dtype=float))   # a 4h bar stamped 2h early
    k = stamped[np.isfinite(stamped)].astype(int)
    assert (hi.ta[k] > lo.ta[np.isfinite(stamped)]).any(), "the mutation must expose a not-yet-closed 4h bar"


def test_a_weekly_bar_is_known_only_at_its_last_daily_close() -> None:
    days = pd.date_range("2012-01-02 22:00", periods=60, freq="D", tz="UTC")
    days = days[days.weekday < 5]
    d = pd.DataFrame({"t": days - pd.Timedelta(hours=24) + pd.Timedelta(hours=0), "ta": days, "o": 1.0, "h": 2.0,
                      "l": 0.5, "c": np.arange(len(days), dtype=float), "spr": 0.1, "vol": 1.0, "src": "x"})
    w = DA._weekly(d)
    ta_d = DA.epoch_ns(d["ta"])
    for _, row in w.iterrows():
        ta_w = int(pd.Timestamp(row["ta"]).value)
        in_week = ta_d[(ta_d > ta_w - 7 * DAY) & (ta_d <= ta_w)]
        assert ta_w == int(in_week.max()), "a weekly bar must be known at the week's last daily close, not before"
        assert row["c"] == d["c"].to_numpy()[ta_d == ta_w][0]


@pytest.mark.skip(reason="no regime code in the harness yet: no registered card carries a regime level; the tercile "
                         "check (trailing 250 sessions to the prior close) lands with the first card that registers one")
def test_regime_terciles_come_from_the_trailing_250_sessions_to_the_prior_close() -> None:
    raise AssertionError


def test_s6_fast_run_passes_on_synthetic_bars() -> None:
    r = ST.s6_truncation(samples=120, seed_key="ci-s6", fast=True)  # type: ignore[attr-defined]
    assert r.passed and r.metrics["checked"] > 0, r


@pytest.mark.selftest
@pytest.mark.skipif(not (DA.raw_dir() / "xauusd_1m_dukascopy_2017.parquet").exists(),
                    reason="real-bar S6 needs local raw/ market data (never in CI or git)")
def test_s6_on_real_explore_bars() -> None:
    r = ST.s6_truncation(samples=2_000, seed_key="selftest-s6", fast=False)  # type: ignore[attr-defined]
    print(r)
    assert r.passed, r
