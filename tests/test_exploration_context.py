"""goldbot.engine.exploration_context: what an exploration arm's rule reads beyond the engine's ~40-day 1m window, so
live decides exactly as the history run ("The same function runs in the history matrix and live", arms expansion
2026-10-26 section 3; ARM-SPEC 8.0).

* Stored bars: C30A/C30C (30 completed trading days + ATR14(1d)), DOND (Donchian-55 on 1d) and G1A/G1C (previous-month
  levels on 1h) get the store's bars of their own timeframe before the window, point in time; no store, a gap between
  the stored bars and the window, or too few bars: the arm is held back (fail closed), never decided on a short input.
* Carried replica state: smc_structure tvr1/tvr2 (XTV1, VXTV2) resume from a `ReplicaFeed` bootstrapped from the 2010
  series start (B4a), so their running FVG threshold and structure equal the history run's; a store that does not
  start the 2010 series never gives state.
* Weekends: a swing arm whose hold reaches the Friday cut is refused (`weekend_hold`), exactly as W1A/W1B."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.data.resample import resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.data.timeutil import utc_index
from goldbot.db.connection import Database
from goldbot.engine import exploration
from goldbot.engine.exploration_context import ArmContext, history_bars, needs_context
from goldbot.research import arm_rules as R
from goldbot.research import arms as A

NO = np.zeros(0, dtype=np.int64)
KEEP_1M = 60_000                                    # EngineConfig.max_bars_in_memory
G1: dict[str, Any] = {"levels": ["pdh", "pdl", "pdc", "asia_hl", "london_hl", "ny_hl", "pwh", "pwl", "pmh", "pml",
                                 "round_10_50_100_within_3atr"],
                      "level_window_local": [["17:00", "America/New_York"], ["08:00", "Europe/London"]],
                      "break_atr": 0.1, "stop_atr": 1.0, "target_r": 2.0, "hold_bars": 32, "one_per_day": True}
# (G1's $3-22.60 stop band is left out: the synthetic path's 1h ATR puts every stop above it, and the band is
# `post_filter`'s, identical on both sides)
STORED = (("C30A", "low30_touch", "15m", {"hold_bars": 460}),
          ("C30C", "low30_control", "15m", {"hold_bars": 460}),
          ("DOND", "donchian_break", "1d", {"hold_bars": 10}),
          ("G1A", "level_break", "1h", G1),
          ("G1C", "level_break", "1h", {**G1, "control": "random_distance_level"}))
SMC = (("XTV1", "tvr1"), ("VXTV2", "tvr2"))


class FakeStore:
    """The store's release bars (`Store.read` subset): bars_<tf> of the 1m fixture, source dukascopy."""

    def __init__(self, b1: pd.DataFrame, start: pd.Timestamp | None = None):
        b1 = b1 if start is None else b1[np.asarray(utc_index(b1["ts_utc"]) >= start)]
        self.b1, self.reads = b1, 0
        self.tables: dict[str, pd.DataFrame] = {}

    def read(self, table: str, *, source: str | None = None, symbol: str = "XAUUSD", start: Any = None,
             end: Any = None, columns: Any = None) -> pd.DataFrame:
        self.reads += 1
        assert source == "dukascopy" and table.startswith("bars_")
        tf = table.removeprefix("bars_")
        if tf not in self.tables:
            self.tables[tf] = resample_bars(self.b1, tf).reset_index(drop=True)
        b = self.tables[tf]
        ts = utc_index(b["ts_utc"])
        keep = np.ones(len(b), dtype=bool)
        if start is not None:
            keep &= np.asarray(ts >= pd.Timestamp(start))
        if end is not None:
            keep &= np.asarray(ts < pd.Timestamp(end))
        out = b[keep].reset_index(drop=True)
        return out[list(columns)] if columns else out


@pytest.fixture(scope="module")
def b1() -> pd.DataFrame:
    # seed 5: a path whose last months hold first touches of the 30-day low (C30A), not only the hashed control's
    return ticks_to_1m(synthetic_ticks("2022-10-02", "2024-01-31", ticks_per_minute=1, seed=5))


