"""Volatility-band diagnostics for docs/research/time-structure/volatility-bands.md (slice F, 2026-10-26).

Read-only, descriptive, NO trial and no strategy backtest. Data: Dukascopy 1m bars (release data-v1), mid prices.

Discipline (written before the first run):
* Outcomes are read on the DISCOVERY half 2010-01-01 .. 2017-12-31 only. 2018-01-01 onward (the pre-registered
  confirmation half) and the holdout 2025-10-01 .. 2026-09-30 are never loaded.
* A bar is usable only after it closes: `available_utc` = last constituent minute + 1 min. A signal on bar i enters at
  the OPEN of bar i+1; the forward return runs to the close of bar i+h. Higher-timeframe context joins through
  `goldbot.data.store.asof_join` on `available_utc`.
* Outcome unit = ATR(14) of the signal bar (so eras with different gold volatility are comparable). Costs are converted
  at the CURRENT median ATR (docs/research/atr-measured-2026-10-18.md) because the cost is in dollars.
* Every event set is compared with randomised entries of the same count, the same long/short mix and the same
  eligible bars (same slice), 300 draws. The statistic carried into the multiple-testing count is the z of the event
  mean against that control. Cells are counted; Benjamini-Hochberg at q = 0.10 is applied over ALL cells.

Usage: uv run python -I docs/research/candidates/time_structure/volatility_bands_diag.py --raw ~/tmp/ts-data/raw --out ~/tmp/vb-out
"""
from __future__ import annotations

import argparse
import json
from math import erf, sqrt
from pathlib import Path

import numpy as np
import pandas as pd

from goldbot.data.store import asof_join
from goldbot.data.timeutil import epoch_ns
from goldbot.features.survey import rolling_hurst
from goldbot.features.technical import adx, atr

DISC_START = pd.Timestamp("2010-01-01", tz="UTC")
DISC_END = pd.Timestamp("2018-01-01", tz="UTC")
NY, LDN = "America/New_York", "Europe/London"
TFS = {"15m": ("15min", 4), "1h": ("1h", 4), "4h": ("4h", 4), "1d": (None, 4)}   # (pandas rule, horizon in bars)
CUR_ATR = {"15m": 8.0, "1h": 17.7, "4h": 36.6, "1d": 87.6}                       # $/oz, last-90-day medians
COST_C, COST_K = 0.144, 0.23                                                     # $/oz round trip, central / conservative
R_DRAWS = 300
CELLS: list[dict] = []
DAYKEY: dict[str, np.ndarray] = {}                                                            # every (event set, slice) cell


# ------------------------------------------------------------------ data
def load(raw: Path) -> pd.DataFrame:
    parts = []
    for y in range(2010, 2018):
        parts.append(pd.read_parquet(raw / f"xauusd_1m_dukascopy_{y}.parquet",
                                     columns=["ts_utc", "bid_open", "bid_high", "bid_low", "bid_close", "ask_open",
                                              "ask_high", "ask_low", "ask_close", "tick_count"]))
    d = pd.concat(parts, ignore_index=True)
    d["ts_utc"] = pd.to_datetime(d["ts_utc"], utc=True).astype("datetime64[ns, UTC]")
    d = d[(d["ts_utc"] >= DISC_START) & (d["ts_utc"] < DISC_END)].sort_values("ts_utc").drop_duplicates("ts_utc")
    for k in ("open", "high", "low", "close"):
        d[k] = (d[f"bid_{k}"] + d[f"ask_{k}"]) / 2
    d["tick_count"] = d["tick_count"].fillna(0.0)
    return d[["ts_utc", "open", "high", "low", "close", "tick_count"]].set_index("ts_utc")


