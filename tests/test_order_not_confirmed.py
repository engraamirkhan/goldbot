"""BACKLOG 72 (docs/ux/order-not-confirmed.md; plan docs/proposals/2026-10-backlog72-not-confirmed-plan.md and its
trading-safety conditions 1-7): an approved or automatic entry the broker neither filled nor definitely refused is NOT
CONFIRMED, never ✅ APPROVED. The engine keeps the row unresolved (never re-sent), looks it up at the broker on every
pass, with or without ticks, refuses new entries while it is unresolved, and tells the owner what it finds: open
(with its stop put back), opened and already closed (recorded), no trade (two clean looks at least 120 s apart), or,
after 10 minutes, that it still doesn't know; after 7 days it stops looking."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.engine import Engine, EngineConfig, unconfirmed
from goldbot.engine.runner import DEFINITE_REJECT_RETCODES
from goldbot.execution.broker import OrderIntent, OrderResult
from goldbot.execution.not_confirmed import (
    Confirmation,
    card_text,
    health_line,
    note,
    notice_text,
)
from goldbot.execution.not_placed import refusal_code
from goldbot.execution.paper import PaperBroker
from goldbot.ops.gates_phase import load_closed_trades
from goldbot.risk.gate import GateDecision
from goldbot.specialists import SPECIALISTS
from goldbot.telegram.approvals import ApprovalCenter
from goldbot.telegram.bot import outcome_text
from goldbot.telegram.bus import ApprovalBus
from tests.test_engine_safety import (
    OWNER,
    T0,
    _approved_through,
    _BrokerRefuses,
    _intent,
    _notices,
    _prop,
    _tick,
)

PID = "icm-demo-72-a"
AGENT = SPECIALISTS["session_open"]()
SL, TP = 2396.0, 2406.0
GATE = " New entries wait until it's settled."
CARD_PENDING = ("⚠️ NOT CONFIRMED: approved and sent, but the broker didn't confirm it. The engine looks it up at the "
                "broker and tells you what it finds, or tells you after 10 minutes that it still doesn't know." + GATE)


class _Unanswered(PaperBroker):
    """order_send comes back with `retcode` and no position. `fill` lots still reach the broker (a late 10012) when
    set. `deals_fail` makes the deal history unreadable; `mt5_deals` replaces it with MT5-shaped rows. The deal history
    ignores `since`: the paper ticks are dated 2025, the rows are stamped with the host clock."""

    def __init__(self, retcode: int = 10012, fill: float | None = None, **kw: Any) -> None:
        super().__init__(**kw)
        self.retcode, self.fill, self.sends = retcode, fill, 0
        self.deals_fail = False
        self.mt5_deals: list[dict] | None = None
        self.modify_ok = True

    def place_order(self, intent: OrderIntent) -> OrderResult:
        self.sends += 1
        if self.fill is not None:
            super().place_order(intent.model_copy(update={"lots": self.fill}))
        return OrderResult(ok=False, retcode=self.retcode, order_id=None, position_id=None, filled_lots=0.0,
                           price=None, message="Request timed out")

    def deals_since(self, since_utc: pd.Timestamp) -> pd.DataFrame:
        if self.deals_fail:
            raise RuntimeError("history_deals_get failed")
        if self.mt5_deals is not None:
            return pd.DataFrame(self.mt5_deals)
        return pd.DataFrame(self._deals)

    def modify(self, position_id: int, sl: float | None, tp: float | None) -> OrderResult:
        if not self.modify_ok:
            return OrderResult(ok=False, retcode=10016, order_id=None, position_id=position_id, filled_lots=0.0,
                               price=None, message="Invalid stops")
        return super().modify(position_id, sl, tp)


def _engine(tmp_path: Path, pb: PaperBroker, **cfg: Any) -> tuple[Engine, PaperBroker]:
    """An engine on `pb` with the approval bus the services share (the owner notices go through it)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), owner_user_id=OWNER,
                              **cfg), pb, [], {}, ApprovalCenter({OWNER}, bus=ApprovalBus(tmp_path)))
    return eng, pb