@pytest.fixture(scope="module")
def b1_2010() -> pd.DataFrame:
    """From the replica's pinned series start (B4a), as research gives the smc family."""
    return ticks_to_1m(synthetic_ticks("2010-01-03", "2010-04-30", ticks_per_minute=1, seed=7))


def _arm(arm_id: str, rule: str, tf: str, params: dict[str, Any]) -> A.Arm:
    return A.Arm(id=arm_id, rule=rule, timeframe=tf, params=params)  # type: ignore[arg-type]


def _smc(arm_id: str, preset: str) -> A.Arm:
    return A.Arm(id=arm_id, rule="specialist", timeframe="15m",
                 params={"specialist": "smc_structure", "preset": preset, "min_stop_usd": 3.0, "max_stop_usd": 22.6})


def _hist(arm: A.Arm, b1: pd.DataFrame) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    ctx = R.RuleContext(bars=resample_bars(b1, arm.timeframe).reset_index(drop=True), b1=b1.reset_index(drop=True),
                        blackout_lo=NO, blackout_hi=NO)
    return R.candidates(arm, ctx), ctx.close_ts


def _live(ac: ArmContext, arm: A.Arm, b1: pd.DataFrame, close: pd.Timestamp, keep: int = KEEP_1M) -> pd.DataFrame | str:
    complete = b1[b1["visible_at"] <= close].tail(keep).reset_index(drop=True)
    got = ac.inputs(arm, complete, close)
    if isinstance(got, str):
        return got
    live = R.candidates(arm, R.RuleContext(bars=got.bars, b1=got.b1, blackout_lo=NO, blackout_hi=NO,
                                           spec_ctx=got.spec_ctx))
    return live[live["idx"] == len(got.bars) - 1].drop(columns="idx").reset_index(drop=True)


def _ac(tmp_path: Path, store: Any, **kw: Any) -> ArmContext:
    return ArmContext(Database.open(str(tmp_path), "aux"), store, "XAUUSD", **kw)


