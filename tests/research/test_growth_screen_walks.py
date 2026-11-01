"""The growth-screen walks (docs/research/candidates/growth/growth_screen.py, prereg-growth-2026-11-01.md) on
hand-built paths: pyramid adds and the open-risk stop, the conservative intrabar order and its optimistic bracket."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PATH = Path(__file__).resolve().parents[2] / "docs" / "research" / "candidates" / "growth" / "growth_screen.py"
spec = importlib.util.spec_from_file_location("growth_screen", PATH)
assert spec and spec.loader
gs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gs)


def arr(*x: float) -> np.ndarray:
    return np.array(x, dtype=float)


def test_pyramid_adds_and_raises_stop_to_one_unit_open_risk() -> None:
    # entry 100, R 1: adds at 101, 102, 103; the walk then falls to the raised common stop
    o, h = arr(100, 100.5, 101.5, 102.5, 103), arr(100.2, 101.1, 102.1, 103.1, 103)
    lo, c = arr(99.8, 100.4, 101.4, 102.4, 101), arr(100, 101, 102, 103, 101)
    gross, units, stopped, j = gs.walk_pyramid(o, h, lo, c, 100.0, 1.0)
    # four units, avg 101.5, stop = avg - 1/4 = 101.25; exit at min(stop, open) = 101.25
    assert (units, stopped, j) == (4, True, 4)
    assert gross == pytest.approx(4 * 101.25 - (100 + 101 + 102 + 103))
    assert gross == pytest.approx(-1.0)          # open risk of one unit


def test_pyramid_same_minute_add_and_stop_is_conservative_unless_optimistic(monkeypatch: pytest.MonkeyPatch) -> None:
    o, h, lo, c = arr(100, 100), arr(100.1, 101.2), arr(99.9, 98.0), arr(100, 98.5)
    gross, units, stopped, _ = gs.walk_pyramid(o, h, lo, c, 100.0, 1.0)
    assert (units, stopped) == (2, True) and gross == pytest.approx(-1.0 + (99.0 - 101.0))
    monkeypatch.setattr(gs, "OPTIMISTIC", True)
    gross, units, _, _ = gs.walk_pyramid(o, h, lo, c, 100.0, 1.0)
    assert units == 1 and gross == pytest.approx(-1.0)


def test_trail_checks_fill_minute_only_when_conservative(monkeypatch: pytest.MonkeyPatch) -> None:
    o, h, lo, c = arr(100, 101), arr(100.5, 103), arr(98.5, 100.8), arr(100, 102.5)
    gross, stopped, j = gs.walk_trail(o, h, lo, c, 0, 100.0, 98.0, 1.0)
    assert stopped and j == 0 and gross == pytest.approx(-1.0)
    monkeypatch.setattr(gs, "OPTIMISTIC", True)
    gross, stopped, _ = gs.walk_trail(o, h, lo, c, 0, 100.0, 98.0, 1.0)
    assert not stopped and gross == pytest.approx(2.5)


def test_target_minute_holding_both_is_a_stop() -> None:
    o, h, lo, c = arr(100, 100), arr(100.5, 105), arr(99.5, 98.5), arr(100, 100)
    gross, stopped, _ = gs.walk_target(o, h, lo, c, 0, 100.0, 99.0, 104.0)
    assert stopped and gross == pytest.approx(-1.0)


def test_rollovers_count_wednesday_triple_and_skip_weekend() -> None:
    t0 = pd.Timestamp("2015-03-02 10:00", tz="UTC").value          # Monday
    t1 = pd.Timestamp("2015-03-09 10:00", tz="UTC").value          # next Monday
    assert gs.rollovers(t0, t1) == 1 + 1 + 3 + 1 + 1               # Mon, Tue, Wed x3, Thu, Fri; none Sat/Sun