def _broker(**kw: Any) -> _Unanswered:
    pb = _Unanswered(equity=10_000, **kw)
    pb.on_tick(_tick(T0))
    return pb


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[pd.Timestamp]:
    """The look-up's host clock (`unconfirmed._now`); a test moves it with `clock[0] += ...`."""
    now = [pd.Timestamp.now("UTC")]
    monkeypatch.setattr(unconfirmed, "_now", lambda: now[0])
    return now


def _sent(tmp_path: Path, pb: _Unanswered, lots: float = 0.1) -> Any:
    eng, _ = _engine(tmp_path, pb)
    res = eng._execute(_prop(PID), AGENT, lots, SL, TP, requested=2400.2)
    assert res is not None and eng._orders[PID].status == "ambiguous"
    return eng


def _look(eng: Any) -> None:
    eng._reconcile(None)


def _at(clock: list[pd.Timestamp], eng: Any, seconds: float) -> None:
    clock[0] = pd.Timestamp(eng._orders[PID].ts_utc) + pd.Timedelta(seconds=seconds)


# ---------------------------------------------------------------------------------------------- the result
@pytest.mark.parametrize("retcode", [10008, 10010, 10011, 10012, 10031, -1, 10099])
def test_an_ambiguous_result_on_approval_archives_not_confirmed_never_approved(tmp_path, retcode):
    pb = _BrokerRefuses(retcode, message="Request timed out", equity=10_000)
    eng, archived, [outcome] = _approved_through(tmp_path, pb)
    assert archived.gate_refusal == ["not_confirmed"]
    assert archived.not_confirmed is not None and archived.not_confirmed.outcome == "pending"
    assert outcome_text(outcome) == CARD_PENDING and "APPROVED" not in outcome_text(outcome)
    assert _notices(tmp_path) == []                          # no "not placed" notice: the order may exist


def test_ten_retcodes_including_10099_and_minus_one_archive_not_confirmed_not_approved():
    def res(retcode: int, ok: bool = False, position: int | None = None) -> OrderResult:
        return OrderResult(ok=ok, retcode=retcode, order_id=None, position_id=position, filled_lots=0.0, price=None,
                           message="no answer")
    ambiguous = [res(c) for c in (-1, 0, 10008, 10010, 10011, 10012, 10023, 10031, 10099)] + [res(10009, ok=True)]
    assert len(ambiguous) == 10
    for r in ambiguous:
        assert unconfirmed.sent_status(r, paper=False, definite=DEFINITE_REJECT_RETCODES) == "ambiguous", r.retcode
        assert refusal_code(r, paper=False, definite=DEFINITE_REJECT_RETCODES) is None
    assert unconfirmed.sent_status(res(10009, ok=True, position=5), paper=False,
                                   definite=DEFINITE_REJECT_RETCODES) == "filled"
    for c in sorted(DEFINITE_REJECT_RETCODES):
        assert unconfirmed.sent_status(res(c), paper=False, definite=DEFINITE_REJECT_RETCODES) == "rejected"


def test_a_definite_reject_stays_rejected_and_is_not_looked_up_again(tmp_path, clock):
    pb = _broker(retcode=10019)
    eng, _ = _engine(tmp_path, pb)
    eng._execute(_prop(PID), AGENT, 0.1, SL, TP, requested=2400.2)
    assert eng._orders[PID].status == "rejected"
    for s in (0, 200, 900):
        _at(clock, eng, s)
        _look(eng)
    assert eng._orders[PID].status == "rejected" and _notices(tmp_path) == []
    assert not [d for d in eng.decisions if str(d.get("action", "")).startswith("unconfirmed_")]
    assert "unconfirmed_order_pending" not in eng.gate.account_reasons(eng.state)


def test_an_ambiguous_result_keeps_the_sent_row_unresolved_and_it_is_never_resent(tmp_path, clock):
    pb = _broker()
    eng = _sent(tmp_path, pb)
    assert PID in eng.sent_ids
    assert eng._execute(_prop(PID), AGENT, 0.1, SL, TP, requested=2400.2) is None
    again, _ = _engine(tmp_path, pb)                                  # restart
    assert again._orders[PID].status == "ambiguous" and PID in again.sent_ids
    assert again._execute(_prop(PID), AGENT, 0.1, SL, TP, requested=2400.2) is None
    assert pb.sends == 1


