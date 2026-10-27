"""Slice C, part 2: causal wavelets, Ehlers cycle tools, Hurst, Goertzel, EMD end effects, Fibonacci time
(docs/research/time-structure/fib-wavelets-cycles.md). Discovery half 2010-2017 only (ts_common.py never loads 2018+).
Descriptive screen, NOT a registry trial. Every look is counted in LOOKS and printed at the end.

Point in time: every indicator is computed with one-sided (causal) filters and `check_causal` proves value(t) is
identical when the bars after t are deleted. Macro joins go through goldbot.data.store.asof_join on available_utc.

Usage: uv run --with pywavelets --with scipy --with statsmodels python docs/research/candidates/time_structure/ts_cycles.py --data DIR
       --macro PARQUET [part ...]   (parts: lookahead scales macro ehlers hurst goertzel emd fibtime)
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pywt
import statsmodels.api as sm
from scipy.stats import rankdata, spearmanr

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from goldbot.data.store import asof_join  # noqa: E402
from goldbot.features.survey import rolling_hurst  # noqa: E402

warnings.filterwarnings("ignore")
LOOKS: list[str] = []
COST_C = 0.144


def look(name: str) -> None:
    LOOKS.append(name)


# ------------------------------------------------------------------ causal MODWT
def modwt_filters(name: str) -> tuple[np.ndarray, np.ndarray]:
    w = pywt.Wavelet(name)
    return np.asarray(w.dec_hi) / np.sqrt(2), np.asarray(w.dec_lo) / np.sqrt(2)     # MODWT rescaling


def _shift(x: np.ndarray, k: int) -> np.ndarray:
    out = np.full_like(x, np.nan)
    if k < len(x):
        out[k:] = x[: len(x) - k]
    return out


def causal_modwt(x: np.ndarray, name: str = "db4", levels: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """Causal MODWT (a trous cascade): W[j][t] and V[j][t] use x[<=t] only, filters applied backwards in time, no
    periodic wrap; the first (L_j - 1) outputs are NaN (the boundary region is dropped, never padded or reflected).
    Returns W (levels, n), V (levels, n)."""
    h, g = modwt_filters(name)
    v = x.astype(float).copy()
    W, V = [], []
    for j in range(1, levels + 1):
        step = 2 ** (j - 1)
        w = np.zeros_like(v)
        a = np.zeros_like(v)
        for ell in range(len(h)):
            s = _shift(v, ell * step)
            w = w + h[ell] * s
            a = a + g[ell] * s
        W.append(w)
        V.append(a)
        v = a
    return np.vstack(W), np.vstack(V)


def check_causal(fn, x: np.ndarray, cuts=(500, 900, 1500)) -> float:
    """Max |fn(x[:T+1])[-1] - fn(x)[T]| over cuts: 0 means the value at T never sees bars after T."""
    full = fn(x)
    err = 0.0
    for T in cuts:
        part = fn(x[: T + 1])
        a, b = np.atleast_1d(part[..., -1]), np.atleast_1d(full[..., T])
        m = np.isfinite(a) & np.isfinite(b)
        err = max(err, float(np.max(np.abs(a[m] - b[m]))) if m.any() else 0.0)
    return err


# ------------------------------------------------------------------ stats helpers
def nw_t(y: np.ndarray, X: np.ndarray, lags: int) -> tuple[float, float]:
    m = np.isfinite(y) & np.isfinite(X).all(axis=1)
    r = sm.OLS(y[m], sm.add_constant(X[m])).fit(cov_type="HAC", cov_kwds={"maxlags": lags})
    return float(r.params[-1]), float(r.tvalues[-1])


def ic_nonoverlap(sig: np.ndarray, fwd_h: np.ndarray, h: int) -> tuple[float, float, int]:
    """Spearman IC on every h-th observation (no overlap), t = IC*sqrt(n-2)/sqrt(1-IC^2)."""
    s, f = sig[::h], fwd_h[::h]
    m = np.isfinite(s) & np.isfinite(f)
    if m.sum() < 30:
        return np.nan, np.nan, int(m.sum())
    r = spearmanr(s[m], f[m])[0]
    n = int(m.sum())
    return float(r), float(r * np.sqrt((n - 2) / max(1e-12, 1 - r * r))), n


def pooled_ic(sig: np.ndarray, fwd: np.ndarray, day: np.ndarray, nboot: int = 200, seed: int = 3):
    """Pooled Spearman IC (never the mean of within-day ICs) with a day-block bootstrap standard error."""
    m = np.isfinite(sig) & np.isfinite(fwd)
    s, f, d = sig[m], fwd[m], day[m]
    rs, rf = rankdata(s), rankdata(f)
    ic = float(np.corrcoef(rs, rf)[0, 1])
    ud, inv = np.unique(d, return_inverse=True)
    idx_by = np.split(np.argsort(inv), np.cumsum(np.bincount(inv))[:-1])
    rng = np.random.default_rng(seed)
    boots = []
    for _ in range(nboot):
        pick = rng.integers(0, len(ud), len(ud))
        ii = np.concatenate([idx_by[p] for p in pick])
        boots.append(np.corrcoef(rs[ii], rf[ii])[0, 1])
    se = float(np.std(boots))
    return ic, se, ic / se if se > 0 else np.nan, int(m.sum())


def load(data: Path, tf: str) -> pd.DataFrame:
    b = pd.read_parquet(data / f"mid_{tf}.parquet").sort_values("ts_ns").reset_index(drop=True)
    b["logc"] = np.log(b["close"])
    b["day"] = (b["ts_ns"] // (24 * 3_600_000_000_000)).astype(int)
    return b


def fwd_ret(logc: np.ndarray, h: int) -> np.ndarray:
    out = np.full_like(logc, np.nan)
    out[:-h] = logc[h:] - logc[:-h]
    return out


# ------------------------------------------------------------------ part: the look-ahead trap
def part_lookahead(data: Path) -> None:
    d = load(data, "1d")
    x = d["logc"].to_numpy()
    r = np.diff(x, prepend=x[0])
    rng = np.random.default_rng(5)
    rw = np.cumsum(rng.normal(0, r.std(), len(x)))      # random walk with gold's daily vol: any "edge" is leakage
    print("\n== causality proof (value at T identical with bars after T deleted)")
    for nm in ("haar", "db4"):
        e = check_causal(lambda z, nm=nm: causal_modwt(z, nm, 5)[0], x)
        print(f"causal MODWT {nm}: max |diff| = {e:.2e}")

    def centred_denoise(z: np.ndarray, level: int = 4) -> np.ndarray:
        pad = len(z) % (2 ** level)
        z = z[pad:]                                                   # SWT needs length % 2^level == 0
        c = pywt.swt(z - z.mean(), "db4", level=level, trim_approx=True)       # whole-sample SWT (centred filters)
        sig = np.median(np.abs(c[-1])) / 0.6745
        thr = sig * np.sqrt(2 * np.log(len(z)))
        c = [c[0]] + [pywt.threshold(ci, thr, "soft") for ci in c[1:]]
        return np.r_[np.full(pad, np.nan), pywt.iswt(c, "db4") + z.mean()] if pad else pywt.iswt(c, "db4") + z.mean()

    def causal_smooth(z: np.ndarray) -> np.ndarray:
        return causal_modwt(z, "db4", 4)[1][3]

    print("\n== look-ahead trap: sign(s_t - s_{t-1}) vs next-day return (Spearman IC, daily, n~2,490)")
    print("series | centred whole-sample denoise (WRONG) | causal db4 smooth (v_t - v_{t-1})")
    for nm, z in (("gold", x), ("random walk", rw)):
        zz = z.copy()
        ds = centred_denoise(zz)
        look(f"trap {nm}")
        a = ds[1:] - ds[:-1]
        f = np.diff(zz)
        mk = np.isfinite(a[:-1])
        ic_bad = spearmanr(a[:-1][mk], f[1:][mk])[0]
        cs = causal_smooth(zz)
        b = cs[1:] - cs[:-1]
        m = np.isfinite(b[:-1])
        ic_ok = spearmanr(b[:-1][m], f[1:][m])[0]
        # the same wrong filter, but computed from a prefix only (causal evaluation of the same code), last point
        print(f"{nm:12s} | {ic_bad:+.3f} | {ic_ok:+.3f}")
    # expanding-window honest version of the SAME centred denoiser: value at t from x[:t+1] only (end effects live)
    ts = np.arange(1000, len(x) - 1, 5)
    es = np.array([centred_denoise(x[: t + 1])[-1] - centred_denoise(x[: t + 1])[-2] for t in ts])
    f = x[ts + 1] - x[ts]
    print(f"same centred denoiser recomputed on x[:t+1] each t (n={len(ts)}): IC {spearmanr(es, f)[0]:+.3f}")
    full = centred_denoise(x)
    dev = np.array([abs(centred_denoise(x[: t + 1])[-1] - full[t]) for t in ts[::10]])
    print(f"mean |denoised(t) from prefix - denoised(t) from full sample| = {dev.mean():.4f} log pts "
          f"(= {dev.mean() / r.std():.2f} daily SDs)")


# ------------------------------------------------------------------ part: wavelet scales
def part_scales(data: Path) -> None:
    for tf, H, per in (("1d", (5, 20), "d"), ("1h", (24,), "h")):
        d = load(data, tf)
        x = d["logc"].to_numpy()
        lev = 7 if tf == "1d" else 8
        W, V = causal_modwt(x, "db4", lev)
        e = check_causal(lambda z: causal_modwt(z, "db4", lev)[0], x[:3000] if tf == "1h" else x, (500, 900, 1500))
        print(f"\n== {tf}: causal db4 MODWT, levels 1..{lev} (scale j = period 2^j..2^(j+1) bars); causality err {e:.1e}")
        r = np.diff(x, prepend=x[0])
        # energy share by scale of returns, after burn-in
        burn = (len(modwt_filters("db4")[0]) - 1) * (2 ** lev - 1) + 1
        ok = np.arange(len(x)) >= burn
        en = np.array([np.nanmean(W[j][ok] ** 2) for j in range(lev)])
        print("energy share by scale j:", " ".join(f"{j+1}:{100*en[j]/en.sum():.0f}%" for j in range(lev)))
        # sign of the band component -> forward return (pooled for 1h, non-overlap for 1d)
        for h in H:
            f = fwd_ret(x, h)
            rows = []
            for j in range(lev):
                look(f"scales {tf} j{j+1} h{h}")
                s = W[j]
                if tf == "1d":
                    ic, t, n = ic_nonoverlap(s, f, h)
                else:
                    ic, se, t, n = pooled_ic(s[ok], f[ok], d["day"].to_numpy()[ok])
                rows.append((j + 1, 2 ** (j + 1), ic, t, n))
            print(f"IC of band component W_j[t] -> forward {h}{per}: " +
                  " | ".join(f"j{j}(~{p}b) {ic:+.3f} t{t:+.1f}" for j, p, ic, t, n in rows))
        # incremental volatility information beyond RV20 (the baseline the director found predictable)
        rv = pd.Series(r ** 2)
        hh = 5 if tf == "1d" else 24
        base = np.log(rv.rolling(20 if tf == "1d" else 120).mean().to_numpy())
        fut = np.log(rv[::-1].rolling(hh).mean()[::-1].shift(-1).to_numpy())          # RV over t+1..t+hh
        feats = {}
        for j in (1, 3, 5, 7):
            if j <= lev:
                feats[f"logE_j{j}"] = np.log(pd.Series(W[j - 1] ** 2).rolling(20 if tf == "1d" else 120).mean().to_numpy())
        feats["E_hi/E_lo"] = np.log(pd.Series(W[0] ** 2 + W[1] ** 2).rolling(20 if tf == "1d" else 120).mean().to_numpy()
                                    / pd.Series(W[lev - 2] ** 2 + W[lev - 1] ** 2).rolling(20 if tf == "1d" else 120).mean().to_numpy())
        m0 = np.isfinite(base) & np.isfinite(fut) & ok
        b0 = sm.OLS(fut[m0], sm.add_constant(base[m0])).fit()
        print(f"vol baseline log RV(next {hh}) ~ log RV20: R2 {b0.rsquared:.3f}")
        for k, v in feats.items():
            look(f"vol incremental {tf} {k}")
            m = m0 & np.isfinite(v)
            X = np.column_stack([base[m], v[m]])
            fit = sm.OLS(fut[m], sm.add_constant(X)).fit(cov_type="HAC", cov_kwds={"maxlags": hh + 20})
            print(f"  + {k}: coef {fit.params[2]:+.3f} t {fit.tvalues[2]:+.1f}  R2 {fit.rsquared:.3f} (gain {fit.rsquared - b0.rsquared:+.4f})")
        _ = V


# ------------------------------------------------------------------ part: wavelet scale correlation with macro
def part_macro(data: Path, macro: Path) -> None:
    d = load(data, "1d")
    m = pd.read_parquet(macro)
    x = d["logc"].to_numpy()
    W, _ = causal_modwt(x, "db4", 6)
    print("\n== causal wavelet scale correlation, gold vs macro (daily, 2010-2017)")
    out = {}
    for sid, nm, sign in (("real_yield_10y", "real yield", 1), ("broad_dollar", "broad dollar", 1)):
        s = m[m.series == sid][["available_utc", "value"]].rename(columns={"value": nm})
        j = asof_join(d[["ts_utc", "visible_at", "close"]].rename(columns={"ts_utc": "ts_utc"}).assign(
            ts_utc=pd.to_datetime(d["visible_at"], utc=True)), s)
        v = j[nm].to_numpy().astype(float)
        if sid == "broad_dollar":
            v = np.log(v)
        Wm, _ = causal_modwt(np.nan_to_num(v, nan=np.nanmean(v)), "db4", 6)
        ok = np.arange(len(x)) >= 500
        # lag between macro availability and gold bar is included: macro known at bar close (asof)
        for k in range(6):
            a, b = W[k][ok], Wm[k][ok]
            look(f"macro corr {sid} j{k+1}")
            c0 = np.corrcoef(a, b)[0, 1]
            f5 = fwd_ret(x, 5)[ok]
            ic, t, n = ic_nonoverlap(Wm[k][ok], f5, 5)
            out[(nm, k + 1)] = (c0, ic, t)
        print(nm + " (asof available_utc, no look-ahead): scale j -> corr(W_gold,W_macro) | IC(W_macro[t] -> gold next 5d) t")
        print("  " + " | ".join(f"j{k}(~{2**k}-{2**(k+1)}d) {out[(nm, k)][0]:+.2f} | {out[(nm, k)][1]:+.3f} t{out[(nm, k)][2]:+.1f}"
                                 for k in range(1, 7)))
        lead = {}
        for k in (3, 5):
            for lag in (0, 1, 2, 5):
                aa = W[k - 1][500 + lag:]
                bb = Wm[k - 1][500: len(Wm[k - 1]) - lag] if lag else Wm[k - 1][500:]
                nn = min(len(aa), len(bb))
                lead[(k, lag)] = np.corrcoef(aa[:nn], bb[:nn])[0, 1]
        print("  lead of macro over gold, corr(W_gold[t], W_macro[t-lag]) j3/j5 at lag 0,1,2,5:",
              [round(lead[(3, lg)], 2) for lg in (0, 1, 2, 5)], [round(lead[(5, lg)], 2) for lg in (0, 1, 2, 5)])


# ------------------------------------------------------------------ part: Ehlers
def supersmoother(x: np.ndarray, period: float) -> np.ndarray:
    a1 = np.exp(-1.414 * np.pi / period)
    b1 = 2 * a1 * np.cos(1.414 * np.pi / period)
    c2, c3 = b1, -a1 * a1
    c1 = 1 - c2 - c3
    y = np.zeros_like(x)
    for i in range(len(x)):
        if i < 2:
            y[i] = x[i]
        else:
            y[i] = c1 * (x[i] + x[i - 1]) / 2 + c2 * y[i - 1] + c3 * y[i - 2]
    return y


def roofing(x: np.ndarray, hp: float = 48, lp: float = 10) -> np.ndarray:
    """Ehlers roofing filter: 2-pole high-pass (cutoff hp bars) then SuperSmoother (lp bars). Recursive = causal."""
    a = (np.cos(0.707 * 2 * np.pi / hp) + np.sin(0.707 * 2 * np.pi / hp) - 1) / np.cos(0.707 * 2 * np.pi / hp)
    h = np.zeros_like(x)
    for i in range(2, len(x)):
        h[i] = (1 - a / 2) ** 2 * (x[i] - 2 * x[i - 1] + x[i - 2]) + 2 * (1 - a) * h[i - 1] - (1 - a) ** 2 * h[i - 2]
    out = supersmoother(h, lp)
    out[:max(hp, 60)] = np.nan                                  # filter warm-up dropped
    return out


def hilbert_cycle(p: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Ehlers (2001) Hilbert-transform dominant cycle (homodyne discriminator) and analytic phase. Causal FIR+IIR."""
    n = len(p)
    sm_, de, q1, i1, i2, q2, re, im, per, sper, phase = (np.zeros(n) for _ in range(11))
    for i in range(7, n):
        sm_[i] = (4 * p[i] + 3 * p[i - 1] + 2 * p[i - 2] + p[i - 3]) / 10
        adj = 0.075 * per[i - 1] + 0.54
        de[i] = (0.0962 * sm_[i] + 0.5769 * sm_[i - 2] - 0.5769 * sm_[i - 4] - 0.0962 * sm_[i - 6]) * adj
        q1[i] = (0.0962 * de[i] + 0.5769 * de[i - 2] - 0.5769 * de[i - 4] - 0.0962 * de[i - 6]) * adj
        i1[i] = de[i - 3]
        ji = (0.0962 * i1[i] + 0.5769 * i1[i - 2] - 0.5769 * i1[i - 4] - 0.0962 * i1[i - 6]) * adj
        jq = (0.0962 * q1[i] + 0.5769 * q1[i - 2] - 0.5769 * q1[i - 4] - 0.0962 * q1[i - 6]) * adj
        i2[i] = 0.2 * (i1[i] - jq) + 0.8 * i2[i - 1]
        q2[i] = 0.2 * (q1[i] + ji) + 0.8 * q2[i - 1]
        re[i] = 0.2 * (i2[i] * i2[i - 1] + q2[i] * q2[i - 1]) + 0.8 * re[i - 1]
        im[i] = 0.2 * (i2[i] * q2[i - 1] - q2[i] * i2[i - 1]) + 0.8 * im[i - 1]
        pr = per[i - 1]
        if im[i] != 0 and re[i] != 0:
            pr = 2 * np.pi / np.arctan(im[i] / re[i])
        pr = min(pr, 1.5 * per[i - 1]) if per[i - 1] else pr
        pr = max(pr, 0.67 * per[i - 1]) if per[i - 1] else pr
        per[i] = min(max(pr, 6), 50)
        sper[i] = 0.33 * per[i] + 0.67 * sper[i - 1]
        phase[i] = np.arctan2(q1[i], i1[i])
    sper[:60] = np.nan
    return sper, phase


