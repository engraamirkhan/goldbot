"""Champion/challenger promotion gates (design: Retraining and promotion).

A retrained model is a challenger that shadow-trades at live prices for at least four weeks AND 40 trades
(whichever comes later). It is promoted only when:
* shadow Sharpe is above 0.8 annualised and no more than 0.5 below its backtest Sharpe,
* shadow hit rate is within one binomial standard error of the backtest's expected hit rate,
* shadow max drawdown is under 1.5x the backtest's,
* turnover is within 30% of the current champion's (skipped when there is no champion yet).
Specialist promotion is automatic when every gate passes; allocator, rule and label changes need the owner.

Clustered effective N (research director ruling 2026-10-19): the shadow book holds up to 3 concurrent trades per agent,
so same-day trades share one move and carry less evidence than their count. The 40 counts INDEPENDENT CLUSTERS, one
per agent per session day (`timeutil.opportunity_day` of the entry: [D-1 22:00, D 22:00) UTC), not raw trades; the
shadow Sharpe is computed on the cluster sums (annualised by clusters a year), the hit rate's binomial SE uses the
cluster count, and `t_stat` is the cluster-robust t of the mean trade return. The 4-week minimum is unchanged.
"""
from __future__ import annotations

import math
from collections.abc import Hashable, Sequence

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord
from goldbot.research.cusum import DEFAULT_K, FALSE_ALARM_QUARTER, WATCH_WEEKS, calibrated_h

MIN_SHADOW_WEEKS = 4.0
MIN_SHADOW_TRADES = 40
MIN_SHADOW_SHARPE = 0.8
MAX_SHARPE_SHORTFALL = 0.5
MAX_DD_RATIO = 1.5
MAX_TURNOVER_CHANGE = 0.30

# H-52 (docs/research/prereg-2026-10-19-live-review.md 1.1, rollout-plan-2026-10-19.md action 5a): until the engine's
# history-gap fix was deployed on the brain (2026-10-19 13:49 UTC) the live decision frames had a two-week hole after
# every restart, so a shadow trade OPENED (entry_ts) before this instant is quarantined as `history_gap`: it stays in
# the book (no survivorship) but is never clustered, scored or counted as promotion evidence. Dated, never moved
# forward to admit trades; a later fault gets its own dated marker.
EVIDENCE_START_UTC = pd.Timestamp("2026-10-19 13:49", tz="UTC")
QUARANTINE_HISTORY_GAP = "history_gap"


def quarantine_reason(entry_ts: pd.Timestamp, evidence_start: pd.Timestamp | None = EVIDENCE_START_UTC) -> str | None:
    """`history_gap` for a trade opened before `evidence_start`, else None (None evidence_start: nothing quarantined)."""
    if evidence_start is None:
        return None
    return QUARANTINE_HISTORY_GAP if pd.Timestamp(entry_ts) < evidence_start else None


class PerfStats(FrozenRecord):
    n_trades: int = Field(ge=0)
    sharpe_ann: float
    hit_rate: float = Field(ge=0, le=1)
    max_dd: float = Field(ge=0)
    trades_per_week: float = Field(ge=0)
    weeks: float = Field(0.0, ge=0)
    mean_ret: float | None = None      # per-trade return moments, used by the CUSUM alarm
    std_ret: float | None = None
    # ruling 2026-10-19: independent clusters (agent x session day) and the cluster-robust t of the mean trade return.
    # None: a record written before clustering (a shadow record without it is never ready: fail closed)
    n_clusters: int | None = Field(None, ge=0)
    t_stat: float | None = None


def effective_n(stats: PerfStats) -> int:
    """The shadow sample size the gates read: independent clusters; 0 for a record without them (fail closed)."""
    return stats.n_clusters if stats.n_clusters is not None else 0


def cluster_sums(ret: Sequence[float], clusters: Sequence[Hashable]) -> np.ndarray:
    """One summed return per cluster, in first-seen order."""
    order: dict[Hashable, int] = {}
    for c in clusters:
        order.setdefault(c, len(order))
    out = np.zeros(len(order))
    for r, c in zip(ret, clusters, strict=True):
        out[order[c]] += float(r)
    return out


def cluster_t_stat(ret: Sequence[float], clusters: Sequence[Hashable]) -> float | None:
    """Cluster-robust (CR1) t of the mean trade return: var(mean) = G/(G-1) x sum over clusters of the squared summed
    residuals / N^2. None below two clusters or with no dispersion."""
    r = np.asarray(ret, dtype=float)
    n = len(r)
    if n == 0:
        return None
    m = float(r.mean())
    e = cluster_sums((r - m).tolist(), clusters)
    g = len(e)
    if g < 2:
        return None
    var = g / (g - 1) * float(np.sum(e ** 2)) / n ** 2
    return m / math.sqrt(var) if var > 0 else None