# ---------------------------------------------------------------------------------------------- outcomes
def test_a_position_found_later_is_adopted_with_the_sent_stop_and_the_owner_told_it_is_open(tmp_path, clock):
    pb = _broker(fill=0.1)
    eng = _sent(tmp_path, pb)
    [pos] = pb.positions()
    _look(eng)
    rec = eng._orders[PID]
    assert rec.status == "filled" and rec.position_id == pos.position_id and rec.notified == "opened"
    tr = eng.open[pos.position_id]
    assert tr.agent_id == AGENT.agent_id and tr.initial_sl == SL and tr.sl == SL and tr.tp == TP
    assert _notices(tmp_path) == [
        f"Order confirmed: LONG 0.1 lots XAUUSD on icm-demo is open at {pos.open_price:.2f} (the broker confirmed it "
        f"late). Stop 2396.00, target 2406.00; exits are automatic."]
    assert "unconfirmed_order_pending" not in eng.gate.account_reasons(eng.state)


def test_a_fill_closed_before_the_next_check_is_recorded_from_its_deals(tmp_path, clock):
    pb = _broker(fill=0.1)
    eng = _sent(tmp_path, pb)
    [pos] = pb.positions()
    pb.on_tick(_tick(T0 + pd.Timedelta(seconds=20), bid=2397.0))      # closed by hand before the next look
    pb.close(pos.position_id)
    _look(eng)
    eng._flush_closed()
    rec = eng._orders[PID]
    assert rec.status == "filled" and rec.position_id == pos.position_id and rec.notified == "opened_closed"
    (trade,), _ = load_closed_trades(tmp_path)
    assert trade.position_id == pos.position_id and trade.client_order_id == PID and trade.lots == 0.1
    [notice] = _notices(tmp_path)
    assert notice.startswith(f"Order confirmed, already closed: LONG 0.1 lots XAUUSD on icm-demo opened at "
                             f"{pos.open_price:.2f} and closed at 2397.00 (−")
    assert notice.endswith("). It's in the trade record.")


def test_a_closed_fill_reaches_the_stop_rule_and_the_ledger(tmp_path, clock):
    pb = _broker(fill=0.1)
    eng = _sent(tmp_path, pb)
    [pos] = pb.positions()
    pb.close(pos.position_id)
    _look(eng)
    eng._flush_closed()
    (trade,), err = load_closed_trades(tmp_path)        # the stop rule (P6), the phase gates (P7) and the ledger read it
    assert err is None and trade.position_id == pos.position_id and trade.money_source == "broker"
    assert [t.position_id for t in eng._throttle_closed] == [pos.position_id]     # the per-agent loss streak sees it


def test_no_position_and_no_deal_marks_unfilled_and_the_owner_told_no_trade_opened(tmp_path, clock):
    pb = _broker()
    eng = _sent(tmp_path, pb)
    _at(clock, eng, 5)
    _look(eng)
    _at(clock, eng, 125)
    _look(eng)
    assert eng._orders[PID].status == "unfilled" and eng._orders[PID].notified == "no_trade"
    assert _notices(tmp_path) == [
        "Order not placed after all: the broker has no trade for LONG 0.1 lots XAUUSD on icm-demo (checked twice, at "
        "least 2 minutes apart). Nothing to do; if it turns up later, you'll be told."]
    assert "unconfirmed_order_pending" not in eng.gate.account_reasons(eng.state)


def test_no_deal_is_not_declared_unfilled_before_settle_s_boundary(tmp_path, clock):
    pb = _broker()
    eng = _sent(tmp_path, pb)
    _at(clock, eng, 1)
    _look(eng)
    _at(clock, eng, 1 + 119)
    _look(eng)
    assert eng._orders[PID].status == "ambiguous"
    _at(clock, eng, 1 + 120)
    _look(eng)
    assert eng._orders[PID].status == "unfilled"


