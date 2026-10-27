"""Box-course families as exploration arms (arms expansion 2026-10-26, sections 3-4, lane L2): box_break (15m),
box_rip, box_retest, fakey and trend_level_signal (1h), each at rung L0 through the `specialist` rule.

Section 3: "The box-course families run their 2R proxy"; the stop is "the specialist's own `label_spec` and
`barrier_atr`", i.e. the family's STRUCTURAL stop (box_course: "R = |signal close - stop|", `stop_atr` 1), not the
roster's 1.5 ATR stop. Section 4: "A candidate outside its £ cap or stop band is skipped and logged with its
propensity", and the same function runs in the history matrix and live.

Warm-up (measured 2026-10-26, synthetic seed 1 here and real 2025-01..09 bars, which reproduce the same picture):
* 60 000 1m bars (~40 trading days) reproduce every history candidate of box_break, box_rip, box_retest and
  trend_level_signal with the same R; fakey's 4h box set is still empty after 271 4h bars, so its L0 candidates were
  lost (real 2025: 0 of 1; it needs ~80 000 minutes there). Real Q3 2025, every 1h close against the full run:
  box_retest 6/6, box_rip 1/1, no candidate the history lacks;
* so the engine's memory, `EngineConfig.max_bars_in_memory`, is 90 000 1m bars (~62 trading days, engine lane E1
  2026-10-26), and the window the runner hands an arm is its declared warm-up (`exploration.lookback`:
  `arm_rules_specialist.WARMUP_MIN`, fakey 80 000 minutes), which reproduces every one of them, fakey's included."""
from __future__ import annotations

from collections import deque
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.data.resample import resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.data.timeutil import utc_index
from goldbot.engine import EngineConfig, exploration
from goldbot.features import box_course as fbc
from goldbot.features.mtf import TF_LABEL, context_tfs
from goldbot.features.technical import atr
from goldbot.research import arm_rules as R
from goldbot.research import arms as A
from goldbot.research.pipeline import build_decision_frame
from goldbot.specialists import SPECIALISTS
from goldbot.specialists import box_course as bc

NO = np.zeros(0, dtype=np.int64)
FAMILIES: tuple[tuple[str, A.Timeframe], ...] = (("box_break", "15m"), ("box_rip", "1h"), ("box_retest", "1h"),
                                                 ("fakey", "1h"), ("trend_level_signal", "1h"))
MEMORY = int(EngineConfig.model_fields["max_bars_in_memory"].default)   # 90 000 1m bars, ~62 trading days
WHOLE_MEMORY = 10**7                   # LOOKBACK_1M so large that `exploration._bars` hands an arm the whole memory
CUT = pd.Timestamp("2024-01-11", tz="UTC")     # a Thursday 00:00 UTC: every family has a candidate before it


def _families() -> list[Any]:
    return [pytest.param(n, tf, id=n) for n, tf in FAMILIES]


@pytest.fixture(scope="module")
def b1() -> pd.DataFrame:
    """11 months of synthetic minutes: every family fires at L0 at least once (box_rip once, box_retest 14 times)."""
    return ticks_to_1m(synthetic_ticks("2023-03-01", "2024-01-31", ticks_per_minute=1, seed=1)).reset_index(drop=True)


def _arm(name: str, tf: A.Timeframe, **extra: Any) -> A.Arm:
    return A.Arm(id="BX", rule="specialist", timeframe=tf, params={"specialist": name, "preset": "L0", **extra})


def _ctx(b1: pd.DataFrame, tf: str) -> R.RuleContext:
    return R.RuleContext(bars=resample_bars(b1, tf).reset_index(drop=True), b1=b1.reset_index(drop=True),
                         blackout_lo=NO, blackout_hi=NO)


_RUNS: dict[tuple[str, int], tuple[R.RuleContext, pd.DataFrame]] = {}


def _history(b1: pd.DataFrame, name: str, tf: A.Timeframe) -> tuple[R.RuleContext, pd.DataFrame]:
    """The arm on all the bars (the history matrix), computed once per family."""
    key = (name, len(b1))
    if key not in _RUNS:
        ctx = _ctx(b1, tf)
        _RUNS[key] = ctx, R.RULE_FUNCS["specialist"](_arm(name, tf), ctx)
    return _RUNS[key]


def _spec_frame(b1: pd.DataFrame, name: str, tf: str) -> tuple[Any, pd.DataFrame, dict[str, pd.DataFrame]]:
    spec = SPECIALISTS[name](timeframe=tf, rung="L0")
    context = {TF_LABEL[x]: resample_bars(b1, x) for x in context_tfs(tf)}
    m, _ = build_decision_frame(resample_bars(b1, tf).reset_index(drop=True), context)
    return spec, m, context


