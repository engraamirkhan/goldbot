"""goldbot.research.arm_rules_channels: the wave-2 channel arms KSQ, DON, C30 and the C30C control (ARM-SPEC 2026-10-25
section 8 and amendment 8.13). Causal closed-bar entries with prefix invariance, fixed-price brackets, the control's
pairing, live == history on the engine's own bar window, and the 2010-2017 history counts against the slice figures."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.data.resample import mid, resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.data.timeutil import utc_index
from goldbot.engine.exploration import _bars as engine_bars
from goldbot.features.technical import atr
from goldbot.research import arm_rules as R
from goldbot.research import arm_rules_channels as C
from goldbot.research import arms as A

NO = np.zeros(0, dtype=np.int64)
CASES = (("squeeze_release", "1h", 4), ("squeeze_release", "4h", 4), ("donchian_break", "4h", 24),
         ("donchian_break", "1d", 10), ("low30_touch", "15m", 460), ("low30_control", "15m", 460))
CUT = pd.Timestamp("2024-01-10", tz="UTC")       # a Wednesday 00:00 UTC: whole bars of every timeframe on both sides
RAW = Path("/Users/aamirkhan/tmp/ts/raw")


@pytest.fixture(scope="module")
def b1() -> pd.DataFrame:
    return ticks_to_1m(synthetic_ticks("2023-09-01", "2024-01-31", ticks_per_minute=1, seed=11))


def _arm(rule: str, tf: str, hold: int, arm_id: str = "X") -> A.Arm:
    return A.Arm(id=arm_id, rule=rule, timeframe=tf, params={"hold_bars": hold})  # type: ignore[arg-type]


def _ctx(b1: pd.DataFrame, tf: str) -> R.RuleContext:
    return R.RuleContext(bars=resample_bars(b1, tf).reset_index(drop=True), b1=b1.reset_index(drop=True),
                         blackout_lo=NO, blackout_hi=NO)


def _run(rule: str, tf: str, hold: int, b1: pd.DataFrame) -> tuple[pd.DataFrame, R.RuleContext]:
    ctx = _ctx(b1, tf)
    return R.RULE_FUNCS[rule](_arm(rule, tf, hold, "C30C" if rule == "low30_control" else "X"), ctx), ctx


@pytest.mark.parametrize(("rule", "tf", "hold"), CASES)
def test_appending_bars_never_changes_a_past_candidate(b1, rule, tf, hold) -> None:
    """8.0: one function in history and live; every candidate decided by the cut is the same on the shorter input."""
    full, full_ctx = _run(rule, tf, hold, b1)
    part, _ = _run(rule, tf, hold, b1[np.asarray(utc_index(b1["ts_utc"]) < CUT)])
    assert len(full) > 0, "the fixture must give candidates, else the check is vacuous"
    decided = np.asarray(full_ctx.close_ts[full["idx"].to_numpy()] <= CUT)
    assert decided.any()
    pd.testing.assert_frame_equal(full[decided].reset_index(drop=True), part.reset_index(drop=True))


def test_squeeze_release_follows_the_frozen_definition_and_brackets(b1) -> None:
    got, ctx = _run("squeeze_release", "1h", 4, b1)
    m = mid(ctx.bars).reset_index(drop=True)
    c, a = m["close"], atr(m, 14)
    bb_mid, sd = c.rolling(20).mean(), c.rolling(20).std(ddof=0)
    kc = c.ewm(span=20, adjust=False, min_periods=20).mean()
    on = ((bb_mid + 2 * sd) < (kc + 1.5 * a)) & ((bb_mid - 2 * sd) > (kc - 1.5 * a))
    m20 = (m["high"].rolling(20).max() + m["low"].rolling(20).min()) / 2
    for i, side in zip(got["idx"], got["side"]):
        assert not on[i] and on[i - 6:i].all()                                   # off at i, on at i-6..i-1
        assert side == np.sign(c[i] - (m20[i] + bb_mid[i]) / 2)
    np.testing.assert_allclose(got["risk_usd"], a.to_numpy()[got["idx"]])         # stop 1.0 ATR14_i
    assert (got["target_r"] == C.NO_TARGET_R).all() and (got["max_bars"] == 3).all()   # exit at the close of bar i+4


@pytest.mark.parametrize(("tf", "hold"), [("4h", 24), ("1d", 10)])
def test_donchian_break_is_the_first_close_beyond_the_prior_55_bar_extreme(b1, tf, hold) -> None:
    got, ctx = _run("donchian_break", tf, hold, b1)
    m = mid(ctx.bars).reset_index(drop=True)
    h, lo, c = (m[k].to_numpy() for k in ("high", "low", "close"))
    for i, side in zip(got["idx"], got["side"]):
        up, dn = h[i - 55:i].max(), lo[i - 55:i].min()
        up1, dn1 = h[i - 56:i - 1].max(), lo[i - 56:i - 1].min()
        assert (c[i] > up and c[i - 1] <= up1) if side == 1 else (c[i] < dn and c[i - 1] >= dn1)
    np.testing.assert_allclose(got["risk_usd"], atr(m, 14).to_numpy()[got["idx"]])
    assert (got["target_r"] == C.NO_TARGET_R).all() and (got["max_bars"] == hold - 1).all()


def test_c30_shorts_the_first_touch_of_the_30_day_low_once_a_day_with_half_atr_brackets(b1) -> None:
    got, ctx = _run("low30_touch", "15m", 460, b1)
    m = mid(ctx.bars).reset_index(drop=True)
    day = C.trading_day(utc_index(m["ts_utc"]))
    daily = m.assign(day=day).groupby("day").agg(high=("high", "max"), low=("low", "min"), close=("close", "last"))
    daily = daily.iloc[1:]
    a = atr(daily, 14)
    assert (got["side"] == -1).all() and (got["target_r"] == 1.0).all() and (got["max_bars"] == 459).all()
    assert len(set(day[got["idx"]])) == len(got)                                   # at most one per trading day
    for i, risk in zip(got["idx"], got["risk_usd"]):
        k = int(np.searchsorted(daily.index.to_numpy(), day[i]))
        assert k >= 30
        l30 = daily["low"].iloc[k - 30:k].min()
        same_day = np.flatnonzero(day == day[i])
        assert m["low"][i] <= l30 and (m["low"].to_numpy()[same_day[same_day < i]] > l30).all()   # the first touch
        assert risk == pytest.approx(0.5 * a.iloc[k - 1])                         # 0.5 x ATR14(1d) of D-1


def test_c30c_pairs_with_c30a_short_on_the_same_bracket_and_calendar(b1) -> None:
    ctrl, ctx = _run("low30_control", "15m", 460, b1)
    day, l30, a_prev = C.low30_context(ctx)
    assert len(ctrl) > 0
    idx = ctrl["idx"].to_numpy()
    assert (ctrl["side"] == -1).all() and (ctrl["target_r"] == 1.0).all() and (ctrl["max_bars"] == 459).all()
    np.testing.assert_allclose(ctrl["risk_usd"], 0.5 * a_prev[idx])               # C30A's stop on that day
    assert np.isfinite(l30[idx]).all()                                             # only days C30A can trade
    assert all(R.hash_u("C30C", ctx.close_ts[int(i)]) < C.C30C_RATE for i in idx)
    eligible = np.isfinite(l30) & np.isfinite(a_prev) & C.wave2_ok(ctx.close_ts)
    drawn = [i for i in np.flatnonzero(eligible) if R.hash_u("C30C", ctx.close_ts[int(i)]) < C.C30C_RATE]
    assert idx.tolist() == drawn                                                  # every hour of the trading day
    other = R.RULE_FUNCS["low30_control"](_arm("low30_control", "15m", 460, "C30D"), ctx)
    assert other["idx"].tolist() != idx.tolist()                                   # the seed is the arm's id


def test_wave2_filters_drop_the_new_york_reopen_windows_and_listed_holidays() -> None:
    ny = pd.DatetimeIndex([pd.Timestamp(f"2026-03-03 {t}", tz="America/New_York")
                           for t in ("16:50", "16:55", "17:00", "17:15", "18:00", "18:30", "18:45")]).tz_convert("UTC")
    assert C.wave2_ok(ny).tolist() == [True, False, False, True, False, False, True]
    good_friday = pd.DatetimeIndex([pd.Timestamp("2026-04-03 10:00", tz="UTC"), pd.Timestamp("2026-04-02 10:00", tz="UTC")])
    assert C.wave2_ok(good_friday).tolist() == [False, True]


@pytest.mark.parametrize(("rule", "tf", "hold"), [("squeeze_release", "1h", 4), ("donchian_break", "4h", 24)])
def test_live_on_the_engines_bar_window_equals_history(b1, rule, tf, hold) -> None:
    """The engine (`exploration.step`) gives the rule its last LOOKBACK bars of tf and the 1m bars from their start; the
    candidate on the bar that just closed is the history run's candidate on that bar."""
    hist, ctx = _run(rule, tf, hold, b1)
    arm = _arm(rule, tf, hold)
    by_close = {ctx.close_ts[int(i)]: row for i, row in zip(hist["idx"], hist.to_dict("records"))}
    closes = list(ctx.close_ts[-300:])
    seen = 0
    for close in closes:
        complete = b1[b1["visible_at"] <= close].tail(60000)                       # the engine's 1m memory
        bars = engine_bars(complete, tf, close)
        assert bars is not None
        live_b1 = complete[complete["ts_utc"] >= pd.Timestamp(bars["ts_utc"].iloc[0])].reset_index(drop=True)
        live = R.RULE_FUNCS[rule](arm, R.RuleContext(bars=bars, b1=live_b1, blackout_lo=NO, blackout_hi=NO))
        last = live[live["idx"] == len(bars) - 1]
        if close in by_close:
            seen += 1
            assert len(last) == 1
            row = by_close[close]
            assert int(last["side"].iloc[0]) == row["side"]
            assert float(last["risk_usd"].iloc[0]) == pytest.approx(row["risk_usd"], rel=1e-4)
            assert int(last["max_bars"].iloc[0]) == row["max_bars"]
        else:
            assert last.empty
    assert seen > 0