def part_ehlers(data: Path) -> None:
    for tf, h_list, hbars in (("1h", (1, 4, 24), 24), ("4h", (1, 6), 6), ("1d", (1, 5), 5)):
        d = load(data, tf)
        x = d["logc"].to_numpy()
        day = d["day"].to_numpy()
        cc = check_causal(roofing, x[:2500])
        cd = check_causal(lambda z: hilbert_cycle(z)[0], x[:2500], (300, 700, 1200))
        print(f"\n== Ehlers {tf} (n={len(x)}): causality err roofing {cc:.1e}, Hilbert cycle {cd:.1e}")
        rf = roofing(x)
        per, ph = hilbert_cycle(x)
        for h in h_list:
            f = fwd_ret(x, h)
            look(f"ehlers {tf} h{h}")
            ic1, se1, t1, n1 = pooled_ic(np.sign(rf), f, day)
            ic2, se2, t2, _ = pooled_ic(rf, f, day)
            lead = np.sin(ph + np.pi / 4) - np.sin(ph)
            ic3, se3, t3, _ = pooled_ic(lead, f, day)
            print(f"  h={h}: roofing sign IC {ic1:+.3f} (t {t1:+.1f}) | roofing level IC {ic2:+.3f} (t {t2:+.1f}) | "
                  f"sinewave lead-minus-sine IC {ic3:+.3f} (t {t3:+.1f}) | n {n1}")
        # dominant cycle stability and relation to the Hurst/vol: how stable is the estimate?
        pp = per[~np.isnan(per)]
        ac = np.corrcoef(pp[:-1], pp[1:])[0, 1]
        ac20 = np.corrcoef(pp[:-20], pp[20:])[0, 1]
        print(f"  dominant cycle (bars): median {np.median(pp):.1f}, IQR {np.percentile(pp, 25):.1f}-{np.percentile(pp, 75):.1f}, "
              f"share at clamp 6/50: {np.mean(pp <= 6.5):.2f}/{np.mean(pp >= 49.5):.2f}, autocorr lag1 {ac:.2f} lag20 {ac20:.2f}")
        # does cycle amplitude of the roofed series predict future range (vs RV baseline)?
        r = np.diff(x, prepend=x[0])
        rv = pd.Series(r ** 2)
        w = 20 if tf == "1d" else 120
        base = np.log(rv.rolling(w).mean().to_numpy())
        fut = np.log(rv[::-1].rolling(hbars).mean()[::-1].shift(-1).to_numpy())
        amp = np.log(pd.Series(rf ** 2).rolling(w).mean().to_numpy())
        look(f"ehlers vol incremental {tf}")
        mk = np.isfinite(base) & np.isfinite(fut) & np.isfinite(amp)
        fit = sm.OLS(fut[mk], sm.add_constant(np.column_stack([base[mk], amp[mk]]))).fit(
            cov_type="HAC", cov_kwds={"maxlags": hbars + 20})
        b0 = sm.OLS(fut[mk], sm.add_constant(base[mk])).fit()
        print(f"  roofed-amplitude adds to RV baseline for next-{hbars} RV: coef {fit.params[2]:+.3f} t {fit.tvalues[2]:+.1f}, "
              f"R2 {b0.rsquared:.3f} -> {fit.rsquared:.3f}")


