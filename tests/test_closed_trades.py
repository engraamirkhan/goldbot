"""The engine's closed-trade record (state/closed_trades.jsonl), the evidence the phase gates (P7) and the stop rule
(P6) read: exactly one record per closed position for every exit path, scale-outs folded into the final record, no
duplicate across a restart, and a record that can never block or crash an exit. Also the per-tick stop check's
backoff and the engine surviving a positions() read that raises."""
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.engine import ConstantModel, Engine, EngineConfig, runner
from goldbot.execution.broker import OrderResult, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops import gates_phase
from goldbot.ops.gates_phase import ClosedTrade, evaluate_gates, load_closed_trades
from goldbot.specialists.breakout import BreakoutSpecialist
from goldbot.specialists.session_open import SessionOpenSpecialist
from goldbot.specialists.trend import TrendSpecialist
from goldbot.telegram.approvals import Proposal
from tests.test_exit_policies import _frame, _scripted

TREND, BREAKOUT, SESSION = TrendSpecialist(), BreakoutSpecialist(), SessionOpenSpecialist()
T0 = pd.Timestamp("2025-03-03 08:00", tz="UTC")
COMM = 3.5                                                     # per lot per side


def _engine(tmp_path: Path, agent: Any, model: Any = None, pb: PaperBroker | None = None,
            **cfg: Any) -> tuple[Engine, PaperBroker]:
    pb = pb or PaperBroker(equity=10_000, commission_per_lot_side=COMM, adverse_slip_points=0.0)
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", mode="demo", state_dir=str(tmp_path),
                              owner_user_id=111, **cfg), pb, [agent], {agent.family: model} if model is not None else {})
    return eng, pb


def _enter(eng: Engine, pb: PaperBroker, agent: Any, at: pd.Timestamp = T0, price: float = 2000.0,
           lots: float = 0.1, side: int = 1) -> int:
    pb.on_tick(Tick(ts_utc=at, bid=price, ask=price))
    ls = agent.label_spec
    stop, target = price - side * ls.stop_atr, price + side * ls.target_atr
    prop = Proposal(proposal_id=f"icm-demo-{int(at.timestamp())}-x", account_id="icm-demo", agent_id=agent.agent_id,
                    side=side, lots=lots, entry=price, stop=stop, target=target, p=0.6, ev_r=0.1, spread_points=0.0,
                    top_features=[])
    before = set(eng.open)
    eng._execute(prop, agent, lots, stop, target, requested=price, atr_usd=1.0)
    (pid,) = set(eng.open) - before
    return pid


def _tick(minutes: float, px: float) -> Tick:
    return Tick(ts_utc=T0 + pd.Timedelta(minutes=minutes), bid=px, ask=px)


def _records(tmp_path: Path) -> list[ClosedTrade]:
    trades, err = load_closed_trades(tmp_path)
    assert err is None
    return trades


def _lines(tmp_path: Path) -> int:
    f = tmp_path / gates_phase.CLOSED_TRADES_FILE
    return len(f.read_text().splitlines()) if f.exists() else 0


def _settle(eng: Engine) -> None:
    """A bar close's position management (broker-side closes are found there) and the write that follows it."""
    eng._manage_open(pd.DataFrame())
    eng._save_orders()


