"""state/instruments.db (multi-instrument plan, lane L3): "costs/swap/lot terms per (account, symbol) in
state/instruments.db (SQLite WAL)"; a corrupt db refuses that instrument's entries while its exits still close, and gold
never reads it."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

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
    row = db.load("icm-probe", "XAGUSD")
    assert row is not None and row.swap_long_per_lot is None
    assert db.load("icm-probe", "XAUUSD") is None


def test_a_terms_row_a_newer_release_wrote_still_reads():
    """ADR 0010: a row with a field this release does not know is read tolerantly (the field dropped)."""
    from goldbot.base import load_record
    from goldbot.engine.instruments_db import InstrumentTerms
    row = terms_from_info("icm-probe", "XAGUSD", "XAGUSD", PaperBroker().symbol_info("XAGUSD"), T0)
    got = load_record(InstrumentTerms, {**row.model_dump(mode="json"), "future_field": 1}, source="instruments.db")
    assert got == row


def test_swap_in_the_profit_currency_is_converted_for_index_cfds():
    """Incident 2026-11-02: ICM's DE40, UK100, JP225, USTEC (mode 3, margin currency) and US500 (mode 2, base currency)
    stored NULL swap and refused every entry. Where the mode's currency is the profit currency the amount already is
    profit currency per lot; where it is the base currency of a pair quoted in the profit currency it is x price."""
    base = PaperBroker().symbol_info("DE40").model_copy(update={"swap_long": -12.5, "swap_short": 3.0,
                                                                "currency_profit": "EUR"})
    m3 = base.model_copy(update={"swap_mode": 3, "currency_margin": "EUR", "currency_base": "EUR"})
    assert swap_per_lot(m3)[:2] == (-12.5, 3.0)
    m2 = base.model_copy(update={"swap_mode": 2, "currency_margin": "USD", "currency_base": "EUR"})
    assert swap_per_lot(m2)[:2] == (-12.5, 3.0)
    # a currency the profit currency is not, and no price to convert: unknown, never 0
    assert swap_per_lot(m3.model_copy(update={"currency_margin": "USD"}))[:2] == (None, None)
    # an older bridge (no base or margin currency reported): unknown, as before
    assert swap_per_lot(base.model_copy(update={"swap_mode": 3}))[:2] == (None, None)
    fx = base.model_copy(update={"swap_mode": 2, "currency_base": "EUR", "currency_profit": "USD", "swap_long": -2.0})
    assert swap_per_lot(fx, price=1.1, group="fx")[0] == pytest.approx(-2.2)
    assert swap_per_lot(fx, group="fx")[:2] == (None, None)


def test_the_base_currency_times_price_rule_is_for_fx_and_metal_pairs_only():
    """Trading-safety review of #319, F5: "x price" turns base-currency units into profit currency only for a pair
    quoted as base/profit (FX, metals). For an index or a commodity the price is points of the index, not a rate, so
    the swap stays unknown (entries refused) instead of a wrong cost."""
    fx = PaperBroker().symbol_info("DE40").model_copy(update={
        "swap_mode": 2, "swap_long": -2.0, "swap_short": 0.5, "currency_base": "EUR", "currency_profit": "USD"})
    assert swap_per_lot(fx, price=1.1, group="fx")[0] == pytest.approx(-2.2)
    assert swap_per_lot(fx, price=30.0, group="metals")[0] == pytest.approx(-60.0)
    for group in (None, "equity", "energy", "rates", "ags", "gold"):
        assert swap_per_lot(fx, price=1.1, group=group)[:2] == (None, None), group
    calc_fx = fx.model_copy(update={"trade_calc_mode": 0}) if "trade_calc_mode" in type(fx).model_fields else None
    if calc_fx is not None:                               # MT5 SYMBOL_CALC_MODE_FOREX, when the record carries it
        assert swap_per_lot(calc_fx, price=1.1)[0] == pytest.approx(-2.2)
    row = terms_from_info("icm-probe", "EURUSD", "EURUSD", fx, T0, price=1.1, group="fx")
    assert row.swap_long_per_lot == pytest.approx(-2.2)


def _interest(mode: int, long_pct: float = -20.0, short_pct: float = -10.0) -> SymbolInfo:
    return PaperBroker().symbol_info("BTCUSD").model_copy(update={
        "swap_mode": mode, "swap_long": long_pct, "swap_short": short_pct, "contract_size": 1.0, "point": 0.01,
        "currency_profit": "USD", "currency_base": "BTC", "swap_rollover3days": 5})


def test_an_interest_swap_is_the_annual_percent_of_the_price_over_a_360_day_year():
    """Vantage refusals 2026-11-10: BTCUSD reports swap mode 5 (SYMBOL_SWAP_MODE_INTEREST_CURRENT, annual % of the
    current price, 360-day bank year), which stored NULL and refused every BTC entry. Per lot per night it is
    rate / 100 / 360 x price x contract size, the broker's sign kept; mode 6 (INTEREST_OPEN) for an entry is the same,
    its open price being the current price."""
    for mode in (5, 6):
        lng, sht, note = swap_per_lot(_interest(mode), price=100_000.0)
        assert lng == pytest.approx(-55.5556, abs=1e-3) and sht == pytest.approx(-27.7778, abs=1e-3)
        assert f"swap mode {mode}" in note
    lng, sht, _ = swap_per_lot(_interest(5, 3.0, -7.2).model_copy(update={"contract_size": 10.0}), price=50.0)
    assert lng == pytest.approx(3.0 / 100 / 360 * 50.0 * 10.0) and sht == pytest.approx(-0.1)


@pytest.mark.parametrize("price", [None, float("nan"), float("inf"), 0.0, -1.0])
def test_an_interest_swap_without_a_usable_price_is_unknown_never_zero(price):
    assert swap_per_lot(_interest(5), price=price)[:2] == (None, None)
    assert swap_per_lot(_interest(6), price=price)[:2] == (None, None)


@pytest.mark.parametrize("mode", [7, 8])
def test_the_reopen_swap_modes_stay_unknown(mode):
    assert swap_per_lot(_interest(mode), price=100_000.0)[:2] == (None, None)


BTC = BookSpec(key="BTCUSD", symbol="BTCUSD", store_symbol="BTCUSD", magic_block=1, digits=2, point=0.01,
               contract_size=1.0)


class InterestPaper(PaperBroker):
    """A Vantage-like terminal: BTCUSD with swap mode 5, -20 % / -10 % a year, triple Friday."""

    def on_tick(self, tick: Tick) -> None:
        return None

    def symbol_info(self, symbol: str) -> SymbolInfo:
        return _interest(5).model_copy(update={"name": symbol}) if symbol == "BTCUSD" else super().symbol_info(symbol)


def test_a_mode_5_book_prices_its_swap_and_the_gate_finds_nothing_invalid(tmp_path):
    """The engine end of the same incident: once converted, `swap_unknown` clears, `_swap_usd_per_oz` is finite and
    the RiskGate reports no `invalid:` field (it refused every BTC entry `invalid:swap_usd_per_oz` before)."""
    from goldbot.risk import AccountState, Intent, RiskGate
    pb = InterestPaper(equity=10_000)
    eng = Engine(EngineConfig(account_id="vantage-demo", broker_name="vantage", mode="demo", magic_base=260500,
                              state_dir=str(tmp_path), instruments=(BTC,)), pb, [], {})
    with using(eng, eng.books["BTCUSD"]):
        eng._refresh_account(_tick(pb, "BTCUSD", 100_000.0))
        assert eng.book.terms_problem is None                     # swap_unknown cleared
        assert eng.book.terms is not None and eng.book.terms.swap_long_per_lot == pytest.approx(-55.5556, abs=1e-3)
        swap = eng._swap_usd_per_oz(1, T0)
    assert swap == swap and swap >= 0.0                           # finite, never NaN
    st = AccountState(equity=10_000.0, balance_closed_hwm=10_000.0, day_start_equity=10_000.0,
                      week_start_equity=10_000.0, open_positions=0, margin_used=0.0, last_tick_age_s=1.0,
                      spread_points=25.0)
    intent = Intent(agent_id="x", side=1, p=0.7, target_atr=3.0, stop_atr=1.0, atr_usd=500.0, cost_atr=0.04,
                    multiplier=0.3, price=100_000.0, swap_usd_per_oz=swap)
    assert not any(r.startswith("invalid:") for r in RiskGate().check(intent, st).reasons)
