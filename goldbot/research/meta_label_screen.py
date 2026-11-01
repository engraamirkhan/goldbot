"""Meta-label filter screen (frozen rule: docs/research/prereg-ml-meta-label-2026-11-01.md): pure logic, no I/O.

A fixed LightGBM regressor on <= 40 point-in-time features predicts each candidate's net R; the top decile of
predictions per test year is the "filtered" book. This module holds the parts the verdict depends on: the 1m barrier
walk, the purged/embargoed year folds and the anchored forward folds, the per-year top-decile selection, the year- and
side-matched random-selection control and the week-block bootstrap. scripts/research/meta_label_screen.py does the I/O.
"""
from __future__ import annotations

from typing import Any

import numpy as np

# ---------------------------------------------------------------- frozen choices (prereg s.4-s.6)
DECISION_FEATURES = (
    "em_atr_cost", "spread_atr", "atr_ratio_14_100", "vol_ts_yz_20_60", "jump_z", "hour_utc", "dow",
    "min_since_newyork_open", "ret_4", "ret_16", "ret_96", "tsmom_score", "im_ldn_ret_atr", "dist_ema50_atr",
    "slope_ema50", "dist_ema200_atr", "adx14", "bb_z_20", "mfi12", "er_30", "donchian_pos_20", "compression_8_96",
    "sess_high_dist_atr", "sess_low_dist_atr", "pdh_dist_atr", "pdl_dist_atr", "sweep_dir",
)
CONTEXT_FEATURES = {"1h": ("adx14",), "4h": ("dist_ema50_atr",), "1d": ("slope_ema20", "atr_ratio_14_100")}
CONTEXT_PREFIX = {"1h": "h1", "4h": "h4", "1d": "d1"}
POSITIONING_FEATURES = ("pos_cot_mm_net_pct_oi", "pos_cot_mm_net_pct_oi_z52", "pos_cot_mm_net_pct_oi_chg4",
                        "pos_gld_tonnes_chg5_pct", "pos_gld_tonnes_chg20_pct")
CALENDAR_FEATURES = ("min_to_next_tier1_pit", "min_since_last_tier1_pit", "tier1_count_today")


def model_columns() -> list[str]:
    """The 40 model columns in a fixed order (side last)."""
    ctx = [f"{CONTEXT_PREFIX[tf]}_{c}" for tf, cols in CONTEXT_FEATURES.items() for c in cols]
    return [*DECISION_FEATURES, *ctx, *POSITIONING_FEATURES, *CALENDAR_FEATURES, "side"]


MODEL_PARAMS: dict[str, Any] = {
    "n_estimators": 300, "learning_rate": 0.03, "num_leaves": 7, "min_child_samples": 200,
    "colsample_bytree": 0.7, "subsample": 0.8, "subsample_freq": 1, "reg_lambda": 10.0,
    "random_state": 20261101, "deterministic": True, "n_jobs": 1, "verbose": -1,
}
TOP_Q = 0.90
PURGE_NS = 26 * 3600 * 10**9          # longest label life (96 x 15m bars, weekend-crossing windows dropped)
EMBARGO_NS = 5 * 86400 * 10**9
FORWARD_YEARS = tuple(range(2012, 2018))
MIN_CANDIDATES, MIN_SELECTED, MIN_YEAR_TRADES, MIN_POS_YEARS = 3000, 300, 20, 5
ALPHA_CONTROL = 0.025                 # Bonferroni over 2 models
SWAP_LONG_USD = 0.60


