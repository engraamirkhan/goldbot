"""BACKLOG 144: the 1d/1w go-live blockers of WS-4 (engine/long_bars.py, ADR 0081).

The blockers, as the backlog names them: "1. Send 1d entries after 22:15 UTC once the broker has reopened, the bracket
from the send price; tests in EET and EEST. 2. Move the 1w decision out of the first 15-60 minutes after the Sunday
open. 3. The 1w missed-week catch-up (a restart across the weekly open), persisted. 5. The rules' standing common filter
at the 21:00 UTC close drops every 1d/1w candidate (quant decision). 6. `_trail_runners` and `score_twins` on
`long_bars.is_bar_close`." The brief adds: the swap of a multi-day hold projected through the RiskGate, unknown swap
refused; weekends and holidays handled explicitly. 1d/1w stay dark (settings) until blocker 7 lifts the guard."""
from __future__ import annotations

from collections import deque
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.config import INTRADAY_LIVE
from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.data.resample import ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine import exploration, long_bars
from goldbot.engine.exploration import Pending
from goldbot.research import arm_rules
from goldbot.research.arms import Arm as SpecArm
from tests.test_engine_long_tf import _arm, _history_frame

M15 = pd.Timedelta(minutes=15)
# EET (winter, UTC+2): the daily break is 21:59-23:02 UTC, the week opens Sunday 23:02 UTC
EET_TUE = pd.Timestamp("2025-02-11 21:00", tz="UTC")
EET_FRI = pd.Timestamp("2025-02-14 21:00", tz="UTC")
# EEST (summer, UTC+3): the daily break is 20:59-22:02 UTC, the week opens Sunday 22:02 UTC
EEST_TUE = pd.Timestamp("2025-06-10 21:00", tz="UTC")
EEST_FRI = pd.Timestamp("2025-06-13 21:00", tz="UTC")


@pytest.fixture(scope="module")
def eet() -> pd.DataFrame:
    return ticks_to_1m(synthetic_ticks("2025-01-06", "2025-02-20", ticks_per_minute=1, seed=3)).reset_index(drop=True)


@pytest.fixture(scope="module")
def eest() -> pd.DataFrame:
    return ticks_to_1m(synthetic_ticks("2025-05-05", "2025-06-20", ticks_per_minute=1, seed=4)).reset_index(drop=True)


@pytest.fixture()
def hist(tmp_path: Path) -> Path:
    """History from 2023 reaching into the winter window (to 2025-01-10, a Friday)."""
    (tmp_path / "bars").mkdir()
    _history_frame("2023-01-02", pd.Timestamp("2025-01-10 21:00", tz="UTC")).to_parquet(
        tmp_path / "bars" / "xauusd_1d.parquet")
    return tmp_path


@pytest.fixture()
def hist_summer(tmp_path: Path) -> Path:
    root = tmp_path / "summer"
    (root / "bars").mkdir(parents=True)
    _history_frame("2023-01-02", pd.Timestamp("2025-05-09 21:00", tz="UTC")).to_parquet(
        root / "bars" / "xauusd_1d.parquet")
    return root


def _engine(b1: pd.DataFrame, arms: list[SpecArm], hist: Path, db: Any) -> SimpleNamespace:
    """What `exploration.step` reads at a close, with the engine's session table and the aux ledger."""
    xa = {a.id: exploration.Arm(spec=a, style="swing", control_share=0.1) for a in arms}
    ex = SimpleNamespace(arms=xa, retired=frozenset(), last_bar={}, queue=deque(), ahead_seen=None, deferred={},
                         needs_context=frozenset(), held_back={}, netting=None, long=None, db=db)
    cfg = SimpleNamespace(live_clock=False, account_id="t", release_symbol="XAUUSD", decision_tf="15m",
                          timeframes_live=(*INTRADAY_LIVE, "1d", "1w"), long_history_dir=str(hist))
    return SimpleNamespace(exploration=ex, cfg=cfg, b1=b1, bars_1m=b1.iloc[:0], decisions=[],
                           sessions=DEFAULT_SESSIONS)


def _run(eng: SimpleNamespace, start: pd.Timestamp, end: pd.Timestamp) -> list[Pending]:
    """Every 15m close in [start, end], the engine's minutes up to each: the signals queued, in order."""
    got: list[Pending] = []
    for close in pd.date_range(start, end, freq="15min"):
        eng.bars_1m = eng.b1[eng.b1["visible_at"] <= close].reset_index(drop=True)
        before = len(eng.exploration.queue)
        assert exploration.step(eng, close)  # type: ignore[arg-type]
        got += list(eng.exploration.queue)[before:]
    return got


