"""Wave 3 engine rows: bar close detected by clock with a 1.5 s grace (D10), the approval window from settings (A2) and
the allocator's tier-1 input fed from the engine's calendar blackout (M9)."""
import inspect
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.allocator import Regime, RuleAllocator, tier1_minutes
from goldbot.config import load_settings
from goldbot.data.econ_calendar import COLUMNS
from goldbot.data.store import Store
from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine import ConstantModel, Engine, EngineConfig
from goldbot.engine.runner import BAR_CLOSE_GRACE_S
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops import run
from goldbot.specialists import SPECIALISTS

T0 = pd.Timestamp("2025-03-03 08:00", tz="UTC")      # a Monday, London session
CLOSE = T0 + pd.Timedelta(minutes=15)                 # the first 15m decision bar closes here


class _Clock:
    """The wall clock the engine reads in production (live_clock), set by the test."""
    def __init__(self, now: pd.Timestamp):
        self.now = now

    def __call__(self, tick: Tick) -> pd.Timestamp:
        return self.now


def _engine(tmp_path: Path, **cfg: Any) -> tuple[Engine, list[pd.Timestamp], _Clock]:
    eng = Engine(EngineConfig(account_id="x", broker_name="icm", state_dir=str(tmp_path), live_clock=True, **cfg),
                 PaperBroker(equity=10_000), [], {})
    clock = _Clock(T0)
    eng._now = clock                                  # type: ignore[method-assign]
    closes: list[pd.Timestamp] = []
    real = eng.on_bar_close

    def spy(close_ts: pd.Timestamp, last_tick: Tick) -> list[dict]:
        closes.append(close_ts)
        return real(close_ts, last_tick)

    eng.on_bar_close = spy                            # type: ignore[method-assign]
    return eng, closes, clock


def _tick(ts: pd.Timestamp, px: float = 2000.0) -> Tick:
    return Tick(ts_utc=ts, bid=px, ask=px + 0.2)


def _feed(eng: Engine, clock: _Clock, ts: pd.Timestamp, wall: pd.Timestamp | None = None) -> Tick:
    t = _tick(ts)
    clock.now = wall or ts
    eng.on_tick(t)
    return t


def _fill_first_bar(eng: Engine, clock: _Clock) -> Tick:
    t = _tick(T0)
    for s in range(0, 15 * 60, 30):                   # a tick every 30 s, the last at 08:14:30
        t = _feed(eng, clock, T0 + pd.Timedelta(seconds=s))
    return t


# ------------------------------------------------------------------------------------------------ D10 bar close
def test_quiet_market_bar_closes_by_clock_after_the_grace_with_no_new_tick(tmp_path):
    eng, closes, clock = _engine(tmp_path)
    last = _fill_first_bar(eng, clock)
    # the run loop keeps polling the terminal, which repeats the last quote: nothing new arrives after the close
    for wall_s in (0.0, 1.0, 1.49):
        clock.now = CLOSE + pd.Timedelta(seconds=wall_s)
        eng.on_tick(last)
    assert closes == [], "inside the grace the bar is not finalised yet"
    clock.now = CLOSE + pd.Timedelta(seconds=BAR_CLOSE_GRACE_S)
    eng.on_tick(last)
    assert closes == [CLOSE]
    # the bar is complete through its last minute and nothing at or after the close is in it (no look-ahead)
    minutes = pd.DatetimeIndex(eng.bars_1m["ts_utc"])
    assert minutes.max() == CLOSE - pd.Timedelta(minutes=1) and (minutes < CLOSE).all()
    assert int(eng.bars_1m["tick_count"].sum()) == 30, "a repeated poll of the same quote is not a new tick"


def test_grace_is_exactly_one_and_a_half_seconds():
    assert BAR_CLOSE_GRACE_S == 1.5


def test_a_bar_closed_by_clock_is_not_processed_again_by_the_next_tick(tmp_path):
    eng, closes, clock = _engine(tmp_path)
    last = _fill_first_bar(eng, clock)
    clock.now = CLOSE + pd.Timedelta(seconds=2)
    eng.on_tick(last)
    for wall_s in (3, 10, 60):                        # more polls of the quiet market
        clock.now = CLOSE + pd.Timedelta(seconds=wall_s)
        eng.on_tick(last)
    _feed(eng, clock, CLOSE + pd.Timedelta(seconds=90))       # the next bar's first tick: the close is already done
    assert closes == [CLOSE]
    # the next bar closes once, by its first tick after the close this time
    for s in range(120, 15 * 60, 60):
        _feed(eng, clock, CLOSE + pd.Timedelta(seconds=s))
    _feed(eng, clock, CLOSE + pd.Timedelta(minutes=15, seconds=0.2))
    clock.now = CLOSE + pd.Timedelta(minutes=15, seconds=5)
    eng.on_tick(_tick(CLOSE + pd.Timedelta(minutes=15, seconds=0.2)))
    assert closes == [CLOSE, CLOSE + pd.Timedelta(minutes=15)]


