"""Approvals across processes: engine -> bus -> dashboard/Telegram -> bus -> engine, the owner halt, and what the
Telegram service sends."""
import json
import time

import pandas as pd
import pytest

from goldbot.engine import ConstantModel, Engine, EngineConfig
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker
from goldbot.risk import AccountState, Intent, RiskGate
from goldbot.specialists import SPECIALISTS
from goldbot.telegram.approvals import ApprovalCenter, Outcome, Proposal
from goldbot.telegram.bus import ApprovalBus
from goldbot.telegram.outbox import MAX_MESSAGE, Outbox


def _p(pid: str, window_s: int = 90, created: float | None = None) -> Proposal:
    return Proposal(proposal_id=pid, account_id="icm-demo", agent_id="session_open-g0-x", side=1, lots=0.1, entry=2400.0,
                    stop=2396.0, target=2406.0, p=0.6, ev_r=0.3, spread_points=20.0, top_features=[("a", 0.1)],
                    window_s=window_s, created=created if created is not None else time.time())


def test_engine_proposal_is_decided_from_another_process(tmp_path):
    decided: list[Proposal] = []
    engine = ApprovalCenter({1}, bus=ApprovalBus(tmp_path), on_decision=decided.append)
    engine.propose(_p("a"))
    engine.propose(_p("b"))
    dashboard = ApprovalBus(tmp_path)                     # a separate process sees what the engine published
    assert [p.proposal_id for p in dashboard.pending()] == ["a", "b"]
    with pytest.raises(ValueError):
        dashboard.submit("a", False, None, by="dashboard:x")          # a rejection needs a reason code
    dashboard.submit("a", True, None, by="telegram:42")
    with pytest.raises(KeyError):
        dashboard.submit("a", False, "cost", by="dashboard:x")        # first decision wins
    with pytest.raises(KeyError):
        dashboard.submit("zzz", True, None, by="dashboard:x")
    assert [p.proposal_id for p in dashboard.pending()] == ["b"]   # decided ones leave the list at once
    assert engine.poll_bus()[0].proposal_id == "a"
    assert decided[0].outcome == Outcome.APPROVED and decided[0].decided_via == "telegram:42"
    assert dashboard.outcome("a") == "APPROVED" and "a" not in engine.pending
    assert not (tmp_path / "approvals" / "pending" / "a.json").exists()
    assert engine.poll_bus() == []                                 # nothing new


def test_expiry_wins_over_a_late_decision(tmp_path):
    engine = ApprovalCenter({1}, bus=ApprovalBus(tmp_path))
    engine.propose(_p("old", window_s=90, created=time.time() - 120))
    bus = ApprovalBus(tmp_path)
    assert bus.pending() == []
    with pytest.raises(KeyError):
        bus.submit("old", True, None, by="dashboard:x")
    assert [p.outcome for p in engine.sweep_expired()] == [Outcome.EXPIRED]
    assert bus.outcome("old") == "EXPIRED_UNAPPROVED"


def test_owner_halt_blocks_entries_and_fails_closed(tmp_path):
    bus = ApprovalBus(tmp_path)
    assert bus.control().halted is False
    bus.set_halt(True, by="telegram:42", reason="fomc")
    c = ApprovalBus(tmp_path).control()
    assert c.halted and c.by == "telegram:42" and c.reason == "fomc"
    (tmp_path / "control.json").write_text("{not json")
    assert bus.control().halted is True                           # unreadable -> no new entries
    st = AccountState(equity=10_000, balance_closed_hwm=10_000, day_start_equity=10_000, week_start_equity=10_000,
                      open_positions=0, margin_used=0, last_tick_age_s=0, spread_points=20, owner_halt=True)
    d = RiskGate().check(Intent(agent_id="x", side=1, p=0.6, target_atr=1.5, stop_atr=1.0, atr_usd=4.0, cost_atr=0.1,
                                multiplier=1.0, price=2400.0), st)
    assert not d.allowed and "owner_halt" in d.reasons


def test_engine_reads_the_supervisor_and_owner_halts(tmp_path):
    spec = SPECIALISTS["session_open"]()
    center = ApprovalCenter({1}, bus=ApprovalBus(tmp_path))
    broker = PaperBroker(equity=10_000)
    tick = Tick(ts_utc=pd.Timestamp("2025-03-03 08:00", tz="UTC"), bid=2400.0, ask=2400.2)
    broker.on_tick(tick)
    eng = Engine(EngineConfig(account_id="x", broker_name="icm", state_dir=str(tmp_path), halt_checks=True), broker,
                 [spec], {"session_open": ConstantModel()}, center)
    eng._refresh_account(tick)
    assert eng.state.supervisor_halt                               # no supervisor heartbeat -> no entries
    (tmp_path / "supervisor.json").write_text(json.dumps({"ts": time.time(), "halt": False, "reasons": []}))
    eng._refresh_account(tick)
    assert not eng.state.supervisor_halt and not eng.state.owner_halt
    ApprovalBus(tmp_path).set_halt(True, by="dashboard:o@x.io")
    eng._refresh_account(tick)
    assert eng.state.owner_halt
    # without halt checks (tests, backtests) neither flag is read
    plain = Engine(EngineConfig(account_id="y", broker_name="icm", state_dir=str(tmp_path)), broker, [spec], {})
    plain._refresh_account(tick)
    assert not plain.state.supervisor_halt and not plain.state.owner_halt


