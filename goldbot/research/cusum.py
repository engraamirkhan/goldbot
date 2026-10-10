"""CUSUM decision interval tuned to the design's "5% quarterly false-alarm rate" (row M25).

Both CUSUM alarms (the drift watch's residual CUSUM, research/drift.py `residual_cusum`, and the new-champion watch,
research/promotion.py `cusum_alarm`) run a one-sided downward CUSUM on standardised per-trade values with allowance k:

    S_0 = 0,  S_t = max(0, S_{t-1} - z_t - k),  alarm when S_t > h.

A fixed h makes the false-alarm rate depend on how often the agent trades: an agent with 60 trades a quarter gets
many more chances to cross h by luck than one with 10. The design fixes the rate instead: h is chosen, for a given k,
so that on in-control values the probability of ANY alarm within one window's expected trade count is at most
`false_alarm` (5%). The count comes from the backtest: round(trades_per_week x weeks), with weeks = 13 for the
quarterly drift watch and 2 for the new-champion watch (its window is the champion's first two weeks).

In-control values are Bernoulli, not normal. A barrier trade either wins (probability p, the model's) or loses, so its
standardised residual is two-point whatever the target distance: win +sqrt((1-p)/p), loss -sqrt(p/(1-p)). Calibrating
on normal values made h far too high for such residuals (at p 0.4 the realised quarterly false-alarm rate was 0.2-0.7%,
7-25x too conservative, and real decay was caught late). So h is calibrated on simulated two-point residuals at the
agent's mean taken p, rounded to a 0.01 bucket (the drift watch: mean p of the shadow trades in the CUSUM; the
champion watch: the backtest hit rate, the win probability its standardised returns have in control). Normal values
remain only when p is unknown.

Because the residuals are discrete, max_t S_t is discrete and the false-alarm rate is a step function of h: h is the
smallest attainable value of max_t S_t whose false-alarm rate is at most `false_alarm` with 95% confidence over the
`N_SIMS` simulated paths (the most sensitive h that still meets the design's rate, i.e. the largest attainable rate
<= 5%; the confidence margin, ~0.1 pp, keeps fresh paths under 5% despite simulation noise). A fixed seed makes h
reproducible; it is cached per (trades, k, rate, p bucket). Without a trade rate (no backtest stats) the fixed
`FALLBACK_H` applies. Simulated, not from an ARL approximation (Siegmund), because a window is short: tens of trades,
where the stationary approximations are poor.

Limit, stated: the drift watch's CUSUM runs over the whole life of a champion version, so 5% is the rate per quarter
of trading, not per life; and one p bucket stands in for the spread of p across trades (each residual is still
standardised, so the spread changes only the shape of the two-point mixture, not its mean or variance).
"""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

QUARTER_WEEKS = 13.0
WATCH_WEEKS = 2.0               # the new-champion watch: "trips the alarm in its first two weeks"
FALSE_ALARM_QUARTER = 0.05      # design: "tuned to a 5% quarterly false-alarm rate"
DEFAULT_K = 0.5                 # allowance in sd: half the 1-sd shift the alarm is meant to catch
FALLBACK_H = 4.0                # h when the expected trade rate is unknown (the previous fixed value)
N_SIMS = 100_000                # in-control paths per calibration (standard error of the rate ~0.07 pp at 5%)
CONFIDENCE_Z = 1.645            # one-sided 95%: the chosen h's simulated rate plus this many SEs is <= false_alarm
SEED = 25                       # row M25: fixed so h is reproducible
MAX_TRADES = 5_000              # a window longer than this is capped (memory and time; no agent trades this often)
P_BUCKET = 0.01                 # the mean taken p is rounded to this before calibrating (and caching)
ROUND = 9                       # decimals: sums of lattice steps that are equal in exact arithmetic compare equal


def trades_per_window(trades_per_week: float | None, weeks: float = QUARTER_WEEKS) -> int | None:
    """Expected trades in a window of `weeks` from the backtest's rate, or None when unknown or not positive."""
    if trades_per_week is None or not math.isfinite(trades_per_week) or trades_per_week <= 0:
        return None
    return int(min(max(round(trades_per_week * weeks), 1), MAX_TRADES))


def trades_per_quarter(trades_per_week: float | None) -> int | None:
    """Expected trades in one quarter from the backtest's rate, or None when unknown or not positive."""
    return trades_per_window(trades_per_week, QUARTER_WEEKS)


