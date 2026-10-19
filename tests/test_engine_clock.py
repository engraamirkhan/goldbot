"""The engine reads the broker's clock and sessions (entries only; exits never gated): an NY+7 server clock through the
Oct/Nov 2026 gap week, ticks stamped in the future, the server-clock probe, and the D13 broker session table."""
import json
import threading
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.data.calendar import BrokerSessions, SessionTable, TradingSession
from goldbot.data.timeutil import NY_PLUS_7, server_to_utc, server_wall
from goldbot.engine import Engine, EngineConfig
from goldbot.execution.broker import OrderIntent, OrderResult, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops import server_clock
from goldbot.ops.run import SessionRefresher, engine_pass, refresh_sessions
from goldbot.risk import Intent
from goldbot.risk.gate import Stage
from goldbot.specialists import SPECIALISTS
from goldbot.telegram.approvals import ApprovalCenter, Outcome, Proposal

OWNER = 111
ATHENS = "Europe/Athens"
WED = pd.Timestamp("2026-10-28 12:00", tz="UTC")      # Wednesday of the Oct/Nov 2026 gap week, market open
FRI = pd.Timestamp("2026-10-30 18:00", tz="UTC")      # Friday 21:00 on the NY+7 server clock (UTC+3)


def _tick(t: pd.Timestamp, bid: float = 2400.0) -> Tick:
    return Tick(ts_utc=t, bid=bid, ask=bid + 0.2)


def _intent() -> Intent:
    return Intent(agent_id="session_open-x", side=1, p=0.62, target_atr=1.5, stop_atr=1.0, atr_usd=4.0, cost_atr=0.1,
                  multiplier=1.0, price=2400.2)


def _engine(tmp_path: Path, start: pd.Timestamp = WED, pb: PaperBroker | None = None, tz: str = NY_PLUS_7,
            host: list[pd.Timestamp] | None = None, **cfg: Any) -> tuple[Engine, PaperBroker, list[pd.Timestamp]]:
    """An engine on the live clock whose host clock is `host[0]` (tests move it)."""
    if pb is None:
        pb = PaperBroker(equity=10_000)
        pb.on_tick(_tick(start))
    clock = host if host is not None else [start]
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), owner_user_id=OWNER,
                              server_tz=tz, live_clock=True, **cfg), pb, [], {}, ApprovalCenter({OWNER}))
    eng.clock = lambda: clock[0]
    _fresh_bar(eng, clock[0])
    return eng, pb, clock


def _fresh_bar(eng: Engine, visible_at: pd.Timestamp) -> None:
    eng.bars_1m = pd.DataFrame({"ts_utc": [visible_at - pd.Timedelta(minutes=1)], "visible_at": [visible_at]})


def _broker_stamp(real_utc: pd.Timestamp, broker_tz: str, configured_tz: str) -> pd.Timestamp:
    """The adapter's tick time: the broker stamps in its clock, the adapter converts with the configured zone."""
    return server_to_utc(server_wall(real_utc, broker_tz), configured_tz)[0]


def _open(pb: PaperBroker, side: int, sl: float, magic: int = 260150) -> int:
    pid = pb.place_order(OrderIntent(client_order_id=f"o{time.monotonic_ns()}", symbol="XAUUSD", side=side, lots=0.1,
                                     sl=sl, tp=2400.0 + side * 500.0, magic=magic, comment="t")).position_id
    assert pid is not None
    return pid


