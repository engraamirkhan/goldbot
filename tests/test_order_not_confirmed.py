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

from goldbot.engine import Engine, EngineConfig, auto_entry, unconfirmed
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
    eng._last_tick = pb.last_tick("XAUUSD")       # a fresh quote (replay clock): looks can be clean from here on
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
    """Condition 2: a restart an hour later has an old row but no clean look in this process: its first look (before
    any quote) never declares `unfilled`; an unreadable look between two clean ones neither counts nor resets the
    first."""
    pb = _broker()
    eng = _sent(tmp_path, pb)
    _at(clock, eng, 3600)
    again, _ = _engine(tmp_path, pb)                 # the restart's look (`_load_orders`): no quote yet, not clean
    assert again._orders[PID].status == "ambiguous" and PID not in again._looks.first_clean
    _at(clock, again, 3640)
    _look(again)                                     # the first clean look
    _at(clock, again, 3700)
    pb.deals_fail = True
    _look(again)
    pb.deals_fail = False
    _at(clock, again, 3759)
    _look(again)
    assert again._orders[PID].status == "ambiguous"
    _at(clock, again, 3760)
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
    auto_entry.after_order(eng, res, GateDecision(allowed=True, lots=0.1, stop_distance=4.0), _prop(PID), close_ts)
    assert eng._auto_paused == (close_ts, "unconfirmed_order") and eng._orders[PID].status == "ambiguous"
    assert auto_entry.action(res) == "auto_unconfirmed:10012"
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


# ---------------------------------------------------------------------------------------------- review round 1
class _Polling(_BrokerRefuses):
    """The broker is slow to answer: while the order is in flight, the Telegram service polls its outbox."""

    def __init__(self, retcode: int, **kw: Any) -> None:
        super().__init__(retcode, message="Request timed out", **kw)
        self.poll: Any = None
        self.seen: list[str] = []

    def place_order(self, intent: OrderIntent) -> OrderResult:
        if self.poll is not None:
            self.seen += self.poll()
        return super().place_order(intent)


def _beat(tmp_path: Path, age_s: float = 0.0) -> None:
    import time

    from goldbot.ops.watchdog import loop_heartbeat_path
    t = time.time() - age_s
    loop_heartbeat_path(tmp_path, "icm-demo").write_text(json.dumps({"ts": t, "started": t, "phase": "loop"}))


def _through_finish(tmp_path: Path, pb: PaperBroker) -> tuple[Any, Any, Any]:
    """The real decision path: the card is proposed, approved on the ApprovalCenter (`_finish` archives, then the
    engine applies it), with the outbox watching the card."""
    from goldbot.telegram.outbox import Outbox
    from tests.test_approved_card_cap import _card
    from tests.test_engine_clock import WED
    from tests.test_engine_clock import _engine as _clock_engine
    from tests.test_engine_clock import _intent as _clock_intent
    from tests.test_engine_clock import _tick as _clock_tick
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "classifier_icm-demo.json").write_text(json.dumps({"class": "raw"}))
    pb.on_tick(_clock_tick(WED))
    eng, _, _ = _clock_engine(tmp_path, pb=pb)
    bus = eng.center.bus = ApprovalBus(tmp_path)
    eng._refresh_account(_clock_tick(WED))
    prop = _card(eng, _clock_intent().model_copy(update=eng._sizing_terms(1, 2400.2, 4.0)))
    eng.center.propose(prop)
    outbox = Outbox(tmp_path, bus)
    outbox.mark_sent(prop.proposal_id, [(1, 2)])
    return eng, prop, outbox


