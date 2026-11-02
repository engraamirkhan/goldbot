"""The trading-safety review of #310 approved the merge but blocks enabling any non-gold instrument until M1-M3 and
L1, L2, L4 are fixed. M1 (bridge load) is held in tests/test_capacity.py, L4 in tests/test_learning_mi.py; the rest
here, one test (or more) per finding, named for the behaviour it holds."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from goldbot.data.calendar import session_profile
from goldbot.engine import Engine, EngineConfig, books
from goldbot.engine.books import FRESH_SYMBOL_STATE, SYMBOL_STATE, BookSpec, using
from goldbot.execution.broker import Position, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.instruments import Instruments, load_instruments
from goldbot.ops.checks.engine import check_symbols
from goldbot.ops.services import engine as svc
from goldbot.risk.supervisor import Supervisor

REG = load_instruments()
T0 = pd.Timestamp("2025-03-05 10:00", tz="UTC")            # a Wednesday
SILVER_MAGIC = 3_050_100 + 95                              # icm-probe's XAGUSD range (index 5, block 1)
PLATINUM_MAGIC = 3_050_200 + 95                            # its XPTUSD range (block 2)


def _spec(key: str, block: int, **kw: Any) -> BookSpec:
    s = REG.get(key)
    sym = next(iter(s.broker_symbols.values()))
    return BookSpec(key=key, symbol=sym, store_symbol=s.store_symbol, magic_block=block, digits=2, point=0.01,
                    contract_size=100.0, instrument=s, **kw)


SILVER = _spec("XAGUSD", 1)
WHEAT = _spec("WHEAT", 26)


class MultiPaper(PaperBroker):
    def on_tick(self, tick: Tick) -> None:
        return None


def _engine(tmp_path: Path, pb: PaperBroker, specs: tuple[BookSpec, ...] = (SILVER,), **cfg: Any) -> Engine:
    return Engine(EngineConfig(account_id=cfg.pop("account_id", "icm-probe"), broker_name="icm", mode="demo",
                               magic_base=260500, state_dir=str(tmp_path), instruments=specs, **cfg), pb, [], {})


def _quote(pb: PaperBroker, sym: str, px: float, at: pd.Timestamp = T0) -> Tick:
    t = Tick(ts_utc=at, bid=px, ask=px + 0.02)
    pb.set_quote(sym, t)
    return t


def _pos(pid: int, sym: str, magic: int, px: float, sl: float | None) -> Position:
    return Position(position_id=pid, symbol=sym, side=1, lots=0.1, open_price=px, sl=sl, tp=None, magic=magic,
                    comment="", open_time_utc=T0, profit=0.0)


def _unknown_exposure_file(tmp_path: Path, **cfg: Any) -> dict[str, Any]:
    """An engine whose silver position has no quote yet (its exposure cannot be valued), its state file written."""
    pb = MultiPaper(equity=10_000)
    eng = _engine(tmp_path, pb, **cfg)
    gold = _quote(pb, "XAUUSD", 2000.0)
    pb._positions[2] = _pos(2, "XAGUSD", SILVER_MAGIC, 30.0, 29.0)
    eng._last_tick = gold
    eng._refresh_account(gold)
    eng._write_state()
    assert eng.state.exposure_unknown
    return dict(json.loads((tmp_path / f"engine_{eng.cfg.account_id}.json").read_text()))


# ------------------------------------------------------------------------------------------------ M2
def test_m2_a_probe_engine_with_unknown_exposure_publishes_an_empty_map_and_does_not_halt_the_real_accounts(tmp_path):
    st = _unknown_exposure_file(tmp_path, probe_only=True)
    assert st["probe_only"] is True and st["group_notional"] == {} and st["exposure_unknown"] is True
    assert "engine_state_unreadable" not in Supervisor(tmp_path).evaluate()["reasons"]


def test_m2_a_real_accounts_unknown_exposure_is_still_null_and_halts_every_account(tmp_path):
    st = _unknown_exposure_file(tmp_path, account_id="icm-demo")
    assert st["probe_only"] is False and st["group_notional"] is None and st["exposure_unknown"] is True
    assert "engine_state_unreadable" in Supervisor(tmp_path).evaluate()["reasons"]


# ------------------------------------------------------------------------------------------------ M3
def test_m3_a_position_on_a_symbol_no_book_holds_makes_exposure_unknown_and_fails_health(tmp_path):
    """An account position on a symbol none of the engine's books trades counts nowhere: every entry is refused
    (`exposure_unknown`), the file names it (`unbooked_positions`) and health's symbols check FAILs."""
    pb = MultiPaper(equity=10_000)
    eng = _engine(tmp_path, pb)
    gold = _quote(pb, "XAUUSD", 2000.0)
    eng._last_tick = gold
    eng._refresh_account(gold)
    assert not eng.state.exposure_unknown
    pb._positions[7] = _pos(7, "XPTUSD", PLATINUM_MAGIC, 900.0, 890.0)
    eng._refresh_account(gold)
    eng._write_state()
    assert eng.state.exposure_unknown and "exposure_unknown" in eng.gate.account_reasons(eng.state)
    st = json.loads((tmp_path / "engine_icm-probe.json").read_text())
    assert st["unbooked_positions"] == [{"position_id": 7, "symbol": "XPTUSD", "magic": PLATINUM_MAGIC}]
    chk = check_symbols(SimpleNamespace(state_dir=tmp_path), "icm-probe")
    assert chk is not None and chk.status == "fail" and "XPTUSD" in chk.reason