# ---------------------------------------------------------------- labels
def walk(o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray, k0: np.ndarray, k1: np.ndarray,
         side: np.ndarray, risk: np.ndarray, target_r: np.ndarray, chunk: int = 512
         ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Barrier walk on 1m mid bars. Entry = open[k0]; minutes k0..k1-1 are searched; stop first when one minute holds
    both barriers; stop fills at -1 R, target at +target_r R, else the time exit at close[k1-1].
    Returns (gross R, exit minute index, stop flag)."""
    n = len(k0)
    gross, exit_k, stop = np.zeros(n), np.zeros(n, dtype=np.int64), np.zeros(n, dtype=bool)
    for s in range(0, n, chunk):
        sl = slice(s, min(n, s + chunk))
        a0, a1, sd, r, t = k0[sl], k1[sl], side[sl], risk[sl], target_r[sl]
        span = a1 - a0
        L = int(span.max()) if len(a0) else 1
        j = a0[:, None] + np.arange(L)[None, :]
        valid = j < a1[:, None]
        jj = np.where(valid, j, 0)
        e = o[a0][:, None]
        up = np.where(sd[:, None] > 0, h[jj] - e, e - lo[jj])      # favourable excursion
        dn = np.where(sd[:, None] > 0, e - lo[jj], h[jj] - e)      # adverse excursion
        hit_s = valid & (dn >= r[:, None] - 1e-12)
        hit_t = valid & (up >= (t * r)[:, None] - 1e-12)
        fs = np.where(hit_s.any(1), hit_s.argmax(1), L + 1)
        ft = np.where(hit_t.any(1), hit_t.argmax(1), L + 1)
        st = (fs <= ft) & (fs <= L)
        tg = (~st) & (ft <= L)
        time_r = sd * (c[a1 - 1] - o[a0]) / r
        gross[sl] = np.where(st, -1.0, np.where(tg, t, time_r))
        exit_k[sl] = a0 + np.where(st, fs, np.where(tg, ft, span - 1))
        stop[sl] = st
    return gross, exit_k, stop


def rollovers(t0_ns: np.ndarray, t1_ns: np.ndarray) -> np.ndarray:
    """Long swap units for each holding (t0, t1]: one per 22:00 UTC weekday rollover, Wednesday's counts 3."""
    day = 86400 * 10**9
    roll = 22 * 3600 * 10**9
    out = np.zeros(len(t0_ns))
    first = (t0_ns - roll) // day + 1                       # first rollover strictly after t0, as a day number
    last = (t1_ns - roll) // day                            # last rollover at or before t1
    for i in range(len(t0_ns)):
        d = np.arange(first[i], last[i] + 1)
        dow = (d + 3) % 7                                   # 1970-01-01 was a Thursday (dow 3, Monday = 0)
        out[i] = float(np.where(dow == 2, 3.0, np.where(dow < 5, 1.0, 0.0)).sum())
    return out


def net_r(gross: np.ndarray, stop: np.ndarray, side: np.ndarray, risk: np.ndarray, rt: np.ndarray, os_: np.ndarray,
          nights: np.ndarray, mult: float = 1.0) -> np.ndarray:
    """Net R = gross - (mult x (round trip + overshoot on a stop) + long swap) / risk (prereg s.3)."""
    swap = np.where(side > 0, nights * SWAP_LONG_USD, 0.0)
    return gross - (mult * (rt + np.where(stop, os_, 0.0)) + swap) / risk


# ---------------------------------------------------------------- folds
def year_bounds(year: int) -> tuple[int, int]:
    import pandas as pd
    return (int(pd.Timestamp(f"{year}-01-01", tz="UTC").value), int(pd.Timestamp(f"{year + 1}-01-01", tz="UTC").value))


def purged_year_folds(dec_ns: np.ndarray, exit_ns: np.ndarray, years: np.ndarray
                      ) -> list[tuple[int, np.ndarray, np.ndarray]]:
    """Fold y tests year y; train = rows of other years whose [decision, exit] does not overlap the test year widened by
    PURGE_NS, and that do not start within EMBARGO_NS after it."""
    out = []
    for y in sorted(set(int(v) for v in years)):
        lo, hi = year_bounds(y)
        test = years == y
        overlap = (dec_ns < hi + PURGE_NS) & (exit_ns > lo - PURGE_NS)
        embargo = (dec_ns >= hi) & (dec_ns < hi + EMBARGO_NS)
        out.append((y, ~test & ~overlap & ~embargo, test))
    return out


def forward_folds(exit_ns: np.ndarray, years: np.ndarray) -> list[tuple[int, np.ndarray, np.ndarray]]:
    """Anchored: test year y in FORWARD_YEARS, train = rows whose label exit is before y's start - PURGE_NS."""
    out = []
    for y in FORWARD_YEARS:
        lo, _ = year_bounds(y)
        out.append((y, exit_ns < lo - PURGE_NS, years == y))
    return out


# ---------------------------------------------------------------- selection, control, bootstrap
def top_decile(pred: np.ndarray, years: np.ndarray, scored: np.ndarray | None = None) -> np.ndarray:
    """Per year, rows with prediction >= that year's TOP_Q quantile (among scored rows)."""
    sel = np.zeros(len(pred), dtype=bool)
    ok = np.isfinite(pred) if scored is None else (scored & np.isfinite(pred))
    for y in np.unique(years[ok]):
        m = ok & (years == y)
        sel |= m & (pred >= np.quantile(pred[m], TOP_Q))
    return sel


def matched_control(y: np.ndarray, years: np.ndarray, side: np.ndarray, sel: np.ndarray, pool: np.ndarray,
                    draws: int, seed: int) -> np.ndarray:
    """Mean of y over random selections that match `sel`'s count in every (year, side) cell, drawn without replacement
    from that cell of `pool`. Returns `draws` means."""
    rng = np.random.default_rng(seed)
    cells = []
    for yr in np.unique(years[sel]):
        for sd in (-1, 1):
            k = int((sel & (years == yr) & (side == sd)).sum())
            if k:
                cand = np.flatnonzero(pool & (years == yr) & (side == sd))
                cells.append((cand, k))
    total = sum(k for _, k in cells)
    out = np.empty(draws)
    for d in range(draws):
        s = 0.0
        for cand, k in cells:
            s += float(y[rng.choice(cand, size=k, replace=False)].sum())
        out[d] = s / total
    return out


def week_block_bootstrap(y: np.ndarray, week: np.ndarray, n: int, seed: int) -> np.ndarray:
    """Means of y over `n` resamples of whole weeks (with replacement)."""
    w, inv = np.unique(week, return_inverse=True)
    sums = np.bincount(inv, weights=y, minlength=len(w))
    cnts = np.bincount(inv, minlength=len(w)).astype(float)
    rng = np.random.default_rng(seed)
    out = np.empty(n)
    step = 1000
    for s in range(0, n, step):
        idx = rng.integers(0, len(w), size=(min(step, n - s), len(w)))
        out[s:s + len(idx)] = sums[idx].sum(1) / np.maximum(cnts[idx].sum(1), 1.0)
    return out


def one_at_a_time(dec_ns: np.ndarray, exit_ns: np.ndarray) -> np.ndarray:
    """Mask of rows kept by a one-position book (in decision order, a row enters only after the last exit)."""
    order = np.argsort(dec_ns, kind="stable")
    keep = np.zeros(len(dec_ns), dtype=bool)
    busy = np.iinfo(np.int64).min
    for i in order:
        if dec_ns[i] >= busy:
            keep[i] = True
            busy = exit_ns[i]
    return keep


def verdict(n_cand: int, sel_net: np.ndarray, lb90: float, p_control: float, year_means: dict[int, tuple[float, int]],
            forward_mean: float) -> dict[str, Any]:
    """The prereg s.6 legs (a)-(e) and the overall PASS/FAIL/UNDERPOWERED."""
    pos_years = sum(1 for m, k in year_means.values() if k >= MIN_YEAR_TRADES and m > 0)
    legs = {"a_power": bool(n_cand >= MIN_CANDIDATES and len(sel_net) >= MIN_SELECTED),
            "b_lb90_gt_0": bool(lb90 > 0),
            "c_beats_control": bool(p_control <= ALPHA_CONTROL),
            "d_years": bool(pos_years >= MIN_POS_YEARS),
            "e_forward_gt_0": bool(forward_mean > 0)}
    status = "UNDERPOWERED" if not legs["a_power"] else ("PASS" if all(legs.values()) else "FAIL")
    return {"legs": legs, "positive_years": pos_years, "verdict": status}
