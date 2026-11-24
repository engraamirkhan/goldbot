"""The red-box week (owner directive 2026-11-15; docs/research/prereg-amendment-2026-11-15-redbox-week.md): every break
and close of the arm's own-timeframe red boxes (the replica's sr_channels) to the next box on 15m/1h/4h, a learning
family on icm-probe with a replay-only control per arm. The rule takes no filter, records the review features, never
decides on a later bar, ends at 2026-11-22 22:00 UTC; the arm sends through the RiskGate and the control never sends."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
import yaml

from goldbot.data.resample import resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.data.timeutil import utc_index
from goldbot.engine import exploration, redbox_week
from goldbot.ops import accounts as acc_mod
from goldbot.research import arm_rules as R
from goldbot.research import arm_rules_redbox as X
from goldbot.research import arms as A
from tests.test_exploration_engine import CLOSE, _at, _engine, _sent

NO = np.zeros(0, dtype=np.int64)
ROOT = Path(__file__).resolve().parents[1]
PREREG = "docs/research/prereg-amendment-2026-11-15-redbox-week.md"
END = "2026-11-22T22:00:00Z"
HOLD = {"15m": 96, "1h": 48, "4h": 24}
TFS: tuple[A.Timeframe, ...] = ("15m", "1h", "4h")
CUT = pd.Timestamp("2023-11-22", tz="UTC")
# research-director review 2026-11-20: RB15 no edge, retired; RBC15 exists only to pair with it
RETIRED_15M = frozenset({"RB15", "RBC15"})
# owner priority 2026-11-22: the 1h/4h arms and controls retired at the week's end (no history pass)
RETIRED_1H4H = frozenset({"RB1H", "RBC1H", "RB4H", "RBC4H"})


def _params(tf: str, **kw: Any) -> dict[str, Any]:
    return {"per_trade_bracket": "redbox", "hold_bars": HOLD[tf], "min_stop_usd": 1.0, "max_stop_usd": 40.0,
            "end_utc": END, **kw}


def _arm(tf: A.Timeframe = "15m", rule: str = "redbox_break", arm_id: str = "RBX", **kw: Any) -> A.Arm:
    return A.Arm(id=arm_id, rule=rule, timeframe=tf, params=_params(tf, **kw))


@pytest.fixture(scope="module")
def b1() -> pd.DataFrame:
    return ticks_to_1m(synthetic_ticks("2023-08-01", "2023-12-20", ticks_per_minute=1, seed=7))


def _ctx(b1: pd.DataFrame, tf: str) -> R.RuleContext:
    return R.RuleContext(bars=resample_bars(b1, tf).reset_index(drop=True), b1=b1.reset_index(drop=True),
                         blackout_lo=NO, blackout_hi=NO)


def _ladders(n: int, tops: list[float], bots: list[float]) -> list[tuple[np.ndarray, np.ndarray]]:
    return [(np.array(tops, float), np.array(bots, float))] * n


# ------------------------------------------------------------------------------------------------ the break
def test_a_close_above_a_box_top_is_a_long_and_below_a_box_bottom_a_short() -> None:
    tops, bots = np.array([100.0, 110.0]), np.array([98.0, 108.0])
    assert X.crossing(99.0, 100.5, tops, bots) == (1, 0)
    assert X.crossing(108.5, 107.0, tops, bots) == (-1, 1)
    assert X.crossing(100.0, 100.0, tops, bots) == (0, -1)            # a close AT the top is not beyond it
    assert X.crossing(101.0, 105.0, tops, bots) == (0, -1)            # already above: no crossing


def test_several_boxes_crossed_break_the_farthest_one() -> None:
    tops, bots = np.array([100.0, 104.0, 120.0]), np.array([98.0, 103.0, 118.0])
    assert X.crossing(97.0, 106.0, tops, bots) == (1, 1)
    assert X.crossing(106.0, 97.0, tops, bots) == (-1, 0)


def test_the_target_is_the_next_box_beyond_the_close_and_the_stop_sits_beyond_the_broken_box() -> None:
    c = np.array([99.0, 101.0])
    o = np.array([99.0, 99.5])
    a = np.array([np.nan, 2.0])
    got = X.events(o, c, a, _ladders(2, [100.0, 112.0], [97.0, 109.0]), 96)
    [row] = got.to_dict("records")
    assert row["side"] == 1 and row["skip"] is None
    assert row["risk_usd"] == pytest.approx(101.0 - (97.0 - 0.1 * 2.0))   # far side minus 0.1 ATR (roster buffer)
    assert row["next_box"] == 109.0                                       # the next box's near edge
    assert row["target_r"] * row["risk_usd"] == pytest.approx(109.0 - 101.0)
    assert row["max_bars"] == 95 and row["box_height"] == 3.0
    assert row["break_depth_pct"] == pytest.approx(100.0 / 3.0)


def test_a_short_targets_the_nearest_top_below_and_a_box_holding_the_close_gives_its_far_edge() -> None:
    c = np.array([101.0, 99.0])
    a = np.array([1.0, 1.0])
    got = X.events(c, c, a, _ladders(2, [102.0, 99.5, 90.0], [100.0, 98.0, 88.0]), 48).to_dict("records")
    [row] = got
    assert row["side"] == -1 and row["next_box"] == 98.0 and row["close_in_box"] is True


def test_a_break_with_no_box_beyond_is_recorded_skipped_never_sent() -> None:
    c = np.array([99.0, 101.0])
    got = X.events(c, c, np.array([1.0, 1.0]), _ladders(2, [100.0], [97.0]), 96).to_dict("records")
    [row] = got
    assert row["skip"] == X.NO_NEXT_BOX and row["target_r"] == X.NO_TARGET_R


def test_no_filter_a_shallow_break_on_a_bearish_body_still_trades() -> None:
    """The roster would refuse it (20% depth, bullish body, money flow); the week's rule takes every break."""
    c = np.array([99.9, 100.01])
    o = np.array([99.0, 100.5])                                           # a bearish candle closing just above
    got = X.events(o, c, np.array([1.0, 1.0]), _ladders(2, [100.0, 105.0], [99.0, 104.0]), 96)
    assert len(got) == 1 and got["skip"].iloc[0] is None
    assert got["break_depth_pct"].iloc[0] < 20.0                         # under the roster's 20% depth


