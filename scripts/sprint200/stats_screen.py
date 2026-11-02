"""Sprint-200 lane `stats`: the 33 statistical and ML rules of docs/research/sprint200/stats-prereg.md (frozen by
commit 07426758 before any outcome was computed).

    uv run python scripts/sprint200/stats_screen.py --window explore --raw ../../raw --out ~/tmp/s200stats/explore.json
    uv run python scripts/sprint200/stats_screen.py --window explore --leak-only        (leakage tests, no outcome)
    uv run python scripts/sprint200/stats_screen.py --window confirm --rules 07,18 ...  (explore passers only, once)

Explore loads 2010-01-01..2017-12-31 only; confirm loads 2010-01-01..2025-09-30 (the fits and warm-up read explore
data; decisions and trades are 2018-01-01..2025-09-30). No row at or after 2025-10-01 is ever loaded: the parquet read
filters it out and the frame is asserted. Writes statistics only (JSON; market data and trade rows are never committed).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections.abc import Callable
from multiprocessing import get_context
from pathlib import Path
from typing import Any

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_v, "1")          # 2 worker processes at most share the Mac (brief rule 6): no BLAS threads

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from goldbot.data.calendar import DEFAULT_SESSIONS  # noqa: E402
from goldbot.data.timeutil import epoch_ns, feature_day, feature_day_close  # noqa: E402
from goldbot.labels.triple_barrier import rollover_nights  # noqa: E402
from goldbot.research import arms as A  # noqa: E402
from goldbot.research.metrics import deflated_sharpe  # noqa: E402

HOLDOUT = pd.Timestamp("2025-10-01", tz="UTC")
EXPLORE = (pd.Timestamp("2010-01-01", tz="UTC"), pd.Timestamp("2018-01-01", tz="UTC"))
CONFIRM = (pd.Timestamp("2018-01-01", tz="UTC"), HOLDOUT)
COST_VERSION = "cor-2026-10-31.2"
SEED = 20261102
N_TWIN = 4
K_TOTAL = 3229
K_STRESS = 3500
MIN = 60_000_000_000
HOUR = 60 * MIN
DAY = 24 * HOUR
MAX_ENTRY_DELAY = 15 * MIN
STOP_ATR = 1.5
SWAP_LONG_PER_OZ = -0.60          # shipped CostSettings: -60 USD/lot/night, 100 oz a lot
SWAP_SHORT_PER_OZ = 0.0
SESSION_CODES = {"asia": 0, "london": 1, "newyork": 2}
BRACKET = {"1h": (2.0, 24 * HOUR), "4h": (2.0, 72 * HOUR), "1d": (math.nan, 7 * DAY)}
ML_HOLD = {"1h": 6 * HOUR, "4h": 24 * HOUR, "1d": 7 * DAY}
ML_H = {"1h": 6, "4h": 6, "1d": 5}
TF_NS = {"1h": HOUR, "4h": 4 * HOUR}
# amendment W1 (owner 2026-11-02): entry years of the pre-2010 windows; test_old is read once, confirm-passers only
OLD_YEARS = {"test_old": (2004, 2006, 2008), "learn_old": (2003, 2005, 2007, 2009)}
OLD_LOAD_FROM = pd.Timestamp("2002-01-01", tz="UTC")


def window_bounds(window: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """[first decision, end of trade paths) of a window; the pre-2010 windows trade inside 2003..2009 only."""
    if window == "explore":
        return EXPLORE
    if window == "confirm":
        return CONFIRM
    return pd.Timestamp("2003-01-01", tz="UTC"), EXPLORE[0]


def in_window(t: np.ndarray, window: str) -> np.ndarray:
    lo, hi = window_bounds(window)
    ok = (t >= lo.value) & (t < hi.value)
    if window in OLD_YEARS:
        yrs = pd.DatetimeIndex(pd.to_datetime(t, utc=True)).year.to_numpy()
        ok &= np.isin(yrs, OLD_YEARS[window])
    return ok


# ================================================================ data
def load_1m(raw: Path, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Mid 1m bars in [start, end), end <= 2025-10-01; rows past `end` are filtered at the parquet read."""
    assert end <= HOLDOUT, "the holdout is sealed"
    frames = []
    for y in range(start.year, end.year + 1):
        f = raw / f"xauusd_1m_dukascopy_{y}.parquet"
        if not f.exists():
            continue
        cols = ["ts_utc", "bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high", "ask_low",
                "ask_close", "dq_flag"]
        d = pd.read_parquet(f, columns=cols, filters=[("ts_utc", ">=", start), ("ts_utc", "<", end)])
        frames.append(d)
    b = pd.concat(frames, ignore_index=True)
    b = b[~b["dq_flag"].fillna("").astype(str).str.startswith("error")]
    b["ts_utc"] = pd.to_datetime(b["ts_utc"], utc=True)
    b = b.drop_duplicates("ts_utc").sort_values("ts_utc").reset_index(drop=True)
    ts = pd.DatetimeIndex(b["ts_utc"])
    assert ts.max() < end and ts.max() < HOLDOUT, "a row at or after the window end was read"
    out = pd.DataFrame({"t": epoch_ns(ts)})
    for k in ("open", "high", "low", "close"):
        out[k] = (b[f"bid_{k}"].to_numpy(float) + b[f"ask_{k}"].to_numpy(float)) / 2
    return out


class Minutes:
    """The 1m arrays every simulation reads."""

    def __init__(self, m: pd.DataFrame):
        self.t = m["t"].to_numpy(np.int64)
        self.o = m["open"].to_numpy(float)
        self.h = m["high"].to_numpy(float)
        self.l = m["low"].to_numpy(float)
        self.c = m["close"].to_numpy(float)
        ts = pd.DatetimeIndex(pd.to_datetime(self.t, utc=True))
        lab = DEFAULT_SESSIONS.session_label(ts)
        self.sess = np.array([SESSION_CODES[str(x)] for x in lab], dtype=np.int64)
        self.day = self.t // DAY
        key = self.day * 4 + self.sess
        self.order = np.argsort(key, kind="stable")
        sk = key[self.order]
        self.ukeys, self.kstart = np.unique(sk, return_index=True)
        self.kend = np.append(self.kstart[1:], len(sk))


def tf_bars(m: pd.DataFrame, tf: str) -> pd.DataFrame:
    """OHLC on the UTC clock (1h, 4h) or the feature day (1d); `vis` = when the bar is visible (ns)."""
    if tf == "1d":
        ts = pd.DatetimeIndex(pd.to_datetime(m["t"].to_numpy(), utc=True))
        fd = feature_day(ts)
        key = epoch_ns(pd.DatetimeIndex(fd).tz_localize("UTC"))
        g = m.assign(k=key).groupby("k", sort=True)
        out = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
                    last=("t", "max")).reset_index()
        vis = feature_day_close(pd.DatetimeIndex(pd.to_datetime(out["k"].to_numpy())).tz_localize(None))
        out["vis"] = epoch_ns(vis)
    else:
        step = TF_NS[tf]
        key = (m["t"].to_numpy(np.int64) // step) * step
        g = m.assign(k=key).groupby("k", sort=True)
        out = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
                    last=("t", "max")).reset_index()
        out["vis"] = out["k"] + step
    return out.rename(columns={"k": "key"}).reset_index(drop=True)


