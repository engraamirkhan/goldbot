"""Unit statistics and the pre-registered gates (top30_screen_design, consolidated plan 2026-11-07).

Explore pass (all must hold):
  t_excess   >= 2      t of the DAY-SUMMED gross matched excess (trades sharing a UTC fill day are one observation)
  net        > 0       own mean net R per trade (measured costs)
  gross_cost >= 2      own mean gross R >= 2 x mean cost R
  years      >= 5/8    explore years (2010-2017) with a positive summed excess
  drift_adj  > 0       mean gross R after removing the instrument's window drift over the holding period
  n          >= 300 when every cell is 1d/1w, else >= 1,000
  learn_pre  same sign of the mean excess on 2003-05..2005 where that window has >= 60 trades (else not required)
CONFIRM (explore passers only, read once): BH-FDR q = 0.10 over the explore passers on one-sided day-block bootstrap
  p of the daily excess; >= 5 of the 7 full years 2018-2024 positive; net > 0; 2025-01..09 reported separately.
TEST-OLD (CONFIRM passers only): n >= 60, net > 0, t_excess >= 1.28.
Void: the run is void when the null units' explore passes exceed the binomial 99 % upper bound at 5 %.
DSR is reported at the registry K and at K_cells, never gated."""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from goldbot.research.catalogue.data import DAY
from goldbot.research.catalogue.sim import STRESS

EXPLORE_YEARS = tuple(range(2010, 2018))
CONFIRM_YEARS = tuple(range(2018, 2025))
T_MIN = 2.0
GROSS_COST_MIN = 2.0
YEARS_MIN = 5
N_MIN_DAILY = 300
N_MIN_INTRADAY = 1000
LEARN_PRE_MIN = 60
BH_Q = 0.10
CONFIRM_YEARS_MIN = 5
TEST_OLD_N = 60
TEST_OLD_T = 1.28
BOOT_RESAMPLES = 20_000
BOOT_BLOCK = 5
NULL_RATE = 0.05


def _t(x: np.ndarray) -> float:
    x = np.asarray(x, float)
    if len(x) < 2:
        return float("nan")
    s = float(np.std(x, ddof=1))
    return float(np.mean(x) / (s / math.sqrt(len(x)))) if s > 0 else float("nan")


