"""Time-structure slice D: oscillators, trend and volume event study on XAUUSD (descriptive, NOT a registry trial).
docs/research/time-structure/oscillators-trend-volume.md

Every signal is defined on CLOSED bars (stamped at the bar's close), enters at the NEXT bar's open and exits at the
close H bars later (mid prices). Research window 2010-01-01..2025-09-30 only: the holdout (2025-10-01 onward) is
never loaded. Greedy non-overlap (one event per H bars) makes the t-statistics honest. Each signal is compared with
500 randomised controls (same count, same direction, same UTC hour, random bar) so the unconditional gold drift and
the time-of-day profile are removed. Costs: central $0.144/oz (docs/research/cost-model-1m-2026-10-25.md) and the
historical-regime $0.71/oz used by tight_stop_entry_study.py.

Usage (no network; reads the 1m Parquet already on disk):
  uv run python scripts/research/time_structure_osc_trend_volume.py --data ~/tmp/kb5data/raw --out ~/tmp/ts_ostv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from goldbot.data.resample import mid, resample_bars  # noqa: E402
from goldbot.features.technical import adx, atr, ema, mfi, rsi, sma  # noqa: E402

START = pd.Timestamp("2010-01-01", tz="UTC")
DISCOVERY_END = pd.Timestamp("2018-01-01", tz="UTC")   # outcomes: discovery half 2010-2017 only (README 4.1)
ALL_END = pd.Timestamp("2025-10-01", tz="UTC")         # activity tables only; the holdout (2025-10-01 on) is never read
H = {"15m": 8, "1h": 8, "4h": 6, "1d": 10}      # holding period in bars
COST_C, COST_H = 0.144, 0.71
SESS = [("Asia", 0, 7), ("London", 7, 12), ("NY", 12, 17), ("Late", 17, 21), ("Roll", 21, 24)]
N_CTRL = 500
RNG = np.random.default_rng(20261026)


# ----------------------------------------------------------------------------------------------------- data
def load(data: Path, end: pd.Timestamp = DISCOVERY_END):
    frames = []
    for f in sorted(data.glob("xauusd_1m_dukascopy_*.parquet")):
        y = int(f.stem.split("_")[-1])
        if y >= end.year + (0 if (end.month, end.day) == (1, 1) else 1):
            continue
        d = pd.read_parquet(f)
        d = d[(d["ts_utc"] >= START) & (d["ts_utc"] < end)]
        frames.append(d)
    b1 = pd.concat(frames, ignore_index=True)
    b1["visible_at"] = pd.to_datetime(b1["visible_at"], utc=True)
    b1["ts_utc"] = pd.to_datetime(b1["ts_utc"], utc=True)
    out = {}
    for tf in H:
        m = mid(resample_bars(b1, tf)).sort_values("ts_utc").reset_index(drop=True)
        m = m.dropna(subset=["open", "high", "low", "close"])
        m = m[(m["ts_utc"] >= START) & (m["ts_utc"] < end)].reset_index(drop=True)
        out[tf] = m
    return out, b1


def fwd_returns(df: pd.DataFrame, h: int) -> np.ndarray:
    """Long-side $ return entering at the next open, exiting at the close h bars after the signal bar."""
    o, c = df["open"].to_numpy(), df["close"].to_numpy()
    n = len(df)
    r = np.full(n, np.nan)
    if n > h + 1:
        r[: n - h] = c[h:] - o[1: n - h + 1]
    return r


def spaced(idx: np.ndarray, h: int) -> np.ndarray:
    out, last = [], -10**9
    for i in idx:
        if i - last >= h:
            out.append(i)
            last = i
    return np.asarray(out, dtype=int)


# ----------------------------------------------------------------------------------------------------- evaluation
def evaluate(name: str, tf: str, df: pd.DataFrame, sig: np.ndarray, fr: np.ndarray, a: np.ndarray, rows: list) -> None:
    """sig: +1 long / -1 short / 0 none per bar (signal stamped at the bar close)."""
    h = H[tf]
    ok = np.flatnonzero((sig != 0) & ~np.isnan(fr))
    ev = spaced(ok, h)
    if len(ev) < 30:
        rows.append(dict(signal=name, tf=tf, n=len(ev)))
        return
    d = sig[ev]
    g = fr[ev] * d
    hour = df["ts_utc"].dt.hour.to_numpy()
    yr = df["ts_utc"].dt.year.to_numpy()[ev]
    day = df["ts_utc"].dt.floor("D").to_numpy()[ev]
    # randomised control: same direction mix, same UTC hour, random bar (with a valid forward return)
    valid = ~np.isnan(fr)
    pools = {hr: np.flatnonzero(valid & (hour == hr)) for hr in np.unique(hour[ev])}
    ctrl = np.zeros(N_CTRL)
    for k in range(N_CTRL):
        s = 0.0
        for hr, pool in pools.items():
            m = hour[ev] == hr
            pick = pool[RNG.integers(0, len(pool), m.sum())]
            s += float((fr[pick] * d[m]).sum())
        ctrl[k] = s / len(ev)
    sd = g.std(ddof=1)
    mean = g.mean()
    cl = pd.Series(g - mean).groupby(day).sum()            # day-clustered t (CR1)
    G = len(cl)
    se = np.sqrt(G / max(G - 1, 1) * (cl ** 2).sum()) / len(ev)
    t_cl = mean / se if se > 0 else np.nan
    pos_years = int(sum(g[yr == y_].mean() > 0 for y_ in np.unique(yr)))
    atr_med = float(np.nanmedian(a[ev]))
    rows.append(dict(
        signal=name, tf=tf, n=len(ev), per_month=len(ev) / ((df["ts_utc"].iloc[-1] - df["ts_utc"].iloc[0]).days / 30.44),
        gross=mean, t=mean / (sd / np.sqrt(len(ev))), t_day=t_cl, pos_years=pos_years, excess=mean - ctrl.mean(),
        p_ctrl=float((ctrl >= mean).mean()), atr=atr_med,
        net_c=mean - COST_C, net_h=mean - COST_H, cost_atr=COST_C / atr_med,
        hit=float((g > 0).mean())))


# ----------------------------------------------------------------------------------------------------- indicators
def stoch(df: pd.DataFrame, n=14, k=3, d=3):
    ll, hh = df["low"].rolling(n).min(), df["high"].rolling(n).max()
    raw = 100 * (df["close"] - ll) / (hh - ll).replace(0, np.nan)
    kk = raw.rolling(k).mean()
    return kk, kk.rolling(d).mean(), raw


def cci(df: pd.DataFrame, n=20) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    ma = tp.rolling(n).mean()
    md = tp.rolling(n).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    return (tp - ma) / (0.015 * md)


def macd(s: pd.Series):
    m = ema(s, 12) - ema(s, 26)
    sg = m.ewm(span=9, adjust=False, min_periods=9).mean()
    return m, sg, m - sg


def dmi(df: pd.DataFrame, n=14):
    up, dn = df["high"].diff(), -df["low"].diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    a = atr(df, n)
    return (100 * pdm.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / a,
            100 * mdm.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / a)


def supertrend(df: pd.DataFrame, n=10, mult=3.0) -> np.ndarray:
    a = atr(df, n).to_numpy()
    h, lw, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    hl2 = (h + lw) / 2
    ub, lb = hl2 + mult * a, hl2 - mult * a
    fu, fl = ub.copy(), lb.copy()
    d = np.zeros(len(c))
    for i in range(1, len(c)):
        if np.isnan(a[i]) or np.isnan(a[i - 1]):
            continue
        fu[i] = ub[i] if (ub[i] < fu[i - 1] or c[i - 1] > fu[i - 1]) else fu[i - 1]
        fl[i] = lb[i] if (lb[i] > fl[i - 1] or c[i - 1] < fl[i - 1]) else fl[i - 1]
        if d[i - 1] <= 0 and c[i] > fu[i - 1]:
            d[i] = 1
        elif d[i - 1] >= 0 and c[i] < fl[i - 1]:
            d[i] = -1
        else:
            d[i] = d[i - 1] if d[i - 1] != 0 else 0
    return d


def cross(x: pd.Series, y) -> tuple[np.ndarray, np.ndarray]:
    xv = x.to_numpy()
    yv = y.to_numpy() if hasattr(y, "to_numpy") else np.full(len(xv), float(y))
    p = np.r_[np.nan, xv[:-1]]
    py = np.r_[np.nan, yv[:-1]]
    return (xv > yv) & (p <= py), (xv < yv) & (p >= py)


def pivots(low: np.ndarray, high: np.ndarray, k: int):
    lw = pd.Series(low).rolling(2 * k + 1, center=True).min().to_numpy()
    hw = pd.Series(high).rolling(2 * k + 1, center=True).max().to_numpy()
    return np.flatnonzero(low == lw), np.flatnonzero(high == hw)


def divergence(df: pd.DataFrame, osc: np.ndarray, k=3, maxgap=60) -> np.ndarray:
    """Regular divergence, stamped at the pivot's confirming bar (i + k): no repainting."""
    low, high = df["low"].to_numpy(), df["high"].to_numpy()
    pl, ph = pivots(low, high, k)
    sig = np.zeros(len(df))
    for piv, price, bull in ((pl, low, 1), (ph, high, -1)):
        for a, b in zip(piv[:-1], piv[1:]):
            if b - a > maxgap or b + k >= len(df) or np.isnan(osc[a]) or np.isnan(osc[b]):
                continue
            if bull == 1 and price[b] < price[a] and osc[b] > osc[a] and osc[a] < 40:
                sig[b + k] = 1
            if bull == -1 and price[b] > price[a] and osc[b] < osc[a] and osc[a] > 60:
                sig[b + k] = -1
    return sig


