"""CUSUM decision interval tuned to the design's "5% quarterly false-alarm rate" (row M25).

Both CUSUM alarms (the drift watch's residual CUSUM, research/drift.py `residual_cusum`, and the new-champion watch,
research/promotion.py `cusum_alarm`) run a one-sided downward CUSUM on standardised per-trade values with allowance k:

    S_0 = 0,  S_t = max(0, S_{t-1} - z_t - k),  alarm when S_t > h.

A fixed h makes the false-alarm rate depend on how often the agent trades: an agent with 60 trades a quarter gets
many more chances to cross h by luck than one with 10. The design fixes the rate instead: h is chosen, for a given k,
so that on in-control values the probability of ANY alarm within one window's expected trade count is at most
`false_alarm` (5%). The count comes from the backtest: round(trades_per_week x 13) for the quarterly drift watch;
for the new-champion watch `watch_trades`: two weeks' trades or the first WATCH_MIN_TRADES (12), whichever is more,
capped at WATCH_MAX_WEEKS (8) weeks' worth (CUSUM re-verify: two weeks of a 2-2.5 trades/week agent are 4-5 trades,
and no h with a <= 5% rate is reachable in so few).

In-control values are Bernoulli, not normal. A barrier trade either wins (probability p, the model's) or loses, so its
standardised residual is two-point whatever the target distance: win +sqrt((1-p)/p), loss -sqrt(p/(1-p)). Calibrating
on normal values made h far too high for such residuals (at p 0.4 the realised quarterly false-alarm rate was 0.2-0.7%,
7-25x too conservative, and real decay was caught late). So h is calibrated on simulated two-point residuals. The drift
watch resamples the agent's actual taken p values (0.01 buckets; each simulated trade draws its p, wins with it and is
standardised at it): the spread of p changes the mixture's tails, and calibrating at the mean p alone let the live
rate reach 5.5-7% at spreads of 0.30-0.70 (CUSUM re-verify). The champion watch uses the backtest hit rate: its
standardised returns are two-point at the marginal win rate whatever the per-trade p. Normal values remain only when p
is unknown.

Because the residuals are discrete, max_t S_t is discrete and the false-alarm rate is a step function of h. v is the
smallest attained value of max_t S_t whose false-alarm rate is at most `false_alarm` with 95% confidence over the
`N_SIMS` simulated paths (the most sensitive choice that still meets the design's rate; the confidence margin, ~0.1 pp,
keeps fresh paths under 5% despite simulation noise), and h is set halfway between v and the next higher attained
value (`decision_interval`): the same rate, but h is not a value the statistic can reach, so the live code's unrounded
float sums (the simulator rounds to ROUND decimals) cannot turn a tie into an alarm. A fixed seed makes h reproducible;
it is cached per (trades, k, rate, p bucket or p histogram). Without a trade rate (no backtest stats) the fixed
`FALLBACK_H` applies. Simulated, not from an ARL approximation (Siegmund), because a window is short: tens of trades,
where the stationary approximations are poor. When even all-loss paths cannot exceed h within the watch's trades
(`can_alarm` false: too few trades for a 5% rate), the champion watch reports `cannot_alarm` instead of a silent "no
alarm"; such slow agents rely on the drift watch (quarterly residual CUSUM, calibration, PSI) and the drawdown halt.

Limit, stated: the drift watch's CUSUM runs over the whole life of a champion version, so 5% is the rate per quarter
of trading, not per life.
"""
from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from functools import lru_cache

import numpy as np

QUARTER_WEEKS = 13.0
WATCH_WEEKS = 2.0               # the new-champion watch: "trips the alarm in its first two weeks"
WATCH_MIN_TRADES = 12           # ... or its first 12 trades, whichever comes later (CUSUM re-verify: 2 weeks of a
                                #   2-2.5 trades/week agent hold 4-5 trades, too few for any alarm at a 5% rate)
