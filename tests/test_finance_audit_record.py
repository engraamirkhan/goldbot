"""What the monthly finance audit needs on the record (docs/finance/audit-framework.md, gaps GAP-01/02/03/04/05/10/13):
the decision price and spread at signal time next to the quote the order was priced from (the cost of the 90 s wait
for the owner's click), the initial and final stop, every exit fill against the price it was asked at, whether a P&L is
net of commission and swap or gross, the owner's decision time and channel on the archived proposal (never an id), and
the timeframe, family and champion model version on the closed record. New persisted fields default safely (ADR 0010)."""
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.data.store import Store
from goldbot.engine import Engine, EngineConfig
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops import gates_phase
from goldbot.ops.gates_phase import ClosedTrade, load_closed_trades
from goldbot.specialists.breakout import BreakoutSpecialist
from goldbot.specialists.trend import TrendSpecialist
from goldbot.telegram.approvals import ApprovalCenter, Outcome, Proposal
from goldbot.telegram.bus import ApprovalBus

TREND, BREAKOUT = TrendSpecialist(), BreakoutSpecialist()
T0 = pd.Timestamp("2025-03-03 08:00", tz="UTC")
PID = f"icm-demo-{int(T0.timestamp())}-x"


def _engine(tmp_path: Path, agent: Any = TREND, pb: PaperBroker | None = None, **kw: Any) -> tuple[Engine, PaperBroker]:
    pb = pb or PaperBroker(equity=10_000, commission_per_lot_side=3.5, adverse_slip_points=0.0)
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", mode="demo", state_dir=str(tmp_path),
                              owner_user_id=111, data_root=str(tmp_path / "data")), pb, [agent], {}, **kw)
    return eng, pb


def _enter(eng: Engine, pb: PaperBroker, agent: Any = TREND, *, decision: float = 2000.0, requested: float = 2000.0,
           spread_points: float = 25.0) -> int:
    pb.on_tick(Tick(ts_utc=T0, bid=requested, ask=requested))
    ls = agent.label_spec
    stop, target = requested - ls.stop_atr, requested + ls.target_atr
    prop = Proposal(proposal_id=PID, account_id="icm-demo", agent_id=agent.agent_id, side=1, lots=0.1, entry=decision,
                    stop=decision - ls.stop_atr, target=decision + ls.target_atr, p=0.6, ev_r=0.1,
                    spread_points=spread_points, top_features=[])
    before = set(eng.open)
    eng._execute(prop, agent, 0.1, stop, target, requested=requested, atr_usd=1.0)
    (pid,) = set(eng.open) - before
    return pid


def _tick(minutes: float, px: float) -> Tick:
    return Tick(ts_utc=T0 + pd.Timedelta(minutes=minutes), bid=px, ask=px)


def _record(tmp_path: Path) -> ClosedTrade:
    trades, err = load_closed_trades(tmp_path)
    assert err is None
    (t,) = trades
    return t


def _exit_fills(tmp_path: Path) -> pd.DataFrame:
    return Store(tmp_path / "data").read("exit_fills", source="icm-demo")


# ------------------------------------------------------------------------------------- GAP-02 decision price and spread
def test_the_closed_record_keeps_the_decision_price_and_spread_apart_from_the_quote_at_approval(tmp_path):
    eng, pb = _engine(tmp_path)
    _enter(eng, pb, decision=2000.0, requested=2000.6, spread_points=25.0)   # price moved during the 90 s window
    eng._kill_switch(everything=True)
    eng._save_orders()
    t = _record(tmp_path)
    assert t.decision_price == 2000.0 and t.decision_spread_points == 25.0
    assert t.requested_price == 2000.6 and t.entry_price == 2000.6
    assert t.decision_utc is not None


def test_the_order_journal_row_names_its_proposal(tmp_path):
    eng, pb = _engine(tmp_path)
    _enter(eng, pb)
    (row,) = [d for d in eng.decisions if d["action"] == "order"]
    assert row["proposal"] == PID


