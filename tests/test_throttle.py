"""BACKLOG 21 / playbook G-6: a per-agent loss-streak and entry-rate throttle, the system analogue of revenge trading
and overtrading. Off by default (`risk.throttle.enabled: false`) until the owner sets the thresholds.

When on, RiskGate refuses an agent's entries with `throttle_loss_streak` after `loss_streak` consecutive full losses
(closed trades of that agent on this account) until `cooldown_bars` bars of its timeframe have passed since the last
of them, or a winning shadow trade of that agent closes after it; and with `throttle_entry_rate` once the agent has
made `max_entries_per_day_per_agent` entries in the risk day. Entries only: exits never consult it. The state is
derived from the durable closed-trade record, the open book and the shadow book, so it survives a restart."""
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from pydantic import ValidationError

from goldbot.config import RiskSettings, ThrottleSettings, load_settings
from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine import ConstantModel, Engine, EngineConfig
from goldbot.engine.shadow import ShadowBook
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops import health
from goldbot.ops.gates_phase import ClosedTrade, append_closed_trade
from goldbot.risk import AccountState, Intent, RiskGate, RiskLimits
from goldbot.risk.throttle import AgentThrottle, agent_throttle, is_full_loss
from goldbot.specialists import SPECIALISTS
from goldbot.specialists.trend import TrendSpecialist
from goldbot.telegram.approvals import Proposal
from tests.test_health import by_name, engine, make_ctx

T0 = pd.Timestamp("2025-03-03 08:00", tz="UTC")
ON = ThrottleSettings(enabled=True, loss_streak=3, cooldown_bars=4, max_entries_per_day_per_agent=3)


def _state(**kw: Any) -> AccountState:
    base: dict[str, Any] = dict(equity=10_000.0, balance_closed_hwm=10_000.0, day_start_equity=10_000.0,
                                week_start_equity=10_000.0, open_positions=0, margin_used=0.0, last_tick_age_s=1.0,
                                spread_points=25.0)
    base.update(kw)
    return AccountState(**base)


def _intent(agent: str = "a1") -> Intent:
    return Intent(agent_id=agent, side=1, p=0.7, target_atr=3.0, stop_atr=1.0, atr_usd=5.0, cost_atr=0.01,
                  multiplier=1.0, price=2_000.0, timeframe="15m")


def _trade(agent: str, exit_utc: pd.Timestamp, r: float, *, pid: int, entry_utc: pd.Timestamp | None = None,
           account: str = "icm-demo", reason: str = "stop") -> ClosedTrade:
    return ClosedTrade(account_id=account, broker="icm", mode="demo", exit_utc=exit_utc, ret=r * 0.0025,
                       pnl=r * 50.0, equity_before=10_000.0, position_id=pid, agent_id=agent, side=1,
                       entry_utc=entry_utc if entry_utc is not None else exit_utc - pd.Timedelta(minutes=30),
                       exit_reason=reason if r < 0 else "target", r=r)


def _losses(agent: str, n: int, last: pd.Timestamp, start_pid: int = 1) -> list[ClosedTrade]:
    return [_trade(agent, last - pd.Timedelta(hours=n - 1 - i), -1.02, pid=start_pid + i) for i in range(n)]


# ---------------------------------------------------------------------------------------------- settings
def test_the_throttle_is_off_by_default_with_the_proposed_thresholds_in_settings_yaml():
    t = load_settings().risk.throttle
    assert (t.enabled, t.loss_streak, t.cooldown_bars, t.max_entries_per_day_per_agent) == (False, 3, 4, 3)
    assert RiskLimits.from_settings(load_settings().risk, tiny_live=False).throttle == t
    assert RiskLimits().throttle.enabled is False


@pytest.mark.parametrize("bad", [{"loss_streak": 0}, {"cooldown_bars": 0}, {"max_entries_per_day_per_agent": 0},
                                 {"loss_streak": -1}, {"enabled": True, "typo": 1}])
def test_throttle_settings_refuse_non_positive_thresholds_and_unknown_keys(bad):
    with pytest.raises(ValidationError):
        ThrottleSettings(**bad)
    risk = load_settings().risk.model_dump()
    risk["throttle"] = {**risk["throttle"], **bad}
    with pytest.raises(ValidationError):
        RiskSettings.model_validate(risk)


# ---------------------------------------------------------------------------------------------- the gate rule
def test_disabled_the_gate_ignores_even_a_long_streak_and_a_full_entry_count():
    t = AgentThrottle(agent_id="a1", timeframe="15m", loss_streak=9, last_loss_utc=T0, entries_today=9)
    off, none = RiskGate(RiskLimits()).check(_intent(), _state(), T0, throttle=t), RiskGate().check(_intent(), _state())
    assert off.allowed and off == none


