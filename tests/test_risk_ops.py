"""Ops audit fixes: settings reach the RiskGate, loss caps roll over, the owner re-arm clears a drawdown halt,
data-quality errors block entries, and the phase gates are recorded."""
import json
from typing import Literal

import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.engine import ConstantModel, Engine, EngineConfig
from goldbot.execution.broker import AccountInfo, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops import accounts as acc_mod
from goldbot.ops.run import engine_limits, record_gate_cli
from goldbot.risk import AccountState, Intent, RiskGate, RiskLimits
from goldbot.risk.gate import Stage, new_day
from goldbot.risk.supervisor import Supervisor, SupervisorLimits
from goldbot.specialists import SPECIALISTS
from goldbot.telegram.approvals import ApprovalCenter
from goldbot.telegram.bus import ApprovalBus


@pytest.fixture
def phase_file(tmp_path, monkeypatch):
    f = tmp_path / "phase_state.json"
    monkeypatch.setattr(acc_mod, "PHASE_FILE", f)
    return f


def _acc(mode: Literal["demo", "live"]) -> acc_mod.Account:
    return acc_mod.Account(account_id=f"icm-{mode}", broker="icm", mode=mode, server="s", login=1, terminal_path="t",
                           server_tz="UTC", symbol="XAUUSD", magic_base=260100, enabled=True)


def _tick(ts: str, bid: float = 2400.0, ask: float = 2400.2) -> Tick:
    return Tick(ts_utc=pd.Timestamp(ts, tz="UTC"), bid=bid, ask=ask)


class _Equity(PaperBroker):
    """Paper broker whose equity the test sets."""
    def __init__(self, equity: float = 10_000.0):
        super().__init__(equity=equity)
        self.eq = equity

    def account(self) -> AccountInfo:
        return AccountInfo(login=0, equity=self.eq, balance=self.eq, margin=0.0, margin_free=self.eq, leverage=20,
                           currency="USD", server="paper")


def _engine(tmp_path, broker: PaperBroker, **cfg) -> Engine:
    center = ApprovalCenter({1}, bus=ApprovalBus(tmp_path))
    return Engine(EngineConfig(account_id="x", broker_name="icm", state_dir=str(tmp_path), **cfg), broker,
                  [SPECIALISTS["session_open"]()], {"session_open": ConstantModel()}, center)


def _stage(e: Engine) -> Stage:
    return e.state.stage


def _intent() -> Intent:
    return Intent(agent_id="x", side=1, p=0.6, target_atr=1.5, stop_atr=1.0, atr_usd=4.0, cost_atr=0.1, multiplier=1.0,
                  price=2400.0)


def _state(**kw) -> AccountState:
    base = dict(equity=10_000, balance_closed_hwm=10_000, day_start_equity=10_000, week_start_equity=10_000,
                open_positions=0, margin_used=0, last_tick_age_s=0, spread_points=20)
    return AccountState(**(base | kw))


# ------------------------------------------------------------------ 1. settings -> RiskLimits -> gate
def test_settings_reach_the_gate_with_tiny_live_risk(tmp_path, phase_file):
    s = load_settings()
    demo = engine_limits(s, _acc("demo"))
    assert demo.risk_per_trade == s.risk.risk_per_trade and demo.daily_cap == s.risk.daily_cap
    assert demo.weekly_cap == s.risk.weekly_cap and demo.dd_stage2 == s.risk.drawdown_stage2
    assert demo.max_positions == s.risk.max_positions_per_account and demo.max_spread_points == s.risk.max_spread_points
    assert demo.stale_tick_seconds == s.risk.stale_tick_seconds and demo.min_target_over_cost == s.risk.min_target_over_cost
    assert tuple(demo.multiplier_bounds) == tuple(s.risk.multiplier_bounds)
    # live in the tiny-live phase (and when the phase cannot be read): the smaller risk
    phase_file.write_text(json.dumps({"phase": 3, "gates_passed": ["backtest_to_paper", "paper_to_tiny_live"]}))
    assert engine_limits(s, _acc("live")).risk_per_trade == s.risk.risk_per_trade_tiny_live
    phase_file.write_text("{broken")
    assert engine_limits(s, _acc("live")).risk_per_trade == s.risk.risk_per_trade_tiny_live
    phase_file.write_text(json.dumps({"phase": 4, "gates_passed": ["paper_to_tiny_live", "tiny_live_to_full_size"]}))
    assert engine_limits(s, _acc("live")).risk_per_trade == s.risk.risk_per_trade
    # the engine's gate sizes with what it was given
    eng = Engine(EngineConfig(account_id="x", broker_name="icm", state_dir=str(tmp_path)), PaperBroker(), [], {},
                 limits=RiskLimits.from_settings(s.risk, tiny_live=True))
    full, tiny = RiskGate(demo).check(_intent(), _state()), eng.gate.check(_intent(), _state())
    assert full.allowed and tiny.allowed and tiny.lots < full.lots
    assert eng.gate.limits.risk_per_trade == s.risk.risk_per_trade_tiny_live
    sup = SupervisorLimits.from_settings(s.risk)
    assert sup.daily_cap == s.risk.supervisor_daily_cap and sup.weekly_cap == s.risk.supervisor_weekly_cap


