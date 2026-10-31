"""The live spec interpreter (ADR 0058 phase 4, backlog 140): any StrategySpec executed through the SAME primitives
as autodisc's MaskProvider. Parity with the research masks, no lookahead, the frozen arm row and its validation.

Seeded synthetic 1m bars dated 2015; no market data needed."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.data.resample import resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.features.technical import atr
from goldbot.research import arm_rules as R
from goldbot.research import arm_rules_spec as S
from goldbot.research.arms import Arm, arm_hash, load_arms
from goldbot.research.autodisc import ExploreFrame
from goldbot.research.autodisc.gen_combine import MAX_BARS, family_spec
from goldbot.research.autodisc.nulls import MaskCache
from goldbot.research.autodisc.primitives import PrimitiveProvider
from goldbot.research.autodisc.spec import ExitTemplate, Group, Leaf, StrategySpec, canonical_json, spec_hash

NO = np.zeros(0, dtype=np.int64)
CUT = pd.Timestamp("2015-10-14", tz="UTC")
T2R = ExitTemplate(stop="atr_1.0", target="2R")
LONDON, WED = Leaf(kind="time", name="london"), Leaf(kind="time", name="wed")
BOX = Leaf(kind="structure", name="family_rule", params={"rule": "box_break_close"})
SPEC_ARMS = Path(__file__).resolve().parents[2] / "config" / "spec_arms.yaml"


@pytest.fixture(scope="module")
def b1() -> pd.DataFrame:
    return ticks_to_1m(synthetic_ticks("2015-06-01", "2015-12-20", ticks_per_minute=6, seed=5)).reset_index(drop=True)


def params_of(spec: StrategySpec) -> dict[str, str]:
    return {"spec": canonical_json(spec), "spec_hash": spec_hash(spec), "prereg": "docs/research/x.md#d2610-1"}


def arm_for(spec: StrategySpec, arm_id: str = "D2610-1") -> Arm:
    a = Arm(id=arm_id, rule="spec_rule", timeframe=spec.timeframe, params=params_of(spec), max_open=1, forward_only=True)
    return a.model_copy(update={"config_hash": arm_hash(a)})


def ctx_of(b1: pd.DataFrame, tf: str, through: pd.Timestamp | None = None, n: int | None = None) -> R.RuleContext:
    frame = b1 if through is None else b1[b1["visible_at"] <= through]
    bars = resample_bars(frame, tf).reset_index(drop=True)
    if n is not None:
        bars = bars.tail(n).reset_index(drop=True)
        frame = frame[frame["ts_utc"] >= bars["ts_utc"].iloc[0]].reset_index(drop=True)
    return R.RuleContext(bars=bars, b1=frame, blackout_lo=NO, blackout_hi=NO)


def research(b1: pd.DataFrame, spec: StrategySpec) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The explore evaluator's entry mask, side and barrier ATR for `spec` (research-side primitives only)."""
    prov = PrimitiveProvider(ExploreFrame.from_frame(b1, "ts_utc"))
    mask, side = MaskCache(prov).entries(spec)
    return mask, side, atr(prov.mid_bars(spec.timeframe), 14).to_numpy(dtype=float)


# ---------------------------------------------------------------- parity
def test_live_interpreter_gives_the_research_entry_bars_of_a_family_spec(b1: pd.DataFrame) -> None:
    spec = family_spec("box_break_close", "15m", LONDON, T2R)
    mask, side, a = research(b1, spec)
    cands = R.candidates(arm_for(spec), ctx_of(b1, "15m"))
    want = np.flatnonzero(mask & np.isfinite(a))
    assert len(want) > 3
    assert cands["idx"].tolist() == want.tolist()
    assert cands["side"].tolist() == side[want].tolist()
    assert np.allclose(cands["risk_usd"], 1.0 * a[want])             # stop 1.0 ATR of the decision bars
    assert (cands["target_r"] == 2.0).all() and (cands["max_bars"] == MAX_BARS["15m"]).all()


