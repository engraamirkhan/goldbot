"""Trading-safety re-verification MEDIUM-A: in MT5 a position's ticket is the ticket of the ORDER that opened it, not of
the deal that filled it. The adapter reported `position_id=res.deal`, so the engine keyed `self.open` by a number the
terminal never lists as a position: the next sweep could record a phantom close and reconciliation adopt the real
position as an orphan. The adapter now resolves the position from the filling deal. `history_deals_get(ticket=X)`
filters on the ORDER ticket (review MEDIUM-1), so the adapter asks for the order's deals and takes the position of the
deal whose ticket is the fill's; it falls back to the order ticket when the deal cannot be read.

Needs verification on the demo terminal (fake mt5 here)."""
from collections import namedtuple
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from goldbot.engine import Engine, EngineConfig
from goldbot.execution import mt5_adapter
from goldbot.execution.broker import OrderIntent
from goldbot.specialists.trend import TrendSpecialist
from goldbot.telegram.approvals import Proposal

DEAL, ORDER = 111, 222
MTick = namedtuple("MTick", "time_msc bid ask flags")
Sym = namedtuple("Sym", "name digits point trade_contract_size volume_min volume_step volume_max trade_stops_level "
                        "filling_mode trade_mode visible")
Send = namedtuple("Send", "retcode order deal volume price comment")
Chk = namedtuple("Chk", "retcode comment")
Deal = namedtuple("Deal", "ticket order position_id")
Pos = namedtuple("Pos", "ticket symbol type volume price_open sl tp magic comment time_msc profit")
Acc = namedtuple("Acc", "login equity balance margin margin_free leverage currency server trade_mode")


def _fake(deal_lookup: Any) -> SimpleNamespace:
    """A terminal that fills every order as deal 111 of order 222 and then lists position 222."""
    book: list[Pos] = []

    def order_send(req: dict) -> Send:
        book.append(Pos(ORDER, req["symbol"], req["type"], req["volume"], 2400.2, req["sl"], req["tp"], req["magic"],
                        req["comment"], 1_741_168_800_000, 0.0))
        return Send(10009, ORDER, DEAL, req["volume"], 2400.2, "done")

    def positions_get(**kw: Any) -> list[Pos]:
        return list(book)

    def history_deals_get(*a: Any, **kw: Any) -> Any:
        if "ticket" in kw:                              # MT5: `ticket=` selects the deals of that ORDER
            return deal_lookup(kw["ticket"])
        return ()

    return SimpleNamespace(
        initialize=lambda **k: True, symbol_select=lambda s, on: True, last_error=lambda: (1, "err"),
        shutdown=lambda: None, ORDER_TYPE_BUY=0, ORDER_TYPE_SELL=1, POSITION_TYPE_BUY=0, TRADE_ACTION_DEAL=1,
        TRADE_ACTION_SLTP=6, ORDER_TIME_GTC=0, ORDER_FILLING_FOK=0, ORDER_FILLING_IOC=1, ORDER_FILLING_RETURN=2,
        COPY_TICKS_ALL=-1, symbol_info=lambda s: Sym(s, 2, 0.01, 100.0, 0.01, 0.01, 50.0, 0, 2, 4, True),
        symbol_info_tick=lambda s: MTick(1_741_168_800_000, 2400.0, 2400.2, 6),
        account_info=lambda: Acc(1, 10_000.0, 10_000.0, 0.0, 10_000.0, 20, "USD", "s", 0),
        order_check=lambda req: Chk(0, ""), order_send=order_send, positions_get=positions_get,
        history_deals_get=history_deals_get, book=book)


def _adapter(monkeypatch: pytest.MonkeyPatch, fake: SimpleNamespace) -> mt5_adapter.MT5Broker:
    monkeypatch.setattr(mt5_adapter, "mt5", fake)
    return mt5_adapter.MT5Broker(terminal_path="", login=None, password=None, server="s", server_tz="Europe/Athens",
                                 symbol="XAUUSD", account_label="icm-demo")