def test_outbox_sends_each_proposal_once_and_reports_outcomes(tmp_path):
    engine = ApprovalCenter({1}, bus=ApprovalBus(tmp_path))
    engine.propose(_p("a"))
    engine.propose(_p("b"))
    ob = Outbox(tmp_path)
    assert [p.proposal_id for p in ob.unsent_proposals()] == ["a", "b"]
    ob.mark_sent("a", [(42, 1)])
    ob.mark_sent("b", [(42, 2)])
    restarted = Outbox(tmp_path)                                   # progress survives a restart
    assert restarted.unsent_proposals() == [] and restarted.finished() == []
    ApprovalBus(tmp_path).submit("a", False, "news", by="dashboard:o@x.io")
    engine.poll_bus()
    assert restarted.finished() == [("a", "REJECTED", [(42, 1)])]
    (tmp_path / "approvals" / "pending" / "b.json").unlink()      # engine restarted before b was decided
    assert restarted.finished() == [("b", "EXPIRED_UNAPPROVED", [(42, 2)])]
    assert restarted.finished() == []


def test_outbox_delivers_new_agent_reports_without_replaying_history(tmp_path):
    log = tmp_path / "agent_runs.jsonl"
    log.write_text(json.dumps({"role": "risk_officer", "status": "ok", "cost_usd": 0.1}) + "\n")
    ob = Outbox(tmp_path)
    assert ob.new_reports() == []                                   # first start: history is not replayed
    reports = tmp_path / "agent_reports" / "data_steward"
    reports.mkdir(parents=True)
    (reports / "r.md").write_text("<!-- data_steward meta -->\n**3-line summary**\nall feeds healthy\n")
    (tmp_path / "long.md").write_text("x" * 10_000)
    outside = tmp_path.parent / "outside.md"
    outside.write_text("secret")
    rows = [{"role": "data_steward", "status": "ok", "cost_usd": 0.05, "report_path": str(reports / "r.md")},
            {"role": "journal_coach", "status": "monthly_cap", "cost_usd": 0.0, "detail": "monthly agent budget 40.00 USD used up"},
            {"role": "improvement_agent", "status": "ok", "cost_usd": 1.2, "report_path": str(tmp_path / "long.md")},
            {"role": "risk_officer", "status": "ok", "cost_usd": 0.2, "report_path": str(outside)}]
    with log.open("a") as fh:
        fh.writelines(json.dumps(r) + "\n" for r in rows)
    msgs = Outbox(tmp_path).new_reports()
    assert msgs[0].startswith("📋 data steward · ok · $0.05") and "all feeds healthy" in msgs[0] and "<!--" not in msgs[0]
    assert "monthly agent budget" in msgs[1]
    assert len(msgs[2]) <= MAX_MESSAGE and msgs[2].endswith("full report on the dashboard (Agents)")
    assert "secret" not in msgs[3]                                  # only reports inside the state dir are read
    assert Outbox(tmp_path).new_reports() == []


def test_engine_keeps_one_position_or_proposal_per_agent(tmp_path):
    from goldbot.engine.runner import OpenTrade
    spec = SPECIALISTS["session_open"]()
    eng = Engine(EngineConfig(account_id="x", broker_name="icm", state_dir=str(tmp_path)), PaperBroker(equity=10_000),
                 [spec], {})
    assert not eng._busy(spec.agent_id)
    eng.open[7] = OpenTrade(position_id=7, agent_id=spec.agent_id, side=1, lots=0.1,
                            entry_bar_ts=pd.Timestamp("2025-03-03", tz="UTC"), max_bars=16)
    assert eng._busy(spec.agent_id) and not eng._busy("other-agent")
    del eng.open[7]
    p = _p("q1")
    eng.pending["q1"] = (Intent(agent_id=spec.agent_id, side=1, p=0.6, target_atr=1.5, stop_atr=1.0, atr_usd=4.0,
                                cost_atr=0.1, multiplier=1.0, price=2400.0), p, spec)
    assert eng._busy(spec.agent_id)
