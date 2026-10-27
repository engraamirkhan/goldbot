"""goldbot.research.arm_rules_bands: the time-structure band-break arm rules F1A/F1C (H-70, prereg-2026-10-26-time-
structure.md section 2) and F2A/F2C (arms-2026-10-25.md amendment 8.13). Causal on closed bars, prefix invariant (the
Hurst window and the 4h join included), the stop/no-target/time exit as frozen, the controls paired as specified, live
(a trailing window of LOOKBACK_BARS) equal to the history run, and the 2010-2017 counts near slice F's."""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.data.resample import resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.data.timeutil import utc_index
from goldbot.features.technical import atr
from goldbot.research import arm_rules as R
from goldbot.research import arm_rules_bands as B
from goldbot.research import arms as A

NO = np.zeros(0, dtype=np.int64)
RULES = tuple(B.RULES)
IDS = {"hurst_band_break": "F1A", "hurst_band_break_control": "F1C", "htf_stretch_break": "F2A",
       "htf_stretch_break_control": "F2C"}
CUT = pd.Timestamp("2024-02-14", tz="UTC")
RAW = Path(os.environ.get("GOLDBOT_RAW_BARS", str(Path(__file__).resolve().parents[1] / "raw")))   # the gitignored local bars (ADR 0048)


@pytest.fixture(scope="module")
def b1() -> pd.DataFrame:
    return ticks_to_1m(synthetic_ticks("2023-10-01", "2024-03-15", ticks_per_minute=1, seed=7)).reset_index(drop=True)


@pytest.fixture(scope="module")
def full(b1) -> R.RuleContext:
    return _ctx(b1)


def _ctx(b1: pd.DataFrame) -> R.RuleContext:
    return R.RuleContext(bars=resample_bars(b1, "15m").reset_index(drop=True), b1=b1.reset_index(drop=True),
                         blackout_lo=NO, blackout_hi=NO)


def _arm(rule: str) -> A.Arm:
    return A.Arm(id=IDS[rule], rule=rule, timeframe="15m",
                 params={"min_stop_usd": 0.0, "max_stop_usd": 1e9, "hold_bars": 4})


@pytest.fixture(scope="module")
def runs(full) -> dict[str, pd.DataFrame]:
    return {r: R.RULE_FUNCS[r](_arm(r), full) for r in RULES}


@pytest.mark.parametrize("rule", RULES)
def test_every_rule_fires_on_the_fixture_so_no_check_below_is_vacuous(runs, rule) -> None:
    assert len(runs[rule]) >= 10


@pytest.mark.parametrize("rule", RULES)
def test_appending_bars_never_changes_a_past_candidate_hurst_window_and_4h_join_included(b1, full, runs, rule) -> None:
    """Live sees the bars up to now, history all of them: every candidate decided by the cut is the same."""
    head = b1[np.asarray(utc_index(b1["ts_utc"]) < CUT)].reset_index(drop=True)
    part = R.RULE_FUNCS[rule](_arm(rule), _ctx(head))
    got = runs[rule]
    decided = np.asarray(full.close_ts[got["idx"].to_numpy()] <= CUT)
    assert len(part) > 0
    pd.testing.assert_frame_equal(got[decided].reset_index(drop=True), part.reset_index(drop=True))


