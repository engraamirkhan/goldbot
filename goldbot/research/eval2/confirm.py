"""EVAL-2 L3 confirm: bootstrap, BatchPRDS, Holm and the read (prereg 2026-11-09 sections 1, 3 and 4.6).

BatchPRDS: Zrnic, Jiang, Ramdas and Jordan, "The power of batching in multiple hypothesis testing", AISTATS 2020
(arXiv 1910.04968), section 5, Algorithm 1, under the quant ruling of 2026-11-09: "both windows use pure
alpha-spending: alpha_j = q·gamma_j, BH at alpha_j within batch j (PRDS within a batch), no rejection credit ever.
Valid under arbitrary cross-batch dependence: FDR <= sum_j FDR_j <= q." gamma_1 = 0.5; gamma_j = 0.5 x (6 / pi^2) /
(j - 1)^2 for j >= 2 (sums to 1). TEST-OLD starts at batch 1; EVAL-2's first 2018-25 read is batch 15
(`catalogue.run.read_batch_index`).

L3 (prereg section 1): "statistic: control-variate net ... at 1.0x deciding cost; weights fixed from explore (n, sd)
in the frozen card". "p: one-sided stationary day-block bootstrap (mean block = E1 lag), instances resampled with their
twins." "Gate (all AND): BatchPRDS rejection at q 0.10 on that p; one-sided p of mean excess <= 0.10; bootstrap 80 % LB
of net at 1.5x deciding cost > 0; over 2006-09 + 2018-25: net > 0 in >= 3 calendar years, at least one of 2021/2022,
no 2-year sub-period with net t <= -2; 2018-25 corroboration one-sided p <= 0.10 on net (CONFIRM-FRESH carried cells
only); E9 for holds >= 1 day." "Cells: after the family rejects: Holm over its <= 3 carried cells at the family's
BatchPRDS level, on each cell's control-variate net p." Lane 3 condition (quant signature 2026-11-09): "the frozen
helper confirm_resamples(m_j, alpha_j) is called by l3_read itself, with m_j = len(batch_p) and alpha_j =
batch.level(j, m_j); the resamples are never taken from the caller."

Readings (listed for the quant-reviewer; no frozen name or threshold changes):
- p is the basic (centred) bootstrap p, (1 + #{T* - T >= T}) / (B + 1); the lower bound is the matching basic bound
  2T - quantile(T*, level), so LB80 > 0 and p < 0.20 agree. A NaN resample counts against the family (a hit for p,
  -inf for the bound).
- p of mean excess: the family's precision-weighted mean excess (the control-variate's first term, same weights).
- The gate net at 1.5x: `stats.control_variate_net_at(..., cost_mult=1.5)`: net_r already pays the deciding cost
  (`costs.deciding_cost`) once, so only the extra 0.5x is charged.
- `corroboration`: the caller passes the 2018-25 units of the family's CONFIRM-FRESH carried cells; None (nothing
  fresh to read) fails the AND. The year rule reads TEST-OLD plus that frame.
- `batch_p` must hold this family's own p (the batch is the families read in batch j, fixed before the read); BH over
  `batch_p` decides the family.
- E9 needs the window's daily closes (`stats.drift_guard`), which the frozen signature does not carry: `e9` is None
  and a family with a 1d or 1w carried cell (holds >= 1 day) is not rejected (fail closed) until the caller's E9 is
  wired by an amendment.
"""
from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

import numpy as np
import pandas as pd

from goldbot.research.catalogue.costs import GATE_COST_MULT
from goldbot.research.catalogue.stats import (
    control_variate_net,
    control_variate_net_at,
    weighted_mean_excess,
    year_rule,
)
from goldbot.research.gates import CHOP_YEARS