def _actions(eng: Any, prefix: str) -> list[dict[str, Any]]:
    return [d for d in eng.decisions if str(d.get("action", "")).startswith(prefix)]


@pytest.fixture()
def db(tmp_path: Path) -> Any:
    return exploration.ledger_db(str(tmp_path / "state"))


@pytest.fixture()
def rule(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The rule proposes a long on the bar that just closed and records what it read. The standing common filter is
    NOT lifted: blocker 5 is that it judges a 1d/1w candidate at its entry close (ADR 0081)."""
    rows: list[dict[str, Any]] = []

    def gen(arm: SpecArm, ctx: arm_rules.RuleContext) -> pd.DataFrame:
        rows.append({"arm": arm.id, "last": ctx.close_ts[-1], "filter": ctx.filter_ts[-1],
                     "b1_last": pd.Timestamp(ctx.b1["visible_at"].iloc[-1])})
        return pd.DataFrame({"idx": [len(ctx.bars) - 1], "side": [1], "risk_usd": [10.0], "target_r": [2.0],
                             "max_bars": [1]})
    monkeypatch.setitem(arm_rules.RULE_FUNCS, "random_bracket", gen)
    monkeypatch.setattr(exploration, "shares", lambda ex, now: {})
    monkeypatch.setattr(exploration, "candidate", lambda *a, **k: None)
    return rows


# ------------------------------------------------------------------------------------------------ 1: the 1d send
def test_a_1d_decision_in_eet_is_sent_at_the_first_close_15_minutes_after_the_2302_utc_reopen(eet, hist, db,
                                                                                             rule) -> None:
    eng = _engine(eet, [_arm("D1", "1d")], hist, db)
    [p] = _run(eng, EET_TUE, EET_TUE + pd.Timedelta(hours=3))
    assert p.bar_ts == pd.Timestamp("2025-02-11 23:30", tz="UTC")       # after 23:02 + 15 min, not 22:30
    assert p.decision_utc == EET_TUE and p.stop_usd == 10.0
    [r] = rule
    assert r["last"] == EET_TUE and r["b1_last"] <= EET_TUE             # decided on the bars through 21:00 only


def test_a_1d_decision_in_eest_is_sent_at_the_first_close_after_2215_utc(eest, hist_summer, db, rule) -> None:
    eng = _engine(eest, [_arm("D1", "1d")], hist_summer, db)
    [p] = _run(eng, EEST_TUE, EEST_TUE + pd.Timedelta(hours=3))
    assert p.bar_ts == pd.Timestamp("2025-06-10 22:30", tz="UTC") and p.decision_utc == EEST_TUE


@pytest.mark.parametrize(("b1", "d", "send"), [
    ("eet", EET_TUE, pd.Timestamp("2025-02-11 23:30", tz="UTC")),
    ("eest", EEST_TUE, pd.Timestamp("2025-06-10 22:30", tz="UTC")),
])
def test_bars_after_the_decision_close_never_change_the_decision_sent_later(b1, d, send, request, hist,
                                                                           hist_summer, tmp_path,
                                                                           monkeypatch) -> None:
    """Quant review of ADR 0081 (1), truncation: the decision sent at the entry close reads only what was known at
    its 21:00 UTC decision close; minutes between the two, however different, change neither its inputs nor its
    signal."""
    seen: list[tuple[pd.DataFrame, pd.DataFrame]] = []

    def gen(arm: SpecArm, ctx: arm_rules.RuleContext) -> pd.DataFrame:
        seen.append((ctx.bars.copy(), ctx.b1.copy()))
        m = ctx.mid
        side = 1 if m["close"].iloc[-1] >= m["close"].iloc[-5] else -1
        return pd.DataFrame({"idx": [len(ctx.bars) - 1], "side": [side],
                             "risk_usd": [float(m["high"].iloc[-1] - m["low"].iloc[-1])], "target_r": [2.0],
                             "max_bars": [1]})
    monkeypatch.setitem(arm_rules.RULE_FUNCS, "random_bracket", gen)
    monkeypatch.setattr(exploration, "shares", lambda ex, now: {})
    monkeypatch.setattr(exploration, "candidate", lambda *a, **k: None)
    frame = request.getfixturevalue(b1)
    root = hist if b1 == "eet" else hist_summer
    after = (frame["ts_utc"] >= d) & (frame["ts_utc"] < send)
    assert after.any()
    moved = frame.copy()
    for c in [c for c in frame.columns if c.startswith(("bid_", "ask_"))]:
        moved.loc[after, c] = moved.loc[after, c] * 1.05                    # a 5% move after the decision close
    got = []
    for i, f in enumerate((frame, moved)):
        eng = _engine(f, [_arm("D1", "1d")], root, exploration.ledger_db(str(tmp_path / f"s{i}")))
        [p] = _run(eng, d, send)
        got.append(p)
    assert got[0] == got[1] and got[0].bar_ts == send
    (bars0, b10), (bars1, b11) = seen
    pd.testing.assert_frame_equal(bars0, bars1)
    pd.testing.assert_frame_equal(b10, b11)


def test_the_1d_send_close_is_never_inside_the_rollover_or_the_daily_break() -> None:
    for d, reopen in ((EET_TUE, pd.Timestamp("2025-02-11 23:02", tz="UTC")),
                      (EEST_TUE, pd.Timestamp("2025-06-10 22:02", tz="UTC"))):
        assert long_bars.reopen_after(DEFAULT_SESSIONS, d) == reopen
        e = long_bars.entry_close("1d", d, reopen, 900)
        assert e > d.normalize() + pd.Timedelta(hours=22, minutes=15) and e >= reopen + long_bars.SETTLE["1d"]


def test_a_1d_signal_is_stamped_at_its_send_close_so_its_bracket_is_priced_from_the_send_price(eet, hist, db,
                                                                                               rule) -> None:
    """The Pending carries distances only (stop_usd, target_usd); `_send` puts them on the price of the pass that
    sends it, and the signal is fresh there (its `bar_ts` is the send close, its decision close in `decision_utc`)."""
    eng = _engine(eet, [_arm("D1", "1d")], hist, db)
    [p] = _run(eng, EET_TUE, EET_TUE + pd.Timedelta(hours=3))
    assert (p.target_usd, p.stop_usd) == (20.0, 10.0)
    assert (p.bar_ts - p.decision_utc) > pd.Timedelta(hours=2)          # type: ignore[operator]


# ------------------------------------------------------------------------------------------------ 2: the 1w decision
@pytest.mark.parametrize(("b1", "fri", "first"), [
    ("eet", EET_FRI, pd.Timestamp("2025-02-17 00:15", tz="UTC")),       # opens Sunday 23:02 UTC
    ("eest", EEST_FRI, pd.Timestamp("2025-06-15 23:15", tz="UTC")),     # opens Sunday 22:02 UTC
])
def test_the_1w_decision_waits_an_hour_after_the_sunday_open(b1, fri, first, request, hist, hist_summer, db,
                                                            rule) -> None:
    eng = _engine(request.getfixturevalue(b1), [_arm("W1", "1w")], hist if b1 == "eet" else hist_summer, db)
    [p] = _run(eng, fri, fri + pd.Timedelta(days=3))
    assert p.bar_ts == first and p.decision_utc == fri
    assert rule[0]["last"] == fri                                        # the weekly bar closed Friday 21:00


# ------------------------------------------------------------------------------------------------ 3: catch-up
def test_a_restart_across_the_weekly_open_catches_the_week_up_once_and_persists_it(eet, hist, db, rule) -> None:
    monday = pd.Timestamp("2025-02-17 03:00", tz="UTC")
    eng = _engine(eet, [_arm("W1", "1w")], hist, db)                     # the engine was down over the weekly open
    [p] = _run(eng, monday, monday)
    assert p.bar_ts == monday and p.decision_utc == EET_FRI
    again = _engine(eet, [_arm("W1", "1w")], hist, db)                  # restarted again: the week is decided
    assert _run(again, monday + M15, monday + pd.Timedelta(hours=2)) == []


def test_a_week_more_than_a_day_late_is_given_up_journalled_once_and_persisted(eet, hist, db, rule) -> None:
    tuesday = pd.Timestamp("2025-02-18 01:00", tz="UTC")
    eng = _engine(eet, [_arm("W1", "1w")], hist, db)
    assert _run(eng, tuesday, tuesday + M15) == [] and rule == []
    [row] = _actions(eng, long_bars.MISSED_ACTION)
    assert row["action"] == "exploration_missed:1w" and row["reason"] == long_bars.LATE
    again = _engine(eet, [_arm("W1", "1w")], hist, db)
    assert _run(again, tuesday + 2 * M15, tuesday + 3 * M15) == [] and not _actions(again, long_bars.MISSED_ACTION)


def test_a_1d_decision_is_persisted_so_a_restart_never_sends_it_twice(eet, hist, db, rule) -> None:
    eng = _engine(eet, [_arm("D1", "1d")], hist, db)
    assert len(_run(eng, EET_TUE, EET_TUE + pd.Timedelta(hours=2, minutes=30))) == 1
    again = _engine(eet, [_arm("D1", "1d")], hist, db)
    assert _run(again, EET_TUE + pd.Timedelta(hours=2, minutes=45), EET_TUE + pd.Timedelta(hours=4)) == []


def test_an_unreadable_decision_store_decides_nothing(eet, hist, rule) -> None:
    class Broken:
        def connection(self) -> Any:
            raise OSError("disk")
    eng = _engine(eet, [_arm("D1", "1d")], hist, Broken())
    assert _run(eng, EET_TUE, EET_TUE + pd.Timedelta(hours=3)) == []
    assert any(r["reason"].startswith("decisions_unread") for r in _actions(eng, long_bars.GAP_ACTION))


# ------------------------------------------------------------------------------------------------ weekends, holidays
def test_a_broker_that_does_not_reopen_holds_the_1d_decision_then_gives_it_up_journalled(eet, hist, db,
                                                                                       rule) -> None:
    """A holiday: no minute after the nominal reopen. Nothing is sent on the session table's word alone."""
    shut = (eet["ts_utc"] >= EET_TUE + pd.Timedelta(hours=1)) & (eet["ts_utc"] < EET_TUE + pd.Timedelta(hours=8))
    eng = _engine(eet[~shut].reset_index(drop=True), [_arm("D1", "1d")], hist, db)
    assert _run(eng, EET_TUE, EET_TUE + pd.Timedelta(hours=6)) == []
    [row] = _actions(eng, long_bars.MISSED_ACTION)
    assert row["action"] == "exploration_missed:1d" and row["reason"] == long_bars.LATE


def test_a_late_1w_catch_up_waits_for_the_rollover_filter_and_the_reopen(eet, hist, db, rule) -> None:
    """A restart on Monday 21:00 UTC (inside the 20:45-22:15 rollover filter, then the 21:59-23:02 break): the week
    is caught up at the first close after a whole open base bar, never inside either."""
    monday = pd.Timestamp("2025-02-17 21:00", tz="UTC")
    eng = _engine(eet, [_arm("W1", "1w")], hist, db)
    [p] = _run(eng, monday, monday + pd.Timedelta(hours=3))
    assert p.bar_ts == pd.Timestamp("2025-02-17 23:30", tz="UTC")


def test_a_friday_1d_decision_is_still_never_queued(eet, hist, db, rule) -> None:
    eng = _engine(eet, [_arm("D1", "1d")], hist, db)
    assert _run(eng, EET_FRI, EET_FRI + pd.Timedelta(days=3)) == []          # up to Monday 21:00


# ------------------------------------------------------------------------------------------------ 5: common filter
def test_the_common_filter_judges_a_long_candidate_at_its_entry_close_not_the_21_utc_close(eet, hist, db,
                                                                                          rule) -> None:
    eng = _engine(eet, [_arm("D1", "1d"), _arm("W1", "1w")], hist, db)
    got = _run(eng, EET_TUE, EET_TUE + pd.Timedelta(hours=3)) + _run(eng, EET_FRI, EET_FRI + pd.Timedelta(days=3))
    assert [p.arm_id for p in got] == ["D1", "W1"]
    sent = [r for r in rule if not (r["arm"] == "D1" and r["last"].weekday() == 4)]     # Friday's 1d: the weekend
    assert len(sent) == 2 and all(bool(arm_rules.common_filter_ok(
        pd.DatetimeIndex([r["filter"]]), np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64))[0]) for r in sent)