@pytest.mark.parametrize("retcode", [10012, 10009])
def test_polling_the_outbox_while_the_order_is_in_flight_never_reports_approved(tmp_path, retcode):
    """Review HIGH (race): `_finish` archives the approval before the engine sends it; the outbox must not report
    (and forget) the card until the engine has the broker's answer."""
    pb = _Polling(retcode, refusals=0 if retcode == 10009 else 1, equity=10_000)
    eng, prop, outbox = _through_finish(tmp_path, pb)
    _beat(tmp_path)
    pb.poll = lambda: [o for pid, o, _ in outbox.finished() if pid == prop.proposal_id]
    eng.center.decide(prop.proposal_id, OWNER, True)
    assert pb.calls == 1 and pb.seen == []                       # in flight: the card is not reported yet
    [after] = [o for pid, o, _ in outbox.finished() if pid == prop.proposal_id]
    stored = eng.center.bus.get(prop.proposal_id)
    assert stored is not None and not stored.placing
    assert after == ("APPROVED" if retcode == 10009 else "GATE_REFUSED: not_confirmed")


def test_a_card_left_placing_by_a_silent_engine_reports_not_confirmed_never_approved(tmp_path):
    from goldbot.telegram.approvals import Outcome
    from goldbot.telegram.outbox import Outbox
    bus = ApprovalBus(tmp_path)
    p = _prop(PID).model_copy(update={"outcome": Outcome.APPROVED, "placing": True})
    bus.archive(p)
    outbox = Outbox(tmp_path, bus)
    outbox.mark_sent(PID, [(1, 2)])
    _beat(tmp_path)
    assert outbox.finished() == []                                # the engine is alive and sending: wait
    _beat(tmp_path, age_s=600)                                     # the engine went silent mid-send
    assert [o for _, o, _ in outbox.finished()] == ["GATE_REFUSED: not_confirmed"]
    assert outcome_text("GATE_REFUSED: not_confirmed") == CARD_PENDING


def test_a_send_that_raised_refuses_the_next_entry_within_sending_resolve_s(tmp_path, clock):
    """Review MEDIUM (condition 1 on `sending`): the single-threaded engine never checks the gate during its own send,
    so a `sending` row at gate time is a send that raised, and its exposure is unknown."""
    class _Raises(_Unanswered):
        def place_order(self, intent: OrderIntent) -> OrderResult:
            super().place_order(intent)
            raise RuntimeError("terminal connection lost")
    pb = _Raises(fill=0.1, equity=10_000)
    pb.on_tick(_tick(T0))
    eng, _ = _engine(tmp_path, pb)
    with pytest.raises(RuntimeError):
        eng._execute(_prop(PID), AGENT, 0.1, SL, TP, requested=2400.2)
    assert eng._orders[PID].status == "sending"
    assert "unconfirmed_order_pending" in eng.gate.account_reasons(eng.state)
    assert not eng.gate.check(_intent(), eng.state).allowed


def test_a_restart_with_a_sending_row_and_no_position_takes_the_two_look_rule(tmp_path, clock):
    """Review MEDIUM (condition 3 on the `sending` path): with no position the row is never marked filled or unfilled
    on one look; it becomes `ambiguous` and the look-up decides (an entry deal without a position id never resolves)."""
    eng, pb = _engine(tmp_path, _broker())
    assert isinstance(pb, _Unanswered)
    eng._orders_record(PID, agent_id=AGENT.agent_id, side=1, magic=eng._magic(AGENT.family), lots=0.1, max_bars=4,
                       sl=SL, tp=TP, ts=pd.Timestamp.now("UTC"))       # crashed before order_send returned
    pb.mt5_deals = _mt5_deals(77, with_ids=False)[:1]
    clock[0] = pd.Timestamp.now("UTC")
    again, _ = _engine(tmp_path, pb)
    assert again._orders[PID].status == "ambiguous" and again._closes == []
    pb.mt5_deals = []
    clock[0] += pd.Timedelta(seconds=10)
    _look(again)
    clock[0] += pd.Timedelta(seconds=120)
    _look(again)
    assert again._orders[PID].status == "unfilled"