# ------------------------------------------------------------------ part: Hurst
def part_hurst(data: Path) -> None:
    rng = np.random.default_rng(9)
    for tf, hz, mom in (("1h", 24, 24), ("4h", 6, 6), ("1d", 5, 20)):
        d = load(data, tf)
        x = d["logc"].to_numpy()
        r = np.diff(x, prepend=x[0])
        H = rolling_hurst(r)
        Hs = rolling_hurst(rng.permutation(r))                      # shuffle null: the finite-sample bias of R/S
        print(f"\n== Hurst (goldbot rolling R/S, window 128) {tf}: gold mean {np.nanmean(H):.3f} (sd {np.nanstd(H):.3f}) | "
              f"shuffled-returns null mean {np.nanmean(Hs):.3f} (sd {np.nanstd(Hs):.3f}); autocorr lag128 of H: "
              f"{pd.Series(H).autocorr(128):.2f}")
        # causal percentile of H in its trailing 1000 bars (no global quantile look-ahead)
        pr = pd.Series(H).rolling(1000, min_periods=300).apply(lambda w: (w[:-1] < w[-1]).mean(), raw=True).to_numpy()
        past = pd.Series(x).diff(mom).to_numpy()
        f = fwd_ret(x, hz)
        trend = np.sign(past) * f                                  # per-bar trend-following payoff (log pts)
        look(f"hurst regimes {tf}")
        for nm, sel in (("low H (<=33%)", pr <= 1 / 3), ("mid", (pr > 1 / 3) & (pr < 2 / 3)), ("high H (>=67%)", pr >= 2 / 3)):
            m = sel & np.isfinite(trend)
            t_ = trend[m][::hz]
            print(f"  {nm}: trend-following payoff over next {hz} bars ({mom}-bar sign) mean {1e4*t_.mean():+.1f} bp, "
                  f"t {t_.mean() / (t_.std() / np.sqrt(len(t_))):+.2f}, n {len(t_)}")
        # does H predict future realised range beyond RV baseline?
        rv = pd.Series(r ** 2)
        w = 20 if tf == "1d" else 120
        base = np.log(rv.rolling(w).mean().to_numpy())
        fut = np.log(rv[::-1].rolling(hz).mean()[::-1].shift(-1).to_numpy())
        look(f"hurst vol incremental {tf}")
        mk = np.isfinite(base) & np.isfinite(fut) & np.isfinite(H)
        fit = sm.OLS(fut[mk], sm.add_constant(np.column_stack([base[mk], H[mk]]))).fit(
            cov_type="HAC", cov_kwds={"maxlags": hz + 20})
        print(f"  H adds to log RV baseline for next-{hz} RV: coef {fit.params[2]:+.3f} t {fit.tvalues[2]:+.1f}")


