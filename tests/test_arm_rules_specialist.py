"""goldbot.research.arm_rules_specialist: the `specialist` arm rule (arms expansion 2026-10-26, section 3). The arm runs
the family's own `candidates_in_context` with the stop and target of its own `label_spec` and `barrier_atr`, in barrier
form, the same function in the history matrix and live; unknown or blocked families are refused at load and at run."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.data.resample import resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.data.timeutil import utc_index
from goldbot.features.mtf import TF_LABEL, context_tfs
from goldbot.features.technical import atr
from goldbot.research import arm_rules as R
from goldbot.research import arm_rules_specialist as S
from goldbot.research import arms as A
from goldbot.research.pipeline import build_decision_frame, prepare
from goldbot.specialists import SPECIALISTS

NO = np.zeros(0, dtype=np.int64)
# box_break at a rung that fires often on synthetic bars (L0 fires about once in 8 months), and trend at its default
CASES: tuple[tuple[str, A.Timeframe, dict[str, Any]], ...] = (("box_break", "15m", {"preset": "L6"}), ("trend", "1h", {}))
CUT = pd.Timestamp("2024-01-10", tz="UTC")       # a Wednesday 00:00 UTC: whole 15m/1h/4h bars on both sides


@pytest.fixture(scope="module")
def b1() -> pd.DataFrame:
    return ticks_to_1m(synthetic_ticks("2023-10-01", "2024-01-31", ticks_per_minute=1, seed=11))


def _arm(name: str, tf: A.Timeframe, extra: dict[str, Any] | None = None, arm_id: str = "X") -> A.Arm:
    return A.Arm(id=arm_id, rule="specialist", timeframe=tf, params={"specialist": name, **(extra or {})})


def _ctx(b1: pd.DataFrame, tf: str) -> R.RuleContext:
    return R.RuleContext(bars=resample_bars(b1, tf).reset_index(drop=True), b1=b1.reset_index(drop=True),
                         blackout_lo=NO, blackout_hi=NO)


@pytest.mark.parametrize(("name", "tf", "extra"), CASES)
def test_the_arm_takes_the_specialists_own_candidates_stop_target_and_time_exit(b1, name, tf, extra) -> None:
    arm = _arm(name, tf, extra)
    ctx = _ctx(b1, tf)
    got = R.RULE_FUNCS["specialist"](arm, ctx)
    assert len(got) > 0, "the fixture must give candidates, else parity is vacuous"
    spec = SPECIALISTS[name](timeframe=tf, **{**SPECIALISTS[name].presets.get(extra.get("preset", ""), {})})
    context = {TF_LABEL[x]: resample_bars(b1, x) for x in context_tfs(tf)}
    m, x = build_decision_frame(ctx.bars, context)
    cands = spec.candidates_in_context(m, x, context, None)
    own = spec.barrier_atr(m, context)
    a = (atr(m, 14) if own is None else own).to_numpy(dtype=float)
    ls = spec.label_spec
    idx = cands["idx"].to_numpy(dtype=int)
    assert got["idx"].tolist() == idx.tolist() and got["side"].tolist() == cands["side"].tolist()
    np.testing.assert_allclose(got["risk_usd"], ls.stop_atr * a[idx])
    np.testing.assert_allclose(got["target_r"], ls.target_atr / ls.stop_atr)
    hold = cands["max_bars"].to_numpy(dtype=int) if "max_bars" in cands else np.full(len(idx), ls.max_bars)
    assert got["max_bars"].tolist() == hold.tolist()            # triple_barrier's count, as the research labels'


@pytest.mark.parametrize(("name", "tf", "extra"), CASES)
def test_the_arm_matches_the_research_labels_stop_and_target_on_every_labelled_candidate(b1, name, tf, extra) -> None:
    """research.pipeline.prepare: the stop is stop_atr x atr_sig and the target target_atr, per labelled candidate."""
    arm = _arm(name, tf, extra)
    ctx = _ctx(b1, tf)
    got = R.RULE_FUNCS["specialist"](arm, ctx).set_index("idx")
    prep = prepare(S.build_specialist(arm), ctx.bars, {TF_LABEL[x]: resample_bars(b1, x) for x in context_tfs(tf)})
    lab = prep.labels
    assert len(lab) > 0
    row = got.loc[lab["idx"].to_numpy()]
    assert row["side"].tolist() == lab["side"].tolist()
    np.testing.assert_allclose(row["risk_usd"], lab["stop_atr"] * lab["atr_sig"])
    np.testing.assert_allclose(row["target_r"], lab["target_atr"] / lab["stop_atr"])


@pytest.mark.parametrize(("name", "tf", "extra"), CASES)
def test_appending_bars_never_changes_a_past_candidate(b1, name, tf, extra) -> None:
    """Live sees the bars up to now; the history run sees them all. Every candidate decided by the cut is the same."""
    arm = _arm(name, tf, extra)
    head = b1[np.asarray(utc_index(b1["ts_utc"]) < CUT)].reset_index(drop=True)
    full_ctx, head_ctx = _ctx(b1, tf), _ctx(head, tf)
    full, part = R.RULE_FUNCS["specialist"](arm, full_ctx), R.RULE_FUNCS["specialist"](arm, head_ctx)
    assert len(part) > 0
    decided = np.asarray(full_ctx.close_ts[full["idx"].to_numpy()] <= CUT)
    pd.testing.assert_frame_equal(full[decided].reset_index(drop=True), part.reset_index(drop=True))


@pytest.mark.parametrize("name", ["no_such_family", "tv_event"])
def test_an_unknown_or_blocked_specialist_is_refused_at_load_and_at_run(b1, name) -> None:
    with pytest.raises(ValueError, match="unknown specialist|blocked"):
        _arm(name, "15m")
    raw = A.Arm.model_construct(id="X", rule="specialist", timeframe="15m", params={"specialist": name}, max_open=1,
                                status="run", note="")
    with pytest.raises(ValueError, match="unknown specialist|blocked"):
        R.candidates(raw, _ctx(b1.iloc[:2000], "15m"))


def test_a_family_that_needs_tradingview_or_a_delayed_signal_is_blocked_whatever_its_name(monkeypatch) -> None:
    for attr, value in (("requires_tv", True), ("entry_delay_s", 60)):
        with monkeypatch.context() as mp:
            mp.setattr(SPECIALISTS["trend"], attr, value)
            assert "blocked" in (S.refusal("trend") or "")
    assert S.refusal("trend") is None


def test_a_missing_family_unknown_preset_or_foreign_timeframe_is_refused_at_load() -> None:
    with pytest.raises(ValueError, match="params.specialist"):
        A.Arm(id="X", rule="specialist", timeframe="1h", params={})
    with pytest.raises(ValueError, match="preset"):
        _arm("box_break", "15m", {"preset": "L99"})
    with pytest.raises(ValueError, match="does not run on"):
        _arm("trend", "4h")
    with pytest.raises(ValueError, match="not pre-registered"):
        _arm("box_rip", "1h", {"config": {"rung": "L99"}})


def test_config_wins_over_the_preset() -> None:
    assert S.specialist_config({"specialist": "box_rip", "preset": "L0", "config": {"rung": "L3"}}) == ("box_rip",
                                                                                                        {"rung": "L3"})


def test_the_arm_hash_covers_the_specialist_its_preset_and_its_config() -> None:
    base = _arm("box_rip", "1h", {"preset": "L0"})
    others = [_arm("box_retest", "1h", {"preset": "L0"}), _arm("box_rip", "1h", {"preset": "L3"}),
              _arm("box_rip", "1h", {"preset": "L0", "config": {"rung": "L3"}}),
              _arm("box_rip", "1h", {"preset": "L0", "window_local": {"from": ["Europe/London", "08:00"],
                                                                      "to": ["America/New_York", "16:00"]}})]
    hashes = {A.arm_hash(base), *(A.arm_hash(o) for o in others)}
    assert len(hashes) == 1 + len(others)
    assert A.arm_hash(_arm("box_rip", "1h", {"preset": "L0"})) == A.arm_hash(base)


def test_specialist_is_a_registered_arm_rule() -> None:
    assert "specialist" in A.RULES and "specialist" in R.RULE_FUNCS