Q_CONFIRM: Final = 0.10
GAMMA_FIRST: Final = 0.5
RESAMPLES_MIN: Final = 10_000
RESAMPLES_PER_TEST: Final = 20                 # ceil(20 m_j / alpha_j): >= 20 resamples beyond each BH threshold
P_EXCESS_MAX: Final = 0.10
P_CORROBORATION_MAX: Final = 0.10
LB_LEVEL: Final = 0.80
MULTI_DAY_TF: Final = ("1d", "1w")             # holds >= 1 day: E9 required

StatFn = Callable[[pd.DataFrame], float]


def gamma(j: int) -> float:
    """Prereg 3.2: gamma_1 = 0.5; gamma_j = 0.5 x (6 / pi^2) / (j - 1)^2 for j >= 2."""
    if j < 1:
        raise ValueError(f"batch index j must be >= 1, got {j}")
    return GAMMA_FIRST if j == 1 else (1.0 - GAMMA_FIRST) * (6.0 / math.pi ** 2) / (j - 1) ** 2


def confirm_resamples(m_j: int, alpha_j: float) -> int:
    """The frozen helper: max(10_000, ceil(20 * m_j / alpha_j)) resamples for every confirm-batch bootstrap."""
    if alpha_j <= 0:
        raise ValueError(f"alpha_j must be > 0, got {alpha_j}")
    return max(RESAMPLES_MIN, math.ceil(RESAMPLES_PER_TEST * m_j / alpha_j))


def _rng(seed_key: str) -> np.random.Generator:
    d = hashlib.sha256(f"eval2-boot|{seed_key}".encode()).digest()
    return np.random.default_rng(int.from_bytes(d[:8], "big"))


class _DayBlocks:
    """The units grouped by session day, in day order: resampling picks days and takes every instance of a picked
    day, each instance with its twins (one row holds the instance and its twin mean)."""

    def __init__(self, units: pd.DataFrame) -> None:
        day = units["day"].to_numpy()
        self.order = np.argsort(day, kind="stable")
        _, self.start, self.count = np.unique(day[self.order], return_index=True, return_counts=True)
        self.n = len(self.start)

    def rows(self, rng: np.random.Generator, mean_block: float) -> np.ndarray:
        """Politis-Romano stationary bootstrap over the day sequence (circular): a new block starts with probability
        1 / mean_block, else the next day follows. As many days as the window holds."""
        n = self.n
        new = rng.random(n) < 1.0 / mean_block
        new[0] = True
        starts = rng.integers(0, n, n)
        seg = np.cumsum(new) - 1
        first = np.flatnonzero(new)
        days = (starts[first][seg] + np.arange(n) - first[seg]) % n
        lens = self.count[days]
        base = np.repeat(self.start[days] - np.concatenate([[0], np.cumsum(lens)[:-1]]), lens)
        return np.asarray(self.order[base + np.arange(int(lens.sum()))])


def stationary_block_bootstrap(units: pd.DataFrame, stat: StatFn, *, mean_block: float, resamples: int,
                               seed_key: str) -> np.ndarray:
    """`resamples` values of `stat` on stationary day-block resamples of `units` (mean block `mean_block` days, the
    E1 lag), instances resampled with their twins. resamples = confirm_resamples(m_j, alpha_j) for every confirm-batch
    bootstrap. Keyed by `seed_key`."""
    if mean_block < 1.0:
        raise ValueError(f"mean block must be >= 1 day, got {mean_block}")
    if resamples < 1:
        raise ValueError(f"resamples must be >= 1, got {resamples}")
    blocks = _DayBlocks(units)
    out = np.full(resamples, np.nan)
    if not blocks.n:
        return out
    base = _Columns(units)
    rng = _rng(seed_key)
    for b in range(resamples):
        out[b] = stat(base.take(blocks.rows(rng, mean_block)))
    return out