# ---------------------------------------------------------------------------------------------- 1. NY+7 gap week
def test_ny7_engine_runs_the_oct_2026_gap_week_with_the_friday_cut_at_1830_and_rollover_at_2100_utc(tmp_path):
    eng, pb, host = _engine(tmp_path, start=pd.Timestamp("2026-10-26 00:00", tz="UTC"))
    t = pd.Timestamp("2026-10-26 00:00", tz="UTC")
    i = 0
    while t < pd.Timestamp("2026-10-30 18:00", tz="UTC"):          # a tick every 10 minutes, Monday to Friday 18:00
        host[0] = t
        px = 2400.0 + (i * 7 % 23) * 0.4                               # a moving price (a flat one has no volatility)
        pb.on_tick(_tick(t, px))
        eng.on_tick(_tick(t, px))
        t += pd.Timedelta(minutes=10)
        i += 1
    for utc, rollover in (("2026-10-28 20:54", False), ("2026-10-28 20:57", True), ("2026-10-28 21:04", True),
                          ("2026-10-28 21:06", False)):
        host[0] = pd.Timestamp(utc, tz="UTC")
        _fresh_bar(eng, host[0])
        eng._refresh_account(_tick(host[0]))
        assert eng.state.in_rollover is rollover, utc                 # 00:00 server = 21:00 UTC at UTC+3
    win, lose = _open(pb, 1, 2390.0), _open(pb, -1, 2450.0)
    for utc, weekend in (("2026-10-30 18:29", False), ("2026-10-30 18:31", True)):
        host[0] = pd.Timestamp(utc, tz="UTC")
        pb.on_tick(_tick(host[0], 2410.0))
        eng._refresh_account(pb.last_tick("XAUUSD"))
        assert eng.state.weekend is weekend, utc                      # Friday 21:30 server = 18:30 UTC
    left = {p.position_id for p in pb.positions()}
    assert lose not in left and win in left


# ---------------------------------------------------------------------------------------------- 2/3. future ticks
def test_a_future_tick_refuses_entries_while_every_exit_still_executes(tmp_path):
    eng, pb, host = _engine(tmp_path, start=FRI)
    ahead = FRI + pd.Timedelta(hours=1)
    stop_pid = _open(pb, 1, 2399.0)
    eng.open[stop_pid] = eng._adopt(next(p for p in pb.positions() if p.position_id == stop_pid))
    pb.on_tick(_tick(ahead, 2398.0))
    eng.on_tick(_tick(ahead, 2398.0))                                  # through the engine stop: closed at market
    assert stop_pid not in {p.position_id for p in pb.positions()}
    eng._refresh_account(_tick(ahead, 2398.0))
    assert eng.state.tick_ahead_s == pytest.approx(3600.0)
    reasons = eng.gate.check(_intent(), eng.state).reasons
    assert "tick_from_future" in reasons and "server_clock_mismatch" in reasons
    # time barrier: a trade past its max bars is closed at the next bar close whatever the clock says
    tb = _open(pb, 1, 2300.0)
    eng.open[tb] = eng._adopt(next(p for p in pb.positions() if p.position_id == tb))
    eng.open[tb].max_bars, eng.open[tb].bars_held = 1, 0
    eng._manage_open(pd.DataFrame())
    assert tb not in {p.position_id for p in pb.positions()}
    # weekend close: Friday 21:30 server reached on the host clock, losers closed despite the future stamps
    loser = _open(pb, -1, 2500.0)
    host[0] = FRI + pd.Timedelta(minutes=31)
    pb.on_tick(_tick(host[0] + pd.Timedelta(hours=1), 2420.0))
    eng._refresh_account(pb.last_tick("XAUUSD"))
    assert loser not in {p.position_id for p in pb.positions()}
    # kill switch: a 12% drawdown closes everything
    _open(pb, 1, 2300.0)
    eng.state.stage = Stage.NORMAL
    eng.state.balance_closed_hwm = 20_000
    eng._refresh_account(pb.last_tick("XAUUSD"))
    assert eng.state.stage == Stage.HALTED and not pb.positions()