def p_bucket(p: float | None) -> float | None:
    """The win probability rounded to P_BUCKET and kept inside (0, 1); None when unknown or not finite."""
    if p is None or not math.isfinite(p):
        return None
    return round(min(max(round(p / P_BUCKET) * P_BUCKET, P_BUCKET), 1 - P_BUCKET), 6)


def residual_values(p: float) -> tuple[float, float]:
    """The two standardised residuals of a barrier trade the model gave probability p: (win, loss)."""
    return math.sqrt((1 - p) / p), -math.sqrt(p / (1 - p))


def _simulate(n: int, k: float, n_sims: int, seed: int, shift: float = 0.0, h: float | None = None,
              p: float | None = None, p_true: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Downward CUSUM on n_sims paths of n iid standardised values, drawn one trade at a time (memory O(n_sims)):
    (max_t S_t per path, trades until S_t first exceeds h per path, n + 1 where it never does or h is None).

    With p None the values are N(shift, 1). With p they are the two-point trade residuals standardised at p
    (`residual_values`), the win drawn with probability `p_true` (default p: in control; lower: the model's edge has
    decayed), plus `shift`."""
    rng = np.random.default_rng(seed)
    s = np.zeros(n_sims)
    peak = np.zeros(n_sims)
    first = np.full(n_sims, n + 1)
    win, loss = residual_values(p) if p is not None else (0.0, 0.0)
    pw = float(p_true if p_true is not None else (p if p is not None else 0.5))
    for t in range(n):
        if p is None:
            z = rng.standard_normal(n_sims) + shift
        else:
            z = np.where(rng.random(n_sims) < pw, win, loss) + shift
        s = np.round(np.maximum(0.0, s - z - k), ROUND).astype(float)
        np.maximum(peak, s, out=peak)
        if h is not None:
            first = np.where((first > n) & (s > h), t + 1, first)
    return peak, first


def smallest_h_at_rate(peaks: np.ndarray, false_alarm: float, z: float = CONFIDENCE_Z) -> float:
    """The smallest attained value v of max_t S_t with P(max S > v) <= false_alarm at one-sided confidence z over the
    simulated paths: the most sensitive h that meets the rate (the largest attainable rate under it). The rate is a
    step function of h for discrete residuals, so h is searched over the attained values, not interpolated."""
    a = np.sort(np.asarray(peaks, dtype=float))
    n = len(a)
    vals = np.unique(a)
    fa = (n - np.searchsorted(a, vals, side="right")) / n
    ok = fa + z * np.sqrt(fa * (1 - fa) / n) <= false_alarm
    return float(vals[int(np.argmax(ok))])          # the largest value always has rate 0, so some value is ok


@lru_cache(maxsize=512)
def _h(n: int, k: float, false_alarm: float, n_sims: int, seed: int, p: float | None = None) -> float:
    return smallest_h_at_rate(_simulate(n, k, n_sims, seed, p=p)[0], false_alarm)


def calibrated_h(trades_per_week: float | None, k: float = DEFAULT_K, false_alarm: float = FALSE_ALARM_QUARTER,
                 n_sims: int = N_SIMS, seed: int = SEED, p: float | None = None,
                 weeks: float = QUARTER_WEEKS) -> float:
    """The decision interval h for allowance k at which in-control residuals alarm within `weeks` of expected trades
    (default a quarter) with probability at most `false_alarm`: two-point trade residuals at the mean taken p (0.01
    bucket), normal ones when p is None; FALLBACK_H without a usable trade rate. Cached per (trades, k, rate, p)."""
    n = trades_per_window(trades_per_week, weeks)
    if n is None:
        return FALLBACK_H
    return _h(n, round(float(k), 6), round(float(false_alarm), 6), int(n_sims), int(seed), p_bucket(p))


def alarm_rate(h: float, k: float, n: int, shift: float = 0.0, n_sims: int = N_SIMS, seed: int = SEED + 1,
               p: float | None = None, p_true: float | None = None) -> float:
    """Share of simulated paths of n standardised values that alarm (S_t > h) at least once. In control (shift 0,
    p_true None) this is the false-alarm rate; with shift -1 (normal) or p_true < p (trade residuals) it is the
    probability of catching the decay within n trades."""
    return float(np.mean(_simulate(n, k, n_sims, seed, shift, p=p_bucket(p), p_true=p_true)[0] > h))


def run_lengths(h: float, k: float, n: int, shift: float, n_sims: int = 10_000, seed: int = SEED + 2,
                p: float | None = None, p_true: float | None = None) -> np.ndarray:
    """Trades until the first alarm on each simulated path (n + 1 where none within n)."""
    return _simulate(n, k, n_sims, seed, shift, h, p=p_bucket(p), p_true=p_true)[1]