def test_a_bar_closed_by_its_next_tick_is_not_closed_again_by_the_clock(tmp_path):
    eng, closes, clock = _engine(tmp_path)
    _fill_first_bar(eng, clock)
    nxt = _feed(eng, clock, CLOSE + pd.Timedelta(seconds=0.3))   # before the grace: the tick proves the bar complete
    assert closes == [CLOSE]
    for wall_s in (1.5, 2, 30):
        clock.now = CLOSE + pd.Timedelta(seconds=wall_s)
        eng.on_tick(nxt)
    assert closes == [CLOSE]


def test_a_tick_older_than_a_bar_the_clock_finalised_never_revises_it(tmp_path):
    eng, closes, clock = _engine(tmp_path)
    _fill_first_bar(eng, clock)
    _feed(eng, clock, CLOSE - pd.Timedelta(seconds=10), wall=CLOSE + pd.Timedelta(seconds=2))   # wall clock ahead
    assert closes == [CLOSE]
    before = eng.bars_1m.copy()
    _feed(eng, clock, CLOSE - pd.Timedelta(seconds=5), wall=CLOSE + pd.Timedelta(seconds=3))    # arrives late
    _feed(eng, clock, CLOSE + pd.Timedelta(seconds=70))
    eng._rebuild_bars()
    pd.testing.assert_frame_equal(eng.bars_1m.iloc[:len(before)].reset_index(drop=True), before.reset_index(drop=True))
    assert closes == [CLOSE]


def test_a_late_tick_raises_a_rate_limited_late_tick_warning_that_does_not_block_entries(tmp_path):
    eng, closes, clock = _engine(tmp_path)
    _fill_first_bar(eng, clock)
    _feed(eng, clock, CLOSE - pd.Timedelta(seconds=10), wall=CLOSE + pd.Timedelta(seconds=2))   # closes the bar
    assert closes == [CLOSE] and eng._late_ticks == 0
    kept = len(eng.ticks)
    _feed(eng, clock, CLOSE - pd.Timedelta(seconds=5), wall=CLOSE + pd.Timedelta(seconds=3))    # stamped before the close
    _feed(eng, clock, CLOSE - pd.Timedelta(seconds=4), wall=CLOSE + pd.Timedelta(seconds=4))    # another, same minute
    assert len(eng.ticks) == kept, "a late tick never enters the live bars"
    late = [e for e in eng._dq_pending if e.check == "late_tick"]
    assert len(late) == 1 and late[0].severity == "warning", "warned once per minute, every late tick counted"
    assert eng._late_ticks == 2
    eng._refresh_account(_tick(CLOSE - pd.Timedelta(seconds=4)))
    assert eng.state.dq_error is False, "a warning never blocks entries"
    eng._write_state()
    st = json.loads((tmp_path / "engine_x.json").read_text())
    assert st["late_ticks"] == 2 and "late_tick" not in st["dq_checks"]
    assert any(w.startswith("late_tick:") for w in st["dq_warnings"])
    # a minute later the next late tick warns again and says how many were dropped since the last warning
    _feed(eng, clock, CLOSE - pd.Timedelta(seconds=3), wall=CLOSE + pd.Timedelta(seconds=70))
    late = [e for e in eng._dq_pending + eng._dq_bar_errors if e.check == "late_tick"]
    assert len(late) == 2 and "2 late tick(s)" in late[1].detail and eng._late_ticks == 3


def test_broker_vs_wall_clock_skew_is_measured_as_a_rolling_median_and_published(tmp_path):
    eng, _, clock = _engine(tmp_path)
    for s in range(0, 600, 30):                       # broker timestamps 3 s behind the wall clock, one 60 s outlier
        lag = 60.0 if s == 300 else 3.0
        _feed(eng, clock, T0 + pd.Timedelta(seconds=s), wall=T0 + pd.Timedelta(seconds=s + lag))
    assert eng.clock_skew_s() == pytest.approx(-3.0)
    eng._refresh_account(_tick(T0 + pd.Timedelta(seconds=570)))
    eng._write_state()
    st = json.loads((tmp_path / "engine_x.json").read_text())
    assert st["clock_skew_s"] == pytest.approx(-3.0) and st["bar_close_grace_s"] == BAR_CLOSE_GRACE_S
    assert eng.state.dq_error is False, "skew alone never blocks entries (stale-data rules still apply)"
    fresh, _, _ = _engine(tmp_path / "fresh")
    assert fresh.clock_skew_s() is None, "no ticks yet: no measurement"