# ------------------------------------------------------------------------------------------------ stored bars
@pytest.mark.parametrize(("arm_id", "rule", "tf", "params"), STORED)
def test_live_with_the_stored_bars_before_the_window_decides_exactly_as_the_history_run(tmp_path, b1, arm_id, rule, tf,
                                                                                       params) -> None:
    """At every sampled close of the last weeks (each history candidate and a spread of quiet closes) the engine's
    window, extended from the store, gives the history run's candidate on that bar: same side, stop, target and hold,
    or none where history has none."""
    arm = _arm(arm_id, rule, tf, params)
    assert needs_context(arm)
    hist, close = _hist(arm, b1)
    first = history_bars(arm)                           # the first close with the declared bars behind it
    want = [int(i) for i in hist["idx"] if int(i) >= first]
    assert want, "the span must hold history candidates, else the check is vacuous"
    span = np.arange(first, len(close))
    quiet = [int(i) for i in span[:: max(1, len(span) // 25)] if int(i) not in set(want)]
    ac = _ac(tmp_path, FakeStore(b1))
    for i in sorted({*want[-12:], *quiet}):
        got = _live(ac, arm, b1, close[i])
        assert not isinstance(got, str), (close[i], got)
        exp = hist[hist["idx"] == i].drop(columns="idx").reset_index(drop=True)
        pd.testing.assert_frame_equal(got.drop(columns="risk_usd"), exp.drop(columns="risk_usd"), obj=str(close[i]))
        np.testing.assert_allclose(got["risk_usd"], exp["risk_usd"], rtol=1e-6)


def test_the_stored_bars_are_read_once_not_at_every_close(tmp_path, b1) -> None:
    arm = _arm(*STORED[2][:3], STORED[2][3])
    _, close = _hist(arm, b1)
    store = FakeStore(b1)
    ac = _ac(tmp_path, store)
    for c in close[-5:]:
        assert not isinstance(_live(ac, arm, b1, c), str)
    assert store.reads == 1


def test_without_a_store_the_arm_is_held_back_not_decided_on_the_short_window(tmp_path, b1) -> None:
    for arm_id, rule, tf, params in STORED:
        arm = _arm(arm_id, rule, tf, params)
        _, close = _hist(arm, b1)
        assert _live(_ac(tmp_path, None), arm, b1, close[-1]) == "history_unavailable"


def test_a_gap_between_the_stored_bars_and_the_window_holds_the_arm_back(tmp_path, b1) -> None:
    """A store whose bars end weeks before the engine's window would join two non-adjacent stretches: never."""
    arm = _arm(*STORED[2][:3], STORED[2][3])
    _, close = _hist(arm, b1)
    stale = FakeStore(b1[np.asarray(utc_index(b1["ts_utc"]) < close[-1] - pd.Timedelta(days=80))])
    assert _live(_ac(tmp_path, stale), arm, b1, close[-1]) == "history_unavailable"


def test_stored_bars_never_reach_past_the_decision_close(tmp_path, b1) -> None:
    """Point in time: a stored bar overlapping the window or closing after the decision is never read."""
    arm = _arm(*STORED[2][:3], STORED[2][3])
    _, close = _hist(arm, b1)
    c = close[-30]
    complete = b1[b1["visible_at"] <= c].tail(KEEP_1M).reset_index(drop=True)
    got = _ac(tmp_path, FakeStore(b1)).inputs(arm, complete, c)
    assert not isinstance(got, str)
    ts = utc_index(got.bars["ts_utc"])
    assert ts.is_monotonic_increasing and ts.is_unique
    assert pd.Timestamp(got.bars["visible_at"].iloc[-1]) == c
    assert (pd.to_datetime(got.bars["visible_at"], utc=True) <= c).all()


# ------------------------------------------------------------------------------------------------ smc replica state
@pytest.mark.parametrize(("arm_id", "preset"), SMC)
def test_smc_arm_with_the_state_carried_from_the_2010_series_start_decides_as_the_history_run(tmp_path, b1_2010, arm_id,
                                                                                            preset) -> None:
    """B4a: TVR-1's gap threshold is a running mean from the series start. The exploration feed bootstraps once from
    the store's 2010 series and resumes bar by bar; on every close after its warm-up it gives history's candidate."""
    arm = _smc(arm_id, preset)
    assert needs_context(arm)
    hist, close = _hist(arm, b1_2010)
    lo = close[-1] - pd.Timedelta(days=4)
    span = np.flatnonzero(np.asarray(close >= lo))
    want = {int(i) for i in hist["idx"] if close[int(i)] >= lo}
    assert want, "the span must hold history candidates, else the check is vacuous"
    ac = _ac(tmp_path, FakeStore(b1_2010), warm_wait_s=120.0)
    decided = 0
    for i in span:                                      # every close, as the engine: the feed advances bar by bar
        got = _live(ac, arm, b1_2010, close[int(i)], keep=12_000)
        if isinstance(got, str):
            assert got == "replica_warming" and decided == 0, (close[int(i)], got)
            continue
        decided += 1
        exp = hist[hist["idx"] == int(i)].drop(columns="idx").reset_index(drop=True)
        pd.testing.assert_frame_equal(got.drop(columns="risk_usd"), exp.drop(columns="risk_usd"), obj=str(close[int(i)]))
        np.testing.assert_allclose(got["risk_usd"], exp["risk_usd"], rtol=1e-6)
    assert decided >= len(span) - 5


def test_a_store_that_does_not_start_the_2010_series_never_gives_smc_state(tmp_path, b1_2010) -> None:
    """Fail closed: the live replica starts where research starts it (2010), or the arm sends nothing."""
    arm = _smc("XTV1", "tvr1")
    _, close = _hist(arm, b1_2010)
    ac = _ac(tmp_path, FakeStore(b1_2010, start=pd.Timestamp("2010-02-01", tz="UTC")), warm_wait_s=120.0)
    for c in close[-6:]:
        assert _live(ac, arm, b1_2010, c, keep=12_000) == "replica_warming"


def test_the_exploration_replica_feed_is_its_own_and_pinned_to_the_research_series_start(tmp_path) -> None:
    from goldbot.features.smc_live import SERIES_START
    ac = _ac(tmp_path, None)
    assert ac.replica.series_start == SERIES_START
    agent = ac.agent(_smc("XTV1", "tvr1"))
    assert ac.replica.feed(agent).feed.startswith("xauusd-s2010-15m-")


def test_rules_that_need_nothing_beyond_the_window_keep_the_engines_own_path() -> None:
    assert not needs_context(A.Arm(id="W1A", rule="tsmom", timeframe="4h", params={"config": {"max_bars": 12}}))
    assert not needs_context(_arm("DON4", "donchian_break", "4h", {"hold_bars": 24}))
    assert not needs_context(A.Arm(id="XEQ", rule="specialist", timeframe="15m",
                                   params={"specialist": "eq_liquidity_run", "preset": "prereg"}))
    assert exploration.lookback(_smc("XTV1", "tvr1")) * 15 * 60 <= exploration.LOOKBACK_MAX_1M * 60


# ------------------------------------------------------------------------------------------------ the weekend
def test_a_swing_hold_that_reaches_the_friday_cut_is_refused_as_the_tsmom_swing_arms_are(tmp_path) -> None:
    """ADR 0043 X3 with quant Q2 (`crosses_friday`, every style, "no label may straddle the weekend gap"): DOND's
    10-day, W1B's 11-day and PDC4's 32 x 4h (128 h) holds reach the cut from any hour of the open week, so none can
    enter; C30's 460 x 15m hold fits only from the Sunday reopen. Never loosened here: a hold may cross the weekend
    only by an arm amendment that runs the Friday cut in history too (as WFRI, ARM-SPEC 8.14 (2))."""
    from types import SimpleNamespace
    eng: Any = SimpleNamespace(cfg=SimpleNamespace(weekend_cut="21:30", server_tz="Europe/Athens"))
    week = pd.date_range("2025-03-02 23:00", "2025-03-07 18:00", freq="1h", tz="UTC")
    open_week = [t for t in week if t.dayofweek < 5 or t.hour >= 23]
    assert all(exploration.crosses_friday(eng, t, 10 * 86400) for t in open_week)          # DOND
    assert all(exploration.crosses_friday(eng, t, 11 * 86400) for t in open_week)          # W1B
    assert all(exploration.crosses_friday(eng, t, 32 * 4 * 3600) for t in open_week)       # PDC4
    c30 = 460 * 900
    assert not exploration.crosses_friday(eng, pd.Timestamp("2025-03-02 23:15", tz="UTC"), c30)
    assert exploration.crosses_friday(eng, pd.Timestamp("2025-03-03 12:00", tz="UTC"), c30)


# ------------------------------------------------------------------------------------------------ the engine
def test_the_engine_queues_the_history_candidate_of_a_stored_bars_arm_and_holds_it_back_without_a_store(tmp_path,
                                                                                                    b1) -> None:
    """`exploration.step`: with the store, DOND's signal at a 1d close is the history run's (side, stop, hold); without
    one it is journalled held back once (`exploration_held_back`), never decided on the short window."""
    from tests.test_arms_smc import _close_at
    from tests.test_arms_smc import _engine as smc_engine
    arm = _arm("DOND", "donchian_break", "1d", {"hold_bars": 10})
    hist, close = _hist(arm, b1)
    i = int(hist["idx"].iloc[-1])
    c, row = close[i], hist.iloc[-1]
    eng, pb = smc_engine(tmp_path, arm, cap=40.0)
    ex = eng.exploration
    assert ex is not None and ex.needs_context == {"DOND"}
    _close_at(eng, pb, b1, c)
    assert exploration.step(eng, c) and exploration.step(eng, c + pd.Timedelta(days=1))
    assert not ex.queue
    held = [d for d in eng.decisions if d.get("action") == "exploration_held_back"]
    assert [(d["agent"], d["reason"]) for d in held] == [("explore:DOND", "history_unavailable")]
    ex.context = ArmContext(ex.db, FakeStore(b1), "XAUUSD")
    ex.last_bar.clear()
    _close_at(eng, pb, b1, c)
    assert exploration.step(eng, c)
    [pending] = list(ex.queue)
    assert (pending.side, pending.bar_ts, pending.hold_bars) == (int(row["side"]), c, int(row["max_bars"]) + 1)
    assert pending.stop_usd == pytest.approx(float(row["risk_usd"]), rel=1e-6)