def test_a_frozen_feed_with_a_future_stamp_goes_stale_on_host_time_and_one_second_ahead_is_fine(tmp_path):
    eng, pb, host = _engine(tmp_path)
    eng._refresh_account(_tick(WED + pd.Timedelta(seconds=1)))       # 1 s ahead: normal broker/host jitter
    assert eng.state.tick_ahead_s == pytest.approx(1.0)
    assert eng.gate.check(_intent(), eng.state).allowed
    host[0] = WED + pd.Timedelta(seconds=25)                           # the feed froze; host time moves on
    _fresh_bar(eng, host[0])
    eng._refresh_account(_tick(WED + pd.Timedelta(seconds=1)))
    assert eng.state.last_tick_age_s == pytest.approx(24.0)
    assert "stale_data" in eng.gate.check(_intent(), eng.state).reasons
    eng2, _, host2 = _engine(tmp_path / "b")
    for s in (0, 30, 120):                                             # a frozen tick an hour ahead never looks fresh
        host2[0] = WED + pd.Timedelta(seconds=s)
        _fresh_bar(eng2, host2[0])
        eng2._refresh_account(_tick(WED + pd.Timedelta(hours=1)))
        reasons = eng2.gate.check(_intent(), eng2.state).reasons
        assert "tick_from_future" in reasons and "stale_data" in reasons


# ---------------------------------------------------------------------------------------------- 4/5/6. broker sessions
def _early_close_table(tz: str, close: pd.Timestamp) -> SessionTable:
    day0 = close.normalize() - pd.Timedelta(days=1)
    return SessionTable(server_tz=tz).model_copy(update={
        "broker_windows": ((day0, close),), "covers_from": day0, "covers_until": day0 + pd.Timedelta(days=7),
        "source": "broker"})


def test_a_holiday_early_close_refuses_entries_and_approvals_but_exits_still_go(tmp_path):
    close = WED + pd.Timedelta(hours=1)                                # the broker closes early at 13:00 UTC
    eng, pb, host = _engine(tmp_path)
    eng.set_sessions(_early_close_table(NY_PLUS_7, close))
    eng._refresh_account(_tick(WED))
    assert not eng.state.market_closed and eng.gate.check(_intent(), eng.state).allowed
    agent = SPECIALISTS["session_open"]()
    prop = Proposal(proposal_id="icm-demo-x", account_id="icm-demo", agent_id=agent.agent_id, side=1, lots=0.1,
                    entry=2400.2, stop=2396.2, target=2406.2, p=0.62, ev_r=0.2, spread_points=20, top_features=[])
    eng.pending[prop.proposal_id] = (_intent(), prop, agent)
    pid = _open(pb, 1, 2399.0)
    eng.open[pid] = eng._adopt(next(p for p in pb.positions() if p.position_id == pid))
    host[0] = close + pd.Timedelta(minutes=5)
    _fresh_bar(eng, host[0])
    pb.on_tick(_tick(host[0], 2398.5))
    eng.on_tick(_tick(host[0], 2398.5))
    assert pid not in {p.position_id for p in pb.positions()}          # the exit was sent
    eng._refresh_account(pb.last_tick("XAUUSD"))
    assert eng.state.market_closed and "market_closed" in eng.gate.check(_intent(), eng.state).reasons
    prop.outcome = Outcome.APPROVED                                    # the owner clicks after the close
    before = len(pb.positions())
    eng._on_decision(prop)
    assert len(pb.positions()) == before
    assert any("market_closed" in str(d.get("action")) for d in eng.decisions)
    state = json.loads((tmp_path / "engine_icm-demo.json").read_text())
    assert state["session_source"] == "broker" and state["market_closed"] is True


class _SpyBroker(PaperBroker):
    calls = 0

    def trading_sessions(self, symbol: str) -> list[TradingSession]:
        type(self).calls += 1
        return super().trading_sessions(symbol)


def test_on_tick_never_asks_the_broker_for_sessions(tmp_path):
    pb = _SpyBroker(equity=10_000)
    pb.on_tick(_tick(WED))
    eng, _, host = _engine(tmp_path, pb=pb)
    for s in range(0, 3600, 15):
        host[0] = WED + pd.Timedelta(seconds=s)
        pb.on_tick(_tick(host[0]))
        eng.on_tick(_tick(host[0]))
    assert _SpyBroker.calls == 0