@pytest.mark.parametrize("rule", RULES)
def test_live_on_its_trailing_lookback_window_equals_the_history_run(b1, full, runs, rule) -> None:
    """The engine hands a rule the last LOOKBACK_BARS decision bars and the 1m bars from the first of them
    (`exploration._bars` / `step`); at every sampled close the window's last-bar candidate is the history run's."""
    n = B.LOOKBACK_BARS[rule]
    bars = full.bars
    hist = runs[rule].set_index("idx")
    later = [int(i) for i in hist.index if i >= n]
    rng = np.random.default_rng(3)
    quiet = [int(i) for i in rng.choice(np.arange(n, len(bars)), 6, replace=False) if i not in hist.index]
    sample = later[:6] + quiet
    assert len(later) >= 3
    ts1 = utc_index(b1["ts_utc"])
    for i in sample:
        win = bars.iloc[i - n + 1:i + 1].reset_index(drop=True)
        close = pd.Timestamp(win["visible_at"].iloc[-1])
        w1 = b1[np.asarray((ts1 >= pd.Timestamp(win["ts_utc"].iloc[0])) & (ts1 < close))].reset_index(drop=True)
        live = R.RULE_FUNCS[rule](_arm(rule), R.RuleContext(bars=win, b1=w1, blackout_lo=NO, blackout_hi=NO))
        last = live[live["idx"] == n - 1]
        if i in hist.index:
            assert len(last) == 1, f"history fires at {close}, live does not"
            row = hist.loc[i]
            assert int(last["side"].iloc[0]) == int(row["side"])
            assert float(last["risk_usd"].iloc[0]) == pytest.approx(float(row["risk_usd"]), rel=1e-9)
        else:
            assert last.empty, f"live fires at {close}, history does not"


@pytest.mark.parametrize("rule", RULES)
def test_the_stop_is_one_atr14_at_the_signal_bar_with_no_target_and_the_time_exit_at_bar_i_plus_4(full, runs,
                                                                                                    rule) -> None:
    got = runs[rule]
    a = atr(full.mid, 14).to_numpy()
    np.testing.assert_allclose(got["risk_usd"], a[got["idx"].to_numpy()])
    assert (got["target_r"] == B.NO_TARGET_R).all() and B.NO_TARGET_R >= 50
    # triple_barrier_1m's count: the exit is the close of bar idx + 1 + max_bars = idx + 4; the engine's hold is 4 bars
    assert (got["max_bars"] == 3).all()


@pytest.mark.parametrize("rule", RULES)
def test_the_stop_band_skips_a_stop_outside_it_and_keeps_one_inside(full, rule) -> None:
    arm = A.Arm(id=IDS[rule], rule=rule, timeframe="15m", params={"min_stop_usd": 3.0, "max_stop_usd": 8.64,
                                                                  "hold_bars": 4})
    raw = R.RULE_FUNCS[rule](arm, full)
    kept = R.candidates(arm, full)
    inside = raw[(raw["risk_usd"] >= 3.0) & (raw["risk_usd"] <= 8.64)]
    assert set(kept["idx"]) <= set(inside["idx"]) and ((kept["risk_usd"] >= 3.0) & (kept["risk_usd"] <= 8.64)).all()


def test_f1a_trades_with_the_break_in_the_low_tercile_against_it_in_the_high_and_never_in_the_middle(full,
                                                                                                    runs) -> None:
    got = runs["hurst_band_break"]
    idx = got["idx"].to_numpy()
    brk, terc = B.band_breaks(full.mid), B.hurst_tercile(full.mid)
    assert (brk[idx] != 0).all() and set(terc[idx]) <= {0, 2}
    expect = np.where(terc[idx] == 0, brk[idx], -brk[idx])
    assert got["side"].tolist() == expect.tolist()
    assert (np.diff(idx) >= 4).all()
    assert {0, 2} <= set(terc[idx])                                  # both arms of the switch occur


def test_a_break_needs_the_previous_close_inside_the_band() -> None:
    c = np.r_[np.full(40, 100.0) + np.tile([0.0, 0.1], 20), 105.0, 106.0]
    m = pd.DataFrame({"open": c, "high": c + 0.05, "low": c - 0.05, "close": c})
    brk = B.band_breaks(m)
    assert brk[40] == 1 and brk[41] == 0 and (brk[:40] == 0).all()


def test_the_hurst_tercile_is_undefined_until_250_ranked_values_exist(full) -> None:
    terc = B.hurst_tercile(full.mid)
    first = int(np.flatnonzero(terc >= 0)[0])
    assert first == 128 + 249                                        # hurst from bar 128, 250 of them from bar 377