def test_the_decision_price_survives_a_restart_while_the_trade_is_open(tmp_path):
    eng, pb = _engine(tmp_path)
    _enter(eng, pb, decision=2000.0, requested=2000.6)
    again, _ = _engine(tmp_path, pb=pb)
    pb.on_tick(_tick(30, 2000.6 + TREND.label_spec.target_atr))         # the server takes the target
    again._manage_open(pd.DataFrame())
    again._save_orders()
    t = _record(tmp_path)
    assert (t.decision_price, t.requested_price, t.timeframe) == (2000.0, 2000.6, TREND.timeframe)


def test_an_orders_file_without_entry_audit_or_with_a_newer_audit_field_still_loads(tmp_path):
    """ADR 0010 both ways: an older release's orders file (no `audit`) loads with the facts unknown; a newer release's
    audit field is dropped and the known ones kept."""
    eng, pb = _engine(tmp_path)
    pid = _enter(eng, pb, decision=2000.0, requested=2000.6)
    path = tmp_path / "orders_icm-demo.json"
    saved = json.loads(path.read_text())
    saved["open"][str(pid)]["audit"]["future_field"] = 1
    saved["sent"][PID]["audit"]["future_field"] = 1
    path.write_text(json.dumps(saved))
    newer, _ = _engine(tmp_path, pb=pb)
    audit = newer.open[pid].audit
    assert audit is not None and audit.decision_price == 2000.0
    for table in ("open", "sent"):
        for row in saved[table].values():
            row.pop("audit")
    path.write_text(json.dumps(saved))
    older, _ = _engine(tmp_path, pb=pb)
    assert older.open[pid].audit is None and older._orders[PID].audit is None
    older._kill_switch(everything=True)
    older._save_orders()
    t = _record(tmp_path)
    assert t.decision_price is None and t.requested_price is None and t.money_source == "broker"


# ------------------------------------------------------------------------------------- GAP-01 / GAP-13 attribution
def test_the_closed_record_carries_the_timeframe_family_and_the_champion_model_version(tmp_path):
    eng, pb = _engine(tmp_path, live_versions={TREND.agent_id: "trend-v7"})
    _enter(eng, pb)
    eng._kill_switch(everything=True)
    eng._save_orders()
    t = _record(tmp_path)
    assert (t.timeframe, t.family, t.model_version) == (TREND.timeframe, TREND.family, "trend-v7")


def test_an_agent_without_a_registered_champion_has_no_model_version(tmp_path):
    eng, pb = _engine(tmp_path)
    _enter(eng, pb)
    eng._kill_switch(everything=True)
    eng._save_orders()
    assert _record(tmp_path).model_version is None


# ------------------------------------------------------------------------------------- GAP-03 stops on the record
def test_the_closed_record_keeps_the_initial_stop_and_the_final_stop_and_target(tmp_path):
    eng, pb = _engine(tmp_path)
    pid = _enter(eng, pb)
    initial = eng.open[pid].initial_sl
    eng.open[pid].sl = 2000.5                                  # trailed tighter than the entry stop
    eng.on_tick(_tick(1, 2000.4))                              # the engine closes at its stop
    t = _record(tmp_path)
    assert t.initial_sl == initial == round(2000.0 - TREND.label_spec.stop_atr, 2)
    assert t.final_sl == 2000.5 and t.final_tp == round(2000.0 + TREND.label_spec.target_atr, 2)