def test_a_sessions_refresh_that_raises_or_hangs_keeps_the_last_table(tmp_path):
    good = _early_close_table(NY_PLUS_7, WED + pd.Timedelta(hours=10))

    class Raising:
        def table(self, now: pd.Timestamp) -> SessionTable:
            raise RuntimeError("bridge down")

    class Hanging:
        def table(self, now: pd.Timestamp) -> SessionTable:
            threading.Event().wait(2.0)
            return SessionTable()

    class Fallback:
        def table(self, now: pd.Timestamp) -> SessionTable:
            return SessionTable(server_tz=NY_PLUS_7).model_copy(update={"source": "static (fallback)", "note": "x"})

    assert refresh_sessions(Raising(), WED, good) is good
    t0 = time.monotonic()
    assert refresh_sessions(Hanging(), WED, good, timeout_s=0.2) is good
    assert time.monotonic() - t0 < 1.5
    assert refresh_sessions(Fallback(), WED, good) is good             # a broker table still covering now is kept
    fresh = refresh_sessions(Fallback(), WED, None)
    assert fresh is not None and fresh.source == "static (fallback)"
    real = BrokerSessions(PaperBroker(equity=1), "XAUUSD", fallback=SessionTable(server_tz=NY_PLUS_7))
    assert refresh_sessions(real, WED, None) is not None


# ---------------------------------------------------------------------------------------------- 7/8/9. the probe
def test_a_probe_that_raises_is_not_measured_and_never_blocks(tmp_path, monkeypatch):
    def boom(*a: Any, **k: Any) -> Any:
        raise ValueError("bad")
    monkeypatch.setattr(server_clock, "assess", boom)
    eng, pb, _ = _engine(tmp_path)
    eng._refresh_account(_tick(WED))
    assert eng.clock_probe is not None and eng.clock_probe.status == "not_measured"
    assert not eng.state.clock_mismatch and eng.gate.check(_intent(), eng.state).allowed


def test_not_measured_never_blocks_and_a_mismatch_clears_through_data_recovering(tmp_path):
    sat = pd.Timestamp("2026-10-31 12:00", tz="UTC")
    eng, _, host = _engine(tmp_path, start=sat, tz=ATHENS)
    eng._refresh_account(_tick(sat - pd.Timedelta(hours=14)))          # weekend: last tick hours old
    assert eng.clock_probe is not None and eng.clock_probe.status == "not_measured"
    assert not eng.state.clock_mismatch
    eng2, _, host2 = _engine(tmp_path / "b", tz=ATHENS)               # Athens configured, broker on NY+7, gap week
    eng2._refresh_account(_tick(_broker_stamp(WED, NY_PLUS_7, ATHENS)))
    assert eng2.state.clock_mismatch and "server_clock_mismatch" in eng2.gate.check(_intent(), eng2.state).reasons
    assert eng2.clock_probe is not None and "NY+7" in eng2.clock_probe.reason
    host2[0] = WED + pd.Timedelta(seconds=10)                          # zone fixed: fresh ticks agree again
    _fresh_bar(eng2, host2[0])
    eng2._refresh_account(_tick(host2[0]))
    assert not eng2.state.clock_mismatch and eng2.state.data_recovering
    host2[0] = WED + pd.Timedelta(seconds=75)
    _fresh_bar(eng2, host2[0])
    eng2._refresh_account(_tick(host2[0]))
    assert eng2.gate.check(_intent(), eng2.state).allowed


def test_a_mismatch_between_proposal_and_click_refuses_the_approval(tmp_path):
    eng, pb, _ = _engine(tmp_path, tz=ATHENS)
    eng._refresh_account(_tick(WED))
    assert eng.gate.check(_intent(), eng.state).allowed
    agent = SPECIALISTS["session_open"]()
    prop = Proposal(proposal_id="icm-demo-y", account_id="icm-demo", agent_id=agent.agent_id, side=1, lots=0.1,
                    entry=2400.2, stop=2396.2, target=2406.2, p=0.62, ev_r=0.2, spread_points=20, top_features=[])
    eng.pending[prop.proposal_id] = (_intent(), prop, agent)
    pb.on_tick(_tick(_broker_stamp(WED, NY_PLUS_7, ATHENS)))          # the broker's stamps now read an hour ahead
    prop.outcome = Outcome.APPROVED
    eng._on_decision(prop)
    assert not pb.positions()
    refusal = [d["action"] for d in eng.decisions if str(d.get("action", "")).startswith("gate_at_approval")]
    assert refusal and "server_clock_mismatch" in refusal[0]