# ================================================================ estimators
def atr14(b: pd.DataFrame) -> np.ndarray:
    h, lo, c = b["high"].to_numpy(), b["low"].to_numpy(), b["close"].to_numpy()
    pc = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.c_[h - lo, np.abs(h - pc), np.abs(lo - pc)], axis=1)
    out = np.full(len(tr), np.nan)
    if len(tr) < 14:
        return out
    out[13] = tr[:14].mean()
    for i in range(14, len(tr)):
        out[i] = out[i - 1] + (tr[i] - out[i - 1]) / 14
    return out


def kalman_llt(logc: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Local linear trend: (slope z, innovation z) per bar, R_t = variance of the last 100 1-bar log returns."""
    n = len(logc)
    r = np.r_[np.nan, np.diff(logc)]
    rv = pd.Series(r).rolling(100, min_periods=100).var().to_numpy()
    sz, iz = np.full(n, np.nan), np.full(n, np.nan)
    lev, slo = logc[0], 0.0
    p11, p12, p22 = 1e-4, 0.0, 1e-6
    for i in range(1, n):
        rr = rv[i] if np.isfinite(rv[i]) else (np.nanvar(r[1:i + 1]) if i > 2 else 1e-6)
        rr = max(float(rr), 1e-12)
        # predict
        lev_p, slo_p = lev + slo, slo
        a11 = p11 + 2 * p12 + p22 + 0.1 * rr
        a12 = p12 + p22
        a22 = p22 + 0.001 * rr
        s = a11 + rr
        v = logc[i] - lev_p
        k1, k2 = a11 / s, a12 / s
        lev, slo = lev_p + k1 * v, slo_p + k2 * v
        p11, p12, p22 = (1 - k1) * a11, (1 - k1) * a12, a22 - k2 * a12
        if np.isfinite(rv[i]):
            sz[i] = slo / math.sqrt(max(p22, 1e-30))
            iz[i] = v / math.sqrt(s)
    return sz, iz


def pct_rank(x: np.ndarray, n: int) -> np.ndarray:
    """Rank of x[i] among the previous n values (exclusive), 0..100; NaN until n prior finite values."""
    out = np.full(len(x), np.nan)
    for i in range(n, len(x)):
        w = x[i - n:i]
        if not np.isfinite(x[i]) or not np.all(np.isfinite(w)):
            continue
        out[i] = 100.0 * float(np.mean(w < x[i]))
    return out


def pct_rank_finite(x: np.ndarray, n: int) -> np.ndarray:
    """Defect fix after the explore run (rules 18, 21, 23, 33 had 0 decisions): the realised-measure series have
    missing days/bars (holidays, < 50 five-minute returns, thin weekend windows), and `pct_rank` returned NaN for every
    window holding one missing value, so these rules never fired. Rank of x[i] among the FINITE values of the previous n
    (exclusive), 0..100, NaN when x[i] is missing or fewer than n // 2 of them are finite. `pct_rank` is kept unchanged
    for rules 04, 05, 22 (their series have no gaps; their results are untouched)."""
    out = np.full(len(x), np.nan)
    for i in range(n, len(x)):
        w = x[i - n:i]
        w = w[np.isfinite(w)]
        if not np.isfinite(x[i]) or len(w) < n // 2:
            continue
        out[i] = 100.0 * float(np.mean(w < x[i]))
    return out


def vr(r: pd.Series, q: int, w: int) -> np.ndarray:
    rq = r.rolling(q, min_periods=q).sum()
    return (rq.rolling(w - q + 1, min_periods=w - q + 1).var() / (q * r.rolling(w, min_periods=w).var())).to_numpy()


def ac1(r: pd.Series, w: int) -> np.ndarray:
    return r.rolling(w, min_periods=w).corr(r.shift(1)).to_numpy()


def hmm_fit(x: np.ndarray, iters: int = 200) -> dict[str, np.ndarray]:
    """2-state Gaussian HMM, Baum-Welch with scaling; init by the halves of |x|."""
    x = x[np.isfinite(x)]
    med = np.median(np.abs(x))
    lo_, hi_ = x[np.abs(x) <= med], x[np.abs(x) > med]
    mu = np.array([lo_.mean(), hi_.mean()])
    var = np.array([max(lo_.var(), 1e-12), max(hi_.var(), 1e-12)])
    pi = np.array([0.5, 0.5])
    A_ = np.array([[0.95, 0.05], [0.05, 0.95]])
    n = len(x)
    last_ll = -np.inf
    for _ in range(iters):
        b = np.exp(-0.5 * (x[:, None] - mu) ** 2 / var) / np.sqrt(2 * np.pi * var) + 1e-300
        al = np.zeros((n, 2))
        sc = np.zeros(n)
        al[0] = pi * b[0]
        sc[0] = al[0].sum()
        al[0] /= sc[0]
        for t in range(1, n):
            al[t] = (al[t - 1] @ A_) * b[t]
            sc[t] = al[t].sum()
            al[t] /= sc[t]
        be = np.ones((n, 2))
        for t in range(n - 2, -1, -1):
            be[t] = (A_ @ (b[t + 1] * be[t + 1])) / sc[t + 1]
        g = al * be
        g /= g.sum(axis=1, keepdims=True)
        xi = np.einsum("ti,ij,tj->ij", al[:-1], A_, b[1:] * be[1:] / sc[1:, None])
        ll = float(np.log(sc).sum())
        if abs(ll - last_ll) < 1e-8 * max(1.0, abs(ll)):
            break
        last_ll = ll
        pi = g[0]
        A_ = xi / xi.sum(axis=1, keepdims=True)
        mu = (g * x[:, None]).sum(0) / g.sum(0)
        var = np.maximum((g * (x[:, None] - mu) ** 2).sum(0) / g.sum(0), 1e-12)
    low = int(np.argmin(var))
    return {"mu": mu, "var": var, "A": A_, "pi": pi, "low": np.array([low])}


def hmm_filter(x: np.ndarray, p: dict[str, np.ndarray]) -> np.ndarray:
    """Forward-filtered P(low-variance state) at each t (uses x[0..t] only); NaN where x is NaN (state carried)."""
    mu, var, A_ = p["mu"], p["var"], p["A"]
    low = int(p["low"][0])
    out = np.full(len(x), np.nan)
    a = p["pi"].copy()
    for t in range(len(x)):
        if not np.isfinite(x[t]):
            continue
        b = np.exp(-0.5 * (x[t] - mu) ** 2 / var) / np.sqrt(2 * np.pi * var) + 1e-300
        a = (a @ A_) * b
        a /= a.sum()
        out[t] = a[low]
    return out


def realised_daily(m: pd.DataFrame) -> pd.DataFrame:
    """Per feature day, from 5m mid log returns inside it: rv, bv, rskew, rs+, rs-, BNS ratio jump z, day return."""
    t = m["t"].to_numpy(np.int64)
    k5 = (t // (5 * MIN)) * (5 * MIN)
    c5 = m.assign(k=k5).groupby("k", sort=True)["close"].last()
    ts = pd.DatetimeIndex(pd.to_datetime(c5.index.to_numpy(), utc=True))
    fd = epoch_ns(pd.DatetimeIndex(feature_day(ts)).tz_localize("UTC"))
    lc = np.log(c5.to_numpy())
    r = np.r_[np.nan, np.diff(lc)]
    same = np.r_[False, fd[1:] == fd[:-1]]
    r[~same] = np.nan                              # never across a feature-day boundary
    df = pd.DataFrame({"fd": fd, "r": r}).dropna()
    rows = []
    mu1 = math.sqrt(2 / math.pi)
    mu43 = 2 ** (2 / 3) * math.gamma(7 / 6) / math.gamma(0.5)
    for d, g in df.groupby("fd", sort=True):
        x = g["r"].to_numpy()
        n = len(x)
        if n < 50:
            continue
        rv = float(np.sum(x ** 2))
        ax = np.abs(x)
        bv = float(mu1 ** -2 * np.sum(ax[1:] * ax[:-1]))
        tq = float(n * mu43 ** -3 * np.sum((ax[2:] * ax[1:-1] * ax[:-2]) ** (4 / 3)))
        rsk = float(math.sqrt(n) * np.sum(x ** 3) / rv ** 1.5) if rv > 0 else 0.0
        theta = (math.pi / 2) ** 2 + math.pi - 5
        jz = ((rv - bv) / rv) / math.sqrt(theta / n * max(1.0, tq / bv ** 2)) if rv > 0 and bv > 0 else 0.0
        rows.append({"fd": d, "rv": rv, "bv": bv, "rskew": rsk, "rsp": float(np.sum(x[x > 0] ** 2)),
                     "rsn": float(np.sum(x[x < 0] ** 2)), "jz": jz, "ret": float(np.sum(x))})
    return pd.DataFrame(rows)


def rskew_trailing(m: pd.DataFrame, vis: np.ndarray) -> np.ndarray:
    """Realised skew of 5m mid log returns in (vis - 24 h, vis] for each decision time `vis` (ns)."""
    t = m["t"].to_numpy(np.int64)
    k5 = (t // (5 * MIN)) * (5 * MIN)
    c5 = m.assign(k=k5).groupby("k", sort=True)["close"].last()
    tk = c5.index.to_numpy(np.int64) + 5 * MIN       # a 5m bar is known at its close
    r = np.r_[0.0, np.diff(np.log(c5.to_numpy()))]
    gap = np.r_[True, np.diff(tk) > 5 * MIN]
    r[gap] = 0.0                                     # no return across a gap (weekend, missing minutes)
    s1, s2, s3 = np.r_[0, np.cumsum(np.ones_like(r))], np.r_[0, np.cumsum(r ** 2)], np.r_[0, np.cumsum(r ** 3)]
    hi = np.searchsorted(tk, vis, side="right")
    lo = np.searchsorted(tk, vis - DAY, side="right")
    n = s1[hi] - s1[lo]
    rv = s2[hi] - s2[lo]
    sk = s3[hi] - s3[lo]
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.sqrt(n) * sk / rv ** 1.5
    out[(n < 100) | (rv <= 0)] = np.nan
    return out


# ================================================================ features (prereg s.4)
def features(b: pd.DataFrame, tf: str) -> pd.DataFrame:
    c, h, lo, o = (b[k].to_numpy(float) for k in ("close", "high", "low", "open"))
    lc = np.log(c)
    r = pd.Series(np.r_[np.nan, np.diff(lc)])
    atr = b["atr"].to_numpy()
    an = atr / c
    f: dict[str, np.ndarray] = {}
    for k in (1, 2, 4, 8, 16, 32, 64):
        f[f"r{k}"] = (lc - pd.Series(lc).shift(k).to_numpy()) / an
    f["rvratio"] = (r.rolling(8).std() / r.rolling(64).std()).to_numpy()
    f["atrn"] = an
    f["vr4"] = vr(r, 4, 64)
    f["ac1"] = ac1(r, 64)
    f["skew"] = r.rolling(64).skew().to_numpy()
    f["kurt"] = r.rolling(64).kurt().to_numpy()
    cs = pd.Series(c)
    for k in (20, 50, 200):
        f[f"sma{k}"] = (c - cs.rolling(k).mean().to_numpy()) / atr
    for k in (20, 55):
        hh = pd.Series(h).rolling(k).max().to_numpy()
        ll = pd.Series(lo).rolling(k).min().to_numpy()
        with np.errstate(invalid="ignore", divide="ignore"):
            f[f"don{k}"] = (c - ll) / (hh - ll)
    f["kz"] = b["kz"].to_numpy()
    rng = h - lo
    with np.errstate(invalid="ignore", divide="ignore"):
        f["rng"] = rng / atr
        f["cloc"] = np.where(rng > 0, (c - lo) / rng, 0.5)
        f["uw"] = np.where(rng > 0, (h - np.maximum(o, c)) / rng, 0.0)
        f["lw"] = np.where(rng > 0, (np.minimum(o, c) - lo) / rng, 0.0)
    dec = pd.DatetimeIndex(pd.to_datetime(b["vis"].to_numpy(), utc=True))
    if tf != "1d":
        hr = dec.hour.to_numpy() + dec.minute.to_numpy() / 60
        f["hs"], f["hc"] = np.sin(2 * np.pi * hr / 24), np.cos(2 * np.pi * hr / 24)
    wd = dec.weekday.to_numpy()
    f["ws"], f["wc"] = np.sin(2 * np.pi * wd / 7), np.cos(2 * np.pi * wd / 7)
    out = pd.DataFrame(f)
    assert out.shape[1] <= 40
    return out


# ================================================================ all decision signals
def prepare(m: pd.DataFrame) -> dict[str, Any]:
    ctx: dict[str, Any] = {}
    for tf in ("1h", "4h", "1d"):
        b = tf_bars(m, tf)
        b["atr"] = atr14(b)
        b["kz"], b["iz"] = kalman_llt(np.log(b["close"].to_numpy()))
        b["r"] = np.r_[np.nan, np.diff(np.log(b["close"].to_numpy()))]
        ctx[tf] = b
    ctx["rd"] = realised_daily(m)
    ctx["rsk4"] = rskew_trailing(m, ctx["4h"]["vis"].to_numpy(np.int64))
    return ctx


def _dec(b: pd.DataFrame, mask: np.ndarray, side: np.ndarray, tf: str, tgt: float | None = None,
         hold: int | None = None) -> pd.DataFrame:
    d_tgt, d_hold = BRACKET[tf]
    mask = np.asarray(mask, dtype=bool) & np.isfinite(b["atr"].to_numpy()) & (np.asarray(side) != 0)
    return pd.DataFrame({"t": b["vis"].to_numpy(np.int64)[mask], "side": np.asarray(side, dtype=int)[mask],
                         "risk": STOP_ATR * b["atr"].to_numpy()[mask],
                         "tgt": (d_tgt if tgt is None else tgt), "hold": (d_hold if hold is None else hold)})


def sgn(x: np.ndarray) -> np.ndarray:
    return np.sign(np.nan_to_num(np.asarray(x, dtype=float))).astype(int)


def cross_up(x: np.ndarray, lvl: float) -> np.ndarray:
    p = np.r_[np.nan, x[:-1]]
    return (x > lvl) & (p <= lvl)


def cross_dn(x: np.ndarray, lvl: float) -> np.ndarray:
    p = np.r_[np.nan, x[:-1]]
    return (x < lvl) & (p >= lvl)


def transition(v: np.ndarray) -> np.ndarray:
    """+1/-1 on the bar where the vote state first becomes all +1/-1 (v = +1/-1/0), else 0."""
    p = np.r_[0, v[:-1]]
    return np.where((v != 0) & (v != p), v, 0)


def year_folds(t: np.ndarray, first: int, window: str) -> list[tuple[int, int, int, int]]:
    """(fit_lo, fit_end, test_lo, test_hi) in ns per fold. Explore: annual expanding walk-forward from `first`.
    Confirm and the pre-2010 windows (amendment W1): one fold fit on the explore window only (2010-2017), frozen."""
    e0, e1 = int(EXPLORE[0].value), int(EXPLORE[1].value)
    if window == "confirm":
        return [(e0, e1, e1, int(CONFIRM[1].value))]
    if window in OLD_YEARS:
        return [(e0, e1, int(pd.Timestamp("2000-01-01", tz="UTC").value), e0)]
    return [(e0, int(pd.Timestamp(f"{y}-01-01", tz="UTC").value), int(pd.Timestamp(f"{y}-01-01", tz="UTC").value),
             int(pd.Timestamp(f"{y + 1}-01-01", tz="UTC").value)) for y in range(first, 2018)]


def hmm_rule(b: pd.DataFrame, window: str) -> np.ndarray:
    """Forward-filtered P(low) per bar, from the fold's parameters (fit on returns of bars visible before the fold)."""
    x = b["r"].to_numpy()
    vis = b["vis"].to_numpy(np.int64)
    p = np.full(len(b), np.nan)
    for fit_lo, fit_end, test_lo, test_end in year_folds(vis, 2011, window):
        fit = x[(vis >= fit_lo) & (vis < fit_end) & np.isfinite(x)]
        if len(fit) < 200:
            continue
        par = hmm_fit(fit)
        filt = hmm_filter(x, par)
        sel = (vis >= test_lo) & (vis < test_end)
        p[sel] = filt[sel]
    return p


def ml_rule(b: pd.DataFrame, tf: str, kind: str, window: str, shuffle: bool = False) -> tuple[np.ndarray, dict]:
    """Side per bar from the fold model (prereg s.3); with `shuffle` the labels are permuted (leakage test)."""
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.preprocessing import StandardScaler
    hz = ML_H[tf]
    X = features(b, tf)
    c = b["close"].to_numpy()
    vis = b["vis"].to_numpy(np.int64)
    fwd = np.r_[c[hz:], np.full(hz, np.nan)]
    lab_end = np.r_[vis[hz:], np.full(hz, np.iinfo(np.int64).max)]
    y = (fwd > c).astype(float)
    y[~np.isfinite(fwd)] = np.nan
    ok = X.notna().all(axis=1).to_numpy() & np.isfinite(b["atr"].to_numpy())
    side = np.zeros(len(b), dtype=int)
    aucs = []
    rng = np.random.default_rng(SEED)
    for fit_lo, fit_end, test_lo, test_end in year_folds(vis, 2012, window):
        fit = ok & np.isfinite(y) & (lab_end < fit_end - DAY) & (vis < fit_end - DAY) & (vis >= fit_lo)
        test = ok & (vis >= test_lo) & (vis < test_end)
        if fit.sum() < 200 or test.sum() == 0:
            continue
        yf = y[fit].copy()
        if shuffle:
            yf = rng.permutation(yf)
        sc = StandardScaler().fit(X[fit])
        if kind == "lr":
            mdl: Any = LogisticRegression(C=1.0, max_iter=2000)
        else:
            mdl = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_leaf_nodes=15,
                                                 min_samples_leaf=200, l2_regularization=1.0, random_state=0)
        mdl.fit(sc.transform(X[fit]), yf)
        pin = mdl.predict_proba(sc.transform(X[fit]))[:, 1]
        q15, q85 = np.quantile(pin, [0.15, 0.85])
        pt = mdl.predict_proba(sc.transform(X[test]))[:, 1]
        idx = np.flatnonzero(test)
        side[idx] = np.where(pt >= q85, 1, np.where(pt <= q15, -1, 0))
        yt = y[test]
        good = np.isfinite(yt)
        if good.sum() > 50 and len(np.unique(yt[good])) == 2:
            aucs.append(float(roc_auc_score(yt[good], pt[good])))
    return side, {"auc_folds": aucs, "auc_mean": float(np.mean(aucs)) if aucs else None}