@pytest.mark.parametrize("tf", TFS)
def test_the_rule_finds_breaks_on_each_timeframe_with_every_review_feature(b1, tf) -> None:
    got = R.RULE_FUNCS["redbox_break"](_arm(tf), _ctx(b1, tf))
    assert len(got) > 0, "the fixture must give breaks, else the test is vacuous"
    assert set(X.FEATURES) <= set(got.columns)
    assert set(got["side"]) <= {-1, 1} and (got["risk_usd"] > 0).all()
    assert (got["max_bars"] == HOLD[tf] - 1).all()


# ------------------------------------------------------------------------------------------------ point in time
@pytest.mark.parametrize("tf", TFS)
@pytest.mark.parametrize("rule", X.REDBOX_RULES)
def test_appending_bars_never_changes_a_past_candidate(b1, tf, rule) -> None:
    full_ctx = _ctx(b1, tf)
    full = R.RULE_FUNCS[rule](_arm(tf, rule), full_ctx)
    part = R.RULE_FUNCS[rule](_arm(tf, rule), _ctx(b1[np.asarray(utc_index(b1["ts_utc"]) < CUT)], tf))
    assert len(part) > 0
    decided = np.asarray(full_ctx.close_ts[full["idx"].to_numpy()] <= CUT)
    pd.testing.assert_frame_equal(full[decided].reset_index(drop=True), part.reset_index(drop=True),
                                  check_dtype=False)


@pytest.mark.parametrize("tf", TFS)
def test_live_on_its_lookback_window_gives_the_history_candidate(b1, tf) -> None:
    """Live hands the rule LOOKBACK_BARS bars ending at the decision close: the channel state's whole memory, so the
    boxes, the break, the stop and the target equal the history run's."""
    arm = _arm(tf)
    ctx = _ctx(b1, tf)
    full = R.RULE_FUNCS["redbox_break"](arm, ctx)
    n = R.lookback_bars(arm, exploration.LOOKBACK)
    assert n == X.LOOKBACK_BARS["redbox_break"] >= 313
    late = full[full["idx"] >= n + 5].tail(3)
    assert len(late) > 0
    for _, row in late.iterrows():
        t = int(row["idx"])
        win = R.RuleContext(bars=ctx.bars.iloc[t - n + 1:t + 1].reset_index(drop=True), b1=ctx.b1, blackout_lo=NO,
                            blackout_hi=NO)
        live = R.RULE_FUNCS["redbox_break"](arm, win)
        mine = live[live["idx"] == n - 1]
        assert len(mine) == 1
        for col in ("side", "risk_usd", "target_r", "box_top", "box_bottom", "next_box"):
            assert mine[col].iloc[0] == pytest.approx(row[col], nan_ok=True), col