# ---------------------------------------------------------------------------------------------- 10. restart
def test_a_ny7_restart_warm_starts_and_reconciles(tmp_path):
    from goldbot.data.resample import ticks_to_1m
    from goldbot.data.store import Store
    from goldbot.data.synthetic import synthetic_ticks
    data = tmp_path / "data"
    ticks = synthetic_ticks("2026-10-26", "2026-10-28", ticks_per_minute=1, seed=3)
    Store(str(data)).append("bars_1m", ticks_to_1m(ticks), source="dukascopy", symbol="XAUUSD")
    eng, pb, host = _engine(tmp_path, data_root=str(data))
    agent = SPECIALISTS["session_open"]()
    prop = Proposal(proposal_id="icm-demo-z", account_id="icm-demo", agent_id=agent.agent_id, side=1, lots=0.1,
                    entry=2400.2, stop=2396.2, target=2406.2, p=0.62, ev_r=0.2, spread_points=20, top_features=[])
    eng._execute(prop, agent, 0.1, 2396.0, 2406.0, requested=2400.2)
    [pos] = pb.positions()
    again, _, _ = _engine(tmp_path, pb=pb, host=host, data_root=str(data))
    assert again.warm_start(WED) > 0
    assert again.open[pos.position_id].agent_id == agent.agent_id
    again.on_tick(_tick(WED + pd.Timedelta(seconds=1)))
    again._refresh_account(_tick(WED + pd.Timedelta(seconds=1)))
    assert again.clock_probe is not None and again.clock_probe.status == "ok"


# ---------------------------------------------------------------------------------------------- review fixes (b55f5d1)
def test_future_tick_threshold_matches_the_clock_skew_rule(tmp_path):
    from goldbot.engine.runner import SKEW_BLOCK_S
    from goldbot.risk import RiskLimits
    assert RiskLimits().max_tick_ahead_s == SKEW_BLOCK_S
    eng, _, _ = _engine(tmp_path)
    eng._refresh_account(_tick(WED + pd.Timedelta(seconds=7)))        # a broker clock a few seconds fast still trades
    assert "tick_from_future" not in eng.gate.check(_intent(), eng.state).reasons
    eng._refresh_account(_tick(WED + pd.Timedelta(seconds=11)))
    assert "tick_from_future" in eng.gate.check(_intent(), eng.state).reasons


class _StopStuck(PaperBroker):
    """The broker keeps the stop it was sent with: every modify is rejected (a trail or weekend modify refused), so
    only the engine's own stop check can close the trade at the engine's tighter stop (stop tests must prove the
    broker did not do the work, docs/AGENT_STANDARDS.md)."""

    def modify(self, position_id: int, sl: float | None, tp: float | None) -> OrderResult:
        return OrderResult(ok=False, retcode=10016, order_id=None, position_id=position_id, filled_lots=0.0,
                           price=None, message="invalid stops")


class _ApprovalBoom(_StopStuck):
    """The broker read at approval time fails (bridge hiccup) while the tick feed is fine."""
    boom = False

    def last_tick(self, symbol: str) -> Tick:
        if self.boom:
            raise ConnectionError("bridge read failed")
        return super().last_tick(symbol)


