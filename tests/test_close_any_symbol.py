"""BACKLOG 62, broker layer: a close uses the position's own symbol.

Design (Drawdown kill switch, DESIGN.md:122): "12%: close everything at market immediately". The kill switch now
closes every position on the account, any symbol (ADR 0016 amendment), so each broker must close a position on a
symbol the engine does not trade: with that symbol's own quote and fill mode, and a symbol without a quote or symbol
info is a refused close the engine retries (retcode 10021, never a raise; trading-safety condition C5). The paper
broker keeps one quote per symbol (C4) and, like MT5, lists only its own symbol in `positions()` while
`account_positions()` lists every symbol (C3). The bridge passes closes through unsized (the notional ceiling only
reads orders). Fake tickets only; the MT5 terminal is a fake (verify on the demo terminal)."""
from collections import namedtuple
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from goldbot.execution import mt5_adapter
from goldbot.execution.bridge import GuardedBroker, RemoteBroker
from goldbot.execution.broker import OrderIntent, Position, Tick
from goldbot.execution.paper import PaperBroker
from tests.test_bridge import TOKEN, _Terminal, bridge, guarded  # noqa: F401 - fixtures

T0 = pd.Timestamp("2025-03-05 10:00", tz="UTC")
MS = 1_741_168_800_000
GOLD, EUR = 7001, 7002                                  # fake tickets
MTick = namedtuple("MTick", "time_msc bid ask flags")
Sym = namedtuple("Sym", "name digits point trade_contract_size volume_min volume_step volume_max trade_stops_level "
                        "filling_mode trade_mode visible")
Send = namedtuple("Send", "retcode order deal volume price comment")
Pos = namedtuple("Pos", "ticket symbol type volume price_open sl tp magic comment time_msc profit")

# MT5 SYMBOL_FILLING_* bits: gold allows IOC (2), EURUSD only FOK (1); ORDER_FILLING_FOK=0, IOC=1, RETURN=2
FILL_BITS = {"XAUUSD": 2, "EURUSD": 1}


def _terminal(*, ticks: dict[str, Any] | None = None, infos: dict[str, Any] | None = None,
              fill: dict[str, float] | None = None) -> SimpleNamespace:
    """A terminal holding a gold position (ours) and a EURUSD position (a manual trade). `fill[symbol]` makes the send
    fill only that many lots (MT5 10010, partial) and leaves the rest open."""
    book = [Pos(GOLD, "XAUUSD", 0, 1.0, 2400.0, 2300.0, 2500.0, 260101, "ours", MS, 0.0),
            Pos(EUR, "EURUSD", 1, 0.37, 1.08, 0.0, 0.0, 0, "manual", MS, 0.0)]
    calls: list[tuple[str, str]] = []
    sent: list[dict] = []
    ticks = {"XAUUSD": MTick(MS, 2400.0, 2400.2, 6), "EURUSD": MTick(MS, 1.0800, 1.0801, 6)} if ticks is None else ticks
    infos = {s: Sym(s, 2, 0.01, 100.0, 0.01, 0.01, 50.0, 0, bits, 4, True) for s, bits in FILL_BITS.items()} \
        if infos is None else infos

    def symbol_select(s: str, on: bool) -> bool:
        calls.append(("select", s))
        return s in infos

    def symbol_info_tick(s: str) -> Any:
        calls.append(("tick", s))
        return ticks.get(s)

    def order_send(req: dict) -> Send:
        sent.append(dict(req))
        if "position" not in req:                       # an opening order: not part of these closes
            return Send(10009, 9, 9, req["volume"], req["price"], "done")
        i = next(k for k, p in enumerate(book) if p.ticket == req["position"])
        part = (fill or {}).get(req["symbol"])
        if part is not None and part < book[i].volume:
            book[i] = book[i]._replace(volume=round(book[i].volume - part, 8))
            return Send(10010, 9, 9, part, 1.08, "partial")
        book.pop(i)
        return Send(10009, 9, 9, req["volume"], 1.08, "done")

    return SimpleNamespace(
        initialize=lambda **k: True, symbol_select=symbol_select, last_error=lambda: (1, "err"),
        shutdown=lambda: None, ORDER_TYPE_BUY=0, ORDER_TYPE_SELL=1, POSITION_TYPE_BUY=0, TRADE_ACTION_DEAL=1,
        ORDER_TIME_GTC=0, ORDER_FILLING_FOK=0, ORDER_FILLING_IOC=1, ORDER_FILLING_RETURN=2,
        symbol_info=lambda s: infos.get(s), symbol_info_tick=symbol_info_tick, order_send=order_send,
        order_check=lambda req: SimpleNamespace(retcode=0, comment=""),
        positions_get=lambda **kw: [p for p in book if kw.get("symbol") in (None, p.symbol)],
        history_deals_get=lambda *a, **k: (), book=book, calls=calls, sent=sent)