def test_two_clean_looks_at_least_120_s_apart_are_needed_and_row_age_alone_never_declares_unfilled(tmp_path, clock):
    """Condition 2: a restart an hour later has an old row but no clean look in this process: its first look never
    declares `unfilled`; an unreadable look between two clean ones neither counts nor resets the first."""
    pb = _broker()
    eng = _sent(tmp_path, pb)
    _at(clock, eng, 3600)
    again, _ = _engine(tmp_path, pb)                 # the restart's look (`_load_orders`)
    assert again._orders[PID].status == "ambiguous"
    _at(clock, again, 3660)
    pb.deals_fail = True
    _look(again)
    pb.deals_fail = False
    _at(clock, again, 3700)
    _look(again)
    assert again._orders[PID].status == "ambiguous"
    _at(clock, again, 3720)
    _look(again)
    assert again._orders[PID].status == "unfilled"


def test_an_unreadable_deal_history_keeps_looking_and_sends_at_most_one_still_unknown_notice(tmp_path, clock):
    pb = _broker()
    pb.deals_fail = True
    eng = _sent(tmp_path, pb)
    for s in (30, 300, 600, 630, 900, 1800):
        _at(clock, eng, s)
        _look(eng)
    assert eng._orders[PID].status == "ambiguous" and eng._orders[PID].unknown_notified
    assert _notices(tmp_path) == [
        "Order still not confirmed after 10 minutes: the broker hasn't shown whether LONG 0.1 lots XAUUSD on icm-demo "
        "opened. The engine keeps looking and tells you when it knows. New entries wait until it's settled. Check MT5: "
        "if a XAUUSD trade is open without a stop, set stop 2396.00."]
    again, _ = _engine(tmp_path, pb)                 # the flag survives a restart: never a second notice
    _at(clock, again, 2400)
    _look(again)
    assert len(_notices(tmp_path)) == 1


def test_still_unknown_at_exactly_600_s_sets_the_health_warn(tmp_path, clock):
    from goldbot.ops import health
    from tests.test_health import engine as engine_file
    from tests.test_health import make_ctx
    pb = _broker()
    pb.deals_fail = True
    eng = _sent(tmp_path, pb)
    _at(clock, eng, 599)
    _look(eng)
    s599 = unconfirmed.summary(eng._orders, clock[0], "XAUUSD")
    assert _notices(tmp_path) == [] and s599["count"] == 1 and s599["oldest_age_s"] == 599
    _at(clock, eng, 600)
    _look(eng)
    s600 = unconfirmed.summary(eng._orders, clock[0], "XAUUSD")
    assert len(_notices(tmp_path)) == 1 and s600["oldest_age_s"] == 600
    ctx = make_ctx(tmp_path)
    engine_file(tmp_path, 60, unconfirmed_orders=s599)
    assert "not confirmed" not in health.check_engine(ctx, "icm-demo").reason
    engine_file(tmp_path, 60, unconfirmed_orders=s600)
    c = health.check_engine(ctx, "icm-demo")
    assert c.status == "warn" and (
        "1 order not confirmed by the broker, oldest 10 min: the engine keeps looking; new entries wait until it's "
        "settled; check MT5 for an open XAUUSD trade and its stop (Runbook: Order not confirmed)") in c.reason


def test_an_unresolved_row_becomes_terminal_unknown_after_7_days_with_one_notice_and_is_then_pruned(tmp_path, clock):
    pb = _broker()
    pb.deals_fail = True
    eng = _sent(tmp_path, pb)
    eng._orders[PID].ts_utc = pd.Timestamp.now("UTC") - pd.Timedelta(days=7, seconds=5)
    eng._orders[PID].unknown_notified = True
    eng._save_orders()
    assert eng._orders[PID].status == "ambiguous"    # the 7-day prune keeps an unresolved row
    clock[0] = pd.Timestamp.now("UTC")
    _look(eng)
    assert PID not in eng._orders and PID in eng.sent_ids           # terminal `unknown`, journalled, then pruned
    assert [d["action"] for d in eng.decisions if d.get("proposal") == PID][-1] == "unconfirmed_gave_up"
    assert _notices(tmp_path) == [
        "Stopped looking: after 7 days the broker still hasn't shown whether LONG 0.1 lots XAUUSD on icm-demo opened. "
        "Check MT5 and ask Claude to reconcile the trade record."]
    assert "unconfirmed_order_pending" not in eng.gate.account_reasons(eng.state)