def test_an_approval_error_never_skips_the_exits_of_that_tick_and_blocks_entries_until_clean(tmp_path):
    pb = _ApprovalBoom(equity=10_000)
    pb.on_tick(_tick(WED))
    eng, _, host = _engine(tmp_path, pb=pb)
    pid = _open(pb, 1, 2300.0)                                         # the broker's stop is far away
    eng.open[pid] = eng._adopt(next(p for p in pb.positions() if p.position_id == pid))
    eng.open[pid].sl = 2399.0                                          # the engine's own stop is tight
    agent = SPECIALISTS["session_open"]()
    prop = Proposal(proposal_id="icm-demo-e", account_id="icm-demo", agent_id=agent.agent_id, side=1, lots=0.1,
                    entry=2400.2, stop=2396.2, target=2406.2, p=0.62, ev_r=0.2, spread_points=20, top_features=[])
    eng.pending[prop.proposal_id] = (_intent(), prop, agent)
    prop.outcome = Outcome.APPROVED

    def poll() -> list[Proposal]:            # the owner's click arrives on this tick; the approval's broker read fails
        pb.boom = True
        try:
            eng.center.on_decision(prop)
        finally:
            pb.boom = False
        return []
    eng.center.poll_bus = poll               # type: ignore[method-assign]
    pb.on_tick(_tick(WED + pd.Timedelta(seconds=1), 2398.0))
    assert pid in {p.position_id for p in pb.positions()}              # the broker did not close it
    eng.on_tick(_tick(WED + pd.Timedelta(seconds=1), 2398.0))         # _on_decision raises inside poll_bus
    assert pid not in {p.position_id for p in pb.positions()}          # the engine stop still closed it
    eng._refresh_account(_tick(WED + pd.Timedelta(seconds=1), 2398.0))
    assert eng.state.tick_error and "tick_error" in eng.gate.check(_intent(), eng.state).reasons
    eng.center.poll_bus = lambda: []         # type: ignore[method-assign]
    for i in range(40):                                                # 10 s of clean polls are not enough
        host[0] = WED + pd.Timedelta(seconds=2 + i)
        eng.on_tick(_tick(host[0], 2401.0 + i * 0.01))
    assert eng.state.tick_error
    for m in (16, 31):                                                 # a whole clean decision bar (12:15-12:30) closes
        host[0] = WED + pd.Timedelta(minutes=m)
        eng.on_tick(_tick(host[0], 2401.5))
    assert not eng.state.tick_error
    state = json.loads((tmp_path / "engine_icm-demo.json").read_text())
    assert state["tick_error"] is False and state["tick_errors_total"] == 1


def test_the_engine_loop_survives_any_exception_and_health_alerts_on_it(tmp_path):
    from goldbot.ops import health

    class Dead(PaperBroker):
        def last_tick(self, symbol: str) -> Tick:
            raise RuntimeError("terminal gone")
    pb = Dead(equity=10_000)
    eng, _, _ = _engine(tmp_path, pb=pb)
    assert engine_pass(eng, pb, "XAUUSD") is False                     # logged, not raised
    assert eng.state.tick_error
    e = json.loads((tmp_path / "engine_icm-demo.json").read_text())
    assert e["tick_error"] is True and "RuntimeError" in e["tick_error_detail"]
    ctx = health.HealthContext(state_dir=tmp_path, now=pd.Timestamp.now("UTC"), get_secret=lambda k: None)
    c = health.check_engine(ctx, "icm-demo")
    assert c.status == "fail" and "tick error" in c.reason


def test_regime_survives_a_constant_volatility_window(tmp_path):
    eng, _, _ = _engine(tmp_path)
    X = pd.DataFrame({"h1_rv_20": [0.001] * 100, "h1_adx14": [25.0] * 100})
    assert eng._regime(X, WED).atr_1h_quartile == 1


def test_the_session_refresh_never_makes_the_loop_wait(tmp_path):
    release = threading.Event()
    good = _early_close_table(NY_PLUS_7, WED + pd.Timedelta(hours=10))

    class Slow:
        def table(self, now: pd.Timestamp) -> SessionTable:
            release.wait(5.0)
            return good
    r = SessionRefresher(Slow())
    t0 = time.monotonic()
    assert r.start(WED) is True
    assert r.collect(WED, None) is None                               # still reading: nothing yet, no wait
    assert r.start(WED) is False                                       # never two reads at once
    assert time.monotonic() - t0 < 0.2
    release.set()
    deadline = time.monotonic() + 2.0
    got = None
    while got is None and time.monotonic() < deadline:
        got = r.collect(WED, None)
        time.sleep(0.01)
    assert got is good