def trading_day(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Trading day = New York date after +7 h (a day ends at 17:00 New York, the 22:00/21:00 UTC rollover)."""
    return (idx.tz_convert(NY) + pd.Timedelta(hours=7)).normalize().tz_localize(None)


def bars(m1: pd.DataFrame, tf: str) -> pd.DataFrame:
    rule, _ = TFS[tf]
    m = m1.copy()
    m["t_end"] = m.index + pd.Timedelta(minutes=1)
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "tick_count": "sum", "t_end": "last"}
    if rule is None:
        key = trading_day(m.index)
        m["first"] = m.index
        b = m.groupby(key).agg({**agg, "first": "first"})
        b.index = pd.DatetimeIndex(b.pop("first"))
    else:
        b = m.resample(rule, label="left", closed="left").agg(agg)
    b = b.dropna(subset=["close"])
    b = b.rename(columns={"t_end": "available_utc"})
    b["available_utc"] = pd.to_datetime(b["available_utc"], utc=True)
    b.index.name = "ts_utc"
    return b


# ------------------------------------------------------------------ bands: each returns (mid, upper, lower)
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def b_keltner(b: pd.DataFrame, n: int = 20, k: float = 2.0, a: pd.Series | None = None):
    a = atr(b, 14) if a is None else a
    m = ema(b["close"], n)
    return m, m + k * a, m - k * a


def b_bollinger(b: pd.DataFrame, n: int = 20, k: float = 2.0):
    m = b["close"].rolling(n, min_periods=n).mean()
    s = b["close"].rolling(n, min_periods=n).std(ddof=0)
    return m, m + k * s, m - k * s


def b_donchian(b: pd.DataFrame, n: int):
    hi, lo = b["high"].rolling(n).max().shift(1), b["low"].rolling(n).min().shift(1)
    return (hi + lo) / 2, hi, lo


def b_starc(b: pd.DataFrame):
    m = b["close"].rolling(6, min_periods=6).mean()
    a = atr(b, 15)
    return m, m + 2 * a, m - 2 * a


def b_linreg(b: pd.DataFrame, n: int = 50, k: float = 2.0):
    y = b["close"].to_numpy(float)
    x = np.arange(n) - (n - 1) / 2
    sxx = float((x ** 2).sum())
    cs = np.concatenate(([0.0], np.cumsum(y)))
    cs2 = np.concatenate(([0.0], np.cumsum(y ** 2)))
    sy = cs[n:] - cs[:-n]
    syy = cs2[n:] - cs2[:-n]
    sxy = np.convolve(y, x[::-1], mode="valid")                 # sum (x - xbar) * y over each window
    slope = sxy / sxx
    ybar = sy / n
    fitted = ybar + slope * ((n - 1) / 2)                          # value at the last x
    resid = np.maximum(syy - n * ybar ** 2 - slope ** 2 * sxx, 0.0)
    se = np.sqrt(resid / (n - 2))
    f = np.full(len(y), np.nan)
    s_ = np.full(len(y), np.nan)
    f[n - 1:], s_[n - 1:] = fitted, se
    f, s_ = pd.Series(f, index=b.index), pd.Series(s_, index=b.index)
    return f, f + k * s_, f - k * s_


def b_vwap(b: pd.DataFrame, k: float = 2.0, min_bars: int = 6):
    day = pd.Series(trading_day(b.index), index=b.index)
    v = b["tick_count"].replace(0, np.nan).fillna(1.0)
    tp = (b["high"] + b["low"] + b["close"]) / 3
    g = day.to_numpy()
    sv = v.groupby(g).cumsum()
    spv = (tp * v).groupby(g).cumsum()
    spv2 = (tp ** 2 * v).groupby(g).cumsum()
    vw = spv / sv
    sd = np.sqrt(np.maximum(spv2 / sv - vw ** 2, 0.0))
    n_in = pd.Series(1, index=b.index).groupby(g).cumsum()
    ok = n_in >= min_bars
    return vw.where(ok), (vw + k * sd).where(ok), (vw - k * sd).where(ok)


# ------------------------------------------------------------------ events, outcomes, controls
def events(b: pd.DataFrame, mid, up, lo) -> dict[str, np.ndarray]:
    """Direction array per bar: +1 long, -1 short, 0 none. 'brk' = first close outside the band (trade WITH it);
    'fade' = wick beyond the band that closes back inside (trade toward the middle)."""
    c, h, lo_b = b["close"], b["high"], b["low"]
    c1, up1, lo1 = c.shift(), up.shift(), lo.shift()
    brk_up = (c > up) & (c1 <= up1)
    brk_dn = (c < lo) & (c1 >= lo1)
    fade_dn = (h > up) & (c <= up)        # pierced the top, closed back inside -> short
    fade_up = (lo_b < lo) & (c >= lo)        # pierced the bottom, closed back inside -> long
    valid = up.notna() & lo.notna()
    brk = np.where(brk_up & valid, 1, np.where(brk_dn & valid, -1, 0))
    fad = np.where(fade_up & valid, 1, np.where(fade_dn & valid, -1, 0))
    return {"brk": brk, "fade": fad}


def forward(b: pd.DataFrame, a: pd.Series, h: int) -> np.ndarray:
    """Long-side return in ATR units: close[i+h] - open[i+1], over ATR[i]."""
    o1 = b["open"].shift(-1)
    ch = b["close"].shift(-h)
    return ((ch - o1) / a).to_numpy()


def dedup(idx: np.ndarray, gap: int) -> np.ndarray:
    keep, last = [], -10 ** 9
    for i in idx:
        if i - last >= gap:
            keep.append(i)
            last = i
    return np.array(keep, dtype=int)


def pnorm_two(z: float) -> float:
    return 1 - erf(abs(z) / sqrt(2)) if np.isfinite(z) else float("nan")


def cell(tag: dict, direction: np.ndarray, f: np.ndarray, years: np.ndarray, gap: int, eligible: np.ndarray,
         rng: np.random.Generator, months: float) -> dict | None:
    idx = np.flatnonzero((direction != 0) & eligible & np.isfinite(f))
    n_raw = len(idx)
    idx = dedup(idx, gap)
    n = len(idx)
    if n < 30:
        return None
    r = f[idx] * direction[idx]
    mu, sd = float(r.mean()), float(r.std(ddof=1))
    t = mu / (sd / np.sqrt(n))
    el = np.flatnonzero(eligible & np.isfinite(f))
    dirs = direction[idx]
    draw_i = rng.choice(el, (R_DRAWS, n))
    draw_d = rng.choice(dirs, (R_DRAWS, n))
    cm = (f[draw_i] * draw_d).mean(axis=1)
    z = (mu - cm.mean()) / cm.std(ddof=1)
    ys = years[idx]
    dk = DAYKEY[tag["tf"]][idx]
    dfc = pd.DataFrame({"r": r - mu, "d": dk}).groupby("d")["r"].sum()
    G = len(dfc)
    se_cl = np.sqrt(G / max(G - 1, 1) * (dfc ** 2).sum()) / n
    t_cl = mu / se_cl if se_cl > 0 else float("nan")
    ypos = sum(1 for y in np.unique(ys) if r[ys == y].mean() > 0 and (ys == y).sum() >= 3)
    ytot = sum(1 for y in np.unique(ys) if (ys == y).sum() >= 3)
    out = {**tag, "n": n, "n_raw": n_raw, "per_month": n_raw / months, "mean_atr": mu, "t": t, "t_day": float(t_cl), "ctrl_mean": float(cm.mean()),
           "ctrl_sd": float(cm.std(ddof=1)), "z": float(z), "p": pnorm_two(z), "yrs_pos": f"{ypos}/{ytot}",
           "hit": float((r > 0).mean())}
    CELLS.append(out)
    return out


def session_bucket(avail: pd.DatetimeIndex) -> np.ndarray:
    h = avail.hour.to_numpy()
    return np.select([(h >= 22) | (h < 7), h < 12, h < 17], ["Asia", "London", "NY"], "Late")


def open_windows(avail: pd.DatetimeIndex) -> np.ndarray:
    """First 90 min after the London (08:00 Europe/London) and New York (08:30 America/New_York) opens, DST-aware."""
    out = np.full(len(avail), "other", dtype=object)
    for name, tz, hh, mm in (("LDN_open", LDN, 8, 0), ("NY_open", NY, 8, 30)):
        loc = avail.tz_convert(tz)
        mins = (loc.hour * 60 + loc.minute - (hh * 60 + mm)).to_numpy()
        out[(mins > 0) & (mins <= 90) & (avail.dayofweek.to_numpy() < 5)] = name
    return out


def terciles(x: pd.Series) -> np.ndarray:
    r = x.rolling(250, min_periods=100).rank(pct=True)
    return np.select([r < 1 / 3, r < 2 / 3, r >= 2 / 3], ["low", "mid", "high"], "na")


# ------------------------------------------------------------------ main analysis
def md(rows: list[dict], cols: list[str], fmt: dict | None = None) -> str:
    fmt = fmt or {}
    head = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
    body = ""
    for r in rows:
        vals = []
        for c in cols:
            v = r.get(c, "")
            vals.append(fmt[c](v) if c in fmt and v != "" else (f"{v:.3f}" if isinstance(v, float) else str(v)))
        body += "| " + " | ".join(vals) + " |\n"
    return head + body


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    m1 = load(Path(args.raw).expanduser())
    months = (DISC_END - DISC_START).days / 30.44
    rng = np.random.default_rng(20261026)
    B = {tf: bars(m1, tf) for tf in TFS}
    print(f"1m rows {len(m1)}  bars " + "  ".join(f"{k}:{len(v)}" for k, v in B.items()))

    feats: dict[str, pd.DataFrame] = {}            # per TF: signal-time context, for MTF joins and slices
    tables: dict[str, str] = {}

    # ---------- 1. band event sets x TF: breakout vs fade, vs random control
    main_rows = []
    sets_for_slices: dict[tuple[str, str], tuple] = {}
    for tf, b in B.items():
        h = TFS[tf][1]
        a = atr(b, 14)
        f = forward(b, a, h)
        years = b.index.year.to_numpy()
        DAYKEY[tf] = np.asarray(trading_day(b.index).asi8 if False else epoch_ns(pd.DatetimeIndex(trading_day(b.index)).tz_localize('UTC')))
        av = pd.DatetimeIndex(b["available_utc"])
        elig_all = np.isfinite(a.to_numpy())
        bands = {
            "KC20x2.0": b_keltner(b, 20, 2.0, a),
            "BB20x2": b_bollinger(b, 20, 2.0),
            "DC20": b_donchian(b, 20),
            "DC55": b_donchian(b, 55),
            "STARC6x2": b_starc(b),
            "LR50x2": b_linreg(b, 50, 2.0),
        }
        if tf in ("15m", "1h"):
            bands["VWAPx2"] = b_vwap(b, 2.0, 6 if tf == "15m" else 3)
        adx14 = adx(b, 14)
        ret = np.log(b["close"]).diff().to_numpy()
        hurst = pd.Series(rolling_hurst(np.nan_to_num(ret)), index=b.index)
        feats[tf] = pd.DataFrame({"ts_utc": av, "available_utc": av, "atr": a.to_numpy(), "adx": adx14.to_numpy(),
                                  "hurst": hurst.to_numpy(), "close": b["close"].to_numpy()}, index=b.index)
        ctx = {"adx": np.select([adx14 < 18, adx14 < 25, adx14 >= 25], ["<18", "18-25", ">25"], "na"),
               "vol": terciles(a / b["close"]), "hurst": terciles(hurst)}
        if tf in ("15m", "1h"):
            ctx["session"] = session_bucket(av)
            ctx["open"] = open_windows(av)
        for name, (mid, up, lo) in bands.items():
            ev = events(b, mid, up, lo)
            feats[tf][f"{name}_pos"] = ((b["close"] - mid) / ((up - lo) / 2)).to_numpy()   # +-1 = at the band
            for typ, d in ev.items():
                res = cell({"tf": tf, "band": name, "type": typ, "slice": "all"}, d, f, years, h, elig_all, rng, months)
                if res:
                    res["net_c_atr"] = res["mean_atr"] - COST_C / CUR_ATR[tf]
                    res["net_k_atr"] = res["mean_atr"] - COST_K / CUR_ATR[tf]
                    main_rows.append(res)
                sets_for_slices[(tf, f"{name}:{typ}")] = (d, f, years, h, ctx, av)
    tables["main"] = md(main_rows, ["tf", "band", "type", "n_raw", "per_month", "n", "mean_atr", "t", "t_day", "ctrl_mean", "z",
                                   "yrs_pos", "net_c_atr", "net_k_atr"])

    # ---------- 2. regime and session slices for the three canonical sets
    slice_rows = []
    for tf in ("15m", "1h", "4h", "1d"):
        for key in ("KC20x2.0:brk", "BB20x2:fade", "DC20:brk", "BB20x2:brk", "KC20x2.0:fade"):
            d, f, years, h, ctx, av = sets_for_slices[(tf, key)]
            for dim, lab in ctx.items():
                for lv in np.unique(lab):
                    if lv in ("na", "other"):
                        continue
                    res = cell({"tf": tf, "band": key.split(":")[0], "type": key.split(":")[1], "slice": f"{dim}={lv}"},
                               d, f, years, h, lab == lv, rng, months)
                    if res:
                        slice_rows.append(res)
    tables["slices"] = md(slice_rows, ["tf", "band", "type", "slice", "n", "per_month", "mean_atr", "t", "ctrl_mean", "z", "yrs_pos"])

    # ---------- 3. squeeze: BB(20,2) inside KC(20, 1.5 ATR), release after >= 6 bars on
    sq_rows = []
    for tf, b in B.items():
        h = TFS[tf][1]
        a = atr(b, 14)
        f = forward(b, a, h)
        years = b.index.year.to_numpy()
        _, bu, bl = b_bollinger(b, 20, 2.0)
        _, ku, kl = b_keltner(b, 20, 1.5, a)
        on = ((bu < ku) & (bl > kl)).fillna(False)
        run = on.groupby((~on).cumsum()).cumsum()
        release = (~on) & on.shift(fill_value=False) & (run.shift(fill_value=0) >= 6)
        sma20 = b["close"].rolling(20).mean()
        dcm = (b["high"].rolling(20).max() + b["low"].rolling(20).min()) / 2
        mom = b["close"] - (dcm + sma20) / 2
        d_ = np.where(release, np.sign(mom), 0).astype(int)
        res = cell({"tf": tf, "band": "squeeze-release>=6", "type": "dir=momentum", "slice": "all"}, d_, f, years, h,
                   np.isfinite(a.to_numpy()), rng, months)
        # range expansion: next-h-bar high-low range over ATR, release vs all bars
        rng_next = (b["high"][::-1].rolling(h).max()[::-1].shift(-1) - b["low"][::-1].rolling(h).min()[::-1].shift(-1)) / a
        rel_idx = dedup(np.flatnonzero(release.to_numpy() & np.isfinite(rng_next.to_numpy())), h)
        base = rng_next.to_numpy()[np.isfinite(rng_next.to_numpy())]
        ctrl = np.array([rng.choice(base, len(rel_idx)).mean() for _ in range(R_DRAWS)])
        ev_mean = float(rng_next.to_numpy()[rel_idx].mean()) if len(rel_idx) else float("nan")
        # bandwidth percentile as a predictor of forward realised range (Spearman, trailing 250-bar percentile)
        bwp = ((bu - bl) / b["close"]).rolling(250, min_periods=100).rank(pct=True)
        fr = rng_next
        ok = bwp.notna() & fr.notna()
        sp = float(pd.Series(bwp[ok]).corr(pd.Series(fr[ok]), method="spearman"))
        # same on the ATR-normalised range is mechanical (ATR is in the denominator); report vs raw $ range too
        fr_d = (rng_next * a)
        sp_d = float(pd.Series(bwp[ok]).corr(pd.Series(fr_d[ok]), method="spearman"))
        atrp = (a / b["close"]).rolling(250, min_periods=100).rank(pct=True)
        ok2 = ok & atrp.notna()
        X = pd.DataFrame({"bw": bwp[ok2], "atr": atrp[ok2], "y": fr_d[ok2]}).rank()
        cxy, cxz, czy = X["bw"].corr(X["y"]), X["bw"].corr(X["atr"]), X["atr"].corr(X["y"])
        rho_atr = float(czy)
        partial = float((cxy - cxz * czy) / np.sqrt((1 - cxz ** 2) * (1 - czy ** 2)))
        sq_rows.append({"tf": tf, "squeeze_on_share": float(on.mean()), "releases": int(len(rel_idx)),
                        "releases_per_month": float(release.sum() / months),
                        "next_range_atr_release": ev_mean, "next_range_atr_ctrl": float(ctrl.mean()),
                        "ctrl_sd": float(ctrl.std(ddof=1)),
                        "dir_mean_atr": res["mean_atr"] if res else float("nan"), "dir_t": res["t"] if res else float("nan"),
                        "dir_z": res["z"] if res else float("nan"), "rho_bwpct_fwd_range_atr": sp, "rho_bwpct_fwd_range_usd": sp_d, "rho_atrpct_fwd_range_usd": rho_atr, "partial_bw_given_atr": partial})
    tables["squeeze"] = md(sq_rows, list(sq_rows[0].keys()))

    # ---------- 4. multi-timeframe: 4h and 1d KC position as a filter on 15m / 1h events (asof_join on available_utc)
    mtf_rows = []
    htf = {}
    MTF: dict[str, pd.DataFrame] = {}
    for tf in ("4h", "1d"):
        b = B[tf]
        a = atr(b, 14)
        m, u, lw = b_keltner(b, 20, 2.0, a)
        htf[tf] = pd.DataFrame({"available_utc": pd.DatetimeIndex(b["available_utc"]), f"{tf}_kc_pos": ((b["close"] - m) / (u - m)).to_numpy(),
                                f"{tf}_dc_pos": ((b["close"] - b["low"].rolling(20).min()) / (b["high"].rolling(20).max() - b["low"].rolling(20).min())).to_numpy()})
    for tf in ("15m", "1h"):
        fe = feats[tf].copy()
        j = fe[["ts_utc"]].reset_index(drop=True)
        for hn in ("4h", "1d"):
            jj = asof_join(j, htf[hn], on="ts_utc", available_col="available_utc")
            fe[f"{hn}_kc_pos"] = jj[f"{hn}_kc_pos"].to_numpy()
            fe[f"{hn}_dc_pos"] = jj[f"{hn}_dc_pos"].to_numpy()
        MTF[tf] = fe
        for key in ("KC20x2.0:brk", "DC20:brk", "BB20x2:fade", "BB20x2:brk"):
            d, f, years, h, ctx, av = sets_for_slices[(tf, key)]
            for hn in ("4h", "1d"):
                pos = fe[f"{hn}_kc_pos"].to_numpy()
                side = np.sign(pos)
                # for a breakout the "with-trend" case is: the event direction equals the higher-timeframe side
                # for a fade the event direction is toward the middle, so 'with' is again direction == HTF side
                for lab, cond in (("HTF agrees", side == d), ("HTF opposes", (side == -d) & (d != 0)),
                                  ("HTF at its band edge against", (np.abs(pos) >= 1) & (side == -d) & (d != 0))):
                    res = cell({"tf": tf, "band": key.split(":")[0], "type": key.split(":")[1], "slice": f"{hn} KC: {lab}"},
                               d, f, years, h, cond & np.isfinite(pos), rng, months)
                    if res:
                        mtf_rows.append(res)
    tables["mtf"] = md(mtf_rows, ["tf", "band", "type", "slice", "n", "per_month", "mean_atr", "t", "ctrl_mean", "z", "yrs_pos"])

    # ---------- 5. redundancy of band-position features (for the 40-feature cap)
    red = {}
    for tf in ("15m", "1h", "4h"):
        cols = [c for c in feats[tf].columns if c.endswith("_pos") and not c.startswith("4h") and not c.startswith("1d")]
        red[tf] = feats[tf][cols].corr(method="spearman")
    for k_ in ("1h", "4h"):
        r_ = red[k_].round(2)
        tables[f"redundancy_{k_}"] = md([{"": i, **{c: float(r_.loc[i, c]) for c in r_.columns}} for i in r_.index], [""] + list(r_.columns))

    # ---------- 6. exits diagnostic: chandelier 3 ATR (frozen entry ATR) vs fixed 4-bar exit, DC20 breakouts vs random same-direction entries
    ex_rows = []
    for tf in ("1h", "4h"):
        b = B[tf]
        a = atr(b, 14).to_numpy()
        hi, lo_, cl, op = (b[k].to_numpy(float) for k in ("high", "low", "close", "open"))
        d, *_ = sets_for_slices[(tf, "DC20:brk")][:1]
        idx = dedup(np.flatnonzero((d != 0) & np.isfinite(a)), 24)
        idx = idx[idx < len(b) - 30]
        el = np.flatnonzero(np.isfinite(a))
        el = el[el < len(b) - 30]

        def run(entries, dirs):
            fixed, chand, mfe, mae = [], [], [], []
            for i, s in zip(entries, dirs):
                e = op[i + 1]
                A = a[i]
                best = e
                res = None
                for k in range(i + 1, i + 25):
                    best = max(best, hi[k]) if s > 0 else min(best, lo_[k])
                    stop = best - 3 * A if s > 0 else best + 3 * A
                    if (s > 0 and cl[k] < stop) or (s < 0 and cl[k] > stop):
                        res = (cl[k] - e) * s / A
                        break
                if res is None:
                    res = (cl[i + 24] - e) * s / A
                chand.append(res)
                fixed.append((cl[i + 4] - e) * s / A)
                seg_h, seg_l = hi[i + 1:i + 25].max(), lo_[i + 1:i + 25].min()
                mfe.append(((seg_h - e) if s > 0 else (e - seg_l)) / A)
                mae.append(((e - seg_l) if s > 0 else (seg_h - e)) / A)
            return np.mean(fixed), np.mean(chand), np.mean(mfe), np.mean(mae)

        ev = run(idx, d[idx])
        ctrls = np.array([run(rng.choice(el, len(idx)), rng.choice(d[idx], len(idx))) for _ in range(40)])
        ex_rows.append({"tf": tf, "n": len(idx), "fixed4_event": ev[0], "chand3_event": ev[1], "mfe24_event": ev[2], "mae24_event": ev[3],
                        "fixed4_ctrl": ctrls[:, 0].mean(), "chand3_ctrl": ctrls[:, 1].mean(), "mfe24_ctrl": ctrls[:, 2].mean(),
                        "mae24_ctrl": ctrls[:, 3].mean(), "chand3_ctrl_sd": ctrls[:, 1].std(ddof=1)})
    tables["exits"] = md(ex_rows, list(ex_rows[0].keys()))

    # ---------- 7. two composite rules seen only AFTER sections 1-4 (post hoc, so charged as extra looks and flagged)
    ext_rows = []
    for tf in ("15m", "1h"):
        d_all = [sets_for_slices[(tf, k)][0] for k in ("KC20x2.0:brk", "BB20x2:brk", "DC20:brk")]
        _, f, years, h, ctx, av = sets_for_slices[(tf, "BB20x2:brk")]
        du = np.sign(sum(d_all)).astype(int)
        fe = MTF[tf]
        # X1: break WITH the move when the 128-bar R/S Hurst is in its low tercile, AGAINST it when high
        hl = ctx["hurst"]
        d1 = np.where(hl == "low", du, np.where(hl == "high", -du, 0))
        r1 = cell({"tf": tf, "band": "union(KC,BB,DC20)", "type": "X1 hurst-switch", "slice": "all"}, d1, f, years, h,
                  np.ones(len(f), bool), rng, months)
        # X2: a band breakout against a stretched higher timeframe (4h KC |pos| >= 1 on the other side) is faded,
        # i.e. trade WITH the 4h stretch; control = random bars in the same 4h-stretched state
        for hn in ("4h", "1d"):
            pos = fe[f"{hn}_kc_pos"].to_numpy()
            side = np.sign(pos)
            cond = (np.abs(pos) >= 1) & (side == -du) & (du != 0)
            d2 = np.where(cond, -du, 0)
            r2 = cell({"tf": tf, "band": "union(KC,BB,DC20)", "type": f"X2 resume {hn} stretch", "slice": "all"}, d2, f, years, h,
                      np.isfinite(pos) & (np.abs(pos) >= 1), rng, months)
            if r2:
                ext_rows.append(r2)
                i2 = np.flatnonzero(d2 != 0)
                ext_rows.append({"tf": tf, "band": "  overlap", "type": f"share of X2 {hn} events in hurst=high",
                                 "slice": f"{float((hl[i2] == 'high').mean()):.2f}", "n": len(i2)})
        if r1:
            ext_rows.append(r1)
    tables["extras"] = md(ext_rows, ["tf", "band", "type", "slice", "n", "per_month", "mean_atr", "t", "t_day", "ctrl_mean", "z", "yrs_pos", "hit"])

    # ---------- 8. null check of X1: bars permuted (shape kept relative to the previous close, order shuffled) -> no
    # serial dependence at all; X1 must read ~0 here or it is an artefact of the Hurst/tercile construction
    b0 = B["15m"]
    pc = b0["close"].shift().fillna(b0["close"].iloc[0]).to_numpy()
    rel = np.stack([(b0[k].to_numpy() - pc) for k in ("open", "high", "low", "close")], axis=1)
    null_rows = []
    for rep in range(3):
        perm = rng.permutation(len(b0))
        rr = rel[perm]
        cl = b0["close"].iloc[0] + np.cumsum(rr[:, 3])
        prev = np.concatenate(([b0["close"].iloc[0]], cl[:-1]))
        bp = pd.DataFrame({"open": prev + rr[:, 0], "high": prev + rr[:, 1], "low": prev + rr[:, 2], "close": cl,
                           "tick_count": 1.0}, index=b0.index)
        a_ = atr(bp, 14)
        f_ = forward(bp, a_, 4)
        dd = []
        for nm, bands_ in (("KC", b_keltner(bp, 20, 2.0, a_)), ("BB", b_bollinger(bp, 20, 2.0)), ("DC", b_donchian(bp, 20))):
            dd.append(events(bp, *bands_)["brk"])
        du_ = np.sign(sum(dd)).astype(int)
        hr = pd.Series(rolling_hurst(np.nan_to_num(np.diff(np.log(bp["close"].clip(lower=1)), prepend=0.0))), index=bp.index)
        hl_ = terciles(hr)
        d1_ = np.where(hl_ == "low", du_, np.where(hl_ == "high", -du_, 0))
        r_ = cell({"tf": "15m", "band": f"NULL permuted bars #{rep}", "type": "X1 hurst-switch", "slice": "all"}, d1_, f_,
                  bp.index.year.to_numpy(), 4, np.isfinite(a_.to_numpy()), rng, months)
        if r_:
            null_rows.append(r_)
            CELLS.pop()      # null cells do not count as looks at the real data
    tables["null"] = md(null_rows, ["tf", "band", "type", "n", "mean_atr", "t", "z", "yrs_pos"])

    # ---------- multiple-testing summary
    ps = np.array([c["p"] for c in CELLS])
    order = np.argsort(ps)
    m = len(ps)
    thr = 0.10 * (np.arange(1, m + 1) / m)
    passed = ps[order] <= thr
    k = int(np.max(np.flatnonzero(passed)) + 1) if passed.any() else 0
    surv = [CELLS[i] for i in order[:k]]
    mt = {"cells": m, "abs_z_gt2": int((np.abs([c["z"] for c in CELLS]) > 2).sum()),
          "expected_by_chance_at_2": round(0.0455 * m, 1), "bh_q10_survivors": k,
          "survivors": [{kk: (round(v, 4) if isinstance(v, float) else v) for kk, v in s.items()} for s in surv]}
    tables["mt"] = json.dumps(mt, indent=1)
    (out / "cells.json").write_text(json.dumps(CELLS, indent=1, default=float))
    for name, t in tables.items():
        (out / f"{name}.md").write_text(t)
        print(f"\n===== {name} =====\n{t}")


if __name__ == "__main__":
    main()