def test_no_look_is_clean_with_a_stale_quote_or_in_no_quote_pass(tmp_path, clock):
    """Review MEDIUM (condition 2): a terminal that lost the trade server answers with cached positions and deals, so
    two successful reads are not enough: a look is clean only with a quote no older than stale_tick_seconds, and never
    in `no_quote_pass` (no "no trade" while the market is closed or the feed is down)."""
    pb = _broker()
    eng = _sent(tmp_path, pb)
    for s in (5, 400):                                          # no quote: never clean, however long
        _at(clock, eng, s)
        eng._looks.last = None
        eng.no_quote_pass()
    assert eng._orders[PID].status == "ambiguous" and PID not in eng._looks.first_clean
    eng._quote_invalid_since = None
    tick = eng._last_tick
    eng.cfg.live_clock = True
    eng.clock = lambda: tick.ts_utc + pd.Timedelta(seconds=21)
    for s in (410, 600):
        _at(clock, eng, s)
        _look(eng)
    assert eng._orders[PID].status == "ambiguous" and PID not in eng._looks.first_clean
    eng.clock = lambda: tick.ts_utc + pd.Timedelta(seconds=20)    # at the limit: fresh
    for s in (610, 730):
        _at(clock, eng, s)
        _look(eng)
    assert eng._orders[PID].status == "unfilled"


def test_one_failing_look_does_not_stop_the_other_rows_or_the_stop_reinstatement(tmp_path, clock, monkeypatch):
    """Review LOW (code 1): a look that raises is logged and waits; the other rows and the reconcile loop go on."""
    pb = _broker(fill=0.1)
    eng = _sent(tmp_path, pb)
    [pos] = pb.positions()
    eng._orders["icm-demo-72-b"] = eng._orders[PID].model_copy(update={"client_order_id": "icm-demo-72-b"})
    real = unconfirmed._look

    def look(e: Any, cid: str, *a: Any, **kw: Any) -> bool:
        if cid == "icm-demo-72-b":
            raise KeyError("a malformed deal row")
        return real(e, cid, *a, **kw)
    monkeypatch.setattr(unconfirmed, "_look", look)
    pb._positions[pos.position_id].sl = None
    eng._orders = dict(sorted(eng._orders.items(), reverse=True))  # the failing row first
    _look(eng)
    assert eng._orders[PID].status == "filled" and pb._positions[pos.position_id].sl == SL
    assert eng._orders["icm-demo-72-b"].status == "ambiguous"


def test_a_late_adopted_trade_is_in_the_open_table_before_its_row_is_saved_and_the_owner_told(tmp_path, clock):
    """Review LOW (code 2): `adopted` saves the table and sends the notice; the trade it names is already in it."""
    pb = _broker(fill=0.1)
    eng = _sent(tmp_path, pb)
    [pos] = pb.positions()
    seen: list[dict] = []
    bus = eng.center.bus
    orig = bus.notify_owner

    def notify(text: str) -> None:
        seen.append(json.loads((tmp_path / "orders_icm-demo.json").read_text()))
        orig(text)
    bus.notify_owner = notify
    eng._adopt(pos)
    assert seen and str(pos.position_id) in seen[0]["open"] and seen[0]["sent"][PID]["status"] == "filled"


def test_a_late_adopted_trade_keeps_the_tighter_of_the_sent_and_the_broker_stop(tmp_path, clock):
    """Review LOW (TS 6): the engine's stop floor is never looser than the stop already at the broker."""
    pb = _broker(fill=0.1)
    eng = _sent(tmp_path, pb)
    [pos] = pb.positions()
    pb._positions[pos.position_id].sl = 2398.0                       # tighter than the stop sent (2396)
    assert eng._adopt(pb.positions()[0]).sl == 2398.0
    pb2 = _broker(fill=0.1)
    eng2 = _sent(tmp_path / "b", pb2)
    [pos2] = pb2.positions()
    pb2._positions[pos2.position_id].sl = 2398.0
    _look(eng2)
    assert eng2.open[pos2.position_id].sl == 2398.0 and pb2._positions[pos2.position_id].sl == 2398.0


