"""Per-column lookback measurement and truncation check (quant ruling on PR #472, ADR 0104 4).

"The input lookback is the maximum over the requested columns of each column's own lookback: the oldest bar whose
perturbation can change the column by >= 1 % of the weight an EWM puts on it, in decision bars x 7/5 below 1w."

Operational definition (one rule for every column, EWM or not):
- A fixture per timeframe: `SEEDS` synthetic mid-bar series of `N_BARS` bars at that tf (random walk, weekday clock,
  spread, ticks). Lookbacks are declared per tf: a session-anchored column reads more bars at 1m than at 4h.
- The column's value at the last bar T. "Perturbing every bar older than L" replaces bars 0..T-L with an independent
  path of three times the volatility, re-levelled so it joins bar T-L+1 continuously (level, range and volume
  regimes all change; anything the column reads from those bars can move).
- The bound: 1 % of the column's own scale (the standard deviation of its unperturbed values over the last
  `SCALE_BARS` bars of the fixture), so an EWM whose remaining weight beyond L is < 1 % passes.
- A declared lookback L passes when perturbing every bar older than L changes the column at T by less than the
  bound on every seed, and is tight when perturbing every bar older than L - 1 (the bar just inside included)
  changes it by at least the bound on some seed. A column that reads no past bar (clock) has L = 1 and no tightness
  check.

ADR 0104 4, appended by the quant sign-off of PR #472 (verbatim): "The truncation measurement uses two perturbations of
every bar older than L, and L is the larger of the two lookbacks: (i) the continuous 3×-volatility replacement path;
(ii) a discontinuous error: every older bar shifted by +5 × the fixture's median bar range, with 1 % of those bars
replaced by spikes of ±20 × the median range. `measure` declares L on 32 seeds. The CI test
(`tests/test_column_lookbacks.py`) re-checks pass and tightness on 8 of them. Declared lookbacks never shrink without a
quant re-review. Counts are re-run after the change."
- (i) is `PATH`, (ii) is `SHIFT` (a spike bar is displaced whole, open to close, by a random sign).
- `MEASURE_SEEDS` (32) declare; `SEEDS` (the first 8 of them) are the CI seeds. `passes` checks both perturbations on
  the CI seeds; `tight` looks for the bar-just-inside failure on the CI seeds first and then on the rest of the 32 (the
  seed that binds L may be any of them).
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from functools import lru_cache

import numpy as np
import pandas as pd

from goldbot.features import build_features

N_BARS = 4_000
MEASURE_SEEDS: tuple[int, ...] = tuple(range(11, 43))       # 32 seeds declare L
SEEDS: tuple[int, ...] = MEASURE_SEEDS[:8]                  # the CI re-check
PATH, SHIFT = "path", "shift"
KINDS: tuple[str, ...] = (PATH, SHIFT)
SHIFT_RANGES = 5.0
SPIKE_SHARE = 0.01
SPIKE_RANGES = 20.0
SCALE_BARS = 1_000
BOUND = 0.01


FREQ = {"1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h", "4h": "4h", "1d": "1D", "1w": "7D"}


@lru_cache(maxsize=None)
def fixture(seed: int, tf: str = "15m", n: int = N_BARS) -> pd.DataFrame:
    """Weekday bars only below 1d (the market's weekend gap), every day at 1d, weekly at 1w."""
    rng = np.random.default_rng(seed)
    if tf in ("1d", "1w"):
        ts = pd.date_range("2000-01-03 13:30", periods=n, freq=FREQ[tf], tz="UTC")
    else:
        # each seed starts at another clock time, so the last bar falls at another point of the session
        start = pd.Timestamp("2014-01-06", tz="UTC") + pd.Timedelta(minutes=433 * seed)
        ts = pd.date_range(start.floor(FREQ[tf]), periods=2 * n + 4 * 1440, freq=FREQ[tf], tz="UTC")
        ts = ts[ts.dayofweek < 5][:n]
    dt = pd.Timedelta(FREQ[tf])
    c = 1200 + np.cumsum(rng.normal(0, 0.6, n))
    hi = c + np.abs(rng.normal(0, 0.5, n))
    lo = c - np.abs(rng.normal(0, 0.5, n))
    op = np.clip(c + rng.normal(0, 0.15, n), lo, hi)
    return pd.DataFrame({"ts_utc": ts, "visible_at": ts + dt, "open": op, "high": hi,
                         "low": lo, "close": c, "spread": np.abs(rng.normal(0.25, 0.05, n)),
                         "tick_count": rng.integers(20, 200, n).astype(float)})


def perturb(frame: pd.DataFrame, keep: int, seed: int, kind: str = PATH) -> pd.DataFrame:
    """Every bar older than the last `keep` bars perturbed: `PATH` replaces them by another path (3x volatility)
    joined at the junction; `SHIFT` shifts them by +5 x the fixture's median bar range and displaces 1 % of them by
    +-20 x the median range (a discontinuous data error)."""
    n = len(frame)
    cut = n - keep                      # bars 0..cut-1 are "older than keep"
    if cut <= 0:
        return frame
    rng = np.random.default_rng(seed + 10_000)
    out = frame.copy()
    if kind == SHIFT:
        mr = float(np.median((frame["high"] - frame["low"]).to_numpy(dtype=float)))
        delta = np.full(cut, SHIFT_RANGES * mr)
        k = max(1, int(round(SPIKE_SHARE * cut)))
        idx = rng.choice(cut, size=k, replace=False)
        delta[idx] += rng.choice([-1.0, 1.0], size=k) * SPIKE_RANGES * mr
        for col in ("open", "high", "low", "close"):
            out.loc[:cut - 1, col] = frame[col].to_numpy(dtype=float)[:cut] + delta
        return out
    if kind != PATH:
        raise ValueError(f"perturbation {kind!r}: one of {KINDS}")
    steps = rng.normal(0, 1.8, cut)
    path = np.cumsum(steps[::-1])[::-1]             # path[cut-1] is one step from the junction
    base = float(frame["open"].iloc[cut]) if cut < n else float(frame["close"].iloc[-1])
    c = base - path
    hi = c + np.abs(rng.normal(0, 1.5, cut))
    lo = c - np.abs(rng.normal(0, 1.5, cut))
    op = np.clip(c + rng.normal(0, 0.45, cut), lo, hi)
    out.loc[:cut - 1, "open"] = op
    out.loc[:cut - 1, "high"] = hi
    out.loc[:cut - 1, "low"] = lo
    out.loc[:cut - 1, "close"] = c
    out.loc[:cut - 1, "spread"] = np.abs(rng.normal(0.8, 0.2, cut))
    out.loc[:cut - 1, "tick_count"] = rng.integers(200, 2000, cut).astype(float)
    return out


Builder = Callable[[pd.DataFrame], pd.DataFrame]


def builder(feature: str) -> Builder:
    return lambda f: build_features(f, [feature])


@lru_cache(maxsize=None)
def _base(feature: str, seed: int, tf: str) -> pd.DataFrame:
    return builder(feature)(fixture(seed, tf))


def _scale(feature: str, column: str, seed: int, tf: str) -> float:
    v = _base(feature, seed, tf)[column].to_numpy(dtype=float)[-SCALE_BARS:]
    v = v[np.isfinite(v)]
    return float(np.std(v)) if len(v) else 0.0


def change(feature: str, columns: Sequence[str], keep: int, seed: int, tf: str,
           kind: str = PATH) -> dict[str, float]:
    """|column(T) perturbed - column(T) base| / (BOUND x scale) per column: < 1 means within the bound."""
    base = _base(feature, seed, tf)
    pert = builder(feature)(perturb(fixture(seed, tf), keep, seed, kind))
    out = {}
    for c in columns:
        a, b = float(base[c].iloc[-1]), float(pert[c].iloc[-1])
        if np.isnan(a) and np.isnan(b):
            out[c] = 0.0
            continue
        if np.isnan(a) != np.isnan(b):
            out[c] = np.inf
            continue
        s = _scale(feature, c, seed, tf)
        out[c] = abs(a - b) / (BOUND * s) if s > 0 else (0.0 if a == b else np.inf)
    return out


def passes(feature: str, column: str, keep: int, tf: str, *, seeds: Sequence[int] = SEEDS,
           kinds: Sequence[str] = KINDS) -> bool:
    """Every perturbation in `kinds` of every bar older than `keep` stays within the bound on every seed."""
    return all(change(feature, [column], keep, s, tf, k)[column] < 1.0 for k in kinds for s in seeds)


def tight(feature: str, column: str, keep: int, tf: str) -> bool:
    """The bar just inside matters: perturbing every bar older than keep - 1 breaks the bound for one of the two
    perturbations on some seed (the CI seeds first, then the rest of the 32 measure seeds)."""
    if keep <= 1:
        return True
    order = (*SEEDS, *(s for s in MEASURE_SEEDS if s not in SEEDS))
    return any(change(feature, [column], keep - 1, s, tf, k)[column] >= 1.0 for s in order for k in KINDS)


def _smallest(feature: str, column: str, tf: str, kind: str, hi: int) -> int:
    if passes(feature, column, 1, tf, seeds=MEASURE_SEEDS, kinds=(kind,)):
        return 1
    if not passes(feature, column, hi, tf, seeds=MEASURE_SEEDS, kinds=(kind,)):
        raise ValueError(f"{feature}/{column} {kind}: no lookback below {hi} bars passes the 1 % bound")
    lo_, hi_ = 1, hi
    while hi_ - lo_ > 1:
        mid = (lo_ + hi_) // 2
        if passes(feature, column, mid, tf, seeds=MEASURE_SEEDS, kinds=(kind,)):
            hi_ = mid
        else:
            lo_ = mid
    return hi_


def measure(feature: str, column: str, tf: str, hi: int = N_BARS - 50) -> int:
    """L = the larger of the two perturbations' smallest passing lookbacks, on the 32 measure seeds (binary search,
    monotone by construction)."""
    return max(_smallest(feature, column, tf, k, hi) for k in KINDS)


__all__ = ["BOUND", "FREQ", "KINDS", "MEASURE_SEEDS", "N_BARS", "PATH", "SEEDS", "SHIFT", "change", "fixture", "measure", "passes", "perturb", "tight"]
