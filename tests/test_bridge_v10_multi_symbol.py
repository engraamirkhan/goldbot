"""Bridge protocol v10, the multi-instrument demo (lane L4): one bridge serves the account's symbol list.

Design rules (multi-instrument plan, adopted 2026-11-02): "bridge selects the account's symbol list,
positions(symbol=None), batched ticks_many(symbols, since) every 500 ms"; "bridge notional cap account-wide"
(execution/bridge.py); "bridge MAX_QUEUED=64 can be exceeded by ~7 clients x N engines: size it for N symbols in one
engine". v10 stays backward-compatible with v9: MIN_COMPATIBLE 5, ticks_many optional with a fallback on the brain.
"""
from __future__ import annotations

import json
import threading
from collections import namedtuple
from http.server import HTTPServer
from types import SimpleNamespace
from typing import Any, Iterator

import pandas as pd
import pytest

from goldbot.execution import bridge as bridge_mod
from goldbot.execution import bridge_queue, mt5_adapter
from goldbot.execution.bridge import (
    API_VERSION,
    METHODS,
    MIN_COMPATIBLE,
    OBSERVE_METHODS,
    OPTIONAL_METHODS,
    GuardedBroker,
    RemoteBroker,
    make_handler,
)
from goldbot.execution.broker import Tick
from goldbot.execution.market_watch import MAX_BRIDGE_SYMBOLS, account_symbols
from goldbot.execution.paper import PaperBroker
from goldbot.instruments import load_instruments
from goldbot.ops import accounts as acc_mod
from goldbot.ops.bridge_compat import REQUIRED_METHODS
from tests.test_bridge import _order, _pos, _refused, _Terminal

TOKEN = "t" * 40
T0 = pd.Timestamp("2025-03-05 10:00", tz="UTC")


# ---------------------------------------------------------------------------------------------- protocol
def test_v10_serves_ticks_many_read_only_and_optional_so_a_v9_bridge_stays_compatible():
    assert API_VERSION >= 10 and MIN_COMPATIBLE == 5            # v11 added SymbolInfo currencies only
    assert METHODS["ticks_many"] == ("dict[str,list[Tick]]", True)
    assert "ticks_many" in OPTIONAL_METHODS and "ticks_many" not in REQUIRED_METHODS
    assert "ticks_many" not in OBSERVE_METHODS            # an observed (live, XAUUSD-only) account never batches


@pytest.fixture
def bridge() -> Iterator[tuple[str, PaperBroker]]:
    pb = PaperBroker(equity=10_000)
    pb.on_tick(Tick(ts_utc=T0, bid=2400.0, ask=2400.2))
    pb.set_quote("XAGUSD", Tick(ts_utc=T0, bid=30.1, ask=30.12))
    httpd = HTTPServer(("127.0.0.1", 0), make_handler(GuardedBroker(pb, magic_base=260100), TOKEN))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", pb
    httpd.shutdown()
    httpd.server_close()


def test_ticks_many_crosses_the_wire_as_a_dict_of_tick_lists_one_call_for_every_symbol(bridge):
    url, _ = bridge
    got = RemoteBroker(url, TOKEN).ticks_many(["XAUUSD", "XAGUSD", "EURUSD"], T0 - pd.Timedelta(seconds=1))
    assert set(got) == {"XAUUSD", "XAGUSD", "EURUSD"}
    assert [t.bid for t in got["XAUUSD"]] == [2400.0] and [t.ask for t in got["XAGUSD"]] == [30.12]
    assert got["EURUSD"] == []                            # no quote: an empty list, never a missing key
    assert all(isinstance(t, Tick) for ticks in got.values() for t in ticks)


def test_ticks_many_returns_only_ticks_after_since(bridge):
    url, _ = bridge
    assert RemoteBroker(url, TOKEN).ticks_many(["XAUUSD"], T0) == {"XAUUSD": []}


def test_the_client_refuses_more_symbols_than_one_bridge_serves_before_sending():
    sent: list[Any] = []
    rb = RemoteBroker("http://b", TOKEN, post=lambda *a, **k: sent.append(a))
    with pytest.raises(ValueError, match="at most"):
        rb.ticks_many([f"S{i}" for i in range(MAX_BRIDGE_SYMBOLS + 1)], T0)
    assert sent == []


