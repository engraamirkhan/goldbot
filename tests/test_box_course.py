"""Box and price-action course families (hypotheses.md A11; docs/research/box-mfi-families-2026-10-18.md sections 3-5,
written before any count): H-37 box break, H-38 RIP, H-39 retest, H-44 fakey, H-45 trend-level-signal and the H-40
union. Each family's only setting is its pre-registered rung; candidates equal the written definitions on crafted bars;
nothing after a signal's close is read (appending bars never changes a past candidate); R is the structural stop and
the target 2R (the K-T fallback); each signal keeps its own timeframe's time exit; the families stay out of the loop
grid, the founders and the pools."""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.data.resample import mid, resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.execution.costs import settings_extra_cost_usd
from goldbot.features import box_course as bc
from goldbot.features.mtf import TF_LABEL, context_tfs
from goldbot.ops.jobs import research_families
from goldbot.research.generator import GRID_V1, _eligible
from goldbot.research.pipeline import pooled_members
from goldbot.research.preregistration import resolve_variant
from goldbot.specialists import SPECIALISTS
from goldbot.specialists import box_course as bx

FAMILIES = {"box_break": ("L0", "L1", "L2", "L3", "L4", "L5", "L6"),
            "box_rip": ("L0", "L1", "L2", "L3", "L4", "L5", "L6"),
            "box_retest": ("L0", "L1", "L2", "L3"),
            "fakey": ("L0", "L1", "L2", "L3", "L4"),
            "trend_level_signal": ("L0", "L1", "L2", "L3")}
START = pd.Timestamp("2024-01-02", tz="UTC")       # a Tuesday: no weekend inside the crafted bars
STEP = {"15m": pd.Timedelta(minutes=15), "1h": pd.Timedelta(hours=1), "4h": pd.Timedelta(hours=4),
        "1d": pd.Timedelta(days=1)}


# ------------------------------------------------------------------------------------------- registration
def test_every_family_offers_exactly_its_preregistered_rungs_and_nothing_else_to_tune():
    for fam, rungs in FAMILIES.items():
        cls = SPECIALISTS[fam]
        assert tuple(cls.presets) == rungs and all(cls.presets[r] == {"rung": r} for r in rungs)
        assert cls.default_config == {"rung": "L0"}
        assert cls.screening and not cls.requires_tv
        for r in rungs:
            spec = cls(**resolve_variant(fam, r))
            assert spec.config == {"rung": r}
        with pytest.raises(ValueError, match="not pre-registered"):
            cls(rung="L9")
        with pytest.raises(ValueError, match="unknown"):
            resolve_variant(fam, {"rung": "L0", "depth": 0.3})       # no setting beyond the rung


def test_decision_bars_entry_and_cost_gate_follow_the_preregistration():
    assert {f: SPECIALISTS[f].timeframe for f in FAMILIES} == bx.DECISION_TF
    assert [f for f in FAMILIES if SPECIALISTS[f].entry_at_next_open] == ["trend_level_signal"]   # H-45 "E"
    assert [f for f in FAMILIES if SPECIALISTS[f].cost_gate_r is not None] == ["box_break"]
    assert SPECIALISTS["box_break"].cost_gate_r == 0.85                      # research director ruling, item 2


def test_labels_use_the_structural_stop_as_r_a_2r_target_and_the_decision_timeframes_exit():
    for fam in FAMILIES:
        ls = SPECIALISTS[fam]().label_spec
        assert (ls.stop_atr, ls.target_atr, ls.has_target) == (1.0, 2.0, True)
        assert ls.max_bars == {"15m": 96, "1h": 48}[SPECIALISTS[fam].timeframe]
        assert SPECIALISTS[fam]().exit_spec is None                         # K-X fallback: 100% at the target


def test_the_families_stay_out_of_the_loop_grid_the_default_founders_and_the_pools():
    settings = load_settings()
    new = {*FAMILIES, "box_course"}
    assert not new & set(_eligible(settings))
    assert not new & set(research_families())
    assert not any(f in new for f, _ in GRID_V1)
    for tf in ("15m", "1h"):
        assert not new & set(pooled_members(tf))