def test_a_streak_refuses_entries_until_the_cooldown_passes():
    g = RiskGate(RiskLimits(throttle=ON))
    t = AgentThrottle(agent_id="a1", timeframe="15m", loss_streak=3, last_loss_utc=T0)
    assert g.check(_intent(), _state(), T0 + pd.Timedelta(minutes=45), throttle=t).reasons == ["throttle_loss_streak"]
    assert g.throttle_cooldown_until(t) == T0 + pd.Timedelta(hours=1)               # 4 bars of 15m
    assert g.check(_intent(), _state(), T0 + pd.Timedelta(hours=1), throttle=t).allowed
    two = t.model_copy(update={"loss_streak": 2})
    assert g.check(_intent(), _state(), T0 + pd.Timedelta(minutes=1), throttle=two).allowed


def test_the_cooldown_counts_bars_of_the_agents_own_timeframe():
    g = RiskGate(RiskLimits(throttle=ON))
    t = AgentThrottle(agent_id="a1", timeframe="4h", loss_streak=3, last_loss_utc=T0)
    assert g.throttle_cooldown_until(t) == T0 + pd.Timedelta(hours=16)
    assert not g.check(_intent(), _state(), T0 + pd.Timedelta(hours=15), throttle=t).allowed


def test_without_a_clock_or_a_known_timeframe_a_streak_fails_closed():
    g = RiskGate(RiskLimits(throttle=ON))
    t = AgentThrottle(agent_id="a1", timeframe="15m", loss_streak=3, last_loss_utc=T0)
    assert g.check(_intent(), _state(), None, throttle=t).reasons == ["throttle_loss_streak"]
    odd = t.model_copy(update={"timeframe": "7m"})
    assert g.check(_intent(), _state(), T0 + pd.Timedelta(days=30), throttle=odd).reasons == ["throttle_loss_streak"]


def test_the_entry_rate_cap_refuses_the_entry_past_the_daily_cap():
    g = RiskGate(RiskLimits(throttle=ON))
    t = AgentThrottle(agent_id="a1", timeframe="15m", entries_today=2)
    assert g.check(_intent(), _state(), T0, throttle=t).allowed                         # the 3rd entry is allowed
    full = t.model_copy(update={"entries_today": 3})
    assert g.check(_intent(), _state(), T0, throttle=full).reasons == ["throttle_entry_rate"]


def test_both_throttle_reasons_are_reported_together_with_other_refusals():
    g = RiskGate(RiskLimits(throttle=ON))
    t = AgentThrottle(agent_id="a1", timeframe="15m", loss_streak=3, last_loss_utc=T0, entries_today=3)
    d = g.check(_intent(), _state(owner_halt=True), T0, throttle=t)
    assert set(d.reasons) == {"owner_halt", "throttle_loss_streak", "throttle_entry_rate"}


# ---------------------------------------------------------------------------------------------- deriving the state
def test_a_full_loss_is_a_loss_of_at_least_nine_tenths_of_the_initial_risk():
    assert is_full_loss(_trade("a1", T0, -1.05, pid=1)) and is_full_loss(_trade("a1", T0, -0.9, pid=1))
    assert not is_full_loss(_trade("a1", T0, -0.4, pid=1)) and not is_full_loss(_trade("a1", T0, 2.0, pid=1))
    legacy = _trade("a1", T0, -1.0, pid=1).model_copy(update={"r": None})
    assert is_full_loss(legacy)                                       # no R: a stop exit at a loss
    assert not is_full_loss(legacy.model_copy(update={"exit_reason": "time_exit"}))


def test_the_streak_counts_trailing_full_losses_of_that_agent_only():
    closed = (_losses("a1", 2, T0 - pd.Timedelta(hours=5)) + [_trade("a1", T0 - pd.Timedelta(hours=3), 1.5, pid=10)]
              + _losses("a1", 3, T0, start_pid=20) + _losses("other", 4, T0, start_pid=40))
    t = agent_throttle("a1", "15m", closed=closed, entry_times=[], now=T0)
    assert t.loss_streak == 3 and t.last_loss_utc == T0
    partial = closed + [_trade("a1", T0 + pd.Timedelta(minutes=5), -0.4, pid=30)]       # a small loss ends it
    assert agent_throttle("a1", "15m", closed=partial, entry_times=[], now=T0).loss_streak == 0