def test_ticks_many_on_a_v9_bridge_falls_back_to_one_last_tick_per_symbol_and_remembers():
    calls: list[str] = []

    def post(url: str, data: str, **kw: Any) -> Any:
        req = json.loads(data)
        calls.append(req["method"])
        if req["method"] == "ticks_many":
            return SimpleNamespace(status_code=403, json=lambda: {"error": "method 'ticks_many' not allowed"})
        sym = req["args"][0]
        tick = Tick(ts_utc=T0, bid=1.0 if sym == "EURUSD" else 2400.0, ask=1.0001 if sym == "EURUSD" else 2400.2)
        return SimpleNamespace(status_code=200, json=lambda: {"result": bridge_mod._encode(tick)})

    rb = RemoteBroker("http://b", TOKEN, post=post)
    got = rb.ticks_many(["XAUUSD", "EURUSD"], T0 - pd.Timedelta(seconds=1))
    assert [t.bid for t in got["XAUUSD"]] == [2400.0] and [t.bid for t in got["EURUSD"]] == [1.0]
    assert rb.ticks_many(["XAUUSD"], T0) == {"XAUUSD": []}          # not newer than since
    assert calls == ["ticks_many", "last_tick", "last_tick", "last_tick"]   # asked once, then per symbol


def test_an_observe_only_client_refuses_ticks_many_before_the_network():
    rb = RemoteBroker("http://b", TOKEN, observe_only=True, post=lambda *a, **k: pytest.fail("sent"))
    with pytest.raises(bridge_mod.ObserveOnlyRefused):
        rb.ticks_many(["XAUUSD"], T0)


def test_positions_and_broker_terms_send_the_symbol_only_when_asked_so_the_gold_call_is_byte_identical():
    sent: list[dict[str, Any]] = []

    def post(url: str, data: str, **kw: Any) -> Any:
        sent.append(json.loads(data))
        return SimpleNamespace(status_code=200, json=lambda: {"result": []})
    rb = RemoteBroker("http://b", TOKEN, post=post)
    rb.positions(260100)
    rb.positions(260100, "XAGUSD")
    assert sent[0]["args"] == [260100] and sent[1]["args"] == [260100, "XAGUSD"]
    sent.clear()
    try:
        rb.broker_terms("icm-probe", T0, T0)
        rb.broker_terms("icm-probe", T0, T0, "XAGUSD")
    except Exception:                                    # noqa: BLE001 - the fake answers [], not a record
        pass
    assert len(sent[0]["args"]) == 3 and sent[-1]["args"][-1] == "XAGUSD"


def test_the_queue_is_sized_for_every_symbol_of_one_engine():
    # plan: "~7 clients x N engines" overflowed 64; Option B is one engine with N symbol books per account
    assert bridge_queue.MAX_QUEUED >= bridge_queue.BASE_CLIENTS + bridge_queue.CLIENTS_PER_SYMBOL * MAX_BRIDGE_SYMBOLS
    assert bridge_mod._BridgeServer.request_queue_size == bridge_queue.MAX_QUEUED
    assert MAX_BRIDGE_SYMBOLS >= 20                       # icm-probe's 19 instruments + its gold


# ---------------------------------------------------------------------------------------------- MT5 adapter
Sym = namedtuple("Sym", "name visible digits point trade_contract_size volume_min volume_step volume_max "
                        "trade_stops_level filling_mode trade_mode swap_mode swap_long swap_short margin_initial "
                        "margin_maintenance currency_profit swap_rollover3days")
TickRow = namedtuple("TickRow", "time_msc bid ask flags")


def _sym(name: str, **kw: Any) -> Sym:
    base: dict[str, Any] = dict(name=name, visible=True, digits=2, point=0.01, trade_contract_size=100.0,
                                volume_min=0.01, volume_step=0.01, volume_max=100.0, trade_stops_level=0,
                                filling_mode=2, trade_mode=4, swap_mode=1, swap_long=-30.0, swap_short=10.0,
                                margin_initial=0.0, margin_maintenance=0.0, currency_profit="USD",
                                swap_rollover3days=3)
    return Sym(**{**base, **kw})


