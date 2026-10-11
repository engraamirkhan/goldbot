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
from goldbot.data.resample import ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine import ConstantModel, Engine, EngineConfig
from goldbot.engine.shadow import ShadowBook, ShadowTrade, VersionBook
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops import health
from goldbot.ops.gates_phase import ClosedTrade, append_closed_trade
from goldbot.risk import AccountState, Intent, RiskGate, RiskLimits
from goldbot.risk.throttle import AgentThrottle, agent_throttle, is_full_loss, latest_shadow_win
from goldbot.specialists import SPECIALISTS
from goldbot.specialists.session_open import SessionOpenSpecialist
from goldbot.specialists.trend import TrendSpecialist
from goldbot.telegram.approvals import ApprovalCenter, Proposal
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
    assert is_full_loss(legacy.model_copy(update={"exit_reason": "stop_out"}))      # the broker's margin stop-out
    assert not is_full_loss(legacy.model_copy(update={"exit_reason": "time_exit"}))
    # no R and no stop on the record: a broker_close loss cannot be measured against the stop, so it is not counted
    assert not is_full_loss(legacy.model_copy(update={"exit_reason": "broker_close"}))


def test_the_streak_counts_trailing_full_losses_of_that_agent_only():
    closed = (_losses("a1", 2, T0 - pd.Timedelta(hours=5)) + [_trade("a1", T0 - pd.Timedelta(hours=3), 1.5, pid=10)]
              + _losses("a1", 3, T0, start_pid=20) + _losses("other", 4, T0, start_pid=40))
    t = agent_throttle("a1", "15m", closed=closed, entry_times=[], now=T0)
    assert t.loss_streak == 3 and t.last_loss_utc == T0
    partial = closed + [_trade("a1", T0 + pd.Timedelta(minutes=5), -0.4, pid=30)]       # a small loss ends it
    assert agent_throttle("a1", "15m", closed=partial, entry_times=[], now=T0).loss_streak == 0


def _sh(entry_ts: pd.Timestamp, *, barrier: str = "target", ret: float = 0.005, version: str = "v1",
        taken: bool = True, agent: str = "a1") -> ShadowTrade:
    """A closed shadow trade: entry 2,000, stop 1,990 (R = ret x 200)."""
    return ShadowTrade(version=version, agent_id=agent, side=1, entry_ts=entry_ts, entry=2_000.0, stop=1_990.0,
                       target=2_020.0, max_bars=10, timeframe="15m", p=0.6, taken=taken,
                       exit_ts=entry_ts + pd.Timedelta(hours=1), exit=2_000.0 * (1 + ret), barrier=barrier, ret=ret)


def _ask(win: ShadowTrade | None, asked: list[int]) -> Any:
    def f() -> ShadowTrade | None:
        asked.append(1)
        return win
    return f


def test_a_qualifying_shadow_win_entered_after_the_last_loss_clears_the_streak():
    closed, asked = _losses("a1", 3, T0), list[int]()                 # losses exit T0 - 2h, T0 - 1h, T0
    w = _sh(T0 + pd.Timedelta(minutes=15))
    t = agent_throttle("a1", "15m", closed=closed, entry_times=[], now=T0, shadow_win=_ask(w, asked),
                       consult_shadow_at=3)
    assert t.loss_streak == 0 and t.last_shadow_win_utc == T0 + pd.Timedelta(minutes=75) and asked


@pytest.mark.parametrize("entry,streak", [(-3 * 60, 3),           # entered before every loss: clears nothing
                                          (-90, 2),               # between losses: those after it start a new streak
                                          (0, 1)])                # at the last loss's exit: entry_ts must be later
def test_a_shadow_win_clears_only_the_losses_that_exited_before_its_entry(entry, streak):
    w = _sh(T0 + pd.Timedelta(minutes=entry))
    t = agent_throttle("a1", "15m", closed=_losses("a1", 3, T0), entry_times=[], now=T0, shadow_win=lambda: w,
                       consult_shadow_at=1)
    assert t.loss_streak == streak