def kmeans_rule(b: pd.DataFrame, window: str) -> np.ndarray:
    from sklearn.cluster import KMeans
    r = b["r"]
    c = b["close"].to_numpy()
    vis = b["vis"].to_numpy(np.int64)
    sd60 = r.rolling(60).std()
    X = pd.DataFrame({"lrv": np.log(r.rolling(20).std()), "z20": (np.log(c) - np.log(pd.Series(c).shift(20)))
                      / (sd60 * math.sqrt(20)), "ac": ac1(r, 60), "vr": vr(r, 5, 250), "sk": r.rolling(60).skew()})
    fwd = np.r_[np.log(c[5:]) - np.log(c[:-5]), np.full(5, np.nan)]
    lab_end = np.r_[vis[5:], np.full(5, np.iinfo(np.int64).max)]
    ok = X.notna().all(axis=1).to_numpy()
    side = np.zeros(len(b), dtype=int)
    for fit_lo, fit_end, test_lo, test_end in year_folds(vis, 2012, window):
        fit = ok & np.isfinite(fwd) & (lab_end < fit_end - DAY) & (vis < fit_end - DAY) & (vis >= fit_lo)
        test = ok & (vis >= test_lo) & (vis < test_end)
        if fit.sum() < 100 or test.sum() == 0:
            continue
        mu, sd = X[fit].mean(), X[fit].std()
        km = KMeans(n_clusters=4, n_init=10, random_state=0).fit(((X[fit] - mu) / sd).to_numpy())
        lf = km.labels_
        dirn = {}
        for k in range(4):
            v = fwd[fit][lf == k]
            t = v.mean() / (v.std(ddof=1) / math.sqrt(len(v))) if len(v) > 2 and v.std() > 0 else 0.0
            dirn[k] = 1 if t >= 2 else (-1 if t <= -2 else 0)
        lt = km.predict(((X[test] - mu) / sd).to_numpy())
        side[np.flatnonzero(test)] = [dirn[int(k)] for k in lt]
    return side