def test_no_candidate_is_decided_at_or_after_the_end_of_the_week(b1) -> None:
    ctx = _ctx(b1, "15m")
    end = pd.Timestamp("2023-10-11 12:00", tz="UTC")
    got = R.RULE_FUNCS["redbox_break"](_arm("15m", end_utc=end.isoformat()), ctx)
    assert len(got) > 0
    assert (ctx.close_ts[got["idx"].to_numpy()] < end).all()
    assert R.RULE_FUNCS["redbox_break"](_arm("15m", end_utc="2023-08-01T00:00:00Z"), ctx).empty


# ------------------------------------------------------------------------------------------------ the control
def test_the_random_ladder_keeps_the_boxes_widths_and_gaps_and_is_seeded() -> None:
    tops, bots = np.array([105.0, 101.0, 120.0]), np.array([103.0, 100.0, 111.0])
    t1, b1_ = X.random_ladder("RBC15", tops, bots)
    t2, b2 = X.random_ladder("RBC15", tops, bots)
    np.testing.assert_array_equal(t1, t2)
    np.testing.assert_array_equal(b1_, b2)
    assert sorted(np.round(t1 - b1_, 9)) == sorted([2.0, 1.0, 9.0])
    assert sorted(np.round(b1_[1:] - t1[:-1], 9)) == sorted([2.0, 6.0])        # real gaps 103-101, 111-105
    assert not np.allclose(X.random_ladder("RBC1H", tops, bots)[1], b1_)        # another arm draws another ladder


@pytest.mark.parametrize("tf", TFS)
def test_the_control_never_sends_every_candidate_is_recorded_replay_only(b1, tf) -> None:
    arm = _arm(tf, "redbox_control", control="learning:RBX")
    kept, skipped = R.screened(arm, _ctx(b1, tf))
    assert kept.empty, "a kept control candidate would be sent"
    assert len(skipped) > 0
    assert set(skipped["reason"]) <= {X.REPLAY_ONLY, X.NO_NEXT_BOX, R.STOP_LT_FLOOR, R.STOP_GT_CAP}


def test_a_per_trade_bracket_control_matches_only_the_same_construction_hold_band_and_end() -> None:
    arm = _arm("1h", arm_id="RB1H")
    ctl = A.Arm(id="RBC1H", rule="redbox_control", timeframe="1h", role="control",
                params=_params("1h", control="learning:RB1H"))
    assert A.control_match_refusal(arm, ctl) is None
    for change in ({"max_stop_usd": 30.0}, {"end_utc": "2026-11-29T22:00:00Z"}):
        bad = ctl.model_copy(update={"params": {**ctl.params, **change}})
        assert "does not match" in str(A.control_match_refusal(arm, bad))
    other_tf = A.Arm(id="RBC4H", rule="redbox_control", timeframe="4h", role="control",
                     params=_params("4h", control="learning:RB1H"))
    assert "timeframe" in str(A.control_match_refusal(arm, other_tf))
    random = A.Arm(id="C1", rule="random_bracket", timeframe="1h", role="control",
                   params={"stop_usd": 5.0, "target_r": 2.0, "hold_bars": 48})
    assert A.control_match_refusal(arm, random) is not None


def test_a_row_on_another_timeframe_or_hold_or_without_its_end_is_refused_at_load() -> None:
    with pytest.raises(ValueError, match="runs on"):
        A.Arm(id="RB5", rule="redbox_break", timeframe="5m", params=_params("15m"))
    with pytest.raises(ValueError, match="holds"):
        A.Arm(id="RB15", rule="redbox_break", timeframe="15m", params=_params("15m", hold_bars=10))
    p = _params("15m")
    del p["end_utc"]
    with pytest.raises(ValueError, match="end_utc"):
        A.Arm(id="RB15", rule="redbox_break", timeframe="15m", params=p)


# ------------------------------------------------------------------------------------------------ the spec rows
def _rows() -> dict[str, dict[str, Any]]:
    raw = yaml.safe_load((ROOT / "config" / "exploration_arms.yaml").read_text())["arms"]
    # the week's rows; RBR*/RBRC* (the rule recorded replay-only from 2026-11-24) are tests/test_replay_only_retired_arms.py's
    return {r["id"]: r for r in raw if r["rule"] in X.REDBOX_RULES and not r["id"].startswith("RBR")}