def _adapter(monkeypatch: pytest.MonkeyPatch, fake: SimpleNamespace) -> mt5_adapter.MT5Broker:
    monkeypatch.setattr(mt5_adapter, "mt5", fake)
    return mt5_adapter.MT5Broker(terminal_path="", login=None, password=None, server="s", server_tz="Europe/Athens",
                                 symbol="XAUUSD", account_label="icm-demo", verify=None)


# ------------------------------------------------------------------ MT5 adapter
def test_close_reads_the_fill_mode_of_the_positions_own_symbol(monkeypatch):
    fake = _terminal()
    res = _adapter(monkeypatch, fake).close(EUR)
    assert res.ok
    [req] = fake.sent
    assert req["symbol"] == "EURUSD" and req["type_filling"] == fake.ORDER_FILLING_FOK   # EURUSD's mode, not gold's IOC
    assert req["price"] == 1.0801                                                        # a sell closes at EURUSD's ask
    assert req["volume"] == 0.37 and req["magic"] == 0                                   # p.volume as is, its own magic


def test_an_order_reads_the_fill_mode_of_its_own_symbol(monkeypatch):
    fake = _terminal()
    intent = OrderIntent(client_order_id="icm-demo-x", symbol="EURUSD", side=1, lots=0.1, sl=1.07, tp=1.09,
                         magic=260101)
    _adapter(monkeypatch, fake).place_order(intent)
    assert fake.sent[0]["type_filling"] == fake.ORDER_FILLING_FOK


def test_the_gold_close_still_uses_gold_s_fill_mode(monkeypatch):
    fake = _terminal()
    assert _adapter(monkeypatch, fake).close(GOLD).ok
    assert fake.sent[0]["type_filling"] == fake.ORDER_FILLING_IOC


def test_close_selects_the_symbol_before_reading_its_tick(monkeypatch):
    fake = _terminal()
    broker = _adapter(monkeypatch, fake)
    fake.calls.clear()
    broker.close(EUR)
    assert fake.calls.index(("select", "EURUSD")) < fake.calls.index(("tick", "EURUSD"))


def test_close_with_no_tick_for_another_symbol_returns_refused_not_raises(monkeypatch):
    fake = _terminal(ticks={"XAUUSD": MTick(MS, 2400.0, 2400.2, 6)})
    res = _adapter(monkeypatch, fake).close(EUR)
    assert not res.ok and res.retcode == 10021 and res.position_id == EUR and res.filled_lots == 0
    assert "EURUSD" in res.message and fake.sent == []


def test_a_close_without_a_quote_is_logged_once_per_streak(monkeypatch, caplog):
    ticks: dict[str, Any] = {"XAUUSD": MTick(MS, 2400.0, 2400.2, 6)}
    fake = _terminal(ticks=ticks)
    broker = _adapter(monkeypatch, fake)
    with caplog.at_level("WARNING", logger="goldbot.mt5"):
        for _ in range(3):
            assert broker.close(EUR).retcode == 10021
        assert sum("EURUSD" in r.getMessage() for r in caplog.records) == 1
        ticks["EURUSD"] = MTick(MS, 1.0800, 1.0801, 6)                  # quoted again: the streak ends
        fake.book.append(fake.book[-1]._replace(ticket=EUR + 1))
        assert broker.close(EUR).ok
        del ticks["EURUSD"]
        assert broker.close(EUR + 1).retcode == 10021
        assert sum("EURUSD" in r.getMessage() for r in caplog.records) == 2


def test_close_with_no_symbol_info_for_another_symbol_returns_refused_not_raises(monkeypatch):
    infos = {"XAUUSD": Sym("XAUUSD", 2, 0.01, 100.0, 0.01, 0.01, 50.0, 0, 2, 4, True)}
    fake = _terminal(infos=infos)
    broker = _adapter(monkeypatch, fake)
    res = broker.close(EUR)
    assert not res.ok and res.retcode == 10021 and fake.sent == []
    assert broker.close(GOLD).ok                                        # one symbol's fault never stops the others


def test_a_partial_close_of_another_symbol_is_never_sent(monkeypatch):
    fake = _terminal()
    res = _adapter(monkeypatch, fake).close(EUR, 0.1)
    assert not res.ok and fake.sent == [] and [p.volume for p in fake.book if p.ticket == EUR] == [0.37]


