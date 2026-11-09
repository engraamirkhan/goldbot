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
- `batch_p` must hold this family's own p (the batch is the families read in batch j, fixed before the read); BH over
  `batch_p` decides the family. The batch runner computes every family's p with `family_cv_p`, the function
  `l3_read` uses, so the two agree to the bit.

Amendment 2026-11-09 (prereg section 10, quant-reviewer and research director): "`l3_read` gains a required
keyword-only argument `confirm_seen`" (A15: F = carried minus confirm_seen reads the corroboration AND, S = carried
and confirm_seen reads the no-credit AND; the year rule reads TEST-OLD plus the 2018-25 units of every carried cell; a
confirm-window read never carries a CONFIRM-SEEN cell); "`holds_multi_day: bool` and `e9: bool | None`" (A16: a
multi-day family is rejected only with e9 True; a cell with no registered time exit counts as holding >= 24 h,
`card_holds_multi_day`); "`L3Result` gains `cv_net`, `cv_net_lo80` and `cv_net_hi80`" (A17: the 1.0x point and its
two-sided 80 % basic interval, seed tag `|ci80`).

Windows (quant M5): nothing at or after 2025-10-01 (holdout); a TEST-OLD read takes intraday (<= 4h) units in
2008-01..2009-12 and 1d/1w units in 2006-02..2009-12 (adding 2006-07 intraday back after the refill and DQ re-check
needs an amendment that passes the card's record); a confirm read and every corroboration take 2018-01..2025-09; the
read and its corroboration never overlap.

Speed (code review): the three L3 statistics (control-variate net, mean excess, the 1.5x gate) are linear in
per-session-day sums, so `_fast_stats` draws the day sequence once per resample (the same draws as
`stationary_block_bootstrap` on the same seed) and evaluates all three as day-count vectors times per-day sums.
"""
from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

import numpy as np
import pandas as pd

from goldbot.research.catalogue.costs import GATE_COST_MULT
from goldbot.research.catalogue.data import DAY, TF_NS
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
CI_LEVEL: Final = 0.80                         # A17: the two-sided interval of the 1.0x point
MULTI_DAY_TF: Final = ("1d", "1w")             # holds >= 1 day by timeframe: E9 required
CHUNK: Final = 2_048                           # resamples per day-count matrix
HOLDOUT_FROM_DAY: Final = 20_362               # 2025-10-01
TEST_OLD_INTRADAY: Final = (13_879, 14_610)    # [2008-01-01, 2010-01-01)
TEST_OLD_DAILY: Final = (13_180, 14_610)       # [2006-02-01, 2010-01-01)
CONFIRM_SPAN: Final = (17_532, 20_362)         # [2018-01-01, 2025-10-01)

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
        self.day_of_row = np.unique(day, return_inverse=True)[1].reshape(-1) if len(day) else np.zeros(0, np.int64)

    def days(self, rng: np.random.Generator, mean_block: float) -> np.ndarray:
        """Politis-Romano stationary bootstrap over the day sequence (circular): a new block starts with probability
        1 / mean_block, else the next day follows. As many days as the window holds."""
        n = self.n
        new = rng.random(n) < 1.0 / mean_block
        new[0] = True
        starts = rng.integers(0, n, n)
        seg = np.cumsum(new) - 1
        first = np.flatnonzero(new)
        return np.asarray((starts[first][seg] + np.arange(n) - first[seg]) % n)

    def rows(self, rng: np.random.Generator, mean_block: float) -> np.ndarray:
        """The rows of a resample: every instance of each drawn day, in draw order."""
        days = self.days(rng, mean_block)
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


def _day_count_chunks(blocks: _DayBlocks, *, mean_block: float, resamples: int, seed_key: str
                      ) -> Iterator[np.ndarray]:
    """The resamples as day-count vectors (how often each session day is drawn), CHUNK at a time, from the same
    random stream `stationary_block_bootstrap` uses for the same seed."""
    rng = _rng(seed_key)
    done = 0
    while done < resamples:
        b = min(CHUNK, resamples - done)
        idx = np.concatenate([r * blocks.n + blocks.days(rng, mean_block) for r in range(b)])
        yield np.bincount(idx, minlength=b * blocks.n).reshape(b, blocks.n).astype(float)
        done += b


def _fast_stats(units: pd.DataFrame, weights: Mapping[str, float], *, mean_block: float, resamples: int,
                seed_key: str) -> dict[str, np.ndarray]:
    """The resampled control-variate net at 1.0x ("cv"), weighted mean excess ("excess") and control-variate net at
    the 1.5x gate ("gate"), from per-session-day sums over the carried cells' instances with an excess: identical to
    `stationary_block_bootstrap` with `stats.control_variate_net` / `weighted_mean_excess` / `control_variate_net_at`
    on the same seed, at ~1/50 of the cost."""
    if mean_block < 1.0:
        raise ValueError(f"mean block must be >= 1 day, got {mean_block}")
    blocks = _DayBlocks(units)
    out = {k: np.full(resamples, np.nan) for k in ("cv", "excess", "gate")}
    if not blocks.n:
        return out
    codes, uniq = pd.factorize(units["cell_id"], sort=True)
    w_u = np.array([float(weights.get(str(c), 0.0)) for c in uniq])
    ex = units["excess_r"].to_numpy(float)
    keep = (codes >= 0) & np.isfinite(ex)
    keep[keep] = w_u[codes[keep]] > 0
    n_c = len(uniq)
    day, cell = blocks.day_of_row[keep], codes[keep]
    flat = day * n_c + cell
    size = blocks.n * n_c
    sx = np.bincount(flat, weights=ex[keep], minlength=size).reshape(blocks.n, n_c)
    nn = np.bincount(flat, minlength=size).reshape(blocks.n, n_c).astype(float)
    cs = np.bincount(flat, weights=units["cost_r"].to_numpy(float)[keep], minlength=size).reshape(blocks.n, n_c)
    tw = np.bincount(day, weights=units["twin_mean_net_r"].to_numpy(float)[keep], minlength=blocks.n)
    kk = np.bincount(day, minlength=blocks.n).astype(float)
    at = 0
    with np.errstate(invalid="ignore", divide="ignore"):
        for cnt in _day_count_chunks(blocks, mean_block=mean_block, resamples=resamples, seed_key=seed_key):
            b = len(cnt)
            n_r = cnt @ nn
            present = n_r > 0
            w_eff = np.where(present, w_u, 0.0)
            wsum = w_eff.sum(axis=1)
            mex = np.where(present, (cnt @ sx) / n_r, 0.0)
            mco = np.where(present, (cnt @ cs) / n_r, 0.0)
            twin = (cnt @ tw) / (cnt @ kk)
            exc = np.where(wsum > 0, (w_eff * mex).sum(axis=1) / wsum, np.nan)
            gate_ex = np.where(wsum > 0, (w_eff * (mex - (GATE_COST_MULT - 1.0) * mco)).sum(axis=1) / wsum, np.nan)
            out["excess"][at:at + b] = exc
            out["cv"][at:at + b] = exc + twin
            out["gate"][at:at + b] = gate_ex + twin
            at += b
    return out


def _p_from(obs: float, t: np.ndarray) -> float:
    """The basic (centred) one-sided p, as `boot_p_one_sided`."""
    if not np.isfinite(obs):
        return 1.0
    hits = int(np.count_nonzero(~np.isfinite(t) | (t - obs >= obs)))
    return (1 + hits) / (len(t) + 1)


def _basic_quantile_bound(obs: float, t: np.ndarray, level: float) -> float:
    if not np.isfinite(obs):
        return -math.inf
    q = float(np.quantile(np.where(np.isfinite(t), t, math.inf), level))
    return 2.0 * obs - q if np.isfinite(q) else -math.inf


def _family_units(df: pd.DataFrame, family: str, cells: Collection[str]) -> pd.DataFrame:
    sel = (df["family"].astype(str) == family).to_numpy() & df["cell_id"].astype(str).isin(list(cells)).to_numpy()
    return df[sel].reset_index(drop=True)


def family_cv_p(units: pd.DataFrame, *, family: str, carried: Sequence[str], weights: Mapping[str, float], m_j: int,
                alpha_j: float, mean_block: float, seed_key: str) -> float:
    """The family's one-sided p of the control-variate net exactly as `l3_read` computes it (its carried cells,
    confirm_resamples(m_j, alpha_j) resamples, seed tag `|cv`): the batch runner writes every family's p of batch j
    with this before any `l3_read`."""
    cells = [str(c) for c in carried]
    w = {c: float(weights[c]) for c in cells}
    u = _family_units(units, family, cells)
    t = _fast_stats(u, w, mean_block=mean_block, resamples=confirm_resamples(m_j, alpha_j),
                    seed_key=f"{seed_key}|cv")["cv"]
    return _p_from(control_variate_net(u, w), t)


def card_holds_multi_day(cells: Mapping[str, tuple[str, int | None]]) -> bool:
    """A16 with RD condition 1: `holds_multi_day` from the frozen card (cell -> (tf, registered time exit in bars or
    None)): true if any carried cell is 1d/1w, has a registered maximum hold (time exit x bar length) >= 24 h, or has
    no registered time exit (an unbounded hold counts as >= 24 h). Never from realised holds."""
    for tf, bars in cells.values():
        if tf in MULTI_DAY_TF or bars is None or int(bars) * TF_NS[tf] >= DAY:
            return True
    return False


def _days_of(df: pd.DataFrame) -> np.ndarray:
    return np.asarray(df["decision_ns"].to_numpy(np.int64) // DAY)


def _check_windows(u: pd.DataFrame, corr: pd.DataFrame | None, window: str) -> None:
    """Quant M5 and the code review's disjoint windows (module docstring)."""
    for name, df in (("units", u), ("corroboration", corr)):
        if df is not None and len(df) and (_days_of(df) >= HOLDOUT_FROM_DAY).any():
            raise ValueError(f"{name} reach the holdout (decision at or after 2025-10-01): never read here")
    if len(u):
        d = _days_of(u)
        if window == "test_old":
            daily = u["tf"].astype(str).isin(MULTI_DAY_TF).to_numpy()
            lo = np.where(daily, TEST_OLD_DAILY[0], TEST_OLD_INTRADAY[0])
            if ((d < lo) | (d >= TEST_OLD_INTRADAY[1])).any():
                raise ValueError("TEST-OLD units outside the window: intraday 2008-01..2009-12, 1d/1w 2006-02..2009-12")
        elif ((d < CONFIRM_SPAN[0]) | (d >= CONFIRM_SPAN[1])).any():
            raise ValueError("confirm-window units outside 2018-01..2025-09")
    if corr is not None and len(corr):
        c = _days_of(corr)
        if ((c < CONFIRM_SPAN[0]) | (c >= CONFIRM_SPAN[1])).any():
            raise ValueError("corroboration units outside 2018-01..2025-09")
        if len(u) and not (c.min() > _days_of(u).max() or c.max() < _days_of(u).min()):
            raise ValueError("the read's units and its corroboration must be disjoint windows")


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
    corroboration_seen_p: float | None          # A15: the S cells' 2018-25 p (an AND with no credit)
    cv_net: float                               # A17: control-variate net at 1.0x on the read window
    cv_net_lo80: float                          # A17: two-sided 80 % basic-bootstrap interval of cv_net
    cv_net_hi80: float