def test_a_winning_shadow_trade_after_the_last_loss_clears_the_streak():
    closed = _losses("a1", 3, T0)
    asked: list[int] = []

    def win() -> pd.Timestamp:
        asked.append(1)
        return T0 + pd.Timedelta(minutes=15)

    t = agent_throttle("a1", "15m", closed=closed, entry_times=[], now=T0, shadow_win=win, consult_shadow_at=3)
    assert t.loss_streak == 0 and t.last_shadow_win_utc == T0 + pd.Timedelta(minutes=15) and asked
    before = agent_throttle("a1", "15m", closed=closed, entry_times=[], now=T0,
                            shadow_win=lambda: T0 - pd.Timedelta(hours=3), consult_shadow_at=3)
    assert before.loss_streak == 3                                    # a win before the losses clears nothing
    between = agent_throttle("a1", "15m", closed=closed, entry_times=[], now=T0,
                             shadow_win=lambda: T0 - pd.Timedelta(minutes=90), consult_shadow_at=3)
    assert between.loss_streak == 2                                   # losses after the win start a new streak
    asked.clear()
    agent_throttle("a1", "15m", closed=closed[:2], entry_times=[], now=T0, shadow_win=win, consult_shadow_at=3)
    assert not asked                                                  # below the threshold the book is never read


def test_entries_are_counted_in_the_risk_day_of_now():
    day = [T0.replace(hour=1), T0.replace(hour=7), T0 - pd.Timedelta(hours=9)]          # the last is yesterday
    assert agent_throttle("a1", "15m", closed=[], entry_times=day, now=T0).entries_today == 2


# ---------------------------------------------------------------------------------------------- the engine
def _engine(tmp_path: Path, pb: PaperBroker, throttle: ThrottleSettings = ON, **kw: Any) -> Engine:
    return Engine(EngineConfig(account_id="icm-demo", broker_name="icm", mode="demo", state_dir=str(tmp_path),
                               owner_user_id=111, **kw), pb, [TrendSpecialist()], {},
                  limits=RiskLimits(throttle=throttle))


def _facts(eng: Engine, agent: TrendSpecialist, now: pd.Timestamp) -> AgentThrottle:
    t = eng._agent_throttle(agent.agent_id, agent.timeframe, now)
    assert t is not None
    return t


def _open_and_stop(eng: Engine, pb: PaperBroker, agent: TrendSpecialist, at: pd.Timestamp, n: int) -> int:
    pb.on_tick(Tick(ts_utc=at, bid=2_000.0, ask=2_000.0))
    prop = Proposal(proposal_id=f"icm-demo-{n}-x", account_id="icm-demo", agent_id=agent.agent_id, side=1, lots=0.1,
                    entry=2_000.0, stop=1_990.0, target=2_030.0, p=0.6, ev_r=0.1, spread_points=0.0, top_features=[])
    eng._execute(prop, agent, 0.1, 1_990.0, 2_030.0, requested=2_000.0, atr_usd=10.0)
    pid = max(eng.open)
    pb.on_tick(Tick(ts_utc=at + pd.Timedelta(minutes=5), bid=1_989.0, ask=1_989.0))
    eng._manage_open(pd.DataFrame())
    eng._flush_closed()
    return pid


def test_a_streak_recorded_before_a_restart_still_refuses_after_it(tmp_path):
    pb, agent = PaperBroker(adverse_slip_points=0.0), TrendSpecialist()
    eng = _engine(tmp_path, pb)
    for i in range(3):
        _open_and_stop(eng, pb, agent, T0 + pd.Timedelta(minutes=10 * i), i)
    assert not eng.open
    now = T0 + pd.Timedelta(minutes=30)
    assert _facts(eng, agent, now).loss_streak == 3
    again = _engine(tmp_path, pb)                                     # restart: a new engine on the same state dir
    t = again._agent_throttle(agent.agent_id, agent.timeframe, now)
    assert t is not None and t.loss_streak == 3 and t.entries_today == 3
    d = again.gate.check(_intent(agent.agent_id), _state(), now, throttle=t)
    assert set(d.reasons) == {"throttle_loss_streak", "throttle_entry_rate"}
    later = now + pd.Timedelta(days=1)                                # cooldown (4 x 1h) and the day both passed
    assert again.gate.check(_intent(agent.agent_id), _state(), later,
                            throttle=again._agent_throttle(agent.agent_id, agent.timeframe, later)).allowed


