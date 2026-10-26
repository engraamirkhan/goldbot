"""Live == history through the engine's own exploration pass (`exploration.step` on the engine's 1m memory) for the
time-structure arms: the band breaks F1A/F1C/F2A/F2C (`arm_rules_bands`, LOOKBACK_BARS 1,000 / 4,000) and WFRI
(`friday_long`, whose stop is ATR14 of the 4h bars). ARM-SPEC 8.0: "Each rule is one function used by the history
matrix and live"; that holds live only when the bars the pass hands the rule reach back far enough for its indicators
to forget their start, and the engine's memory (`EngineConfig.max_bars_in_memory`) holds them.

Also: the bar slices the pass shares across arms of one timeframe are exactly the slices each arm's own lookback gives
(`exploration._bars`), so sharing the resample changes no candidate."""
from __future__ import annotations

from collections import deque
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.config import tf_seconds
from goldbot.data.resample import resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine import EngineConfig, exploration
from goldbot.research import arm_rules as R
from goldbot.research import arm_rules_bands as B
from goldbot.research import arms as A

MEMORY = int(EngineConfig.model_fields["max_bars_in_memory"].default)
BANDS = {"hurst_band_break": "F1A", "hurst_band_break_control": "F1C", "htf_stretch_break": "F2A",
         "htf_stretch_break_control": "F2C"}


@pytest.fixture(scope="module")
def b1() -> pd.DataFrame:
    """Five and a half months of synthetic minutes with weekends: longer than the memory, so the pass reads a tail."""
    return ticks_to_1m(synthetic_ticks("2023-10-01", "2024-03-15", ticks_per_minute=1, seed=7)).reset_index(drop=True)


def _engine_at(b1: pd.DataFrame, arms: list[A.Arm], close: pd.Timestamp) -> SimpleNamespace:
    """What `step` reads at a bar close: the engine's 1m memory (the last max_bars_in_memory complete minutes) and
    the arms."""
    mem = b1[b1["visible_at"] <= close].tail(MEMORY).reset_index(drop=True)
    xa = {a.id: exploration.Arm(spec=a, style="intraday") for a in arms}
    ex = SimpleNamespace(arms=xa, retired=frozenset(), last_bar={}, queue=deque(), ahead_seen=None)
    return SimpleNamespace(exploration=ex, cfg=SimpleNamespace(live_clock=False, account_id="t"), bars_1m=mem,
                           decisions=[])


def _queued(b1: pd.DataFrame, arms: list[A.Arm], close: pd.Timestamp) -> dict[str, list[tuple[Any, ...]]]:
    eng = _engine_at(b1, arms, close)
    assert exploration.step(eng, close)  # type: ignore[arg-type]
    assert not [d for d in eng.decisions if d.get("action") == "exploration_rule_error"], eng.decisions
    out: dict[str, list[tuple[Any, ...]]] = {a.id: [] for a in arms}
    for p in eng.exploration.queue:
        out[p.arm_id].append((p.side, p.stop_usd, p.hold_bars))
    return out


def _history(b1: pd.DataFrame, arm: A.Arm) -> tuple[R.RuleContext, pd.DataFrame]:
    ctx = R.RuleContext(bars=resample_bars(b1, arm.timeframe).reset_index(drop=True), b1=b1.reset_index(drop=True),
                        blackout_lo=np.zeros(0, dtype=np.int64), blackout_hi=np.zeros(0, dtype=np.int64))
    return ctx, R.candidates(arm, ctx)


def _rows(ctx: R.RuleContext, hist: pd.DataFrame) -> dict[pd.Timestamp, tuple[int, float, int]]:
    """History's candidates by decision close: (side, stop, hold in bars) as the pass queues them."""
    return {ctx.close_ts[int(i)]: (int(side), float(risk), int(mb) + 1)
            for i, side, risk, mb in zip(hist["idx"].to_numpy(dtype=int), hist["side"].to_numpy(dtype=int),
                                         hist["risk_usd"].to_numpy(dtype=float), hist["max_bars"].to_numpy(dtype=int))}


def _band(rule: str) -> A.Arm:
    return A.Arm(id=BANDS[rule], rule=rule, timeframe="15m", params={"min_stop_usd": 0.0, "max_stop_usd": 1e9,
                                                                     "hold_bars": 4})


def _closes(ctx: R.RuleContext, hist: pd.DataFrame, first: pd.Timestamp, n: int, seed: int) -> list[tuple[Any, bool]]:
    """`n` history candidates and `n` quiet closes after `first` (the memory is full from there)."""
    close = ctx.close_ts
    fired = [close[int(i)] for i in hist["idx"] if close[int(i)] >= first]
    rng = np.random.default_rng(seed)
    later = np.flatnonzero(np.asarray(close >= first))
    quiet = [close[int(i)] for i in rng.choice(later, 3 * n, replace=False) if close[int(i)] not in set(fired)][:n]
    picks = [fired[int(j)] for j in np.linspace(0, len(fired) - 1, min(n, len(fired))).round().astype(int)]
    assert len(picks) >= min(n, 3), "the fixture must give candidates after the memory fills, else this is vacuous"
    return [(c, True) for c in picks] + [(c, False) for c in quiet]