def daily_ctx(ctx: dict[str, Any]) -> pd.DataFrame:
    """The 1d bars joined (on the feature day key) with that day's realised measures; both are visible at the close."""
    b = ctx["1d"].copy()
    rd = ctx["rd"].rename(columns={"fd": "key"})
    return b.merge(rd, on="key", how="left", suffixes=("", "_rd"))


def intraday_mom(m: pd.DataFrame, ctx: dict[str, Any]) -> pd.DataFrame:
    """Rule 17: sign(mid 08:50 NY - prior day's mid 13:30 NY), entry 13:00 NY, exit at the 13:29 minute's close."""
    t = m["t"].to_numpy(np.int64)
    c = m["close"].to_numpy()
    ts = pd.DatetimeIndex(pd.to_datetime(t, utc=True)).tz_convert("America/New_York")
    days = pd.DatetimeIndex(np.unique(ts.normalize().tz_localize(None)))
    days = days[days.weekday < 5]

    def at(local: pd.DatetimeIndex) -> np.ndarray:
        u = epoch_ns(local.tz_localize("America/New_York", ambiguous="NaT", nonexistent="shift_forward"))
        k = np.searchsorted(t, u, side="left") - 1          # the last bar with ts < u, closed at <= u
        ok = (k >= 0) & (u - t[np.maximum(k, 0)] <= 5 * MIN)
        return np.where(ok, c[np.maximum(k, 0)], np.nan), u
    p0850, _ = at(days + pd.Timedelta(hours=8, minutes=50))
    p1330, _ = at(days + pd.Timedelta(hours=13, minutes=30))
    _, u1300 = at(days + pd.Timedelta(hours=13))
    prev1330 = np.r_[np.nan, p1330[:-1]]
    sig = sgn(p0850 - prev1330)
    h1 = ctx["1h"]
    k = np.searchsorted(h1["vis"].to_numpy(np.int64), u1300, side="right") - 1
    atr = np.where(k >= 0, h1["atr"].to_numpy()[np.maximum(k, 0)], np.nan)
    ok = np.isfinite(atr) & np.isfinite(p0850) & np.isfinite(prev1330) & (sig != 0)
    return pd.DataFrame({"t": u1300[ok], "side": sig[ok], "risk": STOP_ATR * atr[ok], "tgt": math.nan,
                         "hold": 30 * MIN})