# ------------------------------------------------------------------ 2. daily / weekly caps roll over
def test_new_day_rolls_the_week_only_at_the_week_boundary():
    st = _state(equity=9_000)
    assert new_day(st, pd.Timestamp("2025-03-04 00:01", tz="UTC"), pd.Timestamp("2025-03-03 08:00", tz="UTC"))
    assert st.day_start_equity == 9_000 and st.week_start_equity == 10_000          # Tuesday: day only
    assert not new_day(st, pd.Timestamp("2025-03-04 23:59", tz="UTC"), pd.Timestamp("2025-03-04 00:01", tz="UTC"))
    st.equity = 8_800
    assert new_day(st, pd.Timestamp("2025-03-09 22:00", tz="UTC"), pd.Timestamp("2025-03-07 20:00", tz="UTC"))
    assert st.day_start_equity == st.week_start_equity == 8_800                     # Sunday open: new week


def test_engine_rolls_caps_across_day_and_week_and_survives_restart(tmp_path):
    b = _Equity(10_000)
    eng = _engine(tmp_path, b)
    eng.on_tick(_tick("2025-03-03 08:00"))                       # Monday
    assert eng.state.day_start_equity == eng.state.week_start_equity == 10_000
    b.eq = 9_700
    eng.on_tick(_tick("2025-03-03 15:00"))
    eng._refresh_account(_tick("2025-03-03 15:00"))
    d = eng.gate.check(_intent(), eng.state)
    assert "daily_cap" in d.reasons                              # 3% down on the day
    eng.on_tick(_tick("2025-03-04 00:05"))                       # Tuesday: the daily cap rolls, the week does not
    assert eng.state.day_start_equity == 9_700 and eng.state.week_start_equity == 10_000
    eng._refresh_account(_tick("2025-03-04 00:05"))
    assert "daily_cap" not in eng.gate.check(_intent(), eng.state).reasons
    # restart on the same day: no second reset, no lost loss
    b.eq = 9_600
    again = _engine(tmp_path, b)
    assert again.state.day_start_equity == 9_700 and again.state.week_start_equity == 10_000
    again.on_tick(_tick("2025-03-04 10:00"))
    assert again.state.day_start_equity == 9_700
    # restart after downtime across the weekend: both roll, nothing skipped
    b.eq = 9_400
    later = _engine(tmp_path, b)
    later.on_tick(_tick("2025-03-10 01:00"))
    assert later.state.day_start_equity == later.state.week_start_equity == 9_400
    # the supervisor's combined caps sum the engines' rolled starts
    later._refresh_account(_tick("2025-03-10 01:00"))
    later._write_state()
    st = Supervisor(tmp_path).evaluate()
    assert st["day_loss"] == 0 and st["week_loss"] == 0


# ------------------------------------------------------------------ 3. owner re-arm clears the drawdown halt
def test_owner_rearm_on_the_bus_clears_the_drawdown_halt(tmp_path):
    b = _Equity(10_000)
    eng = _engine(tmp_path, b)
    eng.on_tick(_tick("2025-03-03 08:00"))
    eng._refresh_account(_tick("2025-03-03 08:00"))
    b.eq = 8_700                                                 # 13% below the high-water mark
    eng._refresh_account(_tick("2025-03-03 09:00"))
    assert _stage(eng) == Stage.HALTED
    eng._write_state()
    bus = ApprovalBus(tmp_path)
    bus.set_halt(True, by="telegram:1")
    bus.set_halt(False, by="dashboard:o@x.io")                    # clearing the owner halt alone is not a re-arm
    eng.on_tick(_tick("2025-03-03 09:01"))
    assert _stage(eng) == Stage.HALTED
    (tmp_path / "control.json").write_text("{not json")           # unreadable control: never a re-arm
    eng.on_tick(_tick("2025-03-03 09:02"))
    assert _stage(eng) == Stage.HALTED
    restarted = _engine(tmp_path, b)                              # a restart keeps the halt
    assert _stage(restarted) == Stage.HALTED
    c = bus.owner_rearm(by="dashboard:o@x.io")                   # the API calls this after owner role + TOTP
    assert not c.halted and c.rearm_id
    restarted.on_tick(_tick("2025-03-03 09:03"))
    assert _stage(restarted) == Stage.NORMAL and restarted.state.balance_closed_hwm == 8_700
    assert any(d.get("action") == "rearm" for d in restarted.decisions)
    # the same re-arm is not replayed against a later halt, nor after another restart
    b.eq = 7_600
    restarted._refresh_account(_tick("2025-03-03 10:00"))
    assert _stage(restarted) == Stage.HALTED
    restarted.on_tick(_tick("2025-03-03 10:01"))
    assert _stage(restarted) == Stage.HALTED
    bus.set_halt(True, by="telegram:1")
    assert bus.control().rearm_id == c.rearm_id                   # a halt keeps the last re-arm id
    assert _engine(tmp_path, b).state.stage == Stage.HALTED