# ---------------------------------------------------------------------------------------------- one record per exit
@pytest.mark.parametrize("side", [1, -1])
def test_a_broker_side_target_is_recorded_once_with_net_pnl_r_and_commission(tmp_path, side):
    eng, pb = _engine(tmp_path, TREND)
    pid = _enter(eng, pb, TREND, side=side)
    target = 2000.0 + side * TREND.label_spec.target_atr
    pb.on_tick(_tick(30, target))                              # the server takes the target
    _settle(eng)
    _settle(eng)                                               # a second pass finds nothing new
    (t,) = _records(tmp_path)
    gross = 2.5 * 0.1 * 100                                    # 2.5 ATR of 1.0 on 0.1 lots x 100 oz
    assert t.position_id == pid and t.client_order_id == f"icm-demo-{int(T0.timestamp())}-x"
    assert (t.account_id, t.broker, t.mode, t.agent_id, t.side, t.lots) == ("icm-demo", "icm", "demo", TREND.agent_id,
                                                                            side, 0.1)
    assert t.exit_reason == "target" and t.entry_price == 2000.0 and t.exit_price == target
    assert t.entry_utc == T0 and t.exit_utc == T0 + pd.Timedelta(minutes=30)
    assert t.commission == pytest.approx(2 * COMM * 0.1) and t.swap == 0.0
    assert t.pnl == pytest.approx(gross - 2 * COMM * 0.1)
    assert t.r == pytest.approx(t.pnl / (TREND.label_spec.stop_atr * 0.1 * 100))
    assert t.ret == pytest.approx(t.pnl / (2000.0 * 0.1 * 100)) and t.equity_before > 0
    assert pid not in eng.open


def test_a_broker_side_stop_is_recorded_as_a_stop_with_a_loss_of_about_one_r(tmp_path):
    eng, pb = _engine(tmp_path, TREND)
    _enter(eng, pb, TREND)
    pb.on_tick(_tick(30, 2000.0 - TREND.label_spec.stop_atr))
    _settle(eng)
    (t,) = _records(tmp_path)
    assert t.exit_reason == "stop" and t.pnl < 0 and t.r == pytest.approx(-1 - 2 * COMM * 0.1 / 12.5)


def test_the_time_exit_is_recorded(tmp_path):
    eng, pb = _engine(tmp_path, SESSION)
    pid = _enter(eng, pb, SESSION)
    pb.on_tick(_tick(5, 2000.3))
    for _ in range(SESSION.label_spec.max_bars + 1):
        eng._manage_open(pd.DataFrame())
    eng._save_orders()
    (t,) = _records(tmp_path)
    assert t.position_id == pid and t.exit_reason == "time_exit" and t.exit_price == 2000.3


def test_the_hard_flat_policy_exit_is_recorded(tmp_path):
    eng, pb = _engine(tmp_path, SESSION)
    pid = _enter(eng, pb, SESSION, at=pd.Timestamp("2025-03-03 11:00", tz="UTC"))
    flat = eng.open[pid].flat_at
    assert flat is not None
    eng._manage_policies("15m", pd.DataFrame(), flat)
    eng._save_orders()
    (t,) = _records(tmp_path)
    assert t.exit_reason == "hard_flat" and t.agent_id == SESSION.agent_id


def test_the_trail_close_is_recorded(tmp_path):
    eng, pb = _engine(tmp_path, TREND)
    _enter(eng, pb, TREND)
    up = _scripted([(2000, 2001.5, 2000, 2001.4)], "2025-03-03 08:00", "1h")
    higher = pd.concat([up, _scripted([(2001.4, 2003.0, 2001.4, 2001.6)], "2025-03-03 09:00", "1h")], ignore_index=True)
    pb.on_tick(_tick(120, 2001.4))                             # under the 2001.50 stop the trail sets
    eng._manage_policies("1h", higher, T0 + pd.Timedelta(hours=2))
    eng._save_orders()
    (t,) = _records(tmp_path)
    assert t.exit_reason == "trail_close" and t.exit_price == 2001.4


def test_the_engine_stop_close_on_a_tick_is_recorded_by_that_tick(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, TREND)
    pid = _enter(eng, pb, TREND)
    eng.open[pid].sl = 2000.5                                  # tighter than the broker's (a rejected modify)
    eng.on_tick(_tick(1, 2000.4))
    (t,) = _records(tmp_path)
    assert t.exit_reason == "engine_stop_close" and t.exit_price == 2000.4
    assert t.r == pytest.approx(t.pnl / (TREND.label_spec.stop_atr * 0.1 * 100))   # R against the initial stop