def all_signals(m: pd.DataFrame, window: str, rules: list[str] | None = None,
                diag: dict | None = None) -> dict[str, pd.DataFrame]:
    ctx = prepare(m)
    h1, h4, d1 = ctx["1h"], ctx["4h"], daily_ctx(ctx)
    out: dict[str, Callable[[], pd.DataFrame]] = {}
    diag = diag if diag is not None else {}

    def z_ret(b: pd.DataFrame, k: int, sdw: int) -> np.ndarray:
        r = b["r"]
        lc = np.log(b["close"].to_numpy())
        return (lc - pd.Series(lc).shift(k).to_numpy()) / (r.rolling(sdw).std().to_numpy() * math.sqrt(k))

    # --- regime
    def s01() -> pd.DataFrame:
        p = hmm_rule(d1, window)
        lc = np.log(d1["close"].to_numpy())
        r20 = lc - pd.Series(lc).shift(20).to_numpy()
        return _dec(d1, p >= 0.8, sgn(r20), "1d")

    def s02() -> pd.DataFrame:
        p = hmm_rule(d1, window)
        z5 = z_ret(d1, 5, 60)
        return _dec(d1, ((1 - p) >= 0.8) & (np.abs(z5) >= 1.5), -sgn(z5), "1d")

    def s03() -> pd.DataFrame:
        p = hmm_rule(h4, window)
        lc = np.log(h4["close"].to_numpy())
        return _dec(h4, p >= 0.8, sgn(lc - pd.Series(lc).shift(24).to_numpy()), "4h")

    def s04() -> pd.DataFrame:
        rv20 = d1["r"].rolling(20).std().to_numpy()
        pc = pct_rank(rv20, 252)
        c = d1["close"].to_numpy()
        hh = d1["high"].shift(1).rolling(20).max().to_numpy()
        ll = d1["low"].shift(1).rolling(20).min().to_numpy()
        side = np.where(c > hh, 1, np.where(c < ll, -1, 0))
        return _dec(d1, pc <= 33, side, "1d")

    def s05() -> pd.DataFrame:
        r = d1["r"]
        pc = pct_rank(r.rolling(20).std().to_numpy(), 252)
        z = (r / r.rolling(60).std().shift(1)).to_numpy()
        return _dec(d1, (pc >= 67) & (np.abs(z) >= 2), -sgn(z), "1d")

    def s06() -> pd.DataFrame:
        r = h4["r"]
        ratio = (r.rolling(6).std() / r.rolling(60).std()).to_numpy()
        c = h4["close"].to_numpy()
        hh = h4["high"].shift(1).rolling(20).max().to_numpy()
        ll = h4["low"].shift(1).rolling(20).min().to_numpy()
        side = np.where(c > hh, 1, np.where(c < ll, -1, 0))
        return _dec(h4, ratio < 0.5, side, "4h")

    # --- Kalman
    def kcross(b: pd.DataFrame, tf: str) -> pd.DataFrame:
        kz = b["kz"].to_numpy()
        side = np.where(cross_up(kz, 2.0), 1, np.where(cross_dn(kz, -2.0), -1, 0))
        return _dec(b, side != 0, side, tf)

    def s10() -> pd.DataFrame:
        kz, iz = h1["kz"].to_numpy(), h1["iz"].to_numpy()
        return _dec(h1, (np.abs(kz) < 1) & (np.abs(iz) >= 2.5), -sgn(iz), "1h")

    # --- autocorrelation / VR
    def s11(fade: bool) -> pd.DataFrame:
        v = vr(h1["r"], 4, 256)
        z24 = z_ret(h1, 24, 256)
        cond = (v > 1.15) if not fade else (v < 0.85)
        return _dec(h1, cond & (np.abs(z24) >= 1.5), (-1 if fade else 1) * sgn(z24), "1h")

    def acrule(b: pd.DataFrame, w: int, thr: float, follow: bool, tf: str) -> pd.DataFrame:
        a = ac1(b["r"], w)
        mv = (b["close"] - b["close"].shift(1)).to_numpy()
        big = np.abs(mv) >= b["atr"].to_numpy()
        cond = (a > thr) if follow else (a < -thr)
        return _dec(b, cond & big, (1 if follow else -1) * sgn(mv), tf)

    def s15() -> pd.DataFrame:
        v = vr(d1["r"], 5, 250)
        z20 = z_ret(d1, 20, 250)
        return _dec(d1, (v > 1.10) & (np.abs(z20) >= 1), sgn(z20), "1d")

    # --- distribution
    def s18() -> pd.DataFrame:
        sk5 = d1["rskew"].rolling(5).mean().to_numpy()
        pc = pct_rank_finite(sk5, 252)
        return _dec(d1, np.isfinite(pc), np.where(pc >= 80, -1, np.where(pc <= 20, 1, 0)), "1d")

    def jump(cont: bool) -> pd.DataFrame:
        jz = d1["jz"].to_numpy()
        ret = d1["ret"].to_numpy()
        return _dec(d1, np.nan_to_num(jz) >= 3.09, (1 if cont else -1) * sgn(ret), "1d")

    def s21() -> pd.DataFrame:
        sj = ((d1["rsp"] - d1["rsn"]).rolling(5).sum() / d1["rv"].rolling(5).sum()).to_numpy()
        pc = pct_rank_finite(sj, 252)
        return _dec(d1, np.isfinite(pc), np.where(pc >= 80, -1, np.where(pc <= 20, 1, 0)), "1d")

    def s22() -> pd.DataFrame:
        mx = d1["r"].rolling(21).max().to_numpy()
        mn = d1["r"].rolling(21).min().to_numpy()
        pmx, pmn = pct_rank(mx, 252), pct_rank(mn, 252)
        hi, lo = pmx >= 80, pmn <= 20
        side = np.where(hi & ~lo, -1, np.where(lo & ~hi, 1, 0))
        return _dec(d1, np.isfinite(pmx) & np.isfinite(pmn), side, "1d")

    rsk4_pct = pct_rank_finite(ctx["rsk4"], 180)

    def s23() -> pd.DataFrame:
        pc = rsk4_pct
        return _dec(h4, np.isfinite(pc), np.where(pc >= 80, -1, np.where(pc <= 20, 1, 0)), "4h")

    # --- ML
    def ml(b: pd.DataFrame, tf: str, kind: str, rid: str) -> pd.DataFrame:
        side, info = ml_rule(b, tf, kind, window)
        diag[rid] = info
        return _dec(b, side != 0, side, tf, tgt=math.nan, hold=ML_HOLD[tf])

    def s30() -> pd.DataFrame:
        side = kmeans_rule(d1, window)
        return _dec(d1, side != 0, side, "1d")

    # --- ensembles
    def s31() -> pd.DataFrame:
        lc = np.log(h4["close"].to_numpy())
        lcs = pd.Series(lc)
        c = h4["close"]
        mid20 = (h4["high"].rolling(20).max() + h4["low"].rolling(20).min()) / 2
        votes = np.c_[sgn(lc - lcs.shift(6).to_numpy()), sgn(lc - lcs.shift(24).to_numpy()),
                      sgn(lc - lcs.shift(60).to_numpy()), sgn((c.rolling(10).mean() - c.rolling(40).mean()).to_numpy()),
                      sgn((c - mid20).to_numpy())]
        s = votes.sum(1)
        v = np.where(s == 5, 1, np.where(s == -5, -1, 0))
        tr = transition(v)
        return _dec(h4, tr != 0, tr, "4h")

    def s32() -> pd.DataFrame:
        lc = np.log(d1["close"].to_numpy())
        lcs = pd.Series(lc)
        c = d1["close"]
        votes = np.c_[sgn(lc - lcs.shift(20).to_numpy()), sgn(lc - lcs.shift(60).to_numpy()),
                      sgn(lc - lcs.shift(120).to_numpy()), sgn(lc - lcs.shift(250).to_numpy()),
                      sgn((c.rolling(50).mean() - c.rolling(200).mean()).to_numpy())]
        ok = np.isfinite(lcs.shift(250).to_numpy()) & np.isfinite(c.rolling(200).mean().to_numpy())
        s = votes.sum(1)
        v = np.where(ok & (s == 5), 1, np.where(ok & (s == -5), -1, 0))
        tr = transition(v)
        return _dec(d1, tr != 0, tr, "1d")

    def s33() -> pd.DataFrame:
        kz = h4["kz"].to_numpy()
        v1 = np.where(np.abs(np.nan_to_num(kz)) >= 1, sgn(kz), 0)
        a = ac1(h4["r"], 120)
        v2 = sgn(a) * sgn(h4["r"].to_numpy())
        pc = rsk4_pct
        v3 = np.where(np.isfinite(pc) & ((pc < 30) | (pc > 70)), -sgn(np.nan_to_num(pc) - 50), 0)
        v = np.where((v1 == v2) & (v2 == v3) & (v1 != 0), v1, 0)
        tr = transition(v)
        return _dec(h4, tr != 0, tr, "4h")

    out = {
        "01": s01, "02": s02, "03": s03, "04": s04, "05": s05, "06": s06,
        "07": lambda: kcross(h4, "4h"), "08": lambda: kcross(h1, "1h"), "09": lambda: kcross(d1, "1d"), "10": s10,
        "11": lambda: s11(False), "12": lambda: s11(True),
        "13": lambda: acrule(h1, 128, 0.10, True, "1h"), "14": lambda: acrule(h1, 128, 0.10, False, "1h"),
        "15": s15, "16": lambda: acrule(h4, 120, 0.08, True, "4h"), "17": lambda: intraday_mom(m, ctx),
        "18": s18, "19": lambda: jump(True), "20": lambda: jump(False), "21": s21, "22": s22, "23": s23,
        "24": lambda: ml(h1, "1h", "lr", "24"), "25": lambda: ml(h1, "1h", "gbm", "25"),
        "26": lambda: ml(h4, "4h", "lr", "26"), "27": lambda: ml(h4, "4h", "gbm", "27"),
        "28": lambda: ml(d1, "1d", "lr", "28"), "29": lambda: ml(d1, "1d", "gbm", "29"),
        "30": s30, "31": s31, "32": s32, "33": s33,
    }
    res = {}
    for rid, fn in out.items():
        if rules and rid not in rules:
            continue
        d = fn()
        d = d[in_window(d["t"].to_numpy(np.int64), window) & np.isfinite(d["risk"]) & (d["risk"] > 0)]
        res[rid] = d.sort_values("t", kind="stable").reset_index(drop=True)
    return res


