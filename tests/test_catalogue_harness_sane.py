"""S0 harness-sane (EVAL-2 method v6 section 6, prereg 2026-11-09 section 1): "NULL-RAND controls per TF, recomputed
with 20 twins (the 90-control regression). Measures the twin correlation used for the finite-M SE. Mean gross and mean
excess within +/-0.01 R, |t| < 2. No control cell with gross < -0.05 R at n >= 300. Control-cell dispersion =
sampling variance (chi2 p > 0.01)."

Here the controls run on seeded driftless random walks (tests/test_catalogue_random_walk.py `rw_cell`: the price is a
martingale), with the registered NULL-RAND detector (random side, stop 1 ATR14, target 2 ATR14, hold 10), the
harness's simulate for the signals and `sim.draw_twins` / `sim.simulate_twins` for 20 twins each; t statistics are
clustered by fill day. The full run (cells 15m/1h/4h/1d, ~117k signals, ~100 s) is the `selftest` set; its n is set
so that +/-0.01 R is about 2 standard errors of the mean excess (at 39k signals it was 1.25 SE: a sound harness
would fail ~1 run in 5). The smoke test (one 1h walk, fixed seed, < 10 s) runs in the unit suite. The `selftest`
pytest marker is registered by the SRE lane (pyproject + CI job); until it lands the full run is opted into with
GOLDBOT_SELFTEST=1."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

import numpy as np
import pytest
from scipy import stats as sps

from goldbot.research.catalogue import dispatcher as D
from goldbot.research.catalogue import sim as SIM
from goldbot.research.catalogue.core import Ctx
from goldbot.research.catalogue.data import DAY
from tests.test_catalogue_random_walk import rw_cell

COL = {k: j for j, k in enumerate(SIM.TRADE_COLS)}
RATE = 0.1                  # NULL-RAND entry rate on the walk (the screen's 1-2 %, raised for n)
TOL_R = 0.01                # S0: mean gross and mean excess within +/-0.01 R
T_MAX = 2.0
CELL_FLOOR_R = -0.05        # no control cell with gross < -0.05 R at n >= 300
CELL_N = 300
CHI2_P = 0.01
FULL = {"15m": (16_000, 36), "1h": (10_000, 48), "4h": (6_000, 60), "1d": (4_000, 72)}   # tf: (bars, walks)


@dataclass
class Cell:
    tf: str
    gross: np.ndarray
    net: np.ndarray
    twins: np.ndarray
    day: np.ndarray


def null_cell(tf: str, n: int, seed: int) -> Cell:
    b = rw_cell("XAUUSD", tf, seed, n)[0]
    ev = D.null_rand(b, {"rate": RATE}, Ctx(window="explore"))
    cm = SIM.default_model()
    tr = SIM.simulate(b, ev, control="random_side", seed_key=f"s0|{seed}", costs=cm)
    key = {(int(i), int(s)): k for k, (i, s) in enumerate(zip(ev.i, ev.side))}
    ks = np.array([key[(int(r[COL["i"]]), int(r[COL["side"]]))] for r in tr], np.int64)
    exits = np.full(len(ev), -1, np.int64)
    exits[ks] = tr[:, COL["x"]].astype(np.int64)
    fire = np.zeros(len(b), bool)
    fire[ev.i] = True
    draws = SIM.draw_twins(b, ev, exits=exits, family_fire=fire, seed_key=f"s0|{seed}")
    tw = SIM.simulate_twins(b, ev, [draws[k] for k in ks], costs=cm)
    gross = tr[:, COL["gross"]]
    return Cell(tf, gross, gross - tr[:, COL["cost"]], tw, tr[:, COL["t_fill"]].astype(np.int64) // DAY)


def cluster_t(x: np.ndarray, day: np.ndarray) -> tuple[float, float]:
    """(mean, t) with the standard error clustered by day."""
    ok = np.isfinite(x)
    x, day = x[ok], day[ok]
    m = float(x.mean())
    _, inv = np.unique(day, return_inverse=True)
    s = np.bincount(inv, weights=x - m)
    se = float(np.sqrt((s ** 2).sum())) / len(x)
    return m, m / se


def s0_metrics(cells: list[Cell]) -> dict[str, float]:
    # separate walks get disjoint day labels so their days never merge into one cluster
    day = np.concatenate([c.day + 10**7 * j for j, c in enumerate(cells)])
    gross = np.concatenate([c.gross for c in cells])
    net = np.concatenate([c.net for c in cells])
    tw = np.vstack([c.twins for c in cells])
    ex = SIM.excess_r(net, tw)
    g, tg = cluster_t(gross, day)
    e, te = cluster_t(ex, day)
    has = np.isfinite(tw).any(axis=1)
    se_ex = np.nanstd(ex) / np.sqrt(np.isfinite(ex).sum())
    se_sig = np.nanstd(net) / np.sqrt(len(net))
    return {"n": float(len(net)), "gross": g, "t_gross": tg, "excess": e, "t_excess": te,
            "twin_net": float(np.nanmean(tw[has])), "rho": SIM.twin_correlation(net, tw, day),
            "se_ratio": float(se_ex / se_sig), "twin_fill": float(np.isfinite(tw).mean())}


def tf_cells(cells: list[Cell]) -> dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """The control cell of each timeframe (its walks pooled, days kept apart): (gross, excess, day)."""
    out = {}
    for tf in dict.fromkeys(c.tf for c in cells):
        cs = [(j, c) for j, c in enumerate(cells) if c.tf == tf]
        out[tf] = (np.concatenate([c.gross for _, c in cs]),
                   np.concatenate([SIM.excess_r(c.net, c.twins) for _, c in cs]),
                   np.concatenate([c.day + 10**7 * j for j, c in cs]))
    return out


def dispersion_p(cells: list[Cell]) -> float:
    """Control-cell dispersion against sampling variance: sum of the cells' squared clustered t of mean excess ~
    chi2(cells) when every cell is a null."""
    t = np.array([cluster_t(ex, day)[1] for _, ex, day in tf_cells(cells).values()])
    return float(sps.chi2.sf(float((t ** 2).sum()), len(t)))


def test_s0_smoke_null_controls_with_20_twins_on_a_fixed_seed_walk_runs_under_10_s() -> None:
    t0 = time.perf_counter()
    cell = null_cell("1h", 3_000, seed=20261109)
    m = s0_metrics([cell])
    secs = time.perf_counter() - t0
    assert secs < 10.0, secs
    assert m["n"] >= 150 and m["twin_fill"] > 0.9
    assert abs(m["t_gross"]) < 3 and abs(m["t_excess"]) < 3
    assert np.isfinite(m["rho"]) and -1.0 <= m["rho"] <= 1.0
    assert m["twin_net"] < 0.0              # driftless walk: twins lose their cost


@pytest.mark.skipif(os.environ.get("GOLDBOT_SELFTEST") != "1",
                    reason="S0 full run: selftest set (GOLDBOT_SELFTEST=1 until the SRE lane registers the marker)")
def test_s0_null_controls_per_timeframe_are_within_a_hundredth_of_r_with_20_twins() -> None:
    cells: list[Cell] = []
    for tf, (n, walks) in FULL.items():
        cells += [null_cell(tf, n, seed=1_000 * (list(FULL).index(tf) + 1) + w) for w in range(walks)]
    m = s0_metrics(cells)
    print("S0", {k: round(v, 4) for k, v in m.items()})
    assert abs(m["gross"]) <= TOL_R and abs(m["t_gross"]) < T_MAX, m
    assert abs(m["excess"]) <= TOL_R and abs(m["t_excess"]) < T_MAX, m
    for tf, (g, _, _) in tf_cells(cells).items():
        if len(g) >= CELL_N:
            assert g.mean() >= CELL_FLOOR_R, (tf, g.mean())
    assert dispersion_p(cells) > CHI2_P