def test_the_family_is_three_learning_arms_each_with_its_replay_only_control_one_open_and_the_weeks_caps(
        tmp_path) -> None:
    rows = _rows()
    assert set(rows) == {"RB15", "RB1H", "RB4H", "RBC15", "RBC1H", "RBC4H"}
    arms = {r["id"]: A.Arm.model_validate(r) for r in rows.values()}
    learning = {k: a for k, a in arms.items() if a.role == "learning"}
    assert {a.timeframe for a in learning.values()} == set(TFS)
    caps = 0.0
    for a in learning.values():
        block = A.Learning.model_validate(a.learning)
        assert a.max_open == 1 and a.params["end_utc"] == END
        assert block.prereg == PREREG and (ROOT / PREREG).is_file()
        assert (block.until, block.since) == ("2026-11-22", "2026-11-15")
        assert A.control_match_refusal(a, arms[block.control]) is None
        assert A.dedicated_control_of(arms[block.control]) == (a.id,)
        caps += block.loss_cap_gbp
    assert caps == 300.0                                                     # the family's weekly cap
    reg = tmp_path / "registry.jsonl"
    reg.write_text("\n".join(json.dumps({"config_hash": A.Learning.model_validate(a.learning).k_id,
                                         "family": A.LEARNING_FAMILY}) for a in learning.values()) + "\n")
    path = tmp_path / "arms.yaml"
    path.write_text(yaml.safe_dump({"arms": list(rows.values())}))
    loaded = {a.id: a for a in A.load_arms(path, registry=reg)}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(A, "_now", lambda: pd.Timestamp("2026-11-15 22:00", tz="UTC"))
        loaded = {a.id: a for a in A.load_arms(path, registry=reg)}
        # the week ran unrefused; since then every red-box arm is retired (15m by the review, 1h/4h at the week's end)
        assert all(loaded[k].entry_refused for k in RETIRED_15M | RETIRED_1H4H)
        mp.setattr(A, "_now", lambda: pd.Timestamp("2026-11-23 00:00", tz="UTC"))
        late = {a.id: a for a in A.load_arms(path, registry=reg)}
        assert all("ended" in late[k].entry_refused for k in learning), "the learning end is the backstop"


def test_the_arms_are_registered_on_icm_probe_only_at_a_per_trade_cap_that_holds_the_stop_band() -> None:
    accounts = acc_mod.load_accounts(ROOT / "config" / "accounts.yaml", logins=False)
    on = {k: [a for a in (acc.exploration.arms if acc.exploration else {}) if a in _rows()]
          for k, acc in accounts.items()}
    assert sorted(on["icm-probe"]) == sorted(_rows()) and not any(v for k, v in on.items() if k != "icm-probe")
    probe = accounts["icm-probe"]
    assert probe.mode == "demo" and probe.probe_only
    for arm_id in ("RB15", "RB1H", "RB4H"):
        assert probe.exploration is not None
        # the intraday ceiling (ADR 0043), about the $40 band at 0.01 lot; the band's very top is refused by the cap
        assert probe.exploration.arms[arm_id].max_loss == 30.0 >= 39.0 * 0.752 + 0.5


# ------------------------------------------------------------------------------------------------ in the engine
def _box_around_last_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """Boxes at every bar such that the last bar's close breaks one and a second box lies beyond it."""
    def boxes(bars: pd.DataFrame) -> list[tuple[np.ndarray, np.ndarray]]:
        c = bars["close"].to_numpy(float)
        mid = (c[-2] + c[-1]) / 2.0
        if c[-1] >= c[-2]:
            tops, bots = np.array([mid, c[-1] + 5.0]), np.array([mid - 2.0, c[-1] + 4.0])
        else:
            tops, bots = np.array([mid + 2.0, c[-1] - 4.0]), np.array([mid, c[-1] - 5.0])
        return [(tops, bots)] * len(bars)
    monkeypatch.setattr(X, "_boxes", boxes)


def _redbox_engine_arm(arm_id: str, rule: str, **kw: Any) -> exploration.Arm:
    params = _params("15m", **kw)
    spec = A.Arm(id=arm_id, rule=rule, timeframe="15m", params=params, max_open=1,
                 role="control" if rule == "redbox_control" else "strategy")
    return exploration.Arm(spec=spec, style="intraday", control_share=0.01 if rule == "redbox_control" else None)