def daily(tr: pd.DataFrame, col: str = "excess") -> pd.Series:
    if not len(tr):
        return pd.Series(dtype=float)
    day = (tr["t_fill"].to_numpy(np.int64) // DAY)
    return pd.Series(tr[col].to_numpy(float)).groupby(day).sum()


def year_of_day(days: np.ndarray) -> np.ndarray:
    return np.asarray(pd.to_datetime(np.asarray(days, np.int64), unit="D").year)


def summarize(tr: pd.DataFrame) -> dict[str, Any]:
    n = len(tr)
    if n == 0:
        return {"n": 0}
    net = tr["gross"] - tr["cost"]
    d = daily(tr)
    yrs = pd.Series(d.to_numpy(), index=year_of_day(d.index.to_numpy())).groupby(level=0).sum()
    out = {
        "n": int(n), "n_days": int(len(d)),
        "gross": float(tr["gross"].mean()), "cost": float(tr["cost"].mean()), "net": float(net.mean()),
        "net_stress": float((tr["gross"] - STRESS * tr["cost"]).mean()),
        "excess": float(tr["excess"].mean()), "ctrl_gross": float(tr["ctrl_gross"].mean()),
        "t_excess": _t(d.to_numpy()), "t_net": _t(daily(tr.assign(net=net), "net").to_numpy()),
        "drift_adj": float(tr["drift_adj"].mean()),
        "long_share": float((tr["side"] > 0).mean()),
        "years": {int(k): float(v) for k, v in zip(yrs.index.to_numpy(), yrs.to_numpy())},
        "by_cell": {f"{i}|{t}": {"n": int(len(g)), "excess": float(g["excess"].mean()),
                                 "net": float((g["gross"] - g["cost"]).mean())}
                    for (i, t), g in tr.groupby(["inst", "tf"])},
        "median_hold_nights": float(tr["nights"].median()),
    }
    return out


def explore_gates(s: dict[str, Any], timeframes: list[str], pre: dict[str, Any] | None) -> dict[str, bool]:
    if not s.get("n"):
        return {"n": False}
    daily_only = all(tf in ("1d", "1w") for tf in timeframes)
    yrs_pos = sum(1 for y in EXPLORE_YEARS if s["years"].get(y, 0.0) > 0)
    g = {
        "t_excess": bool(np.isfinite(s["t_excess"]) and s["t_excess"] >= T_MIN),
        "net": s["net"] > 0,
        "gross_cost": s["gross"] >= GROSS_COST_MIN * s["cost"] and s["gross"] > 0,
        "years": yrs_pos >= YEARS_MIN,
        "drift_adj": s["drift_adj"] > 0,
        "n": s["n"] >= (N_MIN_DAILY if daily_only else N_MIN_INTRADAY),
    }
    if pre and pre.get("n", 0) >= LEARN_PRE_MIN:
        g["learn_pre"] = np.sign(pre["excess"]) == np.sign(s["excess"])
    return g


def block_boot_p(daily_x: np.ndarray, *, resamples: int = BOOT_RESAMPLES, block: int = BOOT_BLOCK,
                 seed: int = 0) -> float:
    """One-sided p of mean > 0 by a moving-block bootstrap of the centred daily series."""
    x = np.asarray(daily_x, float)
    n = len(x)
    if n < 2 * block:
        return 1.0
    obs = float(x.mean())
    x0 = x - obs
    rng = np.random.default_rng(seed)
    nb = int(math.ceil(n / block))
    hits = 0
    done = 0
    while done < resamples:
        k = min(2000, resamples - done)
        starts = rng.integers(0, n - block + 1, (k, nb))
        idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(k, -1)[:, :n]
        hits += int((x0[idx].mean(axis=1) >= obs).sum())
        done += k
    return (1 + hits) / (resamples + 1)


def bh(p: np.ndarray, q: float = BH_Q) -> np.ndarray:
    p = np.asarray(p, float)
    m = len(p)
    if not m:
        return np.zeros(0, bool)
    order = np.argsort(p, kind="stable")
    ok = p[order] <= np.arange(1, m + 1) * q / m
    out = np.zeros(m, bool)
    if ok.any():
        out[order[: int(np.flatnonzero(ok).max()) + 1]] = True
    return out


def confirm_gates(s: dict[str, Any]) -> dict[str, bool]:
    if not s.get("n"):
        return {"n": False}
    yrs_pos = sum(1 for y in CONFIRM_YEARS if s["years"].get(y, 0.0) > 0)
    return {"years": yrs_pos >= CONFIRM_YEARS_MIN, "net": s["net"] > 0}


def test_old_gates(s: dict[str, Any]) -> dict[str, bool]:
    if not s.get("n"):
        return {"n": False}
    return {"n": s["n"] >= TEST_OLD_N, "net": s["net"] > 0,
            "t_excess": bool(np.isfinite(s["t_excess"]) and s["t_excess"] >= TEST_OLD_T)}


def null_void_bound(n_null: int, rate: float = NULL_RATE) -> int:
    """The largest null pass count NOT voiding the run: the binomial 99 % quantile at `rate`."""
    from scipy.stats import binom
    return int(binom.ppf(0.99, n_null, rate))


def deflated_sharpe(daily_x: np.ndarray, k: int) -> dict[str, float]:
    """Bailey & Lopez de Prado (2014) DSR of the daily excess against the expected max Sharpe of k null trials
    (per-day units; trial-Sharpe variance taken as the null 1/T). Reported, never gated."""
    from scipy.stats import norm
    x = np.asarray(daily_x, float)
    T = len(x)
    if T < 10 or k < 1:
        return {"sr": float("nan"), "sr0": float("nan"), "dsr": float("nan")}
    sd = float(x.std(ddof=1))
    if sd <= 0:
        return {"sr": float("nan"), "sr0": float("nan"), "dsr": float("nan")}
    sr = float(x.mean() / sd)
    g = 0.5772156649
    v = 1.0 / T
    sr0 = math.sqrt(v) * ((1 - g) * norm.ppf(1 - 1 / k) + g * norm.ppf(1 - 1 / (k * math.e))) if k > 1 else 0.0
    from scipy.stats import kurtosis, skew
    sk = float(skew(x))
    ku = float(kurtosis(x, fisher=False))
    den = math.sqrt(max(1e-12, 1 - sk * sr + (ku - 1) / 4 * sr * sr))
    dsr = float(norm.cdf((sr - sr0) * math.sqrt(T - 1) / den))
    return {"sr": sr, "sr0": sr0, "dsr": dsr}
