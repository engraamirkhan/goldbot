"""Brain 2026-11-12 (lane E): "pass budget 5.0 s spent after N of N books (0 due first); the rest go first next pass"
logged every minute per account although every book ran: only the 15m+ work was carried (`_slow_parts`). The
warning is for books left out of a pass, once per run of such passes, with one summary line when the run ends; the
metric (`_passes_deferred`), the carry and the zones' `deferred` flag stay as they were."""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from goldbot.engine import Engine, books
from tests.test_pass_timing_stale_order import _multi


def _run(eng: Engine, monkeypatch: pytest.MonkeyPatch, plan: list[tuple[bool, int]]) -> list[bool]:
    """One account pass per (books left out?, 15m+ parts carried) step; the `deferred` flag each pass handed on."""
    clock = [0.0]
    monkeypatch.setattr(books, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    seen: list[bool] = []
    monkeypatch.setattr(books, "_launch_zones", lambda e, deferred, a, n: seen.append(deferred))
    for left, carried in plan:
        monkeypatch.setattr(books, "_slow_parts", lambda *a, c=carried: c)

        def one(e: Engine, q: Any, s: str, slow: bool = left) -> bool:
            clock[0] += 10.0 if slow else 0.0
            return True
        books.account_pass(eng, eng.broker, one, budget_s=5.0)
    return seen


def _lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if "pass budget" in r.getMessage()]


def test_carried_15m_work_with_every_book_run_logs_no_budget_warning(tmp_path, monkeypatch, caplog):
    eng = _multi(tmp_path)
    with caplog.at_level(logging.DEBUG, logger="goldbot.engine"):
        seen = _run(eng, monkeypatch, [(False, 9), (False, 0), (False, 4), (False, 0)])
    assert not [r for r in caplog.records if r.levelno >= logging.INFO and "pass budget" in r.getMessage()]
    assert seen == [True, False, True, False]                          # the zones still see a carried pass
    assert eng.__dict__.get("_passes_deferred", 0) == 0


def test_a_run_of_passes_with_books_left_warns_once_and_summarises_when_it_ends(tmp_path, monkeypatch, caplog):
    eng = _multi(tmp_path)
    with caplog.at_level(logging.INFO, logger="goldbot.engine"):
        seen = _run(eng, monkeypatch, [(True, 3), (False, 2), (True, 0), (False, 0), (False, 0)])
    lines = _lines(caplog)
    assert len(lines) == 2, lines
    assert "spent after" in lines[0] and "15m+ work carried for 3 books" in lines[0]
    assert "pass budget run ended after 3 passes" in lines[1], lines[1]
    assert "2 with books left" in lines[1] and "most 15m+ parts carried 3" in lines[1], lines[1]
    assert seen == [True, True, True, False, False]
    assert eng.__dict__["_passes_deferred"] == 2


def test_a_second_run_warns_again(tmp_path, monkeypatch, caplog):
    eng = _multi(tmp_path)
    with caplog.at_level(logging.INFO, logger="goldbot.engine"):
        _run(eng, monkeypatch, [(True, 0), (False, 0), (True, 0), (False, 0)])
    assert [m.split(":")[1].strip()[:11] for m in _lines(caplog)] == ["pass budget"] * 4