# ------------------------------------------------------------------ part: Goertzel grid
def part_goertzel(data: Path) -> None:
    d = load(data, "1d")
    x = d["logc"].to_numpy()
    n = len(x)
    W = 360
    res = {}
    for P in range(10, 125, 5):
        h = max(1, P // 4)
        k = np.arange(W)
        e = np.exp(-2j * np.pi * k / P)
        sig = np.full(n, np.nan)
        for t in range(W, n - h):
            seg = x[t - W + 1: t + 1]
            tt = np.arange(W)
            b = np.polyfit(tt, seg, 1)
            res_ = seg - np.polyval(b, tt)
            z = (res_ * e).sum() * 2 / W                             # Goertzel/DFT bin at period P (single bin)
            amp, ph = np.abs(z), np.angle(z)
            def cyc(s, amp=amp, ph=ph, P=P):
                return amp * np.cos(2 * np.pi * s / P - ph)   # fitted sinusoid at in-window index s
            sig[t] = cyc(W - 1 + h) - cyc(W - 1)                    # fitted-sinusoid forecast of change over h days
        f = fwd_ret(x, h)
        ic, t_, nn = ic_nonoverlap(sig, f, h)
        res[P] = (h, ic, t_, nn)
        look(f"goertzel P{P}")
    print("\n== Goertzel single-bin sinusoid extrapolation (360-day causal window, linear detrend): IC of forecast change "
          "over P/4 days vs realised (non-overlapping), by period P")
    print(" | ".join(f"P{P}: {v[1]:+.2f} (t{v[2]:+.1f}, n{v[3]})" for P, v in res.items()))
    ics = np.array([v[1] for v in res.values()])
    print(f"grid mean IC {np.nanmean(ics):+.3f}, sd {np.nanstd(ics):.3f}; 30/60/90-day cells: "
          f"{res[30][1]:+.3f} / {res[60][1]:+.3f} / {res[90][1]:+.3f}; |IC| rank of 30,60,90 among {len(ics)}: "
          f"{[int((np.abs(ics) > abs(res[p][1])).sum() + 1) for p in (30, 60, 90)]}")


# ------------------------------------------------------------------ part: EMD end effect
def emd_imfs(x: np.ndarray, nimf: int = 4, sifts: int = 10) -> np.ndarray:
    from scipy.interpolate import CubicSpline
    from scipy.signal import argrelextrema
    r = x.astype(float).copy()
    imfs = []
    t = np.arange(len(x))
    for _ in range(nimf):
        h = r.copy()
        for _ in range(sifts):
            mx = argrelextrema(h, np.greater)[0]
            mn = argrelextrema(h, np.less)[0]
            if len(mx) < 4 or len(mn) < 4:
                break
            up = CubicSpline(np.r_[0, mx, len(h) - 1], np.r_[h[0], h[mx], h[-1]])(t)       # end points pinned to data
            lo = CubicSpline(np.r_[0, mn, len(h) - 1], np.r_[h[0], h[mn], h[-1]])(t)
            h = h - (up + lo) / 2
        imfs.append(h)
        r = r - h
    return np.vstack(imfs)


def part_emd(data: Path) -> None:
    d = load(data, "1d")
    x = d["logc"].to_numpy()
    W = 250
    ts = np.arange(600, len(x) - 60, 20)
    rows = []
    for t in ts:
        a = emd_imfs(x[t - W + 1: t + 1])                   # information at t only
        b = emd_imfs(x[t - W + 1: t + 61])                  # same window plus 60 future bars (not available live)
        look("emd end effect")
        rows.append([(a[k][-1] - b[k][W - 1]) for k in range(a.shape[0])] + [np.sign(a[2][-1] - a[2][-2]) == np.sign(b[2][W - 1] - b[2][W - 2])])
    r = np.array(rows, dtype=float)
    sd = np.std(np.diff(x))
    print(f"\n== EMD end effect: IMF value at t from x[:t+1] vs from x[:t+61] (same window start; {len(ts)} cuts, daily)")
    print("mean |diff| in daily-return SDs by IMF 1..4:", np.round(np.nanmean(np.abs(r[:, :4]), axis=0) / sd, 2).tolist(),
          "| IMF3 slope-sign agreement:", round(float(np.nanmean(r[:, 4])), 2))


# ------------------------------------------------------------------ part: Fibonacci time
def part_fibtime(data: Path) -> None:
    d = load(data, "1d")
    x = d["logc"].to_numpy()
    n = len(x)
    out = {}
    for w in (10, 20):                                  # pivot = extreme of a centred 2w+1 window, CONFIRMED w days later
        piv = [i for i in range(w, n - w) if x[i] == x[i - w: i + w + 1].max() or x[i] == x[i - w: i + w + 1].min()]
        piv = np.array(piv)
        cnt = np.zeros(121)
        for i, p in enumerate(piv):
            for q in piv[i + 1:]:
                if q - p <= 120:
                    cnt[q - p] += 1
        sm1 = np.convolve(cnt, np.ones(3), "same")                     # +-1 day tolerance
        fib = [13, 21, 34, 55, 89]
        pool = [k for k in range(2 * w + 1, 111) if k not in fib and k not in (30, 60, 90)]
        look(f"fibtime w{w}")
        obs = sm1[fib].mean()
        rng = np.random.default_rng(1)
        null = np.array([sm1[rng.choice(pool, 5, replace=False)].mean() for _ in range(5000)])
        # note offsets <= 2w+... are partly dictated by the pivot rule itself; the pool starts above that
        print(f"\n== Fibonacci time zones, pivot window +-{w}d (pivots {len(piv)}), counts at offsets +-1d: fib {dict((k, int(sm1[k])) for k in fib)}"
              f" | 30/60/90 {dict((k, int(sm1[k])) for k in (30, 60, 90))}")
        print(f"   mean fib-set count {obs:.1f} vs random 5-offset sets mean {null.mean():.1f} (sd {null.std():.1f}); "
              f"permutation p (>=) {np.mean(null >= obs):.3f}")
        out[w] = obs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--macro", type=Path, default=None)
    ap.add_argument("parts", nargs="*")
    a = ap.parse_args()
    parts = a.parts or ["lookahead", "scales", "macro", "ehlers", "hurst", "goertzel", "emd", "fibtime"]
    for p in parts:
        if p == "macro":
            part_macro(a.data, a.macro)
        else:
            globals()[f"part_{p}"](a.data)
    print(f"\nLOOKS behind this run: {len(LOOKS)}")


if __name__ == "__main__":
    main()