def test_the_blackout_close_is_recorded(tmp_path):
    eng, pb = _engine(tmp_path, SESSION, ConstantModel(p=0.4), news_blackout=True)
    _enter(eng, pb, SESSION, at=pd.Timestamp("2025-03-03 13:00", tz="UTC"))
    eng._blackout_event = {"title": "US CPI", "ts_utc": "2025-03-03 13:30"}
    eng._blackout_close(pd.DataFrame(), pd.Timestamp("2025-03-03 13:15", tz="UTC"), {"15m": _frame()})
    (t,) = _records(tmp_path)
    assert t.exit_reason == "blackout_close"


def test_the_kill_switch_records_each_of_its_trades(tmp_path):
    eng, pb = _engine(tmp_path, TREND)
    a = _enter(eng, pb, TREND)
    b = _enter(eng, pb, TREND, at=T0 + pd.Timedelta(seconds=1))
    pb.on_tick(_tick(10, 1999.5))
    eng._kill_switch(everything=True)
    eng._save_orders()
    assert sorted(t.position_id or 0 for t in _records(tmp_path)) == sorted([a, b])
    assert {t.exit_reason for t in _records(tmp_path)} == {"kill_switch_close"}


def test_the_weekend_rule_records_the_losers_it_closes(tmp_path):
    eng, pb = _engine(tmp_path, TREND)
    fri = pd.Timestamp("2025-03-07 19:00", tz="UTC")
    pid = _enter(eng, pb, TREND, at=fri)
    tick = Tick(ts_utc=fri + pd.Timedelta(minutes=30), bid=1999.8, ask=1999.8)
    pb.on_tick(tick)
    eng._weekend_rule(tick)
    eng._save_orders()
    (t,) = _records(tmp_path)
    assert t.position_id == pid and t.exit_reason == "weekend_close_loser" and t.pnl < 0


def test_a_scale_out_is_folded_into_one_record_for_the_position(tmp_path):
    """Design choice: one record per position (a labelled trade), the scale-out's lots, price and P&L inside it."""
    eng, pb = _engine(tmp_path, BREAKOUT)
    pid = _enter(eng, pb, BREAKOUT)
    hit = _tick(5, 2001.0)                                     # scale level: 1.0 ATR
    pb.on_tick(hit)
    eng._scale_out(hit)
    assert pb.positions()[0].lots == pytest.approx(0.05)
    assert _records(tmp_path) == []                            # a partial close is not a trade
    pb.on_tick(_tick(10, 2001.8))                              # under the 2.0 ATR target
    eng._kill_switch(everything=True)
    eng._save_orders()
    (t,) = _records(tmp_path)
    assert t.position_id == pid and t.lots == 0.1 and t.partial_lots == pytest.approx(0.05)
    assert t.exit_price == pytest.approx(2001.4)
    assert t.pnl == pytest.approx(0.05 * 1.0 * 100 + 0.05 * 1.8 * 100 - COMM * (0.1 + 0.05 + 0.05))
    assert t.commission == pytest.approx(COMM * 0.2)


# ---------------------------------------------------------------------------------------------- restarts
def test_no_duplicate_after_a_restart_and_reconcile(tmp_path):
    """Crash after the record was written but before the open-trade table was saved: the restart finds the trade
    gone at the broker and must not record it a second time (key: account + position id)."""
    eng, pb = _engine(tmp_path, TREND)
    pid = _enter(eng, pb, TREND)
    snapshot = (tmp_path / "orders_icm-demo.json").read_text()          # still lists the open trade
    pb.on_tick(_tick(30, 2002.5))
    _settle(eng)
    assert _lines(tmp_path) == 1
    (tmp_path / "orders_icm-demo.json").write_text(snapshot)
    again, _ = _engine(tmp_path, TREND, pb=pb)
    _settle(again)
    again._reconcile(T0 + pd.Timedelta(hours=1))
    assert _lines(tmp_path) == 1 and pid not in again.open


