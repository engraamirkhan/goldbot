"""The trade ledger's per-pass cost (CPU attribution 2026-11-10, round 3: pnl_basis rebuilt it every 60 s at 1.2 s a
pass). Two rules, each with the ledger bit-identical to an uncached build:

* the instruments registry is resolved once per build, not once per `instrument_key` call (14,841 stat() calls of
  config/instruments.yaml a pass), so every row of one build reads the same registry version;
* aux.db's exploration rows are selected again only when aux.db changed: its change marker is the (inode, mtime_ns,
  size) of aux.db and of a non-empty aux.db-wal plus the WAL header, taken before and after the read (a commit
  appends WAL frames, a WAL restart rewrites the header, a checkpoint writes the main file; each moves the marker).
  A marker whose files were written within `AUX_RACY_S` of the read is never trusted (a coarse file clock could hide
  a second commit in the same tick). On the brain the engines hold aux.db open, so the -wal stays; where the last
  writer has closed (CI issue #366, Linux), a read-only open creates an empty -wal, which must not move the marker."""
from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.api import instruments_view
from goldbot.api import trades_ledger as tl
from goldbot.db import Database
from goldbot.db.connection import connect, db_path, write
from goldbot.engine import exploration
from tests import test_trades_ledger as ledger_tests
from tests.test_trades_ledger import NOW, SCALP, _arm, _catalog, _engine


@pytest.fixture(autouse=True)
def _accounts(enabled_accounts: Any) -> None:
    enabled_accounts(SCALP)


@pytest.fixture
def selects(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Every exploration_trades select the ledger makes."""
    calls: list[int] = []
    real = exploration.LEDGER.select

    def counted(*a: Any, **kw: Any) -> Any:
        calls.append(1)
        return real(*a, **kw)
    monkeypatch.setattr(exploration.LEDGER, "select", counted)
    return calls


def _settle(state: Path, minutes: int = 5) -> None:
    """Age aux.db's files past the racy window, as between two health passes (60 s apart); a later write is aged to
    a later stamp (`minutes` fewer), as the real clock would have it."""
    old = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=minutes)).timestamp()
    for f in state.glob("aux.db*"):
        os.utime(f, (old, old))


def _state(tmp_path: Path) -> Path:
    _engine(tmp_path, SCALP)
    for i in range(4):
        _arm(tmp_path, f"x-{i}", "breakout_5m_a", symbol="XAUUSD+" if i % 2 else "XAUUSD")
    _arm(tmp_path, "x-open", "breakout_5m_a", exit_after=None)
    _settle(tmp_path)
    return tmp_path


class _HeldWriter:
    """`Database.write` through one connection kept open, as an engine holding aux.db open writes it."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        with write(self.conn) as c:
            yield c


@pytest.fixture
def engine_writer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[sqlite3.Connection]:
    """Every commit of the test through one writer connection held open across builds: the -wal keeps its frames,
    and no close checkpoints it (the brain whenever an engine holds aux.db open)."""
    Database.open(tmp_path, "aux")                     # the schema, as the engine's first open migrates it
    conn = connect(db_path(tmp_path, "aux"), "aux")
    monkeypatch.setattr(ledger_tests, "_db", lambda state: _HeldWriter(conn))
    yield conn
    conn.close()


@pytest.fixture
def reader_creates_wal(monkeypatch: pytest.MonkeyPatch) -> None:
    """CI issue #366: with no writer open the last writer's close has checkpointed and deleted the -wal on Linux
    (SQLite 3.46), and every read-only open creates a fresh empty one: a new inode stamped now. Modelled on every
    open (macOS's SQLite keeps an empty -wal and refuses a read-only open without one, so it is recreated, not
    left out)."""
    real = tl.labs.aux

    @contextmanager
    def aux(state: Path) -> Iterator[sqlite3.Connection | None]:
        wal = Path(f"{db_path(state, 'aux')}-wal")
        if not wal.exists() or wal.stat().st_size == 0:
            wal.unlink(missing_ok=True)
            wal.touch()
        with real(state) as conn:
            yield conn
    monkeypatch.setattr(tl.labs, "aux", aux)


def _uncached(state: Path, cat: Any) -> tl.Ledger:
    tl._AUX_TRADES.clear()
    return tl.build_ledger(state, NOW, cat=cat, read_only=True)


def test_the_instruments_registry_is_resolved_once_per_ledger_build(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    state, cat = _state(tmp_path), _catalog(tmp_path)
    stats: list[object] = []

    class CountingPath(type(Path())):  # type: ignore[misc]
        """instruments_view's Path: every stat() of config/instruments.yaml is counted."""
        def stat(self, *a: Any, **kw: Any) -> Any:
            stats.append(1)
            return super().stat(*a, **kw)

    tl.build_ledger(state, NOW, cat=cat, read_only=True)          # warm: the yaml parsed once per version
    monkeypatch.setattr(instruments_view, "Path", CountingPath)
    ledger = tl.build_ledger(state, NOW, cat=cat, read_only=True)
    assert len(ledger.rows) == 5
    assert len(stats) == 1


def test_an_unchanged_aux_db_is_selected_once_across_builds_and_the_ledger_is_unchanged(
        tmp_path: Path, selects: list[int], engine_writer: sqlite3.Connection) -> None:
    state, cat = _state(tmp_path), _catalog(tmp_path)
    assert Path(f"{db_path(state, 'aux')}-wal").stat().st_size > 0        # the engine's WAL holds the commits
    first = tl.build_ledger(state, NOW, cat=cat, read_only=True)
    for _ in range(3):
        assert tl.build_ledger(state, NOW, cat=cat, read_only=True).rows == first.rows
    assert len(selects) == 1
    assert _uncached(state, cat).rows == first.rows


@pytest.mark.parametrize("writer", ["engine_writer", "reader_creates_wal"])
def test_a_trade_written_after_a_build_is_in_the_next_build(tmp_path: Path, selects: list[int], writer: str,
                                                            request: pytest.FixtureRequest) -> None:
    request.getfixturevalue(writer)
    state, cat = _state(tmp_path), _catalog(tmp_path)
    before = tl.build_ledger(state, NOW, cat=cat, read_only=True)
    _arm(state, "x-new", "breakout_5m_a")
    _settle(state, minutes=4)                         # written after the last read, then a pass interval goes by
    after = tl.build_ledger(state, NOW, cat=cat, read_only=True)
    new = {r.trade_id for r in after.rows} - {r.trade_id for r in before.rows}
    assert len(new) == 1 and new.pop().endswith(":x-new")
    assert len(selects) == 2
    assert after.rows == _uncached(state, cat).rows


def test_a_readers_own_open_and_close_never_defeat_the_cache(tmp_path: Path, selects: list[int],
                                                             reader_creates_wal: None) -> None:
    """CI issue #366: with no writer open, the read-only open creates an empty -wal (Linux), and the cache missed on
    every build. An empty WAL holds no frame, so it is no change; the ledger is still the uncached one."""
    state, cat = _state(tmp_path), _catalog(tmp_path)
    first = tl.build_ledger(state, NOW, cat=cat, read_only=True)
    for _ in range(3):
        assert tl.build_ledger(state, NOW, cat=cat, read_only=True).rows == first.rows
    assert len(selects) == 1
    assert _uncached(state, cat).rows == first.rows


def test_a_wal_restart_at_the_same_size_and_stamp_still_moves_the_marker(tmp_path: Path) -> None:
    """After a checkpoint a writer restarts the WAL from its start with new salts: same size, and in a coarse clock
    tick the same mtime, but a new header."""
    db, wal = db_path(tmp_path, "aux"), Path(f"{db_path(tmp_path, 'aux')}-wal")
    db.write_bytes(b"\0" * 4096)
    wal.write_bytes(b"\x37\x7f\x06\x82" + b"\1" * 28 + b"\2" * 4096)
    old = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=5)).value
    os.utime(wal, ns=(old, old))
    os.utime(db, ns=(old, old))
    one = tl._aux_marker(db)
    with open(wal, "r+b") as fh:
        fh.seek(16)
        fh.write(b"\x09" * 8)                           # salt-1, salt-2
    os.utime(wal, ns=(old, old))
    two = tl._aux_marker(db)
    assert one is not None and two is not None and one != two