def test_a_stop_that_cannot_be_set_with_the_price_already_through_it_says_the_engine_closes_it(tmp_path, clock):
    """Review LOW (TS 5): the per-tick stop check closes the trade at the first quote, and the notice says so."""
    pb = _broker(fill=0.1)
    pb.modify_ok = False
    eng = _sent(tmp_path, pb)
    [pos] = pb.positions()
    pb._positions[pos.position_id].sl = None
    pb.on_tick(_tick(T0, bid=2395.0))                                  # already through the stop sent (2396)
    _look(eng)
    assert _notices(tmp_path) == [
        f"⚠️ Order confirmed, but without its stop: LONG 0.1 lots XAUUSD on icm-demo is open at {pos.open_price:.2f} and "
        f"the engine couldn't set stop 2396.00. The price is already past stop 2396.00, so the engine closes the trade "
        f"at the first quote."]


# ---------------------------------------------------------------------------------------------- BACKLOG 84
class _SendRaises(PaperBroker):
    """The order reaches the broker and fills, then the send raises (the terminal connection drops mid-call); the
    Telegram service polls its outbox while the order is in flight."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.poll: Any = None
        self.seen: list[str] = []

    def place_order(self, intent: OrderIntent) -> OrderResult:
        if self.poll is not None:
            self.seen += self.poll()
        super().place_order(intent)
        raise RuntimeError("terminal connection lost")


def test_a_send_that_raises_archives_the_card_not_confirmed_and_never_leaves_it_placing(tmp_path):
    """BACKLOG 84: "if `on_decision` raises, `_finish` archives the card not confirmed (placing cleared) then
    re-raises", so the outbox and the dashboard show NOT CONFIRMED at once, never APPROVED and never "submitted"."""
    pb = _SendRaises(equity=10_000)
    eng, prop, outbox = _through_finish(tmp_path, pb)
    _beat(tmp_path)
    pb.poll = lambda: [o for pid, o, _ in outbox.finished() if pid == prop.proposal_id]
    with pytest.raises(RuntimeError, match="terminal connection lost"):
        eng.center.decide(prop.proposal_id, OWNER, True)
    assert pb.seen == []                                          # in flight: not reported
    stored = eng.center.bus.get(prop.proposal_id)
    assert stored is not None and not stored.placing
    assert stored.gate_refusal == ["not_confirmed"]
    assert stored.not_confirmed is not None and stored.not_confirmed.outcome == "pending"
    assert [o for pid, o, _ in outbox.finished() if pid == prop.proposal_id] == ["GATE_REFUSED: not_confirmed"]
    assert eng._orders[prop.proposal_id].status == "sending"     # entries stay refused until it is settled
    # the fill the send left at the broker, met by the reconcile loop: the owner is told it is open, the card says so
    [pos] = pb.positions()
    eng._adopt(pos)
    assert [n for n in _notices(tmp_path) if n.startswith("Order confirmed: LONG")]
    card = eng.center.bus.get(prop.proposal_id)
    assert card is not None and card.not_confirmed is not None and card.not_confirmed.outcome == "opened"
    assert card.gate_refusal == ["not_confirmed"] and not card.placing


def test_a_send_that_raises_after_a_refusal_was_archived_keeps_that_refusal(tmp_path):
    """`_finish` marks NOT CONFIRMED only a card nothing has answered yet: a refusal already archived stands."""
    from goldbot.telegram.approvals import Outcome
    bus = ApprovalBus(tmp_path)

    def refuse_then_raise(p: Any) -> None:
        p.gate_refusal = ["broker_rejected"]
        bus.archive(p)
        raise RuntimeError("state write failed")
    center = ApprovalCenter({OWNER}, on_decision=refuse_then_raise, bus=bus)
    center.propose(_prop(PID))
    with pytest.raises(RuntimeError):
        center.decide(PID, OWNER, True)
    stored = bus.get(PID)
    assert stored is not None and stored.outcome == Outcome.APPROVED and not stored.placing
    assert stored.gate_refusal == ["broker_rejected"] and stored.not_confirmed is None


