"""1d and 1w as engine timeframes for exploration arms (WS-4, consolidated plan 2026-11-07; engine/long_bars.py).

The plan's rules: "1d closes at 21:00 UTC. '1w' added to the Timeframe literal and TF_SECONDS. Warm-up from the long
daily history via asof on available_utc. The 1w decision runs as a fresh decision on the first pass after the week
opens, with the bracket from the send-time price and full RiskGate. A Fri 21:00 decision is refused as 'weekend' and is
never queued." and "crosses_friday, _weekend_rule, the rollover/weekend gate reasons and every cap must be
byte-unchanged". It ships dark (settings engine.timeframes_live); a missing history file is a quiet book and a
`history_gap:<tf>` journal row, never a crash."""
from __future__ import annotations

import hashlib
import inspect
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.config import INTRADAY_LIVE, TF_SECONDS, EngineSettings, load_settings
from goldbot.data.resample import ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine import exploration, long_bars
from goldbot.engine.exploration import Pending
from goldbot.research import arm_rules
from goldbot.research.arms import Arm as SpecArm

TUE_21 = pd.Timestamp("2025-02-11 21:00", tz="UTC")
FRI_21 = pd.Timestamp("2025-02-14 21:00", tz="UTC")
SUN_OPEN_CLOSE = pd.Timestamp("2025-02-16 23:15", tz="UTC")     # the first 15m close after the synthetic 23:02 open
HOLD = {"stop_usd": 10.0, "target_r": 2.0, "hold_bars": 2}


# ------------------------------------------------------------------------------------------------ fixtures
@pytest.fixture(scope="module")
def b1() -> pd.DataFrame:
    """Six weeks of synthetic minutes (the engine's window), through Sunday 2025-02-16's open."""
    return ticks_to_1m(synthetic_ticks("2025-01-06", "2025-02-17", ticks_per_minute=1, seed=3)).reset_index(drop=True)


def _history_frame(first: str, last_close: pd.Timestamp, seed: int = 1) -> pd.DataFrame:
    """A long-history daily file in the schema of raw/daily/bars/<symbol>_1d.parquet: weekdays, 21:00 UTC roll."""
    closes = pd.bdate_range(first, last_close.tz_convert(None).normalize()).tz_localize("UTC") + pd.Timedelta(hours=21)
    rng = np.random.default_rng(seed)
    c = 2000.0 + np.cumsum(rng.normal(0, 10, len(closes)))
    o = np.r_[c[0], c[:-1]]
    hi, lo = np.maximum(o, c) + 5.0, np.minimum(o, c) - 5.0
    prev = pd.DatetimeIndex(np.r_[closes[:1] - pd.Timedelta(days=1), closes[:-1]])
    f = pd.DataFrame({"date": closes.tz_localize(None).normalize(), "ts_utc": prev, "visible_at": closes})
    for side, d in (("bid", -0.1), ("ask", 0.1)):
        f[f"{side}_open"], f[f"{side}_high"], f[f"{side}_low"], f[f"{side}_close"] = o + d, hi + d, lo + d, c + d
    f["open"], f["high"], f["low"], f["close"] = o, hi, lo, c
    f["spread_close"] = f["spread_mean"] = 0.2
    f["n_hours"], f["volume"], f["source"] = 23, 1.0, "test"
    return f


@pytest.fixture()
def hist_dir(tmp_path: Path) -> Path:
    """History from 2023 reaching into the engine's window (to 2025-01-10, a Friday)."""
    (tmp_path / "bars").mkdir()
    _history_frame("2023-01-02", pd.Timestamp("2025-01-10 21:00", tz="UTC")).to_parquet(
        tmp_path / "bars" / "xauusd_1d.parquet")
    return tmp_path


def _arm(arm_id: str, tf: str) -> SpecArm:
    return SpecArm(id=arm_id, rule="random_bracket", timeframe=tf, params=dict(HOLD), role="control")  # type: ignore[arg-type]


