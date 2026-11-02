"""Amendment C1 (docs/research/rethink/audit.md, ADR 0069): the controls and pass rules every research screen applies
from 2026-11-02, beside the C1 cost (`cost_of_record.C1Costs`).

Control: "Null = random-SIDE twin at the same minute with the same stop/exit, or drift-adjusted zero (subtract side x
in-window drift x hold); pass needs excess >= 0. Same-side twins are an 'entry-timing' diagnostic only." The random-side
twin is taken in expectation (both sides walked from the same minute, the coin averaged exactly), so the control adds
no sampling noise: its excess is half the real-minus-opposite net.

Explore (LEARN 2010-17): "day-summed net R > 0, t >= 2.0, >= 5/8 years positive, drift-adjusted gross > 0, random-side
control excess >= 0, n >= 100 (60 for 4h/1d)". Ensemble: "per family an equal-weight composite of its explore passers +
members with gross t >= 2, frozen before confirm, one trial per family". Confirm: "BH-FDR q=0.10 over all families +
singles sent, net > 0, >= 5/8 years, n >= 100" on the one-sided day-block-bootstrap p of the confirm net R (W2).
TEST-OLD: "net > 0, t >= 1.28".
"""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord

DAY_NS = 86_400_000_000_000
EXPLORE_YEARS = tuple(range(2010, 2018))
CONFIRM_YEARS = tuple(range(2018, 2026))
EXPLORE_T = 2.0
TEST_OLD_T = 1.28
MIN_YEARS_POSITIVE = 5
N_MIN = 100
N_MIN_SLOW = 60                    # 4h and 1d decision rules
FDR_Q = 0.10
BOOT_RESAMPLES = 10_000


def window_drift_per_ns(p_first: float, p_last: float, t_first_ns: int, t_last_ns: int) -> float:
    """The window's log drift per ns: what holding gold (either side's sign) earned per unit time in the window."""
    if t_last_ns <= t_first_ns or p_first <= 0 or p_last <= 0:
        return 0.0
    return float(np.log(p_last / p_first) / (t_last_ns - t_first_ns))


def drift_adjusted_gross_r(gross_r: np.ndarray, side: np.ndarray, entry_px: np.ndarray, hold_ns: np.ndarray,
                           risk_usd: np.ndarray, drift_per_ns: float) -> np.ndarray:
    """Gross R less side x in-window drift x hold (in USD at the entry price, over the stop): the part of the gross a
    trader who only held the window's drift for the same time on the same side would not have earned."""
    drift_usd = np.asarray(entry_px, dtype=float) * np.expm1(drift_per_ns * np.asarray(hold_ns, dtype=float))
    return np.asarray(gross_r, dtype=float) - np.sign(np.asarray(side, dtype=float)) * drift_usd \
        / np.asarray(risk_usd, dtype=float)


def random_side_control(net_r: np.ndarray, net_r_opposite: np.ndarray) -> np.ndarray:
    """Per trade, the expected net R of its random-side twin (same minute, stop and exit; the side a fair coin): the
    mean of the real side's and the opposite side's net, each charged its own costs and swap."""
    return 0.5 * (np.asarray(net_r, dtype=float) + np.asarray(net_r_opposite, dtype=float))


def random_side_excess(net_r: np.ndarray, net_r_opposite: np.ndarray) -> np.ndarray:
    """Real net minus its random-side twin: the value of the rule's side call (C1 gate: mean >= 0)."""
    return np.asarray(net_r, dtype=float) - random_side_control(net_r, net_r_opposite)


def day_sums(r: np.ndarray, entry_ns: np.ndarray) -> np.ndarray:
    """R summed by UTC entry day, in day order (days without a trade are absent)."""
    days = np.asarray(entry_ns, dtype=np.int64) // DAY_NS
    _, inv = np.unique(days, return_inverse=True)
    return np.bincount(inv, weights=np.asarray(r, dtype=float))