def test_a_trade_closed_while_the_engine_was_down_is_recorded_on_restart(tmp_path):
    eng, pb = _engine(tmp_path, TREND)
    pid = _enter(eng, pb, TREND)
    pb.on_tick(_tick(30, 2000.0 - TREND.label_spec.stop_atr))          # the server stop, engine down
    again, _ = _engine(tmp_path, TREND, pb=pb)
    (t,) = _records(tmp_path)
    assert t.position_id == pid and t.exit_reason == "stop" and pid not in again.open


def test_a_trade_the_broker_briefly_stops_listing_is_kept_until_its_exit_deal_is_seen(tmp_path, monkeypatch):
    """A positions() reply that drops a live trade (MT5 returns None on an error): with no exit deal the trade is
    kept, not recorded, and carries on when it reappears."""
    eng, pb = _engine(tmp_path, TREND)
    pid = _enter(eng, pb, TREND)
    real = pb.positions
    monkeypatch.setattr(pb, "positions", lambda magic_prefix=None: [])
    _settle(eng)
    assert pid in eng.open and _records(tmp_path) == []
    monkeypatch.setattr(pb, "positions", real)
    _settle(eng)
    assert pid in eng.open and _records(tmp_path) == []


# ---------------------------------------------------------------------------------------------- never blocks an exit
def test_a_record_write_failure_is_logged_and_never_blocks_the_exit(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, TREND)
    pid = _enter(eng, pb, TREND)

    def disk_full(*_: Any) -> None:
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(runner, "append_closed_trade", disk_full)
    eng.open[pid].sl = 2000.5
    eng.on_tick(_tick(1, 2000.4))                              # must not raise
    assert not pb.positions() and pid not in eng.open          # the exit happened
    failed = [d for d in eng.decisions if d["action"] == "closed_trade_record_failed"]
    assert len(failed) == 1 and failed[0]["position"] == pid and "No space" in failed[0]["error"]
    eng.on_tick(_tick(2, 2000.4))                              # the engine carries on


def test_an_unreadable_deal_history_still_records_the_trade_from_the_engines_fills(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, TREND)
    _enter(eng, pb, TREND)

    def broken(since: pd.Timestamp) -> pd.DataFrame:
        raise RuntimeError("history not synced")
    monkeypatch.setattr(pb, "deals_since", broken)
    pb.on_tick(_tick(10, 2001.0))
    eng._kill_switch(everything=True)
    eng._save_orders()
    (t,) = _records(tmp_path)
    assert t.exit_price == pytest.approx(2001.0) and t.pnl == pytest.approx(10.0) and t.commission is None


# ---------------------------------------------------------------------------------------------- gates read the record
def test_gates_count_the_paper_trades_the_engine_recorded(tmp_path):
    eng, pb = _engine(tmp_path, TREND)
    for k in range(3):
        _enter(eng, pb, TREND, at=T0 + pd.Timedelta(hours=k))
        pb.on_tick(Tick(ts_utc=T0 + pd.Timedelta(hours=k, minutes=30), bid=2002.5, ask=2002.5))
        pb.on_tick(Tick(ts_utc=T0 + pd.Timedelta(hours=k, minutes=31), bid=2000.0, ask=2000.0))
        _settle(eng)
    rep = evaluate_gates(tmp_path, load_settings(), T0 + pd.Timedelta(days=1), trials_path=tmp_path / "none.jsonl")
    gate = next(g for g in rep.gates if g.gate == "paper_to_tiny_live")
    item = next(i for i in gate.items if i.name == "paper_trades")
    assert item.evidence == "3 closed demo trades" and not rep.errors
    assert rep.stop_rule.pooled_trades == 3


def test_a_repeated_line_is_counted_once(tmp_path):
    eng, pb = _engine(tmp_path, TREND)
    _enter(eng, pb, TREND)
    pb.on_tick(_tick(30, 2002.5))
    _settle(eng)
    f = tmp_path / gates_phase.CLOSED_TRADES_FILE
    f.write_text(f.read_text() * 2)
    assert len(_records(tmp_path)) == 1


