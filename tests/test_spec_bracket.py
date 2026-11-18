"""The discovered arms' per-trade bracket (docs/research/prereg-amendment-2026-11-18-discovered-arms.md section 4).

"The construction is one shared function, `arm_rules_spec.bracket(spec, bars, t)`, used for live, replay and the twin.
It evaluates the spec's exit template (stop, target, time barrier) on the bars closed at the decision." A bracket that
was not fixed in the candidate's INSERT at its decision, or that does not match the recompute within 1e-9, is void and
never sent (quant ruling 2026-11-18, Q6). Nothing here is market data: the bars are a seeded random walk."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from goldbot.config import tf_seconds
from goldbot.research import arm_rules
from goldbot.research import arm_rules_spec as S
from goldbot.research.arms import PER_TRADE_KEY, PER_TRADE_SAME, Arm, arm_hash, control_match_refusal
from goldbot.research.autodisc.gen_combine import family_spec
from goldbot.research.autodisc.spec import ExitTemplate, canonical_json, spec_hash

SPEC = family_spec("box_break_close", "1h", None, ExitTemplate(stop="atr_1.0", target="2R"))
H = spec_hash(SPEC)
END = "2027-01-04T22:00:00Z"


def bars(n: int = 300, seed: int = 7, start: str = "2026-11-02 00:00") -> pd.DataFrame:
    """1h bars in the store schema (bid/ask OHLC, visible_at = the bar's close)."""
    rng = np.random.default_rng(seed)
    close = 2000.0 + np.cumsum(rng.normal(0, 3.0, n))
    open_ = np.concatenate(([2000.0], close[:-1]))
    hi = np.maximum(open_, close) + rng.uniform(0.1, 2.0, n)
    lo = np.minimum(open_, close) - rng.uniform(0.1, 2.0, n)
    ts = pd.date_range(start, periods=n, freq="1h", tz="UTC")
    out = pd.DataFrame({"ts_utc": ts, "visible_at": ts + pd.Timedelta(hours=1), "spread_mean": 0.2,
                        "tick_count": 100})
    for k, v in (("open", open_), ("high", hi), ("low", lo), ("close", close)):
        out[f"bid_{k}"] = v - 0.1
        out[f"ask_{k}"] = v + 0.1
    return out


def learning_params(**over: object) -> dict[str, object]:
    return {"spec": canonical_json(SPEC), "spec_hash": H, "prereg": S.DISCOVERED_PREREG + "#5",
            PER_TRADE_KEY: S.bracket_key(H), "hold_bars": S.hold_bars(SPEC), "min_stop_usd": S.MIN_STOP_USD,
            "max_stop_usd": 30.0, "end_utc": END, **over}


def control_params(**over: object) -> dict[str, object]:
    p = {k: v for k, v in learning_params().items() if k != "prereg"}
    return {**p, "control": "learning:D2611-1", "signal_rate": 0.05, **over}


def frozen(arm: Arm) -> Arm:
    return arm.model_copy(update={"config_hash": arm_hash(arm)})


def arm_and_control() -> tuple[Arm, Arm]:
    arm = frozen(Arm(id="D2611-1", rule="spec_rule", timeframe="1h", params=learning_params(), forward_only=True))
    ctl = frozen(Arm(id="D2611-1C", rule=S.SPEC_CONTROL, timeframe="1h", params=control_params(), role="control",
                     forward_only=True))
    return arm, ctl


# ---------------------------------------------------------------- the shared construction
def test_bracket_is_unchanged_by_bars_after_decision():
    """T6: perturbing or removing every bar after the decision leaves the bracket unchanged (truncation test)."""
    b = bars()
    t = pd.Timestamp(b["visible_at"].iloc[200])
    want = S.bracket(SPEC, b, t)
    assert want is not None and want.stop_dist > 0
    later = b["visible_at"] > t
    shaken = b.copy()
    for col in [c for c in b.columns if c.startswith(("bid_", "ask_"))]:
        shaken.loc[later, col] = shaken.loc[later, col] * 3.0 + 500.0
    assert S.bracket(SPEC, shaken, t) == want
    assert S.bracket(SPEC, b[~later], t) == want


def test_the_bracket_is_the_specs_exit_template_on_atr14_of_the_decision_bars():
    b = bars()
    t = pd.Timestamp(b["visible_at"].iloc[150])
    got = S.bracket(SPEC, b, t)
    assert got is not None
    assert got.target_dist == pytest.approx(2.0 * got.stop_dist, rel=1e-12)          # target 2R of a 1.0 ATR stop
    assert got.max_hold_s == S.hold_bars(SPEC) * tf_seconds("1h")


def test_the_live_candidates_carry_exactly_the_shared_bracket():
    """`spec_candidates` (live and replay) and `bracket` agree on every candidate within 1e-9."""
    b = bars(400)
    arm, _ = arm_and_control()
    ctx = arm_rules.RuleContext(bars=b, b1=b.iloc[:0], blackout_lo=np.zeros(0, np.int64),
                                blackout_hi=np.zeros(0, np.int64))
    c = S.spec_candidates(arm, ctx)
    for row in c.to_dict("records"):
        t = pd.Timestamp(b["visible_at"].iloc[int(row["idx"])])
        want = S.bracket(SPEC, b, t)
        assert want is not None
        got = S.Bracket(stop_dist=float(row["risk_usd"]), target_dist=float(row["target_r"]) * float(row["risk_usd"]),
                        max_hold_s=(int(row["max_bars"]) + 1) * tf_seconds("1h"))
        assert S.bracket_mismatch(got, want) is None


# ---------------------------------------------------------------- written at the decision, or void
DECISION = pd.Timestamp("2026-11-12 10:00", tz="UTC")


def _row(b: S.Bracket | None, recorded_s: float = 2.0, entry_s: float | None = 5.0) -> dict[str, object]:
    d = int(DECISION.value)
    return {"decision_ns": d, "recorded_ns": d + int(recorded_s * 1e9),
            "entry_ns": None if entry_s is None else d + int(entry_s * 1e9),
            "stop_dist": None if b is None else b.stop_dist, "target_dist": None if b is None else b.target_dist,
            "max_hold_s": None if b is None else b.max_hold_s}


GOOD = S.Bracket(stop_dist=12.5, target_dist=25.0, max_hold_s=49 * 3600)


def test_a_bracket_in_the_insert_at_the_decision_that_matches_the_recompute_passes():
    assert S.bracket_void(_row(GOOD), GOOD) is None


@pytest.mark.parametrize("case", ["set_by_update", "recorded_late", "recorded_after_entry", "stop_off",
                                  "target_off", "hold_off", "no_recompute"])
def test_a_bracket_written_after_decision_utc_is_refused(case):
    """T4: a field set later by UPDATE (absent from the INSERT, so the row's value is not the INSERT's), one recorded
    after decision + 60 s or after the entry, or one that differs from the recompute by more than 1e-9: void."""
    row = _row(GOOD)
    recompute: S.Bracket | None = GOOD
    if case == "set_by_update":
        inserted = _row(None)
        row = {**inserted, "stop_dist": GOOD.stop_dist, "target_dist": GOOD.target_dist,
               "max_hold_s": GOOD.max_hold_s}
        assert S.bracket_void(inserted, GOOD) is not None
        assert S.bracket_void(row, GOOD, inserted=inserted) is not None
        return
    if case == "recorded_late":
        row = _row(GOOD, recorded_s=61.0, entry_s=None)
    elif case == "recorded_after_entry":
        row = _row(GOOD, recorded_s=10.0, entry_s=5.0)
    elif case == "stop_off":
        recompute = GOOD.model_copy(update={"stop_dist": GOOD.stop_dist + 2e-9})
    elif case == "target_off":
        recompute = GOOD.model_copy(update={"target_dist": GOOD.target_dist - 2e-9})
    elif case == "hold_off":
        recompute = GOOD.model_copy(update={"max_hold_s": GOOD.max_hold_s + 1})
    else:
        recompute = None
    assert S.bracket_void(row, recompute) is not None


def test_a_bracket_within_1e9_of_the_recompute_is_not_void():
    assert S.bracket_void(_row(GOOD), GOOD.model_copy(update={"stop_dist": GOOD.stop_dist + 5e-10})) is None


# ---------------------------------------------------------------- the arm's row and its replay-only control
def test_a_learning_spec_arm_and_its_control_carry_the_same_per_trade_bracket_key_and_match():
    arm, ctl = arm_and_control()
    assert arm.params[PER_TRADE_KEY] == ctl.params[PER_TRADE_KEY] == "spec_exit:" + H[:12]
    assert all(arm.params[k] == ctl.params[k] for k in PER_TRADE_SAME)
    assert control_match_refusal(arm, ctl) is None


def test_control_match_refuses_when_one_side_lacks_the_per_trade_bracket():
    arm, ctl = arm_and_control()
    bare = frozen(Arm(id="D2611-1C", rule=S.SPEC_CONTROL, timeframe="1h", params=control_params(), role="control"))
    plain = Arm(id="D2611-1", rule="spec_rule", timeframe="1h", params={k: arm.params[k] for k in S.PARAM_KEYS})
    assert control_match_refusal(plain, bare) is not None
    with pytest.raises(ValueError):
        Arm(id="D2611-1C", rule=S.SPEC_CONTROL, timeframe="1h", role="control",
            params={k: v for k, v in control_params().items() if k != PER_TRADE_KEY})


@pytest.mark.parametrize("bad", [{PER_TRADE_KEY: "spec_exit:" + "0" * 12}, {"hold_bars": 12},
                                 {"min_stop_usd": 0.5}, {"end_utc": "2027-01-04T22:00:00"}, {"exit": "runner"},
                                 {"prereg": "docs/research/other.md#1"}])
def test_a_learning_spec_row_with_a_bracket_param_other_than_the_registered_one_is_refused(bad):
    with pytest.raises(ValueError):
        Arm(id="D2611-1", rule="spec_rule", timeframe="1h", params=learning_params(**bad))


@pytest.mark.parametrize("tf_spec", ["15m", "1d"])
def test_a_learning_spec_arm_runs_on_1h_or_4h_only(tf_spec):
    """Section 2.4 (d) allows 1h, 4h, 1d; 1d is refused here until its 22:30 entry meets the 60 s latency guard
    (ADR 0081: a 1d candidate is sent 90 minutes after its decision close)."""
    s = family_spec("box_break_close", "15m", None, ExitTemplate(stop="atr_1.0", target="2R")) if tf_spec == "15m" \
        else family_spec("squeeze_release", "4h", None, ExitTemplate(stop="atr_1.0", target="2R"))
    if tf_spec == "1d":
        s = s.model_copy(update={"timeframe": "1d"})
    p = learning_params(spec=canonical_json(s), spec_hash=spec_hash(s), **{PER_TRADE_KEY: S.bracket_key(spec_hash(s))})
    with pytest.raises(ValueError):
        Arm(id="D2611-1", rule="spec_rule", timeframe=tf_spec, params=p)


def test_the_control_draws_seeded_entries_at_its_signal_rate_both_sides_and_never_an_order():
    _, ctl = arm_and_control()
    ctl = frozen(ctl.model_copy(update={"params": control_params(signal_rate=0.3)}))
    b = bars(2000, start="2026-08-03 00:00")
    ctx = arm_rules.RuleContext(bars=b, b1=b.iloc[:0], blackout_lo=np.zeros(0, np.int64),
                                blackout_hi=np.zeros(0, np.int64))
    kept, skipped = arm_rules.screened(ctl, ctx)
    assert kept.empty                                        # nothing is ever left to send
    assert set(skipped["reason"]) <= {S.REPLAY_ONLY, arm_rules.STOP_LT_FLOOR, arm_rules.STOP_GT_CAP}
    assert (skipped["reason"] == S.REPLAY_ONLY).any()
    allowed = arm_rules.common_filter_ok(ctx.close_ts, ctx.blackout_lo, ctx.blackout_hi)
    allowed &= np.asarray(ctx.close_ts < pd.Timestamp(END))
    rate = len(S.spec_control(ctl, ctx)) / max(int(allowed[S.ATR_BARS:].sum()), 1)
    assert 0.25 < rate < 0.35
    sides = S.spec_control(ctl, ctx)["side"]
    assert 0.4 < (sides > 0).mean() < 0.6
    again = S.spec_control(ctl, ctx)
    assert again.equals(S.spec_control(ctl, ctx))            # seeded on (arm id, bar close): restart-safe
    for row in again.head(5).to_dict("records"):
        t = pd.Timestamp(b["visible_at"].iloc[int(row["idx"])])
        want = S.bracket(SPEC, b, t)
        assert want is not None and abs(float(row["risk_usd"]) - want.stop_dist) <= 1e-9


def test_no_candidate_is_decided_at_or_after_end_utc():
    arm, ctl = arm_and_control()
    b = bars(2000, start="2026-11-02 00:00")
    end = pd.Timestamp(b["visible_at"].iloc[1000])
    for a in (arm, ctl):
        a = frozen(a.model_copy(update={"params": {**a.params, "end_utc": end.strftime("%Y-%m-%dT%H:%M:%SZ")}}))
        ctx = arm_rules.RuleContext(bars=b, b1=b.iloc[:0], blackout_lo=np.zeros(0, np.int64),
                                    blackout_hi=np.zeros(0, np.int64))
        out = arm_rules.RULE_FUNCS[a.rule](a, ctx)
        assert (ctx.close_ts[out["idx"].to_numpy(int)] < end).all()
