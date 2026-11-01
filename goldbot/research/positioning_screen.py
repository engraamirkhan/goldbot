"""Positioning and flows screen (docs/research/prereg-positioning-2026-11-01.md): pure logic, no I/O.

Three pre-registered variants at a daily decision bar with a one-week hold:

    P1_EXT   COT managed-money net % OI in its previous-156-report bottom decile -> long, top decile -> short (weekly)
    P2_FLOW  -sign(one-report change of that %) when |change| is in the top third of the previous 52 (weekly)
    P3_ETF   sign(5-observation % change of GLD tonnes) when |change| is in the top third of the previous 252 (daily)

Signals are computed on each series' first releases (`goldbot.data.macro.first_release`) on their own availability
timeline and reach decision bars only through `store.asof_join` on `available_utc` (the script does the join).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.data.macro import first_release

P1_WINDOW, P1_MIN, P1_LO, P1_HI = 156, 104, 0.10, 0.90
P2_WINDOW, P2_MIN, P2_Q = 52, 40, 2 / 3
P3_LAG, P3_WINDOW, P3_MIN, P3_Q = 5, 252, 126, 2 / 3
HOLD_DECISIONS = 5
VARIANTS = ("P1_EXT", "P2_FLOW", "P3_ETF")
WEEKLY = {"P1_EXT": True, "P2_FLOW": True, "P3_ETF": False}


def trailing_share_below(x: np.ndarray, window: int, min_prior: int) -> np.ndarray:
    """For each i: the share of the previous `window` finite values (x[i-window:i]) strictly below x[i]; NaN when x[i]
    is not finite or fewer than `min_prior` finite previous values exist. Uses nothing at or after i except x[i]."""
    x = np.asarray(x, dtype=float)
    out = np.full(len(x), np.nan)
    for i in range(len(x)):
        if not np.isfinite(x[i]):
            continue
        prior = x[max(0, i - window):i]
        prior = prior[np.isfinite(prior)]
        if len(prior) >= min_prior:
            out[i] = float(np.mean(prior < x[i]))
    return out


def cot_signals(pos: pd.DataFrame) -> pd.DataFrame:
    """One row per COT report: available_utc, report_date, mm_pct_oi, P1_EXT and P2_FLOW sides (0 = no trade)."""
    net, oi = first_release(pos, "cot_mm_net"), first_release(pos, "cot_open_interest")
    cot = net.merge(oi, on="value_date", suffixes=("_net", "_oi"))
    cot = cot[cot["value_oi"] > 0].sort_values("value_date").reset_index(drop=True)
    avail = cot[["available_utc_net", "available_utc_oi"]].max(axis=1).cummax()
    x = (100.0 * cot["value_net"].astype(float) / cot["value_oi"].astype(float)).to_numpy()
    pct = trailing_share_below(x, P1_WINDOW, P1_MIN)
    p1 = np.where(pct <= P1_LO, 1, np.where(pct >= P1_HI, -1, 0))
    d = np.concatenate(([np.nan], np.diff(x)))
    share = trailing_share_below(np.abs(d), P2_WINDOW, P2_MIN)
    p2 = np.where(share >= P2_Q, -np.sign(np.nan_to_num(d)), 0)
    return pd.DataFrame({"available_utc": pd.DatetimeIndex(avail).as_unit("ns"),
                         "report_date": pd.to_datetime(cot["value_date"]).to_numpy(),
                         "mm_pct_oi": x, "P1_EXT": p1.astype(int), "P2_FLOW": p2.astype(int)})


def gld_signals(pos: pd.DataFrame) -> pd.DataFrame:
    """One row per GLD observation: available_utc, gld_chg5_pct, P3_ETF side (0 = no trade)."""
    g = first_release(pos, "gld_tonnes").sort_values("value_date").reset_index(drop=True)
    t = g["value"].astype(float).to_numpy()
    chg = np.full(len(t), np.nan)
    if len(t) > P3_LAG:
        chg[P3_LAG:] = 100.0 * (t[P3_LAG:] / t[:-P3_LAG] - 1.0)
    share = trailing_share_below(np.abs(chg), P3_WINDOW, P3_MIN)
    p3 = np.where(share >= P3_Q, np.sign(np.nan_to_num(chg)), 0)
    return pd.DataFrame({"available_utc": pd.DatetimeIndex(g["available_utc"]).as_unit("ns"),
                         "gld_chg5_pct": chg, "P3_ETF": p3.astype(int)})


def new_report_mask(report_date: pd.Series) -> np.ndarray:
    """True at the first decision bar at which a new COT report (report_date) is visible."""
    r = pd.to_datetime(report_date)
    prev = r.shift(1)
    return (r.notna() & (prev.isna() | (r != prev))).to_numpy()


def trade_path(o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray, idx: int, exit_idx: int, side: int,
               r: float) -> tuple[float, int, str]:
    """Gross move in R of one trade entered at c[idx]: stop at 1 R checked on bars idx+1..exit_idx (filled at the worse
    of the stop and the bar's open), else exit at c[exit_idx]. Returns (gross R, exit bar, 'stop' | 'time')."""
    entry = c[idx]
    stop = entry - side * r
    for j in range(idx + 1, exit_idx + 1):
        if side > 0 and lo[j] <= stop:
            return side * (min(o[j], stop) - entry) / r, j, "stop"
        if side < 0 and h[j] >= stop:
            return side * (max(o[j], stop) - entry) / r, j, "stop"
    return side * (c[exit_idx] - entry) / r, exit_idx, "time"


def exit_bars(dec_idx: np.ndarray, n_bars: int) -> np.ndarray:
    """For decision k: the bar of decision k + HOLD_DECISIONS, or the last bar when that is past the data."""
    out = np.full(len(dec_idx), n_bars - 1, dtype=int)
    if len(dec_idx) > HOLD_DECISIONS:
        out[:-HOLD_DECISIONS] = dec_idx[HOLD_DECISIONS:]
    return out


def holm(p: dict[str, float]) -> dict[str, float]:
    keys = sorted(p, key=lambda k: p[k])
    m, out, run = len(keys), {}, 0.0
    for i, k in enumerate(keys):
        run = max(run, min(1.0, (m - i) * p[k]))
        out[k] = run
    return out


def block_bootstrap_means(sums: np.ndarray, cnt: np.ndarray, b: int, block: int,
                          rng: np.random.Generator) -> np.ndarray:
    """Circular block bootstrap over weeks: b x k resampled means per trade (sum of R / trade count)."""
    nw = len(cnt)
    nb = int(np.ceil(nw / block))
    starts = rng.integers(0, nw, size=(b, nb))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(b, -1)[:, :nw] % nw
    return sums[idx].sum(axis=1) / np.maximum(cnt[idx].sum(axis=1), 1)[:, None]


def matched_control(years: np.ndarray, sides: np.ndarray, pool_years: np.ndarray, pool_long: np.ndarray,
                    pool_short: np.ndarray, draws: int, rng: np.random.Generator) -> np.ndarray:
    """Mean net R of `draws` random-entry books, each replacing every trade (year y, side s) by a trade of the same side
    entered on a decision bar drawn uniformly from the pool's bars of year y (pool_long / pool_short: each pool bar's
    independent net R as a long / a short)."""
    out = np.zeros(draws)
    total = np.zeros(draws)
    for y in np.unique(years):
        cand = np.flatnonzero(pool_years == y)
        if len(cand) == 0:
            raise ValueError(f"no control pool bars in {y}")
        for s, pool in ((1, pool_long), (-1, pool_short)):
            k = int(np.sum((years == y) & (sides == s)))
            if k:
                pick = cand[rng.integers(0, len(cand), size=(draws, k))]
                total += pool[pick].sum(axis=1)
    out = total / max(1, len(years))
    return out