# ================================================================ simulation
def simulate(M: Minutes, k0: int, side: int, risk: float, tgt: float, hold: int, end_ns: int) -> tuple[float, bool, int]:
    """(signed move per oz, stop exit, exit index) entering at the open of minute k0."""
    entry = M.o[k0]
    kend = int(np.searchsorted(M.t, min(M.t[k0] + hold, end_ns), side="left"))
    kend = max(kend, k0 + 1)
    lo_, hi_ = M.l[k0:kend], M.h[k0:kend]
    if side > 0:
        stop = entry - risk
        sh = lo_ <= stop
        th = hi_ >= entry + tgt * risk if np.isfinite(tgt) else np.zeros(len(lo_), bool)
    else:
        stop = entry + risk
        sh = hi_ >= stop
        th = lo_ <= entry - tgt * risk if np.isfinite(tgt) else np.zeros(len(lo_), bool)
    i_s = int(np.argmax(sh)) if sh.any() else len(sh)
    i_t = int(np.argmax(th)) if th.any() else len(th)
    if i_s < len(sh) and i_s <= i_t:
        k = k0 + i_s
        op = M.o[k]
        fill = min(op, stop) if side > 0 else max(op, stop)
        if k == k0:
            fill = stop
        return side * (fill - entry), True, k
    if i_t < len(th):
        return tgt * risk, False, k0 + i_t
    return side * (M.c[kend - 1] - entry), False, kend - 1


