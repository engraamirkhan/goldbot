"""S0 harness-sane (EVAL-2 method v6 section 6, prereg 2026-11-09 section 1): "NULL-RAND controls per TF, recomputed
with 20 twins (the 90-control regression). Measures the twin correlation used for the finite-M SE. Mean gross and mean
excess within +/-0.01 R, |t| < 2. No control cell with gross < -0.05 R at n >= 300. Control-cell dispersion =
sampling variance (chi2 p > 0.01)."

Here the controls run on seeded driftless random walks (tests/test_catalogue_random_walk.py `rw_cell`: the price is a
martingale), with the registered NULL-RAND detector (random side, stop 1 ATR14, target 2 ATR14, hold 10), the
harness's simulate for the signals and `sim.draw_twins` / `sim.simulate_twins` for 20 twins each; t statistics are
clustered by session day (17:00 New York, `sim.session_day`). The full run (cells 15m/1h/4h/1d, ~117k signals,
~100 s) is the `selftest` set and qualifies at SE(mean excess) <= 0.005 R (at 39k signals +/-0.01 R was only 1.25 SE:
a sound harness would fail ~1 run in 5). The fast CI S0 (one 1h walk, fixed seed, < 10 s) asserts only |t| < 3.
`se_ratio` and `rho` are reported diagnostics, never a multiplier."""
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
from tests.test_catalogue_random_walk import rw_cell

COL = {k: j for j, k in enumerate(SIM.TRADE_COLS)}
RATE = 0.1                  # NULL-RAND entry rate on the walk (the screen's 1-2 %, raised for n)
TOL_R = 0.01                # S0: mean gross and mean excess within +/-0.01 R
T_MAX = 2.0
CELL_FLOOR_R = -0.05        # no control cell with gross < -0.05 R at n >= 300
CELL_N = 300
CHI2_P = 0.01
SE_MAX = 0.005              # the full run qualifies at SE(mean excess) <= 0.005 R
WALK_DAYS = 10_000_000      # day-label offset per walk (walks never share a cluster)
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
    return Cell(tf, gross, gross - tr[:, COL["cost"]], tw, SIM.session_day(tr[:, COL["t_fill"]].astype(np.int64)))


def cluster_t(x: np.ndarray, day: np.ndarray) -> tuple[float, float, float]:
    """(mean, t, se) with the standard error clustered by session day."""
    ok = np.isfinite(x)
    x, day = x[ok], day[ok]
    m = float(x.mean())
    _, inv = np.unique(day, return_inverse=True)
    s = np.bincount(inv, weights=x - m)
    se = float(np.sqrt((s ** 2).sum())) / len(x)
    return m, m / se, se


def s0_metrics(cells: list[Cell]) -> dict[str, float]:
    """S0 statistics. `se_ratio` (SE of the excess over SE of the signal net) and `rho` are diagnostics only: Lane 3
    uses the measured day-clustered SD of the 20-twin excess per cell, never a multiplier (quant round 2 item 1)."""
    # separate walks get disjoint day labels so their days never merge into one cluster
    day = np.concatenate([c.day + WALK_DAYS * j for j, c in enumerate(cells)])
    gross = np.concatenate([c.gross for c in cells])
    net = np.concatenate([c.net for c in cells])
    tw = np.vstack([c.twins for c in cells])
    ex = SIM.excess_r(net, tw)
    g, tg, _ = cluster_t(gross, day)
    e, te, se_e = cluster_t(ex, day)
    n_tw = np.isfinite(tw).sum(axis=1)
    has = n_tw > 0
    _, _, se_s = cluster_t(net, day)
    return {"n": float(len(net)), "gross": g, "t_gross": tg, "excess": e, "t_excess": te, "se_excess": se_e,
            "twin_net": float(np.nanmean(tw[has])), "rho": SIM.twin_correlation(net, tw, day),
            "se_ratio": se_e / se_s, "has_twin": float(has.mean()), "m_eff": float(n_tw[has].mean())}


def tf_walks(cells: list[Cell]) -> dict[str, list[tuple[np.ndarray, np.ndarray, np.ndarray]]]:
    """Per timeframe, its walks: (gross, excess, session day)."""
    out: dict[str, list[tuple[np.ndarray, np.ndarray, np.ndarray]]] = {}
    for c in cells:
        out.setdefault(c.tf, []).append((c.gross, SIM.excess_r(c.net, c.twins), c.day))
    return out


def dispersion_p(walks: list[tuple[np.ndarray, np.ndarray, np.ndarray]]) -> float:
    """Control-cell dispersion against sampling variance within a timeframe: the walks' squared clustered t of mean
    excess sum to chi2(walks) when every walk is a null."""
    t = np.array([cluster_t(ex, day)[1] for _, ex, day in walks])
    return float(sps.chi2.sf(float((t ** 2).sum()), len(t)))


def test_s0_smoke_null_controls_with_20_twins_on_a_fixed_seed_walk_runs_under_10_s() -> None:
    """CI fast S0: plumbing and a loose sanity bound only (|t| < 3); the +/-0.01 R qualification is the full run."""
    t0 = time.perf_counter()
    m = s0_metrics([null_cell("1h", 3_000, seed=20261109)])
    assert time.perf_counter() - t0 < 10.0
    assert abs(m["t_gross"]) < 3 and abs(m["t_excess"]) < 3, m


# TODO(eval2): switch the skipif to @pytest.mark.selftest once branch ci/selftest-marker (SRE lane: marker in
# pyproject + the CI `selftest` job) is merged; until then the full run is opted into with GOLDBOT_SELFTEST=1.
@pytest.mark.skipif(os.environ.get("GOLDBOT_SELFTEST") != "1",
                    reason="S0 full run: selftest set (GOLDBOT_SELFTEST=1 until the selftest marker is merged)")
def test_s0_null_controls_per_timeframe_are_within_a_hundredth_of_r_with_20_twins() -> None:
    """Qualifies at SE(mean excess) <= 0.005 R (quant round 2 item 4: n about 117k); prints the full-run log."""
    cells: list[Cell] = []
    for tf, (n, n_walks) in FULL.items():
        cells += [null_cell(tf, n, seed=1_000 * (list(FULL).index(tf) + 1) + w) for w in range(n_walks)]
    m = s0_metrics(cells)
    print("S0 pooled", {k: round(v, 4) for k, v in m.items()})
    ok = True
    for tf, walks in tf_walks(cells).items():
        g = np.concatenate([w[0] for w in walks])
        mg, tg, _ = cluster_t(g, np.concatenate([w[2] + WALK_DAYS * j for j, w in enumerate(walks)]))
        p = dispersion_p(walks)
        floor_ok = len(g) < CELL_N or mg >= CELL_FLOOR_R
        print(f"S0 {tf:>3}: n {len(g):6d} gross {mg:+.4f} (t {tg:+.2f}) cell floor >= {CELL_FLOOR_R} "
              f"{'PASS' if floor_ok else 'FAIL'}; chi2 dispersion over {len(walks)} walks p {p:.3f} "
              f"{'PASS' if p > CHI2_P else 'FAIL'}")
        ok = ok and floor_ok and p > CHI2_P
    print(f"S0 diagnostics: se_ratio {m['se_ratio']:.3f}, rho {m['rho']:.3f} (reported, never a multiplier)")
    assert m["se_excess"] <= SE_MAX, m
    assert abs(m["gross"]) <= TOL_R and abs(m["t_gross"]) < T_MAX, m
    assert abs(m["excess"]) <= TOL_R and abs(m["t_excess"]) < T_MAX, m
    assert ok