# ------------------------------------------------------------------------------------- GAP-04 exit fills
def test_an_engine_market_close_logs_its_exit_fill_against_the_quote_it_was_asked_at(tmp_path):
    eng, pb = _engine(tmp_path)
    pid = _enter(eng, pb)
    eng.open[pid].sl = 2000.5
    eng.on_tick(Tick(ts_utc=T0 + pd.Timedelta(minutes=1), bid=2000.4, ask=2000.7))
    (row,) = _exit_fills(tmp_path).to_dict("records")
    assert row["position_id"] == pid and row["client_order_id"] == PID and row["kind"] == "engine_stop_close"
    assert row["requested"] == 2000.4 and row["filled"] == 2000.4 and row["side"] == 1 and row["lots"] == 0.1
    assert row["spread"] == pytest.approx(0.3)


def test_a_broker_stop_fill_is_logged_against_its_stop_level(tmp_path):
    pb = PaperBroker(equity=10_000, commission_per_lot_side=3.5, adverse_slip_points=20.0)
    eng, _ = _engine(tmp_path, pb=pb)
    pid = _enter(eng, pb)
    stop = eng.open[pid].sl
    assert stop is not None
    pb.on_tick(_tick(30, stop - 0.5))                          # a gap through the stop: filled below it
    eng._manage_open(pd.DataFrame())
    eng._save_orders()
    (row,) = _exit_fills(tmp_path).to_dict("records")
    assert row["kind"] == "stop" and row["requested"] == stop
    assert row["filled"] == pytest.approx(stop - 0.5 - 0.2)    # 0.5 gap plus 20 points of slippage
    assert _record(tmp_path).final_sl == stop


def test_a_scale_out_logs_its_exit_fill(tmp_path):
    eng, pb = _engine(tmp_path, BREAKOUT)
    pid = _enter(eng, pb, BREAKOUT)
    hit = _tick(5, 2001.0)
    pb.on_tick(hit)
    eng._scale_out(hit)
    (row,) = _exit_fills(tmp_path).to_dict("records")
    assert row["position_id"] == pid and row["kind"] == "scale_out" and row["lots"] == pytest.approx(0.05)
    assert row["requested"] == 2001.0 and row["filled"] == 2001.0


