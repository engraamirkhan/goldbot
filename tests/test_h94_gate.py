"""H-94 range-to-cost gate runner (scripts/mlprob/h94_gate.py): its pure pieces on hand-built inputs."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def g() -> Any:
    spec = importlib.util.spec_from_file_location("h94_gate", ROOT / "scripts" / "mlprob" / "h94_gate.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["h94_gate"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_session_costs_and_gate_multipliers_match_the_card(g: Any) -> None:
    rt = g.session_rt()
    assert rt == {"asia": 0.204, "london": 0.293, "newyork": 0.263}
    m = g.gate_mult(rt)
    assert m["london"] == pytest.approx(1.157, abs=0.002)
    assert m["newyork"] == pytest.approx(1.038, abs=0.002)
    assert m["asia"] == pytest.approx(0.805, abs=0.002)


def test_thin_hours_count_as_asia(g: Any) -> None:
    t = pd.Timestamp("2012-03-06 22:30", tz="UTC").value       # NY 17:30 EST: after the close, before Tokyo
    assert g.sessions(np.array([t]))[0] == "asia"


def test_har_design_uses_only_past_rows(g: Any) -> None:
    lr = np.arange(30, dtype=float)
    x = g.har_design(lr)
    assert np.isnan(x[21]).all()
    assert x[22].tolist() == [1.0, 21.0, np.mean(lr[17:22]), np.mean(lr[0:22])]
    lr2 = lr.copy()
    lr2[25] = 999.0                                              # changing day 25 cannot move rows <= 25
    assert np.array_equal(g.har_design(lr2)[:26], x[:26], equal_nan=True)


def test_tier1_blocks_entries_and_closes_open_positions(g: Any) -> None:
    mn = g.E.MIN
    t = np.arange(0, 600, dtype=np.int64) * mn
    o = 100.0 + np.arange(600) * 0.01
    ev = np.array([300 * mn])
    t_fill = np.array([250 * mn, 330 * mn, 100 * mn, 100 * mn, 331 * mn])
    t_exit = np.array([400 * mn, 400 * mn, 400 * mn, 200 * mn, 500 * mn])
    keep, gross, tx, kind = g.tier1(t_fill, t_exit, np.array([1, 1, -1, 1, 1]), np.zeros(5),
                                    np.array(["time"] * 5, dtype=object), t, o, ev)
    assert keep.tolist() == [False, False, True, True, True]     # T-60 and T+30 are inside the block
    assert tx[2] == 295 * mn and kind[2] == "news"                # closed at the open of T-5
    assert gross[2] == pytest.approx(-(o[295] - o[100]))
    assert kind[3] == "time" and tx[3] == 200 * mn               # exited before T-5: untouched
    assert kind[4] == "time"                                      # filled after the event


def test_clustered_t_matches_plain_t_with_singleton_clusters(g: Any) -> None:
    rng = np.random.default_rng(0)
    y = rng.normal(size=400)
    x = (np.arange(400) % 2).astype(float)
    y[x == 1] += 0.3
    d, t = g.clustered_diff_t(y, x, np.arange(400))
    assert d == pytest.approx(y[x == 1].mean() - y[x == 0].mean())
    assert 3 < t < 7


def test_placebo_matches_cell_counts_with_whole_days(g: Any) -> None:
    cell = np.array(["a"] * 6 + ["b"] * 4)
    day = np.array([1, 1, 2, 3, 3, 3, 1, 2, 3, 4])
    m = g.placebo_masks(cell, day, {"a": 3, "b": 2}, 50, 1)
    assert (m[:, 6:].sum(1) == 2).all()
    assert np.isin(m[:, :6].sum(1), [3, 4]).all()               # greedy: within the smallest day of the target
    for row in m:                                                 # whole days only
        for c in ("a", "b"):
            for dd in np.unique(day[cell == c]):
                sel = (cell == c) & (day == dd)
                assert row[sel].all() or not row[sel].any()


def test_price_floor_and_financing(g: Any) -> None:
    df = pd.DataFrame({"t_fill": [pd.Timestamp("2012-03-06 10:00", tz="UTC").value] * 2,
                       "t_exit": [pd.Timestamp("2012-03-08 10:00", tz="UTC").value] * 2,
                       "side": [1, -1], "risk": [10.0, 0.4], "gross": [5.0, 0.0], "kind": ["stop", "target"],
                       "px": [1600.0, 1600.0]})
    dtb3 = pd.DataFrame({"available_utc": [pd.Timestamp("2012-01-01", tz="UTC")], "rate_pct": [0.1]})
    out = g.price_h94(df, g.session_rt(), g.session_overshoot(), dtb3, (0.74, 0.74), g.SwapSpec.shipped())
    assert out.sess.tolist() == ["london", "london"]
    assert out.rt.iloc[0] == pytest.approx(0.293 * 0.4)
    assert out.os.tolist()[1] == 0.0                              # target exit: no overshoot
    # Tue 10:00 -> Thu 10:00: one night plus Wednesday's triple = 4 nights; long pays 0.1 + 0.74, short pays 0.74 - 0.1
    assert out.fin.iloc[0] == pytest.approx(4 * 1600 * 0.84 / 100 / 365)
    assert out.fin.iloc[1] == pytest.approx(4 * 1600 * 0.64 / 100 / 365)
    assert out.floor_skip.tolist() == [False, True]