def test_a_session_table_error_fails_closed_for_entries_only(tmp_path):
    eng, pb, _ = _engine(tmp_path)

    class Broken(SessionTable):
        def entry_block(self, now: pd.Timestamp) -> str | None:
            raise ValueError("bad window")

        def is_open(self, utc_ts: pd.DatetimeIndex) -> Any:
            raise ValueError("bad window")
    eng.set_sessions(Broken(server_tz=NY_PLUS_7))
    pid = _open(pb, 1, 2399.0)
    eng.open[pid] = eng._adopt(next(p for p in pb.positions() if p.position_id == pid))
    pb.on_tick(_tick(WED, 2398.0))
    eng.on_tick(_tick(WED, 2398.0))
    assert pid not in {p.position_id for p in pb.positions()}          # exits unaffected
    eng._refresh_account(_tick(WED, 2398.0))
    assert eng.state.market_closed and "market_closed" in eng.gate.check(_intent(), eng.state).reasons


# ---------------------------------------------------------------------------------------------- exits before logging
def test_a_full_disk_and_a_failing_account_read_on_every_tick_never_stop_the_engine_stop(tmp_path):
    from goldbot.data.store import Store
    pb = _StopStuck(equity=10_000)
    pb.on_tick(_tick(WED))
    eng, _, host = _engine(tmp_path, pb=pb, data_root=str(tmp_path / "data"), tick_flush_s=0)

    def full(*a: Any, **k: Any) -> Any:
        raise OSError(28, "No space left on device")
    assert isinstance(eng.store, Store)
    eng.store.append = full                                            # type: ignore[method-assign]
    pid = _open(pb, 1, 2300.0)                                         # the broker's stop is far away
    eng.open[pid] = eng._adopt(next(p for p in pb.positions() if p.position_id == pid))
    eng.open[pid].sl = 2399.0                                          # the engine's own stop is tight
    eng._roll_risk_period = lambda now: (_ for _ in ()).throw(OSError("account read failed"))  # type: ignore[method-assign]
    for i, px in enumerate((2401.0, 2400.5, 2398.0)):                  # the third quote is through the engine stop
        host[0] = WED + pd.Timedelta(seconds=i + 1)
        pb.on_tick(_tick(host[0], px))
        assert pid in {p.position_id for p in pb.positions()}          # open before the engine pass: not the broker
        engine_pass(eng, pb, "XAUUSD")                                 # the run loop's pass
    assert pid not in {p.position_id for p in pb.positions()}
    assert eng.state.tick_error and eng.tick_errors_total >= 3
    for i in range(3, 5000):                                           # the tick buffer is capped, not unbounded
        host[0] = WED + pd.Timedelta(seconds=i)
        pb.on_tick(_tick(host[0], 2401.0 + (i % 7) * 0.01))
        engine_pass(eng, pb, "XAUUSD")
    from goldbot.engine.runner import TICK_LOG_MAX
    assert len(eng._tick_log) <= TICK_LOG_MAX


def test_tick_errors_are_rate_limited_in_the_log_and_the_state_file(tmp_path, caplog):
    class Dead(PaperBroker):
        def last_tick(self, symbol: str) -> Tick:
            raise RuntimeError("terminal gone")
    pb = Dead(equity=10_000)
    eng, _, _ = _engine(tmp_path, pb=pb)
    writes: list[int] = []
    real = eng._write_state

    def counted(heartbeat: bool = False) -> None:
        writes.append(1)
        real(heartbeat)
    eng._write_state = counted                                         # type: ignore[method-assign]
    import logging
    with caplog.at_level(logging.ERROR, logger="goldbot.engine"):
        for _ in range(400):                                           # 100 s of 0.25 s passes, all failing
            engine_pass(eng, pb, "XAUUSD")
    logged = [r for r in caplog.records if "tick processing failed" in r.getMessage()]
    assert 1 <= len(logged) <= 3                                       # the first, then at most once a minute
    assert len(writes) <= 12                                           # not one state write per pass
    assert eng.tick_errors_total == 400
    e = json.loads((tmp_path / "engine_icm-demo.json").read_text())
    assert e["tick_errors_total"] >= 1 and e["last_tick_error_at"]


