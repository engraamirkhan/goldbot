"""The mlexec lane's execution engine (scripts/mlprob/mlexec_screen.py) on hand-built minute paths."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def mx():
    spec = importlib.util.spec_from_file_location("mlexec_screen", ROOT / "scripts" / "mlprob" / "mlexec_screen.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mlexec_screen"] = mod
    spec.loader.exec_module(mod)
    return mod


def _ctx(mx, o, h, lo, c, t0="2012-03-06 10:00"):
    n = len(o)
    t = (pd.Timestamp(t0, tz="UTC").value + np.arange(n, dtype=np.int64) * mx.MIN).astype(np.int64)
    z = np.zeros(n, dtype=np.int64)
    return mx.Ctx(t, np.asarray(o, float), np.asarray(h, float), np.asarray(lo, float), np.asarray(c, float),
                  np.full(n, n - 1), z, z, z, {}, pd.DataFrame(), int(t[0]), int(t[-1]) + mx.MIN)


def test_limit_fill_needs_trade_through(mx):
    # long, P = 100, R = 10, depth 0.3 -> L = 97; a low of 96.95 touches but does not trade through by $0.10
    o = [100, 99, 98, 97.5, 97.2]
    h = [100.5, 99.5, 98.5, 98, 97.5]
    lo = [99, 97.5, 96.95, 96.85, 96]
    ctx = _ctx(mx, o, h, lo, o)
    kf, px = mx.limit_fill(ctx, 0, 1, 100.0, 10.0, 0.30, 60, np.iinfo(np.int64).max)
    assert kf == 3 and px == pytest.approx(97.0)


def test_limit_fill_gap_takes_open_and_expiry_skips(mx):
    o = [100, 96.0]
    ctx = _ctx(mx, o, [100.2, 96.5], [99.8, 95.5], o)
    kf, px = mx.limit_fill(ctx, 0, 1, 100.0, 10.0, 0.30, 60, np.iinfo(np.int64).max)
    assert kf == 1 and px == pytest.approx(96.0)
    ctx2 = _ctx(mx, [100, 100], [101, 101], [99.5, 99.5], [100, 100])
    assert mx.limit_fill(ctx2, 0, 1, 100.0, 10.0, 0.30, 60, np.iinfo(np.int64).max) is None
    # short mirror: L = 103, the high must reach 103.10
    ctx3 = _ctx(mx, [100, 102, 103], [101, 103.05, 103.2], [99, 101, 102.5], [100, 102, 103])
    kf, px = mx.limit_fill(ctx3, 0, -1, 100.0, 10.0, 0.30, 60, np.iinfo(np.int64).max)
    assert kf == 2 and px == pytest.approx(103.0)


def test_fill_minute_counts_the_stop_but_not_the_target(mx):
    # long filled at 97 in minute 0 with R = 1: that minute's high 99 (>= 2R target) is ignored, the next minute hits it
    o = [98, 98.5, 99.5]
    h = [99, 99.2, 100]
    lo = [96.5, 98, 99]
    ctx = _ctx(mx, o, h, lo, o)
    g, kind, kx, _ = mx.walk_px(ctx, 0, 1, 97.0, 1.0, 2.0, np.nan, np.iinfo(np.int64).max, None,
                                first_stop_only=True)
    assert kind == "target" and kx == 1 and g == pytest.approx(2.0)
    # the same path without the fill-minute rule takes the target in minute 0
    g2, kind2, kx2, _ = mx.walk_px(ctx, 0, 1, 97.0, 1.0, 2.0, np.nan, np.iinfo(np.int64).max, None)
    assert kind2 == "target" and kx2 == 0
    # the stop in the fill minute counts
    ctx2 = _ctx(mx, [98, 98], [98.5, 98.5], [95.9, 97], [97, 98])
    g3, kind3, kx3, _ = mx.walk_px(ctx2, 0, 1, 97.0, 1.0, np.nan, np.nan, np.iinfo(np.int64).max, None,
                                   first_stop_only=True)
    assert kind3 == "stop" and kx3 == 0 and g3 == pytest.approx(-1.0)


def test_swapcut_exits_a_long_not_yet_working(mx):
    o = [100, 100.2, 100.3, 100.4, 100.1]
    ctx = _ctx(mx, o, [x + 0.1 for x in o], [x - 0.1 for x in o], o)
    checks = np.array([2])
    g, kind, kx, _ = mx.walk_px(ctx, 0, 1, 100.0, 1.0, np.nan, np.nan, np.iinfo(np.int64).max, None, checks=checks)
    assert kind == "swapcut" and kx == 2 and g == pytest.approx(0.3)
    # a long already +0.5 R at the check holds; shorts are never checked by the caller
    o2 = [100, 100.6, 100.7, 100.8]
    ctx2 = _ctx(mx, o2, [x + 0.1 for x in o2], [x - 0.1 for x in o2], o2)
    g2, kind2, _, _ = mx.walk_px(ctx2, 0, 1, 100.0, 1.0, np.nan, np.nan, np.iinfo(np.int64).max, None,
                                 checks=np.array([2]))
    assert kind2 == "time"


def test_bps_costs_scale_with_price(mx):
    rt, os_, sw = mx.bps_scale(np.array([0.4]), np.array([0.2]), np.array([-0.6]), np.array([2000.0]))
    assert rt[0] == pytest.approx(0.2) and os_[0] == pytest.approx(0.1) and sw[0] == pytest.approx(-0.3)