def test_the_placeable_rule_is_the_engines_gate_as_the_settings_hold_it():
    s = load_settings()
    assert bc.MIN_TARGET_OVER_COST == s.risk.min_target_over_cost
    assert bc.COST_PRIOR_USD == pytest.approx(settings_extra_cost_usd(s))
    assert bc.MIN_STOP_USD == pytest.approx((s.research.lot_fit_spread_points + s.research.lot_fit_stops_level_points)
                                            * 0.01)
    assert bc.placeable(100.0, 99.8, 101.5, 0.2) and not bc.placeable(100.0, 99.85, 101.5, 0.2)   # stop < $0.20
    assert not bc.placeable(100.0, 99.0, 101.4, 0.2)                          # target < 2.5 x (0.2 + 0.37)


# ------------------------------------------------------------------------------------------- crafted bars
def _mid_frame(tf: str, rows: Sequence[tuple[float, float, float, float]], tick: float = 100.0) -> pd.DataFrame:
    ts = pd.DatetimeIndex([START + k * STEP[tf] for k in range(len(rows))])
    o, h, lo, c = (np.array([r[i] for r in rows], dtype=float) for i in range(4))
    return pd.DataFrame({"ts_utc": ts, "visible_at": ts + STEP[tf], "open": o, "high": h, "low": lo, "close": c,
                         "spread": 0.2, "tick_count": tick})