def test_the_nominal_entry_of_a_long_bar_passes_the_filter_except_a_friday_1d() -> None:
    closes = pd.date_range("2025-02-10 21:00", periods=5, freq="D", tz="UTC")       # Monday to Friday
    ok = arm_rules.common_filter_ok(long_bars.entry_after(closes, "1d"), np.zeros(0, dtype=np.int64),
                                    np.zeros(0, dtype=np.int64))
    assert list(ok) == [True, True, True, True, False]
    assert arm_rules.common_filter_ok(long_bars.entry_after(closes[-1:], "1w"), np.zeros(0, dtype=np.int64),
                                      np.zeros(0, dtype=np.int64))[0]


def test_an_intraday_context_filters_at_its_close_as_before() -> None:
    bars = pd.DataFrame({"visible_at": pd.date_range("2025-02-11 20:00", periods=3, freq="15min", tz="UTC")})
    ctx = arm_rules.RuleContext(bars=bars, b1=bars, blackout_lo=np.zeros(0, dtype=np.int64),
                                blackout_hi=np.zeros(0, dtype=np.int64))
    assert ctx.filter_ts.equals(ctx.close_ts)


# ------------------------------------------------------------------------------------------------ 6: is_bar_close
THU_0000 = pd.Timestamp("2025-02-13 00:00", tz="UTC")                   # an epoch-week boundary (1970-01-01 a Thursday)