class GateCheck(FrozenRecord):
    name: str
    passed: bool
    detail: str


class PromotionDecision(FrozenRecord):
    promote: bool
    ready: bool                 # enough shadow history to judge at all
    checks: list[GateCheck]

    @property
    def failed(self) -> list[str]:
        return [c.name for c in self.checks if not c.passed]


def evaluate_promotion(backtest: PerfStats, shadow: PerfStats, champion: PerfStats | None = None) -> PromotionDecision:
    n_eff = effective_n(shadow)
    enough = shadow.weeks >= MIN_SHADOW_WEEKS and n_eff >= MIN_SHADOW_TRADES
    checks = [GateCheck(name="shadow_length", passed=enough,
                        detail=f"{shadow.weeks:.1f} weeks / {n_eff} independent clusters ({shadow.n_trades} trades) "
                               f"(need {MIN_SHADOW_WEEKS:.0f} and {MIN_SHADOW_TRADES} clusters)")]
    if not enough:
        return PromotionDecision(promote=False, ready=False, checks=checks)
    checks.append(GateCheck(name="sharpe_floor", passed=shadow.sharpe_ann > MIN_SHADOW_SHARPE,
                            detail=f"shadow Sharpe {shadow.sharpe_ann:.2f} vs floor {MIN_SHADOW_SHARPE}"))
    checks.append(GateCheck(name="sharpe_vs_backtest", passed=shadow.sharpe_ann >= backtest.sharpe_ann - MAX_SHARPE_SHORTFALL,
                            detail=f"shadow {shadow.sharpe_ann:.2f} vs backtest {backtest.sharpe_ann:.2f} (max shortfall {MAX_SHARPE_SHORTFALL})"))
    p = backtest.hit_rate
    se = math.sqrt(max(p * (1 - p), 1e-12) / n_eff)          # effective N: the cluster count
    checks.append(GateCheck(name="hit_rate", passed=abs(shadow.hit_rate - p) <= se,
                            detail=f"shadow {shadow.hit_rate:.3f} vs expected {p:.3f} ± {se:.3f} (1 binomial SE)"))
    dd_cap = MAX_DD_RATIO * backtest.max_dd
    checks.append(GateCheck(name="drawdown", passed=shadow.max_dd < dd_cap,
                            detail=f"shadow max DD {shadow.max_dd:.3f} vs cap {dd_cap:.3f}"))
    if champion is not None and champion.trades_per_week > 0:
        change = abs(shadow.trades_per_week - champion.trades_per_week) / champion.trades_per_week
        checks.append(GateCheck(name="turnover", passed=change <= MAX_TURNOVER_CHANGE,
                                detail=f"{shadow.trades_per_week:.1f}/week vs champion {champion.trades_per_week:.1f} ({change:.0%} change)"))
    return PromotionDecision(promote=all(c.passed for c in checks), ready=True, checks=checks)


def cusum_alarm(returns: list[float], expected_mean: float, expected_std: float, k: float = DEFAULT_K,
                h: float | None = None, trades_per_week: float | None = None,
                false_alarm: float = FALSE_ALARM_QUARTER, p: float | None = None,
                weeks: float = WATCH_WEEKS, trades: int | None = None) -> bool:
    """One-sided CUSUM on standardised trade returns for a downward shift from the backtest's mean (design: a new
    champion that trips the alarm in its first two weeks is replaced by the previous one). k is the allowance and
    h the decision interval, both in standard deviations. Row M25: unless `h` is given, h is tuned so that returns
    at the backtest's mean alarm within the watch's expected trades (backtest `trades_per_week` x `weeks`, two weeks)
    with probability at most `false_alarm` (5%; research/cusum.py). In control a trade wins with the backtest's hit
    rate `p`, so its standardised return is two-point and h is simulated that way (normal when p is None); without a
    trade rate the fixed FALLBACK_H applies. `trades`: calibrate for exactly this many trades (the watch's
    `cusum.watch_trades`) instead of `weeks` of the trade rate."""
    if expected_std <= 0 or not returns:
        return False
    h = calibrated_h(trades_per_week, k, false_alarm, p=p, weeks=weeks, trades=trades) if h is None else h
    s = 0.0
    for r in returns:
        s = max(0.0, s + (expected_mean - r) / expected_std - k)
        if s > h:
            return True
    return False