def sess_mask(df: pd.DataFrame, lo: int, hi: int) -> np.ndarray:
    hr = df["ts_utc"].dt.hour.to_numpy()
    return (hr >= lo) & (hr < hi)


# ----------------------------------------------------------------------------------------------------- signal sets
def oscillator_signals(tf: str, df: pd.DataFrame) -> dict[str, np.ndarray]:
    c = df["close"]
    out: dict[str, np.ndarray] = {}
    r = rsi(c, 14)
    up, dn = cross(r, 30)                                  # re-entry from oversold = cross back above 30
    dup, ddn = cross(r, 70)
    out["RSI14 oversold re-entry long / overbought re-entry short"] = (up.astype(int) - ddn.astype(int)).astype(float)
    out["RSI14 momentum: cross above 70 long / below 30 short"] = (dup.astype(int) - dn.astype(int)).astype(float)
    k, d, raw = stoch(df)
    kup, kdn = cross(k, d)
    out["Stoch(14,3,3) K>D below 20 long / K<D above 80 short"] = (((kup) & (k.to_numpy() < 20)).astype(int)
                                                                     - ((kdn) & (k.to_numpy() > 80)).astype(int)).astype(float)
    wr = raw - 100                                         # Williams %R = raw %K - 100
    wu, _ = cross(wr, -80)
    _, wd = cross(wr, -20)
    out["Williams %R14 re-entry -80 long / -20 short"] = (wu.astype(int) - wd.astype(int)).astype(float)
    cc = cci(df)
    cu, _ = cross(cc, -100)
    _, cd = cross(cc, 100)
    out["CCI20 re-entry -100 long / +100 short"] = (cu.astype(int) - cd.astype(int)).astype(float)
    m = mfi(df, 12)
    mu, _ = cross(m, 20)
    _, md = cross(m, 80)
    out["MFI12 (tick) re-entry 20 long / 80 short"] = (mu.astype(int) - md.astype(int)).astype(float)
    m50u, m50d = cross(m, 50)
    out["MFI12 (tick) 50-cross follow"] = (m50u.astype(int) - m50d.astype(int)).astype(float)
    ml, sg, hist = macd(c)
    zu, zd = cross(ml, 0)
    out["MACD(12,26,9) zero-line cross follow"] = (zu.astype(int) - zd.astype(int)).astype(float)
    su, sd_ = cross(ml, sg)
    out["MACD signal-line cross follow"] = (su.astype(int) - sd_.astype(int)).astype(float)
    out["RSI14 regular divergence (pivot k=3, confirmed)"] = divergence(df, r.to_numpy())
    out["MFI12 (tick) regular divergence (pivot k=3)"] = divergence(df, m.to_numpy())
    return out