# ---------------------------------------------------------------------------------------------- conditions 3-6
def _mt5_deals(position: int, *, exit_comment: str = "[sl 2396.00]", with_ids: bool = True,
               volume: float = 0.1) -> list[dict]:
    t = pd.Timestamp.now("UTC")
    rows = [{"ts_utc": t, "position_id": position, "entry": 0, "volume": volume, "price": 2400.4, "profit": 0.0,
             "commission": -0.35, "swap": 0.0, "fee": 0.0, "comment": PID[-31:], "reason": 3},
            {"ts_utc": t + pd.Timedelta(seconds=9), "position_id": position, "entry": 1, "volume": volume,
             "price": 2396.0, "profit": -44.0, "commission": -0.35, "swap": 0.0, "fee": 0.0, "comment": exit_comment,
             "reason": 4}]
    return rows if with_ids else [{k: v for k, v in r.items() if k != "position_id"} for r in rows]


def test_an_exit_deal_without_our_comment_is_found_by_position_id(tmp_path, clock):
    pb = _broker()
    eng = _sent(tmp_path, pb)
    pb.mt5_deals = _mt5_deals(77, with_ids=False)    # no position ids: never resolved without a record
    _look(eng)
    assert eng._orders[PID].status == "ambiguous" and eng._closes == []
    pb.mt5_deals = _mt5_deals(77)
    _look(eng)
    eng._flush_closed()
    assert eng._orders[PID].status == "filled" and eng._orders[PID].position_id == 77
    (trade,), _ = load_closed_trades(tmp_path)
    assert trade.position_id == 77 and trade.exit_reason == "stop" and trade.exit_price == 2396.0
    assert _notices(tmp_path) == [
        "Order confirmed, already closed: LONG 0.1 lots XAUUSD on icm-demo opened at 2400.40 and closed at 2396.00 "
        "(−$44.70). It's in the trade record."]


def test_an_entry_deal_alone_is_a_close_in_flight_and_keeps_looking(tmp_path, clock):
    pb = _broker()
    eng = _sent(tmp_path, pb)
    pb.mt5_deals = _mt5_deals(77)[:1]
    for s in (10, 200):
        _at(clock, eng, s)
        _look(eng)
    assert eng._orders[PID].status == "ambiguous" and _notices(tmp_path) == []


def test_a_partial_ambiguous_fill_records_the_dealt_lots(tmp_path, clock):
    pb = _broker(fill=0.2)
    eng = _sent(tmp_path, pb, lots=0.37)
    [pos] = pb.positions()
    _look(eng)
    assert eng.open[pos.position_id].initial_lots == 0.2 and eng.open[pos.position_id].lots == 0.2
    assert _notices(tmp_path)[0].startswith("Order confirmed: LONG 0.2 of 0.37 lots XAUUSD on icm-demo is open at ")
    pb2 = _broker()
    eng2 = _sent(tmp_path / "closed", pb2, lots=0.37)
    pb2.mt5_deals = _mt5_deals(78, volume=0.2)
    _look(eng2)
    eng2._flush_closed()
    (trade,), _ = load_closed_trades(tmp_path / "closed")
    assert trade.lots == 0.2


def test_a_fill_found_with_no_stop_gets_its_stop_put_back_in_the_same_no_quote_pass(tmp_path, clock):
    pb = _broker(fill=0.1)
    eng = _sent(tmp_path, pb)
    [pos] = pb.positions()
    pb._positions[pos.position_id].sl = None
    pb._positions[pos.position_id].tp = None
    eng.no_quote_pass()
    assert pb._positions[pos.position_id].sl == SL and pb._positions[pos.position_id].tp == TP
    assert _notices(tmp_path) == [
        f"Order confirmed: LONG 0.1 lots XAUUSD on icm-demo is open at {pos.open_price:.2f} (the broker confirmed it "
        f"late). Its stop was missing or wider at the broker, so the engine set stop 2396.00 and target 2406.00; exits "
        f"are automatic."]


def test_an_adopted_position_whose_stop_cannot_be_set_tells_the_owner_to_set_it(tmp_path, clock):
    pb = _broker(fill=0.1)
    pb.modify_ok = False
    eng = _sent(tmp_path, pb)
    [pos] = pb.positions()
    pb._positions[pos.position_id].sl = 2390.0                       # wider than the stop sent
    _look(eng)
    assert eng.open[pos.position_id].sl == SL                         # the reconcile pass retries it with backoff
    assert _notices(tmp_path) == [
        f"⚠️ Order confirmed, but without its stop: LONG 0.1 lots XAUUSD on icm-demo is open at {pos.open_price:.2f} and "
        f"the engine couldn't set stop 2396.00. Set stop 2396.00 in MT5 now."]