@pytest.mark.parametrize("rule", list(BANDS))
def test_the_engines_exploration_pass_queues_every_band_break_candidate_history_has_and_no_other(b1, rule) -> None:
    arm = _band(rule)
    ctx, hist = _history(b1, arm)
    rows = (B.LOOKBACK_BARS[rule] + 1) * 15
    first = pd.Timestamp(b1["visible_at"].iloc[rows + 2 * 1440])
    by_close = _rows(ctx, hist)
    for close, fired in _closes(ctx, hist, first, 6, seed=11):
        got = _queued(b1, [arm], close)[arm.id]
        if fired:
            side, risk, hold = by_close[close]
            assert got == [(side, pytest.approx(risk, rel=1e-9), hold)], close
        else:
            assert got == [], f"live queues at {close}, history has no candidate"


def test_the_engines_exploration_pass_gives_wfri_the_history_stop_from_the_4h_atr(b1) -> None:
    """WFRI's stop is 1.0 x ATR14(4h): Wilder's recursion needs about 200 4h bars to forget its start, far more than
    the default 200 decision bars (25 4h bars) the pass hands a rule that declares nothing."""
    arm = A.Arm(id="WFRI", rule="friday_long", timeframe="30m", params={"max_stop_usd": 1e9})
    ctx, hist = _history(b1, arm)
    first = pd.Timestamp(b1["visible_at"].iloc[(exploration.lookback(arm) + 1) * 30 + 2 * 1440])
    fired = [(close, row) for close, row in _rows(ctx, hist).items() if close >= first]
    assert len(fired) >= 3
    for close, (side, risk, hold) in fired:
        assert _queued(b1, [arm], close)[arm.id] == [(side, pytest.approx(risk, rel=1e-6), hold)], close


def test_every_declared_lookback_fits_the_engines_memory() -> None:
    """A lookback the memory cannot hold is silently shortened, and live stops equalling history."""
    assert exploration.LOOKBACK_MAX_1M == MEMORY
    from goldbot.research.arm_rules_clock_box import LOOKBACK_BARS as CLOCK
    from goldbot.research.arm_rules_specialist import WARMUP_MIN
    for rule, n in B.LOOKBACK_BARS.items():
        assert (n + 1) * 15 <= MEMORY, rule
    assert (CLOCK["friday_long"] + 1) * tf_seconds("30m") // 60 <= MEMORY
    for family, minutes in WARMUP_MIN.items():
        assert minutes <= MEMORY, family


def _own_slice(frame_1m: pd.DataFrame, tf: str, through: pd.Timestamp, n: int) -> pd.DataFrame | None:
    """The reference: the slice one lookback reads, resampled on its own (`exploration._bars` before the sharing)."""
    rows = max((n + 1) * tf_seconds(tf) // 60, exploration.LOOKBACK_1M)
    src = frame_1m[frame_1m["visible_at"] <= through]
    src = src if tf == "1d" else src.tail(rows)
    bars = resample_bars(src, tf)
    bars = bars[bars["visible_at"] <= through].reset_index(drop=True)
    return None if bars.empty or pd.Timestamp(bars["visible_at"].iloc[-1]) != through else bars


@pytest.mark.parametrize("tf", ["15m", "30m", "1h", "4h", "1d"])
def test_the_bars_shared_across_lookbacks_are_exactly_each_lookbacks_own_slice(b1, tf) -> None:
    """Several lookbacks of one timeframe share one resample of the longest slice; each is exactly the resample of
    its own slice of 1m rows (which may start inside a bar), at its timeframe's closes and at closes of other ones."""
    ns = [200, 690, 1_000, 2_760, 4_000]
    seen = 0
    rng = np.random.default_rng(5)
    closes = pd.DatetimeIndex(resample_bars(b1, tf)["visible_at"])
    vis = pd.DatetimeIndex(b1["visible_at"])
    picks = [*rng.choice(closes[closes > vis[MEMORY]], 10, replace=False), *rng.choice(vis[MEMORY:], 2)]
    for close in map(pd.Timestamp, picks):
        mem = b1[b1["visible_at"] <= close].tail(MEMORY).reset_index(drop=True)
        shared = exploration._shared_bars(mem, tf, close, ns)
        for n in ns:
            own, got = _own_slice(mem, tf, close, n), shared[n]
            if own is None or got is None:
                assert own is None and got is None, (close, n)
            else:
                pd.testing.assert_frame_equal(got, own)
                assert exploration._bars(mem, tf, close, n) is not None
                seen += 1
    assert seen >= 40, "the sampled closes must give bars, else the check is vacuous"