def t_of(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    if len(x) < 2:
        return float("nan")
    sd = float(np.std(x, ddof=1))
    return float(np.mean(x) / (sd / np.sqrt(len(x)))) if sd > 0 else float("nan")


def day_t(r: np.ndarray, entry_ns: np.ndarray) -> float:
    """t of the day-summed series (trades on one day are not independent)."""
    return t_of(day_sums(r, entry_ns))


def by_year(r: np.ndarray, entry_ns: np.ndarray) -> dict[int, float]:
    """Mean R per trade by entry year."""
    years = pd.DatetimeIndex(pd.to_datetime(np.asarray(entry_ns, dtype=np.int64), utc=True)).year.to_numpy()
    rr = np.asarray(r, dtype=float)
    return {int(y): float(rr[years == y].mean()) for y in np.unique(years)}


def years_positive(r: np.ndarray, entry_ns: np.ndarray) -> int:
    return sum(1 for v in by_year(r, entry_ns).values() if v > 0)


class ExploreVerdict(FrozenRecord):
    """One rule's C1 explore read: every leg, the legs it failed, and the verdict."""
    n: int
    n_min: int
    net_r: float
    day_t: float
    years_positive: int
    gross_r: float
    gross_t: float
    drift_adjusted_gross_r: float
    random_side_excess_r: float
    failed: tuple[str, ...]
    passed: bool


def explore_verdict(*, net_r: np.ndarray, net_r_opposite: np.ndarray, gross_r: np.ndarray, side: np.ndarray,
                    entry_px: np.ndarray, risk_usd: np.ndarray, hold_ns: np.ndarray, entry_ns: np.ndarray,
                    drift_per_ns: float, n_min: int = N_MIN) -> ExploreVerdict:
    """The C1 explore rule on one rule's trades (module docstring)."""
    net = np.asarray(net_r, dtype=float)
    n = len(net)
    if n == 0:
        return ExploreVerdict(n=0, n_min=n_min, net_r=float("nan"), day_t=float("nan"), years_positive=0,
                              gross_r=float("nan"), gross_t=float("nan"), drift_adjusted_gross_r=float("nan"),
                              random_side_excess_r=float("nan"), failed=("n",), passed=False)
    t = day_t(net, entry_ns)
    yp = years_positive(net, entry_ns)
    adj = float(drift_adjusted_gross_r(gross_r, side, entry_px, hold_ns, risk_usd, drift_per_ns).mean())
    ex = float(random_side_excess(net, net_r_opposite).mean())
    legs = {"n": n >= n_min, "net": float(net.mean()) > 0, "day_t": bool(np.isfinite(t) and t >= EXPLORE_T),
            "years": yp >= MIN_YEARS_POSITIVE, "drift_adjusted_gross": adj > 0, "random_side": ex >= 0}
    failed = tuple(k for k, ok in legs.items() if not ok)
    return ExploreVerdict(n=n, n_min=n_min, net_r=float(net.mean()), day_t=t, years_positive=yp,
                          gross_r=float(np.mean(gross_r)), gross_t=t_of(np.asarray(gross_r, dtype=float)),
                          drift_adjusted_gross_r=adj, random_side_excess_r=ex, failed=failed, passed=not failed)


def composite_days(members: Sequence[tuple[np.ndarray, np.ndarray]]) -> tuple[np.ndarray, np.ndarray]:
    """An equal-weight composite of members (entry ns, R per trade): each member's R summed by day, the composite day
    the mean over ALL members (a member without a trade that day contributes 0). Returns (UTC day number, R)."""
    sums: list[pd.Series] = []
    for entry_ns, r in members:
        d = np.asarray(entry_ns, dtype=np.int64) // DAY_NS
        sums.append(pd.Series(np.asarray(r, dtype=float)).groupby(d).sum())
    if not sums:
        return np.zeros(0, dtype=np.int64), np.zeros(0)
    frame = pd.concat(sums, axis=1).fillna(0.0).sort_index()
    return frame.index.to_numpy(np.int64), frame.mean(axis=1).to_numpy(float) * len(sums) / len(members)


def day_bootstrap_p(day_r: np.ndarray, rng: np.random.Generator, resamples: int = BOOT_RESAMPLES) -> float:
    """One-sided p of mean > 0 from a bootstrap over whole days (W2: the confirm statistic BH-FDR reads)."""
    x = np.asarray(day_r, dtype=float)
    if len(x) < 2:
        return 1.0
    means = x[rng.integers(0, len(x), size=(resamples, len(x)))].mean(axis=1)
    return float((1 + np.sum(means <= 0.0)) / (resamples + 1))


def bh_fdr(p: np.ndarray, q: float = FDR_Q) -> np.ndarray:
    """Benjamini-Hochberg: which of the m hypotheses are rejected at false discovery rate q."""
    p = np.asarray(p, dtype=float)
    m = len(p)
    if m == 0:
        return np.zeros(0, dtype=bool)
    order = np.argsort(p, kind="stable")
    ok = p[order] <= q * np.arange(1, m + 1) / m
    k = int(np.flatnonzero(ok).max()) + 1 if ok.any() else 0
    out = np.zeros(m, dtype=bool)
    out[order[:k]] = True
    return out