def test_replay_tick_clock_never_decides_a_close_twice_with_clock_polls(tmp_path):
    """A replayed day where the run loop polls between ticks (the wall clock just before the next tick): every
    decision bar is decided exactly once, and the same closes as a replay without polls."""
    ticks = synthetic_ticks("2025-03-03", "2025-03-04", ticks_per_minute=1, seed=5)
    rows = [_tick(pd.Timestamp(ts), float(b)) for ts, b in zip(ticks["ts_utc"], ticks["bid"].to_numpy(float))]
    plain, plain_closes, pclock = _engine(tmp_path / "plain")
    polled, polled_closes, qclock = _engine(tmp_path / "polled")
    for i, t in enumerate(rows):
        pclock.now = qclock.now = t.ts_utc
        plain.on_tick(t)
        polled.on_tick(t)
        if i + 1 < len(rows):                         # quiet until the next tick: the clock passes, the quote repeats
            qclock.now = rows[i + 1].ts_utc - pd.Timedelta(milliseconds=1)
            polled.on_tick(t)
    assert polled_closes == plain_closes and len(set(polled_closes)) == len(polled_closes) > 50


# ------------------------------------------------------------------------------------------------ A2 window
def test_engine_approval_window_defaults_to_the_settings_value():
    assert EngineConfig(account_id="x", broker_name="icm").approval_window_s == \
        load_settings().risk.approval_window_seconds == 90


def test_production_engine_reads_the_approval_window_from_settings():
    assert "approval_window_s=settings.risk.approval_window_seconds" in inspect.getsource(run.run_engine)


@pytest.mark.integration
def test_proposals_carry_the_configured_approval_window(tmp_path):
    ticks = synthetic_ticks("2025-03-03", "2025-03-06", ticks_per_minute=1, seed=5)
    spec = SPECIALISTS["session_open"](min_body_pct=0.0, asia_range_max_atr_d=99.0)
    eng = Engine(EngineConfig(account_id="x", broker_name="icm", state_dir=str(tmp_path), owner_user_id=111,
                              approval_window_s=45), PaperBroker(equity=10_000), [spec],
                 {"session_open": ConstantModel(p=0.65)})
    windows = []
    for ts, bid, ask in zip(ticks["ts_utc"], ticks["bid"].to_numpy(float), ticks["ask"].to_numpy(float)):
        eng.on_tick(Tick(ts_utc=pd.Timestamp(ts), bid=float(bid), ask=float(ask)))
        windows += [p.window_s for _, p, _ in eng.pending.values()]
    assert windows and set(windows) == {45}


# ------------------------------------------------------------------------------------------------ M9 allocator
EVENT = pd.Timestamp("2026-10-14 12:30", tz="UTC")


def _calendar(root: Path) -> None:
    rows = [("cpi", EVENT, "USD", "CPI m/m", "High", 1, "0.3%", "0.2%"),
            ("ism", EVENT - pd.Timedelta(hours=2), "USD", "ISM Manufacturing PMI", "High", 2, "49.5", "48.7")]
    Store(root).append("calendar_events", pd.DataFrame(
        [{"event_id": i, "ts_utc": t, "country": c, "title": ti, "impact": im, "tier": tier, "forecast": f,
          "previous": p, "received_utc": EVENT - pd.Timedelta(days=3)} for i, t, c, ti, im, tier, f, p in rows],
        columns=COLUMNS), source="forexfactory")


def test_tier1_minutes_counts_only_tier1_events():
    ev = pd.DataFrame({"ts_utc": [EVENT - pd.Timedelta(hours=2), EVENT], "tier": [2, 1]})
    assert tier1_minutes(ev, EVENT - pd.Timedelta(minutes=20)) == (20.0, None)
    assert tier1_minutes(ev, EVENT + pd.Timedelta(minutes=25)) == (None, 25.0)
    assert tier1_minutes(pd.DataFrame(), EVENT) == (None, None)


@pytest.mark.parametrize("offset_min, zero", [(-45, False), (-20, False), (-10, True), (0, True), (25, True),
                                               (45, False)])