# ------------------------------------------------------------------------------------------------ parity
@pytest.mark.parametrize(("name", "tf"), _families())
def test_the_arm_trades_the_specialists_own_candidates_with_its_label_spec_stop_target_and_hold(b1, name, tf) -> None:
    _, got = _history(b1, name, tf)
    assert len(got) > 0, "the fixture must give candidates, else parity is vacuous"
    spec, m, context = _spec_frame(b1, name, tf)
    cands = spec.candidates_in_context(m, m, context, None)
    ls = spec.label_spec
    assert (ls.stop_atr, ls.target_atr, ls.max_bars) == (1.0, bc.TARGET_R, bc.HOLD_BARS[tf])    # 2R proxy
    idx = cands["idx"].to_numpy(dtype=int)
    assert got["idx"].tolist() == idx.tolist() and got["side"].tolist() == cands["side"].tolist()
    r = spec.barrier_atr(m, context).to_numpy(dtype=float)[idx]
    np.testing.assert_allclose(got["risk_usd"], ls.stop_atr * r)
    np.testing.assert_allclose(got["target_r"], 2.0)
    assert got["max_bars"].tolist() == cands["max_bars"].tolist()   # each signal timeframe's own hold


@pytest.mark.parametrize(("name", "tf"), _families())
def test_the_stop_is_the_familys_structural_stop_not_the_rosters_atr_stop(b1, name, tf) -> None:
    """R = |signal close - structural stop|, the signal close being the decision bar's close (the arm's market entry);
    the roster's 1.5 x ATR(14) stop is a different number on every candidate."""
    ctx, got = _history(b1, name, tf)
    spec, m, context = _spec_frame(b1, name, tf)
    events = spec.events(bc.Course(m, tf, context, sorted(spec.box_tfs())))
    frame, kept = bc.place(events, m, tf)
    assert frame["idx"].tolist() == got["idx"].tolist()
    close = m["close"].to_numpy(dtype=float)
    for (i, risk), e in zip(zip(got["idx"], got["risk_usd"]), kept):
        assert e.side * (e.entry - e.stop) > 0                       # the stop is on the losing side
        assert risk == pytest.approx(abs(e.entry - e.stop))
        assert e.entry == pytest.approx(float(close[i]))
        assert fbc.reward_ratio(e.entry, e.stop, e.target, e.side) >= bc.REWARD_FLOOR - 1e-9   # next box >= 2R
    roster = 1.5 * atr(m, 14).to_numpy(dtype=float)[got["idx"].to_numpy(dtype=int)]
    assert not np.isclose(got["risk_usd"].to_numpy(), roster, rtol=1e-3).any()


# ------------------------------------------------------------------------------------------------ prefix invariance
@pytest.mark.parametrize(("name", "tf"), _families())
def test_appending_bars_never_changes_a_past_candidate(b1, name, tf) -> None:
    full_ctx, full = _history(b1, name, tf)
    head = b1[np.asarray(utc_index(b1["ts_utc"]) < CUT)].reset_index(drop=True)
    part = R.RULE_FUNCS["specialist"](_arm(name, tf), _ctx(head, tf))
    assert len(part) > 0
    decided = np.asarray(full_ctx.close_ts[full["idx"].to_numpy()] <= CUT)
    pd.testing.assert_frame_equal(full[decided].reset_index(drop=True), part.reset_index(drop=True))


# ------------------------------------------------------------------------------------------------ warm-up
def _engine_at(b1: pd.DataFrame, arm: A.Arm, close: pd.Timestamp) -> SimpleNamespace:
    """The engine's state the runner's `step` reads at a bar close: its 1m memory (the last max_bars_in_memory complete
    minutes) and one exploration arm."""
    mem = b1[b1["visible_at"] <= close].tail(MEMORY).reset_index(drop=True)
    xarm = exploration.Arm(spec=arm, style="intraday")
    # what a skipped candidate's log reads: the pots (equal shares without a ledger), the broker, no champion
    ex = SimpleNamespace(arms={arm.id: xarm}, retired=frozenset(), last_bar={}, queue=deque(), ahead_seen=None,
                         needs_context=frozenset(), cfg=SimpleNamespace(pots={"intraday": None}), weights_cache=None,
                         db=None, broker="icm", champion_seen={}, deferred={}, netting=None, held_back={})
    return SimpleNamespace(exploration=ex, cfg=SimpleNamespace(live_clock=False, account_id="t"), bars_1m=mem,
                           decisions=[])


def _queued(b1: pd.DataFrame, arm: A.Arm, close: pd.Timestamp) -> tuple[list[Any], list[dict[str, Any]]]:
    """The runner's own `step` at `close`: what it queues and journals."""
    eng = _engine_at(b1, arm, close)
    assert exploration.step(eng, close)  # type: ignore[arg-type]
    return list(eng.exploration.queue), eng.decisions