def test_the_record_line_is_one_json_object_per_trade(tmp_path):
    eng, pb = _engine(tmp_path, TREND)
    _enter(eng, pb, TREND)
    pb.on_tick(_tick(30, 2002.5))
    _settle(eng)
    row = json.loads((tmp_path / gates_phase.CLOSED_TRADES_FILE).read_text().splitlines()[0])
    assert {"account_id", "mode", "agent_id", "side", "lots", "entry_utc", "entry_price", "exit_utc", "exit_price",
            "exit_reason", "pnl", "r", "commission", "swap", "client_order_id", "position_id"} <= set(row)


# ---------------------------------------------------------------------------------------------- stop-check backoff
def _refused(calls: list[int]) -> Any:
    def close(position_id: int, lots: float | None = None) -> OrderResult:
        calls.append(position_id)
        return OrderResult(ok=False, retcode=10018, order_id=None, position_id=position_id, filled_lots=0.0,
                           price=None, message="market closed")
    return close


def test_a_refused_engine_stop_close_backs_off_instead_of_retrying_every_tick(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, TREND)
    pid = _enter(eng, pb, TREND)
    eng.open[pid].sl = 2000.5
    calls: list[int] = []
    reads: list[int] = []
    real_close, real_positions = pb.close, pb.positions
    monkeypatch.setattr(pb, "close", _refused(calls))

    def counted(magic_prefix: int | None = None) -> list:
        reads.append(1)
        return real_positions()
    monkeypatch.setattr(pb, "positions", counted)
    for s in range(100):                                       # a tick a second for 100 s
        eng._stop_check(Tick(ts_utc=T0 + pd.Timedelta(seconds=60 + s), bid=2000.4, ask=2000.4))
    assert len(calls) == 3 and len(reads) == 3                 # at 0, 30 and 90 s: 30 s doubling
    monkeypatch.setattr(pb, "close", real_close)
    eng._stop_check(Tick(ts_utc=T0 + pd.Timedelta(seconds=60 + 150), bid=2000.4, ask=2000.4))   # due at 210 s
    assert pid in eng.open
    eng._stop_check(Tick(ts_utc=T0 + pd.Timedelta(seconds=60 + 210), bid=2000.4, ask=2000.4))
    assert pid not in eng.open and not pb.positions() and pid not in eng._close_backoff


def test_the_stop_check_backoff_is_capped(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, TREND)
    pid = _enter(eng, pb, TREND)
    eng.open[pid].sl = 2000.5
    monkeypatch.setattr(pb, "close", _refused([]))
    at = T0 + pd.Timedelta(minutes=1)
    for _ in range(12):
        eng._stop_check(Tick(ts_utc=at, bid=2000.4, ask=2000.4))
        at = eng._close_backoff[pid][1]
    fails, due = eng._close_backoff[pid]
    eng._stop_check(Tick(ts_utc=at, bid=2000.4, ask=2000.4))
    assert eng._close_backoff[pid][1] - at == pd.Timedelta(seconds=eng.cfg.modify_backoff_max_s)


def test_positions_raising_does_not_crash_on_tick_and_later_ticks_still_manage(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, TREND)
    pid = _enter(eng, pb, TREND)
    eng.open[pid].sl = 2000.5
    real = pb.positions

    def down(magic_prefix: int | None = None) -> list:
        raise RuntimeError("terminal disconnected")
    monkeypatch.setattr(pb, "positions", down)
    for k in range(3):
        eng.on_tick(_tick(1 + k, 2000.4))                      # stop check, reconcile and account refresh all read it
    eng._scale_out(_tick(4, 2000.4))
    eng._reconcile(T0 + pd.Timedelta(minutes=5))
    assert pid in eng.open and eng.state.dq_error              # nothing managed, entries blocked meanwhile
    monkeypatch.setattr(pb, "positions", real)
    eng.on_tick(_tick(6, 2000.4))
    assert pid not in eng.open and not pb.positions()
    (t,) = _records(tmp_path)
    assert t.exit_reason == "engine_stop_close"
