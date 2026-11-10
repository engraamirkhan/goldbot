"""The trade ledger's per-pass cost (CPU attribution 2026-11-10, round 3: pnl_basis rebuilt it every 60 s at 1.2 s a
pass). Two rules, each with the ledger bit-identical to an uncached build:

* the instruments registry is resolved once per build, not once per `instrument_key` call (14,841 stat() calls of
  config/instruments.yaml a pass), so every row of one build reads the same registry version;
* aux.db's exploration rows are selected again only when aux.db changed: its change marker is the (inode, mtime_ns,
  size) of aux.db and of aux.db-wal, taken before and after the read (a commit appends WAL frames or, at a
  checkpoint, writes the main file; either moves the marker). A marker whose files were written within
  `AUX_RACY_S` of the read is never trusted (a coarse file clock could hide a second commit in the same tick)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.api import instruments_view
from goldbot.api import trades_ledger as tl
from goldbot.engine import exploration
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


def _settle(state: Path) -> None:
    """Age aux.db's files past the racy window, as between two health passes (60 s apart)."""
    old = (NOW - pd.Timedelta(minutes=5)).timestamp()
    for f in state.glob("aux.db*"):
        os.utime(f, (old, old))


def _state(tmp_path: Path) -> Path:
    _engine(tmp_path, SCALP)
    for i in range(4):
        _arm(tmp_path, f"x-{i}", "breakout_5m_a", symbol="XAUUSD+" if i % 2 else "XAUUSD")
    _arm(tmp_path, "x-open", "breakout_5m_a", exit_after=None)
    _settle(tmp_path)
    return tmp_path


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
        tmp_path: Path, selects: list[int]) -> None:
    state, cat = _state(tmp_path), _catalog(tmp_path)
    first = tl.build_ledger(state, NOW, cat=cat, read_only=True)
    for _ in range(3):
        assert tl.build_ledger(state, NOW, cat=cat, read_only=True).rows == first.rows
    assert len(selects) == 1
    assert _uncached(state, cat).rows == first.rows


def test_a_trade_written_after_a_build_is_in_the_next_build(tmp_path: Path, selects: list[int]) -> None:
    state, cat = _state(tmp_path), _catalog(tmp_path)
    before = tl.build_ledger(state, NOW, cat=cat, read_only=True)
    _arm(state, "x-new", "breakout_5m_a")
    _settle(state)                                    # even with the clock of the files put back: inode/size move
    after = tl.build_ledger(state, NOW, cat=cat, read_only=True)
    new = {r.trade_id for r in after.rows} - {r.trade_id for r in before.rows}
    assert len(new) == 1 and new.pop().endswith(":x-new")
    assert len(selects) == 2
    assert after.rows == _uncached(state, cat).rows


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
