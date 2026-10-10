"""CUSUM decision interval tuned to the design's "5% quarterly false-alarm rate" (row M25).

Both CUSUM alarms (the drift watch's residual CUSUM, research/drift.py `residual_cusum`, and the new-champion watch,
research/promotion.py `cusum_alarm`) run a one-sided downward CUSUM on standardised per-trade values with allowance k:

    S_0 = 0,  S_t = max(0, S_{t-1} - z_t - k),  alarm when S_t > h.

A fixed h makes the false-alarm rate depend on how often the agent trades: an agent with 60 trades a quarter gets
many more chances to cross h by luck than one with 10. The design fixes the rate instead: h is chosen, for a given k,
so that on in-control values (iid standard normal: the model is right and the residuals are standardised) the
probability of ANY alarm within one quarter's expected trade count is `false_alarm` (5%). The count comes from the
backtest: round(trades_per_week x 13).

h is the (1 - false_alarm) quantile of max_t S_t over `N_SIMS` simulated in-control paths of that length (a fixed seed,
so h is reproducible), cached per (trades, k, rate). Without a trade rate (no backtest stats) the fixed
`FALLBACK_H` applies. Simulated, not from an ARL approximation (Siegmund), because a quarter is short: tens of trades,
where the stationary approximations are poor.

Limits, stated: the standardised residual of a barrier trade is two-point (win T or lose 1), not normal, so the realised
false-alarm rate of the drift watch differs somewhat from 5% (heavier lower tail for high-p trades); and the drift
watch's CUSUM runs over the whole life of a champion version, so 5% is the rate per quarter of trading, not per life.
"""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

QUARTER_WEEKS = 13.0
FALSE_ALARM_QUARTER = 0.05      # design: "tuned to a 5% quarterly false-alarm rate"
DEFAULT_K = 0.5                 # allowance in sd: half the 1-sd shift the alarm is meant to catch
FALLBACK_H = 4.0                # h when the expected trade rate is unknown (the previous fixed value)
N_SIMS = 100_000                # in-control paths per calibration (standard error of the rate ~0.07 pp at 5%)
SEED = 25                       # row M25: fixed so h is reproducible
MAX_TRADES = 5_000              # a quarter longer than this is capped (memory and time; no agent trades this often)


def trades_per_quarter(trades_per_week: float | None) -> int | None:
    """Expected trades in one quarter from the backtest's rate, or None when unknown or not positive."""
    if trades_per_week is None or not math.isfinite(trades_per_week) or trades_per_week <= 0:
        return None
    return int(min(max(round(trades_per_week * QUARTER_WEEKS), 1), MAX_TRADES))


def _simulate(n: int, k: float, n_sims: int, seed: int, shift: float = 0.0, h: float | None = None
              ) -> tuple[np.ndarray, np.ndarray]:
    """Downward CUSUM on n_sims paths of n iid N(shift, 1) values, drawn one trade at a time (memory O(n_sims)):
    (max_t S_t per path, trades until S_t first exceeds h per path, n + 1 where it never does or h is None)."""
    rng = np.random.default_rng(seed)
    s = np.zeros(n_sims)
    peak = np.zeros(n_sims)
    first = np.full(n_sims, n + 1)
    for t in range(n):
        s = np.maximum(0.0, s - (rng.standard_normal(n_sims) + shift) - k)
        np.maximum(peak, s, out=peak)
        if h is not None:
            first = np.where((first > n) & (s > h), t + 1, first)
    return peak, first


@lru_cache(maxsize=512)
def _h(n: int, k: float, false_alarm: float, n_sims: int, seed: int) -> float:
    return float(np.quantile(_simulate(n, k, n_sims, seed)[0], 1.0 - false_alarm))


def calibrated_h(trades_per_week: float | None, k: float = DEFAULT_K, false_alarm: float = FALSE_ALARM_QUARTER,
                 n_sims: int = N_SIMS, seed: int = SEED) -> float:
    """The decision interval h for allowance k at which in-control residuals alarm within one quarter's expected
    trades with probability `false_alarm`; FALLBACK_H without a usable trade rate. Cached."""
    n = trades_per_quarter(trades_per_week)
    if n is None:
        return FALLBACK_H
    return _h(n, round(float(k), 6), round(float(false_alarm), 6), int(n_sims), int(seed))


def alarm_rate(h: float, k: float, n: int, shift: float = 0.0, n_sims: int = N_SIMS, seed: int = SEED + 1) -> float:
    """Share of simulated paths of n standardised values with mean `shift` that alarm (S_t > h) at least once. With
    shift 0 this is the false-alarm rate; with shift -1 the probability of catching a 1-sd drop within n trades."""
    return float(np.mean(_simulate(n, k, n_sims, seed, shift)[0] > h))


def run_lengths(h: float, k: float, n: int, shift: float, n_sims: int = 10_000, seed: int = SEED + 2) -> np.ndarray:
    """Trades until the first alarm on each simulated path with mean `shift` (n + 1 where none within n)."""
    return _simulate(n, k, n_sims, seed, shift, h)[1]