def run_rule(M: Minutes, dec: pd.DataFrame, end_ns: int, rng: np.random.Generator) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows, twins = [], []
    busy_until = -1
    for t, side, risk, tgt, hold in zip(dec["t"].to_numpy(np.int64), dec["side"].to_numpy(int),
                                        dec["risk"].to_numpy(float), dec["tgt"].to_numpy(float),
                                        dec["hold"].to_numpy(np.int64), strict=True):
        k0 = int(np.searchsorted(M.t, t, side="left"))
        if k0 >= len(M.t) or M.t[k0] - t > MAX_ENTRY_DELAY or M.t[k0] >= end_ns:
            continue
        if M.t[k0] <= busy_until:
            continue
        mv, st, kx = simulate(M, k0, int(side), float(risk), float(tgt), int(hold), end_ns)
        busy_until = int(M.t[kx])
        rows.append((M.t[k0], M.t[kx], int(side), float(risk), mv, st, int(M.sess[k0])))
        key = M.day[k0] * 4 + M.sess[k0]
        j = int(np.searchsorted(M.ukeys, key))
        pool = M.order[M.kstart[j]:M.kend[j]]
        # brief control (same day x session, any minute): reported, but it looks ahead (coordinator fix 2026-11-02)
        for kk in rng.choice(pool, size=N_TWIN, replace=True):
            mv2, st2, kx2 = simulate(M, int(kk), int(side), float(risk), float(tgt), int(hold), end_ns)
            twins.append((M.t[int(kk)], M.t[kx2], int(side), float(risk), mv2, st2, int(M.sess[int(kk)]),
                          len(rows) - 1, "sameday"))
        # leak-free control: same day x session x side, entry minute at or after the real entry; if none, the same
        # time of day on other days of the window
        fwd = pool[pool >= k0]
        for _ in range(N_TWIN):
            kk = int(rng.choice(fwd)) if len(fwd) else _other_day(M, int(M.t[k0]), rng, end_ns)
            if kk < 0:
                continue
            mv2, st2, kx2 = simulate(M, kk, int(side), float(risk), float(tgt), int(hold), end_ns)
            twins.append((M.t[kk], M.t[kx2], int(side), float(risk), mv2, st2, int(M.sess[kk]), len(rows) - 1,
                          "forward"))
    cols = ["entry", "exit", "side", "risk", "move", "stop", "sess"]
    return pd.DataFrame(rows, columns=cols), pd.DataFrame(twins, columns=[*cols, "parent", "kind"])


def _other_day(M: Minutes, t_entry: int, rng: np.random.Generator, end_ns: int) -> int:
    """A minute at the same UTC time of day on another day of the loaded window (within 15 minutes), or -1."""
    days = M.ukeys // 4
    tod = t_entry % DAY
    for _ in range(50):
        d = int(rng.choice(days))
        target = d * DAY + tod
        if target >= end_ns or d == t_entry // DAY:
            continue
        k = int(np.searchsorted(M.t, target, side="left"))
        if k < len(M.t) and M.t[k] - target <= MAX_ENTRY_DELAY:
            return k
    return -1


SESS_NAMES = {v: k for k, v in SESSION_CODES.items()}


def price(tr: pd.DataFrame, costs: A.ArmCosts, broker: str) -> np.ndarray:
    if tr.empty:
        return np.zeros(0)
    e = pd.DatetimeIndex(pd.to_datetime(tr["entry"].to_numpy(), utc=True))
    x = pd.DatetimeIndex(pd.to_datetime(tr["exit"].to_numpy(), utc=True))
    nights = rollover_nights(e, x, "Europe/Athens", 2)
    swap = nights * np.where(tr["side"].to_numpy() > 0, SWAP_LONG_PER_OZ, SWAP_SHORT_PER_OZ)
    sess = np.array([SESS_NAMES[int(s)] for s in tr["sess"].to_numpy()], dtype=object)
    return A.screen_net_r(tr["move"].to_numpy() + swap, tr["risk"].to_numpy(), tr["stop"].to_numpy(), costs,
                          broker=broker, sessions=sess, version=COST_VERSION)


def tstat(x: np.ndarray) -> float:
    if len(x) < 3 or np.std(x, ddof=1) == 0:
        return 0.0
    return float(np.mean(x) / (np.std(x, ddof=1) / math.sqrt(len(x))))


def day_boot_lb(x: np.ndarray, days: np.ndarray, rng: np.random.Generator, n: int = 2000) -> list[float]:
    if len(x) < 3:
        return [math.nan, math.nan]
    u, inv = np.unique(days, return_inverse=True)
    s = np.bincount(inv, weights=x)
    c = np.bincount(inv)
    idx = rng.integers(0, len(u), size=(n, len(u)))
    m = s[idx].sum(1) / c[idx].sum(1)
    return [float(np.quantile(m, 0.05)), float(np.quantile(m, 0.95))]