def l3_read(units: pd.DataFrame, *, family: str, carried: Sequence[str], weights: Mapping[str, float],
            batch: BatchPRDS, j: int, batch_p: Sequence[float],
            corroboration: pd.DataFrame | None, mean_block: float, seed_key: str,
            confirm_seen: Collection[str], holds_multi_day: bool, e9: bool | None) -> L3Result:
    """The family's one L3 read on `batch.window` at batch `j` (module docstring for every leg and the amendment).
    `units`: the window's instances (filtered here to `family` and its `carried` cells); `weights`: the frozen card's
    cell weights; `batch_p`: the p of every family read in batch j, this one's included (`family_cv_p`);
    `corroboration`: the family's 2018-25 units; `confirm_seen`: the card's CONFIRM-SEEN cells for 2018-25;
    `holds_multi_day` (`card_holds_multi_day`) and `e9` (the caller's EVAL-1 C1 drift guard on 2018-25 closes)."""
    m_j = len(batch_p)
    alpha_j = batch.level(j, m_j)
    resamples = confirm_resamples(m_j, alpha_j)
    cells = [str(c) for c in carried]
    w = {c: float(weights[c]) for c in cells}
    seen = {str(c) for c in confirm_seen}
    fresh = [c for c in cells if c not in seen]
    seen_cells = [c for c in cells if c in seen]
    if batch.window == "confirm" and seen_cells:
        raise ValueError(f"a confirm-window read never carries a CONFIRM-SEEN cell: {seen_cells} (amendment 10.1(g))")
    u = _family_units(units, family, cells)
    corr = _family_units(corroboration, family, cells) if corroboration is not None else None
    _check_windows(u, corr, batch.window)
    if len(u) and u["tf"].astype(str).isin(MULTI_DAY_TF).any() and not holds_multi_day:
        raise ValueError("a 1d/1w carried cell holds >= 1 day: holds_multi_day must be true (amendment 10.2)")

    draws = _fast_stats(u, w, mean_block=mean_block, resamples=resamples, seed_key=f"{seed_key}|cv")
    cv = control_variate_net(u, w)
    p_cv = _p_from(cv, draws["cv"])
    bp = np.asarray(batch_p, float)
    own = np.flatnonzero(np.isclose(bp, p_cv, rtol=0.0, atol=1e-12))
    if not len(own):
        raise ValueError(f"batch_p must hold {family}'s own p_cv_net {p_cv:.6g} (the batch's p-values, this "
                         f"family's included; compute it with family_cv_p); got {list(bp)}")
    bh_ok = bool(_bh(bp, alpha_j)[int(own[0])])
    p_ex = _p_from(weighted_mean_excess(u, w), draws["excess"])
    lb = _basic_quantile_bound(control_variate_net_at(u, w, cost_mult=GATE_COST_MULT), draws["gate"], LB_LEVEL)
    ci = _fast_stats(u, w, mean_block=mean_block, resamples=resamples, seed_key=f"{seed_key}|ci80")["cv"]
    lo80 = _basic_quantile_bound(cv, ci, 0.5 + CI_LEVEL / 2)
    hi80 = _basic_quantile_bound(cv, ci, 0.5 - CI_LEVEL / 2)

    def corr_p(group: list[str], tag: str) -> float | None:
        if not group or corr is None:
            return None
        sub = corr[corr["cell_id"].astype(str).isin(group).to_numpy()].reset_index(drop=True)
        if not len(sub):
            return None
        wg = {c: w[c] for c in group}
        t = _fast_stats(sub, wg, mean_block=mean_block, resamples=resamples, seed_key=f"{seed_key}|{tag}")["cv"]
        return _p_from(control_variate_net(sub, wg), t)
    p_fresh = corr_p(fresh, "corr")
    p_seen = corr_p(seen_cells, "corr_seen")
    both = pd.concat([u, corr], ignore_index=True) if corr is not None and len(corr) else u
    years = year_rule(both, chop_years=CHOP_YEARS) if len(both) else {
        "years_pos_ge3": False, "chop_year_pos": False, "no_2y_t_le_m2": False}
    rejected = bool(bh_ok and p_ex <= P_EXCESS_MAX and lb > 0 and all(years.values())
                    and (not fresh or (p_fresh is not None and p_fresh <= P_CORROBORATION_MAX))
                    and (not seen_cells or (p_seen is not None and p_seen <= P_CORROBORATION_MAX))
                    and (not holds_multi_day or e9 is True))
    forwarded: tuple[str, ...] = ()
    if rejected:
        cell_p = []
        for c in cells:
            uc = u[(u["cell_id"].astype(str) == c).to_numpy()].reset_index(drop=True)
            t = _fast_stats(uc, {c: 1.0}, mean_block=mean_block, resamples=resamples,
                            seed_key=f"{seed_key}|cell|{c}")["cv"]
            cell_p.append(_p_from(control_variate_net(uc, {c: 1.0}), t))
        forwarded = tuple(c for c, ok in zip(cells, holm(cell_p, alpha_j)) if ok)
    return L3Result(family, batch.window, j, alpha_j, p_cv, p_ex, lb, years, p_fresh, e9, rejected, forwarded,
                    p_seen, cv, lo80, hi80)