def test_first_start_ignores_an_old_rearm(tmp_path):
    ApprovalBus(tmp_path).owner_rearm(by="dashboard:o@x.io")
    eng = _engine(tmp_path, _Equity())
    eng.state.stage = Stage.HALTED
    eng.on_tick(_tick("2025-03-03 08:00"))
    assert _stage(eng) == Stage.HALTED


# ------------------------------------------------------------------ 4. data-quality errors
def test_crossed_and_out_of_order_ticks_set_dq_error_until_a_clean_bar(tmp_path):
    eng = _engine(tmp_path, _Equity())
    eng.on_tick(_tick("2025-03-03 08:00:00"))
    eng._refresh_account(_tick("2025-03-03 08:00:00"))
    assert not eng.state.dq_error
    eng.on_tick(_tick("2025-03-03 08:00:05", bid=2400.5, ask=2400.1))            # bid > ask
    eng._refresh_account(_tick("2025-03-03 08:00:06"))
    assert eng.state.dq_error and "data_quality_error" in eng.gate.check(_intent(), eng.state).reasons
    assert all(t.bid <= t.ask for t in eng.ticks)                                 # dropped, never in the bars
    eng.on_tick(_tick("2025-03-03 08:20"))       # the 08:15 close is decided with the error -> still blocked
    eng._refresh_account(_tick("2025-03-03 08:20"))
    assert eng.state.dq_error
    eng.on_tick(_tick("2025-03-03 08:31"))       # next close: clean
    eng._refresh_account(_tick("2025-03-03 08:31"))
    assert not eng.state.dq_error
    eng.on_tick(_tick("2025-03-03 08:30"))       # a tick older than the previous one
    eng._refresh_account(_tick("2025-03-03 08:31"))
    assert eng.state.dq_error
    eng._write_state()
    st = json.loads((tmp_path / "engine_x.json").read_text())
    assert st["dq_error"] and st["dq_checks"] == ["non_monotonic"]


def test_bar_checks_and_stale_feed_set_dq_error(tmp_path):
    eng = _engine(tmp_path, _Equity())
    eng.on_tick(_tick("2025-03-03 08:00"))
    for m in range(5):
        eng.on_tick(_tick(f"2025-03-03 08:0{m}:30"))
    eng._rebuild_bars()
    assert len(eng.bars_1m) >= 3 and not eng._dq_pending
    dup = eng.bars_1m.tail(1).copy()                         # a completed minute that is not after the last one
    eng._check_new_bars(dup)
    assert {e.check for e in eng._dq_pending} >= {"duplicate_ts"}
    # stale feed in an open session, measured on the wall clock in production
    live = _engine(tmp_path / "live", _Equity(), live_clock=True)
    old = _tick("2025-03-03 08:00")
    live._now = lambda tick: pd.Timestamp("2025-03-03 08:05", tz="UTC")  # type: ignore[method-assign]  # wall clock +5 min
    live.on_tick(old)
    live._refresh_account(old)
    assert live.state.dq_error and "stale_data" in live.gate.check(_intent(), live.state).reasons


# ------------------------------------------------------------------ 5. phase gates
def test_record_gate_appends_atomically_and_refuses_bad_names(phase_file, tmp_path, capsys):
    with pytest.raises(ValueError, match="unknown gate"):
        acc_mod.record_gate("paper_to_live", "x")
    with pytest.raises(ValueError, match="must be recorded before"):
        acc_mod.record_gate("paper_to_tiny_live", "x")
    assert not phase_file.exists()
    acc_mod.record_gate("foundation_to_backtest", "phase 0 checklist done")
    report = tmp_path / "paper_report.html"
    report.write_text("<p>six months of paper</p>")
    assert record_gate_cli(["backtest_to_paper", "--evidence", "walk-forward + DSR pass, trial 41"]) == 0
    assert record_gate_cli(["paper_to_tiny_live", "--evidence", str(report)]) == 0
    assert record_gate_cli(["paper_to_tiny_live", "--evidence", "again"]) == 1            # already recorded
    assert record_gate_cli(["nonsense", "--evidence", "x"]) == 1
    assert "refused" in capsys.readouterr().out
    st = json.loads(phase_file.read_text())
    assert st["gates_passed"] == ["foundation_to_backtest", "backtest_to_paper", "paper_to_tiny_live"]
    assert st["phase"] == 3 and len(st["gate_log"]) == 3
    last = st["gate_log"][-1]
    assert last["gate"] == "paper_to_tiny_live" and pd.Timestamp(last["ts_utc"]).tzinfo is not None
    assert len(last["evidence"]["sha256"]) == 64 and last["evidence"]["path"].endswith("paper_report.html")
    assert st["gate_log"][1]["evidence"] == {"text": "walk-forward + DSR pass, trial 41"}
    assert not list(phase_file.parent.glob("*.tmp"))
    assert "paper_to_tiny_live" in acc_mod.phase_state()["gates_passed"]           # what unlock_live reads
