"""Engine safety paths without a full replay: halt inputs, RiskGate re-check at approval, time exits, reconciliation,
risk-day reset and the 12% kill switch (design: Risk management, Operating mode, Order lifecycle)."""
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.data.resample import ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine import Engine, EngineConfig
from goldbot.execution.broker import OrderIntent, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.risk import Intent
from goldbot.risk.gate import Stage
from goldbot.risk.supervisor import Supervisor
from goldbot.specialists import SPECIALISTS
from goldbot.telegram.approvals import ApprovalCenter, Outcome, Proposal
from goldbot.telegram.bus import ApprovalBus

OWNER = 111
T0 = pd.Timestamp("2025-03-05 10:00", tz="UTC")


def _tick(t: pd.Timestamp, bid: float = 2400.0) -> Tick:
    return Tick(ts_utc=t, bid=bid, ask=bid + 0.2)


def _engine(tmp_path: Path, *, halt_checks: bool = False, bus: bool = False, **cfg: Any) -> tuple[Engine, PaperBroker, ApprovalCenter]:
    pb = PaperBroker(equity=10_000)
    pb.on_tick(_tick(T0))
    center = ApprovalCenter({OWNER}, bus=ApprovalBus(tmp_path) if bus else None)
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), owner_user_id=OWNER,
                              halt_checks=halt_checks, **cfg), pb, [], {}, center)
    return eng, pb, center


def _open(pb: PaperBroker, cid: str, magic: int = 260150, lots: float = 0.1, sl: float = 2300.0, tp: float = 2500.0) -> int:
    r = pb.place_order(OrderIntent(client_order_id=cid, symbol="XAUUSD", side=1, lots=lots, sl=sl, tp=tp, magic=magic,
                                   comment=cid))
    assert r.ok and r.position_id is not None
    return r.position_id


# ---------------------------------------------------------------------------------------------- halt inputs
def test_production_engines_fail_closed_without_a_supervisor_and_obey_the_owner_halt(tmp_path):
    eng, pb, _ = _engine(tmp_path, halt_checks=True, bus=True)
    eng._refresh_account(pb.last_tick("XAUUSD"))
    assert eng.state.supervisor_halt and not eng.state.owner_halt          # no heartbeat yet: blocked
    Supervisor(tmp_path).evaluate()
    eng._refresh_account(pb.last_tick("XAUUSD"))
    assert not eng.state.supervisor_halt
    ApprovalBus(tmp_path).set_halt(True, "telegram:111")
    eng._refresh_account(pb.last_tick("XAUUSD"))
    assert eng.state.owner_halt
    assert "owner_halt" in eng.gate.check(_intent(), eng.state).reasons


def test_engines_without_halt_checks_ignore_the_flags_and_measure_the_spread(tmp_path):
    eng, pb, _ = _engine(tmp_path)
    eng._refresh_account(_tick(T0, 2400.0))
    assert not eng.state.supervisor_halt and not eng.state.owner_halt
    assert eng.state.spread_points == pytest.approx(20.0) and eng.state.equity == 10_000
    assert eng.state.day_start_equity == eng.state.week_start_equity == eng.state.balance_closed_hwm == 10_000


# ---------------------------------------------------------------------------------------------- approvals -> orders
def _intent() -> Intent:
    return Intent(agent_id="session_open-x", side=1, p=0.62, target_atr=1.5, stop_atr=1.0, atr_usd=4.0, cost_atr=0.1,
                  multiplier=1.0, price=2400.2)


def _pend(eng: Engine, center: ApprovalCenter, pid: str) -> None:
    agent = SPECIALISTS["session_open"]()
    prop = Proposal(proposal_id=pid, account_id="icm-demo", agent_id=agent.agent_id, side=1, lots=0.12, entry=2400.2,
                    stop=2396.2, target=2406.2, p=0.62, ev_r=0.2, spread_points=20, top_features=[])
    eng.pending[pid] = (_intent(), prop, agent)
    center.propose(prop)


def test_an_approved_entry_is_sized_again_and_sent_once(tmp_path):
    eng, pb, center = _engine(tmp_path)
    eng.bars_1m = pd.DataFrame({"ts_utc": [T0 - pd.Timedelta(minutes=1)], "visible_at": [T0]})   # fresh data
    _pend(eng, center, "icm-demo-1-a")
    assert eng._busy(SPECIALISTS["session_open"]().agent_id)               # one pending proposal per agent
    center.decide("icm-demo-1-a", OWNER, True)
    [pos] = pb.positions()
    assert pos.lots == 0.12 and pos.sl is not None and pos.sl < pos.open_price < (pos.tp or 0)
    assert "icm-demo-1-a" in eng.sent_ids and not eng.pending
    orders = [d for d in eng.decisions if d.get("action") == "order"]
    assert len(orders) == 1 and orders[0]["ok"]


def test_approval_re_runs_the_risk_gate_and_a_halt_meanwhile_blocks_the_entry(tmp_path):
    eng, pb, center = _engine(tmp_path, halt_checks=True, bus=True)
    Supervisor(tmp_path).evaluate()
    _pend(eng, center, "icm-demo-2-a")
    ApprovalBus(tmp_path).set_halt(True, "dashboard:owner@x")              # owner halts while the proposal waits
    center.decide("icm-demo-2-a", OWNER, True)
    assert pb.positions() == []
    assert any(str(d.get("action")).startswith("gate_at_approval:") and "owner_halt" in str(d["action"])
               for d in eng.decisions)