def test_an_assert_in_the_tick_path_is_recorded_not_swallowed(tmp_path):
    class Asserting(PaperBroker):
        def last_tick(self, symbol: str) -> Tick:
            raise AssertionError("invariant broken")
    pb = Asserting(equity=10_000)
    eng, _, _ = _engine(tmp_path, pb=pb)
    assert engine_pass(eng, pb, "XAUUSD") is False
    assert eng.state.tick_error and eng.tick_errors_total == 1


def test_health_warns_on_a_recent_or_new_tick_error_after_it_cleared(tmp_path):
    import time as _t

    from goldbot.ops import health
    now = pd.Timestamp.now("UTC")
    base = {"account": "icm-demo", "ts": _t.time(), "stage": "normal", "tick_error": False, "tick_errors_total": 2}
    f = tmp_path / "engine_icm-demo.json"
    ctx = health.HealthContext(state_dir=tmp_path, now=now, get_secret=lambda k: None)
    f.write_text(json.dumps({**base, "last_tick_error_at": (now - pd.Timedelta(minutes=5)).isoformat()}))
    c = health.check_tick_errors(ctx, "icm-demo")
    assert c is not None and c.status == "warn" and "tick error" in c.reason
    assert health._alerting(c.name, c.status)                          # announced on Telegram
    f.write_text(json.dumps({**base, "last_tick_error_at": (now - pd.Timedelta(minutes=40)).isoformat()}))
    old = health.check_tick_errors(ctx, "icm-demo")
    assert old is not None and old.status == "ok"
    since = health.HealthContext(state_dir=tmp_path, now=now, get_secret=lambda k: None,
                                 last_run=now - pd.Timedelta(hours=1))
    c = health.check_tick_errors(since, "icm-demo")                    # a new error since the last health run
    assert c is not None and c.status == "warn"
    f.write_text(json.dumps(base))
    assert health.check_tick_errors(ctx, "icm-demo") is None


def test_tick_error_clearing_uses_the_tick_clock_when_ticks_run_ahead_of_the_host(tmp_path):
    ahead = pd.Timedelta(seconds=30)                                   # broker stamps 30 s ahead of the host clock
    start = pd.Timestamp("2026-10-28 12:14:00", tz="UTC")
    eng, pb, host = _engine(tmp_path, start=start)

    def feed(tick_ts: pd.Timestamp) -> None:
        host[0] = tick_ts - ahead
        pb.on_tick(_tick(tick_ts, 2401.0))
        eng.on_tick(_tick(tick_ts, 2401.0))
    feed(pd.Timestamp("2026-10-28 12:14:30", tz="UTC"))
    feed(pd.Timestamp("2026-10-28 12:15:20", tz="UTC"))                # bar 12:15-12:30 on the tick clock; host 12:14:50
    eng.note_tick_error(RuntimeError("bridge hiccup"))                 # happens inside bar 12:15-12:30
    feed(pd.Timestamp("2026-10-28 12:20:00", tz="UTC"))
    feed(pd.Timestamp("2026-10-28 12:30:05", tz="UTC"))                # closes 12:15-12:30: that bar had the error
    assert eng.state.tick_error
    feed(pd.Timestamp("2026-10-28 12:40:00", tz="UTC"))
    feed(pd.Timestamp("2026-10-28 12:45:05", tz="UTC"))                # closes 12:30-12:45, clean on the tick clock
    assert not eng.state.tick_error