def trend_signals(tf: str, df: pd.DataFrame) -> dict[str, np.ndarray]:
    c = df["close"]
    out: dict[str, np.ndarray] = {}
    ad = adx(df, 14)
    pdi, mdi = dmi(df, 14)
    dirn = np.sign((pdi - mdi).to_numpy())
    a14 = ad.to_numpy()
    out["DMI direction when ADX>25 (state, sampled every H bars)"] = np.where(a14 > 25, dirn, 0.0)
    out["DMI direction when ADX<18 (chop, same direction)"] = np.where(a14 < 18, dirn, 0.0)
    out["DI cross with ADX>20"] = np.where(a14 > 20, np.where((pdi > mdi) & (pdi.shift() <= mdi.shift()), 1.0,
                                                           np.where((pdi < mdi) & (pdi.shift() >= mdi.shift()), -1.0, 0.0)), 0.0)
    e20, e50, s200 = ema(c, 20), ema(c, 50), sma(c, 200)
    u, d = cross(e20 - e50, 0)
    out["EMA20/50 cross follow"] = (u.astype(int) - d.astype(int)).astype(float)
    u, d = cross(c - s200, 0)
    out["Close vs SMA200 cross follow"] = (u.astype(int) - d.astype(int)).astype(float)
    reg = np.where((c > e50) & (e50 > s200), 1.0, np.where((c < e50) & (e50 < s200), -1.0, 0.0))
    out["MA regime state close>EMA50>SMA200 (sampled every H bars)"] = reg
    st = supertrend(df, 10, 3.0)
    flip = np.r_[0.0, np.diff(st)]
    out["Supertrend(10,3) flip follow"] = np.where(np.abs(flip) >= 1, np.sign(flip), 0.0) * (st != 0)
    lb = {"15m": 96, "1h": 120, "4h": 120, "1d": 120}[tf]
    ret = c.diff(lb)
    vol = c.diff().rolling(lb).std() * np.sqrt(lb)
    z = (ret / vol).to_numpy()
    out[f"TSMOM sign of {lb}-bar vol-scaled return (every H bars)"] = np.where(np.abs(z) > 0.5, np.sign(z), 0.0)
    return out