def _engine(b1: pd.DataFrame, arms: list[SpecArm], close: pd.Timestamp, hist: Path,
            live: tuple[str, ...] = ("1d", "1w")) -> SimpleNamespace:
    """What `exploration.step` reads at a close (as tests/test_exploration_live_parity.py), with the 1d/1w settings."""
    xa = {a.id: exploration.Arm(spec=a, style="swing", control_share=0.1) for a in arms}
    ex = SimpleNamespace(arms=xa, retired=frozenset(), last_bar={}, queue=deque(), ahead_seen=None, deferred={},
                         needs_context=frozenset(), held_back={}, netting=None, long=None)
    cfg = SimpleNamespace(live_clock=False, account_id="t", release_symbol="XAUUSD", decision_tf="15m",
                          timeframes_live=(*INTRADAY_LIVE, *live), long_history_dir=str(hist))
    return SimpleNamespace(exploration=ex, cfg=cfg, bars_1m=b1[b1["visible_at"] <= close].reset_index(drop=True),
                           decisions=[])


@pytest.fixture()
def seen(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The rule always proposes a long on the bar that just closed and records what it read; the standing common filter
    (rollover 20:45-22:15 UTC, Friday from 19:00 UTC) is lifted here: these tests are about the engine's timing (the
    filter's effect on 1d/1w is pinned on its own below). Skipped candidates are journalled without the ledger."""
    rows: list[dict[str, Any]] = []

    def gen(arm: SpecArm, ctx: arm_rules.RuleContext) -> pd.DataFrame:
        rows.append({"arm": arm.id, "n": len(ctx.bars), "last": ctx.close_ts[-1], "bars": ctx.bars})
        return pd.DataFrame({"idx": [len(ctx.bars) - 1], "side": [1], "risk_usd": [10.0], "target_r": [2.0],
                             "max_bars": [1]})
    monkeypatch.setitem(arm_rules.RULE_FUNCS, "random_bracket", gen)
    monkeypatch.setattr(arm_rules, "common_filter_ok", lambda c, lo, hi: np.ones(len(c), dtype=bool))
    monkeypatch.setattr(exploration, "shares", lambda ex, now: {})
    monkeypatch.setattr(exploration, "candidate", lambda *a, **k: None)
    return rows


def _step(eng: SimpleNamespace, close: pd.Timestamp) -> list[Pending]:
    assert exploration.step(eng, close)  # type: ignore[arg-type]
    return list(eng.exploration.queue)


def _actions(eng: Any, prefix: str) -> list[dict[str, Any]]:
    return [d for d in eng.decisions if str(d.get("action", "")).startswith(prefix)]


# ------------------------------------------------------------------------------------------------ the timeframes
def test_1w_is_an_engine_and_an_arm_timeframe() -> None:
    assert TF_SECONDS["1d"] == 86_400 and TF_SECONDS["1w"] == 7 * 86_400
    assert _arm("W", "1w").timeframe == "1w"


def test_a_1d_bar_closes_only_at_21_utc_on_a_weekday() -> None:
    assert long_bars.is_bar_close("1d", TUE_21) and long_bars.is_bar_close("1d", FRI_21)
    for t in (TUE_21 - pd.Timedelta(minutes=15), TUE_21 + pd.Timedelta(minutes=15), TUE_21 + pd.Timedelta(hours=3),
              pd.Timestamp("2025-02-15 21:00", tz="UTC"), pd.Timestamp("2025-02-16 21:00", tz="UTC")):
        assert not long_bars.is_bar_close("1d", t), t


def test_a_1w_bar_closes_only_at_friday_21_utc() -> None:
    assert long_bars.is_bar_close("1w", FRI_21)
    assert not long_bars.is_bar_close("1w", TUE_21) and not long_bars.is_bar_close("1w", SUN_OPEN_CLOSE)


@pytest.mark.parametrize("tf", ["1m", "5m", "15m", "30m", "1h", "4h"])
def test_an_intraday_close_is_the_clock_boundary_as_before(tf: str) -> None:
    for t in pd.date_range("2025-02-10", periods=500, freq="7min", tz="UTC"):
        assert long_bars.is_bar_close(tf, t) == (int(t.timestamp()) % TF_SECONDS[tf] == 0)


def test_settings_and_engine_ship_1d_and_1w_dark() -> None:
    from goldbot.engine import EngineConfig
    s = load_settings()
    assert "1d" not in s.engine.timeframes_live and "1w" not in s.engine.timeframes_live
    assert set(s.engine.timeframes_live) == set(INTRADAY_LIVE) == set(EngineSettings().timeframes_live)
    cfg = EngineConfig(account_id="a", broker_name="icm")
    assert set(cfg.timeframes_live) == set(INTRADAY_LIVE) and "1d" not in cfg.timeframes_live


def test_the_engine_service_passes_the_settings_to_the_engine() -> None:
    src = Path(__file__).parents[1].joinpath("goldbot/ops/services/engine.py").read_text()
    assert "timeframes_live=tuple(settings.engine.timeframes_live)" in src
    assert "long_history_dir=settings.engine.long_history_dir" in src


def test_every_spec_1d_arm_is_retired_or_blocked_so_the_21_utc_roll_moves_no_running_arm() -> None:
    """The 1d bar moves from the COMEX feature-day to the long history's 21:00 UTC roll; no arm running today reads it."""
    from goldbot.research.arms import load_arms
    rows = [a for a in load_arms(exploration.SPEC_PATH, check_hash=False) if a.timeframe in ("1d", "1w")]
    assert rows and all(a.status != "run" or a.retired_day for a in rows), [a.id for a in rows]


# ------------------------------------------------------------------------------------------------ dark by default
def test_a_dark_1d_arm_is_never_evaluated(b1, hist_dir, seen) -> None:
    eng = _engine(b1, [_arm("D1", "1d")], TUE_21, hist_dir, live=())
    assert _step(eng, TUE_21) == [] and seen == []
    assert not _actions(eng, "history_gap")


def test_a_dark_arm_is_held_retired_from_the_start_and_journalled(tmp_path, monkeypatch) -> None:
    from tests.test_exploration_engine import _engine as real_engine
    from tests.test_exploration_engine import qualified
    arm = qualified(exploration.Arm(spec=_arm("D1", "1d"), style="swing", control_share=0.1))
    eng, _ = real_engine(tmp_path, [arm])
    assert eng.exploration is not None and "D1" in eng.exploration.retired
    [row] = _actions(eng, "exploration_arm_dark")
    assert row["arm"] == "D1" and row["tf"] == "1d"


# ------------------------------------------------------------------------------------------------ 1d decisions
def test_1d_decides_at_the_21_utc_close_on_the_warm_up_and_the_engines_bars(b1, hist_dir, seen) -> None:
    eng = _engine(b1, [_arm("D1", "1d")], TUE_21, hist_dir)
    [p] = _step(eng, TUE_21)
    assert p.arm_id == "D1" and p.bar_ts == TUE_21 and p.stop_usd == 10.0
    [r] = seen
    assert r["n"] >= long_bars.MIN_ROWS["1d"] and r["last"] == TUE_21
    vis = pd.DatetimeIndex(r["bars"]["visible_at"])
    assert vis.is_monotonic_increasing and vis.is_unique and (vis.hour == 21).all() and (vis.dayofweek < 5).all()


def test_1d_does_not_decide_off_its_close(b1, hist_dir, seen) -> None:
    for t in (TUE_21 - pd.Timedelta(minutes=15), TUE_21 + pd.Timedelta(minutes=15)):
        eng = _engine(b1, [_arm("D1", "1d")], t, hist_dir)
        assert _step(eng, t) == [], t
    assert seen == []


def test_a_friday_21_decision_is_refused_weekend_and_never_queued(b1, hist_dir, seen) -> None:
    eng = _engine(b1, [_arm("D1", "1d"), _arm("W1", "1w")], FRI_21, hist_dir)
    assert _step(eng, FRI_21) == []
    [row] = _actions(eng, "exploration_skipped:")
    assert row["action"] == "exploration_skipped:weekend" and row["arm"] == "D1"


def test_the_engines_window_bars_win_over_the_history_where_both_have_a_day(b1, hist_dir, seen) -> None:
    eng = _engine(b1, [_arm("D1", "1d")], TUE_21, hist_dir)
    _step(eng, TUE_21)
    bars = seen[0]["bars"].set_index("visible_at")
    day = pd.Timestamp("2025-01-14 21:00", tz="UTC")                   # inside the window: built from its minutes
    m = b1[(b1["ts_utc"] >= day - pd.Timedelta(days=1)) & (b1["ts_utc"] < day)]
    assert bars.loc[day, "bid_close"] == pytest.approx((m["bid_close"].iloc[-1] + m["ask_close"].iloc[-1]) / 2)


# ------------------------------------------------------------------------------------------------ 1w decisions
def test_1w_is_not_decided_on_friday_but_fresh_at_the_first_close_after_the_weekly_open(b1, hist_dir, seen) -> None:
    eng = _engine(b1, [_arm("W1", "1w")], FRI_21, hist_dir)
    assert _step(eng, FRI_21) == [] and seen == []
    eng = _engine(b1, [_arm("W1", "1w")], SUN_OPEN_CLOSE, hist_dir)
    [p] = _step(eng, SUN_OPEN_CLOSE)
    assert p.arm_id == "W1" and p.bar_ts == SUN_OPEN_CLOSE          # the send-time decision, not Friday's close
    [r] = seen
    assert r["n"] >= long_bars.MIN_ROWS["1w"] and r["last"] == FRI_21
    later = SUN_OPEN_CLOSE + pd.Timedelta(minutes=15)
    eng = _engine(b1, [_arm("W1", "1w")], later, hist_dir)
    assert _step(eng, later) == []                                   # once a week: only the first close after the open


def test_weekly_bars_are_the_iso_weeks_of_the_daily_bars_known_at_their_last_close(hist_dir) -> None:
    d = long_bars.read_history(hist_dir, "XAUUSD")
    assert d is not None
    w = long_bars.weekly(d)
    assert (pd.DatetimeIndex(w["visible_at"]).dayofweek == 4).mean() > 0.95
    last = w.iloc[-1]
    days = d[pd.DatetimeIndex(d["visible_at"]) > pd.DatetimeIndex(w["visible_at"])[-2]]
    assert last["bid_high"] == days["bid_high"].max() and last["bid_close"] == days["bid_close"].iloc[-1]


# ------------------------------------------------------------------------------------------------ warm-up, fail closed
def test_the_warm_up_is_point_in_time_on_available_utc(tmp_path) -> None:
    """A history row is read only once it is available: the long file's `available_utc`, the bars file's
    `visible_at`."""
    (tmp_path / "long").mkdir()
    f = pd.DataFrame({"date": pd.to_datetime(["2025-01-06", "2025-01-07", "2025-01-08"]),
                      "open": [1.0, 2.0, 3.0], "high": [1.5, 2.5, 3.5], "low": [0.5, 1.5, 2.5], "close": [1.2, 2.2, 3.2],
                      "source": "t", "available_utc": pd.to_datetime(["2025-01-06 21:00", "2025-01-07 21:00",
                                                                      "2025-01-09 09:00"], utc=True)})
    f.to_parquet(tmp_path / "long" / "xagusd_daily_long.parquet")
    d = long_bars.read_history(tmp_path, "XAGUSD")
    assert d is not None
    at = long_bars.as_of(d, pd.Timestamp("2025-01-08 21:00", tz="UTC"))
    assert list(at["bid_close"]) == [1.2, 2.2]                       # the third is not available until 01-09 09:00
    assert (at["bid_close"] == at["ask_close"]).all()                # a mid-only history: bid = ask = mid


def test_a_missing_history_file_keeps_the_book_quiet_and_journals_history_gap_once(b1, tmp_path, seen) -> None:
    eng = _engine(b1, [_arm("D1", "1d")], TUE_21, tmp_path / "nowhere")
    assert _step(eng, TUE_21) == [] and seen == []
    nxt = TUE_21 + pd.Timedelta(days=1)
    eng.bars_1m = b1[b1["visible_at"] <= nxt].reset_index(drop=True)
    assert _step(eng, nxt) == []
    [row] = _actions(eng, "history_gap")
    assert row["action"] == "history_gap:1d" and row["reason"] == long_bars.MISSING_FILE and row["arm"] == "D1"


def test_a_history_that_does_not_reach_the_engines_window_is_a_gap(b1, tmp_path, seen) -> None:
    (tmp_path / "bars").mkdir()
    _history_frame("2023-01-02", pd.Timestamp("2024-11-29 21:00", tz="UTC")).to_parquet(
        tmp_path / "bars" / "xauusd_1d.parquet")
    eng = _engine(b1, [_arm("D1", "1d")], TUE_21, tmp_path)
    assert _step(eng, TUE_21) == [] and seen == []
    [row] = _actions(eng, "history_gap")
    assert row["reason"] == long_bars.GAP


def test_a_history_too_short_for_the_warm_up_is_a_gap(b1, tmp_path, seen) -> None:
    (tmp_path / "bars").mkdir()
    _history_frame("2024-11-04", pd.Timestamp("2025-01-10 21:00", tz="UTC")).to_parquet(
        tmp_path / "bars" / "xauusd_1d.parquet")
    eng = _engine(b1, [_arm("D1", "1d")], TUE_21, tmp_path)
    assert _step(eng, TUE_21) == [] and seen == []
    [row] = _actions(eng, "history_gap")
    assert row["reason"] == long_bars.SHORT


def test_an_unreadable_history_file_is_a_gap_never_a_crash(b1, tmp_path, seen) -> None:
    (tmp_path / "bars").mkdir()
    (tmp_path / "bars" / "xauusd_1d.parquet").write_bytes(b"not parquet")
    eng = _engine(b1, [_arm("D1", "1d")], TUE_21, tmp_path)
    assert _step(eng, TUE_21) == []
    [row] = _actions(eng, "history_gap")
    assert row["reason"].startswith("error:")


# ------------------------------------------------------------------------------------------------ the entry side
def test_a_long_timeframe_pending_older_than_one_base_bar_is_dropped_stale(tmp_path, monkeypatch) -> None:
    from tests.test_exploration_engine import CLOSE, _at, qualified
    from tests.test_exploration_engine import _engine as real_engine
    arm = qualified(exploration.Arm(spec=_arm("D1", "1d"), style="swing", control_share=0.1))
    eng, pb = real_engine(tmp_path, [arm])
    ex = eng.exploration
    assert ex is not None
    ex.retired = frozenset()
    tick = _at(eng, pb, CLOSE + pd.Timedelta(minutes=15), lag_s=1.0)
    ex.queue.append(Pending(arm_id="D1", side=1, bar_ts=CLOSE, stop_usd=10.0, target_usd=20.0, hold_bars=2))
    exploration._enter(eng, ex, tick.ts_utc, tick)
    assert [d["action"] for d in _actions(eng, "exploration_stale")] == ["exploration_stale"]
    assert not ex.queue and not pb.positions()


def test_an_oversized_gold_1d_stop_is_refused_min_lot_exceeds_risk_and_journalled(tmp_path, monkeypatch) -> None:
    """Lot-fit is the RiskGate's, unchanged: a daily stop the minimum lot cannot carry inside the per-trade cap is
    refused and journalled, never resized."""
    from tests.test_exploration_engine import CLOSE, _at, qualified
    from tests.test_exploration_engine import _engine as real_engine
    arm = qualified(exploration.Arm(spec=_arm("D1", "1d"), style="swing", control_share=0.1))
    pots = {"swing": exploration.Pot(gbp=5_000, max_open=2)}
    eng, pb = real_engine(tmp_path, [arm], caps={"D1": 5_000.0}, pots=pots)
    ex = eng.exploration
    assert ex is not None
    ex.retired = frozenset()
    tick = _at(eng, pb, CLOSE)
    ex.queue.append(Pending(arm_id="D1", side=1, bar_ts=CLOSE, stop_usd=150.0, target_usd=300.0, hold_bars=1))
    exploration._enter(eng, ex, tick.ts_utc, tick)
    rows = _actions(eng, "exploration_refused:")
    assert [r["action"] for r in rows] == ["exploration_refused:min_lot_exceeds_risk"], eng.decisions
    assert rows[0]["arm"] == "D1" and rows[0]["tf"] == "1d" and not pb.positions()


def test_demo_weekend_flat_still_closes_a_1w_position_at_the_cut(tmp_path) -> None:
    """A winner held by a 1w arm (agent `explore:W1`, a hold of weeks) is closed at the Friday cut like any position:
    the cut (`weekend_lane.weekday_rule`, byte-unchanged) reads no timeframe."""
    from goldbot.execution.broker import Tick
    from tests.test_invalid_feed import CUT, _open_ids, _winner_before_the_cut
    eng, pb, host, pid = _winner_before_the_cut(tmp_path)
    eng.cfg = eng.cfg.model_copy(update={"demo_weekend_flat": True})
    eng.open[pid] = eng.open[pid].model_copy(update={"agent_id": "explore:W1", "max_bars": 4})
    host[0] = CUT + pd.Timedelta(seconds=2)
    pb.on_tick(Tick(ts_utc=host[0], bid=2410.0, ask=2410.3))
    eng._refresh_account(pb.last_tick("XAUUSD"))
    assert pid not in _open_ids(pb) and eng.state.weekend


# ------------------------------------------------------------------------------------------------ unchanged code
WEEKEND_CODE = {   # sha256[:16] of each function's source at cb3e52a4 (before WS-4); a change needs trading-safety review
    "exploration.crosses_friday": "6de740bf56ef935c",
    "exploration.early_close_windows": "24afeefbf6290683",
    "exploration.weekend_backstop": "841112789617f544",
    "exploration._week_due": "dc9a6c24b9e960a9",
    "weekend_lane.weekday_rule": "66e43514f0f448cb",
    "weekend_lane.crosses_cut": "d5cf379596ca5faf",
    "runner.Engine._weekend_rule": "718f9f9245de4de9",
    "arm_rules.common_filter_ok": "a78da98b8a7f6aa9",
    "exploration.stale_s": "082bc886e2a95a87",
}


def test_the_weekend_rollover_and_stale_code_is_byte_unchanged() -> None:
    from goldbot.engine import runner, weekend_lane
    from goldbot.research import arm_rules as rules
    objs: dict[str, Any] = {"exploration.crosses_friday": exploration.crosses_friday,
            "exploration.early_close_windows": exploration.early_close_windows,
            "exploration.weekend_backstop": exploration.weekend_backstop,
            "exploration._week_due": exploration._week_due,
            "weekend_lane.weekday_rule": weekend_lane.weekday_rule, "weekend_lane.crosses_cut": weekend_lane.crosses_cut,
            "runner.Engine._weekend_rule": runner.Engine._weekend_rule,
            "arm_rules.common_filter_ok": rules.common_filter_ok, "exploration.stale_s": exploration.stale_s}
    got = {k: hashlib.sha256(inspect.getsource(o).encode()).hexdigest()[:16] for k, o in objs.items()}
    assert got == WEEKEND_CODE


def test_the_standing_common_filter_drops_a_1d_candidate_at_the_21_utc_close(b1, hist_dir, monkeypatch) -> None:
    """Pinned for the go-live decision (WS-4 report): the rules' standing common filter (rollover 20:45-22:15 UTC,
    Friday from 19:00 UTC; byte-unchanged) judges a candidate at its bar's close, so every ARM-SPEC rule's 1d candidate
    at 21:00 UTC and 1w candidate (Friday 21:00 close) is dropped before the engine sees it."""
    def gen(arm: SpecArm, ctx: arm_rules.RuleContext) -> pd.DataFrame:
        return pd.DataFrame({"idx": [len(ctx.bars) - 1], "side": [1], "risk_usd": [10.0], "target_r": [2.0],
                             "max_bars": [1]})
    monkeypatch.setitem(arm_rules.RULE_FUNCS, "random_bracket", gen)
    eng = _engine(b1, [_arm("D1", "1d")], TUE_21, hist_dir)
    assert _step(eng, TUE_21) == []
    eng = _engine(b1, [_arm("W1", "1w")], SUN_OPEN_CLOSE, hist_dir)
    assert _step(eng, SUN_OPEN_CLOSE) == []
