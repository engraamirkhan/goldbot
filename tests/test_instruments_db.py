"""state/instruments.db (multi-instrument plan, lane L3): "costs/swap/lot terms per (account, symbol) in
state/instruments.db (SQLite WAL)"; a corrupt db refuses that instrument's entries while its exits still close, and gold
never reads it."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

from goldbot.engine import Engine, EngineConfig
from goldbot.engine.books import BookSpec, swap_spec, using
from goldbot.engine.instruments_db import InstrumentsDb, swap_per_lot, terms_from_info
from goldbot.execution.broker import Position, SymbolInfo, Tick
from goldbot.execution.paper import PaperBroker

T0 = pd.Timestamp("2025-03-05 10:00", tz="UTC")
SILVER = BookSpec(key="XAGUSD", symbol="XAGUSD", store_symbol="XAGUSD", magic_block=1, digits=2, point=0.01,
                  contract_size=100.0)


class TermsPaper(PaperBroker):
    """Paper terminal reporting swap in points (MT5 mode 1) and the triple day (Wednesday = 3)."""

    def on_tick(self, tick: Tick) -> None:
        return None

    def symbol_info(self, symbol: str) -> SymbolInfo:
        return super().symbol_info(symbol).model_copy(update={"swap_mode": 1, "swap_long": -5.0, "swap_short": 1.5,
                                                              "swap_rollover3days": 3, "currency_profit": "USD"})


def _engine(tmp_path: Path, pb: PaperBroker) -> Engine:
    return Engine(EngineConfig(account_id="icm-probe", broker_name="icm", mode="demo", magic_base=260500,
                               state_dir=str(tmp_path), instruments=(SILVER,)), pb, [], {})


def _tick(pb: PaperBroker, sym: str, px: float) -> Tick:
    t = Tick(ts_utc=T0, bid=px, ask=px + 0.02)
    pb.set_quote(sym, t)
    return t


def test_terms_are_written_from_the_terminal_and_price_the_swap(tmp_path):
    pb = TermsPaper(equity=10_000)
    eng = _engine(tmp_path, pb)
    with using(eng, eng.books["XAGUSD"]):
        eng._refresh_account(_tick(pb, "XAGUSD", 30.0))
        book = eng.book
        assert book.terms_problem is None and book.terms is not None
        assert book.terms.swap_long_per_lot == -5.0 * 0.01 * 100 and book.terms.contract_size == 100.0
        spec = swap_spec(eng)
        assert spec is not None and spec.triple_weekday == 2 and spec.contract_oz == 100.0
        assert eng._swap_usd_per_oz(1, T0) >= 0.0
    with sqlite3.connect(tmp_path / "instruments.db") as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("SELECT account_id, symbol FROM instrument_terms").fetchall() == [("icm-probe", "XAGUSD")]


def test_instruments_db_corrupt_refuses_entries_and_exits_still_close(tmp_path):
    (tmp_path / "instruments.db").write_bytes(b"this is not a database" * 100)
    pb = TermsPaper(equity=10_000, commission_per_lot_side=0.0, adverse_slip_points=0.0)
    gold = Tick(ts_utc=T0, bid=2000.0, ask=2000.2)
    pb.set_quote("XAUUSD", gold)
    eng = _engine(tmp_path, pb)
    pb._positions[5] = Position(position_id=5, symbol="XAGUSD", side=1, lots=0.1, open_price=30.0, sl=29.0, tp=None,
                                magic=3_050_100 + 95, comment="", open_time_utc=T0, profit=0.0)
    with using(eng, eng.books["XAGUSD"]):
        t = _tick(pb, "XAGUSD", 30.0)
        eng._last_tick = t
        eng._refresh_account(t)
        assert eng.book.terms_problem == "instruments_db_unreadable"
        assert eng.state.dq_error                                   # every entry on silver refused
        assert swap_spec(eng) is None and eng._swap_usd_per_oz(1, T0) != eng._swap_usd_per_oz(1, T0)   # NaN
        eng._reconcile(T0)
        assert 5 in eng.open
        assert eng._try_close(5, "time_exit", T0).ok              # exits never read instruments.db
    assert not pb.positions(symbol="XAGUSD")
    eng._last_tick = gold
    eng._refresh_account(gold)                                     # gold never reads it
    assert not eng.state.dq_error
    eng._write_state()
    st = json.loads((tmp_path / "engine_icm-probe.json").read_text())
    assert st["books"]["XAGUSD"]["terms_problem"] == "instruments_db_unreadable"


def test_a_swap_mode_the_engine_cannot_convert_is_unknown_never_zero():
    info = PaperBroker().symbol_info("X").model_copy(update={"swap_mode": 2, "swap_long": -1.0, "swap_short": -1.0})
    assert swap_per_lot(info)[:2] == (None, None)
    assert swap_per_lot(info.model_copy(update={"swap_mode": 0}))[:2] == (0.0, 0.0)
    assert swap_per_lot(info.model_copy(update={"swap_mode": None}))[:2] == (None, None)


def test_swap_unknown_refuses_that_books_entries(tmp_path):
    db = InstrumentsDb(tmp_path)
    info = PaperBroker().symbol_info("XAGUSD").model_copy(update={"swap_mode": 4, "swap_long": -1.0,
                                                                  "swap_short": -1.0, "swap_rollover3days": 3})
    db.save(terms_from_info("icm-probe", "XAGUSD", "XAGUSD", info, T0))
    assert db.load("icm-probe", "XAGUSD").swap_long_per_lot is None
    assert db.load("icm-probe", "XAUUSD") is None