def sample_every(sig: np.ndarray, h: int) -> np.ndarray:
    """Turn a persistent state into events every h bars."""
    out = np.zeros_like(sig)
    idx = np.arange(0, len(sig), h)
    out[idx] = sig[idx]
    return out


STATE_SIGNALS = ("state", "every H", "TSMOM")


# ----------------------------------------------------------------------------------------------------- volume
def vwap_signals(df: pd.DataFrame) -> dict[str, np.ndarray]:
    """15m / 1h: session VWAP (tick-weighted typical price) anchored at the London (07:00 UTC) and rollover (22:00 UTC)."""
    out = {}
    tp = ((df["high"] + df["low"] + df["close"]) / 3).to_numpy()
    w = df["tick_count"].astype(float).clip(lower=1e-9).to_numpy()
    ts = df["ts_utc"]
    a = atr(df, 14).to_numpy()
    for label, hour in (("day(22:00)", 22), ("London(07:00)", 7), ("NY(12:00)", 12)):
        shifted = ts - pd.Timedelta(hours=hour)
        key = (shifted.dt.floor("D")).to_numpy()
        g = pd.DataFrame({"k": key, "pv": tp * w, "v": w})
        cum_pv, cum_v = g.groupby("k")["pv"].cumsum().to_numpy(), g.groupby("k")["v"].cumsum().to_numpy()
        vw = cum_pv / cum_v
        dev = (df["close"].to_numpy() - vw) / a
        # within the first 4 bars of an anchor the VWAP is the price itself: require >= 8 bars since anchor
        n_since = g.groupby("k").cumcount().to_numpy()
        ok = n_since >= 8
        o, cl = df["open"].to_numpy(), df["close"].to_numpy()
        fade = np.where(ok & (dev < -1.0) & (cl > o), 1.0, np.where(ok & (dev > 1.0) & (cl < o), -1.0, 0.0))
        follow = np.where(ok & (dev > 1.0) & (cl > o), 1.0, np.where(ok & (dev < -1.0) & (cl < o), -1.0, 0.0))
        out[f"VWAP {label}: stretch >1 ATR then turn candle -> fade"] = fade
        out[f"VWAP {label}: stretch >1 ATR with candle -> follow"] = follow
    return out