def test_open_trades_count_toward_the_entry_rate_and_other_accounts_do_not(tmp_path):
    pb, agent = PaperBroker(adverse_slip_points=0.0), TrendSpecialist()
    append_closed_trade(tmp_path, _trade(agent.agent_id, T0, 1.0, pid=900, entry_utc=T0 - pd.Timedelta(hours=1)))
    append_closed_trade(tmp_path, _trade(agent.agent_id, T0, 1.0, pid=901, account="vantage-demo"))
    eng = _engine(tmp_path, pb)
    pb.on_tick(Tick(ts_utc=T0, bid=2_000.0, ask=2_000.0))
    prop = Proposal(proposal_id="icm-demo-o-x", account_id="icm-demo", agent_id=agent.agent_id, side=1, lots=0.1,
                    entry=2_000.0, stop=1_990.0, target=2_030.0, p=0.6, ev_r=0.1, spread_points=0.0, top_features=[])
    eng._execute(prop, agent, 0.1, 1_990.0, 2_030.0, requested=2_000.0, atr_usd=10.0)
    t = eng._agent_throttle(agent.agent_id, agent.timeframe, T0 + pd.Timedelta(minutes=1))
    assert t is not None and t.entries_today == 2


def test_a_winning_shadow_trade_in_the_shared_shadow_book_clears_the_streak(tmp_path):
    pb, agent = PaperBroker(adverse_slip_points=0.0), TrendSpecialist()
    for c in _losses(agent.agent_id, 3, T0):
        append_closed_trade(tmp_path, c)
    book = ShadowBook(tmp_path)
    book.track("trend-v1", T0 - pd.Timedelta(days=1))
    tr = book.open_trade(version="trend-v1", agent_id=agent.agent_id, side=1, bar_ts=T0, entry=2_000.0, atr_usd=10.0,
                         target_atr=1.0, stop_atr=1.0, max_bars=10, p=0.6, timeframe="1h")
    assert tr is not None
    book.on_bar(pd.Series({"ts_utc": T0 + pd.Timedelta(hours=1), "bid_high": 2_020.0, "bid_low": 1_995.0,
                           "ask_high": 2_020.0, "ask_low": 1_995.0, "bid_close": 2_015.0, "ask_close": 2_015.0}), "1h")
    eng = _engine(tmp_path, pb)                                       # not the shadow host: reads the book's file
    now = T0 + pd.Timedelta(minutes=90)
    assert _facts(eng, agent, now).loss_streak == 3                 # the book is not saved yet
    book.save(now)
    t = eng._agent_throttle(agent.agent_id, agent.timeframe, now)
    assert t is not None and t.loss_streak == 0 and t.last_shadow_win_utc == T0 + pd.Timedelta(hours=1)


def test_disabled_the_engine_computes_no_throttle(tmp_path):
    pb = PaperBroker()
    for c in _losses("x", 3, T0):
        append_closed_trade(tmp_path, c)
    eng = _engine(tmp_path, pb, throttle=ThrottleSettings())
    assert eng._agent_throttle("x", "15m", T0) is None


def test_the_engine_state_publishes_the_throttle_and_who_is_blocked(tmp_path):
    pb, agent = PaperBroker(), TrendSpecialist()
    for c in _losses(agent.agent_id, 3, T0):
        append_closed_trade(tmp_path, c)
    pb.on_tick(Tick(ts_utc=T0 + pd.Timedelta(minutes=10), bid=2_000.0, ask=2_000.2))
    eng = _engine(tmp_path, pb)
    eng._last_tick = Tick(ts_utc=T0 + pd.Timedelta(minutes=10), bid=2_000.0, ask=2_000.2)
    eng._write_state()
    th = json.loads((tmp_path / "engine_icm-demo.json").read_text())["throttle"]
    assert th["enabled"] and th["loss_streak"] == 3 and th["cooldown_bars"] == 4
    a = th["agents"][agent.agent_id]
    assert a["loss_streak"] == 3 and a["entries_today"] == 3                      # the three losses were entered today
    assert a["blocked"] == ["throttle_loss_streak", "throttle_entry_rate"]
    assert pd.Timestamp(a["cooldown_until"]) == T0 + pd.Timedelta(hours=4)          # trend decides on 1h
    off = _engine(tmp_path, pb, throttle=ThrottleSettings())
    off._write_state()
    th = json.loads((tmp_path / "engine_icm-demo.json").read_text())["throttle"]
    assert th == {"enabled": False, "loss_streak": 3, "cooldown_bars": 4, "max_entries_per_day_per_agent": 3}