def test_a_fill_after_the_silent_engine_fallback_still_sends_the_opened_notice_and_updates_the_card(tmp_path,
                                                                                                    clock):
    """BACKLOG 84: the engine went silent mid-send (it died with the card `placing`), so the outbox reported the card
    NOT CONFIRMED; the restarted engine's look-up finds the fill: the owner gets the "opened" notice and the card
    says so."""
    from goldbot.telegram.approvals import Outcome
    from goldbot.telegram.outbox import Outbox
    pb = _SendRaises(equity=10_000)
    pb.on_tick(_tick(T0))
    eng, _ = _engine(tmp_path, pb)
    bus = eng.center.bus
    assert bus is not None
    bus.archive(_prop(PID).model_copy(update={"outcome": Outcome.APPROVED, "placing": True}))   # `_finish` archived it
    with pytest.raises(RuntimeError):
        eng._execute(_prop(PID), AGENT, 0.1, SL, TP, requested=2400.2)                       # then the engine died
    outbox = Outbox(tmp_path, bus)
    outbox.mark_sent(PID, [(1, 2)])
    _beat(tmp_path, age_s=600)
    assert [o for _, o, _ in outbox.finished()] == ["GATE_REFUSED: not_confirmed"]           # the silent-engine fallback
    [pos] = pb.positions()
    again, _ = _engine(tmp_path, pb)                                                          # restart: the look-up
    assert again._orders[PID].status == "filled" and again.open[pos.position_id].initial_sl == SL
    assert _notices(tmp_path) == [
        f"Order confirmed: LONG 0.1 lots XAUUSD on icm-demo is open at {pos.open_price:.2f} (the broker confirmed it "
        f"late). Stop 2396.00, target 2406.00; exits are automatic."]
    card = bus.get(PID)
    assert card is not None and not card.placing and card.gate_refusal == ["not_confirmed"]
    assert card.not_confirmed is not None and card.not_confirmed.outcome == "opened"
    outbox.mark_sent(PID, [(1, 2)])                                # polled again: never APPROVED
    assert [o for _, o, _ in outbox.finished()] == ["GATE_REFUSED: not_confirmed"]
    _engine(tmp_path, pb)                                          # one notice per order, across another restart
    assert len(_notices(tmp_path)) == 1


def test_a_sending_row_found_filled_with_no_card_shown_unconfirmed_sends_no_notice(tmp_path, clock):
    """The notice follows the card: a crash-restart fill whose card was never shown NOT CONFIRMED (none on the bus:
    the auto path) is adopted as before, silently."""
    pb = _SendRaises(equity=10_000)
    pb.on_tick(_tick(T0))
    eng, _ = _engine(tmp_path, pb)
    with pytest.raises(RuntimeError):
        eng._execute(_prop(PID), AGENT, 0.1, SL, TP, requested=2400.2)
    again, _ = _engine(tmp_path, pb)
    assert again._orders[PID].status == "filled" and _notices(tmp_path) == []