def avwap_signals(df: pd.DataFrame, days: int, per_day: int) -> dict[str, np.ndarray]:
    """Anchored VWAP from the rolling N-day low / high (anchor known in real time: the extreme of the past N days). Touch and reject."""
    n = days * per_day
    tp = ((df["high"] + df["low"] + df["close"]) / 3).to_numpy()
    w = df["tick_count"].astype(float).clip(lower=1e-9).to_numpy()
    cpv, cv = np.cumsum(tp * w), np.cumsum(w)
    low, high, close = df["low"].to_numpy(), df["high"].to_numpy(), df["close"].to_numpy()
    sw_low = pd.Series(low).rolling(n, min_periods=n).apply(np.argmin, raw=True).to_numpy()
    sw_high = pd.Series(high).rolling(n, min_periods=n).apply(np.argmax, raw=True).to_numpy()
    idx = np.arange(len(df))
    res = {}
    for nm, sw, sign in (("low", sw_low, 1.0), ("high", sw_high, -1.0)):
        anchor = idx - n + 1 + np.nan_to_num(sw, nan=0).astype(int)
        base_pv = np.where(anchor > 0, cpv[np.maximum(anchor - 1, 0)], 0.0)
        base_v = np.where(anchor > 0, cv[np.maximum(anchor - 1, 0)], 0.0)
        av = (cpv - base_pv) / np.maximum(cv - base_v, 1e-9)
        valid = ~np.isnan(sw) & (idx - anchor >= 24)
        if nm == "low":
            hit = valid & (low <= av) & (close > av)
        else:
            hit = valid & (high >= av) & (close < av)
        res[f"AVWAP from {days}d {nm}: touch and reject ({'long' if sign > 0 else 'short'})"] = np.where(hit, sign, 0.0)
    return res


def volume_flow_signals(df: pd.DataFrame) -> dict[str, np.ndarray]:
    out = {}
    c, h, lw, v = df["close"], df["high"], df["low"], df["tick_count"].astype(float)
    obv = (np.sign(c.diff()).fillna(0) * v).cumsum()
    up, dn = cross(obv, ema(obv, 20))
    out["OBV(tick) cross above/below its EMA20 follow"] = (up.astype(int) - dn.astype(int)).astype(float)
    # OBV divergence: new 40-bar price low but OBV above its 40-bar-ago low (stamped at the closed bar, rolling not pivot)
    plow = c <= c.rolling(40).min()
    phigh = c >= c.rolling(40).max()
    out["OBV(tick) divergence at 40-bar price extreme (fade)"] = np.where(
        plow & (obv > obv.shift(1).rolling(40).min()), 1.0, np.where(phigh & (obv < obv.shift(1).rolling(40).max()), -1.0, 0.0))
    mfm = ((c - lw) - (h - c)) / (h - lw).replace(0, np.nan)
    cmf = (mfm * v).rolling(20).sum() / v.rolling(20).sum()
    cu, _ = cross(cmf, 0.1)
    _, cd = cross(cmf, -0.1)
    out["CMF20(tick) cross +0.1 long / -0.1 short (follow)"] = (cu.astype(int) - cd.astype(int)).astype(float)
    cu2, _ = cross(cmf, -0.1)
    _, cd2 = cross(cmf, 0.1)
    out["CMF20(tick) re-entry from -0.1 long / +0.1 short (fade)"] = (cu2.astype(int) - cd2.astype(int)).astype(float)
    return out


