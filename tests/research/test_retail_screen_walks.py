"""The retail-setup screen walks (docs/research/candidates/retail/retail_screen.py,
prereg-2026-11-01-retail-setups.md) on hand-built paths: limit fills, the stop-first intrabar order, gap exits, the
FVG test, the cost gate and the twin geometry."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

PATH = Path(__file__).resolve().parents[2] / "docs" / "research" / "candidates" / "retail" / "retail_screen.py"
spec = importlib.util.spec_from_file_location("retail_screen", PATH)
assert spec and spec.loader
rs = importlib.util.module_from_spec(spec)
sys.modules["retail_screen"] = rs
spec.loader.exec_module(rs)

MIN = 60_000_000_000


def arr(*x: float) -> np.ndarray:
    return np.array(x, dtype=float)


def test_limit_fills_at_first_touch_at_the_limit() -> None:
    assert rs.fill_index(arr(101, 100.5, 99.9, 98), 100.0) == 2
    assert rs.fill_index(arr(101, 100.5), 100.0) is None


def test_fill_minute_counts_only_the_stop() -> None:
    # fill minute reaches both the stop (99) and the target (102): only the stop counts
    o, h, lo, c = arr(100.5, 101), arr(102.5, 101), arr(98.5, 100), arr(100, 100.5)
    gross, how, j = rs.walk_exit(o, h, lo, c, 100.0, 99.0, 102.0)
    assert (how, j) == ("stop", 0) and gross == pytest.approx(-1.0)
    # target in the fill minute is ignored; time exit at the last close
    o, h, lo, c = arr(100.5, 101), arr(102.5, 101), arr(99.5, 100), arr(100, 100.5)
    gross, how, j = rs.walk_exit(o, h, lo, c, 100.0, 99.0, 102.0)
    assert (how, j) == ("time", 1) and gross == pytest.approx(0.5)


def test_same_minute_stop_and_target_is_a_stop_and_gaps_exit_at_the_open() -> None:
    o, h, lo, c = arr(100, 100.5), arr(100.2, 102.5), arr(99.8, 98.9), arr(100, 101)
    gross, how, j = rs.walk_exit(o, h, lo, c, 100.0, 99.0, 102.0)
    assert (how, j) == ("stop", 1) and gross == pytest.approx(-1.0)
    o, h, lo, c = arr(100, 98.0), arr(100.2, 98.2), arr(99.8, 97.5), arr(100, 98)
    gross, how, _ = rs.walk_exit(o, h, lo, c, 100.0, 99.0, 102.0)
    assert how == "stop" and gross == pytest.approx(-2.0)        # gapped open, worse than the stop


def test_target_exit() -> None:
    o, h, lo, c = arr(100, 100.5, 101), arr(100.2, 101, 102.1), arr(99.8, 100.2, 100.9), arr(100, 100.8, 102)
    gross, how, j = rs.walk_exit(o, h, lo, c, 100.0, 99.0, 102.0)
    assert (how, j) == ("target", 2) and gross == pytest.approx(2.0)


def bars(o: list[float], h: list[float], lo: list[float], c: list[float], minutes: int = 15) -> Any:
    n = len(o)
    open_ns = np.arange(n, dtype=np.int64) * minutes * MIN
    return rs.TF(arr(*o), arr(*h), arr(*lo), arr(*c), np.ones(n), open_ns, open_ns + minutes * MIN)


def test_fvg_needs_a_gap_and_consecutive_bars() -> None:
    b = bars([10, 11, 13], [10.5, 12.5, 14], [9.5, 10.8, 11.0], [10.2, 12.4, 13.5])
    assert rs.fvg(b, 2) == 1                                   # low_2 11.0 > high_0 10.5
    b2 = bars([10, 9, 7], [10.5, 9.2, 8.0], [9.5, 8.0, 7.0], [9.6, 8.1, 7.5])
    assert rs.fvg(b2, 2) == -1                                 # high_2 8.0 < low_0 9.5
    b.open_ns[2] += 60 * MIN                            # a hole between the bars: no gap
    assert rs.fvg(b, 2) == 0


def test_cost_gate_uses_the_setup_session_round_trip() -> None:
    costs = {"asia": {"rt": 0.2, "ov": 0.1}, "london": {"rt": 0.2, "ov": 0.1}, "newyork": {"rt": 0.2, "ov": 0.1}}
    s = rs.Setup(8 * 60 * MIN, 1, 100.0, 98.6, 100.5, 9 * 60 * MIN, None, 60 * MIN)
    assert rs.gated(costs, s)                                  # 0.30 > 0.2 x 1.4
    s2 = rs.Setup(8 * 60 * MIN, 1, 100.0, 98.4, 100.5, 9 * 60 * MIN, None, 60 * MIN)
    assert not rs.gated(costs, s2)                             # 0.30 <= 0.2 x 1.6


def test_twin_keeps_risk_offset_and_life_on_its_own_side() -> None:
    b = bars([100.0] * 6, [101.0] * 6, [99.0] * 6, [100.0, 101, 102, 103, 104, 105])
    real = rs.Setup(int(b.close_ns[0]), 1, 99.0, 97.0, 100.0, int(b.close_ns[0]) + 16 * 15 * MIN, None,
                    8 * 60 * MIN)
    for i in range(8):
        tw = rs.twin_setup("RS3", real, b, list(range(6)), i)
        assert tw is not None
        assert abs(tw.limit - tw.stop) == pytest.approx(2.0)
        assert abs(tw.close - tw.limit) == pytest.approx(1.0)
        assert tw.side * (tw.close - tw.limit) > 0              # limit on the pull-back side
        assert tw.side * (tw.limit - tw.stop) > 0
        assert tw.t_expiry - tw.t_setup == 16 * 15 * MIN