@pytest.mark.parametrize("approve", [False, None])
def test_rejected_or_expired_proposals_never_reach_the_broker(tmp_path, approve):
    eng, pb, center = _engine(tmp_path)
    _pend(eng, center, "icm-demo-3-a")
    if approve is None:
        center.pending["icm-demo-3-a"].created = time.time() - 200
        [p] = center.sweep_expired()
        assert p.outcome == Outcome.EXPIRED
    else:
        center.decide("icm-demo-3-a", OWNER, False, "discretion")
    assert pb.positions() == [] and not eng.pending
    assert not [d for d in eng.decisions if d.get("action") == "order"]


# ---------------------------------------------------------------------------------------------- position management
def test_time_exit_and_broker_side_closes_are_tracked(tmp_path):
    eng, pb, _ = _engine(tmp_path)
    from goldbot.engine.runner import OpenTrade
    pid = _open(pb, "a")
    gone = _open(pb, "b")
    for p, mb in ((pid, 2), (gone, 5)):
        eng.open[p] = OpenTrade(position_id=p, agent_id=f"agent-{p}", side=1, lots=0.1, entry_bar_ts=T0, max_bars=mb)
    pb.close(gone)                                     # stopped out server-side
    eng._manage_open(pd.DataFrame())
    assert set(eng.open) == {pid} and eng.open[pid].bars_held == 1
    eng._manage_open(pd.DataFrame())
    assert eng.open == {} and pb.positions() == []
    assert [d["action"] for d in eng.decisions] == ["time_exit"]


def test_reconciliation_adopts_only_positions_in_its_own_magic_range(tmp_path):
    eng, pb, _ = _engine(tmp_path)
    mine = _open(pb, "mine", magic=260199)
    _open(pb, "other_account", magic=260201)
    _open(pb, "manual", magic=0)
    eng._manage_open(pd.DataFrame())
    assert list(eng.open) == [mine] and eng.open[mine].agent_id == "orphan"
    assert eng._busy("orphan") and not eng._busy("someone")


# ---------------------------------------------------------------------------------------------- risk periods and kill switch
def test_day_start_equity_resets_at_the_risk_day_boundary(tmp_path):
    eng, pb, _ = _engine(tmp_path)
    eng._refresh_account(_tick(T0))
    _open(pb, "a", lots=0.5)
    nxt = pd.Timestamp("2025-03-06 00:05", tz="UTC")
    pb.on_tick(_tick(nxt, 2397.0))                     # $160 floating loss carried into the new day
    eng._refresh_account(pb.last_tick("XAUUSD"))
    assert eng.state.day_start_equity == pytest.approx(pb.account().equity)


def test_twelve_percent_drawdown_closes_everything_at_market(tmp_path):
    eng, pb, _ = _engine(tmp_path)
    ticks = synthetic_ticks("2025-03-03", "2025-03-05", ticks_per_minute=1, seed=5)
    eng.bars_1m = ticks_to_1m(ticks)
    last = pd.Timestamp(ticks["ts_utc"].iloc[-1])
    px = float(ticks["bid"].iloc[-1])
    pb.on_tick(_tick(last, px))
    eng._refresh_account(pb.last_tick("XAUUSD"))
    _open(pb, "big", lots=1.0, sl=px - 100, tp=px + 100)
    crash = _tick(last + pd.Timedelta(seconds=1), px - 13.0)       # -$1,300 on $10k: 13% mark-to-market drawdown
    pb.on_tick(crash)
    close_ts = (last + pd.Timedelta(minutes=15)).floor("15min")
    eng.on_bar_close(close_ts, crash)
    assert eng.state.stage == Stage.HALTED
    assert pb.positions() == []


def test_kill_switch_and_state_heartbeat_act_between_bar_closes(tmp_path):
    eng, pb, _ = _engine(tmp_path)
    t = T0.floor("15min") + pd.Timedelta(minutes=1)
    eng.on_tick(_tick(t, 2400.0))                                    # first tick: account refreshed, state written
    first = (tmp_path / "engine_icm-demo.json").stat().st_mtime_ns
    _open(pb, "big", lots=1.0, sl=2300.0, tp=2500.0)
    eng.on_tick(_tick(t + pd.Timedelta(seconds=5), 2399.0))           # inside state_every_s: no refresh yet
    assert len(pb.positions()) == 1
    crash = t + pd.Timedelta(seconds=11)                              # same 15m bar, 11 s later
    eng.on_tick(_tick(crash, 2387.0))                                 # -$1,300 on $10k: 13% drawdown
    assert eng.state.stage == Stage.HALTED and pb.positions() == []  # closed within seconds, not at the bar close
    assert (tmp_path / "engine_icm-demo.json").stat().st_mtime_ns >= first


def test_expired_proposals_of_this_account_are_archived_on_start(tmp_path):
    bus = ApprovalBus(tmp_path)
    old: dict[str, Any] = {"account_id": "icm-demo", "agent_id": "a", "side": 1, "lots": 0.1, "entry": 2400.0, "stop": 2396.0,
           "target": 2406.0, "p": 0.6, "ev_r": 0.1, "spread_points": 20, "top_features": []}
    mine = Proposal(proposal_id="icm-demo-1-aaaa", created=time.time() - 3600, **old)
    other = Proposal(proposal_id="vantage-demo-1-bbbb", created=time.time() - 3600, **{**old, "account_id": "vantage-demo"})
    live = Proposal(proposal_id="icm-demo-2-cccc", **old)                # still inside its window
    for p in (mine, other, live):
        bus.publish(p)
    _engine(tmp_path, bus=True)
    left = {p.name for p in bus.pending_dir.glob("*.json")}
    assert left == {"vantage-demo-1-bbbb.json", "icm-demo-2-cccc.json"}
    assert (bus.done_dir / "icm-demo-1-aaaa.json").exists()