def test_a_close_recorded_on_an_open_trade_is_seen_on_the_next_build(tmp_path: Path) -> None:
    state, cat = _state(tmp_path), _catalog(tmp_path)
    assert {r.status for r in tl.build_ledger(state, NOW, cat=cat, read_only=True).rows} == {"closed", "open"}
    _arm(state, "x-open", "breakout_5m_a")            # the same order, now closed: an upsert of the row
    got = tl.build_ledger(state, NOW, cat=cat, read_only=True)
    assert {r.status for r in got.rows} == {"closed"}
    assert got.rows == _uncached(state, cat).rows


def test_a_freshly_written_aux_db_is_not_cached(tmp_path: Path, selects: list[int]) -> None:
    state, cat = _state(tmp_path), _catalog(tmp_path)
    now = pd.Timestamp.now(tz="UTC").timestamp()
    for f in state.glob("aux.db*"):
        os.utime(f, (now, now))                       # inside the racy window: a same-tick commit could hide
    tl.build_ledger(state, NOW, cat=cat, read_only=True)
    tl.build_ledger(state, NOW, cat=cat, read_only=True)
    assert len(selects) == 2


def test_open_rows_still_follow_the_engines_price_between_builds(tmp_path: Path) -> None:
    """Only aux.db's records are cached, never the rows: an open row's money now follows the engine report."""
    state, cat = _state(tmp_path), _catalog(tmp_path)
    one = next(r for r in tl.build_ledger(state, NOW, cat=cat, read_only=True).rows if r.status == "open")
    _engine(state, SCALP, price=2420.0)
    two = next(r for r in tl.build_ledger(state, NOW, cat=cat, read_only=True).rows if r.status == "open")
    assert one.price_now == 2410.0 and two.price_now == 2420.0