def test_a_1w_twin_is_scored_at_the_weekly_close_not_at_an_epoch_week_boundary(tmp_path, monkeypatch) -> None:
    from goldbot.engine.exploration import TWINS, TwinRecord
    from goldbot.labels.triple_barrier import SwapSpec
    from goldbot.research import arm_rules_clock_box as K
    assert int(THU_0000.timestamp()) % (7 * 86_400) == 0
    db = exploration.ledger_db(str(tmp_path))
    with db.write() as conn:
        TWINS.insert_if_absent(conn, TwinRecord(
            twin_id="c:fixed", client_order_id="c", account_id="t", broker="icm", arm_id="BXFW", of_arm_id="BXRW",
            exit_mode="fixed", tf="1w", side=1, signal_utc=EET_FRI - pd.Timedelta(days=7), entry_price=2000.0,
            risk_usd=10.0, target_r=2.0, hold_bars=4))
    calls: list[pd.Timestamp] = []
    monkeypatch.setattr(K, "twin_outcome", lambda *a, **k: calls.append(pd.Timestamp("now")))
    monkeypatch.setattr(K, "decision_closes", lambda *a, **k: pd.DatetimeIndex([], tz="UTC"))
    monkeypatch.setattr(K, "mid_path", lambda *a, **k: None)
    eng = SimpleNamespace(cfg=SimpleNamespace(account_id="t", swap_prior=SwapSpec.shipped(), server_tz="Europe/Athens"),
                          _cost_table=lambda: None)
    ex = SimpleNamespace(db=db)
    b1 = pd.DataFrame({"ts_utc": [THU_0000]})
    exploration.score_twins(eng, ex, b1, THU_0000)  # type: ignore[arg-type]
    assert calls == []
    exploration.score_twins(eng, ex, b1, EET_FRI)  # type: ignore[arg-type]
    assert len(calls) == 1