def test_a_failing_exit_fill_write_never_blocks_the_exit(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    pid = _enter(eng, pb)

    def disk_full(*_: Any, **__: Any) -> int:
        raise OSError(28, "No space left on device")
    assert eng.store is not None
    monkeypatch.setattr(eng.store, "append", disk_full)
    eng.open[pid].sl = 2000.5
    eng.on_tick(_tick(1, 2000.4))                              # must not raise
    assert not pb.positions() and pid not in eng.open
    assert _record(tmp_path).exit_reason == "engine_stop_close"


# ------------------------------------------------------------------------------------- GAP-05 net or gross
def test_a_trade_with_the_brokers_commission_and_swap_is_marked_net_from_the_broker(tmp_path):
    eng, pb = _engine(tmp_path)
    _enter(eng, pb)
    pb.on_tick(_tick(10, 2001.0))
    eng._kill_switch(everything=True)
    eng._save_orders()
    t = _record(tmp_path)
    assert (t.money_source, t.pnl_basis) == ("broker", "net")
    assert t.commission == pytest.approx(0.7) and t.swap == 0.0


def test_a_trade_recorded_from_the_engines_own_fills_is_marked_gross(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _enter(eng, pb)

    def broken(since: pd.Timestamp) -> pd.DataFrame:
        raise RuntimeError("history not synced")
    monkeypatch.setattr(pb, "deals_since", broken)
    pb.on_tick(_tick(10, 2001.0))
    eng._kill_switch(everything=True)
    eng._save_orders()
    t = _record(tmp_path)
    assert (t.money_source, t.pnl_basis) == ("engine", "gross") and t.commission is None and t.swap is None


def test_broker_deals_without_a_commission_figure_are_marked_gross(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _enter(eng, pb)
    real = pb.deals_since
    monkeypatch.setattr(pb, "deals_since", lambda since: real(since).drop(columns=["commission"]))
    pb.on_tick(_tick(10, 2001.0))
    eng._kill_switch(everything=True)
    eng._save_orders()
    t = _record(tmp_path)
    assert (t.money_source, t.pnl_basis) == ("broker", "gross") and t.commission is None


def test_an_older_closed_record_without_the_audit_fields_reads_them_as_unknown(tmp_path):
    line = {"account_id": "icm-demo", "broker": "icm", "mode": "demo", "exit_utc": "2025-03-03T08:30:00Z",
            "ret": 0.001, "pnl": 10.0, "equity_before": 10_000.0}
    gates_phase.closed_trade_file(tmp_path, "icm-demo").write_text(json.dumps(line) + "\n")
    t = _record(tmp_path)
    assert (t.decision_price, t.requested_price, t.initial_sl, t.final_sl, t.final_tp) == (None,) * 5
    assert (t.money_source, t.pnl_basis, t.timeframe, t.family, t.model_version) == (None,) * 5


# ------------------------------------------------------------------------------------- GAP-10 decision time
def test_the_archived_proposal_keeps_the_owners_decision_time_and_channel_from_the_bus(tmp_path):
    bus = ApprovalBus(tmp_path)
    center = ApprovalCenter({111}, bus=bus)
    center.propose(Proposal(proposal_id="p1", account_id="icm-demo", agent_id="a", side=1, lots=0.1, entry=2000.0,
                            stop=1998.0, target=2004.0, p=0.6, ev_r=0.2, spread_points=20.0, top_features=[]))
    d = bus.submit("p1", approve=True, reason_code=None, by="telegram:12345")
    center.poll_bus()
    archived = json.loads((bus.done_dir / "p1.json").read_text())
    assert archived["outcome"] == Outcome.APPROVED.value
    assert archived["decided_ts"] == d.ts and archived["decided_channel"] == "telegram"


def test_an_in_process_decision_records_its_time_and_the_telegram_channel(tmp_path):
    bus = ApprovalBus(tmp_path)
    center = ApprovalCenter({111}, bus=bus)
    center.propose(Proposal(proposal_id="p1", account_id="icm-demo", agent_id="a", side=1, lots=0.1, entry=2000.0,
                            stop=1998.0, target=2004.0, p=0.6, ev_r=0.2, spread_points=20.0, top_features=[]))
    p = center.decide("p1", 111, approve=False, reason_code="news")
    archived = bus.get("p1")
    assert archived is not None and p.decided_ts is not None and archived.decided_ts == p.decided_ts
    assert archived.decided_channel == "telegram" and archived.created <= p.decided_ts


def test_archive_takes_the_decision_time_from_the_decision_file_before_deleting_it(tmp_path):
    bus = ApprovalBus(tmp_path)
    p = Proposal(proposal_id="p1", account_id="icm-demo", agent_id="a", side=1, lots=0.1, entry=2000.0, stop=1998.0,
                 target=2004.0, p=0.6, ev_r=0.2, spread_points=20.0, top_features=[])
    bus.publish(p)
    d = bus.submit("p1", approve=True, reason_code=None, by="dashboard:owner")
    bus.archive(p.model_copy(update={"outcome": Outcome.APPROVED}))
    archived = bus.get("p1")
    assert archived is not None and (archived.decided_ts, archived.decided_channel) == (d.ts, "dashboard")
    assert not (bus.decisions_dir / "p1.json").exists()


def test_an_expired_proposal_has_no_decision_time(tmp_path):
    bus = ApprovalBus(tmp_path)
    center = ApprovalCenter({111}, bus=bus)
    center.propose(Proposal(proposal_id="p1", account_id="icm-demo", agent_id="a", side=1, lots=0.1, entry=2000.0,
                            stop=1998.0, target=2004.0, p=0.6, ev_r=0.2, spread_points=20.0, top_features=[],
                            created=0.0))
    center.sweep_expired()
    archived = bus.get("p1")
    assert archived is not None and archived.outcome == Outcome.EXPIRED
    assert (archived.decided_ts, archived.decided_channel) == (None, None)
