"""Survey feature families: the indicator survey's best-ranked bulk candidates goldbot did not have yet
(docs/research/indicator-survey.md, section 4a and top-15 ranks 3, 4 and 10).

Owner's request: point-in-time features only, for research to screen; no specialist declares these columns, so they
reach a model only through the P4 screen and a pre-registered trial (adding a feature is not a trial).

Conventions (as the trader and structure families):
- a value at bar t uses bars that closed at or before t only (bar t included: features are computed on its close);
  every rolling window is trailing (`rolling(n, min_periods=n)`), so a value never changes when later bars arrive;
- values are unitless (log-return volatilities per bar, ratios, exponents, flags, counts) or scaled by ATR14 / by an
  assumed round-trip cost, never raw prices;
- a column is NaN until its window is full (warm-up below); counts and flags are 0 where nothing happened.

Families and warm-up (n bars before the first value):
- `vol_estimators` (family `volatility`): per-bar volatility of log returns from OHLC. Garman-Klass
  `0.5 ln(H/L)^2 - (2 ln 2 - 1) ln(C/O)^2`, Rogers-Satchell `ln(H/C) ln(H/O) + ln(L/C) ln(L/O)` (drift-robust),
  Yang-Zhang `var(overnight) + k var(open-close) + (1 - k) mean(RS)`, k = 0.34 / (1.34 + (n + 1) / (n - 1)), where
  "overnight" is open[t] / close[t-1] (the gap between bars: weekend and session gaps). Windows 20 and 60 bars
  (`vol_gk_20` ... `vol_yz_60`), Parkinson over 60 (`vol_pk_60`; the 20-bar one is `realised_vol`'s `parkinson_20`).
  Term structure `vol_ts_yz_20_60` = YZ20 / YZ60 (> 1 expanding, < 1 compressing). Vol-of-vol `vol_of_vol_60` =
  SD / mean of YZ20 over the last 60 bars (coefficient of variation). Warm-up: 20 (GK/RS/PK at 20: 19), YZ needs the
  previous close (YZ20 from bar 20, YZ60 from bar 60), vol-of-vol from bar 79.
- `round_numbers` (family `structure`): for each step $5, $10, $25, $50 the nearest multiple L of the step to the
  close; `round_<s>_dist_atr` = (close - L) / ATR14 (positive above the level, at most step / 2 / ATR in size) and
  `round_<s>_touches` = how many of the last `ctx['round_touch_bars']` (50) bars, bar t included, traded through L
  (low <= L <= high). Warm-up: ATR 13 bars, touches 49 bars.
- `regime_stats` (family `regime`): variance ratio `vr_<q>` = var(q-bar log returns) / (q x var(1-bar log returns))
  over the last `ctx['vr_window']` (120) bars, q = 2, 4, 8 (Lo-MacKinlay's ratio with overlapping q-bar returns;
  > 1 trending, < 1 mean-reverting); `hurst_128` = rolling rescaled-range (R/S) Hurst exponent over the last 128
  one-bar returns: R/S averaged over the non-overlapping chunks of 8, 16, 32 and 64 returns that tile the window,
  H = least-squares slope of log(R/S) on log(chunk size) (0.5 random walk, > 0.5 persistent, < 0.5 anti-persistent;
  small-sample R/S is biased upwards, the screen sees the raw value); Kaufman efficiency ratio `er_10`, `er_30` =
  |close[t] - close[t-n]| / sum of |one-bar changes| over n bars (1 straight line, ~0 noise). Warm-up: vr_q from bar
  119 + q, hurst from bar 128, er_n from bar n.
- `jumps` (family `volatility`): Lee-Mykland style flag. sigma[t] = sqrt(pi/2 x mean(|r_j| |r_j-1|)) over the
  `ctx['jump_window']` (60) returns before bar t (bipower variation, robust to the jumps it detects; bar t itself is
  excluded); `jump_z` = r[t] / sigma[t], `jump_flag` = 1 when |jump_z| > `ctx['jump_k']` (4.0), `jump_sign` = sign of a
  flagged return (0 otherwise), `bars_since_jump` = bars since the last flagged bar (0 on it), capped at 500 and 500
  before the first jump ("none in the last 500 bars"), so a bounded live window agrees once warmed up. Raw features only: whether a jump continues (news) or
  reverts (stop cascade) needs news timing and is a later trial. Warm-up: 61 bars.
- `expected_move` (family `volatility`): forecast move in units of an assumed round-trip cost, a feature only (the
  cost-to-move filter is a later trial). Cost per unit = the median bar spread over the last 96 bars plus
  `ctx['round_trip_extra']` (0.37 USD/oz: 2 x the 0.15 slippage prior plus 2 x 3.5 USD/lot commission over 100 oz,
  config/settings.yaml `costs`). `em_yz_<h>_cost` = close x YZ20 x sqrt(h) / cost for horizons h of
  `ctx['em_horizons']` (4, 16, 48 bars); `em_atr_cost` = ATR14 / cost. Warm-up: 95 bars (the spread median).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.features.columns import Columns
from goldbot.features.registry import FeatureCtx, feature
from goldbot.features.technical import atr

ROUND_STEPS = (5, 10, 25, 50)
VR_LAGS = (2, 4, 8)
HURST_WINDOW = 128
HURST_CHUNKS = (8, 16, 32, 64)
JUMP_MAX_AGE = 500
ROUND_TRIP_EXTRA_USD = 2 * 0.15 + 2 * 3.5 / 100     # slippage prior and commission per side, per oz
_BLOCK = 20_000                                       # rows per sliding-window block (bounds memory)


def _ohlc(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    return tuple(df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))  # type: ignore[return-value]


def _prev(x: np.ndarray) -> np.ndarray:
    return np.concatenate(([np.nan], x[:-1])) if len(x) else x


def _mean(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).rolling(n, min_periods=n).mean().to_numpy(dtype=float)


def _var(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).rolling(n, min_periods=n).var().to_numpy(dtype=float)


def _sqrt(x: np.ndarray) -> np.ndarray:
    return np.sqrt(np.maximum(x, 0.0))


def yang_zhang(df: pd.DataFrame, n: int) -> np.ndarray:
    """Yang-Zhang per-bar volatility of log returns over the last n bars (module docstring)."""
    o, h, lo, c = _ohlc(df)
    ov = np.log(o / _prev(c))
    oc = np.log(c / o)
    rs = np.log(h / c) * np.log(h / o) + np.log(lo / c) * np.log(lo / o)
    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    return _sqrt(_var(ov, n) + k * _var(oc, n) + (1 - k) * _mean(rs, n))


# --------------------------------------------------------------------------- volatility estimators
@feature("vol_estimators", "volatility", lookback=200)
def f_vol_estimators(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    """Garman-Klass, Rogers-Satchell and Yang-Zhang over 20 and 60 bars, Parkinson over 60, the YZ term structure
    and vol-of-vol (definitions and warm-up in the module docstring)."""
    o, h, lo, c = _ohlc(df)
    hl, co = np.log(h / lo), np.log(c / o)
    gk = 0.5 * hl ** 2 - (2 * np.log(2) - 1) * co ** 2
    rs = np.log(h / c) * np.log(h / o) + np.log(lo / c) * np.log(lo / o)
    out = Columns(df.index)
    for n in (20, 60):
        out[f"vol_gk_{n}"] = _sqrt(_mean(gk, n))
        out[f"vol_rs_{n}"] = _sqrt(_mean(rs, n))
        out[f"vol_yz_{n}"] = yang_zhang(df, n)
    out["vol_pk_60"] = _sqrt(_mean(hl ** 2, 60) / (4 * np.log(2)))
    yz20, yz60 = out["vol_yz_20"].to_numpy(dtype=float), out["vol_yz_60"].to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        out["vol_ts_yz_20_60"] = np.where(yz60 > 0, yz20 / yz60, np.nan)
        m, sd = _mean(yz20, 60), np.sqrt(_var(yz20, 60))
        out["vol_of_vol_60"] = np.where(m > 0, sd / m, np.nan)
    return out.frame()


# --------------------------------------------------------------------------- round numbers
@feature("round_numbers", "structure", lookback=100)
def f_round_numbers(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    """Distance in ATR to the nearest $5/$10/$25/$50 level and how many of the last N bars traded through it."""
    n_touch = int(ctx.get("round_touch_bars", 50))
    _, h, lo, c = _ohlc(df)
    a = atr(df, 14).to_numpy(dtype=float)
    n = len(df)
    out = Columns(df.index)
    for step in ROUND_STEPS:
        level = np.round(c / step) * step
        out[f"round_{step}_dist_atr"] = (c - level) / a
        touches = np.full(n, np.nan)
        if n >= n_touch:
            hw = np.lib.stride_tricks.sliding_window_view(h, n_touch)
            lw = np.lib.stride_tricks.sliding_window_view(lo, n_touch)
            for s in range(0, len(hw), _BLOCK):
                lv = level[s + n_touch - 1: s + n_touch - 1 + _BLOCK, None]
                hit = (lw[s: s + _BLOCK] <= lv) & (hw[s: s + _BLOCK] >= lv)
                touches[s + n_touch - 1: s + n_touch - 1 + len(hit)] = hit.sum(axis=1)
        out[f"round_{step}_touches"] = touches
    return out.frame()


# --------------------------------------------------------------------------- regime statistics
def _rescaled_range(r: np.ndarray, m: int) -> np.ndarray:
    """R/S of the m returns ending at each bar (NaN until m returns, or for a flat chunk)."""
    out = np.full(len(r), np.nan)
    if len(r) < m:
        return out
    win = np.lib.stride_tricks.sliding_window_view(r, m)
    for s in range(0, len(win), _BLOCK):
        w = win[s: s + _BLOCK]
        y = np.cumsum(w - w.mean(axis=1, keepdims=True), axis=1)
        sd = w.std(axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            out[s + m - 1: s + m - 1 + len(w)] = np.where(sd > 0, (y.max(axis=1) - y.min(axis=1)) / sd, np.nan)
    return out


def rolling_hurst(r: np.ndarray, window: int = HURST_WINDOW, chunks: tuple[int, ...] = HURST_CHUNKS) -> np.ndarray:
    """R/S Hurst exponent over the last `window` returns: the mean R/S of the window's non-overlapping chunks of each
    size, regressed (log-log, least squares) on the chunk size."""
    n = len(r)
    logs = []
    for m in chunks:
        rs = _rescaled_range(r, m)
        k = window // m
        stack = np.vstack([np.concatenate((np.full(i * m, np.nan), rs[: n - i * m]))[:n] for i in range(k)])
        logs.append(np.log(stack.mean(axis=0)))       # any NaN chunk (warm-up, flat) leaves the bar NaN
    x = np.log(np.asarray(chunks, dtype=float))
    xc = x - x.mean()
    y = np.vstack(logs)
    return (xc[:, None] * (y - y.mean(axis=0))).sum(axis=0) / (xc ** 2).sum()


@feature("regime_stats", "regime", lookback=300)
def f_regime_stats(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    """Variance ratios (2/4/8 bars), rolling R/S Hurst over 128 returns, Kaufman efficiency ratio over 10 and 30."""
    w = int(ctx.get("vr_window", 120))
    c = df["close"].to_numpy(dtype=float)
    lc = pd.Series(np.log(c))
    r1 = lc.diff().to_numpy(dtype=float)
    v1 = _var(r1, w)
    out = Columns(df.index)
    with np.errstate(divide="ignore", invalid="ignore"):
        for q in VR_LAGS:
            vq = _var(lc.diff(q).to_numpy(dtype=float), w)
            out[f"vr_{q}"] = np.where(v1 > 0, vq / (q * v1), np.nan)
        out[f"hurst_{HURST_WINDOW}"] = rolling_hurst(r1)
        cs = pd.Series(c)
        step = cs.diff().abs()
        for n in (10, 30):
            path = step.rolling(n, min_periods=n).sum().to_numpy(dtype=float)
            net = cs.diff(n).abs().to_numpy(dtype=float)
            out[f"er_{n}"] = np.where(path > 0, net / path, np.where(np.isfinite(path), 0.0, np.nan))
    return out.frame()


# --------------------------------------------------------------------------- jumps
@feature("jumps", "volatility", lookback=600, signed={"jump_z": 0.0, "jump_sign": 0.0})
def f_jumps(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    """Bar return in units of the preceding bipower sigma, a |z| > k flag, its sign and bars since the last jump."""
    w = int(ctx.get("jump_window", 60))
    k = float(ctx.get("jump_k", 4.0))
    r = pd.Series(np.log(df["close"].to_numpy(dtype=float))).diff().to_numpy(dtype=float)
    bv = (np.pi / 2) * _mean(np.abs(r) * np.abs(_prev(r)), w)
    sigma = _sqrt(_prev(bv))                           # returns before bar t only
    with np.errstate(divide="ignore", invalid="ignore"):
        z = np.where(sigma > 0, r / sigma, np.nan)
    flag = np.abs(z) > k                               # NaN compares False: no jump during warm-up
    n = len(df)
    at = pd.Series(np.where(flag, np.arange(n, dtype=float), np.nan)).ffill().to_numpy(dtype=float)
    out = Columns(df.index)
    out["jump_z"] = z
    out["jump_flag"] = flag.astype(np.int64)
    out["jump_sign"] = np.where(flag, np.sign(r), 0.0).astype(np.int64)
    since = np.arange(n, dtype=float) - at
    out["bars_since_jump"] = np.where(np.isnan(since), JUMP_MAX_AGE, np.minimum(since, JUMP_MAX_AGE))
    return out.frame()


# --------------------------------------------------------------------------- expected move vs cost
@feature("expected_move", "volatility", lookback=200)
def f_expected_move(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    """Forecast move over the specialist horizons (YZ20 scaled by sqrt(h)) and ATR14, in round-trip costs."""
    horizons = tuple(int(x) for x in ctx.get("em_horizons", (4, 16, 48)))
    extra = float(ctx.get("round_trip_extra", ROUND_TRIP_EXTRA_USD))
    c = df["close"].to_numpy(dtype=float)
    spread = pd.Series(df["spread"].to_numpy(dtype=float)).rolling(96, min_periods=96).median().to_numpy(dtype=float)
    cost = spread + extra
    yz = yang_zhang(df, 20)
    out = Columns(df.index)
    with np.errstate(divide="ignore", invalid="ignore"):
        for hz in horizons:
            out[f"em_yz_{hz}_cost"] = np.where(cost > 0, c * yz * np.sqrt(hz) / cost, np.nan)
        out["em_atr_cost"] = np.where(cost > 0, atr(df, 14).to_numpy(dtype=float) / cost, np.nan)
    return out.frame()