def test_a_partially_filled_close_is_not_ok_and_the_resend_closes_the_remaining_volume(monkeypatch):
    """MT5 10010 (done partially) is not a confirmed close: the engine keeps it due and the resend reads the position
    again, so it sends what is left, never the original volume."""
    fake = _terminal(fill={"EURUSD": 0.2})
    broker = _adapter(monkeypatch, fake)
    first = broker.close(EUR)
    assert not first.ok and first.retcode == 10010
    fake.sent.clear()
    broker.close(EUR)
    assert fake.sent[0]["volume"] == pytest.approx(0.17)


# ------------------------------------------------------------------ paper broker
def _paper() -> tuple[PaperBroker, int, int]:
    pb = PaperBroker(equity=10_000)
    pb.on_tick(Tick(ts_utc=T0, bid=2400.0, ask=2400.2))
    pb.set_quote("EURUSD", Tick(ts_utc=T0, bid=1.0800, ask=1.0801))
    gold = pb.place_order(OrderIntent(client_order_id="g", symbol="XAUUSD", side=1, lots=0.5, sl=2300.0, tp=2500.0,
                                      magic=260101)).position_id
    eur = pb.place_order(OrderIntent(client_order_id="e", symbol="EURUSD", side=-1, lots=0.3, sl=1.10, tp=1.05,
                                     magic=0)).position_id
    assert gold is not None and eur is not None
    return pb, gold, eur


def test_paper_positions_lists_its_own_symbol_and_account_positions_every_symbol():
    pb, gold, eur = _paper()
    assert [p.position_id for p in pb.positions()] == [gold]
    assert {p.position_id for p in pb.account_positions()} == {gold, eur}


def test_paper_closes_a_non_engine_symbol_at_that_symbol_s_own_quote():
    pb, gold, eur = _paper()
    res = pb.close(eur)
    assert res.ok and res.price == 1.0801                               # a short closes at EURUSD's ask
    assert {p.position_id for p in pb.account_positions()} == {gold}


def test_paper_last_tick_for_an_unseen_symbol_raises():
    pb = PaperBroker()
    pb.on_tick(Tick(ts_utc=T0, bid=2400.0, ask=2400.2))
    with pytest.raises(LookupError, match="EURUSD"):
        pb.last_tick("EURUSD")


def test_paper_close_without_a_quote_raises_before_any_state_change():
    pb, gold, eur = _paper()
    pb.set_quote("EURUSD", None)                                        # the symbol has no live quote now
    balance = pb.account().balance
    with pytest.raises(LookupError):
        pb.close(eur)
    assert {p.position_id for p in pb.account_positions()} == {gold, eur} and pb.account().balance == balance


def test_paper_eurusd_without_a_quote_never_breaks_xauusd_stop_checks():
    pb, gold, eur = _paper()
    pb.set_quote("EURUSD", None)
    pb.on_tick(Tick(ts_utc=T0 + pd.Timedelta(seconds=1), bid=2299.0, ask=2299.2))   # through the gold stop
    assert [p.position_id for p in pb.account_positions()] == [eur]


def test_paper_prices_each_position_with_its_own_symbol_s_quote():
    pb, gold, eur = _paper()
    pb.set_quote("EURUSD", Tick(ts_utc=T0 + pd.Timedelta(seconds=1), bid=1.1001, ask=1.1002))   # through its stop
    assert [p.position_id for p in pb.account_positions()] == [gold]   # the gold stop is not judged on EURUSD prices


# ------------------------------------------------------------------ bridge
def test_paper_and_bridge_close_a_non_engine_symbol(bridge, guarded):  # noqa: F811 - fixtures
    for url, pb in (bridge, guarded):
        pb.set_quote("EURUSD", Tick(ts_utc=T0, bid=1.0800, ask=1.0801))
        eur = pb.place_order(OrderIntent(client_order_id=f"e{url}", symbol="EURUSD", side=1, lots=0.2, sl=1.07,
                                         tp=1.09, magic=0)).position_id
        rb = RemoteBroker(url, TOKEN)
        assert [p.symbol for p in rb.account_positions()] == ["EURUSD"] and rb.positions() == []
        res = rb.close(eur)
        assert res.ok and res.price == 1.0800 and rb.account_positions() == []


def test_the_notional_ceiling_ignores_closes():
    eur = Position(position_id=EUR, symbol="EURUSD", side=1, lots=3.0, open_price=1.08, sl=None, tp=None, magic=0,
                   comment="manual", open_time_utc=T0, profit=0.0)
    t = _Terminal(equity=1.0, positions=[eur])          # a foreign magic on another symbol, far above any ceiling
    res = GuardedBroker(t, magic_base=260100).close(EUR)
    assert res.ok and t.reads == [] and t.sent == []