def test_live_interpreter_matches_a_composed_predicate_spec_on_the_research_masks(b1: pd.DataFrame) -> None:
    spec = StrategySpec(timeframe="15m", session_window="london_ny", side_rule="against", side_primitive=BOX,
                        entry=Group(op="or", children=(BOX, Leaf(kind="regime", name="hurst_trend"))),
                        filter=WED, exit=ExitTemplate(stop="atr_1.5", target="1R"))
    prov = PrimitiveProvider(ExploreFrame.from_frame(b1, "ts_utc"))
    side = -prov.side_of(BOX, "15m")
    ent = (prov.predicate_mask(spec.entry, "15m") & prov.session_mask("london_ny", "15m")
           & prov.predicate_mask(WED, "15m") & (side != 0))
    a = atr(prov.mid_bars("15m"), 14).to_numpy(dtype=float)
    cands = R.candidates(arm_for(spec), ctx_of(b1, "15m"))
    want = np.flatnonzero(ent & np.isfinite(a))
    assert len(want) > 0
    assert cands["idx"].tolist() == want.tolist()
    assert cands["side"].tolist() == side[want].tolist()
    assert np.allclose(cands["risk_usd"], 1.5 * a[want]) and (cands["target_r"] == 1.0).all()


@pytest.mark.parametrize("regime", ["rvol_q_hi", "hurst_trend", "trend_strength_mid"])
def test_live_decision_on_a_trailing_window_equals_the_research_mask_at_that_bar(b1: pd.DataFrame, regime: str) -> None:
    """The engine hands a rule `lookback` bars: with the interpreter's declared warm-up the last bar's decision is the
    full-history one (the tercile rank and the Hurst band forget their start)."""
    spec = family_spec("box_break_close", "15m", Leaf(kind="regime", name=regime), T2R)
    need = S.required_bars(spec)
    assert need >= 1000
    mask, side, a = research(b1, spec)
    full = ctx_of(b1, "15m")
    ok = mask & np.isfinite(a)
    hits = np.flatnonzero(ok)
    assert len(hits) > 3
    misses = np.flatnonzero(~ok & (np.arange(len(ok)) > need))[-5:]
    for i in [*hits[-5:], *misses]:
        through = pd.Timestamp(full.bars["visible_at"].iloc[i])
        c = R.candidates(arm_for(spec), ctx_of(b1, "15m", through, n=need))
        last = c[c["idx"] == need - 1]
        assert (len(last) == 1) == bool(ok[i]), (i, through)
        if ok[i]:
            assert int(last["side"].iloc[0]) == int(side[i])


def test_the_interpreter_declares_its_warm_up_to_the_engine() -> None:
    plain = StrategySpec(timeframe="1h", side_rule="long", entry=LONDON, exit=T2R)
    ranked = plain.model_copy(update={"filter": Leaf(kind="regime", name="trend_strength_hi")})
    assert R.lookback_bars(arm_for(plain), 200) == 200
    assert R.lookback_bars(arm_for(ranked), 200) >= 1000


# ---------------------------------------------------------------- no lookahead
def test_decisions_before_a_cut_do_not_change_when_later_bars_are_perturbed(b1: pd.DataFrame) -> None:
    spec = family_spec("box_break_close", "15m", Leaf(kind="regime", name="trend_strength_mid"), T2R)
    rng = np.random.default_rng(1)
    after = (b1["ts_utc"] >= CUT).to_numpy()
    pert = b1.copy()
    pert["tick_count"] = pert["tick_count"].astype(float)
    for c in [c for c in pert.columns if c.startswith(("bid_", "ask_"))]:
        pert.loc[after, c] = pert.loc[after, c].to_numpy() * rng.uniform(0.8, 1.25, int(after.sum()))
    pert.loc[after, "tick_count"] = rng.uniform(0.001, 0.01, int(after.sum()))
    ctx = ctx_of(b1, "15m")
    a, b = (R.candidates(arm_for(spec), ctx_of(x, "15m")) for x in (b1, pert))
    early = np.asarray(ctx.close_ts <= CUT)
    ka, kb = (x[early[x["idx"].to_numpy()]].reset_index(drop=True) for x in (a, b))
    assert len(ka) > 3
    assert ka.equals(kb)


