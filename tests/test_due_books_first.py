"""Live demo issue 2026-11-03 (icm-probe, 20 books): the S0 random-bracket control (1m) fires on every book at the
same minute close, the 5 s pass budget covers 4-11 books, and the later books' signals pass their 10 s staleness
before their pass runs (`exploration_stale`, ~80 an hour). A multi-book pass (`books.account_pass`) now runs the books
with an exploration decision due (`exploration.due`) right after gold, then the rest in their fair rotation; the
rotation's next book in turn still runs every pass, so no book starves."""
from __future__ import annotations

from collections import deque
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd

from goldbot.engine import Engine, EngineConfig, books, exploration
from goldbot.engine.books import BookSpec
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker

NOW = pd.Timestamp("2025-03-05 10:00:03", tz="UTC")      # 1.5 s grace past the 10:00 close: that minute is complete
SPECS = tuple(BookSpec(key=f"SYM{k:02d}", symbol=f"SYM{k:02d}", store_symbol=f"SYM{k:02d}", magic_block=k, digits=2,
                       point=0.01, contract_size=100.0) for k in range(1, 7))


class MultiPaper(PaperBroker):
    def on_tick(self, tick: Tick) -> None:
        return None


def _engine(tmp_path: Path) -> Engine:
    eng = Engine(EngineConfig(account_id="icm-probe", broker_name="icm", mode="demo", magic_base=260500,
                              state_dir=str(tmp_path), instruments=SPECS), MultiPaper(equity=10_000), [], {})
    eng.clock = lambda: NOW
    return eng


def _ex(tf: str = "1m", last_minute: pd.Timestamp | None = None, queued: int = 0) -> Any:
    """An Exploration's fields `due` reads: its arms' timeframes, the last minute decided and the signals queued."""
    return SimpleNamespace(arms={"S0": SimpleNamespace(tf=tf)}, last_minute=last_minute, queue=deque(range(queued)))


def _arm(eng: Engine, symbol: str, ex: Any) -> None:
    eng.books[symbol].__dict__["exploration"] = ex


def _slow(monkeypatch: Any, seen: list[str], step_s: float = 1.0) -> Any:
    """A book pass that takes `step_s` on the pass deadline's clock (books.time.monotonic, faked: no sleeping)."""
    clock = [0.0]
    monkeypatch.setattr(books, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def one(e: Engine, q: Any, s: str) -> bool:
        seen.append(s)
        clock[0] += step_s
        return True
    return one


def test_a_minute_complete_and_not_yet_decided_by_a_1m_arm_is_due():
    assert exploration.due(_ex(last_minute=NOW.floor("min") - pd.Timedelta(minutes=1)), NOW)
    assert exploration.due(_ex(last_minute=None), NOW)


def test_a_minute_already_decided_or_still_inside_its_grace_is_not_due():
    assert not exploration.due(_ex(last_minute=NOW.floor("min")), NOW)
    assert not exploration.due(_ex(last_minute=NOW.floor("min") - pd.Timedelta(minutes=1)),
                               NOW.floor("min") + pd.Timedelta(seconds=1))


def test_a_queued_signal_is_due_whatever_the_minute():
    assert exploration.due(_ex(last_minute=NOW.floor("min"), queued=1), NOW)


def test_no_exploration_a_15m_arm_or_a_5m_arm_off_its_close_is_never_due_on_the_minute():
    assert not exploration.due(None, NOW)
    assert not exploration.due(_ex(tf="15m"), NOW)
    assert not exploration.due(_ex(tf="5m"), NOW + pd.Timedelta(minutes=1))
    assert exploration.due(_ex(tf="5m"), NOW)


def test_books_with_an_exploration_decision_due_run_right_after_gold(tmp_path):
    eng = _engine(tmp_path)
    _arm(eng, "SYM04", _ex())
    _arm(eng, "SYM06", _ex(last_minute=NOW.floor("min"), queued=1))
    _arm(eng, "SYM02", _ex(last_minute=NOW.floor("min")))            # decided: not due
    seen: list[str] = []

    def one(e: Engine, q: Any, s: str) -> bool:
        seen.append(s)
        return True
    books.account_pass(eng, eng.broker, one)
    assert seen == ["XAUUSD", "SYM04", "SYM06", "SYM01", "SYM02", "SYM03", "SYM05"]


def test_a_spent_budget_runs_the_due_books_first_then_the_next_book_in_turn_and_the_rest_go_first_next_pass(
        tmp_path, monkeypatch):
    """Due books first, then the rotation's next book in turn (it always runs, so the rest never starve behind due
    books); the books left go first next pass, in rotation order."""
    eng = _engine(tmp_path)
    for s in ("SYM03", "SYM05"):
        _arm(eng, s, _ex())
    seen: list[str] = []
    slow = _slow(monkeypatch, seen)
    books.account_pass(eng, eng.broker, slow, budget_s=2.5)
    assert seen == ["XAUUSD", "SYM03", "SYM05", "SYM01"]
    for s in ("SYM03", "SYM05"):
        _arm(eng, s, _ex(last_minute=NOW.floor("min")))              # decided on that pass
    seen.clear()
    books.account_pass(eng, eng.broker, slow, budget_s=2.5)
    assert seen == ["XAUUSD", "SYM02", "SYM03"]


def test_no_book_starves_when_every_pass_has_due_books_that_spend_the_budget(tmp_path, monkeypatch):
    eng = _engine(tmp_path)
    for s in ("SYM01", "SYM02"):
        _arm(eng, s, _ex(queued=1))                                     # due on every pass
    seen: list[str] = []
    slow = _slow(monkeypatch, seen)
    for _ in range(len(SPECS)):
        books.account_pass(eng, eng.broker, slow, budget_s=1.5)
    assert set(seen) == set(eng.books)


def test_a_due_check_that_raises_never_stops_any_books_exits(tmp_path, monkeypatch):
    """Trading-safety L1: `due` is an ordering hint; if it raises, every book's exits still run and the pass goes on."""
    eng = _engine(tmp_path)
    _arm(eng, "SYM03", _ex())

    def boom(ex: Any, now: pd.Timestamp) -> bool:
        raise ValueError("malformed exploration")
    monkeypatch.setattr(exploration, "due", boom)
    exits: list[str] = []

    def exits_first(e: Engine, q: Any, s: str) -> bool:
        exits.append(s)
        return True
    monkeypatch.setattr(books, "_exits_first", exits_first)
    seen: list[str] = []

    def one(e: Engine, q: Any, s: str) -> bool:
        seen.append(s)
        return True
    books.account_pass(eng, eng.broker, one)
    assert sorted(exits) == sorted(["XAUUSD", *(s.symbol for s in SPECS)])
    assert seen[0] == "XAUUSD" and len(seen) == 1 + len(SPECS)