def test_a_fill_after_no_trade_was_reported_sends_one_correction(tmp_path, clock):
    pb = _broker()
    eng = _sent(tmp_path, pb)
    _at(clock, eng, 1)
    _look(eng)
    _at(clock, eng, 130)
    _look(eng)
    assert eng._orders[PID].status == "unfilled"
    PaperBroker.place_order(pb, OrderIntent(client_order_id=PID, symbol="XAUUSD", side=1, lots=0.1, sl=SL, tp=TP,
                                            magic=eng._magic(AGENT.family), comment=PID[-31:]))   # it turns up late
    [pos] = pb.positions()
    _look(eng)
    _look(eng)
    assert eng._orders[PID].status == "filled" and eng._orders[PID].notified == "opened_late"
    assert _notices(tmp_path)[1:] == [
        f"Correction: the order did open after all. LONG 0.1 lots XAUUSD on icm-demo is open at {pos.open_price:.2f}; "
        f"the earlier \"not placed\" notice was wrong. Stop 2396.00, target 2406.00; exits are automatic."]


# ---------------------------------------------------------------------------------------------- paths and restarts
def test_the_look_up_runs_with_no_ticks_through_no_quote_pass(tmp_path, clock):
    pb = _broker(fill=0.1)
    eng = _sent(tmp_path, pb)
    [pos] = pb.positions()
    eng.no_quote_pass()
    assert eng._orders[PID].status == "filled" and pos.position_id in eng.open
    assert len(_notices(tmp_path)) == 1


def test_an_auto_unconfirmed_result_is_resolved_the_same_way_and_auto_entries_stay_paused_for_the_bar(tmp_path,
                                                                                                    clock):
    pb = _broker(fill=0.1)
    eng, _ = _engine(tmp_path, pb, approval_mode="auto")
    res = eng._execute(_prop(PID), AGENT, 0.1, SL, TP, requested=2400.2)
    close_ts = T0.floor("15min")
    eng._after_auto_order(res, GateDecision(allowed=True, lots=0.1, stop_distance=4.0), 1, close_ts)
    assert eng._auto_paused == (close_ts, "unconfirmed_order") and eng._orders[PID].status == "ambiguous"
    assert eng._auto_action(res) == "auto_unconfirmed:10012"
    assert "unconfirmed_order_pending" in eng.gate.account_reasons(eng.state)
    _look(eng)
    assert eng._orders[PID].status == "filled" and len(_notices(tmp_path)) == 1


def test_a_restart_resolves_an_unconfirmed_row_as_before(tmp_path, clock):
    pb = _broker(fill=0.1)
    _sent(tmp_path, pb)
    [pos] = pb.positions()
    again, _ = _engine(tmp_path, pb)
    assert again._orders[PID].status == "filled" and again.open[pos.position_id].initial_sl == SL
    assert len(_notices(tmp_path)) == 1


def test_one_resolution_notice_per_order_across_a_restart_and_a_reconcile_adopt(tmp_path, clock):
    pb = _broker(fill=0.1)
    eng = _sent(tmp_path, pb)
    [pos] = pb.positions()
    eng.open.pop(pos.position_id, None)
    eng._adopt(pos)                                   # the reconcile loop met it first
    _look(eng)
    again, _ = _engine(tmp_path, pb)
    _look(again)
    again.open.pop(pos.position_id, None)
    again._adopt(pos)
    assert len(_notices(tmp_path)) == 1 and again._orders[PID].notified == "opened"


def test_a_corrupt_ambiguous_row_stops_the_start_loudly(tmp_path, clock):
    pb = _broker()
    _sent(tmp_path, pb)
    path = tmp_path / "orders_icm-demo.json"
    saved = json.loads(path.read_text())
    saved["sent"][PID]["lots"] = "many"
    path.write_text(json.dumps(saved))
    with pytest.raises(ValueError):
        _engine(tmp_path, pb)