# ---------------------------------------------------------------- the arm row
def test_spec_rule_arm_is_hash_frozen_and_carries_the_canonical_spec() -> None:
    spec = family_spec("box_break_close", "15m", None, T2R)
    arm = arm_for(spec)
    assert S.spec_of(arm) == spec
    assert arm.params["spec"] == canonical_json(spec) and arm.params["spec_hash"] == spec_hash(spec)
    assert arm.config_hash == arm_hash(arm)


@pytest.mark.parametrize("bad", ["hash", "noncanonical", "timeframe", "time_exit", "structural", "leaf", "extra",
                                 "family_session", "be_trail", "v2_leaf"])
def test_a_spec_arm_that_is_not_executable_live_is_refused_at_load(bad: str) -> None:
    spec = family_spec("box_break_close", "15m", None, T2R)
    p, tf = dict(params_of(spec)), "15m"
    if bad == "hash":
        p["spec_hash"] = "0" * 64
    elif bad == "noncanonical":
        p["spec"] = spec.model_dump_json(indent=1)
    elif bad == "timeframe":
        tf = "1h"
    elif bad == "extra":
        p["note"] = "x"
    else:
        other = {"time_exit": spec.model_copy(update={"exit": ExitTemplate(stop="atr_1.0", target="time", time_bars=10)}),
                 "structural": spec.model_copy(update={"exit": ExitTemplate(stop="structural", target="2R")}),
                 "leaf": spec.model_copy(update={"filter": Leaf(kind="oscillator", name="rsi_bucket")}),
                 "family_session": spec.model_copy(update={"session_window": "london"}),
                 # grammar v2 (ADR 0058 amendment 2026-11-02): the engine has no trail for a spec arm and no v2 gate
                 "be_trail": spec.model_copy(update={"exit": ExitTemplate(stop="atr_1.0", target="be_trail")}),
                 "v2_leaf": spec.model_copy(update={"filter": Leaf(kind="macro", name="ry_down")})}[bad]
        p.update(spec=canonical_json(other), spec_hash=spec_hash(other))
    with pytest.raises(ValueError):
        Arm(id="D2610-1", rule="spec_rule", timeframe=tf, params=p)       # type: ignore[arg-type]


def test_a_spec_arm_with_an_unknown_family_is_refused_at_load() -> None:
    spec = family_spec("no_such_rule", "15m", None, T2R)
    with pytest.raises(ValueError, match="family"):
        arm_for(spec)


def test_shipped_spec_arms_file_loads_hash_checked() -> None:
    assert [a for a in load_arms(SPEC_ARMS) if a.rule != "spec_rule"] == []


@pytest.mark.parametrize("family,tf", [("level_break", "1h"), ("donchian_break", "1d"), ("low30_touch", "15m"),
                                       ("specialist", "15m")])
def test_a_family_leaf_whose_arm_needs_stored_bars_or_replica_state_is_refused_at_load(family: str, tf: str) -> None:
    """The live provider has no stored bars or replica state: such a leaf would read differently live than in research."""
    spec = family_spec(family, tf, None, T2R)
    with pytest.raises(ValueError, match="stored bars|replica"):
        arm_for(spec)


def test_a_hand_written_row_the_engine_cannot_warm_up_is_refused_at_load() -> None:
    spec = StrategySpec(timeframe="1d", side_rule="long", entry=Leaf(kind="regime", name="hurst_trend"), exit=T2R)
    assert S.required_bars(spec) > 90_000 * 60 // 86_400
    with pytest.raises(ValueError, match="warm-up"):
        arm_for(spec)