# ---------------------------------------------------------------------------------------------- BACKLOG 85
class _ReadFails(PaperBroker):
    """The MT5 box can't be read at approval (`account()` raises) once `fail` is set: the approval path raises before
    `_execute` writes the order row. The Telegram service polls its outbox meanwhile."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.fail, self.sends = False, 0
        self.poll: Any = None
        self.seen: list[str] = []

    def account(self) -> Any:
        if self.fail:
            if self.poll is not None:
                self.seen += self.poll()
            from goldbot.execution.bridge import BridgeUnavailable
            raise BridgeUnavailable("bridge account: ConnectTimeout 10.0.0.9")
        return super().account()

    def place_order(self, intent: OrderIntent) -> OrderResult:
        self.sends += 1
        return super().place_order(intent)


def test_a_raise_before_the_order_row_archives_not_sent_never_approved_and_allows_the_next_entry(tmp_path):
    """BACKLOG 85: "`_on_decision` archives a "not sent" refusal code on a raise before the row exists": the card
    reads NOT PLACED (could not be sent), the outbox never returns APPROVED, no row exists, nothing is pending, so the
    next entry is not refused `unconfirmed_order_pending`, and the owner gets exactly one notice."""
    pb = _ReadFails(equity=10_000)
    eng, prop, outbox = _through_finish(tmp_path, pb)
    _beat(tmp_path)
    pb.fail = True
    pb.poll = lambda: [o for pid, o, _ in outbox.finished() if pid == prop.proposal_id]
    with pytest.raises(ConnectionError):
        eng.center.decide(prop.proposal_id, OWNER, True)
    assert pb.seen == [] and pb.sends == 0                        # in flight: not reported; nothing was sent
    stored = eng.center.bus.get(prop.proposal_id)
    assert stored is not None and not stored.placing and stored.not_confirmed is None
    assert stored.gate_refusal == ["not_sent:mt5_box"]
    [after] = [o for pid, o, _ in outbox.finished() if pid == prop.proposal_id]
    assert after == "GATE_REFUSED: not_sent:mt5_box"
    assert outcome_text(after) == ("🛑 NOT PLACED: approved, but the order could not be sent: the MT5 box couldn't be "
                                   "reached. Nothing was sent.")
    assert prop.proposal_id not in eng._orders and not unconfirmed.pending(eng._orders)
    assert "unconfirmed_order_pending" not in eng.gate.account_reasons(eng.state)
    notices = _notices(tmp_path)
    assert len(notices) == 1 and notices[0].startswith("Order not placed: LONG ")
    assert "could not be sent: the MT5 box couldn't be reached." in notices[0]
    assert prop.proposal_id not in notices[0] and "10.0.0.9" not in notices[0]
    assert [d for d in eng.decisions if d.get("action") == "not_sent"]
    eng._reconcile(None)                                          # the look-up has nothing to find: no second notice
    assert len(_notices(tmp_path)) == 1


def test_a_raise_in_the_engine_before_the_order_row_is_coded_not_sent_by_the_engine(tmp_path, monkeypatch):
    pb = _ReadFails(equity=10_000)
    eng, prop, _ = _through_finish(tmp_path, pb)

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("gate state corrupt")
    monkeypatch.setattr(eng.gate, "check", boom)
    with pytest.raises(RuntimeError, match="gate state corrupt"):
        eng.center.decide(prop.proposal_id, OWNER, True)
    stored = eng.center.bus.get(prop.proposal_id)
    assert stored is not None and stored.gate_refusal == ["not_sent:engine"] and not stored.placing
    assert prop.proposal_id not in eng._orders and pb.sends == 0
    assert len(_notices(tmp_path)) == 1


def test_a_raise_after_the_order_row_exists_keeps_not_confirmed_never_not_sent(tmp_path):
    """BACKLOG 84 stands once the row is written: the send may have reached the broker, so the card is NOT CONFIRMED
    and no "not sent" notice goes out."""
    pb = _SendRaises(equity=10_000)
    eng, prop, _ = _through_finish(tmp_path, pb)
    with pytest.raises(RuntimeError, match="terminal connection lost"):
        eng.center.decide(prop.proposal_id, OWNER, True)
    stored = eng.center.bus.get(prop.proposal_id)
    assert stored is not None and stored.gate_refusal == ["not_confirmed"]
    assert eng._orders[prop.proposal_id].status == "sending"
    assert not [n for n in _notices(tmp_path) if "could not be sent" in n]


# ---------------------------------------------------------------------------------------------- BACKLOG 86
class _SlowFill(PaperBroker):
    """A send that takes `took_s` of wall time (`offset`, added to the clock the outbox and the engine read) and then
    returns a plain fill; the outbox is polled meanwhile."""

    def __init__(self, took_s: float, offset: list[float], **kw: Any) -> None:
        super().__init__(**kw)
        self.took_s, self.offset = took_s, offset
        self.poll: Any = None
        self.seen: list[str] = []

    def place_order(self, intent: OrderIntent) -> OrderResult:
        self.offset[0] += self.took_s
        if self.poll is not None:
            self.seen += self.poll()
        return super().place_order(intent)


def _slow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, took_s: float, beat_age_s: float
          ) -> tuple[Any, Any, Any, Any]:
    import time
    offset = [0.0]
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + offset[0])
    pb = _SlowFill(took_s, offset, equity=10_000)
    eng, prop, outbox = _through_finish(tmp_path, pb)
    _beat(tmp_path, age_s=beat_age_s)                              # the last loop beat before the send
    pb.poll = lambda: [o for pid, o, _ in outbox.finished() if pid == prop.proposal_id]
    return eng, prop, outbox, pb


def _opened(tmp_path: Path) -> list[str]:
    return [n for n in _notices(tmp_path) if n.startswith("Order confirmed")]


def test_a_plain_fill_after_the_outbox_showed_not_confirmed_sends_one_opened_notice_and_updates_the_card(
        tmp_path, monkeypatch):
    """BACKLOG 86: the beat was 40 s old when the send began and the send took 25 s, so the outbox (heartbeat age
    >= PLACING_SILENT_S) showed the card NOT CONFIRMED; the send then returned a plain fill: exactly one "opened"
    notice and the card says so. Send duration alone (25 s) would have missed it."""
    eng, prop, outbox, pb = _slow(tmp_path, monkeypatch, 25.0, 40.0)
    eng.center.decide(prop.proposal_id, OWNER, True)
    assert pb.seen == ["GATE_REFUSED: not_confirmed"]
    [pos] = pb.positions()
    rec = eng._orders[prop.proposal_id]
    assert _opened(tmp_path) == [
        f"Order confirmed: LONG {rec.lots} lots XAUUSD on icm-demo is open at {pos.open_price:.2f} (the broker "
        f"confirmed it late). Stop {rec.sl:.2f}, target {rec.tp:.2f}; exits are automatic."]
    card = eng.center.bus.get(prop.proposal_id)
    assert card is not None and not card.placing and card.gate_refusal == ["not_confirmed"]
    assert card.not_confirmed is not None and card.not_confirmed.outcome == "opened"
    eng._reconcile(None)                                          # the reconcile loop adds no second notice
    assert len(_opened(tmp_path)) == 1


def test_a_70_s_send_from_a_fresh_beat_under_placing_max_is_noticed_because_the_outbox_would_show_it(
        tmp_path, monkeypatch):
    from goldbot.telegram.outbox import PLACING_MAX_S
    eng, prop, _, pb = _slow(tmp_path, monkeypatch, 70.0, 0.0)
    assert 70.0 < PLACING_MAX_S
    eng.center.decide(prop.proposal_id, OWNER, True)
    # the loop beat 70 s ago is >= PLACING_SILENT_S by the time the send returns: the outbox would show it
    assert len(_opened(tmp_path)) == 1


def test_a_fill_whose_outbox_test_stays_false_sends_no_notice_and_the_card_stays_approved(tmp_path, monkeypatch):
    eng, prop, _, pb = _slow(tmp_path, monkeypatch, 5.0, 0.0)
    eng.center.decide(prop.proposal_id, OWNER, True)
    assert pb.seen == [] and _opened(tmp_path) == []
    card = eng.center.bus.get(prop.proposal_id)
    assert card is not None and not card.placing and card.gate_refusal is None and card.not_confirmed is None


def test_the_notice_follows_the_outbox_test_at_the_exact_heartbeat_age_boundary(tmp_path, monkeypatch):
    from goldbot.telegram.outbox import PLACING_SILENT_S
    for took_s, expect in ((PLACING_SILENT_S - 2.0, 0), (PLACING_SILENT_S + 2.0, 1)):
        sub = tmp_path / str(expect)
        eng, prop, _, _ = _slow(sub, monkeypatch, took_s, 0.0)
        eng.center.decide(prop.proposal_id, OWNER, True)
        assert len(_opened(sub)) == expect, took_s