def _acc(**kw: Any) -> Any:
    base = dict(account_id="icm-probe", broker="icm", mode="demo", is_observe_only=False, magic_base=260500,
                instruments=["XAGUSD"], instrument_magic_index=5, enabled=True)
    return SimpleNamespace(**{**base, **kw})


def _confirmed(monkeypatch: pytest.MonkeyPatch, *keys: str) -> None:
    got = {k: (v.model_copy(update={"enabled": True, "verify": False, "digits": 2, "point": 0.01})
               if k in keys else v) for k, v in REG.instruments.items()}
    monkeypatch.setattr("goldbot.instruments.load_instruments", lambda *a, **k: Instruments(instruments=got))


def test_m3_an_instrument_no_longer_listed_keeps_a_wind_down_book_while_the_account_holds_its_magics(monkeypatch):
    """XPTUSD was taken off the account while one of its trades is open: it keeps a book (exits run) that refuses
    every entry; a position on its symbol outside the account's range, or no position, gives no book."""
    _confirmed(monkeypatch, "XAGUSD", "XPTUSD")
    pb = PaperBroker()
    assert [b.key for b in svc.instrument_books(_acc(), pb, None)] == ["XAGUSD"]
    pb._positions[8] = _pos(8, "XPTUSD", 999_999, 900.0, 890.0)             # someone else's magic: no book
    assert [b.key for b in svc.instrument_books(_acc(), pb, None)] == ["XAGUSD"]
    pb._positions[7] = _pos(7, "XPTUSD", PLATINUM_MAGIC, 900.0, None)
    got = svc.instrument_books(_acc(), pb, None)
    assert [(b.key, b.blocked) for b in got] == [("XAGUSD", None), ("XPTUSD", books.WIND_DOWN)]
    wind = got[1]
    assert wind.magic_base == 3_050_200 and wind.exploration_config is None
    gold_only = svc.instrument_books(_acc(instruments=[]), pb, None)          # every instrument taken off
    assert [(b.key, b.blocked) for b in gold_only] == [("XPTUSD", books.WIND_DOWN)]
    assert svc.instrument_books(_acc(instruments=[], mode="live"), pb, None) == ()


def test_m3_the_wind_down_book_refuses_entries_and_gives_the_orphan_a_stop(tmp_path, monkeypatch):
    _confirmed(monkeypatch, "XAGUSD", "XPTUSD")
    pb = MultiPaper(equity=10_000)
    pb._positions[7] = _pos(7, "XPTUSD", PLATINUM_MAGIC, 900.0, None)
    specs = svc.instrument_books(_acc(), pb, None)
    eng = _engine(tmp_path, pb, specs=specs)
    wind = eng.books["XPTUSD"]
    assert books.problem(wind) == books.WIND_DOWN
    with using(eng, wind):
        eng._last_tick = _quote(pb, "XPTUSD", 905.0)
        eng._last_atr = 4.0
        eng._reconcile(T0)
        assert 7 in eng.open and pb._positions[7].sl == pytest.approx(900.0 - 1.5 * 4.0)
    gold = _quote(pb, "XAUUSD", 2000.0)
    eng._last_tick = gold
    eng._refresh_account(gold)
    eng._write_state()
    st = json.loads((tmp_path / "engine_icm-probe.json").read_text())
    assert st["symbols"]["XPTUSD"]["state"] == "disabled" and "winding down" in st["symbols"]["XPTUSD"]["text"]
    assert "unbooked_positions" not in st or st["unbooked_positions"] == []


# ------------------------------------------------------------------------------------------------ L1
def test_l1_the_blackout_kind_is_each_books_own(tmp_path):
    assert "blackout_kind" in SYMBOL_STATE and FRESH_SYMBOL_STATE["blackout_kind"] is None
    eng = _engine(tmp_path, MultiPaper(equity=10_000))
    with using(eng, eng.books["XAGUSD"]):
        eng.state.in_blackout, eng.state.blackout_kind = True, "news_shock"
    assert eng.state.blackout_kind is None
    with using(eng, eng.books["XAGUSD"]):
        assert eng.state.blackout_kind == "news_shock"


# ------------------------------------------------------------------------------------------------ L2
def test_l2_a_books_initial_sessions_go_through_set_sessions(tmp_path):
    """The book's profile before the broker's hours are read narrows its entries only (E3), as a broker table does."""
    eng = _engine(tmp_path, MultiPaper(equity=10_000), specs=(WHEAT,))
    book = eng.books["Wheat-C"]
    assert book.entry_sessions is not None and book.entry_sessions.intersect_broker
    assert book.entry_sessions.friday_cut == session_profile("ags").friday_cut
    with using(eng, book):
        assert not eng.sessions.intersect_broker


def test_l2_a_failed_session_refresh_handing_back_the_current_table_keeps_the_entry_profile(tmp_path):
    """`SessionRefresher.collect` hands back the engine's current table when a read fails; set again, it must not
    drop the profile that narrows the book's entries."""
    eng = _engine(tmp_path, MultiPaper(equity=10_000), specs=(WHEAT,))
    book = eng.books["Wheat-C"]
    profile = book.entry_sessions
    with using(eng, book):
        eng.set_sessions(eng.sessions)
        assert book.entry_sessions is profile and not eng.sessions.intersect_broker