def test_the_shadow_book_is_read_only_while_a_streak_is_at_the_threshold_and_cooling_down():
    w, asked = _sh(T0 + pd.Timedelta(minutes=15)), list[int]()
    agent_throttle("a1", "15m", closed=_losses("a1", 2, T0), entry_times=[], now=T0, shadow_win=_ask(w, asked),
                   consult_shadow_at=3)
    assert not asked                                                  # below the threshold
    after = agent_throttle("a1", "15m", closed=_losses("a1", 3, T0), entry_times=[], now=T0 + pd.Timedelta(hours=2),
                           shadow_win=_ask(w, asked), consult_shadow_at=3, cooldown=pd.Timedelta(hours=1))
    assert not asked and after.loss_streak == 3                       # cooldown passed: the count persists, unread
    agent_throttle("a1", "15m", closed=_losses("a1", 3, T0), entry_times=[], now=T0 + pd.Timedelta(minutes=30),
                   shadow_win=_ask(w, asked), consult_shadow_at=3, cooldown=pd.Timedelta(hours=1))
    assert asked                                                      # cooling down: read


def test_only_a_real_win_of_the_agents_live_version_qualifies(tmp_path):
    book = ShadowBook(tmp_path)
    e = T0 + pd.Timedelta(minutes=15)
    book.books["v1"] = VersionBook(version="v1", started_utc=T0 - pd.Timedelta(days=1), closed=[
        _sh(e, barrier="time", ret=0.0005),                           # a scratch: R 0.1
        _sh(e + pd.Timedelta(minutes=15), taken=False),               # a candidate the model would not take
        _sh(e + pd.Timedelta(minutes=30), agent="other")])
    book.books["v2"] = VersionBook(version="v2", started_utc=T0 - pd.Timedelta(days=1),
                                   closed=[_sh(e + pd.Timedelta(hours=1), version="v2")])   # a challenger's win
    assert latest_shadow_win(book, "a1", "v1") is None
    assert latest_shadow_win(book, "a1", None) is None                # no live version known: nothing clears
    book.books["v1"].closed.append(_sh(e + pd.Timedelta(minutes=45), barrier="time", ret=0.00125))   # R 0.25
    assert latest_shadow_win(book, "a1", "v1").entry_ts == e + pd.Timedelta(minutes=45)   # type: ignore[union-attr]
    book.books["v1"].closed.append(_sh(e - pd.Timedelta(hours=2)))   # an earlier target hit: the later entry wins
    assert latest_shadow_win(book, "a1", "v1").entry_ts == e + pd.Timedelta(minutes=45)   # type: ignore[union-attr]
    assert latest_shadow_win(book, "a1", "v2").version == "v2"       # type: ignore[union-attr]


def test_entries_are_counted_in_the_risk_day_of_now():
    day = [T0.replace(hour=1), T0.replace(hour=7), T0 - pd.Timedelta(hours=9)]          # the last is yesterday
    assert agent_throttle("a1", "15m", closed=[], entry_times=day, now=T0).entries_today == 2


# ---------------------------------------------------------------------------------------------- the engine
def _engine(tmp_path: Path, pb: PaperBroker, throttle: ThrottleSettings = ON,
            live_versions: dict[str, str] | None = None, **kw: Any) -> Engine:
    return Engine(EngineConfig(account_id="icm-demo", broker_name="icm", mode="demo", state_dir=str(tmp_path),
                               owner_user_id=111, **kw), pb, [TrendSpecialist()], {},
                  limits=RiskLimits(throttle=throttle), live_versions=live_versions)


def _facts(eng: Engine, agent: TrendSpecialist, now: pd.Timestamp) -> AgentThrottle:
    t = eng._agent_throttle(agent.agent_id, agent.timeframe, now)
    assert t is not None
    return t


