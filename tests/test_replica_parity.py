"""Replica parity check (goldbot/research/replica_parity.py): an optional health check comparing the replica with
pulled chart values. Fixtures are synthetic bars and "pulls" made from the replica's own outputs (no vendor data)."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from goldbot.features import smc_replica as R
from goldbot.research.replica_parity import PLOTS, parity

STUDIES = {"S-A": "a", "S-B": "b", "S-C": "c"}
PIDS = {"a": "S-A", "b": "S-B", "c": "S-C"}


def _bars(n: int = 900, seed: int = 4) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    c = 2000 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    o = np.r_[2000.0, c[:-1]]
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.001, n)))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.001, n)))
    ts = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"bar_time": ts, "open": o, "high": h, "low": lo, "close": c,
                         "volume": rng.integers(100, 1000, n).astype(float)})


def _pull_from_replica(bars: pd.DataFrame) -> pd.DataFrame:
    cache: dict[str, Any] = {k: bars[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume")}
    parts = []
    for letter, pid, _, kind, fn in PLOTS:
        v = np.asarray(fn(dict(cache), R.DEFAULT_PARAMS), dtype=float)
        if kind == "state":
            v = np.where(v < 0, np.nan, v)                 # the chart leaves uncoloured bars empty
        parts.append(pd.DataFrame({"bar_time": bars["bar_time"], "pine_id": PIDS[letter], "plot_id": pid, "value": v}))
    return pd.concat(parts, ignore_index=True)


def test_a_pull_equal_to_the_replica_is_ok_and_exact():
    bars = _bars()
    rep = parity(bars, _pull_from_replica(bars), studies=STUDIES)
    assert rep.status == "OK" and len(rep.rows) == len(PLOTS)
    assert all(r.exact for r in rep.rows)


def test_a_moved_event_or_line_gives_warn_and_names_the_plot():
    bars = _bars()
    pull = _pull_from_replica(bars)
    sel = (pull["plot_id"] == "plot_21") & (pull["pine_id"] == "S-A")     # bullish gaps
    ev = pull.index[sel & (pull["value"] == 1)]
    pull.loc[ev[: max(1, len(ev) // 10)], "value"] = 0.0                  # drop 10% of the chart's events
    line = (pull["plot_id"] == "plot_0") & (pull["pine_id"] == "S-C")
    pull.loc[pull.index[line][-1], "value"] = 55.5
    rep = parity(bars, pull, studies=STUDIES)
    assert rep.status == "WARN"
    names = {r.plot for r in rep.failing()}
    assert "a:plot_21 fvg_bull" in names and "c:plot_0 mfi" in names


def test_window_limits_the_comparison_and_an_empty_window_is_skip():
    bars = _bars()
    pull = _pull_from_replica(bars)
    rep = parity(bars, pull, start="2025-01-20", end="2025-01-25", studies=STUDIES)
    assert rep.status == "OK" and rep.bars_compared == 5 * 24
    assert parity(bars, pull, start="2030-01-01", studies=STUDIES).status == "SKIP"
    assert parity(bars, pull.iloc[:0], studies=STUDIES).status == "SKIP"
