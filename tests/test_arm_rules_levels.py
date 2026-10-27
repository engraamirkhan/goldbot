"""goldbot.research.arm_rules_levels: the wave-2 level arm rules `pdc_break` (PDC1/PDC4, ARM-SPEC 8.6 + 8.13) and
`ob_retest` (OBR15, 8.5 + 8.13). Section 8.0: "A level, band, pivot, block or anchor exists only from the close of the
bar that confirms it ... its test proves prefix invariance"; one function for the history matrix and live."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from goldbot.data.resample import mid, resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.data.timeutil import utc_index
from goldbot.engine.exploration import _bars
from goldbot.features.technical import atr
from goldbot.research import arm_rules as R
from goldbot.research import arm_rules_levels as L
from goldbot.research import arms as A

NO = np.zeros(0, dtype=np.int64)
CUT = pd.Timestamp("2024-01-10", tz="UTC")
RAW = Path.home() / "tmp" / "ts" / "raw"
CASES = (("pdc_break", "1h"), ("pdc_break", "4h"), ("ob_retest", "15m"))


@pytest.fixture(scope="module")
def b1() -> pd.DataFrame:
    return ticks_to_1m(synthetic_ticks("2023-10-01", "2024-01-31", ticks_per_minute=1, seed=11))


def _ctx(b1: pd.DataFrame, tf: str) -> R.RuleContext:
    return R.RuleContext(bars=resample_bars(b1, tf).reset_index(drop=True), b1=b1.reset_index(drop=True),
                         blackout_lo=NO, blackout_hi=NO)


def _arm(rule: str, tf: A.Timeframe) -> A.Arm:
    return A.Arm(id="X", rule=rule, timeframe=tf, params={"stop_atr": 1.0, "target_r": 2.0, "hold_bars": 32})


def test_the_level_rules_are_registered_arm_rules() -> None:
    for rule in ("pdc_break", "ob_retest"):
        assert rule in A.RULES and rule in R.RULE_FUNCS


# ---------------------------------------------------------------- PDC
@pytest.mark.parametrize("tf", ["1h", "4h"])
def test_pdc_breaks_close_beyond_a_previous_day_close_known_before_the_bar_from_the_second_bar_of_the_day(b1, tf):
    ctx = _ctx(b1, tf)
    m = ctx.mid
    ev = L.pdc_events(m, b1)
    assert len(ev) > 5, "the fixture must give events, else the checks are vacuous"
    m1 = mid(b1).reset_index(drop=True)
    t1 = utc_index(m1["ts_utc"])
    d1 = L.trading_day(t1)
    start, close = utc_index(m["ts_utc"]), ctx.close_ts
    a = atr(m, 14).to_numpy()
    days = L.trading_day(start[ev["idx"].to_numpy()])
    assert len(set(days)) == len(days), "one a day"
    for (j, side, level, aj), day in zip(ev.itertuples(index=False, name=None), days, strict=True):
        prev = d1 < day
        assert prev.any()
        # the level: the last 1m close of the previous trading day, which closed before the decision bar did
        assert level == m1["close"].to_numpy()[np.flatnonzero(prev)[-1]]
        assert t1[np.flatnonzero(prev)[-1]] + pd.Timedelta(minutes=1) <= close[j]
        # the reopen bar (holding the day's first minute) is never the touch bar
        first = t1[np.flatnonzero(d1 == day)[0]]
        assert start[j] > first
        assert m["low"].iloc[j] <= level <= m["high"].iloc[j]
        assert side * (m["close"].iloc[j] - level) >= 0.1 * a[j - 1] - 1e-9 and aj == a[j - 1]
        # first touch: no earlier window bar of the day (after the reopen bar) touched the level
        r = int(np.flatnonzero((start <= first) & (close > first))[0])
        for q in range(r + 1, j):
            assert not (m["low"].iloc[q] <= level <= m["high"].iloc[q])
        # the break is against the reopen bar's side
        assert np.sign(m["close"].iloc[r] - level) == -side


@pytest.mark.parametrize("tf", ["1h", "4h"])
def test_pdc_stop_is_a_fixed_price_one_atr_back_through_the_level_with_a_2r_target_and_32_bar_hold(b1, tf) -> None:
    ctx = _ctx(b1, tf)
    got = R.RULE_FUNCS["pdc_break"](_arm("pdc_break", tf), ctx)
    ev = L.pdc_events(ctx.mid, b1).set_index("idx").loc[got["idx"].to_numpy()]
    assert len(got) > 0
    entry = ctx.mid["close"].to_numpy()[got["idx"].to_numpy()]
    stop = ev["level"].to_numpy() - got["side"].to_numpy() * ev["atr"].to_numpy()
    np.testing.assert_allclose(got["risk_usd"], np.abs(entry - stop))
    assert (got["target_r"] == 2.0).all()
    if tf == "1h":
        assert (got["max_bars"] == 31).all() and "cut_at" not in got
    else:                                   # PDC4, 8.15: the 32-bar hold capped at the week's end under the Friday cut
        from goldbot.research.arm_rules_weekcut import week_hold
        close = ctx.close_ts[got["idx"].to_numpy()]
        np.testing.assert_array_equal(got["max_bars"], week_hold(close, np.full(len(got), 32), "4h") - 1)
        assert (got["max_bars"] < 31).all() and (got["max_bars"] >= 0).all()


def test_no_level_entry_in_the_new_york_reopen_minutes() -> None:
    ts = pd.DatetimeIndex(["2024-01-10 16:55", "2024-01-10 17:00", "2024-01-10 18:15", "2024-01-10 18:31",
                           "2024-01-10 16:50"], tz=L.NY).tz_convert("UTC")
    assert L.reopen_ok(ts).tolist() == [False, False, False, True, True]


# ---------------------------------------------------------------- OBR
def _ob_bars(pivot_to_break: int) -> pd.DataFrame:
    """15m mid bars: flat warm-up, a swing high at bar 20, a bearish block bar, then a displacement break of the pivot
    `pivot_to_break` bars after it and a retest of the block."""
    rows = [(100.0, 100.5, 99.5, 100.2 if i % 2 else 99.8) for i in range(20)]
    rows.append((100.5, 101.5, 100.0, 100.8))                                    # p = 20, the swing high
    rows += [(100.3, 100.6, 99.6, 99.9)] * (pivot_to_break - 2)                  # lower bars
    rows.append((100.2, 100.4, 99.0, 99.7))                                      # the last bearish bar (block)
    rows.append((99.8, 102.7, 99.7, 102.6))                                      # b: displacement close above P
    rows.append((101.5, 101.8, 100.3, 101.0))                                    # j: retest of the zone, close above
    rows += [(101.0, 101.4, 100.6, 101.2)] * 5
    ts = pd.date_range("2024-01-10 08:00", periods=len(rows), freq="15min", tz="UTC")
    o, h, lo_, c = (np.array(x) for x in zip(*rows, strict=True))
    return pd.DataFrame({"ts_utc": ts, "visible_at": ts + pd.Timedelta(minutes=15), "open": o, "high": h, "low": lo_,
                         "close": c})


def test_an_order_block_needs_its_broken_pivot_confirmed_by_the_close_of_the_bar_before_the_break() -> None:
    late = L.ob_events(_ob_bars(6))                          # b = p + 6: confirmed at the close of p + 5 = b - 1
    longs = late[late["side"] == 1]
    assert longs["idx"].tolist() == [27]
    a = atr(_ob_bars(6), 14).to_numpy()
    assert longs["stop"].iloc[0] == pytest.approx(99.0 - 0.25 * a[26])
    early = L.ob_events(_ob_bars(5))                         # b = p + 5: confirmed on b itself, does not count
    assert (early["side"] == 1).sum() == 0


def test_a_retest_that_closes_through_the_block_invalidates_it() -> None:
    m = _ob_bars(6)
    m.loc[27, "close"] = 98.9                                # closes below low_o = 99.0
    assert (L.ob_events(m)["side"] == 1).sum() == 0


def test_obr_stop_target_and_hold_are_fixed_at_entry(b1) -> None:
    ctx = _ctx(b1, "15m")
    got = R.RULE_FUNCS["ob_retest"](_arm("ob_retest", "15m"), ctx)
    assert len(got) > 0
    ev = L.ob_events(ctx.mid).drop_duplicates("idx").set_index("idx").loc[got["idx"].to_numpy()]
    entry = ctx.mid["close"].to_numpy()[got["idx"].to_numpy()]
    np.testing.assert_allclose(got["risk_usd"], np.abs(entry - ev["stop"].to_numpy()))
    assert (got["side"].to_numpy() * (entry - ev["stop"].to_numpy()) > 0).all()
    assert (got["target_r"] == 2.0).all() and (got["max_bars"] == 31).all()


# ---------------------------------------------------------------- both: causality, live == history, controls
@pytest.mark.parametrize(("rule", "tf"), CASES)
def test_appending_bars_never_changes_a_past_candidate(b1, rule, tf) -> None:
    arm = _arm(rule, tf)
    head = b1[np.asarray(utc_index(b1["ts_utc"]) < CUT)].reset_index(drop=True)
    full_ctx = _ctx(b1, tf)
    full, part = R.RULE_FUNCS[rule](arm, full_ctx), R.RULE_FUNCS[rule](arm, _ctx(head, tf))
    assert len(part) > 0
    decided = np.asarray(full_ctx.close_ts[full["idx"].to_numpy()] <= CUT)
    pd.testing.assert_frame_equal(full[decided].reset_index(drop=True), part.reset_index(drop=True))


@pytest.mark.parametrize(("rule", "tf"), CASES)
def test_live_on_the_engines_window_decides_as_the_history_run(b1, rule, tf) -> None:
    """The engine passes `_bars` (LOOKBACK bars of the timeframe) and the 1m bars from their first bar; the candidate
    on the bar that just closed is the history run's, at every decision close after the warm-up."""
    arm = _arm(rule, tf)
    hist_ctx = _ctx(b1, tf)
    hist = R.candidates(arm, hist_ctx)
    closes = hist_ctx.close_ts
    warm = closes[0] + pd.Timedelta(days=45)
    picks = [i for i in hist["idx"].tolist() if closes[i] > warm][:12]
    picks += [i for i in range(len(closes)) if closes[i] > warm][::97][:12]
    assert len(picks) > 12
    for i in sorted(set(picks)):
        through = closes[i]
        complete = b1[b1["visible_at"] <= through]
        bars = _bars(complete, tf, through)
        assert bars is not None
        live_b1 = complete[complete["ts_utc"] >= pd.Timestamp(bars["ts_utc"].iloc[0])].reset_index(drop=True)
        live = R.candidates(arm, R.RuleContext(bars=bars, b1=live_b1, blackout_lo=NO, blackout_hi=NO))
        got = live[live["idx"] == len(bars) - 1].drop(columns="idx").reset_index(drop=True)
        want = hist[hist["idx"] == i].drop(columns="idx").reset_index(drop=True)
        # the decision (bar, side, target, hold) is identical; the stop carries Wilder ATR's start residue, because the
        # engine's window is LOOKBACK bars of CLOCK time (fewer bars over a weekend): measured <= 3e-4 of the stop
        pd.testing.assert_frame_equal(got.drop(columns="risk_usd"), want.drop(columns="risk_usd"))
        np.testing.assert_allclose(got["risk_usd"], want["risk_usd"], rtol=1e-3)