def _open_and_stop(eng: Engine, pb: PaperBroker, agent: Any, at: pd.Timestamp, n: int, px: float = 2_000.0) -> int:
    """Open a position for `agent` and let the broker's stop close it, found by the bar-close sweep. Nothing is
    flushed: as in `on_bar_close`, the close is only queued (written after the decisions, by `on_tick`)."""
    pb.on_tick(Tick(ts_utc=at, bid=px, ask=px))
    prop = Proposal(proposal_id=f"icm-demo-{n}-x", account_id="icm-demo", agent_id=agent.agent_id, side=1, lots=0.1,
                    entry=px, stop=px - 10, target=px + 30, p=0.6, ev_r=0.1, spread_points=0.0, top_features=[])
    eng._execute(prop, agent, 0.1, px - 10, px + 30, requested=px, atr_usd=10.0)
    pid = max(eng.open)
    pb.on_tick(Tick(ts_utc=at + pd.Timedelta(minutes=5), bid=px - 11, ask=px - 11))
    eng._manage_open(pd.DataFrame())
    return pid


def test_a_streak_recorded_before_a_restart_still_refuses_after_it(tmp_path):
    pb, agent = PaperBroker(adverse_slip_points=0.0), TrendSpecialist()
    eng = _engine(tmp_path, pb)
    for i in range(3):
        _open_and_stop(eng, pb, agent, T0 + pd.Timedelta(minutes=10 * i), i)
    assert not eng.open and len(eng._closes) == 1                     # each new order wrote the closes before it
    now = T0 + pd.Timedelta(minutes=30)
    queued = _facts(eng, agent, now)                                  # before the third record is written
    assert queued.loss_streak == 3 and queued.provisional_losses == 1 and queued.entries_today == 3
    eng._flush_closed()
    written = _facts(eng, agent, now)
    assert written.loss_streak == 3 and written.provisional_losses == 0 and written.entries_today == 3
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


def _shadow_target_hit(book: ShadowBook, version: str, agent_id: str, bar_ts: pd.Timestamp) -> None:
    book.track(version, T0 - pd.Timedelta(days=1))
    tr = book.open_trade(version=version, agent_id=agent_id, side=1, bar_ts=bar_ts, entry=2_000.0, atr_usd=10.0,
                         target_atr=1.0, stop_atr=1.0, max_bars=10, p=0.6, timeframe="1h")
    assert tr is not None
    book.on_bar(pd.Series({"ts_utc": bar_ts + pd.Timedelta(hours=1), "bid_high": 2_020.0, "bid_low": 1_995.0,
                           "ask_high": 2_020.0, "ask_low": 1_995.0, "bid_close": 2_015.0, "ask_close": 2_015.0}), "1h")


def test_a_win_of_the_live_version_in_the_shared_shadow_book_clears_the_streak(tmp_path, monkeypatch):
    import goldbot.engine.runner as runner
    pb, agent = PaperBroker(adverse_slip_points=0.0), TrendSpecialist()
    for c in _losses(agent.agent_id, 3, T0):
        append_closed_trade(tmp_path, c)
    book = ShadowBook(tmp_path)
    _shadow_target_hit(book, "trend-v2", agent.agent_id, T0 + pd.Timedelta(minutes=15))   # a challenger's win
    _shadow_target_hit(book, "trend-v1", agent.agent_id, T0 + pd.Timedelta(minutes=15))
    loads: list[int] = []

    class Counting(ShadowBook):
        def __init__(self, d: Any) -> None:
            loads.append(1)
            super().__init__(d)

    monkeypatch.setattr(runner, "ShadowBook", Counting)
    eng = _engine(tmp_path, pb, live_versions={agent.agent_id: "trend-v1"})   # not the host: reads the book's file
    now = T0 + pd.Timedelta(minutes=90)
    assert _facts(eng, agent, now).loss_streak == 3                 # the book is not saved yet
    book.books.pop("trend-v1")
    book.save(now)
    assert _facts(eng, agent, now).loss_streak == 3                 # only the challenger won: nothing clears
    assert _facts(eng, agent, now).loss_streak == 3 and len(loads) == 1   # unchanged file: read once (mtime cache)
    _shadow_target_hit(book, "trend-v1", agent.agent_id, T0 + pd.Timedelta(minutes=15))
    book.save(now)
    t = _facts(eng, agent, now)
    assert t.loss_streak == 0 and t.last_shadow_win_utc == T0 + pd.Timedelta(minutes=75) and len(loads) == 2
    unknown = _engine(tmp_path, pb)                                   # no live version known
    assert _facts(unknown, agent, now).loss_streak == 3