def test_f1c_fires_at_every_f1a_decision_time_with_the_side_of_its_own_hash(full, runs) -> None:
    a, c = runs["hurst_band_break"], runs["hurst_band_break_control"]
    assert c["idx"].tolist() == a["idx"].tolist()
    close = full.close_ts
    assert c["side"].tolist() == [R.hash_side("F1C", close[int(i)]) for i in c["idx"]]
    np.testing.assert_allclose(c["risk_usd"], a["risk_usd"])
    assert (c["side"] != a["side"]).any() and (c["side"] == a["side"]).any()


def test_f2a_trades_with_a_stretched_4h_against_the_15m_break(full, runs) -> None:
    got = runs["htf_stretch_break"]
    idx = got["idx"].to_numpy()
    pos, brk = B.htf_kc_pos(full), B.band_breaks(full.mid)
    assert (np.abs(pos[idx]) >= 1).all()
    assert got["side"].tolist() == np.sign(pos[idx]).astype(int).tolist()
    assert (brk[idx] == -got["side"].to_numpy()).all()
    assert not B._ny_reopen(full.close_ts[idx]).any()


def test_f2c_is_a_seeded_tenth_of_the_stretched_closes_without_an_f2a_event(full, runs) -> None:
    a, c = runs["htf_stretch_break"], runs["htf_stretch_break_control"]
    close = full.close_ts
    pos = B.htf_kc_pos(full)
    with np.errstate(invalid="ignore"):
        eligible = np.isfinite(pos) & (np.abs(pos) >= 1)
    eligible[a["idx"].to_numpy()] = False
    eligible &= ~B._ny_reopen(close)
    expect = [int(i) for i in np.flatnonzero(eligible) if R.hash_u("F2C", close[int(i)]) < 0.10]
    assert c["idx"].tolist() == expect
    assert not set(c["idx"]) & set(a["idx"])
    assert c["side"].tolist() == np.sign(pos[c["idx"].to_numpy()]).astype(int).tolist()


def test_the_4h_position_joins_only_4h_bars_closed_by_the_decision_close(b1, full) -> None:
    """pos4 at a close computed from the 1m bars up to that close equals the full run's: a forming 4h bar never joins."""
    pos = B.htf_kc_pos(full)
    ts1 = utc_index(b1["ts_utc"])
    for i in (3000, 3001, 3005, 3015):                                # inside and at the end of a 4h bar
        close = full.close_ts[i]
        head = b1[np.asarray(ts1 < close)].reset_index(drop=True)
        ctx = _ctx(head)
        part = B.htf_kc_pos(ctx)
        assert part[-1] == pytest.approx(pos[i], nan_ok=True)
        assert pd.Timestamp(ctx.bars["visible_at"].iloc[-1]) == close


def test_f2_refuses_a_decision_timeframe_at_or_above_4h(full) -> None:
    arm = A.Arm(id="F2A", rule="htf_stretch_break", timeframe="4h", params={"hold_bars": 4})
    with pytest.raises(ValueError, match="decides below 4h"):
        R.RULE_FUNCS["htf_stretch_break"](arm, full)


def test_the_band_rules_are_registered_arm_rules() -> None:
    for r in RULES:
        assert r in A.RULES and r in R.RULE_FUNCS


@pytest.mark.integration
@pytest.mark.skipif(not (RAW / "xauusd_1m_dukascopy_2017.parquet").exists(), reason="local 2010-2017 data absent")
def test_the_2010_2017_event_counts_reproduce_slice_f_within_10_percent() -> None:
    """Slice F section 6: X1 15m n 10,425; X2 (4h stretch) 15m n 832 (de-duplicated, no filters, no stop band)."""
    from goldbot.data.release import read_bar_files
    b = read_bar_files(RAW, 2010, 2017)
    b = b[np.asarray(utc_index(b["ts_utc"]) < pd.Timestamp("2018-01-01", tz="UTC"))].reset_index(drop=True)
    ctx = _ctx(b)
    for rule, n in (("hurst_band_break", 10_425), ("htf_stretch_break", 832)):
        got = len(R.RULE_FUNCS[rule](_arm(rule), ctx))
        assert abs(got - n) <= 0.10 * n, f"{rule}: {got} events against slice F's {n}"