def summarise(rid: str, tr: pd.DataFrame, tw: pd.DataFrame, costs: A.ArmCosts, window: str) -> dict[str, Any]:
    rng = np.random.default_rng(SEED + int(rid))
    if tr.empty:
        return {"rule": rid, "n": 0, "pass": False}
    net = price(tr, costs, "icm")
    gross = tr["move"].to_numpy() / tr["risk"].to_numpy()
    van = price(tr, costs, "vantage")
    tw_same = tw[tw["kind"] == "sameday"] if not tw.empty else tw
    tw_fwd = tw[tw["kind"] == "forward"] if not tw.empty else tw
    tsame = price(tw_same, costs, "icm") if not tw_same.empty else np.zeros(0)
    tnet = price(tw_fwd, costs, "icm") if not tw_fwd.empty else np.zeros(0)
    years = pd.DatetimeIndex(pd.to_datetime(tr["entry"].to_numpy(), utc=True)).year.to_numpy()
    by_year = {int(y): {"n": int((years == y).sum()), "net": float(net[years == y].mean())} for y in np.unique(years)}
    longs = tr["side"].to_numpy() > 0
    out: dict[str, Any] = {
        "rule": rid, "n": int(len(net)), "net_mean_r": float(net.mean()), "t": tstat(net),
        "net_lb_ub90_dayboot": day_boot_lb(net, tr["entry"].to_numpy() // DAY, rng),
        "gross_mean_r": float(gross.mean()), "cost_mean_r": float((gross - net).mean()),
        "vantage_stress_net_r": float(van.mean()), "twin_n": int(len(tnet)),
        "twin_net_mean_r": float(tnet.mean()) if len(tnet) else None,
        "minus_twin": float(net.mean() - tnet.mean()) if len(tnet) else None,
        "sameday_twin_net_mean_r": float(tsame.mean()) if len(tsame) else None,
        "minus_sameday_twin": float(net.mean() - tsame.mean()) if len(tsame) else None,
        "long_n": int(longs.sum()), "long_net": float(net[longs].mean()) if longs.any() else None,
        "short_n": int((~longs).sum()), "short_net": float(net[~longs].mean()) if (~longs).any() else None,
        "stop_share": float(tr["stop"].mean()), "median_risk_usd": float(np.median(tr["risk"])),
        "by_year": by_year, "years_positive": int(sum(v["net"] > 0 for v in by_year.values())),
    }
    if window == "explore":
        out["pass"] = bool(out["n"] >= 100 and out["net_mean_r"] > 0 and out["t"] >= 2.0
                           and out["minus_twin"] is not None and out["minus_twin"] >= 0.05)
    elif window in OLD_YEARS:
        # amendment W1: n >= 50 over the three years, net > 0, t >= 1.5, net minus the leak-free control >= 0
        out["pass"] = bool(out["n"] >= 50 and out["net_mean_r"] > 0 and out["t"] >= 1.5
                           and out["minus_twin"] is not None and out["minus_twin"] >= 0)
    else:
        from scipy import stats as st
        sd = float(np.std(net, ddof=1))
        sr = float(net.mean() / sd) if sd > 0 else 0.0
        sk, ku = float(st.skew(net)), float(st.kurtosis(net, fisher=False))
        dsr = deflated_sharpe(sr, K_TOTAL, len(net), sk, ku, 1.0 / len(net))
        out.update({"sr": sr, "dsr_k3229": dsr, "dsr_k3500": deflated_sharpe(sr, K_STRESS, len(net), sk, ku,
                                                                             1.0 / len(net))})
        yrs = [y for y in range(2018, 2026)]
        pos = sum(1 for y in yrs if y in by_year and by_year[y]["net"] > 0)
        out["confirm_years_positive_of_8"] = pos
        out["pass"] = bool(out["net_mean_r"] > 0 and out["t"] >= 2.0 and dsr > 0.5 and pos >= 6)
        out["weak_dsr"] = bool(dsr < 0.95)
    return out


_G: dict[str, Any] = {}


def load_for_window(raw: Path, window: str) -> pd.DataFrame:
    if window in OLD_YEARS:
        # warm-up from 2002; the fitted rules fit on 2010-2017 only, so the explore years are loaded too
        m = load_1m(raw, OLD_LOAD_FROM, EXPLORE[1])
        assert int(m["t"].min()) < EXPLORE[0].value, "no pre-2010 data on disk yet: old-test pending"
        return m
    return load_1m(raw, EXPLORE[0], window_bounds(window)[1])


def _init(raw: str, window: str, payload: dict[str, Any]) -> None:
    """Spawned worker: loads its own 1m frame (no fork of a threaded parent; coordinator fix 2026-11-02)."""
    m = load_for_window(Path(raw), window)
    costs = A.load_costs(ROOT / "config" / "arm_costs.yaml")
    costs.require_version(COST_VERSION)
    _G.update(m=m, M=Minutes(m), window=window, end=int(window_bounds(window)[1].value), costs=costs, **payload)


def _work(rid: str) -> dict[str, Any]:
    M, sig, end_ns, costs, window = _G["M"], _G["sig"], _G["end"], _G["costs"], _G["window"]
    t0 = time.time()
    rng = np.random.default_rng(SEED * 100 + int(rid))
    tr, tw = run_rule(M, sig[rid], end_ns, rng)
    out = summarise(rid, tr, tw, costs, window)
    out["seconds"] = round(time.time() - t0, 1)
    return out


# ================================================================ leakage tests (prereg s.3)
def _cut_check(cut: int) -> tuple[int, dict[str, int], int]:
    m, full, window = _G["m"], _G["full"], _G["window"]
    t = m["t"].to_numpy(np.int64)
    part = all_signals(m[t < cut].reset_index(drop=True), window)
    bad: dict[str, int] = {}
    checked = 0
    for rid, d in full.items():
        a = d[d["t"] < cut].reset_index(drop=True)          # every decision made before the cut
        b = part[rid]
        b = b[b["t"] < cut].reset_index(drop=True)
        same = len(a) == len(b) and (len(a) == 0 or (np.array_equal(a["t"], b["t"]) and np.array_equal(
            a["side"], b["side"]) and np.allclose(a["risk"], b["risk"], rtol=1e-9)))
        checked += len(a)
        if not same:
            bad[rid] = 1
    return cut, bad, checked


def lookahead_test(m: pd.DataFrame, raw: str, window: str, n_cuts: int = 8, workers: int = 2) -> dict[str, Any]:
    """Every decision made before a cut time is identical when computed on 1m data truncated at the cut."""
    full = all_signals(m, window)
    rng = np.random.default_rng(SEED)
    lo, hi = window_bounds(window)
    first = int(pd.Timestamp("2013-01-01", tz="UTC").value) if window == "explore" else lo.value
    cuts = [int(c) for c in np.sort(rng.integers(first, hi.value, size=n_cuts))]
    res = []
    with get_context("spawn").Pool(workers, initializer=_init, initargs=(raw, window, {"full": full})) as pool:
        for r in pool.imap_unordered(_cut_check, cuts):
            print(f"lookahead cut {pd.Timestamp(r[0], tz='UTC')}: {r[2]} decisions, mismatches {r[1]}", flush=True)
            res.append(r)
    bad: dict[str, int] = {}
    for _, b, _ in res:
        for k in b:
            bad[k] = bad.get(k, 0) + 1
    return {"cuts": [str(pd.Timestamp(c, tz="UTC")) for c in cuts], "decisions_checked": int(sum(r[2] for r in res)),
            "mismatching_rules": bad, "pass": not bad}


def shuffle_test(m: pd.DataFrame, window: str) -> dict[str, Any]:
    ctx = prepare(m)
    out = {}
    for rid, tf, kind in (("24", "1h", "lr"), ("25", "1h", "gbm"), ("26", "4h", "lr"), ("27", "4h", "gbm"),
                          ("28", "1d", "lr"), ("29", "1d", "gbm")):
        _, info = ml_rule(ctx[tf], tf, kind, window, shuffle=True)
        a = info["auc_mean"]
        out[rid] = {"shuffled_auc_mean": a, "pass": a is not None and abs(a - 0.5) <= 0.03}
    return out


# ================================================================ main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", choices=["explore", "confirm", "test_old", "learn_old"], required=True)
    ap.add_argument("--confirm-json", default="", help="test_old: the confirm result; only its passers are read")
    ap.add_argument("--raw", default=str(ROOT / "raw"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--rules", default="")
    ap.add_argument("--leak-only", action="store_true")
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args()
    t0 = time.time()
    lo, hi = window_bounds(a.window)
    rules = [r.zfill(2) for r in a.rules.split(",") if r]
    if a.window == "test_old":
        if not a.confirm_json:
            raise SystemExit("test_old reads confirm-passers only: give --confirm-json")
        conf = json.loads(Path(a.confirm_json).expanduser().read_text())["rules"]
        passed = {k for k, v in conf.items() if v["pass"]}
        refused = [r for r in rules if r not in passed]
        if refused or not rules:
            raise SystemExit(f"test_old refuses rules that did not pass confirm: {refused or 'none given'}")
    m = load_for_window(Path(a.raw), a.window)
    print(f"loaded {len(m):,} 1m rows to {pd.Timestamp(int(m['t'].max()), tz='UTC')}", flush=True)
    result: dict[str, Any] = {"window": a.window, "prereg": "docs/research/sprint200/stats-prereg.md",
                              "cost_version": COST_VERSION, "k_total": K_TOTAL}
    if a.leak_only:
        result["shuffle"] = shuffle_test(m, a.window)
        print(json.dumps(result["shuffle"]), flush=True)
        result["lookahead"] = lookahead_test(m, a.raw, a.window, workers=a.workers)
        print(json.dumps(result["lookahead"]), flush=True)
    else:
        diag: dict[str, Any] = {}
        sig = all_signals(m, a.window, rules or None, diag)
        result["ml_oof_auc"] = diag
        result["decision_counts"] = {k: int(len(v)) for k, v in sig.items()}
        print(f"signals built ({time.time() - t0:.0f} s): {result['decision_counts']}", flush=True)
        del m
        res = []
        with get_context("spawn").Pool(a.workers, initializer=_init, initargs=(a.raw, a.window, {"sig": sig})) as pool:
            for r in pool.imap_unordered(_work, sorted(sig)):
                print(f"rule {r['rule']} done: n {r['n']} ({r.get('seconds')} s)", flush=True)
                res.append(r)
        result["rules"] = {r["rule"]: r for r in sorted(res, key=lambda r: r["rule"])}
    result["seconds"] = round(time.time() - t0, 1)
    Path(a.out).expanduser().parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).expanduser().write_text(json.dumps(result, indent=1, default=float))
    print(f"wrote {a.out} in {result['seconds']} s", flush=True)


if __name__ == "__main__":
    main()