WATCH_MAX_WEEKS = 8.0           # ... capped at 8 weeks
FALSE_ALARM_QUARTER = 0.05      # design: "tuned to a 5% quarterly false-alarm rate"
DEFAULT_K = 0.5                 # allowance in sd: half the 1-sd shift the alarm is meant to catch
FALLBACK_H = 4.0                # h when the expected trade rate is unknown (the previous fixed value)
N_SIMS = 100_000                # in-control paths per calibration (standard error of the rate ~0.07 pp at 5%)
CONFIDENCE_Z = 1.645            # one-sided 95%: the chosen h's simulated rate plus this many SEs is <= false_alarm
SEED = 25                       # row M25: fixed so h is reproducible
MAX_TRADES = 5_000              # a window longer than this is capped (memory and time; no agent trades this often)
P_BUCKET = 0.01                 # taken p values are rounded to this before calibrating (and caching)
ROUND = 9                       # decimals: sums of lattice steps that are equal in exact arithmetic compare equal

PHistogram = tuple[tuple[float, int], ...]


def trades_per_window(trades_per_week: float | None, weeks: float = QUARTER_WEEKS) -> int | None:
    """Expected trades in a window of `weeks` from the backtest's rate, or None when unknown or not positive."""
    if trades_per_week is None or not math.isfinite(trades_per_week) or trades_per_week <= 0:
        return None
    return int(min(max(round(trades_per_week * weeks), 1), MAX_TRADES))


def trades_per_quarter(trades_per_week: float | None) -> int | None:
    """Expected trades in one quarter from the backtest's rate, or None when unknown or not positive."""
    return trades_per_window(trades_per_week, QUARTER_WEEKS)


def watch_trades(trades_per_week: float | None) -> int | None:
    """Trades the new-champion watch covers: two weeks' or the first WATCH_MIN_TRADES, whichever is more, but no more
    than WATCH_MAX_WEEKS' worth; None without a trade rate. Its h is calibrated for exactly this count."""
    two, cap = trades_per_window(trades_per_week, WATCH_WEEKS), trades_per_window(trades_per_week, WATCH_MAX_WEEKS)
    if two is None or cap is None:
        return None
    return min(max(two, WATCH_MIN_TRADES), cap)


def p_bucket(p: float | None) -> float | None:
    """The win probability rounded to P_BUCKET and kept inside (0, 1); None when unknown or not finite."""
    if p is None or not math.isfinite(p):
        return None
    return round(min(max(round(p / P_BUCKET) * P_BUCKET, P_BUCKET), 1 - P_BUCKET), 6)


def p_histogram(ps: Sequence[float] | None) -> PHistogram | None:
    """The taken p values as ((bucket, count), ...) sorted by bucket (the calibration's cache key), or None when
    there are none."""
    buckets = [b for b in (p_bucket(float(x)) for x in (ps or [])) if b is not None]
    return tuple(sorted(Counter(buckets).items())) or None


def residual_values(p: float) -> tuple[float, float]:
    """The two standardised residuals of a barrier trade the model gave probability p: (win, loss)."""
    return math.sqrt((1 - p) / p), -math.sqrt(p / (1 - p))


def max_statistic(n: int, k: float, p: float | None) -> float:
    """The largest S_n any path of n in-control two-point residuals at p can reach (n losses in a row); infinite for
    normal values (p None)."""
    if p is None:
        return math.inf
    return n * max(-residual_values(p)[1] - k, 0.0)


def can_alarm(h: float, n: int, k: float, p: float | None) -> bool:
    """Whether S can exceed h within n trades at all (two-point residuals at p's 0.01 bucket, the calibration's)."""
    return h < max_statistic(n, k, p_bucket(p))


