"""goldbot.research.arm_rules_clock_box: WFRI, KA15/XC15 and the H-80 box family (arms expansion wave 2, lane W2d).
Decisions on closed bars only, prefix invariance (live == history), local clocks across DST, fixed-price brackets, a
trail that only tightens, and XC15 pairing without the shifted entries."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.data.resample import resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.data.timeutil import utc_index
from goldbot.research import arm_rules as R
from goldbot.research import arm_rules_clock_box as K
from goldbot.research import arms as A

NO = np.zeros(0, dtype=np.int64)
NY = "America/New_York"
CUT = pd.Timestamp("2023-11-22", tz="UTC")          # a Wednesday after the US switch (5 Nov): both regimes before it
ARMS: tuple[tuple[str, A.Timeframe, dict[str, Any]], ...] = (
    ("friday_long", "30m", {}), ("clock_anchor", "15m", {}), ("clock_anchor_control", "15m", {}),
    ("box_next", "15m", {"exit": "runner"}), ("box_next", "15m", {"exit": "struct"}),
    ("box_next", "15m", {"exit": "fixed"}))


@pytest.fixture(scope="module")
def b1() -> pd.DataFrame:
    return ticks_to_1m(synthetic_ticks("2023-09-20", "2023-12-20", ticks_per_minute=1, seed=5))


def _arm(rule: str, tf: A.Timeframe, params: dict[str, Any] | None = None, arm_id: str = "X") -> A.Arm:
    return A.Arm(id=arm_id, rule=rule, timeframe=tf, params=params or {})


def _ctx(b1: pd.DataFrame, tf: str) -> R.RuleContext:
    return R.RuleContext(bars=resample_bars(b1, tf).reset_index(drop=True), b1=b1.reset_index(drop=True),
                         blackout_lo=NO, blackout_hi=NO)


def _run(b1: pd.DataFrame, rule: str, tf: A.Timeframe, params: dict[str, Any]) -> tuple[pd.DataFrame, R.RuleContext]:
    ctx = _ctx(b1, tf)
    return R.RULE_FUNCS[rule](_arm(rule, tf, params), ctx), ctx


@pytest.mark.parametrize(("rule", "tf", "params"), ARMS)
def test_appending_bars_never_changes_a_past_candidate(b1, rule, tf, params) -> None:
    full, fctx = _run(b1, rule, tf, params)
    head = b1[np.asarray(utc_index(b1["ts_utc"]) < CUT)]
    part, _ = _run(head, rule, tf, params)
    assert len(part) > 0, "the fixture must give candidates before the cut, else the test is vacuous"
    decided = np.asarray(fctx.close_ts[full["idx"].to_numpy()] <= CUT)
    pd.testing.assert_frame_equal(full[decided].reset_index(drop=True), part.reset_index(drop=True))


@pytest.mark.parametrize(("rule", "tf", "params"), ARMS)
def test_live_on_the_bars_up_to_the_decision_close_gives_the_history_candidate(b1, rule, tf, params) -> None:
    """Live sees the 1m bars through the close it decides on; its candidate on the last bar is history's."""
    full, fctx = _run(b1, rule, tf, params)
    assert len(full) > 0
    for _, row in full.iloc[:: max(1, len(full) // 4)].iterrows():
        close = fctx.close_ts[int(row["idx"])]
        live, lctx = _run(b1[np.asarray(utc_index(b1["ts_utc"]) < close)], rule, tf, params)
        last = live[live["idx"] == len(lctx.bars) - 1]
        assert len(last) == 1
        got, want = last.iloc[0].drop("idx"), row.drop("idx")
        pd.testing.assert_series_equal(got, want, check_names=False)


def test_wfri_is_long_at_thursday_19_new_york_in_both_dst_regimes_and_exits_at_friday_1630(b1) -> None:
    got, ctx = _run(b1, "friday_long", "30m", {})
    close = ctx.close_ts[got["idx"].to_numpy()]
    ny = close.tz_convert(NY)
    # the first on-the-hour close at or after 19:00 (a gap in the synthetic feed moves one Thursday to 20:00)
    assert (ny.dayofweek == 3).all() and (ny.hour >= 19).all() and (ny.minute == 0).all()
    assert len(set(ny.date)) == len(ny) and (ny.hour == 19).mean() > 0.8
    assert {23, 0} <= set(close[ny.hour == 19].hour)        # EDT and EST
    assert (got["side"] == 1).all() and (got["target_r"] == K.NO_TARGET_R).all()
    exit_at = close + (got["max_bars"].to_numpy() + 1) * pd.Timedelta(minutes=30)
    ex = exit_at.tz_convert(NY)
    assert (ex.dayofweek == 4).all() and (ex.hour == 16).all() and (ex.minute == 30).all()
    np.testing.assert_allclose(got["risk_usd"], K.atr4h_at(ctx, close))


def test_wfri_stop_is_the_atr_of_the_last_closed_4h_bar_only(b1) -> None:
    got, ctx = _run(b1, "friday_long", "30m", {})
    close = ctx.close_ts[got["idx"].to_numpy()]
    b4 = resample_bars(b1, "4h")
    assert (utc_index(b4["visible_at"])[np.searchsorted(utc_index(b4["visible_at"]), close, side="right") - 1]
            <= close).all()


def test_wfri_carries_the_engines_friday_weekend_cut_before_its_1630_exit(b1) -> None:
    """Prereg section 4, amendment 1: the cut is EngineConfig's (server_tz, weekend_cut), about 14:30 New York."""
    from goldbot.engine.runner import EngineConfig
    f = EngineConfig.model_fields
    assert f["server_tz"].default == K.WFRI_CUT_TZ
    assert f["weekend_cut"].default == "%02d:%02d" % K.WFRI_CUT
    got, ctx = _run(b1, "friday_long", "30m", {})
    close = ctx.close_ts[got["idx"].to_numpy()]
    cut = pd.DatetimeIndex(got["cut_at"])
    srv = cut.tz_convert(K.WFRI_CUT_TZ)
    assert (srv.dayofweek == 4).all() and (srv.hour == 21).all() and (srv.minute == 30).all()
    exit_at = close + (got["max_bars"].to_numpy() + 1) * pd.Timedelta(minutes=30)
    assert (cut < exit_at).all() and (cut > close).all()
    assert set(cut.tz_convert(NY).hour) <= {14, 15}            # 14:30 New York; 15:30 in the DST mismatch weeks
    # live on the bars up to the decision close sees the same cut (the prefix tests cover the whole frame)


def _fri(prices: list[float]) -> tuple[pd.DataFrame, pd.Timestamp, pd.Timestamp]:
    """A 1m path from 14:00 New York on a Friday (EST); cut at 14:30 New York, exit at 16:30."""
    path = _path(prices, start="2024-01-12 19:00")
    return path, pd.Timestamp("2024-01-12 19:30", tz="UTC"), pd.Timestamp("2024-01-12 21:30", tz="UTC")


def test_walk_friday_closes_a_loser_at_the_cut() -> None:
    path, cut, ex = _fri([99.5] * 200)
    w = K.walk_friday(path, entry=100.0, risk=3.0, cut_at=cut, exit_at=ex)
    assert w["kind"] == "weekend_loser" and w["exit"] == 99.5 and w["at"] == cut


def test_walk_friday_tightens_a_winner_at_the_cut_and_holds_it_to_1630() -> None:
    path, cut, ex = _fri([104.0] * 200)
    w = K.walk_friday(path, entry=100.0, risk=3.0, cut_at=cut, exit_at=ex)
    assert w["kind"] == "time" and w["exit"] == 104.0 and w["at"] == ex and w["stop"] == 102.0
    fade = _fri([104.0] * 40 + [101.0] * 160)[0]                # falls through the tightened stop after the cut
    w = K.walk_friday(fade, entry=100.0, risk=3.0, cut_at=cut, exit_at=ex)
    assert w["kind"] == "stop" and w["exit"] == 101.0 and w["stop"] == 102.0     # gapped through: the open


def test_walk_friday_keeps_the_entry_stop_before_the_cut() -> None:
    path, cut, ex = _fri([99.0] * 10 + [96.5] * 190)
    w = K.walk_friday(path, entry=100.0, risk=3.0, cut_at=cut, exit_at=ex)
    assert w["kind"] == "stop" and w["exit"] == 96.5 and w["at"] < cut and w["stop"] == 97.0


def test_clock_anchors_follow_local_clocks_and_t_only_on_us_daylight_time(b1) -> None:
    got, ctx = _run(b1, "clock_anchor", "15m", {})
    close = ctx.close_ts[got["idx"].to_numpy()]
    for s, (zone, hh, mm, _) in K.ANCHORS.items():
        loc = close[got["stratum"].to_numpy() == s].tz_convert(zone)
        assert len(loc) > 0 and (loc.hour == hh).all() and (loc.minute == mm).all() and (loc.dayofweek < 5).all()
    t = close[got["stratum"].to_numpy() == "T"]
    assert (t < pd.Timestamp("2023-11-05", tz="UTC")).all()          # US standard time from 5 Nov: no T
    c = close[got["stratum"].to_numpy() == "C"]
    assert set(c.hour) == {17, 18}                                   # 13:30 New York in EDT and EST


def test_clock_anchor_side_stop_and_exit_follow_the_pre_window(b1) -> None:
    got, ctx = _run(b1, "clock_anchor", "15m", {})
    m = ctx.mid
    c = m["close"].to_numpy()
    from goldbot.features.technical import atr
    a = atr(m, 14).to_numpy()
    for _, r in got.iterrows():
        i = int(r["idx"])
        pre = c[i] - c[i - 2]                                         # 15m bars: the close 30 min before
        assert abs(pre) >= 0.1 * a[i]
        assert r["side"] == K.ANCHORS[r["stratum"]][3] * np.sign(pre)
        assert r["risk_usd"] == pytest.approx(a[i]) and r["max_bars"] == 1   # exit at the close of bar i + 2


def test_control_takes_ka15s_decisions_with_a_hashed_side(b1) -> None:
    ka, ctx = _run(b1, "clock_anchor", "15m", {})
    xc = R.RULE_FUNCS["clock_anchor_control"](_arm("clock_anchor_control", "15m", arm_id="XC15"), ctx)
    assert xc["idx"].tolist() == ka["idx"].tolist()
    pd.testing.assert_frame_equal(xc.drop(columns="side"), ka.drop(columns="side"))
    assert xc["side"].tolist() == [R.hash_side("XC15", ctx.close_ts[int(i)]) for i in xc["idx"]]


def test_a_control_entry_shifted_because_ka15_is_open_is_excluded_from_pairing() -> None:
    t0 = pd.Timestamp("2024-01-10 02:15", tz="UTC")
    d = pd.DatetimeIndex([t0, t0 + pd.Timedelta(hours=11, minutes=15)])
    sched = K.shift_control(d, [(t0, t0 + pd.Timedelta(minutes=30))])
    assert sched["entry"].tolist() == [t0 + pd.Timedelta(minutes=30), d[1]]
    assert sched["shifted"].tolist() == [True, False]
    arm = pd.DataFrame({"decision": d, "net_r": [0.5, -0.2]})
    pairs = K.pair_with_control(arm, sched)
    assert pairs["decision"].tolist() == [d[1]]


def test_the_struct_target_is_the_first_box_at_or_beyond_2r_and_fixed_is_2r(b1) -> None:
    fixed, _ = _run(b1, "box_next", "15m", {"exit": "fixed"})
    struct, _ = _run(b1, "box_next", "15m", {"exit": "struct"})
    runner, _ = _run(b1, "box_next", "15m", {"exit": "runner"})
    assert fixed["idx"].tolist() == struct["idx"].tolist() == runner["idx"].tolist()
    assert (fixed["target_r"] == 2.0).all() and (struct["target_r"] >= 2.0 - 1e-9).all()
    assert (runner["target_r"] == K.NO_TARGET_R).all() and (fixed["max_bars"] == 95).all()


def _path(prices: list[float], start: str = "2024-01-10 10:00") -> pd.DataFrame:
    p = np.asarray(prices, dtype=float)
    ts = pd.date_range(start, periods=len(p), freq="1min", tz="UTC")
    return pd.DataFrame({"ts_utc": ts, "open": p, "high": p + 0.1, "low": p - 0.1, "close": p})


def test_stop_and_target_are_fixed_prices_set_at_entry() -> None:
    closes = pd.date_range("2024-01-10 10:15", periods=8, freq="15min", tz="UTC")
    up = _path([100 + 0.2 * k for k in range(120)])
    hit = K.walk_runner(up, closes, entry=100.0, side=1, risk=2.0, atr_sig=1.0, target_r=2.0)
    assert hit["kind"] == "target" and hit["exit"] == 104.0 and set(hit["stops"]) == {98.0}
    down = _path([100 - 0.2 * k for k in range(120)])
    st = K.walk_runner(down, closes, entry=100.0, side=1, risk=2.0, atr_sig=1.0, target_r=2.0)
    assert st["kind"] == "stop" and st["exit"] == 98.0


def test_the_runner_trail_arms_at_one_r_and_only_ever_tightens() -> None:
    rng = np.random.default_rng(3)
    for side in (1, -1):
        stop, entry, risk = 100.0 - side * 3.0, 100.0, 3.0
        assert K.runner_stop(stop, entry, side, risk, 2.0, entry + side * 2.9) == stop      # not armed below +1 R
        best = entry
        for _ in range(300):
            best = max(best, best + side * rng.normal(0, 1)) if side > 0 else min(best, best + side * rng.normal(0, 1))
            new = K.runner_stop(stop, entry, side, risk, 2.0, best)
            assert side * (new - stop) >= 0
            stop = new
        assert side * (stop - entry) >= K.BE_USD - 1e-9                                      # break-even reached


def test_the_runner_stop_moves_only_at_a_decision_close() -> None:
    closes = pd.date_range("2024-01-10 10:15", periods=4, freq="15min", tz="UTC")
    prices = [100 + 0.3 * k for k in range(30)] + [109 - 0.4 * k for k in range(40)]
    w = K.walk_runner(_path(prices), closes, entry=100.0, side=1, risk=3.0, atr_sig=2.0)
    assert len(w["stops"]) <= len(closes) and all(b >= a for a, b in zip(w["stops"], w["stops"][1:], strict=False))
    assert w["kind"] in ("stop", "time")


@pytest.mark.parametrize(("rule", "tf", "params", "match"), [
    ("friday_long", "1h", {}, "30m"), ("clock_anchor", "1h", {}, "15m"),
    ("clock_anchor", "15m", {"strata": ["X"]}, "strata"), ("box_next", "5m", {"exit": "fixed"}, "box_next runs"),
    ("box_next", "15m", {"exit": "partial"}, "params.exit")])
def test_a_rule_off_its_specification_is_refused_at_load(rule, tf, params, match) -> None:
    with pytest.raises(ValueError, match=match):
        _arm(rule, tf, params)


def test_the_new_rules_are_registered() -> None:
    for name in A.CLOCK_BOX_RULES:
        assert name in A.RULES and name in R.RULE_FUNCS


# ---------------------------------------------------------------- engine lane E3: the runner live, the twins
def test_every_box_row_carries_the_struct_target_the_bxt_twin_reads(b1) -> None:
    struct, _ = _run(b1, "box_next", "15m", {"exit": "struct"})
    for mode in ("runner", "fixed"):
        got, _ = _run(b1, "box_next", "15m", {"exit": mode})
        np.testing.assert_allclose(got["struct_r"], struct["target_r"])


def test_the_runner_stop_live_after_each_close_is_the_history_walks_stop_on_the_same_bars(b1) -> None:
    """Live == history: on the 1m bars through a decision close, `runner_stop_at` is the stop `walk_runner` holds
    after that close on the whole path (the trail is a function of the bars, so a restart recomputes it)."""
    runner, ctx = _run(b1, "box_next", "15m", {"exit": "runner"})
    close = ctx.close_ts
    checked = 0
    for _, row in runner.iloc[:: max(1, len(runner) // 6)].iterrows():
        i, side, risk = int(row["idx"]), int(row["side"]), float(row["risk_usd"])
        hold = int(row["max_bars"]) + 1
        closes = K.decision_closes(b1, "15m", close[i], hold)
        np.testing.assert_array_equal(closes, close[i + 1:i + 1 + hold])
        path = K.mid_path(b1, close[i])
        entry = float(path["open"].iloc[0])
        hist = K.walk_runner(path, closes, entry, side, risk, risk / K.BOX_STOP_ATR)
        for k, at in enumerate(closes[:len(hist["stops"]) - 1]):
            seen = b1[np.asarray(utc_index(b1["visible_at"]) <= at)]
            live = K.runner_stop_at(K.mid_path(seen, close[i]), K.decision_closes(seen, "15m", close[i], hold), entry,
                                    side, risk, risk / K.BOX_STOP_ATR)
            assert live == hist["stops"][k + 1]
            checked += 1
    assert checked > 10


def test_twin_ids_follow_the_bxr_row_naming() -> None:
    assert K.twin_ids("BXR15") == {"struct": "BXT15", "fixed": "BXF15"}
    assert K.twin_ids("BXR4H") == {"struct": "BXT4H", "fixed": "BXF4H"}
    assert K.twin_ids("RUN1") == {"struct": "RUN1-struct", "fixed": "RUN1-fixed"}


def test_a_twin_stays_open_until_its_own_barrier_and_then_equals_the_full_walk() -> None:
    closes = pd.date_range("2024-01-10 10:15", periods=8, freq="15min", tz="UTC")
    up = _path([100 + 0.05 * k for k in range(200)])
    full = K.walk_runner(up, closes, entry=100.0, side=1, risk=2.0, atr_sig=1.0, target_r=2.0)
    assert full["kind"] == "target"
    assert K.twin_outcome(up.iloc[:30], closes[:1], 8, entry=100.0, side=1, risk=2.0, target_r=2.0) is None
    got = K.twin_outcome(up, closes, 8, entry=100.0, side=1, risk=2.0, target_r=2.0)
    assert got is not None and (got["kind"], got["exit"], got["at"]) == (full["kind"], full["exit"], full["at"])
    flat = _path([100.0] * 200)
    timed = K.twin_outcome(flat, closes, 8, entry=100.0, side=1, risk=2.0, target_r=2.0)
    assert timed is not None and timed["kind"] == "time" and timed["at"] == closes[-1]
