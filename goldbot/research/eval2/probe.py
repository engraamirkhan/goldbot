"""EVAL-2 L1: the exit-agnostic information probe (prereg 2026-11-09 sections 1 and 4.4; method v6 section 2, L1).

"L1 probe: symmetric first passage at +/-1 ATR (decision-TF ATR), from the fill bar's close, capped at the native
horizon; side-0 OCO bar = loss." "Statistic: excess of the probe over 20 twins, partially pooled per family. Family
retire (NO SIGNAL) if the pooled 90 % UB < the cheapest feasible cell's deciding cost in ATR and power >= 0.8 at that
cost. Per cell the probe is a diagnostic, not a gate."

Readings (listed for the quant-reviewer; none changes a frozen name or threshold):
- ATR: Wilder ATR14 of the decision timeframe (sim.TWIN_ATR_N, the twins' geometry ATR) at the fill bar, known at
  the fill bar's close where the probe starts.
- A bar touching both barriers scores -1 whatever the side (the intrabar order is unknown: the sim's stop-first
  convention); an instance whose side is 0 (both OCO legs on one bar) scores -1 outright. A gap open beyond one
  barrier is that barrier first. A close-only bar (`Bars.co`) is read on its close.
- Neither barrier by the horizon scores 0 (the truncated first-passage value; E = 0 under a driftless walk). No
  finite ATR at the fill bar, or no bar after it: NaN (left out).
- Partial pooling (quant M2): random effects over the family's cells, Hartung-Knapp-Sidik-Jonkman variance floored
  at the DerSimonian-Laird variance, with the t quantile on k - 1 df (DL with z covered 86.8 % at k = 3, tau = se);
  a one-cell family uses its own normal bound. Heterogeneous cells widen the bound, so disagreement can never retire
  a family. Power: the one-sided level-0.10 test with the same SE and quantile.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from goldbot.research.catalogue.core import atr
from goldbot.research.catalogue.data import Bars
from goldbot.research.catalogue.sim import TWIN_ATR_N
from goldbot.research.catalogue.stats import cluster_se

UB_LEVEL = 0.90
Z_UB = 1.2815515655446004            # z(0.90): the one-sided 90 % upper bound


@dataclass(frozen=True, slots=True)
class ProbeResult:
    family: str
    cell_id: str
    n: int
    excess_atr: float
    se_atr: float
    ub90_atr: float


def first_passage(b: Bars, *, fill_bars: np.ndarray, sides: np.ndarray, horizon_bars: int,
                  barrier_atr: float = 1.0) -> np.ndarray:
    """+1 / -1 per instance: which of the +/-`barrier_atr` x ATR barriers around the fill bar's close the instance's
    side reaches first within `horizon_bars` bars after the fill bar (module docstring for ties, side 0, no hit)."""
    fills = np.asarray(fill_bars, np.int64)
    sd = np.asarray(sides, np.int64)
    a = atr(b, TWIN_ATR_N)
    n = len(b)
    out = np.full(len(fills), np.nan)
    for k, (f, side) in enumerate(zip(fills.tolist(), sd.tolist())):
        if f < 0 or f >= n - 1 or not np.isfinite(a[f]) or a[f] <= 0:
            continue
        if side == 0:
            out[k] = -1.0
            continue
        ref, d = float(b.c[f]), barrier_atr * float(a[f])
        up, dn = ref + d, ref - d
        res = 0.0
        for j in range(f + 1, min(f + horizon_bars, n - 1) + 1):
            if b.co[j]:
                hit_up, hit_dn = b.c[j] >= up, b.c[j] <= dn
            elif b.o[j] >= up or b.o[j] <= dn:
                hit_up, hit_dn = b.o[j] >= up, b.o[j] <= dn
            else:
                hit_up, hit_dn = b.h[j] >= up, b.l[j] <= dn
            if hit_up and hit_dn:
                res = -1.0
                break
            if hit_up or hit_dn:
                res = float(side) if hit_up else -float(side)
                break
        out[k] = res
    return out


def probe_cell(signal: np.ndarray, twins: np.ndarray, day: np.ndarray, *, family: str, cell_id: str) -> ProbeResult:
    """The cell's probe excess: signal probe minus the mean of its twins' probes (instances with a probe and at least
    one twin), its session-day-clustered SE and the one-sided 90 % upper bound. A diagnostic, not a gate."""
    s = np.asarray(signal, float)
    tw = np.asarray(twins, float)
    cnt = np.isfinite(tw).sum(axis=1)
    ex = s - np.where(cnt > 0, np.nansum(tw, axis=1) / np.maximum(cnt, 1), np.nan)
    ok = np.isfinite(ex)
    mean = float(ex[ok].mean()) if ok.any() else float("nan")
    se = cluster_se(ex, np.asarray(day))
    return ProbeResult(family, cell_id, int(ok.sum()), mean, se, mean + Z_UB * se)


def _pooled(probes: Sequence[ProbeResult]) -> tuple[float, float, float]:
    """(pooled mean, SE, one-sided 90 % quantile) over the cells with a finite estimate and SE > 0: HKSJ with the DL
    variance as a floor and t_{k-1}; one cell: its own estimate, SE and z(0.90)."""
    from scipy.stats import t as student_t
    est = np.array([p.excess_atr for p in probes], float)
    se = np.array([p.se_atr for p in probes], float)
    ok = np.isfinite(est) & np.isfinite(se) & (se > 0)
    est, v = est[ok], se[ok] ** 2
    k = len(est)
    if not k:
        return float("nan"), float("nan"), float("nan")
    if k == 1:
        return float(est[0]), float(math.sqrt(v[0])), Z_UB
    w = 1.0 / v
    fixed = float(w @ est / w.sum())
    q = float(w @ (est - fixed) ** 2)
    c = float(w.sum() - (w @ w) / w.sum())
    tau2 = max(0.0, (q - (k - 1)) / c) if c > 0 else 0.0
    w2 = 1.0 / (v + tau2)
    mean = float(w2 @ est / w2.sum())
    var_dl = 1.0 / float(w2.sum())
    var_hk = float(w2 @ (est - mean) ** 2) / ((k - 1) * float(w2.sum()))
    return mean, math.sqrt(max(var_dl, var_hk)), float(student_t.ppf(UB_LEVEL, k - 1))


def pooled_bound(probes: Sequence[ProbeResult]) -> tuple[float, float]:
    """(pooled probe excess, its one-sided 90 % upper bound) by the family pooling of the L1 retire (module
    docstring). NaN, NaN without a usable cell."""
    mean, se, q = _pooled(probes)
    return mean, mean + q * se


def pooled_lower_bound(probes: Sequence[ProbeResult]) -> float:
    """The pooled probe excess's one-sided 90 % lower bound, same pooling as the bound above (A17: no information when
    it is <= 0)."""
    mean, se, q = _pooled(probes)
    return mean - q * se


def family_no_signal(probes: Sequence[ProbeResult], *, cheapest_cost_atr: float, power_min: float = 0.8) -> bool:
    """NO SIGNAL (family retire): the partially pooled 90 % upper bound of the probe excess is below the cheapest
    feasible cell's deciding cost in ATR AND the pooled read has power >= `power_min` to see an excess of that cost.
    No cells, or a NaN anywhere in the pooled read: never retired."""
    mean, se, q = _pooled(probes)
    if not (np.isfinite(mean) and np.isfinite(se) and np.isfinite(cheapest_cost_atr)) or se <= 0:
        return False
    from scipy.stats import norm
    power = float(norm.cdf(cheapest_cost_atr / se - q))                   # same SE and quantile as the bound
    return bool(mean + q * se < cheapest_cost_atr and power >= power_min)