def spike_signals(df: pd.DataFrame, tf: str) -> dict[str, np.ndarray]:
    """Tick-volume spike at a session open: z of the bar's ticks vs the same hour-of-week median/MAD of the previous 8 weeks."""
    ts = df["ts_utc"]
    key = (ts.dt.dayofweek * 24 + ts.dt.hour + ts.dt.minute / 60).to_numpy()
    lt = np.log(df["tick_count"].astype(float).clip(lower=1e-9)).to_numpy()
    s = pd.Series(lt)
    grp = s.groupby(key)
    med = grp.transform(lambda x: x.shift(1).rolling(8, min_periods=4).median())
    mad = grp.transform(lambda x: x.shift(1).rolling(8, min_periods=4).std())
    z = ((s - med) / mad).to_numpy()
    body = np.sign(df["close"] - df["open"]).to_numpy()
    out = {}
    for nm, hr, mn in (("London open 07:00", 7, 0), ("NY 12:00", 12, 0), ("NY cash 13:30", 13, 30), ("Asia 00:00", 0, 0)):
        at = (ts.dt.hour.to_numpy() == hr) & (ts.dt.minute.to_numpy() == mn)
        if tf == "1h" and mn:
            continue
        out[f"Open spike {nm}: z>1.5, follow bar direction"] = np.where(at & (z > 1.5), body, 0.0)
        out[f"Open spike {nm}: z>1.5, fade bar direction"] = np.where(at & (z > 1.5), -body, 0.0)
    return out


def volume_profile_signal(b1: pd.DataFrame, m15: pd.DataFrame, bin_usd=1.0, va=0.70) -> dict[str, np.ndarray]:
    """Prior-day (22:00 UTC to 22:00 UTC) tick-weighted volume profile on 1m typical price. POC and 70% value area are
    computed from the COMPLETED day only and applied from the next day, so nothing is built on an unfinished session."""
    mm = mid(b1)
    tp = ((mm["high"] + mm["low"] + mm["close"]) / 3).to_numpy()
    w = mm["tick_count"].astype(float).clip(lower=1e-9).to_numpy()
    day = (pd.to_datetime(b1["ts_utc"], utc=True) - pd.Timedelta(hours=22)).dt.floor("D").to_numpy()
    prof = pd.DataFrame({"day": day, "bin": np.floor(tp / bin_usd) * bin_usd, "w": w}).groupby(["day", "bin"])["w"].sum().reset_index()
    rows = {}
    for d, g in prof.groupby("day"):
        g = g.sort_values("bin")
        poc = g.loc[g["w"].idxmax(), "bin"] + bin_usd / 2
        order = g.sort_values("w", ascending=False)
        cum = order["w"].cumsum() / order["w"].sum()
        keep = order[cum.shift(fill_value=0) < va]
        rows[d] = (poc, keep["bin"].min(), keep["bin"].max() + bin_usd)
    days = sorted(rows)
    ts = m15["ts_utc"]
    dkey = (ts - pd.Timedelta(hours=22)).dt.floor("D")
    # previous trading day may be a weekend gap: take the latest completed profile before this key
    keys = np.array(days, dtype="datetime64[ns]")
    pos = np.searchsorted(keys, dkey.to_numpy().astype("datetime64[ns]"), side="left") - 1
    poc = np.where(pos >= 0, [rows[days[p]][0] if p >= 0 else np.nan for p in pos], np.nan)
    val = np.where(pos >= 0, [rows[days[p]][1] if p >= 0 else np.nan for p in pos], np.nan)
    vah = np.where(pos >= 0, [rows[days[p]][2] if p >= 0 else np.nan for p in pos], np.nan)
    a = atr(m15, 14).to_numpy()
    c, o = m15["close"].to_numpy(), m15["open"].to_numpy()
    hr = ts.dt.hour.to_numpy()
    cp = np.r_[np.nan, c[:-1]]
    out = {}
    out["Prior-day POC: price within 0.25 ATR and turns toward POC from >1 ATR away"] = np.where(
        (c - poc > 1.0 * a) & (c < o), -1.0, np.where((poc - c > 1.0 * a) & (c > o), 1.0, 0.0))
    # value-area rule: first 15m close back inside the prior value area after being outside at the London open
    lo_open = (hr == 7)
    state = pd.Series(np.where(lo_open & (np.r_[np.nan, c[:-1]] > vah), 1, np.where(lo_open & (np.r_[np.nan, c[:-1]] < val), -1, np.nan))).ffill(limit=40).to_numpy()
    inside = (c <= vah) & (c >= val)
    prev_inside = np.r_[False, inside[:-1]]
    sig = np.where((state == 1) & inside & ~prev_inside & (hr >= 7) & (hr < 16), -1.0,
                   np.where((state == -1) & inside & ~prev_inside & (hr >= 7) & (hr < 16), 1.0, 0.0))
    out["Prior-day value area: London open outside, first close back inside -> trade toward POC (80% rule)"] = sig
    out["Prior-day VAH/VAL break by a 15m close, follow"] = np.where(
        (c > vah) & (cp <= vah), 1.0, np.where((c < val) & (cp >= val), -1.0, 0.0))
    return out