# ---------------------------------------------------------------------------------------------- condition 1
def test_entries_are_refused_while_an_order_is_unconfirmed_and_allowed_once_it_resolves(tmp_path, clock):
    pb = _broker(fill=0.1)
    eng = _sent(tmp_path, pb)
    assert eng.state.unconfirmed_order and "unconfirmed_order_pending" in eng.gate.account_reasons(eng.state)
    # a late 10012 filled at the broker, unseen: the next entry is refused, never sized on understated exposure
    second = eng.gate.check(_intent(), eng.state)
    assert not second.allowed and "unconfirmed_order_pending" in second.reasons and second.lots == 0.0
    eng._write_state()
    state = json.loads((tmp_path / "engine_icm-demo.json").read_text())
    assert "unconfirmed_order_pending" in state["entry_blocks"] and state["unconfirmed_orders"]["count"] == 1
    _look(eng)
    assert not eng.state.unconfirmed_order and "unconfirmed_order_pending" not in eng.gate.account_reasons(eng.state)


def test_a_card_refused_at_approval_for_an_unconfirmed_order_says_so_in_words():
    assert outcome_text("GATE_REFUSED: unconfirmed_order_pending") == (
        "🛑 NOT PLACED: approved, but the risk check refused it at approval (an earlier order isn't confirmed by the "
        "broker yet)")


# ---------------------------------------------------------------------------------------------- the copy
FACTS = Confirmation(outcome="pending", symbol="XAUUSD", entry=2400.4, exit=2396.0, pnl=-8.2, currency="GBP")


@pytest.mark.parametrize(("outcome", "text"), [
    ("pending", CARD_PENDING),
    ("opened", "✅ OPENED: the broker confirmed it late. Stop, target and exit are automatic."),
    ("opened_closed", "ℹ️ OPENED AND CLOSED: the broker confirmed it late; it opened at 2400.40 and closed at 2396.00 "
                      "(−£8.20)."),
    ("no_trade", "🛑 NOT PLACED: approved and sent, but the broker has no trade for it (checked twice, at least 2 "
                 "minutes apart). No trade was opened."),
    ("opened_late", "✅ OPENED AFTER ALL: the broker confirmed it after it was reported not placed. Stop, target and "
                    "exit are automatic."),
    ("still_unknown", "⚠️ STILL NOT CONFIRMED after 10 minutes. The engine keeps looking; check MT5 for an open XAUUSD "
                      "trade."),
])
def test_every_telegram_card_row_reads_byte_for_byte(outcome, text):
    assert card_text(FACTS.model_copy(update={"outcome": outcome})) == text


@pytest.mark.parametrize(("outcome", "heading", "text", "tone"), [
    ("pending", "Not confirmed.", "Sent, but the broker didn't confirm it. The engine looks it up at the broker and "
     "tells you what it finds, or tells you after 10 minutes that it still doesn't know." + GATE, "warn"),
    ("opened", "Opened.", "The broker confirmed it late; stop, target and exit are automatic.", "ok"),
    ("opened_closed", "Opened and already closed.", "The broker confirmed it late: it opened at 2400.40 and closed at "
     "2396.00 (−£8.20). It's in the trade record.", "neutral"),
    ("no_trade", "Not placed.", "Sent, but the broker has no trade for it (checked twice, at least 2 minutes apart); no "
     "trade was opened.", "bad"),
    ("opened_late", "Opened after all.", "The broker confirmed it after it was reported not placed; stop, target and "
     "exit are automatic.", "ok"),
    ("still_unknown", "Still not confirmed.", "After 10 minutes the broker hasn't shown whether it opened. The engine "
     "keeps looking; check MT5 for an open XAUUSD trade." + GATE, "warn"),
])
def test_every_dashboard_note_reads_byte_for_byte(outcome, heading, text, tone):
    n = note(FACTS.model_copy(update={"outcome": outcome}))
    assert (n.outcome, n.heading, n.text, n.tone) == (outcome, heading, text, tone)


NOTICE_FACTS: dict[str, Any] = dict(side=1, lots=0.37, dealt=None, symbol="XAUUSD", account="icm-demo", entry=2400.4,
                                    exit=2412.7, pnl=12.3, currency="GBP", sl=2396.0, tp=2406.0)


