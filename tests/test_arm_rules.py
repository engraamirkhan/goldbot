"""goldbot.research.arm_rules: the ARM-SPEC rules on synthetic bars (schedules, the sha256 seed, the common filters,
point-in-time candidates) and the blackout windows."""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.data.resample import resample_bars
from goldbot.research import arm_rules as R
from goldbot.research import arms as A
from tests.test_count_owed_events import _bars_1m

ROOT = Path(__file__).resolve().parents[1]
ARMS = {a.id: a for a in A.load_arms(ROOT / "config" / "exploration_arms.yaml")}
NO = (np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64))


def _ctx(b1: pd.DataFrame, tf: str, blackout: tuple[np.ndarray, np.ndarray] = NO) -> R.RuleContext:
    bars = b1.reset_index(drop=True) if tf == "1m" else resample_bars(b1, tf).reset_index(drop=True)
    return R.RuleContext(bars=bars, b1=b1.reset_index(drop=True), blackout_lo=blackout[0], blackout_hi=blackout[1])


@pytest.fixture(scope="module")
def b1() -> pd.DataFrame:
    return _bars_1m("2023-01-02", "2023-03-03")


def test_seed_is_the_frozen_sha256_of_arm_and_close_minute() -> None:
    close = pd.Timestamp("2023-03-01 08:10", tz="UTC")
    digest = hashlib.sha256(b"goldbot-arms-2026-10-25|S0|2023-03-01T08:10Z").digest()[:8]
    assert R.hash_u("S0", close) == int.from_bytes(digest, "big") / 2 ** 64
    assert R.hash_side("S0", close) in (-1, 1)
    sides = [R.hash_side("S0", close + pd.Timedelta(minutes=i)) for i in range(400)]
    assert 120 < sum(1 for s in sides if s == 1) < 280          # a fair coin
    assert R.hash_u("S0", close) != R.hash_u("S0R", close)       # each arm its own draw


def test_s0_schedule_window_and_side_come_from_the_spec(b1: pd.DataFrame) -> None:
    ctx = _ctx(b1, "1m")
    c = R.candidates(ARMS["S0"], ctx)
    close = ctx.close_ts[c["idx"].to_numpy()]
    assert len(c) > 100
    assert set(close.minute) <= {0, 10, 20, 30, 40, 50} and close.hour.min() >= 7
    assert (close.hour.to_numpy() * 60 + close.minute.to_numpy()).max() <= 19 * 60 + 50
    assert (close.dayofweek.to_numpy() < 5).all()
    assert (c["risk_usd"] == 3.0).all() and (c["target_r"] == 1.0).all() and (c["max_bars"] == 29).all()
    assert [R.hash_side("S0", t) for t in close[:20]] == list(c["side"][:20])
    s0r = R.candidates(ARMS["S0R"], ctx)
    assert set(ctx.close_ts[s0r["idx"].to_numpy()].minute) <= {5, 35}


def test_i0_and_w0_use_atr_stops_on_their_own_clocks(b1: pd.DataFrame) -> None:
    c15 = _ctx(b1, "15m")
    i0 = R.candidates(ARMS["I0"], c15)
    assert (i0["target_r"] == 2.0).all() and (i0["max_bars"] == 95).all()
    assert set(c15.close_ts[i0["idx"].to_numpy()].minute) == {0}
    c4 = _ctx(b1, "4h")
    w0 = R.candidates(ARMS["W0"], c4)
    assert set(c4.close_ts[w0["idx"].to_numpy()].hour) == {12}
    a4 = R.atr(c4.mid, 14).to_numpy()
    assert w0["risk_usd"].to_numpy() == pytest.approx(1.5 * a4[w0["idx"].to_numpy()])


def test_common_filters_drop_blackout_rollover_and_friday_evening() -> None:
    t = pd.DatetimeIndex(pd.to_datetime(["2023-03-01 10:00", "2023-03-01 21:00", "2023-03-03 19:00", "2023-03-03 18:50",
                                         "2023-03-01 15:10"], utc=True))
    ev = pd.Timestamp("2023-03-01 15:15", tz="UTC")
    lo = np.array([(ev - pd.Timedelta(minutes=15)).value]), np.array([(ev + pd.Timedelta(minutes=30)).value])
    ok = R.common_filter_ok(t, lo[0], lo[1])
    assert list(ok) == [True, False, False, True, False]


def test_blackout_windows_are_point_in_time_and_scheduled_only() -> None:
    ev = pd.Timestamp("2023-03-10 13:30", tz="UTC")
    cal = pd.DataFrame({"event_id": ["a", "a", "b"], "tier": [1, 1, 1], "event_utc": [ev, ev, ev],
                        "available_utc": [ev - pd.Timedelta(days=7), ev - pd.Timedelta(hours=1), ev - pd.Timedelta(days=7)],
                        "status": ["scheduled", "cancelled", "scheduled"]})
    lo, hi = R.blackout_windows(cal)
    # event a was cancelled an hour before it: its scheduled row ends before the window opens, so only b's blacks out
    assert len(lo) == 1 and hi[0] - lo[0] == pd.Timedelta(minutes=45).value