def test_c30_fails_closed_on_fewer_than_30_completed_days(b1) -> None:
    """The engine's 15m window (200 bars, about two days) cannot hold L30: no candidate, never one from a short low."""
    _, ctx = _run("low30_touch", "15m", 460, b1)
    short = ctx.bars.iloc[-200:].reset_index(drop=True)
    sctx = R.RuleContext(bars=short, b1=b1, blackout_lo=NO, blackout_hi=NO)
    for rule, arm_id in (("low30_touch", "X"), ("low30_control", "C30C")):
        assert R.RULE_FUNCS[rule](_arm(rule, "15m", 460, arm_id), sctx).empty


def test_a_foreign_timeframe_or_hold_is_refused_at_load() -> None:
    with pytest.raises(ValueError, match="runs on"):
        _arm("donchian_break", "1h", 24)
    with pytest.raises(ValueError, match="hold_bars"):
        _arm("squeeze_release", "1h", 5)
    with pytest.raises(ValueError, match="hold_bars"):
        A.Arm(id="X", rule="low30_touch", timeframe="15m", params={})
    for rule in C.RULES:
        assert rule in A.RULES and rule in R.RULE_FUNCS


# ---------------------------------------------------------------- 2010-2017 history counts against the slice figures
def _load_1m() -> pd.DataFrame:
    parts = [pd.read_parquet(RAW / f"xauusd_1m_dukascopy_{y}.parquet") for y in range(2010, 2018)]
    d = pd.concat(parts, ignore_index=True)
    d["ts_utc"] = pd.to_datetime(d["ts_utc"], utc=True).astype("datetime64[ns, UTC]")
    d = d.sort_values("ts_utc").drop_duplicates("ts_utc").reset_index(drop=True)
    if "visible_at" not in d:
        d["visible_at"] = d["ts_utc"] + pd.Timedelta(minutes=1)
    return d