def test_a_1w_runner_is_trailed_at_the_weekly_close_not_at_an_epoch_week_boundary(monkeypatch) -> None:
    arm = exploration.Arm(spec=_arm("W1", "1w"), style="swing", control_share=0.1)
    monkeypatch.setattr(exploration, "_is_runner", lambda a: True)
    reads: list[str] = []
    eng = SimpleNamespace(open={7: SimpleNamespace(agent_id=arm.tag)}, _close_due=set(),
                          _read_positions=lambda why: reads.append(why))
    ex = SimpleNamespace(arms={"W1": arm})
    b1 = pd.DataFrame({"ts_utc": [THU_0000]})
    exploration._trail_runners(eng, ex, b1, THU_0000)  # type: ignore[arg-type]
    assert reads == []
    exploration._trail_runners(eng, ex, b1, EET_FRI)  # type: ignore[arg-type]
    assert len(reads) == 1


# ------------------------------------------------------------------------------------------------ swap of the hold
def test_a_1d_intent_projects_the_swap_of_every_rollover_to_its_last_weekday_close(tmp_path) -> None:
    from goldbot.ops.cost_imputation import account_swap, paid_swap_per_oz
    from tests.test_exploration_engine import CLOSE, _at, qualified
    from tests.test_exploration_engine import _engine as real_engine
    arm = qualified(exploration.Arm(spec=_arm("D1", "1d"), style="swing", control_share=0.1))
    eng, pb = real_engine(tmp_path, [arm])
    tick = _at(eng, pb, CLOSE)                                              # Tuesday 20:00 UTC
    s = Pending(arm_id="D1", side=1, bar_ts=CLOSE, stop_usd=10.0, target_usd=20.0, hold_bars=3)
    terms = eng._sizing_terms(1, tick.ask, 10.0, cycle=CLOSE)
    intent = exploration._intent(eng, arm, 1, tick.ask, s, tick.ts_utc, terms, 40.0)
    assert intent is not None
    end = pd.Timestamp("2025-03-06 21:00", tz="UTC")                        # Tue, Wed, Thu 21:00: the third close
    assert long_bars.hold_end("1d", tick.ts_utc, 3) == end
    spec = account_swap(eng._cost_table(), eng.cfg.swap_prior.model_copy(update={"server_tz": eng.cfg.server_tz}))
    whole = paid_swap_per_oz(spec, 1, tick.ts_utc, end)
    assert intent.swap_usd_per_oz == pytest.approx(whole)
    assert whole > eng._swap_usd_per_oz(1, tick.ts_utc) > 0                # more than the risk day's own rollover