def test_disabled_the_engine_computes_no_throttle(tmp_path):
    pb = PaperBroker()
    for c in _losses("x", 3, T0):
        append_closed_trade(tmp_path, c)
    eng = _engine(tmp_path, pb, throttle=ThrottleSettings())
    assert eng._agent_throttle("x", "15m", T0) is None


class _EveryBar(SessionOpenSpecialist):
    """A 15m agent with a long candidate on every bar, so the bar close always reaches the gate for it."""

    def candidates(self, mid_bars: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame({"idx": [len(mid_bars) - 1], "side": [1]})


@pytest.mark.parametrize("mode", ["auto", "propose"])
@pytest.mark.parametrize("enabled", [False, True])
def test_the_stop_out_just_found_by_the_bar_close_counts_at_that_closes_decision(tmp_path, mode, enabled):
    # trading-safety review HIGH-1: the sweep in on_bar_close queues the broker's stop-out and the record is written
    # only after the decisions (on_tick's flush); the third loss must already refuse the decision on that bar close
    ticks = synthetic_ticks("2025-03-03", "2025-03-05", ticks_per_minute=1, seed=5)
    last, px = pd.Timestamp(ticks["ts_utc"].iloc[-1]), float(ticks["bid"].iloc[-1])
    agent, pb, center = _EveryBar(), PaperBroker(equity=10_000, adverse_slip_points=0.0), ApprovalCenter({111})
    for c in _losses(agent.agent_id, 2, last - pd.Timedelta(hours=2), start_pid=100):
        append_closed_trade(tmp_path, c)
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", approval_mode=mode, state_dir=str(tmp_path),
                              owner_user_id=111), pb, [agent], {agent.family: ConstantModel(p=0.9)}, center,
                 limits=RiskLimits(throttle=ON.model_copy(update={"enabled": enabled,
                                                                  "max_entries_per_day_per_agent": 50})))
    eng.bars_1m = ticks_to_1m(ticks)
    pb.on_tick(Tick(ts_utc=last - pd.Timedelta(minutes=6), bid=px, ask=px))
    prop = Proposal(proposal_id="icm-demo-s-x", account_id="icm-demo", agent_id=agent.agent_id, side=1, lots=0.1,
                    entry=px, stop=px - 10, target=px + 30, p=0.6, ev_r=0.1, spread_points=0.0, top_features=[])
    eng._execute(prop, agent, 0.1, px - 10, px + 30, requested=px, atr_usd=10.0)
    (pid,) = eng.open
    pb.on_tick(Tick(ts_utc=last - pd.Timedelta(minutes=3), bid=px - 11, ask=px - 11))    # the broker's SL fills
    assert not pb.positions() and pid in eng.open                     # the engine has not seen it yet
    tick = Tick(ts_utc=last, bid=px, ask=px + 0.2)
    pb.on_tick(tick)
    decisions = eng.on_bar_close((last + pd.Timedelta(minutes=15)).floor("15min"), tick)
    assert pid not in eng.open and pid in eng._recorded               # swept at this close, written at its end
    mine = [d["action"] for d in decisions if d.get("agent") == agent.agent_id]
    if enabled:
        assert mine == ["gate:throttle_loss_streak"], mine
    else:                                                             # the control: the same close trades when off
        assert mine == ["executed:auto" if mode == "auto" else "proposed"], mine