def test_allocator_weight_is_zero_inside_the_settings_blackout_of_a_tier1_event_from_the_engine_calendar(
        tmp_path, offset_min, zero):
    _calendar(tmp_path / "data")
    eng = Engine(EngineConfig(account_id="x", broker_name="icm", state_dir=str(tmp_path),
                              data_root=str(tmp_path / "data"), news_blackout=True), PaperBroker(equity=10_000), [], {})
    now = EVENT + pd.Timedelta(minutes=offset_min)
    eng._blackout(now)                                # the RiskGate's calendar read (_refresh_account at every close)
    w = eng.allocator.weights(eng._regime(pd.DataFrame({"adx14": [30.0] * 50}), now))
    if zero:
        assert set(w.values()) == {0.0}
    else:
        assert w["trend"] == 1.0 and w["session_open"] == 0.75


def test_allocator_has_no_tier1_input_without_the_engine_news_blackout(tmp_path):
    eng = Engine(EngineConfig(account_id="x", broker_name="icm", state_dir=str(tmp_path)), PaperBroker(equity=10_000),
                 [], {})
    r = eng._regime(pd.DataFrame({"adx14": [30.0] * 50}), EVENT)
    assert (r.minutes_to_tier1, r.minutes_since_tier1) == (None, None)
    assert RuleAllocator().weights(r)["trend"] == 1.0


@pytest.mark.integration
def test_engine_bar_closes_inside_the_tier1_window_get_zero_allocator_weight(tmp_path):
    """End to end: the weights the engine computes at each bar close in a replay are zero inside the settings blackout
    (15 min before to 30 min after) of a tier-1 event in the archived calendar, and not outside it."""
    event = pd.Timestamp("2025-03-05 13:30", tz="UTC")
    rows = [("nfp", event, "USD", "Non-Farm Employment Change", "High", 1, "150K", "142K")]
    Store(tmp_path / "data").append("calendar_events", pd.DataFrame(
        [{"event_id": i, "ts_utc": t, "country": c, "title": ti, "impact": im, "tier": tier, "forecast": f,
          "previous": p, "received_utc": t - pd.Timedelta(days=3)} for i, t, c, ti, im, tier, f, p in rows],
        columns=COLUMNS), source="forexfactory")
    eng = Engine(EngineConfig(account_id="x", broker_name="icm", state_dir=str(tmp_path),
                              data_root=str(tmp_path / "data"), news_blackout=True), PaperBroker(equity=10_000), [], {})
    by_close: dict[pd.Timestamp, dict[str, float]] = {}
    real = eng._regime

    def regime(X: pd.DataFrame, close_ts: pd.Timestamp) -> Regime:
        r = real(X, close_ts)
        by_close[close_ts] = eng.allocator.weights(r)
        return r

    eng._regime = regime                              # type: ignore[method-assign]
    ticks = synthetic_ticks("2025-03-03", "2025-03-06", ticks_per_minute=1, seed=5)
    for ts, bid, ask in zip(ticks["ts_utc"], ticks["bid"].to_numpy(float), ticks["ask"].to_numpy(float)):
        eng.on_tick(Tick(ts_utc=pd.Timestamp(ts), bid=float(bid), ask=float(ask)))
    inside = [w for c, w in by_close.items() if -15 * 60 <= (c - event).total_seconds() <= 30 * 60]
    outside = [w for c, w in by_close.items() if abs((c - event).total_seconds()) > 60 * 60]
    assert inside and all(set(w.values()) == {0.0} for w in inside)
    assert outside and all(w["session_open"] == 0.75 for w in outside)


def test_engine_allocator_blackout_window_matches_the_risk_gate_window_from_settings(tmp_path):
    b = load_settings().risk.blackout
    eng = Engine(EngineConfig(account_id="x", broker_name="icm", state_dir=str(tmp_path),
                              blackout_before_min=b.before_min, blackout_after_min=b.after_min),
                 PaperBroker(equity=10_000), [], {})
    assert (eng.allocator.before, eng.allocator.after) == (b.before_min, b.after_min) == (15, 30)
    dflt = Engine(EngineConfig(account_id="y", broker_name="icm", state_dir=str(tmp_path / "y")),
                  PaperBroker(equity=10_000), [], {})
    assert (dflt.allocator.before, dflt.allocator.after) == (dflt.cfg.blackout_before_min, dflt.cfg.blackout_after_min)
    custom = Engine(EngineConfig(account_id="z", broker_name="icm", state_dir=str(tmp_path / "z"),
                                 blackout_before_min=5, blackout_after_min=7), PaperBroker(equity=10_000), [], {})
    assert (custom.allocator.before, custom.allocator.after) == (5, 7)