def _rule_at(b1: pd.DataFrame, name: str, tf: A.Timeframe, close: pd.Timestamp) -> pd.DataFrame:
    """The rule on exactly the bars `step` hands the arm at `close` (`exploration._bars`, then its 1m slice), before the
    window and common filters: the candidate of the bar that just closed."""
    eng = _engine_at(b1, _arm(name, tf), close)
    got_bars = exploration.arm_bars(eng.bars_1m, _arm(name, tf), close)
    assert got_bars is not None
    bars, sub = got_bars
    got = R.RULE_FUNCS["specialist"](_arm(name, tf), R.RuleContext(bars=bars, b1=sub, blackout_lo=NO, blackout_hi=NO))
    return got[got["idx"] == len(bars) - 1].drop(columns="idx").reset_index(drop=True)


def _expect(got: pd.DataFrame, i: int) -> pd.DataFrame:
    return got[got["idx"] == i].drop(columns="idx").reset_index(drop=True)


@pytest.mark.parametrize(("name", "tf"), _families())
def test_the_engines_1m_memory_is_enough_warm_up_for_every_history_candidate(b1, name, tf, monkeypatch) -> None:
    """With the arm reading the engine's whole memory (~40 trading days), every history candidate is decided the same
    (side, structural R, 2R, hold), and the runner queues each one its filters keep with that stop, target and hold."""
    monkeypatch.setattr(exploration, "LOOKBACK_1M", WHOLE_MEMORY)
    ctx, got = _history(b1, name, tf)
    assert len(got) > 0
    for i in got["idx"].tolist():
        pd.testing.assert_frame_equal(_rule_at(b1, name, tf, ctx.close_ts[i]), _expect(got, i), rtol=1e-6)
    for i, risk, hold in R.candidates(_arm(name, tf), ctx)[["idx", "risk_usd", "max_bars"]].itertuples(index=False):
        q, _ = _queued(b1, _arm(name, tf), ctx.close_ts[int(i)])
        assert [(p.stop_usd, p.target_usd, p.hold_bars) for p in q] == [
            pytest.approx((risk, 2 * risk, int(hold) + 1), rel=1e-6)], ctx.close_ts[int(i)]


@pytest.mark.parametrize(("name", "tf"), _families())
def test_the_runners_live_arm_window_reproduces_every_history_candidate(b1, name, tf) -> None:
    ctx, got = _history(b1, name, tf)
    assert len(got) > 0
    for i in got["idx"].tolist():
        pd.testing.assert_frame_equal(_rule_at(b1, name, tf, ctx.close_ts[i]), _expect(got, i), rtol=1e-6)


# ------------------------------------------------------------------------------------------------ stop band / £ cap
# box_rip's one fixture candidate (2024-01-10 21:00 UTC) sits in the common 20:45-22:15 cut, so it has no band case;
# the band is the rule-agnostic `post_filter`, the same code for every family.
BANDED = [f for f in FAMILIES if f[0] != "box_rip"]


def _first(b1: pd.DataFrame, name: str, tf: A.Timeframe) -> tuple[R.RuleContext, int, float]:
    """The first candidate the arm's own filters keep: its row and structural R."""
    ctx, _ = _history(b1, name, tf)
    kept = R.candidates(_arm(name, tf), ctx)
    assert len(kept) > 0
    return ctx, int(kept["idx"].iloc[0]), float(kept["risk_usd"].iloc[0])


@pytest.mark.parametrize(("name", "tf"), BANDED)
def test_a_structural_stop_outside_the_stop_band_is_skipped(b1, name, tf) -> None:
    ctx, i, risk = _first(b1, name, tf)
    for band in ({"max_stop_usd": risk - 0.01}, {"min_stop_usd": risk + 0.01}):
        assert i not in R.candidates(_arm(name, tf, **band), ctx)["idx"].tolist(), band
    inside = R.candidates(_arm(name, tf, min_stop_usd=risk - 0.01, max_stop_usd=risk + 0.01), ctx)
    assert i in inside["idx"].tolist()


@pytest.mark.parametrize(("name", "tf"), BANDED)
def test_a_structural_stop_outside_the_stop_band_is_journalled_by_the_runner(b1, name, tf, monkeypatch) -> None:
    monkeypatch.setattr(exploration, "LOOKBACK_1M", WHOLE_MEMORY)
    ctx, i, risk = _first(b1, name, tf)
    arm = _arm(name, tf)
    assert len(_queued(b1, arm, ctx.close_ts[i])[0]) == 1                # inside the band: queued
    q, decisions = _queued(b1, _arm(name, tf, max_stop_usd=risk - 0.01), ctx.close_ts[i])
    assert not q
    [skip] = [d for d in decisions if d.get("agent") == exploration.PREFIX + arm.id]
    assert skip["action"] == "exploration_skipped:stop_gt_cap" and skip["propensity"] > 0, decisions