# ----------------------------------------------------------------------------------------------------- proxy diagnostics
def tick_proxy(b1: pd.DataFrame, bars: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for tf, df in bars.items():
        lt = np.log(df["tick_count"].astype(float).clip(lower=1e-9))
        lr = np.log((df["high"] - df["low"]).clip(lower=1e-3))
        sp = np.log(df["spread"].clip(lower=1e-3))
        ts = df["ts_utc"]
        how = ts.dt.dayofweek * 24 + ts.dt.hour + (ts.dt.minute // 15) * 0.25
        res_t = lt - lt.groupby(how).transform("median")
        res_r = lr - lr.groupby(how).transform("median")
        ar = np.log(df["close"].diff().abs().clip(lower=1e-3))
        rows.append(dict(tf=tf, n=len(df),
                         corr_logtick_logrange=lt.corr(lr), corr_after_removing_hour_of_week=res_t.corr(res_r),
                         corr_logtick_logabsret=lt.corr(ar), corr_logtick_logspread=lt.corr(sp),
                         share_tick_var_from_hour_of_week=1 - res_t.var() / lt.var()))
    return pd.DataFrame(rows)


def tick_scale_by_year(b1: pd.DataFrame) -> pd.DataFrame:
    y = pd.to_datetime(b1["ts_utc"], utc=True).dt.year
    g = b1.groupby(y)["tick_count"].agg(["median", "mean", "count"])
    return g



def range_forecast(df: pd.DataFrame, h: int) -> dict:
    """Does the slice beat 'range/volatility is what is predictable'? Target = log of the next-h-bar high-low range / ATR.
    Baseline = log ATR ratio (ATR14 / ATR100) + hour-of-week median of the target. Extra = tick-volume z, ADX, |recent move|.
    Fit on 2010-2014, score on 2015-2017 (both discovery half): out-of-sample R^2 of baseline vs baseline + extras."""
    a14, a100 = atr(df, 14), atr(df, 100)
    hi = df["high"][::-1].rolling(h, min_periods=h).max()[::-1].shift(-1)
    lo = df["low"][::-1].rolling(h, min_periods=h).min()[::-1].shift(-1)
    y = np.log((hi - lo) / a14)
    ts = df["ts_utc"]
    how = (ts.dt.dayofweek * 24 + ts.dt.hour + (ts.dt.minute // 15) * 0.25)
    lt = np.log(df["tick_count"].astype(float).clip(lower=1e-9))
    tz = lt.rolling(h).mean() - lt.groupby(how).transform(lambda x: x.shift(1).rolling(8, min_periods=4).median())
    X0 = pd.DataFrame({"base": np.log(a14 / a100), "one": 1.0}, index=df.index)
    X1 = X0.assign(tickz=tz, adx=adx(df, 14), move=np.log1p(df["close"].diff(h).abs() / a14))
    yr = ts.dt.year
    out = {}
    for nm, X in (("baseline: ATR14/ATR100 + hour-of-week", X0), ("+ tick z, ADX, |move|", X1)):
        d = pd.concat([y.rename("y"), X, how.rename("how")], axis=1).dropna()
        tr, te = d[yr.loc[d.index] <= 2014], d[yr.loc[d.index] >= 2015]
        prof = tr.groupby("how")["y"].median()
        def design(z):
            Z = z.drop(columns=["y", "how"]).copy()
            Z["prof"] = z["how"].map(prof).fillna(tr["y"].median())
            return Z
        beta = np.linalg.lstsq(design(tr).to_numpy(), tr["y"].to_numpy(), rcond=None)[0]
        pred = design(te).to_numpy() @ beta
        ss = ((te["y"] - te["y"].mean()) ** 2).sum()
        out[nm] = 1 - ((te["y"] - pred) ** 2).sum() / ss
    return out


def run(args) -> None:
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    bars, b1 = load(Path(args.data).expanduser())
    bars_all, b1_all = load(Path(args.data).expanduser(), ALL_END)    # activity/cost tables only (no outcomes)
    print({k: (len(v), str(v['ts_utc'].iloc[0])[:10], str(v['ts_utc'].iloc[-1])[:10]) for k, v in bars.items()})
    rows: list[dict] = []
    for tf, df in bars.items():
        h = H[tf]
        fr = fwd_returns(df, h)
        a = atr(df, 14).to_numpy()
        sets: dict[str, np.ndarray] = {}
        sets.update(oscillator_signals(tf, df))
        sets.update(trend_signals(tf, df))
        if tf in ("15m", "1h"):
            sets.update(vwap_signals(df))
            sets.update(spike_signals(df, tf))
        if tf in ("1h", "4h"):
            per_day = {"1h": 24, "4h": 6}[tf]
            for days in (30, 60, 90):
                sets.update(avwap_signals(df, days, per_day))
        sets.update(volume_flow_signals(df))
        if tf == "15m":
            sets.update(volume_profile_signal(b1, df))
        for name, sig in sets.items():
            if any(s in name for s in STATE_SIGNALS):
                sig = sample_every(np.nan_to_num(sig), h)
            evaluate(name, tf, df, np.nan_to_num(np.asarray(sig, dtype=float)), fr, a, rows)
        # session-conditioned oscillator events
        if tf in ("15m", "1h"):
            base = oscillator_signals(tf, df)
            for nm in ("RSI14 oversold re-entry long / overbought re-entry short", "MFI12 (tick) re-entry 20 long / 80 short",
                       "Stoch(14,3,3) K>D below 20 long / K<D above 80 short"):
                for sname, lo, hi in SESS[:4]:
                    sg = np.where(sess_mask(df, lo, hi), base[nm], 0.0)
                    evaluate(f"[{sname}] {nm}", tf, df, sg, fr, a, rows)
            tr = trend_signals(tf, df)
            for nm in ("DMI direction when ADX>25 (state, sampled every H bars)", "TSMOM sign of 96-bar vol-scaled return (every H bars)",
                       "TSMOM sign of 120-bar vol-scaled return (every H bars)"):
                if nm not in tr:
                    continue
                for sname, lo, hi in SESS[:4]:
                    sg = sample_every(np.where(sess_mask(df, lo, hi), tr[nm], 0.0), h)
                    evaluate(f"[{sname}] {nm}", tf, df, sg, fr, a, rows)
    res = pd.DataFrame(rows)
    res.to_csv(out / "results.csv", index=False)
    tick_proxy(b1_all, bars_all).to_csv(out / "tick_proxy.csv", index=False)
    tick_scale_by_year(b1_all).to_csv(out / "tick_scale_by_year.csv")
    # trend strength by session: ADX mean and DI-sign continuation by UTC session (1h)
    df = bars["1h"]
    ad = adx(df, 14).to_numpy()
    pdi, mdi = dmi(df, 14)
    fr1 = fwd_returns(df, H["1h"])
    dirn = np.sign((pdi - mdi).to_numpy())
    hr = df["ts_utc"].dt.hour.to_numpy()
    rows2 = []
    for sname, lo, hi in SESS:
        m = (hr >= lo) & (hr < hi) & ~np.isnan(fr1) & ~np.isnan(ad)
        s25 = m & (ad > 25)
        rows2.append(dict(session=sname, adx_mean=float(np.nanmean(ad[m])), share_adx_gt25=float((ad[m] > 25).mean()),
                          abs_ret8_atr=float(np.nanmean(np.abs(fr1[m]))),
                          cont_mean_adx_gt25=float(np.nanmean(fr1[s25] * dirn[s25])), n_adx_gt25=int(s25.sum())))
    pd.DataFrame(rows2).to_csv(out / "trend_by_session_1h.csv", index=False)
    rf = [dict(tf=tf, model=k, oos_r2=v) for tf in ("1h", "4h") for k, v in range_forecast(bars[tf], H[tf]).items()]
    pd.DataFrame(rf).to_csv(out / "range_forecast.csv", index=False)
    print(pd.DataFrame(rf).round(4).to_string())
    print(res.round(3).to_string())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="dir with xauusd_1m_dukascopy_<year>.parquet")
    ap.add_argument("--out", required=True)
    run(ap.parse_args())
