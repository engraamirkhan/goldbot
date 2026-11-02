"""Live demo issue 2026-11-02 (icm-probe, 20 books): the 19 non-gold books were blocked (`instrument_unverified`) and
the bridge served gold only, yet every pass each of them called the broker (ticks, refresh, symbol_info, positions).
The single-worker bridge queued them, gold's own reads timed out and passes took up to 35.8 s. Rule held here: a
blocked book that holds nothing in its magic range makes ZERO broker calls per pass and publishes "disabled" from
config alone; a blocked book that holds positions keeps its exits, on the pass's shared account_positions() read, and
never reads symbol terms."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from goldbot.engine import Engine, EngineConfig, books, magic
from goldbot.engine.books import UNVERIFIED, BookSpec, using
from goldbot.execution.broker import Position, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops.services.engine import engine_pass

N_BLOCKED = 19
COUNTED = frozenset({"account", "account_positions", "positions", "last_tick", "ticks_many", "symbol_info",
                     "broker_terms", "loss_per_lot", "margin_required", "modify", "close", "place_order",
                     "deals_since", "get_bars", "get_bars_range", "copy_ticks", "trading_sessions"})
T_PASS = pd.Timestamp.now("UTC").floor("s")


class CountingBroker(PaperBroker):
    """The paper broker as the bridge sees it: every broker call logged as (name, symbol= or first argument)."""

    def __init__(self) -> None:
        super().__init__(equity=10_000, commission_per_lot_side=0.0, adverse_slip_points=0.0)
        self.calls: list[tuple[str, Any]] = []

    def on_tick(self, tick: Tick) -> None:
        return None

    def __getattribute__(self, name: str) -> Any:
        attr = object.__getattribute__(self, name)
        if name not in COUNTED:
            return attr
        calls = object.__getattribute__(self, "calls")

        def counted(*a: Any, **k: Any) -> Any:
            calls.append((name, k.get("symbol", a[0] if a else None)))
            return attr(*a, **k)
        return counted

    def about(self, symbol: str) -> list[tuple[str, Any]]:
        """Every call naming `symbol` (a ticks_many naming it too)."""
        return [(n, a) for n, a in self.calls if a == symbol or (isinstance(a, (list, tuple)) and symbol in a)]


def _engine(tmp_path: Path, blocked: str | None = UNVERIFIED) -> tuple[Engine, CountingBroker]:
    specs = tuple(BookSpec(key=f"SYM{k:02d}", symbol=f"SYM{k:02d}", store_symbol=f"SYM{k:02d}", magic_block=k,
                           digits=2, point=0.01, contract_size=100.0, blocked=blocked)
                  for k in range(1, N_BLOCKED + 1))
    pb = CountingBroker()
    eng = Engine(EngineConfig(account_id="icm-probe", broker_name="icm", mode="demo", magic_base=260500,
                              state_dir=str(tmp_path / "state"), instruments=specs), pb, [], {})
    for b in eng.books.values():
        pb.set_quote(b.__dict__["cfg"].symbol, Tick(ts_utc=T_PASS, bid=30.0, ask=30.02))
    pb.calls.clear()
    return eng, pb


def _held(pb: CountingBroker, pid: int, symbol: str, magic_no: int, sl: float = 20.0) -> None:
    pb._positions[pid] = Position(position_id=pid, symbol=symbol, side=1, lots=0.1, open_price=31.0, sl=sl, tp=None,
                                  magic=magic_no, comment="", open_time_utc=T_PASS, profit=0.0)


def _others(eng: Engine) -> list[str]:
    return [s for s, b in eng.books.items() if not b.primary]


def _in_range(k: int) -> int:
    return magic.book_base(260500, k) + 95


def test_nineteen_blocked_books_with_no_positions_make_no_broker_call_on_any_pass(tmp_path):
    eng, pb = _engine(tmp_path)
    feed = books.QuoteFeed(pb)
    for _ in range(3):
        books.account_pass(eng, pb, engine_pass, feed)
    for sym in _others(eng):
        assert pb.about(sym) == [], (sym, pb.about(sym))
    assert pb.about("XAUUSD") != []                 # gold's pass ran as before
    assert all(a == ["XAUUSD"] for n, a in pb.calls if n == "ticks_many"), pb.calls


def test_blocked_books_with_no_positions_publish_disabled_from_config_alone(tmp_path):
    eng, pb = _engine(tmp_path)
    books.account_pass(eng, pb, engine_pass, books.QuoteFeed(pb))
    pub = books.published(eng)
    assert {pub["symbols"][s]["state"] for s in _others(eng)} == {"disabled"}
    assert {pub["books"][s]["terms_problem"] for s in _others(eng)} == {UNVERIFIED}


def test_a_blocked_book_holding_a_position_in_its_range_still_runs_its_stop_check_and_close(tmp_path):
    eng, pb = _engine(tmp_path)
    last = list(eng.books.values())[-1]
    _held(pb, 9, last.__dict__["cfg"].symbol, _in_range(N_BLOCKED))
    with using(eng, last):
        eng._reconcile(None)
        eng.open[9].sl = 30.5                       # the engine's stop, above the 30.00 bid: through it
    pb.calls.clear()
    books.account_pass(eng, pb, engine_pass, books.QuoteFeed(pb))
    assert ("close", 9) in pb.calls and 9 not in pb._positions


def test_a_blocked_book_holding_a_position_reads_it_from_the_shared_account_read_and_never_reads_symbol_terms(
        tmp_path):
    eng, pb = _engine(tmp_path)
    last = list(eng.books.values())[-1]
    sym = last.__dict__["cfg"].symbol
    _held(pb, 9, sym, _in_range(N_BLOCKED))
    with using(eng, last):
        eng._reconcile(None)
    assert 9 in last.__dict__["open"]               # adopted by its own book's reconcile
    assert ("account_positions", None) in pb.calls and ("positions", sym) not in pb.calls, pb.calls
    pb.calls.clear()
    feed = books.QuoteFeed(pb)
    for _ in range(3):
        books.account_pass(eng, pb, engine_pass, feed)
    assert ("ticks_many", ["XAUUSD", sym]) in pb.calls   # awake: its quote is read for its exits
    names = [n for n, _ in pb.about(sym)]
    # its contract size (exposure) once; no terms read, no per-book positions call
    assert names.count("symbol_info") <= 1 and "broker_terms" not in names and "positions" not in names, names
    for other in _others(eng)[:-1]:                 # the flat blocked books stay silent meanwhile
        assert pb.about(other) == [], (other, pb.about(other))


def test_a_manual_position_outside_a_blocked_books_magic_range_does_not_wake_it(tmp_path):
    eng, pb = _engine(tmp_path)
    sym = _others(eng)[0]
    _held(pb, 7, sym, 12345)
    books.account_pass(eng, pb, engine_pass, books.QuoteFeed(pb))
    assert pb.about(sym) == []


def test_a_flat_blocked_book_wakes_at_the_next_recheck_once_a_position_appears_in_its_range(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    now = [1000.0]
    monkeypatch.setattr(books.time, "monotonic", lambda: now[0])
    feed = books.QuoteFeed(pb)
    books.account_pass(eng, pb, engine_pass, feed)
    last = list(eng.books.values())[-1]
    sym = last.__dict__["cfg"].symbol
    _held(pb, 9, sym, _in_range(N_BLOCKED))
    now[0] += books.QUIET_RECHECK_S
    books.account_pass(eng, pb, engine_pass, feed)
    assert pb.about(sym) != [] and 9 in last.__dict__["open"]


def test_the_flat_blocked_books_check_reads_no_account_positions_between_rechecks(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    now = [1000.0]
    monkeypatch.setattr(books.time, "monotonic", lambda: now[0])
    feed = books.QuoteFeed(pb)
    books.account_pass(eng, pb, engine_pass, feed)
    pb.calls.clear()
    for _ in range(5):
        now[0] += 0.5
        books.account_pass(eng, pb, engine_pass, feed)
    assert [n for n, _ in pb.calls if n == "account_positions"] == [], pb.calls


def test_an_unblocked_non_gold_book_reads_its_symbol_terms_at_most_once_over_several_passes(tmp_path):
    eng, pb = _engine(tmp_path, blocked=None)
    feed = books.QuoteFeed(pb)
    for _ in range(3):
        books.account_pass(eng, pb, engine_pass, feed)
    for sym in _others(eng):
        names = [n for n, _ in pb.about(sym)]
        assert names.count("symbol_info") <= 1 and "positions" not in names, (sym, names)