class _Columns:
    """`units` as column arrays (text columns as categorical codes), rebuilt into a DataFrame for each resample: the
    same values, dtypes compared equal, and about 0.1 ms a resample instead of 4 ms for DataFrame.take on Arrow
    strings (the confirm runs up to ~4 x 10^5 resamples per bootstrap)."""

    def __init__(self, units: pd.DataFrame) -> None:
        self.cols: dict[str, np.ndarray | tuple[np.ndarray, pd.Index]] = {}
        for c in units.columns:
            col = units[c]
            if pd.api.types.is_numeric_dtype(col) or pd.api.types.is_bool_dtype(col):
                self.cols[str(c)] = col.to_numpy()
            else:
                cat = col if isinstance(col.dtype, pd.CategoricalDtype) else col.astype("category")
                self.cols[str(c)] = (cat.cat.codes.to_numpy(), cat.cat.categories)

    def take(self, rows: np.ndarray) -> pd.DataFrame:
        return pd.DataFrame({c: pd.Categorical.from_codes(v[0][rows], categories=v[1]) if isinstance(v, tuple)
                             else v[rows] for c, v in self.cols.items()}, copy=False)


def boot_p_one_sided(units: pd.DataFrame, stat: StatFn, *, mean_block: float, resamples: int,
                     seed_key: str) -> float:
    """One-sided p of H0: stat <= 0 by the centred (basic) bootstrap: (1 + #{T* - T >= T}) / (B + 1). A NaN
    statistic gives p = 1; a NaN resample counts as a hit."""
    obs = float(stat(units))
    if not np.isfinite(obs):
        return 1.0
    t = stationary_block_bootstrap(units, stat, mean_block=mean_block, resamples=resamples, seed_key=seed_key)
    hits = int(np.count_nonzero(~np.isfinite(t) | (t - obs >= obs)))
    return (1 + hits) / (resamples + 1)


def boot_lower_bound(units: pd.DataFrame, stat: StatFn, *, level: float, mean_block: float, resamples: int,
                     seed_key: str) -> float:
    """One-sided `level` lower bound of stat by the basic bootstrap: 2T - quantile(T*, level) (-inf for a NaN T or
    when NaN resamples reach the quantile)."""
    obs = float(stat(units))
    if not np.isfinite(obs):
        return -math.inf
    t = stationary_block_bootstrap(units, stat, mean_block=mean_block, resamples=resamples, seed_key=seed_key)
    t = np.where(np.isfinite(t), t, math.inf)          # a NaN resample sits above every quantile: lowers the bound
    q = float(np.quantile(t, level))
    return 2.0 * obs - q if np.isfinite(q) else -math.inf


def _bh(p: np.ndarray, alpha: float) -> np.ndarray:
    p = np.where(np.isfinite(p), p, 1.0)
    m = len(p)
    out = np.zeros(m, bool)
    if not m:
        return out
    order = np.argsort(p, kind="stable")
    ok = p[order] <= np.arange(1, m + 1) * alpha / m
    if ok.any():
        out[order[: int(np.flatnonzero(ok).max()) + 1]] = True
    return out


@dataclass(frozen=True, slots=True)
class BatchPRDS:
    window: Literal["test_old", "confirm"]
    q: float = Q_CONFIRM

    def level(self, j: int, n_j: int) -> float:
        """alpha_j = q gamma_j: pure alpha-spending, the same whatever n_j or earlier rejections (no credit)."""
        return self.q * gamma(j)

    def test(self, p: Sequence[float], *, j: int) -> np.ndarray:
        """BH at alpha_j over the batch's p-values (PRDS within the batch); NaN is never rejected."""
        arr = np.asarray(p, float)
        return _bh(arr, self.level(j, len(arr)))


def holm(p: Sequence[float], alpha: float) -> np.ndarray:
    """Holm step-down at `alpha`: the k-th smallest p is rejected while p_(i) <= alpha / (m - i + 1) for all i <= k."""
    arr = np.asarray(p, float)
    arr = np.where(np.isfinite(arr), arr, 1.0)
    m = len(arr)
    out = np.zeros(m, bool)
    for i, k in enumerate(np.argsort(arr, kind="stable")):
        if arr[k] > alpha / (m - i):
            break
        out[k] = True
    return out