@pytest.mark.parametrize(("kind", "text"), [
    ("opened", "Order confirmed: LONG 0.37 lots XAUUSD on icm-demo is open at 2400.40 (the broker confirmed it late). "
               "Stop 2396.00, target 2406.00; exits are automatic."),
    ("opened_stop_set", "Order confirmed: LONG 0.37 lots XAUUSD on icm-demo is open at 2400.40 (the broker confirmed it "
                        "late). Its stop was missing or wider at the broker, so the engine set stop 2396.00 and target "
                        "2406.00; exits are automatic."),
    ("opened_no_stop", "⚠️ Order confirmed, but without its stop: LONG 0.37 lots XAUUSD on icm-demo is open at 2400.40 "
                       "and the engine couldn't set stop 2396.00. Set stop 2396.00 in MT5 now."),
    ("opened_closed", "Order confirmed, already closed: LONG 0.37 lots XAUUSD on icm-demo opened at 2400.40 and closed "
                      "at 2412.70 (+£12.30). It's in the trade record."),
    ("no_trade", "Order not placed after all: the broker has no trade for LONG 0.37 lots XAUUSD on icm-demo (checked "
                 "twice, at least 2 minutes apart). Nothing to do; if it turns up later, you'll be told."),
    ("opened_late", "Correction: the order did open after all. LONG 0.37 lots XAUUSD on icm-demo is open at 2400.40; the "
                    "earlier \"not placed\" notice was wrong. Stop 2396.00, target 2406.00; exits are automatic."),
    ("still_unknown", "Order still not confirmed after 10 minutes: the broker hasn't shown whether LONG 0.37 lots XAUUSD "
                      "on icm-demo opened. The engine keeps looking and tells you when it knows. New entries wait until "
                      "it's settled. Check MT5: if a XAUUSD trade is open without a stop, set stop 2396.00."),
    ("gave_up", "Stopped looking: after 7 days the broker still hasn't shown whether LONG 0.37 lots XAUUSD on icm-demo "
                "opened. Check MT5 and ask Claude to reconcile the trade record."),
])
def test_every_owner_notice_reads_byte_for_byte(kind, text):
    assert notice_text(kind, **NOTICE_FACTS) == text


def test_a_part_fill_names_the_dealt_lots_of_the_lots_sent():
    assert notice_text("opened", **{**NOTICE_FACTS, "dealt": 0.2}).startswith(
        "Order confirmed: LONG 0.2 of 0.37 lots XAUUSD on icm-demo is open at 2400.40")
    assert notice_text("opened_closed", **{**NOTICE_FACTS, "dealt": 0.2, "side": -1}).startswith(
        "Order confirmed, already closed: SHORT 0.2 of 0.37 lots XAUUSD")


def test_no_owner_string_carries_a_proposal_id_the_word_retcode_a_code_number_or_raw_text():
    texts = [card_text(FACTS.model_copy(update={"outcome": o})) for o in
             ("pending", "opened", "opened_closed", "no_trade", "opened_late", "still_unknown")]
    texts += [note(FACTS.model_copy(update={"outcome": o})).text for o in ("pending", "still_unknown", "no_trade")]
    texts += [notice_text(k, **NOTICE_FACTS) for k in ("opened", "opened_stop_set", "opened_no_stop", "opened_closed",
                                                      "no_trade", "opened_late", "still_unknown", "gave_up")]
    texts += [health_line(2, "12 min", "XAUUSD")]
    for t in texts:
        for banned in (PID, "retcode", "10012", "Request timed out", "not_confirmed", "51234567"):
            assert banned not in t, (banned, t)


def test_the_health_line_uses_real_plurals():
    assert health_line(1, "10 min", "XAUUSD").startswith("1 order not confirmed by the broker, oldest 10 min: ")
    assert health_line(2, "1.5 h", "XAUUSD").startswith("2 orders not confirmed by the broker, oldest 1.5 h: ")


def test_an_unknown_outcome_falls_back_to_the_pending_texts():
    raw = Confirmation.model_construct(outcome="sideways", symbol="XAUUSD")  # type: ignore[arg-type]  # a newer value
    assert card_text(raw) == CARD_PENDING and note(raw).heading == "Not confirmed."
    assert card_text(None) == CARD_PENDING