def _dedup(idx: np.ndarray, gap: int) -> int:
    n, last = 0, -10 ** 9
    for i in idx:
        if i - last >= gap:
            n, last = n + 1, i
    return n


@pytest.mark.integration          # slow: 8 years of 1m bars (no `slow` marker in this repo)
@pytest.mark.skipif(not RAW.exists(), reason="local 2010-2017 data (data-v1) not present")
def test_history_counts_2010_2017_reproduce_the_slice_figures() -> None:
    """Slice F (volatility-bands.md): squeeze releases 1h 566, 4h 94; DC55 breakouts (one per 4 bars) 4h 512, 1d 77.
    Slice C (fib-wavelets-cycles.md C-1): break-low 310 raw, about 118 non-overlapping (8.4: "about 15 a year"). Within
    10% (the PDC precedent of 8.13) where the definitions coincide; DOND's day is the 13:30 New York settlement (module
    docstring), C30's touch window is its own trading day (8.4's declared deviation), so those two are bounded wider."""
    b1 = _load_1m()
    got: dict[str, int] = {}
    for key, rule, tf, hold in (("ksq1", "squeeze_release", "1h", 4), ("ksq4", "squeeze_release", "4h", 4),
                                ("don4", "donchian_break", "4h", 24), ("dond", "donchian_break", "1d", 10),
                                ("c30", "low30_touch", "15m", 460)):
        ctx = _ctx(b1, tf)
        c = C.RULES[rule](_arm(rule, tf, hold), ctx)
        bars = mid(ctx.bars).reset_index(drop=True)
        got[key] = _dedup(c["idx"].to_numpy(), 4) if rule == "donchian_break" else len(c)
        if rule != "low30_touch":                                        # "no target" stays out of reach
            hi, lo, cl = (bars[k].to_numpy() for k in ("high", "low", "close"))
            for i, side, risk in zip(c["idx"], c["side"], c["risk_usd"]):
                path = slice(i + 1, i + 1 + hold)
                fav = (hi[path].max() - cl[i]) if side > 0 else (cl[i] - lo[path].min())
                assert fav < C.NO_TARGET_R * risk
    print(got)
    # the wave-2 NY filters drop a few candidates the slice kept, so the count may sit a little under the figure
    assert 0.85 * 566 <= got["ksq1"] <= 1.10 * 566
    assert 0.85 * 94 <= got["ksq4"] <= 1.10 * 94
    assert 0.85 * 512 <= got["don4"] <= 1.10 * 512
    assert 0.70 * 77 <= got["dond"] <= 1.30 * 77
    assert 118 * 0.7 <= got["c30"] <= 310