def test_s2_opening_range_break_is_one_per_session_with_structural_stop(b1: pd.DataFrame) -> None:
    ctx = _ctx(b1, "5m")
    arm = ARMS["S2A"]
    c = R.opening_range_break(arm, ctx)
    assert len(c) > 20
    assert c["target_r"].eq(2.0).all() and (c["max_bars"] <= 23).all()
    close = ctx.close_ts[c["idx"].to_numpy()]
    assert (close.hour.to_numpy() * 60 + close.minute.to_numpy() <= 20 * 60).all()
    # one trade per (day, session): London breaks close before 13:00 UTC, NY ones after
    keys = pd.Series(list(zip(close.normalize(), close.hour >= 13, strict=True)))
    assert not keys.duplicated().any()
    m = ctx.mid
    i = int(c["idx"].iloc[0])
    day = close[0].normalize()
    rng = m[(utc_ts(m) >= day + pd.Timedelta(hours=7)) & (utc_ts(m) < day + pd.Timedelta(hours=9))]
    assert rng["low"].min() < m["close"].iloc[i] or rng["high"].max() > m["close"].iloc[i]


def utc_ts(m: pd.DataFrame) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.to_datetime(m["ts_utc"], utc=True))


def test_s1_vwap_fade_candidates_fade_the_band_with_bounded_targets(b1: pd.DataFrame) -> None:
    ctx = _ctx(b1, "5m")
    a = R.session_vwap_fade(ARMS["S1A"], ctx)
    b = R.session_vwap_fade(ARMS["S1B"], ctx)
    assert len(b) > 5 and len(a) <= len(b)
    assert a["target_r"].between(1.0, 3.0).all() and b["target_r"].eq(1.0).all()
    assert a["risk_usd"].between(3.0, 6.0).all()
    close = ctx.close_ts[b["idx"].to_numpy()]
    assert (close.hour.to_numpy() * 60 + close.minute.to_numpy() <= 20 * 60).all()
    m = ctx.mid
    # fade: a long fires on a close below the previous 6 bars' mean region, i.e. after a down move
    ret = m["close"].to_numpy()[b["idx"].to_numpy()] - m["open"].to_numpy()[b["idx"].to_numpy()]
    assert (np.sign(ret) == -b["side"].to_numpy()).mean() > 0.5


def test_s5_ema_pullback_uses_the_fixed_dollar_stop(b1: pd.DataFrame) -> None:
    ctx = _ctx(b1, "1m")
    c5a = R.candidates(ARMS["S5A"], ctx)
    c5b = R.candidates(ARMS["S5B"], ctx)
    assert len(c5a) > 10 and (c5a["risk_usd"] == 3.0).all() and (c5b["risk_usd"] == 3.75).all()
    assert c5a["target_r"].eq(1.5).all() and c5a["max_bars"].eq(29).all()


def test_candidates_are_point_in_time_for_the_new_rules(b1: pd.DataFrame) -> None:
    """Prices after a cut change no candidate decided before it (S1, S2, S5, S0)."""
    cut = pd.Timestamp("2023-02-10", tz="UTC")
    late = b1.copy()
    after = utc_ts(late) >= cut
    for k in ("open", "high", "low", "close"):
        for side in ("bid", "ask"):
            late.loc[after, f"{side}_{k}"] = late.loc[after, f"{side}_{k}"] * 1.01 + 3
    for arm_id, tf in (("S0", "1m"), ("S1B", "5m"), ("S2A", "5m"), ("S5A", "1m")):
        a, b = _ctx(b1, tf), _ctx(late, tf)
        ca, cb = R.RULE_FUNCS[ARMS[arm_id].rule](ARMS[arm_id], a), R.RULE_FUNCS[ARMS[arm_id].rule](ARMS[arm_id], b)
        ta, tb = a.close_ts[ca["idx"].to_numpy()], b.close_ts[cb["idx"].to_numpy()]
        ba, bb = ca[np.asarray(ta < cut - pd.Timedelta(hours=1))], cb[np.asarray(tb < cut - pd.Timedelta(hours=1))]
        pd.testing.assert_frame_equal(ba[["idx", "side"]].reset_index(drop=True), bb[["idx", "side"]].reset_index(drop=True))


def test_blocked_arm_refuses_to_run(b1: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="blocked"):
        R.candidates(ARMS["S6"], _ctx(b1, "5m"))


def test_hold_bars_follow_the_spec_count() -> None:
    f = R._frame(np.array([5]), np.array([1]), np.array([3.0]), 1.0, 30)
    assert int(f["max_bars"].iloc[0]) == 29          # triple_barrier_1m exits at the close of bar idx + 1 + max_bars