def by_order(*deals: Deal) -> Any:
    """A deal history queried as MT5 does: `history_deals_get(ticket=X)` returns the deals of order X."""
    return lambda order: tuple(d for d in deals if d.order == order)


FILL = Deal(DEAL, ORDER, ORDER)                         # deal 111 filled order 222, opening position 222


def _intent(cid: str = "icm-demo-x") -> OrderIntent:
    return OrderIntent(client_order_id=cid, symbol="XAUUSD", side=1, lots=0.1, sl=2390.0, tp=2430.0, magic=1001)


@pytest.mark.parametrize(("lookup", "why"), [
    (by_order(FILL), "the deal names its position"),
    (lambda t: None, "history_deals_get failed: the order ticket"),
    (lambda t: (), "deal not synced yet: the order ticket"),
])
def test_a_filled_order_reports_the_position_ticket_not_the_deal_ticket(monkeypatch, lookup, why):
    res = _adapter(monkeypatch, _fake(lookup)).place_order(_intent())
    assert res.ok and res.order_id == ORDER and res.position_id == ORDER, why


def test_the_deal_lookup_wins_when_it_names_another_position(monkeypatch):
    # a fill that netted into an existing position (netting accounts): the deal's position_id is the truth
    res = _adapter(monkeypatch, _fake(by_order(Deal(DEAL, ORDER, 999)))).place_order(_intent())
    assert res.position_id == 999


def test_a_past_order_whose_ticket_equals_the_fill_deal_is_never_taken(monkeypatch):
    # review MEDIUM-1: order and deal tickets share a number space; an older ORDER numbered 111 (the new fill's deal
    # ticket) has its own deal and position. Asking `ticket=deal` would return that unrelated position (777)
    past = Deal(555, DEAL, 777)
    assert _adapter(monkeypatch, _fake(by_order(past, FILL))).place_order(_intent()).position_id == ORDER
    # and with the new fill not synced yet, the order ticket, never the past position
    assert _adapter(monkeypatch, _fake(by_order(past))).place_order(_intent()).position_id == ORDER


def test_only_the_deal_with_the_fills_ticket_counts(monkeypatch):
    # an order with several deals (a partial fill then the rest): the deal whose ticket is res.deal names the position
    other = Deal(333, ORDER, 888)
    assert _adapter(monkeypatch, _fake(by_order(other, FILL))).place_order(_intent()).position_id == ORDER


def test_a_raising_deal_lookup_falls_back_to_the_order_ticket(monkeypatch):
    def boom(t: int) -> Any:
        raise RuntimeError("terminal busy")

    assert _adapter(monkeypatch, _fake(boom)).place_order(_intent()).position_id == ORDER


def test_the_engine_keys_the_trade_by_the_position_and_reconcile_keeps_it(monkeypatch, tmp_path):
    fake = _fake(by_order(FILL))
    broker = _adapter(monkeypatch, fake)
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", mode="demo", state_dir=str(tmp_path),
                              owner_user_id=111, magic_base=1000), broker, [TrendSpecialist()], {})
    agent = TrendSpecialist()
    prop = Proposal(proposal_id="icm-demo-1-x", account_id="icm-demo", agent_id=agent.agent_id, side=1, lots=0.1,
                    entry=2400.2, stop=2390.0, target=2430.0, p=0.6, ev_r=0.1, spread_points=20.0, top_features=[])
    eng._execute(prop, agent, 0.1, 2390.0, 2430.0, requested=2400.2, atr_usd=10.0)
    assert set(eng.open) == {ORDER}
    eng._manage_open(pd.DataFrame())                     # sweep + reconcile against the terminal's positions
    assert set(eng.open) == {ORDER}
    actions = {d.get("action") for d in eng.decisions}
    assert not actions & {"adopt_orphan", "close_unconfirmed"}, actions