def test_a_1d_hold_across_the_weekend_projects_the_weekend_and_triple_nights() -> None:
    """Quant review of ADR 0081 (3): 1d bars are weekday closes, so a hold of n bars ends at the n-th weekday 21:00
    UTC close, not n calendar days on: a Thursday entry held 5 bars ends the next Thursday, its weekend and
    Wednesday's triple night inside the projection."""
    from goldbot.labels.triple_barrier import SwapSpec
    from goldbot.ops.cost_imputation import paid_swap_per_oz
    thu = pd.Timestamp("2025-02-13 23:30", tz="UTC")
    assert long_bars.hold_end("1d", thu, 3) == pd.Timestamp("2025-02-18 21:00", tz="UTC")        # Fri, Mon, Tue
    assert long_bars.hold_end("1d", thu, 5) == pd.Timestamp("2025-02-20 21:00", tz="UTC")        # ... Wed, Thu
    assert long_bars.hold_end("1w", thu, 2) == pd.Timestamp("2025-02-21 21:00", tz="UTC")        # Fridays
    end = long_bars.hold_end("1d", thu, 5)
    assert end is not None
    spec = SwapSpec.shipped().model_copy(update={"server_tz": "Europe/Athens"})
    weekdays = paid_swap_per_oz(spec, 1, thu, end)
    calendar = paid_swap_per_oz(spec, 1, thu, thu + pd.Timedelta(days=5))
    assert weekdays > calendar > 0


def test_an_intraday_intent_keeps_the_risk_days_swap(tmp_path) -> None:
    from tests.test_exploration_engine import CLOSE, _at, qualified
    from tests.test_exploration_engine import _engine as real_engine
    arm = qualified(exploration.Arm(spec=_arm("H1", "1h"), style="swing", control_share=0.1))
    eng, pb = real_engine(tmp_path, [arm])
    tick = _at(eng, pb, CLOSE)
    s = Pending(arm_id="H1", side=1, bar_ts=CLOSE, stop_usd=10.0, target_usd=20.0, hold_bars=30)
    terms = eng._sizing_terms(1, tick.ask, 10.0, cycle=CLOSE)
    intent = exploration._intent(eng, arm, 1, tick.ask, s, tick.ts_utc, terms, 40.0)
    assert intent is not None and intent.swap_usd_per_oz == eng._swap_usd_per_oz(1, tick.ts_utc)


def test_a_1d_entry_whose_swap_cannot_be_priced_is_refused_by_the_gate(tmp_path, monkeypatch) -> None:
    from goldbot.engine import runner
    from tests.test_exploration_engine import CLOSE, _at, qualified
    from tests.test_exploration_engine import _engine as real_engine
    arm = qualified(exploration.Arm(spec=_arm("D1", "1d"), style="swing", control_share=0.1))
    eng, pb = real_engine(tmp_path, [arm])
    ex = eng.exploration
    assert ex is not None
    ex.retired = frozenset()
    monkeypatch.setattr(runner, "account_swap", lambda table, prior: None)      # swap unknown
    tick = _at(eng, pb, CLOSE)
    ex.queue.append(Pending(arm_id="D1", side=1, bar_ts=CLOSE, stop_usd=10.0, target_usd=20.0, hold_bars=1))
    exploration._enter(eng, ex, tick.ts_utc, tick)
    refused = [d["action"] for d in _actions(eng, "exploration_refused:")]
    assert refused and all("swap_usd_per_oz" in a for a in refused), eng.decisions
    assert not pb.positions()