def _fake(selected: list[str], *, refuse: tuple[str, ...] = (), ticks: dict[str, list[TickRow]] | None = None,
          raise_for: tuple[str, ...] = ()) -> SimpleNamespace:
    infos = {"XAUUSD": _sym("XAUUSD"), "USDJPY": _sym("USDJPY", digits=3, point=0.001, trade_contract_size=100_000.0,
                                                      currency_profit="JPY", swap_rollover3days=3),
             "US500": _sym("US500", trade_contract_size=1.0, swap_rollover3days=5)}

    def select(s: str, on: bool) -> bool:
        if s in refuse:
            return False
        selected.append(s)
        return True

    def copy_ticks_from(s: str, since: Any, n: int, flags: int) -> Any:
        if s in raise_for:
            raise RuntimeError("IPC")
        import numpy as np
        rows = (ticks or {}).get(s, [])
        return np.array(rows, dtype=[("time_msc", "i8"), ("bid", "f8"), ("ask", "f8"), ("flags", "i4")])
    return SimpleNamespace(initialize=lambda **k: True, symbol_select=select, symbol_info=lambda s: infos.get(s),
                           last_error=lambda: (1, "err"), shutdown=lambda: None, copy_ticks_from=copy_ticks_from,
                           COPY_TICKS_ALL=-1)


def _adapter(monkeypatch: pytest.MonkeyPatch, fake: SimpleNamespace, symbols: tuple[str, ...] = ()) -> Any:
    monkeypatch.setattr(mt5_adapter, "mt5", fake)
    return mt5_adapter.MT5Broker(terminal_path="", login=None, password=None, server="s", server_tz="UTC",
                                 symbol="XAUUSD", account_label="icm-probe", verify=None, symbols=symbols)


def test_the_adapter_selects_the_accounts_symbol_list_into_market_watch(monkeypatch):
    selected: list[str] = []
    b = _adapter(monkeypatch, _fake(selected), ("XAUUSD", "USDJPY", "US500"))
    assert selected == ["XAUUSD", "USDJPY", "US500"] and b.unselected == ()


def test_a_symbol_the_terminal_lacks_is_logged_and_skipped_and_gold_still_starts(monkeypatch, caplog):
    selected: list[str] = []
    b = _adapter(monkeypatch, _fake(selected, refuse=("US500",)), ("USDJPY", "US500"))
    assert selected == ["XAUUSD", "USDJPY"] and b.unselected == ("US500",)
    assert "US500" in caplog.text


def test_the_adapters_own_symbol_still_refuses_to_start_when_missing(monkeypatch):
    with pytest.raises(RuntimeError, match="Market Watch"):
        _adapter(monkeypatch, _fake([], refuse=("XAUUSD",)), ("USDJPY",))


def test_symbol_info_reports_the_profit_currency_and_the_triple_swap_day(monkeypatch):
    b = _adapter(monkeypatch, _fake([]))
    jpy = b.symbol_info("USDJPY")
    assert (jpy.currency_profit, jpy.swap_rollover3days, jpy.digits) == ("JPY", 3, 3)
    assert b.symbol_info("US500").swap_rollover3days == 5