def test_a_red_box_break_is_sent_at_the_minimum_lot_through_the_riskgate_with_the_box_bracket(tmp_path,
                                                                                             monkeypatch) -> None:
    _box_around_last_close(monkeypatch)
    eng, pb = _engine(tmp_path, [_redbox_engine_arm("RB15", "redbox_break")])
    seen: list[Any] = []
    check = eng.gate.check

    def spy(intent: Any, *a: Any, **kw: Any) -> Any:
        gd = check(intent, *a, **kw)
        seen.append((intent, gd))
        return gd

    eng.gate.check = spy                                                    # type: ignore[method-assign]
    tick = _at(eng, pb, CLOSE)
    exploration.on_pass(eng, tick)
    sent = _sent(eng)
    assert sent and sent[0]["action"] == "exploration:executed:auto", eng.decisions
    [(intent, gd)] = seen
    assert intent.purpose == "exploration" and intent.arm_id == "RB15" and gd.allowed
    assert intent.volume_max == intent.volume_min == 0.01
    [tr] = eng.open.values()
    assert tr.sl is not None and tr.tp is not None
    assert min(tr.sl, tr.tp) < tick.bid <= tick.ask < max(tr.sl, tr.tp)                  # stop and target either side
    [feat] = [d for d in eng.decisions if d.get("action") == redbox_week.ACTION]
    assert feat["arm"] == "RB15" and feat["skip"] is None
    assert {"session", "mfi", "trend_swing", "golden_zone", "break_depth_pct", "box_height",
            "htf_1h_box", "htf_4h_box"} <= set(feat["features"])


def test_the_replay_only_control_places_no_order_and_its_candidate_is_recorded_blocked(tmp_path, monkeypatch) -> None:
    _box_around_last_close(monkeypatch)
    monkeypatch.setattr(X, "random_ladder", lambda arm_id, tops, bots: (tops, bots))    # the arm's own geometry
    # a control dedicated to a learning arm stops without it (`exploration_learning`): here an undedicated one
    eng, pb = _engine(tmp_path, [_redbox_engine_arm("RBC15", "redbox_control", control="RB15")])
    calls: list[Any] = []
    monkeypatch.setattr(eng.gate, "check", lambda *a, **k: calls.append(a))
    tick = _at(eng, pb, CLOSE)
    exploration.on_pass(eng, tick)
    assert not calls and not _sent(eng) and not pb.positions()
    [feat] = [d for d in eng.decisions if d.get("action") == redbox_week.ACTION]
    skipped = [d for d in eng.decisions if str(d.get("action", "")).startswith("exploration_skipped:")]
    assert feat["skip"] == X.REPLAY_ONLY
    assert skipped and skipped[0]["action"] == "exploration_skipped:" + X.REPLAY_ONLY


def test_the_session_of_a_decision_close() -> None:
    at = pd.Timestamp("2026-11-16", tz="UTC")
    got = [redbox_week.session_label(at + pd.Timedelta(hours=h)) for h in (0, 6, 7, 11, 12, 15, 16, 21, 22, 23)]
    assert got == ["asia", "asia", "london", "london", "overlap", "overlap", "newyork", "newyork", "asia", "asia"]


# ------------------------------------------------------------------------------------------------ the review's retirement


def test_rb15_and_its_dedicated_control_are_retired_from_the_sunday_open_under_the_directors_review() -> None:
    """Research-director review 2026-11-20 of the red-box week: RB15 replayed -0.077 R a candidate against its random
    ladder's -0.061 R, no edge, so it is retired (prereg "End": "the arms get `retired_day` 2026-11-22 in the spec");
    RBC15 is dedicated to RB15 alone and stops with it (as LCS5A/LCS5B with S5A/S5B)."""
    arms = {k: A.Arm.model_validate(r) for k, r in _rows().items()}
    for arm_id in RETIRED_15M:
        a = arms[arm_id]
        assert a.retired and a.retired_day == "2026-11-22", arm_id
        assert "research-director 2026-11-20" in a.retired_rule and "-0.077 R" in a.retired_rule, a.retired_rule
        assert not A.A2_RULE.search(a.retired_rule)                         # a review verdict, not A2 readability
    assert A.dedicated_control_of(arms["RBC15"]) == ("RB15",)


def test_rb1h_rb4h_and_their_controls_retire_at_the_weeks_end_without_a_history_pass() -> None:
    arms = {k: A.Arm.model_validate(r) for k, r in _rows().items()}
    assert {k for k, a in arms.items() if a.retired} == RETIRED_15M | RETIRED_1H4H
    assert all(arms[k].retired_day == "2026-11-22" and "history replay" in arms[k].retired_rule for k in RETIRED_1H4H)


def test_the_engine_on_icm_probe_takes_no_new_rb15_or_rbc15_entry_and_keeps_both_configured_for_their_exits() -> None:
    probe = acc_mod.load_accounts(ROOT / "config" / "accounts.yaml", logins=False)["icm-probe"]
    cfg = exploration.config_for(probe)
    assert cfg is not None
    configured = {a.arm_id for a in cfg.arms}
    assert RETIRED_15M | RETIRED_1H4H <= configured                          # an open position keeps every exit
    assert exploration.spec_retired(cfg) & set(_rows()) == RETIRED_15M | RETIRED_1H4H