def test_a_close_whose_record_is_given_up_still_counts_as_a_loss(tmp_path):
    pb, agent = PaperBroker(adverse_slip_points=0.0), TrendSpecialist()
    for c in _losses(agent.agent_id, 2, T0 - pd.Timedelta(minutes=30), start_pid=100):
        append_closed_trade(tmp_path, c)
    eng = _engine(tmp_path, pb, closed_record_retries=1)

    def broken(*a: Any, **k: Any) -> None:
        raise OSError("disk full")

    eng._record_closed = broken                                       # type: ignore[method-assign]
    pid = _open_and_stop(eng, pb, agent, T0, 0)
    eng._flush_closed()
    assert eng._records_lost == [pid] and not eng._closes
    t = _facts(eng, agent, T0 + pd.Timedelta(minutes=10))
    assert t.loss_streak == 3 and t.provisional_losses == 1           # fails closed: the lost close is a full loss


def test_an_error_deriving_one_agents_facts_refuses_only_that_agent_and_the_state_reports_errors(tmp_path):
    pb, agent = PaperBroker(), TrendSpecialist()
    eng = _engine(tmp_path, pb)
    eng._throttle_closed = None                                       # type: ignore[assignment]
    t = _facts(eng, agent, T0)
    assert t.unknown and eng.gate.check(_intent(), _state(), T0, throttle=t).reasons == ["throttle_unknown"]
    assert eng.gate.check(_intent(), _state(), T0, throttle=AgentThrottle(agent_id="b", timeframe="15m")).allowed
    eng._throttle_closed = []

    def boom(*a: Any) -> Any:
        raise RuntimeError("x")

    eng.gate.throttle_cooldown_until = boom                           # type: ignore[method-assign,assignment]
    eng._write_state()                                                # the state write itself never fails on it
    th = json.loads((tmp_path / "engine_icm-demo.json").read_text())["throttle"]
    assert th["error"] == "RuntimeError" and "agents" not in th


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


def _agent(blocked: list[str], until: str | None = "2026-03-03T12:00:00+00:00", streak: int = 3) -> dict[str, Any]:
    return {"blocked": blocked, "loss_streak": streak, "entries_today": 1, "cooldown_until": until}


def test_health_has_one_check_per_agent_so_each_newly_throttled_agent_alerts(tmp_path):
    ctx = make_ctx(tmp_path)
    engine(tmp_path, 60, throttle={"enabled": False})
    assert health.check_throttle(ctx, "icm-demo") == []
    engine(tmp_path, 60, throttle={"enabled": True, "agents": {"a1": _agent([], streak=1), "a2": _agent([], None, 0)}})
    out = by_name(health.check_throttle(ctx, "icm-demo"))
    assert set(out) == {"throttle:icm-demo:a1", "throttle:icm-demo:a2"} and {c.status for c in out.values()} == {"ok"}
    engine(tmp_path, 60, throttle={"enabled": True, "agents": {"a1": _agent(["throttle_loss_streak"]),
                                                                "a2": _agent(["throttle_entry_rate"], None, 0)}})
    out = by_name(health.check_throttle(ctx, "icm-demo"))
    a1, a2 = out["throttle:icm-demo:a1"], out["throttle:icm-demo:a2"]
    assert a1.status == "warn" and "throttle_loss_streak" in a1.reason and "2026-03-03T12:00" in a1.reason
    assert a2.status == "warn" and "throttle_entry_rate" in a2.reason       # a rate block needs no cooldown
    assert health._alerting(a1.name, a1.status) and health._alerting(a2.name, a2.status)   # each told on Telegram
    assert {"throttle:icm-demo:a1", "throttle:icm-demo:a2"} <= {x.name for x in health.run_checks(ctx).checks}


@pytest.mark.parametrize("throttle", [
    {"enabled": True, "agents": {"a1": _agent(["throttle_loss_streak"], None)}},   # a streak block that cannot lift
    {"enabled": True, "agents": {"a1": _agent(["throttle_unknown"], None, 0)}},
    {"enabled": True, "error": "RuntimeError"}])
def test_health_fails_when_a_throttle_block_cannot_lift_or_the_state_is_unknown(tmp_path, throttle):
    engine(tmp_path, 60, throttle=throttle)
    (c,) = health.check_throttle(make_ctx(tmp_path), "icm-demo")
    assert c.status == "fail"


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
