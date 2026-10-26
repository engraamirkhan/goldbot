"""Slice E measurements on real gold, DISCOVERY HALF ONLY (2010-01-01 .. 2017-12-31). Read-only, no trial.

Only the 2010-2017 yearly files are opened; nothing from 2018 on is read (README 4.1). Mid prices from the Dukascopy
1m bid/ask bars; spread from the bar for the cost. Outputs a JSON of tables (printed too).

Measures: (1) hindsight vs causal on gold (swing and FVG), (2) E-1 order-block retest on 15m vs two randomised
controls, net of the research-prior cost, (3) E-2 FVG midpoint retrace and range diagnostic, (4) E-4 value-area 80%
rule vs random value areas, (5) the ATR15 / stop-size by year behind the #88 cost question.

Usage: uv run python scripts/research/smc_gold_discovery.py --raw ~/tmp/ts/raw --out ~/tmp/smc_gold.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from smc_hindsight_demo import atr, bracket, fvgs, swing_highs  # noqa: E402

from goldbot.data.timeutil import epoch_ns  # noqa: E402

YEARS = range(2010, 2018)             # discovery half; the loader never opens another file
NY = "America/New_York"
FIXED_USD = 0.37                      # research prior: commission/slippage per oz round trip, plus the bar spread
BROKER_USD = 0.144                    # cost-model-1m-2026-10-25.md central
K_SWING, DISP_D, EXPIRY, TARGET_R, TIME_EXIT, BUF = 5, 1.5, 32, 2.0, 32, 0.25
rng = np.random.default_rng(20261026)


def load(raw: Path) -> pd.DataFrame:
    parts = [pd.read_parquet(raw / f"xauusd_1m_dukascopy_{y}.parquet",
                             columns=["ts_utc", "bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high",
                                      "ask_low", "ask_close", "tick_count"]) for y in YEARS]
    d = pd.concat(parts, ignore_index=True)
    d["ts_utc"] = pd.to_datetime(d["ts_utc"], utc=True).astype("datetime64[ns, UTC]")
    d = d[d["ts_utc"] < pd.Timestamp("2018-01-01", tz="UTC")].sort_values("ts_utc").drop_duplicates("ts_utc")
    for k in ("open", "high", "low", "close"):
        d[k] = (d[f"bid_{k}"] + d[f"ask_{k}"]) / 2
    d["spread"] = d["ask_close"] - d["bid_close"]
    assert d["ts_utc"].max() < pd.Timestamp("2018-01-01", tz="UTC")
    return d[["ts_utc", "open", "high", "low", "close", "tick_count", "spread"]].set_index("ts_utc")


def bars(m1: pd.DataFrame, rule: str) -> pd.DataFrame:
    g = m1.resample(rule, label="left", closed="left")
    b = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                      "close": g["close"].last(), "tick_count": g["tick_count"].sum(), "spread": g["spread"].mean(),
                      "n": g["close"].count()})
    b = b[b["n"] >= (10 if rule == "15min" else 1)].dropna(subset=["close"])
    b["bar_time"] = b.index
    return b.reset_index(drop=True)


def dclust(x: np.ndarray, day: np.ndarray) -> tuple[float, float, int]:
    """Mean, CR1 day-clustered t, n."""
    ok = np.isfinite(x)
    x, day = x[ok], day[ok]
    n = len(x)
    if n < 10:
        return float("nan"), float("nan"), n
    m = x.mean()
    df = pd.DataFrame({"e": x - m, "d": day}).groupby("d")["e"].sum()
    g = len(df)
    var = (g / (g - 1)) * (df**2).sum() / n**2
    return float(m), float(m / np.sqrt(var)), n


def day_boot(diff: np.ndarray, day: np.ndarray, reps: int = 1000) -> tuple[float, float]:
    ok = np.isfinite(diff)
    s = pd.DataFrame({"x": diff[ok], "d": day[ok]}).groupby("d")["x"].agg(["sum", "count"])
    sums, cnts = s["sum"].to_numpy(), s["count"].to_numpy()
    idx = rng.integers(0, len(s), size=(reps, len(s)))
    est = sums[idx].sum(axis=1) / cnts[idx].sum(axis=1)
    return float(np.percentile(est, 2.5)), float(np.percentile(est, 97.5))


# ---------------------------------------------------------------- E-1: order-block retest

def ob_events(b: pd.DataFrame) -> pd.DataFrame:
    """Causal order blocks (doc section 2 row 5). One row per event: bar b (BOS close), side, zone, stop."""
    o, h, lo, c = (b[x].to_numpy() for x in ("open", "high", "low", "close"))
    a = atr(b)                                        # Wilder ATR(14) at bar i (includes bar i); use a[i-1] in filters
    n, k = len(b), K_SWING
    rows = []
    ph = pl = None                                    # latest unbroken confirmed pivot high / low: (price, bar index)
    for j in range(2 * k, n):
        i = j - k
        w_h, w_l = h[i - k:j + 1], lo[i - k:j + 1]
        if h[i] == w_h.max() and (w_h == h[i]).sum() == 1:
            ph = (h[i], i)
        if lo[i] == w_l.min() and (w_l == lo[i]).sum() == 1:
            pl = (lo[i], i)
        if np.isnan(a[j - 1]):
            continue
        for side in (1, -1):
            piv = ph if side > 0 else pl
            if piv is None:
                continue
            p, pi = piv
            broke = c[j] > p if side > 0 else c[j] < p
            if not broke:
                continue
            if side > 0:
                ph = None
            else:
                pl = None
            seg = slice(pi, j + 1)
            s = pi + int(np.argmin(lo[seg])) if side > 0 else pi + int(np.argmax(h[seg]))
            rng_ = h[s + 1:j + 1] - lo[s + 1:j + 1]
            body = np.abs(c[s + 1:j + 1] - o[s + 1:j + 1])
            if len(rng_) == 0:
                continue
            pos = (c[s + 1:j + 1] - lo[s + 1:j + 1]) / np.where(rng_ > 0, rng_, 1)
            ok_disp = (rng_ >= DISP_D * a[s:j]) & (body >= 0.6 * rng_) & (
                (pos >= 0.75) if side > 0 else (pos <= 0.25))
            if not ok_disp.any():
                continue
            cand = [t for t in range(s, j) if (c[t] < o[t] if side > 0 else c[t] > o[t])]
            if not cand:
                continue
            t = cand[-1]
            zlo, zhi = lo[t], h[t]
            entry = zhi if side > 0 else zlo
            stop = (zlo - BUF * a[j - 1]) if side > 0 else (zhi + BUF * a[j - 1])
            if (side > 0 and c[j] <= entry) or (side < 0 and c[j] >= entry):
                continue                              # price already inside the zone at the break close
            rows.append((j, side, zlo, zhi, entry, stop, a[j - 1]))
    return pd.DataFrame(rows, columns=["b", "side", "zlo", "zhi", "entry", "stop", "atr"])


def trade(b_h: np.ndarray, b_l: np.ndarray, b_c: np.ndarray, j: int, side: int, entry: float, stop: float,
          inval: float) -> tuple[float, int]:
    """Gross R and fill bar; nan when never filled or invalidated by a close through `inval` before the fill."""
    risk = abs(entry - stop)
    target = entry + side * TARGET_R * risk
    fill = -1
    n = len(b_h)
    for t in range(j + 1, min(n, j + 1 + EXPIRY)):
        if (side > 0 and b_c[t] < inval) or (side < 0 and b_c[t] > inval):
            if fill < 0:
                return np.nan, -1
        if fill < 0:
            if (side > 0 and b_l[t] <= entry) or (side < 0 and b_h[t] >= entry):
                fill = t
            else:
                continue
        if (side > 0 and b_l[t] <= stop) or (side < 0 and b_h[t] >= stop):
            return -1.0, fill
        if t > fill and ((side > 0 and b_h[t] >= target) or (side < 0 and b_l[t] <= target)):
            return TARGET_R, fill
        if t - fill >= TIME_EXIT:
            return side * (b_c[t] - entry) / risk, fill
    return (np.nan, -1) if fill < 0 else (side * (b_c[min(n - 1, fill + TIME_EXIT)] - entry) / risk, fill)


def run_ob(b: pd.DataFrame, ev: pd.DataFrame) -> dict:
    h, lo, c = b["high"].to_numpy(), b["low"].to_numpy(), b["close"].to_numpy()
    sp = b["spread"].to_numpy()
    ny = b["bar_time"].dt.tz_convert(NY)
    hour = (ny.dt.hour + ny.dt.minute / 60).to_numpy()
    day = (ny + pd.Timedelta(hours=7)).dt.normalize().dt.tz_localize(None).to_numpy().astype("datetime64[D]").astype(int)
    ev = ev[(hour[ev["b"]] >= 2) & (hour[ev["b"]] < 12)].reset_index(drop=True)      # London + NY-morning window
    dist = np.abs(ev["entry"] - ev["stop"]).to_numpy()
    out = {"events_gate": int(len(ev))}
    rows = []
    for e in ev.itertuples(index=False):
        inval = e.zlo if e.side > 0 else e.zhi
        r, f = trade(h, lo, c, e.b, e.side, e.entry, e.stop, inval)
        rows.append((r, f))
    real_r = np.array([r for r, _ in rows])
    fill_i = np.array([f for _, f in rows])
    filled = np.isfinite(real_r)
    out["filled"] = int(filled.sum())
    risk = dist
    cost_prior = np.where(filled, (sp[np.clip(fill_i, 0, None)] + FIXED_USD) / risk, np.nan)
    cost_broker = np.where(filled, BROKER_USD / risk, np.nan)
    ev_day = day[ev["b"].to_numpy()]
    out["stop_usd_median"] = float(np.median(risk[filled]))
    out["stop_atr_median"] = float(np.median(risk[filled] / ev["atr"].to_numpy()[filled]))
    out["share_stop_le_6.65"] = float(np.mean(risk[filled] <= 6.65))
    out["by_year_events"] = {int(y): int(((ev_day[filled] + 0) // 365.25 + 1970 == y).sum()) for y in YEARS}
    # control A: same event bar and side, entry distance + stop distance from another event of the same year
    # (identity of the zone removed; BOS context, direction and distance distribution kept)
    # control B: same side, same stop/entry distances in ATR, event bar replaced by a random bar of the same NY hour
    yr = (pd.to_datetime(ev_day, unit="D").year).to_numpy()
    d_entry = np.abs(ev["entry"].to_numpy() - c[ev["b"].to_numpy()])
    a_arr = atr(b)
    R_REPS = 10
    ca = np.full((len(ev), R_REPS), np.nan)
    cb = np.full((len(ev), R_REPS), np.nan)
    cra = np.full((len(ev), R_REPS), np.nan)               # net cost in R for the control trades
    crb = np.full((len(ev), R_REPS), np.nan)
    hours_pool = {hh: np.flatnonzero((hour >= hh) & (hour < hh + 1) & np.isfinite(a_arr)) for hh in range(2, 12)}
    for ei, e in enumerate(ev.itertuples(index=False)):
        same_year = np.flatnonzero(yr == yr[ei])
        for rep in range(R_REPS):
            m = int(rng.choice(same_year))
            de, ds = d_entry[m], dist[m]                    # entry distance below/above close, stop distance
            entry = c[e.b] - e.side * de
            stop = entry - e.side * ds
            r, f = trade(h, lo, c, e.b, e.side, entry, stop, entry - e.side * de)       # inval: a close past the level
            if np.isfinite(r):
                ca[ei, rep] = r
                cra[ei, rep] = (sp[f] + FIXED_USD) / ds
            # B: random bar, same NY hour, distances rescaled by ATR
            hh = int(hour[e.b])
            if hh in hours_pool and len(hours_pool[hh]):
                bb = int(rng.choice(hours_pool[hh]))
                sc = a_arr[bb - 1] / e.atr if bb > 0 else np.nan
                if np.isfinite(sc) and bb < len(c) - 2 * EXPIRY:
                    de2, ds2 = d_entry[ei] * sc, dist[ei] * sc
                    entry2 = c[bb] - e.side * de2
                    stop2 = entry2 - e.side * ds2
                    r2, f2 = trade(h, lo, c, bb, e.side, entry2, stop2, entry2 - e.side * de2)
                    if np.isfinite(r2):
                        cb[ei, rep] = r2
                        crb[ei, rep] = (sp[f2] + FIXED_USD) / ds2
    # fills: control rows are average over their filled replicates (fill rate differs; reported)
    out["fill_rate_real"] = float(filled.mean())
    out["fill_rate_ctl_A"] = float(np.isfinite(ca).mean())
    out["fill_rate_ctl_B"] = float(np.isfinite(cb).mean())
    g_real = np.where(filled, real_r, np.nan)
    ga, gb = np.nanmean(ca, axis=1), np.nanmean(cb, axis=1)
    na, nb_ = np.nanmean(ca - cra, axis=1), np.nanmean(cb - crb, axis=1)
    m, t, n = dclust(g_real, ev_day)
    out["real_gross"] = {"mean_R": m, "t": t, "n": n}
    nr = g_real - cost_prior
    m, t, n = dclust(nr, ev_day)
    out["real_net_prior"] = {"mean_R": m, "t": t, "n": n}
    m, t, n = dclust(g_real - cost_broker, ev_day)
    out["real_net_broker"] = {"mean_R": m, "t": t, "n": n}
    out["cost_R_prior_median"] = float(np.nanmedian(cost_prior))
    for name, g, nn in (("A_shuffled_distance", ga, na), ("B_random_time", gb, nb_)):
        both = np.isfinite(g_real) & np.isfinite(g)
        diff_g = (g_real - g)[both]
        diff_n = (nr - nn)[both]
        mg, tg, ng = dclust(diff_g, ev_day[both])
        mn, tn, _ = dclust(diff_n, ev_day[both])
        out[f"vs_control_{name}"] = {
            "control_gross_mean": float(np.nanmean(g[both])), "control_net_prior_mean": float(np.nanmean(nn[both])),
            "diff_gross": mg, "diff_gross_t": tg, "diff_gross_ci95": day_boot(diff_g, ev_day[both]),
            "diff_net": mn, "diff_net_t": tn, "diff_net_ci95": day_boot(diff_n, ev_day[both]), "n_pairs": ng}
    out["hit_target_real"] = float(np.mean(g_real[filled] >= TARGET_R - 1e-9))
    out["hit_stop_real"] = float(np.mean(g_real[filled] <= -1 + 1e-9))
    return out


# ---------------------------------------------------------------- E-2: FVG diagnostics

def fvg_diag(b: pd.DataFrame, horizons=(8, 32)) -> dict:
    h, lo, c = b["high"].to_numpy(), b["low"].to_numpy(), b["close"].to_numpy()
    a = atr(b)
    g = fvgs(b)
    g = g[(g["t"] > 20) & (g["t"] < len(b) - 40)]
    g = g[(g["hi"] - g["lo"]) >= 0.25 * a[g["t"].to_numpy() - 1]]
    t = g["t"].to_numpy()
    mid = (g["hi"].to_numpy() + g["lo"].to_numpy()) / 2
    side = g["side"].to_numpy()
    d = np.abs(c[t] - mid)
    out = {"n_fvg": int(len(g))}
    yrs = b["bar_time"].dt.year.to_numpy()[t]
    for hz in horizons:
        def touched(level, tt, sd):                 # level below close for bull gaps (price comes back down to it)
            wnd_h = np.array([h[x + 1:x + 1 + hz].max() for x in tt])
            wnd_l = np.array([lo[x + 1:x + 1 + hz].min() for x in tt])
            return np.where(sd > 0, wnd_l <= level, wnd_h >= level)
        real = touched(mid, t, side)
        # control: shuffled distances inside the same year, same side
        ctl = np.zeros(len(t))
        for rep in range(20):
            perm = np.arange(len(t))
            for y in np.unique(yrs):
                ix = np.flatnonzero(yrs == y)
                perm[ix] = rng.permutation(ix)
            lvl = c[t] - side * d[perm]
            ctl += touched(lvl, t, side)
        ctl /= 20
        day = (b["bar_time"].dt.tz_convert(NY) + pd.Timedelta(hours=7)).dt.normalize().dt.tz_localize(None) \
            .to_numpy().astype("datetime64[D]").astype(int)[t]
        diff = real.astype(float) - ctl
        out[f"retrace_mid_{hz}bars"] = {"real": float(real.mean()), "control": float(ctl.mean()),
                                        "diff": float(diff.mean()), "diff_ci95": day_boot(diff, day)}
    # range predictor: next-16-bar range / ATR_{j-1} after a displacement bar with a gap vs without a gap
    hh = b["high"].to_numpy()
    rng16 = np.array([hh[x + 1:x + 17].max() - lo[x + 1:x + 17].min() for x in range(len(b) - 17)] + [np.nan] * 17)
    rngb = (hh - lo)
    disp = np.zeros(len(b), bool)
    disp[1:] = rngb[1:] >= 1.5 * a[:-1]
    gap = np.zeros(len(b), bool)
    gap[t] = True
    base = np.isfinite(rng16) & np.isfinite(a)
    prev_a = np.concatenate(([np.nan], a[:-1]))
    r = rng16 / prev_a
    sel = lambda m: r[m & base & (np.arange(len(b)) > 20)]               # noqa: E731
    with_gap, no_gap = sel(disp & gap), sel(disp & ~gap)
    out["range16_over_atr"] = {"disp_with_fvg": [float(with_gap.mean()), len(with_gap)],
                               "disp_without_fvg": [float(no_gap.mean()), len(no_gap)],
                               "all_bars": float(np.nanmean(r[base]))}
    return out


# ---------------------------------------------------------------- E-4: value-area 80% rule

def value_area(m1d: pd.DataFrame, bin_usd: float = 0.25) -> tuple[float, float, float] | None:
    tp = ((m1d["high"] + m1d["low"] + m1d["close"]) / 3).to_numpy()
    w = m1d["tick_count"].to_numpy().astype(float)
    if len(tp) < 200 or w.sum() <= 0:
        return None
    lo_, hi_ = tp.min(), tp.max()
    edges = np.arange(np.floor(lo_ / bin_usd) * bin_usd, hi_ + 2 * bin_usd, bin_usd)
    hist, _ = np.histogram(tp, bins=edges, weights=w)
    poc = int(np.argmax(hist))
    lo_i = hi_i = poc
    tot, acc = hist.sum(), hist[poc]
    while acc < 0.7 * tot:
        up = hist[hi_i + 1] + (hist[hi_i + 2] if hi_i + 2 < len(hist) else 0) if hi_i + 1 < len(hist) else -1
        dn = hist[lo_i - 1] + (hist[lo_i - 2] if lo_i - 2 >= 0 else 0) if lo_i - 1 >= 0 else -1
        if up < 0 and dn < 0:
            break
        if up >= dn:
            hi_i = min(hi_i + 2, len(hist) - 1)
            acc = hist[lo_i:hi_i + 1].sum()
        else:
            lo_i = max(lo_i - 2, 0)
            acc = hist[lo_i:hi_i + 1].sum()
    return float(edges[lo_i]), float(edges[hi_i + 1]), float(edges[poc] + bin_usd / 2)


def va_rule(m1: pd.DataFrame) -> dict:
    ny = m1.index.tz_convert(NY)
    tday = (ny + pd.Timedelta(hours=7)).normalize().tz_localize(None)
    days = pd.Series(tday, index=m1.index)
    h, lo, c = m1["high"].to_numpy(), m1["low"].to_numpy(), m1["close"].to_numpy()
    ts = epoch_ns(m1.index)
    res_real, res_ctl = [], []
    ud = pd.DatetimeIndex(days.unique()).sort_values()
    grp = {d: ix for d, ix in days.groupby(days).indices.items()}
    for d_prev, d in zip(ud[:-1], ud[1:]):
        if d.dayofweek >= 5 or d_prev not in grp:
            continue
        pv = m1.iloc[grp[d_prev]]
        va = value_area(pv)
        if va is None or d not in grp:
            continue
        val, vah, _ = va
        day_lo, day_hi = pv["low"].min(), pv["high"].max()
        # 08:30 NY of trading day d: d is the date after +7h shift, so NY calendar date = d for hours < 17
        open_ts = pd.Timestamp(d.year, d.month, d.day, 8, 30).tz_localize(NY).tz_convert("UTC")
        t0 = open_ts.value
        i0 = np.searchsorted(ts, t0)
        if i0 >= len(ts) or ts[i0] - t0 > 5 * 60 * 10**9:
            continue
        t1, t2 = t0 + 30 * 60 * 10**9, t0 + 60 * 60 * 10**9
        i1, i2 = np.searchsorted(ts, t1) - 1, np.searchsorted(ts, t2) - 1          # last 1m bar of each 30-min bar
        end = np.searchsorted(ts, pd.Timestamp(d.year, d.month, d.day, 17, 0).tz_localize(NY).tz_convert("UTC").value)
        if i2 + 1 >= end or i2 - i0 < 40:
            continue
        # a set of value areas to apply the same rule: real, then 10 random areas of the same width
        width = vah - val
        areas = [("real", val, vah)] + [("ctl", x, x + width) for x in rng.uniform(day_lo, max(day_lo, day_hi - width), 10)]
        for kind, v_lo, v_hi in areas:
            o = c[i0 - 1] if i0 > 0 else c[i0]
            if v_lo <= o <= v_hi:
                continue
            if not (v_lo <= c[i1] <= v_hi and v_lo <= c[i2] <= v_hi):
                continue
            side = 1 if o < v_lo else -1                       # below the area -> long toward the far edge
            entry = c[i2]
            ext = lo[i0:i2 + 1].min() if side > 0 else h[i0:i2 + 1].max()
            stop = ext - side * 0.5
            target = v_hi if side > 0 else v_lo
            risk = abs(entry - stop)
            if risk <= 0 or (target - entry) * side <= 0:
                continue
            r = np.nan
            for k in range(i2 + 1, end):
                if (side > 0 and lo[k] <= stop) or (side < 0 and h[k] >= stop):
                    r = -1.0
                    break
                if (side > 0 and h[k] >= target) or (side < 0 and lo[k] <= target):
                    r = abs(target - entry) / risk
                    break
            else:
                r = side * (c[end - 1] - entry) / risk
            (res_real if kind == "real" else res_ctl).append((int(d.year), r, risk, d.value // 10**9 // 86400))
    out = {}
    for name, rows in (("real", res_real), ("control", res_ctl)):
        if not rows:
            out[name] = {"n": 0}
            continue
        df = pd.DataFrame(rows, columns=["y", "r", "risk", "day"])
        cost = (0.37 + 0.30) / df["risk"]
        m, t, n = dclust(df["r"].to_numpy(), df["day"].to_numpy())
        mn, tn, _ = dclust((df["r"] - cost).to_numpy(), df["day"].to_numpy())
        out[name] = {"n": n, "gross_R": m, "gross_t": t, "net_prior_R": mn, "net_t": tn,
                     "stop_usd_median": float(df["risk"].median()), "per_year_n": df.groupby("y").size().to_dict()}
    return out


# ---------------------------------------------------------------- #88 cost: ATR15 by year

def atr_by_year(b15: pd.DataFrame) -> dict:
    a = atr(b15)
    df = pd.DataFrame({"y": b15["bar_time"].dt.year, "atr": a, "spread": b15["spread"], "close": b15["close"]})
    g = df.groupby("y").agg(atr15_median=("atr", "median"), spread_median=("spread", "median"),
                            price=("close", "median"))
    g["rt_cost_usd_prior"] = g["spread_median"] + FIXED_USD
    g["stop_0.6atr"] = 0.6 * g["atr15_median"]
    g["stop_1.0atr"] = 1.0 * g["atr15_median"]
    g["cost_R_at_0.6atr"] = g["rt_cost_usd_prior"] / g["stop_0.6atr"]
    g["cost_R_at_1.0atr"] = g["rt_cost_usd_prior"] / g["stop_1.0atr"]
    return {int(k): {kk: round(float(vv), 3) for kk, vv in row.items()} for k, row in g.iterrows()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    m1 = load(Path(a.raw))
    b15 = bars(m1, "15min")
    b60 = bars(m1, "60min")
    res: dict = {"bars_15m": len(b15), "bars_1h": len(b60), "span": [str(m1.index[0]), str(m1.index[-1])]}
    h, lo, c = (b15[x].to_numpy() for x in ("high", "low", "close"))
    a15 = atr(b15)
    # hindsight vs causal on gold, 15m
    k, fwd = 5, 10
    sh = np.flatnonzero(swing_highs(h, k))
    sh = sh[(sh + k + fwd < len(c)) & np.isfinite(a15[sh])]
    res["T1_gold_15m"] = {"peek_gain_atr": float(np.mean((c[sh] - c[sh + fwd]) / a15[sh])),
                          "causal_gain_atr": float(np.mean((c[sh + k] - c[sh + k + fwd]) / a15[sh])), "n": len(sh)}
    allr, resp = [], []
    for t, side, zlo, zhi in fvgs(b15).itertuples(index=False):
        if t < 20 or not np.isfinite(a15[t - 1]) or zhi - zlo < 0.25 * a15[t - 1]:
            continue
        w = zhi - zlo
        entry, stop, tgt = (zhi, zlo, zhi + 2 * w) if side > 0 else (zlo, zhi, zlo - 2 * w)
        r = bracket(h, lo, t, side, entry, stop, tgt)
        if not np.isfinite(r):
            continue
        allr.append(r)
        cl = c[t + 1:t + 51]
        if not ((cl < zlo).any() if side > 0 else (cl > zhi).any()):
            resp.append(r)
    res["T2_gold_15m_fvg"] = {"causal_all_meanR": float(np.mean(allr)), "n_all": len(allr),
                              "respected_meanR": float(np.mean(resp)), "n_respected": len(resp)}
    res["atr15_by_year"] = atr_by_year(b15)
    ev = ob_events(b15)
    res["ob_events_all_hours"] = int(len(ev))
    res["E1"] = run_ob(b15, ev)
    res["E2_15m"] = fvg_diag(b15)
    res["E2_1h"] = fvg_diag(b60)
    res["E4_value_area"] = va_rule(m1)
    Path(a.out).write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