def test_the_adapters_ticks_many_reads_each_symbol_after_since_and_isolates_a_failing_one(monkeypatch):
    ms = int(T0.value // 1_000_000)
    ticks = {"XAUUSD": [TickRow(ms - 1000, 2399.0, 2399.2, 6), TickRow(ms + 500, 2400.0, 2400.2, 6)],
             "USDJPY": [TickRow(ms + 250, 150.123, 150.131, 6)]}
    b = _adapter(monkeypatch, _fake([], ticks=ticks, raise_for=("US500",)))
    got = b.ticks_many(["XAUUSD", "USDJPY", "US500"], T0)
    assert [t.bid for t in got["XAUUSD"]] == [2400.0] and got["XAUUSD"][0].flags == 6
    assert [t.ask for t in got["USDJPY"]] == [150.131] and got["US500"] == []
    with pytest.raises(ValueError, match="at most"):
        b.ticks_many([f"S{i}" for i in range(MAX_BRIDGE_SYMBOLS + 1)], T0)


# ---------------------------------------------------------------------------------------------- symbol list
def _acc(account_id: str, **kw: Any) -> acc_mod.Account:
    return acc_mod.load_accounts(logins=False)[account_id].model_copy(update=kw)


def _registry(**enabled: bool) -> Any:
    reg = load_instruments()
    return reg.model_copy(update={"instruments": {
        k: v.model_copy(update={"enabled": enabled.get(k, k == "XAUUSD")}) for k, v in reg.instruments.items()}})


def test_account_symbols_is_the_accounts_own_symbol_while_no_instrument_is_listed():
    assert account_symbols(_acc("icm-demo"), _registry()) == ("XAUUSD",)


def test_account_symbols_adds_the_enabled_instruments_the_account_lists_in_its_brokers_names():
    acc = _acc("icm-probe")
    object.__setattr__(acc, "instruments", ("XAGUSD", "USDJPY", "US500"))
    assert account_symbols(acc, _registry(XAGUSD=True, USDJPY=True)) == ("XAUUSD", "XAGUSD", "USDJPY")


def test_a_live_or_observe_only_account_never_selects_a_non_gold_symbol():
    for account_id in ("icm-live", "vantage-live-obs"):
        acc = _acc(account_id)
        object.__setattr__(acc, "instruments", ("XAGUSD",))
        assert account_symbols(acc, _registry(XAGUSD=True)) == (acc.symbol,)


# ---------------------------------------------------------------------------------------------- account-wide ceiling
class _MultiTerminal(_Terminal):
    """_Terminal with figures per symbol: contract, (bid, ask) and the account loss per lot over 1.0 of price."""

    def __init__(self, *, per_symbol: dict[str, tuple[float, float, float, str]], **kw: Any) -> None:
        super().__init__(**kw)
        self.per_symbol = per_symbol            # symbol -> (contract, price, loss per lot over 1.0, profit currency)

    def symbol_info(self, symbol: str) -> Any:
        info = super().symbol_info(symbol)
        if symbol in self.per_symbol:
            contract, _, _, ccy = self.per_symbol[symbol]
            info = info.model_copy(update={"contract_size": self._v(f"contract:{symbol}", contract),
                                           "currency_profit": ccy})
        return info

    def last_tick(self, symbol: str) -> Tick:
        if symbol in self.per_symbol:
            px = self._v(f"price:{symbol}", self.per_symbol[symbol][1])
            return Tick(ts_utc=T0, bid=px, ask=px)
        return super().last_tick(symbol)

    def loss_per_lot(self, symbol: str, side: int, price: float, distance: float) -> float | None:
        if symbol in self.per_symbol:
            return self._v(f"fx:{symbol}", self.per_symbol[symbol][2] * distance)
        return super().loss_per_lot(symbol, side, price, distance)


def test_an_open_position_on_another_symbol_counts_toward_the_account_wide_ceiling():
    # 0.02 XAGUSD x 5000 oz x 30 = 3,000 USD + 0.01 XAUUSD x 100 x 2500 = 2,500: 5.5x of 1,000 passes; 0.03 XAGUSD
    # makes it 7x: refused, and the message names the other symbol
    xag = {"XAGUSD": (5000.0, 30.0, 5000.0, "USD")}
    t = _MultiTerminal(equity=1_000.0, per_symbol=xag, positions=[_pos(0.02, symbol="XAGUSD")])
    assert GuardedBroker(t, magic_base=260100).place_order(_order(0.01)).ok
    t2 = _MultiTerminal(equity=1_000.0, per_symbol=xag, positions=[_pos(0.03, symbol="XAGUSD")])
    res = GuardedBroker(t2, magic_base=260100).place_order(_order(0.01))
    assert _refused(res, t2) and "XAGUSD" in res.message


def test_a_non_usd_quote_converts_with_the_terminals_own_loss_per_lot_not_the_gold_usd_check():
    # USDJPY 0.05 lot on a USD account: 0.05 x 100,000 x 150 JPY x (1/150 USD per JPY) = 5,000 USD = 5x of 1,000:
    # passes; account_fx's "USD figure within 1% of 1" check is a gold (USD profit) rule and must not refuse it
    jpy = {"USDJPY": (100_000.0, 150.0, 100_000.0 / 150.0, "JPY")}
    t = _MultiTerminal(equity=1_000.0, per_symbol=jpy)
    order = _order(0.05).model_copy(update={"symbol": "USDJPY", "sl": 149.0, "tp": 151.0})
    assert GuardedBroker(t, magic_base=260100).place_order(order).ok
    t2 = _MultiTerminal(equity=1_000.0, per_symbol=jpy)
    assert _refused(GuardedBroker(t2, magic_base=260100).place_order(order.model_copy(update={"lots": 0.07})), t2)


def test_a_figure_of_another_open_symbol_that_cannot_be_read_refuses_the_entry():
    xag = {"XAGUSD": (5000.0, 30.0, 5000.0, "USD")}
    for bad in ({"price:XAGUSD": RuntimeError("no tick")}, {"fx:XAGUSD": None}, {"contract:XAGUSD": 0.0}):
        t = _MultiTerminal(equity=1_000_000.0, per_symbol=xag, positions=[_pos(0.01, symbol="XAGUSD")], bad=bad)
        res = GuardedBroker(t, magic_base=260100).place_order(_order(0.01))
        assert _refused(res, t) and "unreadable" in res.message, bad


def test_a_gold_only_book_reads_and_refuses_exactly_as_v9():
    t = _Terminal(equity=1_000.0, positions=[_pos(0.02)])           # 0.02 open + 0.01 = 7.5x, today's message
    res = GuardedBroker(t, magic_base=260100).place_order(_order(0.01))
    assert res.message == ("bridge_notional_ceiling: notional 7500.00 USD > 6 x equity 1000.00 "
                           "(0.02 lots open + 0.01 on XAUUSD)")


def test_closes_are_never_sized_whatever_the_other_symbols_hold():
    t = _MultiTerminal(equity=1.0, per_symbol={"XAGUSD": (5000.0, 30.0, 5000.0, "USD")},
                       positions=[_pos(3.0, symbol="XAGUSD")])
    assert GuardedBroker(t, magic_base=260100).close(7).ok and t.reads == []


# ---------------------------------------------------------------------------------------------- instrument magics
# Incident 2026-11-03: every non-gold entry on icm-probe failed with "magic 3051695 outside 260500..260599": the guard
# knew gold's range only. Its account's instrument blocks (engine/magic.py book_bases: "the set reconciliation, the
# wind-down check and the bridge guard accept") pass; any other magic is still refused before the terminal.
XAG_BASE = 3_051_600


def _xag_order(magic: int) -> Any:
    return _order(0.01).model_copy(update={"symbol": "XAGUSD", "magic": magic})


def test_the_guard_accepts_its_accounts_instrument_ranges_and_refuses_every_other_magic():
    t = _Terminal(contract=5000.0, bid=30.0, ask=30.0)
    g = GuardedBroker(t, magic_base=260500, instrument_bases=(XAG_BASE,))
    assert g.place_order(_xag_order(XAG_BASE + 95)).ok and len(t.sent) == 1
    for magic in (XAG_BASE + 100, XAG_BASE - 1, 3_061_695, 260100):
        with pytest.raises(PermissionError, match=f"magic {magic} outside"):
            g.place_order(_xag_order(magic))
    assert len(t.sent) == 1


def test_without_instrument_bases_the_guard_is_gold_only_as_before():
    with pytest.raises(PermissionError, match="magic 3051695 outside 260500..260599"):
        GuardedBroker(_Terminal(), magic_base=260500).place_order(_xag_order(3_051_695))


class _GoldTerminal(_Terminal):
    """As MT5Broker on the box: positions() reads the terminal's own symbol only; account_positions() every symbol."""

    def positions(self, magic_prefix: int | None = None) -> list[Any]:
        return [p for p in self.open if p.symbol == "XAUUSD"]


def test_stop_changes_on_an_instrument_position_pass_and_a_foreign_one_is_refused():
    t = _GoldTerminal(positions=[_pos(0.01, magic=XAG_BASE + 95, symbol="XAGUSD", pid=11),
                                 _pos(0.01, magic=999, pid=12), _pos(0.01, magic=260595, pid=13)])
    g = GuardedBroker(t, magic_base=260500, instrument_bases=(XAG_BASE,))
    assert g.modify(11, 29.0, 31.0).ok and g.modify(13, 2400.0, 2600.0).ok
    with pytest.raises(PermissionError, match="not one of this account's goldbot positions"):
        g.modify(12, 29.0, 31.0)


def test_a_wind_down_blocks_stops_still_change_but_it_takes_no_new_order():
    xpt = 3_050_200                                   # an instrument the account no longer lists (M3 wind-down)
    t = _GoldTerminal(positions=[_pos(0.01, magic=xpt + 95, symbol="XPTUSD", pid=21)])
    g = GuardedBroker(t, magic_base=260500, instrument_bases=(XAG_BASE,), managed_bases=(XAG_BASE, xpt))
    assert g.modify(21, 1400.0, 1500.0).ok
    with pytest.raises(PermissionError, match="outside"):
        g.place_order(_xag_order(xpt + 95))


def test_the_managed_bases_cover_every_registry_block_on_demo_and_none_on_live():
    from goldbot.ops.services.engine import account_magic_bases, account_managed_bases
    probe = acc_mod.load_accounts()["icm-probe"]
    managed = account_managed_bases(probe)
    assert set(account_magic_bases(probe)[1:]) <= set(managed)
    assert all(3_050_000 <= b < 3_060_000 for b in managed)
    assert account_managed_bases(probe.model_copy(update={"mode": "live"})) == ()


# trading-safety L1 (review of #325): each magic block is one symbol's. The gold base takes only the account's symbol,
# an instrument base only its own broker symbol, so a magic never opens exposure the books do not count on its symbol.
SYMBOLS = {260500: "XAUUSD", XAG_BASE: "XAGUSD"}


def _tied(t: Any) -> GuardedBroker:
    return GuardedBroker(t, magic_base=260500, instrument_bases=(XAG_BASE,), block_symbols=SYMBOLS)


def test_the_guard_refuses_an_instrument_magic_on_gold_and_accepts_it_on_its_own_symbol():
    t = _Terminal(contract=5000.0, bid=30.0, ask=30.0)
    g = _tied(t)
    with pytest.raises(PermissionError, match=f"magic {XAG_BASE + 95} is XAGUSD's block, not XAUUSD"):
        g.place_order(_order(0.01).model_copy(update={"magic": XAG_BASE + 95}))
    assert t.sent == []
    assert g.place_order(_xag_order(XAG_BASE + 95)).ok and len(t.sent) == 1


def test_the_guard_refuses_a_gold_magic_on_an_instrument_symbol_and_accepts_it_on_gold():
    t = _Terminal()
    g = _tied(t)
    with pytest.raises(PermissionError, match="magic 260595 is XAUUSD's block, not XAGUSD"):
        g.place_order(_xag_order(260595))
    assert t.sent == []
    assert g.place_order(_order(0.01).model_copy(update={"magic": 260595})).ok and len(t.sent) == 1


def test_an_instrument_block_with_no_symbol_in_the_map_is_refused():
    t = _Terminal(contract=5000.0, bid=30.0, ask=30.0)
    g = GuardedBroker(t, magic_base=260500, instrument_bases=(XAG_BASE,), block_symbols={260500: "XAUUSD"})
    with pytest.raises(PermissionError, match=f"magic {XAG_BASE + 95}.s block has no symbol on this account"):
        g.place_order(_xag_order(XAG_BASE + 95))
    assert t.sent == []


def test_the_block_symbols_are_the_accounts_symbol_and_each_instruments_broker_symbol():
    from goldbot.instruments import registry
    from goldbot.ops.services.engine import account_block_symbols, account_instruments, account_magic_bases
    probe = acc_mod.load_accounts()["icm-probe"]
    tied = account_block_symbols(probe)
    assert tuple(tied) == account_magic_bases(probe) and tied[probe.magic_base] == probe.symbol
    for key in account_instruments(probe):
        assert tied[acc_mod.instrument_magic_base(probe, key)] == registry().get(key).broker_symbols[probe.broker]
    assert account_block_symbols(probe.model_copy(update={"mode": "live"})) == {probe.magic_base: probe.symbol}


def test_the_bridge_bases_are_the_engines_books_on_demo_and_gold_alone_on_live():
    from goldbot.ops.services.engine import account_magic_bases
    accs = acc_mod.load_accounts()
    probe = accs["icm-probe"]
    bases = account_magic_bases(probe)
    assert bases[0] == probe.magic_base and len(bases) == 1 + len(probe.instruments)
    assert any(b <= 3_051_695 < b + 100 for b in bases[1:])
    live = probe.model_copy(update={"mode": "live"})
    assert account_magic_bases(live) == (probe.magic_base,)


# ---------------------------------------------------------------------------------------------- same-side cap
# Incident 2026-11-03: S0-JP225 was refused "same_side" 23 times overnight: JP225's minimum lot (1.0) is above gold's
# 0.10-lot cap, so no JP225 (or UST10Y/BUND10Y) entry could ever pass. The cap is 10 minimum lots per book.
@pytest.mark.parametrize(("volume_min", "cap"), [(0.01, 0.10), (None, 0.10), (0.1, 1.0), (1.0, 10.0)])
def test_the_same_side_cap_is_ten_minimum_lots_and_gold_keeps_its_tenth_of_a_lot(volume_min, cap):
    from goldbot.engine.exploration import net_lot_cap
    assert net_lot_cap(volume_min) == pytest.approx(cap)