def test_health_warns_and_alerts_while_an_agent_is_throttled(tmp_path):
    ctx = make_ctx(tmp_path)
    engine(tmp_path, 60, throttle={"enabled": False})
    assert health.check_throttle(ctx, "icm-demo") == []
    engine(tmp_path, 60, throttle={"enabled": True, "agents": {"a1": {"blocked": [], "loss_streak": 1,
                                                                      "entries_today": 1}}})
    assert by_name(health.check_throttle(ctx, "icm-demo"))["throttle:icm-demo"].status == "ok"
    engine(tmp_path, 60, throttle={"enabled": True, "agents": {"a1": {"blocked": ["throttle_loss_streak"],
                                                                      "loss_streak": 3, "entries_today": 1}}})
    c = by_name(health.check_throttle(ctx, "icm-demo"))["throttle:icm-demo"]
    assert c.status == "warn" and "a1" in c.reason and "throttle_loss_streak" in c.reason
    assert health._alerting(c.name, c.status)                         # announced on Telegram, like a tripped cap
    assert any(x.name == "throttle:icm-demo" for x in health.run_checks(ctx).checks)


def test_exits_never_consult_the_throttle(tmp_path):
    pb, agent = PaperBroker(adverse_slip_points=0.0), TrendSpecialist()
    for c in _losses(agent.agent_id, 5, T0 - pd.Timedelta(minutes=10)):
        append_closed_trade(tmp_path, c)
    eng = _engine(tmp_path, pb, throttle=ON.model_copy(update={"max_entries_per_day_per_agent": 1}))
    pb.on_tick(Tick(ts_utc=T0, bid=2_000.0, ask=2_000.0))
    prop = Proposal(proposal_id="icm-demo-e-x", account_id="icm-demo", agent_id=agent.agent_id, side=1, lots=0.1,
                    entry=2_000.0, stop=1_990.0, target=2_030.0, p=0.6, ev_r=0.1, spread_points=0.0, top_features=[])
    eng._execute(prop, agent, 0.1, 1_990.0, 2_030.0, requested=2_000.0, atr_usd=10.0)
    (pid,) = eng.open
    calls: list[Any] = []
    real_check, real_throttle = eng.gate.check, eng._agent_throttle

    def spy(*a: Any, **k: Any) -> Any:
        calls.append(a)
        return real_check(*a, **k)

    def spy_t(*a: Any, **k: Any) -> Any:
        calls.append(a)
        return real_throttle(*a, **k)

    eng.gate.check = spy                                              # type: ignore[method-assign]
    eng._agent_throttle = spy_t                                       # type: ignore[method-assign]
    pb.on_tick(Tick(ts_utc=T0 + pd.Timedelta(minutes=5), bid=1_989.0, ask=1_989.0))
    eng._manage_open(pd.DataFrame())
    assert pid not in eng.open and not pb.positions() and calls == []


# ---------------------------------------------------------------------------------------------- replay
def _replay(tmp_path: Path, throttle: ThrottleSettings | None) -> list[tuple[Any, ...]]:
    spec = SPECIALISTS["session_open"](min_body_pct=0.0, asia_range_max_atr_d=99.0)    # permissive, as test_engine
    for c in _losses(spec.agent_id, 3, pd.Timestamp("2025-03-02 22:00", tz="UTC")):    # a streak just before the run
        append_closed_trade(tmp_path, c)
    ticks = synthetic_ticks("2025-03-03", "2025-03-05", ticks_per_minute=1, seed=5)
    limits = RiskLimits() if throttle is None else RiskLimits(throttle=throttle)
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", approval_mode="auto", state_dir=str(tmp_path),
                              owner_user_id=111), PaperBroker(equity=10_000), [spec],
                 {"session_open": ConstantModel(p=0.65)}, limits=limits)
    out: list[tuple[Any, ...]] = []
    for ts, bid, ask in zip(ticks["ts_utc"], ticks["bid"].to_numpy(float), ticks["ask"].to_numpy(float)):
        out += [(d.get("agent"), d.get("action"), d.get("p"), d.get("proposal"))
                for d in eng.on_tick(Tick(ts_utc=pd.Timestamp(ts), bid=float(bid), ask=float(ask)))]
    return out


@pytest.mark.integration
def test_disabled_replay_decisions_are_identical_to_no_throttle_and_enabled_ones_are_refused(tmp_path):
    base = _replay(tmp_path / "base", None)
    off = _replay(tmp_path / "off", ThrottleSettings(enabled=False, loss_streak=1, cooldown_bars=10_000,
                                                     max_entries_per_day_per_agent=1))
    assert any(a == "executed:auto" for _, a, _, _ in base) and off == base
    on = _replay(tmp_path / "on", ThrottleSettings(enabled=True, loss_streak=3, cooldown_bars=10_000,
                                                   max_entries_per_day_per_agent=3))
    assert not any(a == "executed:auto" for _, a, _, _ in on)
    assert any(a == "gate:throttle_loss_streak" for _, a, _, _ in on)