@dataclass(frozen=True, slots=True)
class L3Result:
    family: str
    window: str
    batch_j: int
    alpha_j: float
    p_cv_net: float
    p_excess: float
    lb80_net_gate: float
    years: Mapping[str, bool]
    corroboration_p: float | None
    e9: bool | None
    rejected: bool
    cells_forwarded: tuple[str, ...]


def l3_read(units: pd.DataFrame, *, family: str, carried: Sequence[str], weights: Mapping[str, float],
            batch: BatchPRDS, j: int, batch_p: Sequence[float],
            corroboration: pd.DataFrame | None, mean_block: float, seed_key: str) -> L3Result:
    """The family's one L3 read on `batch.window` at batch `j` (module docstring for every leg). `units`: the window's
    instances (filtered here to `family` and its `carried` cells); `weights`: the frozen card's cell weights;
    `batch_p`: the p-values of every family read in batch j, this one's included."""
    m_j = len(batch_p)
    alpha_j = batch.level(j, m_j)
    resamples = confirm_resamples(m_j, alpha_j)
    cells = [str(c) for c in carried]
    w = {c: float(weights[c]) for c in cells}

    def mine(df: pd.DataFrame) -> pd.DataFrame:
        sel = (df["family"].astype(str) == family) & df["cell_id"].astype(str).isin(cells)
        return df[sel.to_numpy()].reset_index(drop=True)
    u = mine(units)

    def p_of(frame: pd.DataFrame, stat: StatFn, tag: str) -> float:
        return boot_p_one_sided(frame, stat, mean_block=mean_block, resamples=resamples, seed_key=f"{seed_key}|{tag}")

    def cv(weights_: Mapping[str, float]) -> StatFn:
        return lambda d: control_variate_net(d, weights_)
    p_cv = p_of(u, cv(w), "cv")
    bp = np.asarray(batch_p, float)
    own = np.flatnonzero(np.isclose(bp, p_cv, rtol=0.0, atol=1e-12))
    if not len(own):
        raise ValueError(f"batch_p must hold {family}'s own p_cv_net {p_cv:.6g} (the batch's p-values, this "
                         f"family's included); got {list(bp)}")
    bh_ok = bool(_bh(bp, alpha_j)[int(own[0])])
    p_ex = p_of(u, lambda d: weighted_mean_excess(d, w), "excess")
    lb = boot_lower_bound(u, lambda d: control_variate_net_at(d, w, cost_mult=GATE_COST_MULT), level=LB_LEVEL,
                          mean_block=mean_block, resamples=resamples, seed_key=f"{seed_key}|lb80")
    corr = mine(corroboration) if corroboration is not None else None
    corr_p = p_of(corr, cv(w), "corr") if corr is not None and len(corr) else None
    both = pd.concat([u, corr], ignore_index=True) if corr is not None and len(corr) else u
    years = year_rule(both, chop_years=CHOP_YEARS) if len(both) else {
        "years_pos_ge3": False, "chop_year_pos": False, "no_2y_t_le_m2": False}
    multi_day = bool(u["tf"].astype(str).isin(MULTI_DAY_TF).any()) if len(u) else False
    e9: bool | None = None
    rejected = bool(bh_ok and p_ex <= P_EXCESS_MAX and lb > 0 and all(years.values())
                    and corr_p is not None and corr_p <= P_CORROBORATION_MAX and not multi_day)
    forwarded: tuple[str, ...] = ()
    if rejected:
        cell_p = [p_of(u[(u["cell_id"].astype(str) == c).to_numpy()].reset_index(drop=True), cv({c: 1.0}), f"cell|{c}")
                  for c in cells]
        forwarded = tuple(c for c, ok in zip(cells, holm(cell_p, alpha_j)) if ok)
    return L3Result(family, batch.window, j, alpha_j, p_cv, p_ex, lb, years, corr_p, e9, rejected, forwarded)