def _simulate(n: int, k: float, n_sims: int, seed: int, shift: float = 0.0, h: float | None = None,
              p: float | None = None, p_true: float | None = None,
              hist: PHistogram | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Downward CUSUM on n_sims paths of n iid standardised values, drawn one trade at a time (memory O(n_sims)):
    (max_t S_t per path, trades until S_t first exceeds h per path, n + 1 where it never does or h is None).

    With p None the values are N(shift, 1). With p they are the two-point trade residuals standardised at p
    (`residual_values`), the win drawn with probability `p_true` (default p: in control; lower: the model's edge has
    decayed), plus `shift`. With `hist` (the agent's taken p values) each trade's p is resampled from it and its
    residual is standardised at that p, the win drawn with that p (in control)."""
    rng = np.random.default_rng(seed)
    s = np.zeros(n_sims)
    peak = np.zeros(n_sims)
    first = np.full(n_sims, n + 1)
    win, loss = residual_values(p) if p is not None else (0.0, 0.0)
    pw = float(p_true if p_true is not None else (p if p is not None else 0.5))
    if hist is not None:
        hp = np.repeat([b for b, _ in hist], [c for _, c in hist])
        hwin, hloss = np.sqrt((1 - hp) / hp), -np.sqrt(hp / (1 - hp))
    for t in range(n):
        if hist is not None:
            i = rng.integers(0, len(hp), n_sims)
            z = np.where(rng.random(n_sims) < hp[i], hwin[i], hloss[i]) + shift
        elif p is None:
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


def decision_interval(peaks: np.ndarray, false_alarm: float, z: float = CONFIDENCE_Z) -> float:
    """h halfway between `smallest_h_at_rate`'s value v and the next higher attained value (half a step above the
    top value when v is the top): P(max S > h) is the same as at v, but h no longer sits ON a value the statistic
    reaches, so live arithmetic (unrounded floats, a p a hair off its bucket) cannot flip an exact tie into an alarm
    (CUSUM re-verify: h on the lattice let production false alarms run at 5.5-7%)."""
    v = smallest_h_at_rate(peaks, false_alarm, z)
    vals = np.unique(np.asarray(peaks, dtype=float))
    higher, lower = vals[vals > v], vals[vals < v]
    if higher.size:
        return float((v + higher[0]) / 2)
    return float(v + ((v - lower[-1]) if lower.size else 1.0) / 2)


@lru_cache(maxsize=512)
def _h(n: int, k: float, false_alarm: float, n_sims: int, seed: int, p: float | None = None,
       hist: PHistogram | None = None) -> float:
    return decision_interval(_simulate(n, k, n_sims, seed, p=p, hist=hist)[0], false_alarm)


def calibrated_h(trades_per_week: float | None, k: float = DEFAULT_K, false_alarm: float = FALSE_ALARM_QUARTER,
                 n_sims: int = N_SIMS, seed: int = SEED, p: float | None = None,
                 weeks: float = QUARTER_WEEKS, ps: Sequence[float] | None = None, trades: int | None = None) -> float:
    """The decision interval h for allowance k at which in-control residuals alarm within `weeks` of expected trades
    (default a quarter; `trades` gives the count directly, e.g. `watch_trades`) with probability at most
    `false_alarm`: two-point trade residuals resampling the agent's taken p values `ps` (0.01 buckets) when given,
    else at p (the mean taken p or the backtest hit rate), normal ones when both are None; FALLBACK_H without a usable
    trade rate. Cached per (trades, k, rate, p or p histogram)."""
    n = trades_per_window(trades_per_week, weeks) if trades is None else (min(int(trades), MAX_TRADES) or None)
    if n is None or n <= 0:
        return FALLBACK_H
    hist = p_histogram(ps)
    if hist is not None and len(hist) == 1:          # one bucket: exactly the single-p calibration
        p, hist = hist[0][0], None
    return _h(n, round(float(k), 6), round(float(false_alarm), 6), int(n_sims), int(seed),
              None if hist is not None else p_bucket(p), hist)


def alarm_rate(h: float, k: float, n: int, shift: float = 0.0, n_sims: int = N_SIMS, seed: int = SEED + 1,
               p: float | None = None, p_true: float | None = None, ps: Sequence[float] | None = None) -> float:
    """Share of simulated paths of n standardised values that alarm (S_t > h) at least once. In control (shift 0,
    p_true None) this is the false-alarm rate; with shift -1 (normal) or p_true < p (trade residuals) it is the
    probability of catching the decay within n trades. `ps`: per-trade p resampled from these values."""
    return float(np.mean(_simulate(n, k, n_sims, seed, shift, p=p_bucket(p), p_true=p_true, hist=p_histogram(ps))[0] > h))


def run_lengths(h: float, k: float, n: int, shift: float, n_sims: int = 10_000, seed: int = SEED + 2,
                p: float | None = None, p_true: float | None = None) -> np.ndarray:
    """Trades until the first alarm on each simulated path (n + 1 where none within n)."""
    return _simulate(n, k, n_sims, seed, shift, h, p=p_bucket(p), p_true=p_true)[1]
