"""Champion/challenger promotion gates (design: Retraining and promotion).

A retrained model is a challenger that shadow-trades at live prices for at least four weeks AND 40 trades
(whichever comes later). It is promoted only when:
* shadow Sharpe is above 0.8 annualised and no more than 0.5 below its backtest Sharpe,
* shadow hit rate is within one binomial standard error of the backtest's expected hit rate,
* shadow max drawdown is under 1.5x the backtest's,
* turnover is within 30% of the current champion's (skipped when there is no champion yet).
Specialist promotion is automatic when every gate passes; allocator, rule and label changes need the owner.
"""
from __future__ import annotations

import math

from pydantic import Field

from goldbot.base import FrozenRecord
from goldbot.research.cusum import DEFAULT_K, FALSE_ALARM_QUARTER, WATCH_WEEKS, calibrated_h

MIN_SHADOW_WEEKS = 4.0
MIN_SHADOW_TRADES = 40
MIN_SHADOW_SHARPE = 0.8
MAX_SHARPE_SHORTFALL = 0.5
MAX_DD_RATIO = 1.5
MAX_TURNOVER_CHANGE = 0.30


class PerfStats(FrozenRecord):
    n_trades: int = Field(ge=0)
    sharpe_ann: float
    hit_rate: float = Field(ge=0, le=1)
    max_dd: float = Field(ge=0)
    trades_per_week: float = Field(ge=0)
    weeks: float = Field(0.0, ge=0)
    mean_ret: float | None = None      # per-trade return moments, used by the CUSUM alarm
    std_ret: float | None = None


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
    enough = shadow.weeks >= MIN_SHADOW_WEEKS and shadow.n_trades >= MIN_SHADOW_TRADES
    checks = [GateCheck(name="shadow_length", passed=enough,
                        detail=f"{shadow.weeks:.1f} weeks / {shadow.n_trades} trades (need {MIN_SHADOW_WEEKS:.0f} and {MIN_SHADOW_TRADES})")]
    if not enough:
        return PromotionDecision(promote=False, ready=False, checks=checks)
    checks.append(GateCheck(name="sharpe_floor", passed=shadow.sharpe_ann > MIN_SHADOW_SHARPE,
                            detail=f"shadow Sharpe {shadow.sharpe_ann:.2f} vs floor {MIN_SHADOW_SHARPE}"))
    checks.append(GateCheck(name="sharpe_vs_backtest", passed=shadow.sharpe_ann >= backtest.sharpe_ann - MAX_SHARPE_SHORTFALL,
                            detail=f"shadow {shadow.sharpe_ann:.2f} vs backtest {backtest.sharpe_ann:.2f} (max shortfall {MAX_SHARPE_SHORTFALL})"))
    p = backtest.hit_rate
    se = math.sqrt(max(p * (1 - p), 1e-12) / shadow.n_trades)
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
                weeks: float = WATCH_WEEKS) -> bool:
    """One-sided CUSUM on standardised trade returns for a downward shift from the backtest's mean (design: a new
    champion that trips the alarm in its first two weeks is replaced by the previous one). k is the allowance and
    h the decision interval, both in standard deviations. Row M25: unless `h` is given, h is tuned so that returns
    at the backtest's mean alarm within the watch's expected trades (backtest `trades_per_week` x `weeks`, two weeks)
    with probability at most `false_alarm` (5%; research/cusum.py). In control a trade wins with the backtest's hit
    rate `p`, so its standardised return is two-point and h is simulated that way (normal when p is None); without a
    trade rate the fixed FALLBACK_H applies."""
    if expected_std <= 0 or not returns:
        return False
    h = calibrated_h(trades_per_week, k, false_alarm, p=p, weeks=weeks) if h is None else h
    s = 0.0
    for r in returns:
        s = max(0.0, s + (expected_mean - r) / expected_std - k)
        if s > h:
            return True
    return False