def test_each_level_arm_pairs_with_a_control_on_its_own_timeframe() -> None:
    """8.5 / 8.6: OBR15 -> I0 (15m), PDC4 -> W0 (4h), PDC1 -> VC1H (1h, a wave-2 control owed by its own lane)."""
    arms = {a["id"]: a for a in yaml.safe_load(Path("config/exploration_arms.yaml").read_text())["arms"]}
    for arm_id, tf in (("OBR15", "15m"), ("PDC4", "4h"), ("PDC1", "1h")):
        control, ctf = L.CONTROLS[arm_id]
        assert ctf == tf
        if control in arms:
            assert arms[control]["timeframe"] == tf and arms[control]["rule"] == "random_bracket"
    assert {"I0", "W0"} <= set(arms)


# ---------------------------------------------------------------- 8.13: the history count gate for PDC1 / PDC4
@pytest.mark.integration
@pytest.mark.skipif(not (RAW / "xauusd_1m_dukascopy_2017.parquet").exists(), reason="local 2010-2017 1m files absent")
@pytest.mark.parametrize(("tf", "slice_g_n"), [("4h", 600), ("1h", 592)])
def test_pdc_history_count_reproduces_slice_g_within_ten_percent(tf, slice_g_n) -> None:
    """8.13 gate, reconciled by amendment 8.14 (the 0.05 ATR reopen-close skip): the frozen rule without it gave
    727 / 689 (+21% / +16%), a NO-GO; with it the count must sit within 10% of slice G's n."""
    from goldbot.data.resample import BAR_COLUMNS
    parts = [pd.read_parquet(RAW / f"xauusd_1m_dukascopy_{y}.parquet") for y in range(2010, 2018)]
    raw = pd.concat(parts, ignore_index=True).sort_values("ts_utc").drop_duplicates("ts_utc").reset_index(drop=True)
    bars = resample_bars(raw[BAR_COLUMNS], tf)
    n = len(L.pdc_events(mid(bars).reset_index(drop=True), raw))
    assert abs(n - slice_g_n) <= 0.10 * slice_g_n, n