def _agg(m: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Store-schema bars of `tf` from finer mid bars (bid = ask = mid)."""
    key = pd.DatetimeIndex(m["ts_utc"]).floor(STEP[tf])
    g = m.groupby(key)
    out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                        "close": g["close"].last(), "spread_mean": g["spread"].mean(), "tick_count": g["tick_count"].sum()})
    out = out.reset_index(names="ts_utc")
    out["visible_at"] = out["ts_utc"] + STEP[tf]
    for k in ("open", "high", "low", "close"):
        out[f"bid_{k}"] = out[f"ask_{k}"] = out[k]
    return out


Boxes = tuple[Sequence[float], Sequence[float]]


def _course(dec_tf: str, rows: Sequence[tuple[float, float, float, float]], boxes: dict[str, Boxes]) -> bx.Course:
    """A Course on crafted decision bars whose boxes are fixed (`boxes[tf]` = (tops, bottoms) at every row; a timeframe
    left out has none), so a test reads the rule alone, not the channel replica."""
    m = _mid_frame(dec_tf, rows)
    ctx = {TF_LABEL[tf]: _agg(m, tf) for tf in context_tfs(dec_tf)}
    c = bx.Course(m, dec_tf, ctx, box_tfs=())
    for tf, b in c.b.items():
        tops, bots = boxes.get(tf, ((), ()))
        b.boxes = [(np.asarray(tops, dtype=float), np.asarray(bots, dtype=float))] * b.n
    return c


def _bar(close: float, *, o: float | None = None, h: float | None = None, lo: float | None = None) -> tuple:
    o = close if o is None else o
    return (o, max(o, close) + 0.05 if h is None else h, min(o, close) - 0.05 if lo is None else lo, close)


def test_h39_a_broken_box_top_retested_and_rejected_is_a_long_with_its_wick_stop_and_the_next_box():
    rows = [_bar(100.5)] * 60 + [_bar(102.0, o=100.6)] + [_bar(102.5)] * 2 \
        + [_bar(101.6, o=101.2, lo=100.9)] + [_bar(101.6)] * 16
    c = _course("1h", rows, {"1h": ((101.0, 112.0), (100.0, 110.0))})
    ev = bx.scan_retest(c, "L3")
    assert [(e.i, e.side) for e in ev] == [(63, 1)]
    b = c.b["1h"]
    assert ev[0].stop == pytest.approx(100.9 - 0.1 * b.atr[63])
    assert ev[0].target == 110.0 and ev[0].entry == 101.6
    # a close back below the box bottom before the retest cancels the break
    bad = rows[:62] + [_bar(99.5)] + rows[63:]
    assert bx.scan_retest(_course("1h", bad, {"1h": ((101.0, 112.0), (100.0, 110.0))}), "L3") == []


def test_h39_needs_room_to_the_next_box_of_at_least_two_r():
    rows = [_bar(100.5)] * 60 + [_bar(102.0, o=100.6)] + [_bar(102.5)] * 2 \
        + [_bar(101.6, o=101.2, lo=100.9)] + [_bar(101.6)] * 16
    near = _course("1h", rows, {"1h": ((101.0, 103.0), (100.0, 102.6))})       # next box 1.0 above, R about 0.7
    assert bx.scan_retest(near, "L3") == []


def test_h38_a_touch_from_below_then_a_bearish_close_back_outside_is_a_short_above_the_wicks():
    rows = [_bar(99.5)] * 40 + [_bar(99.9, o=99.6, h=100.3)] + [_bar(99.4, o=99.9)] + [_bar(99.4)] * 10
    c = _course("1h", rows, {"1h": ((101.0, 95.0), (100.0, 94.0))})
    ev = bx.scan_rip(c, "L6")
    assert [(e.i, e.side) for e in ev] == [(41, -1)]
    b = c.b["1h"]
    assert ev[0].stop == pytest.approx(max(100.3, b.h[41]) + 0.1 * b.atr[41])
    assert ev[0].target == 95.0
    # a close through the box (above its top) is a break, not a RIP
    through = rows[:40] + [_bar(101.2, o=99.6)] + rows[41:]
    assert bx.scan_rip(_course("1h", through, {"1h": ((101.0, 95.0), (100.0, 94.0))}), "L6") == []


def test_h44_an_inside_bar_false_broken_below_its_mother_and_closed_back_inside_is_a_long():
    pre = [_bar(90 + 0.1 * k) for k in range(30)]                       # rising highs: no inside bars before
    rows = pre + [(100.0, 102.0, 98.0, 100.0), (100.0, 101.0, 99.0, 100.0), (99.0, 99.5, 97.5, 99.0)] \
        + [_bar(99.0)] * 10
    c = _course("1h", rows, {"1h": ((110.0,), (108.0,))})
    ev = [e for e in bx.scan_fakey(c, "L4") if e.tf == "1h"]
    assert [(e.i, e.side) for e in ev] == [(32, 1)]
    assert ev[0].stop == pytest.approx(97.5 - 0.1 * c.b["1h"].atr[32]) and ev[0].target == 108.0
    assert (ev[0].lo, ev[0].hi) == (98.0, 102.0)                       # the mother range (major-level check)


def test_h37_a_box_break_then_the_first_15m_pullback_to_the_box_is_a_long_with_the_box_stop():
    pre = [_bar(100.5)] * (4 * 50)
    brk = [_bar(100.8), _bar(101.0), _bar(101.2), _bar(101.5, o=101.2)]  # the 1h bar closes 101.5 (> 101 + 0.2)
    pull = [_bar(101.3, o=101.4, lo=100.2)]
    rows = pre + brk + pull + [_bar(101.3)] * 40
    c = _course("15m", rows, {"1h": ((101.0, 111.0), (100.0, 110.0))})
    ev = bx.scan_break(c, "L6")
    assert [(e.i, e.side, e.tf) for e in ev] == [(204, 1, "15m")]
    q = c.b["15m"]
    assert ev[0].stop == pytest.approx(100.0 - 0.1 * q.atr[204]) and ev[0].target == 110.0
    assert ev[0].info["anchor"] == 1.0                                   # the pullback reached the broken box


def _signals(bars: Sequence[tuple[float, float, float, float]]) -> set[str]:
    """The S1 candles that fire on the last of `bars` (o, h, l, c)."""
    o, h, lo, c = (np.array([b[i] for b in bars]) for i in range(4))
    return {k for k, v in bc.course_signals(o, h, lo, c).items() if v[-1]}


def test_h45_signal_candles_are_the_course_definitions():
    flat = (10.0, 10.1, 9.9, 10.0)
    assert _signals([flat, flat, (9.8, 10.0, 8.0, 9.9)]) == {"hammer"}            # lower wick 0.9, body 0.05
    assert _signals([flat, flat, (9.9, 11.8, 9.8, 10.0)]) == {"shooting_star", "inv_hammer"}  # course: both shapes
    assert "hammer" not in _signals([flat, flat, (9.0, 10.1, 8.0, 9.9)])          # body 0.43 of the range
    assert "bull_engulf" in _signals([flat, (10.0, 10.1, 9.4, 9.5), (9.4, 10.2, 9.3, 10.1)])
    assert "piercing" in _signals([flat, (10.0, 10.1, 8.9, 9.0), (8.9, 9.7, 8.8, 9.6)])
    assert "piercing" not in _signals([flat, (10.0, 10.1, 8.9, 9.0), (8.9, 9.5, 8.8, 9.4)])   # below the midpoint
    assert "morning_star" in _signals([(10.0, 10.1, 8.9, 9.0), (8.95, 9.2, 8.8, 9.0), (9.0, 9.8, 8.95, 9.7)])
    assert "dark_cloud" in _signals([flat, (9.0, 10.1, 8.9, 10.0), (10.1, 10.2, 9.3, 9.4)])


# ------------------------------------------------------------------------------------------- placement
def _ev(close_ns: int, side: int, tf: str, member: str) -> bx.Event:
    return bx.Event(close_ns=close_ns, tf=tf, i=0, side=side, entry=100.0, stop=99.0, target=103.0, member=member,
                    lo=99.0, hi=100.0)


def test_signals_land_on_the_decision_bar_closing_with_them_keep_their_own_exit_and_conflicts_place_nothing():
    m = _mid_frame("1h", [_bar(100.0)] * 12)
    close = bc.TfBars(m, "1h").close_ns
    evs = [_ev(int(close[3]), 1, "4h", "fakey:L4:4h"), _ev(int(close[3]), 1, "1h", "fakey:L4:1h"),
           _ev(int(close[5]), 1, "1h", "a"), _ev(int(close[5]), -1, "1h", "b"),
           _ev(int(close[7]) + 1, 1, "1h", "off-grid")]
    frame, kept = bx.place(evs, m, "1h")
    assert frame["idx"].tolist() == [3]                                   # bar 5 conflicts, the last is off the grid
    assert frame["max_bars"].tolist() == [96] and kept[0].member == "fakey:L4:4h"   # 24 x 4h = 96 x 1h; first kept
    assert frame["risk_usd"].tolist() == [1.0]


# ------------------------------------------------------------------------------------------- point in time
@pytest.fixture(scope="module")
def b1() -> pd.DataFrame:
    return ticks_to_1m(synthetic_ticks("2023-06-01", "2023-11-01", ticks_per_minute=1, seed=5))


def _run(spec, b1: pd.DataFrame) -> pd.DataFrame:
    tf = spec.timeframe
    dec = resample_bars(b1, tf).reset_index(drop=True)
    ctx = {TF_LABEL[x]: resample_bars(b1, x) for x in context_tfs(tf)}
    m = mid(dec)
    return spec.candidates_in_context(m, pd.DataFrame(index=m.index), ctx)


@pytest.mark.parametrize("fam,rung", [("box_retest", "L3"), ("box_rip", "L6"), ("fakey", "L4"),
                                      ("trend_level_signal", "L3"), ("box_break", "L6"), ("box_break", "L1")])
def test_appending_bars_never_changes_a_past_candidate_its_stop_or_its_exit(fam, rung, b1):
    full = _run(SPECIALISTS[fam](rung=rung), b1)
    cut = pd.Timestamp("2023-09-15 13:00", tz="UTC")
    part = _run(SPECIALISTS[fam](rung=rung), b1[pd.to_datetime(b1["ts_utc"], utc=True) < cut])
    n = len(resample_bars(b1[pd.to_datetime(b1["ts_utc"], utc=True) < cut], SPECIALISTS[fam].timeframe))
    assert len(full) > 0
    pd.testing.assert_frame_equal(part.reset_index(drop=True), full[full["idx"] < n].reset_index(drop=True))


def test_every_family_and_its_flags_are_known_at_the_signal_close(b1):
    """The sharpest point-in-time check: for the last candidate of each member of the widest union (all five families
    on 15m, with the H-40 flags), the bars are cut at that candidate's own signal close and everything is recomputed;
    the candidate, its stop, its exit and every flag must be the same. A rule that read even one bar past the signal
    (on any timeframe) would change here."""
    spec = SPECIALISTS["box_course"](**resolve_variant("box_course", "a11_box_break"))
    full = _run(spec, b1)
    dec = resample_bars(b1, "15m").reset_index(drop=True)
    closes = pd.DatetimeIndex(pd.to_datetime(dec["visible_at"], utc=True))
    fams = full["member"].str.split(":").str[0]
    picks = [int(full.loc[fams == f, "idx"].iloc[-1]) for f in fams.unique()]
    assert len(picks) >= 4
    for i in picks:
        cut = closes[i]
        part = _run(SPECIALISTS["box_course"](**resolve_variant("box_course", "a11_box_break")),
                    b1[pd.to_datetime(b1["ts_utc"], utc=True) < cut])
        row = part[part["idx"] == i].reset_index(drop=True)
        pd.testing.assert_frame_equal(row, full[full["idx"] == i].reset_index(drop=True))


def test_each_rung_relaxes_the_one_before_it_on_the_ladders_that_only_drop_or_pool_conditions(b1):
    """Section 4: rungs relax only by pooling, a looser threshold or a dropped condition, so on these ladders every
    signal of a rung is a signal of the next (H-37 and H-45 change thresholds or side order, so are counted only)."""
    for fam in ("box_retest", "box_rip", "fakey"):
        prev: set[tuple[int, int]] | None = None
        for rung in FAMILIES[fam]:
            spec = SPECIALISTS[fam](rung=rung)
            assert isinstance(spec, bx._CourseSpecialist)
            tf = spec.timeframe
            dec = resample_bars(b1, tf).reset_index(drop=True)
            ctx = {TF_LABEL[x]: resample_bars(b1, x) for x in context_tfs(tf)}
            c = bx.Course(mid(dec), tf, ctx, sorted(spec.box_tfs()))
            now = {(e.close_ns, e.side) for e in spec.events(c)}
            if prev is not None:
                assert prev <= now, f"{fam} {rung} dropped signals of the rung before"
            prev = now


# ------------------------------------------------------------------------------------------- H-40 union
def test_the_union_screens_exactly_the_21_preregistered_groups():
    spec = SPECIALISTS["box_course"](**resolve_variant("box_course", "a11"))
    groups = set(spec.candidate_feature_groups.values())
    assert len(groups) == 21 and "mfi_signals" in groups
    assert set(spec.candidate_features) == set(spec.candidate_feature_groups)
    assert spec.timeframe == "1h" and spec.config["members"] == list(bx.A11_MEMBERS)
    spec15 = SPECIALISTS["box_course"](**resolve_variant("box_course", "a11_box_break"))
    assert spec15.timeframe == "15m" and len(set(spec15.candidate_feature_groups.values())) == 21


def test_the_union_refuses_an_unregistered_member_two_rungs_of_one_family_and_box_break_off_15m():
    cls = SPECIALISTS["box_course"]
    with pytest.raises(ValueError, match="pre-registered"):
        cls(members=["box_retest:L9"])
    with pytest.raises(ValueError, match="one rung per family"):
        cls(members=["box_retest:L0", "box_retest:L3"])
    with pytest.raises(ValueError, match="15m"):
        cls(members=["box_break:L6"])


def test_the_union_candidates_carry_their_flags_and_family_only_flags_are_blank_elsewhere(b1):
    spec = SPECIALISTS["box_course"](**resolve_variant("box_course", "a11"))
    frame = _run(spec, b1)
    assert len(frame) > 0 and set(spec.candidate_features) <= set(frame.columns)
    retest = frame["member"].str.startswith("box_retest")
    tls = frame["member"].str.startswith("trend_level_signal")
    assert frame.loc[~frame["member"].str.startswith(("box_rip", "box_retest")), "engulf"].isna().all()
    assert frame.loc[retest, "rr_3"].notna().all() and frame.loc[~tls, "tlv_count"].isna().all()
    assert frame.loc[:, "major_level"].isin([0.0, 1.0]).all()
