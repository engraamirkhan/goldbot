"""Time-structure measurements for docs/research/time-structure-2026-10-26.md (research director, 2026-10-26).

Read-only, descriptive, no trial. Data: data-v1 Dukascopy 1m bars (`gh release download data-v1`), mid prices.

Discipline (written before this script was run):
* OUTCOME tables (returns, continuation/reversal, level reactions, oscillator ICs, cycles) use the DISCOVERY half
  2010-01-01 .. 2017-12-31 only. The CONFIRMATION half 2018-01-01 .. 2025-09-30 is never read for an outcome here:
  it is reserved for the pre-registered tests in prereg-2026-10-26-time-structure.md.
* ACTIVITY/COST tables (spread, ATR, absolute move by clock time) use both halves; they are cost inputs, not the
  outcome any hypothesis below is read on (except H-TS1, whose target is range: its activity table here is
  discovery-half only).
* The holdout 2025-10-01 .. 2026-09-30 is never loaded (rows from 2025-10-01 on are dropped at load).
* A price at clock time T is the close of the last 1m bar whose `visible_at` <= T (point in time).

Usage: uv run python docs/research/candidates/time_structure_2026-10-26.py --raw ~/tmp/ts-data/raw --cal \
       ~/tmp/ts-data/cal/us_calendar.parquet --out ~/tmp/ts-data/out
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from goldbot.data.timeutil import epoch_ns

DISC_END = pd.Timestamp("2018-01-01", tz="UTC")
HOLDOUT_START = pd.Timestamp("2025-10-01", tz="UTC")
NY, LDN, TKO = "America/New_York", "Europe/London", "Asia/Tokyo"


def load(raw: Path) -> pd.DataFrame:
    parts = []
    for y in range(2010, 2026):
        d = pd.read_parquet(raw / f"xauusd_1m_dukascopy_{y}.parquet",
                            columns=["ts_utc", "visible_at", "bid_open", "bid_high", "bid_low", "bid_close",
                                     "ask_open", "ask_high", "ask_low", "ask_close", "tick_count", "spread_mean"])
        parts.append(d)
    d = pd.concat(parts, ignore_index=True)
    d["ts_utc"] = pd.to_datetime(d["ts_utc"], utc=True).astype("datetime64[ns, UTC]")
    d = d[d["ts_utc"] < HOLDOUT_START].sort_values("ts_utc").drop_duplicates("ts_utc")
    for k in ("open", "high", "low", "close"):
        d[k] = (d[f"bid_{k}"] + d[f"ask_{k}"]) / 2
    d["spread"] = d["ask_close"] - d["bid_close"]
    return d[["ts_utc", "open", "high", "low", "close", "tick_count", "spread", "spread_mean"]].set_index("ts_utc")


def trading_day(idx: pd.DatetimeIndex) -> pd.Index:
    """Trading day = NY calendar date after shifting by +7 h (a day ends at 17:00 New York)."""
    return (idx.tz_convert(NY) + pd.Timedelta(hours=7)).normalize().tz_localize(None)


def local_anchor(days: pd.DatetimeIndex, tz: str, hh: int, mm: int, day_offset: int = 0) -> pd.DatetimeIndex:
    loc = pd.DatetimeIndex([pd.Timestamp(d + pd.Timedelta(days=day_offset)).replace(hour=hh, minute=mm)
                            for d in days]).tz_localize(tz, ambiguous="NaT", nonexistent="shift_forward")
    return loc.tz_convert("UTC")


def price_at(m1: pd.DataFrame, t: pd.DatetimeIndex) -> np.ndarray:
    """Close of the last 1m bar visible at t (bar open + 1 min <= t)."""
    vis = epoch_ns(m1.index) + 60_000_000_000
    pos = np.searchsorted(vis, epoch_ns(t), side="right") - 1
    out = np.where(pos >= 0, m1["close"].to_numpy()[np.clip(pos, 0, None)], np.nan)
    # stale guard: no bar in the 30 minutes before t -> NaN
    stale = (epoch_ns(t) - vis[np.clip(pos, 0, None)]) > 30 * 60_000_000_000
    out[stale] = np.nan
    return out


def tstat(x: np.ndarray) -> tuple[float, float, int]:
    x = x[np.isfinite(x)]
    if len(x) < 3:
        return float("nan"), float("nan"), len(x)
    return float(x.mean()), float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))), len(x)


def resample(m1: pd.DataFrame, rule: str) -> pd.DataFrame:
    g = m1.resample(rule, label="left", closed="left")
    b = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                      "close": g["close"].last(), "tick_count": g["tick_count"].sum(), "spread": g["spread"].mean()})
    return b.dropna(subset=["close"])


def wilder_atr(b: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = b["close"].shift()
    tr = pd.concat([b["high"] - b["low"], (b["high"] - pc).abs(), (b["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


# ---------------------------------------------------------------- sessions
def anchors(days: pd.DatetimeIndex) -> dict[str, pd.DatetimeIndex]:
    return {
        "ny_close_prev": local_anchor(days, NY, 17, 0, -1),
        "tokyo_open": local_anchor(days, TKO, 8, 0, 0),
        "sge_am": local_anchor(days, "Asia/Shanghai", 10, 15),
        "tokyo_close": local_anchor(days, TKO, 15, 0),
        "london_open": local_anchor(days, LDN, 8, 0),
        "lbma_am": local_anchor(days, LDN, 10, 30),
        "comex_open": local_anchor(days, NY, 8, 20),
        "ny_open": local_anchor(days, NY, 8, 30),
        "lbma_pm": local_anchor(days, LDN, 15, 0),
        "wmr_4pm": local_anchor(days, LDN, 16, 0),
        "london_close": local_anchor(days, LDN, 16, 30),
        "comex_settle": local_anchor(days, NY, 13, 30),
        "ny_close": local_anchor(days, NY, 17, 0),
    }


def session_tables(m1: pd.DataFrame, days: pd.DatetimeIndex) -> dict:
    a = anchors(days)
    # Tokyo 08:00 on calendar day d is 23:00 UTC of d-1: Tokyo open belongs to trading day d.
    px = {k: price_at(m1, v) for k, v in a.items()}
    segs = [("overnight (NY close -> Tokyo 08:00)", "ny_close_prev", "tokyo_open"),
            ("Asia (Tokyo 08:00 -> London 08:00)", "tokyo_open", "london_open"),
            ("London AM (08:00 -> NY 08:30)", "london_open", "ny_open"),
            ("NY pit (08:30 -> COMEX settle 13:30)", "ny_open", "comex_settle"),
            ("NY late (13:30 -> 17:00 NY)", "comex_settle", "ny_close"),
            ("full day (NY close -> NY close)", "ny_close_prev", "ny_close")]
    yrs = days.year
    out = {}
    for name, s, e in segs:
        r = 1e4 * np.log(px[e] / px[s])
        mu, t, n = tstat(r)
        pos_years = sum(1 for y in np.unique(yrs) if np.nanmean(r[yrs == y]) > 0)
        out[name] = {"mean_bp": mu, "t": t, "n": n, "sd_bp": float(np.nanstd(r)),
                     "var_share": float(np.nanvar(r)), "pos_years": pos_years, "years": int(len(np.unique(yrs)))}
    tot = out["full day (NY close -> NY close)"]["var_share"]
    for k in out:
        out[k]["var_share"] = out[k]["var_share"] / tot
    return out


def anchor_windows(m1: pd.DataFrame, days: pd.DatetimeIndex, atr15_day: pd.Series) -> dict:
    """For each clock anchor: |move| in the 15 min after vs the 15 min before (activity jump), and the correlation of
    the 30 min before with the 30 min after (sign: continuation > 0, reversal < 0), in ATR15 units."""
    a = anchors(days)
    atr = atr15_day.reindex(days).to_numpy()
    out = {}
    for k, t in a.items():
        if k == "ny_close_prev":
            continue
        p0 = price_at(m1, t)
        pm30, pm15 = price_at(m1, t - pd.Timedelta(minutes=30)), price_at(m1, t - pd.Timedelta(minutes=15))
        pp15, pp30 = price_at(m1, t + pd.Timedelta(minutes=15)), price_at(m1, t + pd.Timedelta(minutes=30))
        pp60 = price_at(m1, t + pd.Timedelta(minutes=60))
        before, after = (p0 - pm30) / atr, (pp30 - p0) / atr
        act_b, act_a = np.abs(p0 - pm15) / atr, np.abs(pp15 - p0) / atr
        ok = np.isfinite(before) & np.isfinite(after)
        c = float(np.corrcoef(before[ok], after[ok])[0, 1])
        ok2 = np.isfinite(before) & np.isfinite((pp60 - p0) / atr)
        c60 = float(np.corrcoef(before[ok2], ((pp60 - p0) / atr)[ok2])[0, 1])
        out[k] = {"abs15_before_atr": float(np.nanmean(act_b)), "abs15_after_atr": float(np.nanmean(act_a)),
                  "jump_ratio": float(np.nanmean(act_a) / np.nanmean(act_b)),
                  "corr_pre30_post30": c, "corr_pre30_post60": c60, "n": int(ok.sum()),
                  "t_corr": c * np.sqrt(ok.sum() - 2) / np.sqrt(1 - c * c)}
    return out


def opening_range(m15: pd.DataFrame, days: pd.DatetimeIndex, open_key: str, end_key: str, minutes: int) -> dict:
    """First `minutes` after the open = OR. Session = open .. end anchor. Stats on the session's 15m bars."""
    a = anchors(days)
    o, e = a[open_key], a[end_key]
    rows = []
    idx = m15.index
    hi, lo, cl = m15["high"].to_numpy(), m15["low"].to_numpy(), m15["close"].to_numpy()
    for d, t0, t1 in zip(days, o, e):
        if pd.isna(t0) or pd.isna(t1):
            continue
        i0, i1 = idx.searchsorted(t0), idx.searchsorted(t1)
        k = minutes // 15
        if i1 - i0 < k + 4:
            continue
        orh, orl = hi[i0:i0 + k].max(), lo[i0:i0 + k].min()
        sh, sl = hi[i0:i1].max(), lo[i0:i1].min()
        ext_in_or = int(orh >= sh) + int(orl <= sl)
        brk = None
        for j in range(i0 + k, i1):
            if cl[j] > orh:
                brk = (1, j)
                break
            if cl[j] < orl:
                brk = (-1, j)
                break
        rng = max(orh - orl, 1e-9)
        if brk is None:
            rows.append({"ext_in_or": ext_in_or, "broke": 0})
            continue
        s, j = brk
        end_ret = s * (cl[i1 - 1] - cl[j]) / rng
        held = float((s * (cl[i1 - 1] - (orh if s > 0 else orl))) > 0)
        failed = float((s * (cl[i1 - 1] - (orl if s > 0 else orh))) < 0)
        rows.append({"ext_in_or": ext_in_or, "broke": 1, "end_ret_or": end_ret, "held": held, "full_reversal": failed,
                     "side": s})
    r = pd.DataFrame(rows)
    b = r[r.broke == 1]
    mu, t, n = tstat(b["end_ret_or"].to_numpy())
    return {"days": len(r), "p_session_high_or_low_in_OR": float((r.ext_in_or >= 1).mean()),
            "p_both_in_OR": float((r.ext_in_or == 2).mean()), "p_break": float(r.broke.mean()),
            "p_close_beyond_break_side": float(b.held.mean()), "p_full_reversal": float(b.full_reversal.mean()),
            "mean_break_to_end_in_OR_units": mu, "t": t, "n_breaks": n}


def asia_sweep(m15: pd.DataFrame, days: pd.DatetimeIndex) -> dict:
    """Asia range (Tokyo 08:00 -> London 08:00); during London (08:00 London -> 12:00 NY): first take-out of an Asia
    extreme, then move from that bar's close to 17:00 NY in the sweep direction, in Asia-range units."""
    a = anchors(days)
    idx = m15.index
    hi, lo, cl = m15["high"].to_numpy(), m15["low"].to_numpy(), m15["close"].to_numpy()
    rows = []
    lon_end = local_anchor(days, NY, 12, 0)
    for t0, t1, t2, t3 in zip(a["tokyo_open"], a["london_open"], lon_end, a["ny_close"]):
        if pd.isna(t0) or pd.isna(t1):
            continue
        i0, i1, i2, i3 = (idx.searchsorted(x) for x in (t0, t1, t2, t3))
        if i1 - i0 < 8 or i3 - i1 < 20:
            continue
        ah, al = hi[i0:i1].max(), lo[i0:i1].min()
        rng = max(ah - al, 1e-9)
        took_h = (hi[i1:i3] > ah).any()
        took_l = (lo[i1:i3] < al).any()
        first = None
        for j in range(i1, i2):
            if hi[j] > ah and lo[j] < al:
                first = None
                break
            if hi[j] > ah:
                first = (1, j)
                break
            if lo[j] < al:
                first = (-1, j)
                break
        row = {"took_h": bool(took_h), "took_l": bool(took_l)}
        if first:
            s, j = first
            row["cont"] = s * (cl[i3 - 1] - cl[j]) / rng
            row["back_inside_close"] = float((cl[i3 - 1] < ah) and (cl[i3 - 1] > al))
        rows.append(row)
    r = pd.DataFrame(rows)
    mu, t, n = tstat(r["cont"].dropna().to_numpy())
    return {"days": len(r), "p_take_high": float(r.took_h.mean()), "p_take_low": float(r.took_l.mean()),
            "p_take_both": float((r.took_h & r.took_l).mean()), "p_take_none": float((~r.took_h & ~r.took_l).mean()),
            "first_sweep_cont_to_ny_close_asia_units": mu, "t": t, "n": n,
            "p_close_back_inside_asia_range": float(r["back_inside_close"].dropna().mean())}


def hourly_cost(m15: pd.DataFrame, atr15: pd.Series, lo: pd.Timestamp, hi: pd.Timestamp) -> pd.DataFrame:
    s = m15[(m15.index >= lo) & (m15.index < hi)].copy()
    s["atr"] = atr15.reindex(s.index)
    s["spr_atr"] = s["spread"] / s["atr"]
    s["absret_atr"] = (s["close"] - s["open"]).abs() / s["atr"]
    s["ny_hour"] = s.index.tz_convert(NY).hour
    g = s.groupby("ny_hour")
    return pd.DataFrame({"spread_usd": g["spread"].median(), "atr15_usd": g["atr"].median(),
                         "spread_over_atr": g["spr_atr"].median(), "abs15m_over_atr": g["absret_atr"].mean(),
                         "ticks": g["tick_count"].median()})


def dst_gap_check(m15: pd.DataFrame, atr15: pd.Series) -> dict:
    """Peak-activity UTC hour (mean |15m move|/ATR over 12:00-15:00 UTC) in the US-DST-on/UK-DST-off gap weeks vs
    weeks where both are on and where both are off. Activity, both halves."""
    s = m15.copy()
    s["a"] = (s["close"] - s["open"]).abs() / atr15.reindex(s.index)
    ny_off = s.index.tz_convert(NY).strftime("%z").astype(str)
    ld_off = s.index.tz_convert(LDN).strftime("%z").astype(str)
    reg = np.where((ny_off == "-0400") & (ld_off == "+0100"), "both_summer",
                   np.where((ny_off == "-0500") & (ld_off == "+0000"), "both_winter", "gap"))
    s["reg"] = reg
    s["utc_slot"] = s.index.hour + s.index.minute / 60
    out = {}
    for r in ("both_summer", "both_winter", "gap"):
        x = s[(s.reg == r) & (s.utc_slot >= 11) & (s.utc_slot < 15.5)].groupby("utc_slot")["a"].mean()
        out[r] = {"peak_utc_slot": float(x.idxmax()), "profile": {f"{k:.2f}": round(float(v), 3) for k, v in x.items()}}
    return out


# ---------------------------------------------------------------- calendar effects (daily)
def daily_bars(m1: pd.DataFrame) -> pd.DataFrame:
    td = trading_day(m1.index)
    g = m1.groupby(td)
    d = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                      "close": g["close"].last(), "n": g["close"].size()})
    d = d[(d.n > 600) & (d.index.dayofweek < 5)]
    d["ret_bp"] = 1e4 * np.log(d["close"] / d["close"].shift())
    d["range_bp"] = 1e4 * np.log(d["high"] / d["low"])
    return d


def calendar_effects(d: pd.DataFrame, cal: pd.DataFrame) -> dict:
    x = d[d.index < DISC_END.tz_localize(None)].copy()
    x["rng_rel"] = x["range_bp"] / x["range_bp"].rolling(20).median().shift()
    res: dict = {}
    res["day_of_week"] = {int(k): {"mean_bp": tstat(v["ret_bp"].to_numpy())[0], "t": tstat(v["ret_bp"].to_numpy())[1],
                                   "rng_rel": float(v["rng_rel"].mean()), "n": len(v)}
                          for k, v in x.groupby(x.index.dayofweek)}
    # turn of month: last trading day of month (TOM-1) and first 3 (TOM+1..+3)
    ym = x.index.to_period("M")
    pos = x.groupby(ym).cumcount()
    rpos = x.groupby(ym).cumcount(ascending=False)
    x["tom"] = np.where(rpos == 0, "last", np.where(pos < 3, "first3", "rest"))
    res["turn_of_month"] = {k: {"mean_bp": tstat(v["ret_bp"].to_numpy())[0], "t": tstat(v["ret_bp"].to_numpy())[1],
                                "rng_rel": float(v["rng_rel"].mean()), "n": len(v)} for k, v in x.groupby("tom")}
    qe = (rpos == 0) & x.index.month.isin([3, 6, 9, 12])
    res["quarter_end_day"] = dict(zip(("mean_bp", "t", "n"), tstat(x.loc[qe, "ret_bp"].to_numpy())))
    res["month_of_year"] = {int(k): {"mean_bp_per_day": tstat(v["ret_bp"].to_numpy())[0],
                                     "t": tstat(v["ret_bp"].to_numpy())[1], "n": len(v),
                                     "years_pos": int((v.groupby(v.index.year)["ret_bp"].sum() > 0).sum())}
                            for k, v in x.groupby(x.index.month)}
    # options expiry proxy: COMEX OG monthly options expire 4 business days before month end (CME rule, holidays
    # ignored: approximate); futures FND proxy: last business day of the month before Feb/Apr/Jun/Aug/Dec delivery
    x["og_exp"] = rpos == 4
    x["og_exp_week"] = (rpos >= 3) & (rpos <= 6)
    x["fnd"] = (rpos == 0) & x.index.month.isin([1, 3, 5, 7, 11])
    x["gsci_roll"] = (pos >= 4) & (pos <= 8) & x.index.month.isin([1, 3, 5, 7, 11])
    for k in ("og_exp", "og_exp_week", "fnd", "gsci_roll"):
        v = x[x[k]]
        res[k] = {"mean_bp": tstat(v["ret_bp"].to_numpy())[0], "t": tstat(v["ret_bp"].to_numpy())[1],
                  "rng_rel": float(v["rng_rel"].mean()), "n": len(v)}
    # macro events (scheduled times only; no surprise used)
    c = cal.copy()
    c["day"] = trading_day(pd.DatetimeIndex(c["event_utc"]))
    for et in ("fomc_statement", "nfp", "cpi", "pce"):
        days = set(c.loc[c.event_type == et, "day"])
        m = x.index.isin(days)
        res[f"event_{et}"] = {"mean_bp": tstat(x.loc[m, "ret_bp"].to_numpy())[0],
                              "t": tstat(x.loc[m, "ret_bp"].to_numpy())[1],
                              "abs_mean_bp": float(x.loc[m, "ret_bp"].abs().mean()),
                              "rng_rel": float(x.loc[m, "rng_rel"].mean()), "n": int(m.sum())}
        # day before / after
        idx = np.where(m)[0]
        for lag, nm in ((-1, "before"), (1, "after")):
            j = idx + lag
            j = j[(j >= 0) & (j < len(x))]
            res[f"event_{et}"][f"{nm}_mean_bp"] = tstat(x["ret_bp"].to_numpy()[j])[0]
            res[f"event_{et}"][f"{nm}_t"] = tstat(x["ret_bp"].to_numpy()[j])[1]
            res[f"event_{et}"][f"{nm}_rng_rel"] = float(x["rng_rel"].to_numpy()[j].mean())
    nonev = ~x.index.isin(set(c.loc[c.tier == 1, "day"]))
    res["non_tier1_days_rng_rel"] = float(x.loc[nonev, "rng_rel"].mean())
    # FOMC cycle (Cieslak, Morse, Vissing-Jorgensen 2019 even weeks): week 0,2,4,6 vs odd, days since last FOMC
    fomc = sorted(c.loc[c.event_type == "fomc_statement", "day"])
    fd = pd.DatetimeIndex(fomc)
    k = np.searchsorted(fd.values, x.index.values, side="right") - 1
    ds = (x.index.values - fd.values[np.clip(k, 0, None)]).astype("timedelta64[D]").astype(int)
    wk = ds // 7
    x["fomc_week"] = np.where(k >= 0, wk, -1)
    res["fomc_cycle_week"] = {int(w): {"mean_bp": tstat(v["ret_bp"].to_numpy())[0], "t": tstat(v["ret_bp"].to_numpy())[1],
                                       "n": len(v)} for w, v in x[(x.fomc_week >= 0) & (x.fomc_week <= 6)].groupby("fomc_week")}
    ev = x[(x.fomc_week >= 0) & (x.fomc_week <= 6)]
    even = ev[ev.fomc_week % 2 == 0]["ret_bp"].to_numpy()
    odd = ev[ev.fomc_week % 2 == 1]["ret_bp"].to_numpy()
    res["fomc_even_vs_odd"] = {"even": tstat(even), "odd": tstat(odd)}
    return res


def multiday(d: pd.DataFrame) -> dict:
    """30/60/90-day signals vs forward returns (non-overlapping samples), discovery half only."""
    x = d.copy()
    lp = np.log(x["close"])
    out = {}
    r1 = lp.diff()
    vol = r1.rolling(60).std()
    for n in (30, 60, 90):
        x[f"mom{n}"] = (lp - lp.shift(n)) / (vol * np.sqrt(n))
        hi, lo = x["high"].rolling(n).max(), x["low"].rolling(n).min()
        x[f"pos{n}"] = (x["close"] - lo) / (hi - lo)
    x["rv20"] = r1.rolling(20).std()
    x["rv90"] = r1.rolling(90).std()
    x["vreg"] = x["rv20"] / x["rv90"]
    for h in (5, 20):
        x[f"fwd{h}"] = (lp.shift(-h) - lp) / (vol * np.sqrt(h))
        x[f"fwdrv{h}"] = r1[::-1].rolling(h).std()[::-1].shift(-1)
    disc = x[x.index < DISC_END.tz_localize(None)].dropna()
    for h in (5, 20):
        s = disc.iloc[::h]
        for sig in ("mom30", "mom60", "mom90", "pos30", "pos60", "pos90", "vreg"):
            ic = s[[sig, f"fwd{h}"]].corr(method="spearman").iloc[0, 1]
            n = len(s)
            sign_ret = np.sign(s[sig] - (0.5 if sig.startswith("pos") else (1.0 if sig == "vreg" else 0.0))) * s[f"fwd{h}"]
            out[f"{sig}->fwd{h}"] = {"ic": float(ic), "t_ic": float(ic * np.sqrt(n - 2) / np.sqrt(1 - ic * ic)),
                                     "n": n, "sign_rule_mean_z": float(sign_ret.mean()),
                                     "sign_rule_t": float(sign_ret.mean() / (sign_ret.std() / np.sqrt(n)))}
        ic = s[["rv20", f"fwdrv{h}"]].corr(method="spearman").iloc[0, 1]
        out[f"rv20->fwd_rv{h}"] = {"ic": float(ic), "n": len(s)}
    # by sub-period sign stability of mom60 -> fwd20
    s = disc.iloc[::20]
    out["mom60_fwd20_by_year"] = {int(y): float(v[["mom60", "fwd20"]].corr(method="spearman").iloc[0, 1])
                                  for y, v in s.groupby(s.index.year)}
    return out


def variance_ratios(d: pd.DataFrame, m15: pd.DataFrame) -> dict:
    """Lo-MacKinlay VR(q) with the heteroskedasticity-robust z, discovery half: daily q and 15m q."""
    def vr(r: np.ndarray, q: int) -> tuple[float, float]:
        r = r[np.isfinite(r)]
        mu = r.mean()
        n = len(r)
        rq = np.convolve(r, np.ones(q), "valid")
        v1 = ((r - mu) ** 2).sum() / (n - 1)
        vq = ((rq - q * mu) ** 2).sum() / (q * (n - q + 1) * (1 - q / n))
        e2 = (r - mu) ** 2
        delta = [((e2[j:] * e2[:-j]).sum()) / (e2.sum() ** 2) for j in range(1, q)]
        theta = sum((2 * (q - j) / q) ** 2 * delta[j - 1] for j in range(1, q)) * n
        z = (vq / v1 - 1) / np.sqrt(theta / n)
        return float(vq / v1), float(z)
    dd = d[d.index < DISC_END.tz_localize(None)]
    rd = np.diff(np.log(dd["close"].to_numpy()))
    m = m15[m15.index < DISC_END]
    r15 = np.diff(np.log(m["close"].to_numpy()))
    r15 = r15[np.abs(r15) < 0.05]   # drop the weekend gaps
    return {"daily": {q: vr(rd, q) for q in (2, 5, 10, 20, 30, 60, 90)},
            "15m": {q: vr(r15, q) for q in (2, 4, 8, 16, 32, 96)}}


def periodogram(d: pd.DataFrame) -> dict:
    """Power at 20-120 trading-day periods in daily returns and |returns| vs a 500-shuffle null (max over band)."""
    dd = d[d.index < DISC_END.tz_localize(None)]
    rng = np.random.default_rng(0)
    res = {}
    for nm, x in (("ret", dd["ret_bp"].dropna().to_numpy()), ("absret", dd["ret_bp"].dropna().abs().to_numpy())):
        x = x - x.mean()
        n = len(x)
        f = np.fft.rfftfreq(n)
        P = np.abs(np.fft.rfft(x)) ** 2
        band = (f > 1 / 120) & (f < 1 / 20)
        # null: block shuffle of 5-day blocks keeps short dependence, destroys long cycles
        nul = []
        for _ in range(500):
            blocks = x[: n - n % 5].reshape(-1, 5)
            y = blocks[rng.permutation(len(blocks))].ravel()
            Py = np.abs(np.fft.rfft(y, n=n)) ** 2
            nul.append((Py[band] / Py[1:].mean()).max())
        obs = P[band] / P[1:].mean()
        top = np.argsort(obs)[::-1][:3]
        res[nm] = {"top_periods_days": [float(1 / f[band][i]) for i in top], "top_power_rel": [float(obs[i]) for i in top],
                   "null_95_max": float(np.quantile(nul, 0.95)), "p_max": float((np.array(nul) >= obs.max()).mean()),
                   "power_at_30_60_90": {p: float(obs[np.argmin(np.abs(1 / f[band] - p))]) for p in (30, 60, 90)}}
    return res


# ---------------------------------------------------------------- Fibonacci vs random levels
def level_reactions(m15: pd.DataFrame, atr15: pd.Series, days: pd.DatetimeIndex, d: pd.DataFrame, source: str,
                    fracs: np.ndarray, hold: int = 8) -> pd.DataFrame:
    """For each day, levels at `fracs` of the reference swing (prior day, or the Asia range for the London+NY session),
    retracement direction set by the swing's direction. First touch after the session start: bounce = signed move
    from the level to the close `hold` 15m bars later, in ATR15 units (positive = level held). Discovery half."""
    a = anchors(days)
    idx = m15.index
    hi, lo, cl = m15["high"].to_numpy(), m15["low"].to_numpy(), m15["close"].to_numpy()
    at = atr15.reindex(idx).to_numpy()
    rows = []
    dprev = d.shift()
    for k, day in enumerate(days):
        if source == "prior_day":
            if day not in dprev.index or pd.isna(dprev.at[day, "high"]):
                continue
            H, L, up = dprev.at[day, "high"], dprev.at[day, "low"], dprev.at[day, "close"] > dprev.at[day, "open"]
            t0, t1 = a["tokyo_open"][k], a["ny_close"][k]
        else:
            t0a, t0 = a["tokyo_open"][k], a["london_open"][k]
            if pd.isna(t0a) or pd.isna(t0):
                continue
            i0a, i0b = idx.searchsorted(t0a), idx.searchsorted(t0)
            if i0b - i0a < 8:
                continue
            H, L = hi[i0a:i0b].max(), lo[i0a:i0b].min()
            up = cl[i0b - 1] > m15["open"].to_numpy()[i0a]
            t1 = a["ny_close"][k]
        if pd.isna(t0) or pd.isna(t1):
            continue
        i0, i1 = idx.searchsorted(t0), idx.searchsorted(t1)
        if i1 - i0 < hold + 4:
            continue
        R = H - L
        if R <= 0:
            continue
        start = cl[i0 - 1] if i0 > 0 else np.nan
        for f in fracs:
            lvl = H - f * R if up else L + f * R
            side = 1 if start > lvl else -1           # +1: level below price (support), -1: resistance
            for j in range(i0, i1 - hold):
                if lo[j] <= lvl <= hi[j]:
                    if not np.isfinite(at[j]) or at[j] <= 0:
                        break
                    rows.append({"day": day, "frac": f, "bounce_atr": side * (cl[j + hold] - lvl) / at[j],
                                 "atr": at[j]})
                    break
    return pd.DataFrame(rows)


def fib_test(m15, atr15, days, d) -> dict:
    fib = np.array([0.236, 0.382, 0.5, 0.618, 0.786])
    rng = np.random.default_rng(7)
    ctrl = np.round(rng.uniform(0.15, 0.85, 40), 4)
    res = {}
    for src in ("prior_day", "asia_range"):
        df = level_reactions(m15, atr15, days, d, src, np.concatenate([fib, ctrl]))
        df["is_fib"] = df["frac"].isin(fib)
        f_ = df[df.is_fib]
        c_ = df[~df.is_fib]
        per = {str(k): tstat(v["bounce_atr"].to_numpy()) for k, v in f_.groupby("frac")}
        # day-block bootstrap of the fib-minus-control difference
        dayids = df["day"].unique()
        g = {dd: v for dd, v in df.groupby("day")}
        diffs = []
        for _ in range(300):
            pick = rng.choice(dayids, len(dayids))
            s = pd.concat([g[p] for p in pick])
            diffs.append(s.loc[s.is_fib, "bounce_atr"].mean() - s.loc[~s.is_fib, "bounce_atr"].mean())
        res[src] = {"fib_mean_bounce_atr": float(f_["bounce_atr"].mean()), "ctrl_mean_bounce_atr": float(c_["bounce_atr"].mean()),
                    "diff": float(f_["bounce_atr"].mean() - c_["bounce_atr"].mean()),
                    "diff_ci95": [float(np.quantile(diffs, 0.025)), float(np.quantile(diffs, 0.975))],
                    "p_hold_fib": float((f_["bounce_atr"] > 0).mean()), "p_hold_ctrl": float((c_["bounce_atr"] > 0).mean()),
                    "n_fib": len(f_), "n_ctrl": len(c_), "per_fib_level": per,
                    "ctrl_rank_of_fib_mean": float((c_.groupby("frac")["bounce_atr"].mean() < f_["bounce_atr"].mean()).mean())}
    return res


def fib_time(d: pd.DataFrame) -> dict:
    """Spacing of 10-day swing pivots (centred window: historical description only, never a live input). Share of
    gaps equal to a Fibonacci number vs the mean share of the two neighbouring integers."""
    x = d[d.index < DISC_END.tz_localize(None)]
    h, lw = x["high"], x["low"]
    w = 10
    ph = h == h.rolling(2 * w + 1, center=True).max()
    pl = lw == lw.rolling(2 * w + 1, center=True).min()
    piv = np.where(ph | pl)[0]
    gaps = np.diff(piv)
    cnt = pd.Series(gaps).value_counts()
    res = {}
    for fnum in (8, 13, 21, 34, 55):
        obs = cnt.get(fnum, 0)
        nb = (cnt.get(fnum - 1, 0) + cnt.get(fnum + 1, 0)) / 2
        res[fnum] = {"count": int(obs), "neighbour_mean": float(nb)}
    res["n_gaps"] = int(len(gaps))
    return res


# ---------------------------------------------------------------- oscillators by session
def oscillators(m15: pd.DataFrame, atr15: pd.Series) -> dict:
    b = m15[m15.index < DISC_END].copy()
    c, h, lw = b["close"], b["high"], b["low"]
    d = c.diff()
    up, dn = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean(), (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    b["rsi"] = 100 - 100 / (1 + up / dn)
    hh, ll = h.rolling(14).max(), lw.rolling(14).min()
    b["stoch"] = 100 * (c - ll) / (hh - ll)                          # Williams %R = stoch - 100: same ranks
    tp = (h + lw + c) / 3
    md = tp.rolling(20).apply(lambda v: np.abs(v - v.mean()).mean(), raw=True)
    b["cci"] = (tp - tp.rolling(20).mean()) / (0.015 * md)
    mf = tp * b["tick_count"]
    pos = mf.where(tp > tp.shift(), 0).rolling(14).sum()
    neg = mf.where(tp < tp.shift(), 0).rolling(14).sum()
    b["mfi"] = 100 - 100 / (1 + pos / neg)
    e12, e26 = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    macd = e12 - e26
    b["macd_h"] = (macd - macd.ewm(span=9, adjust=False).mean()) / atr15.reindex(b.index)
    at = atr15.reindex(b.index)
    for hz in (4, 16):
        b[f"fwd{hz}"] = (c.shift(-hz - 1) - c.shift(-1)) / at       # enter next bar open ~ close; conservative lag
    b["sess"] = pd.Categorical(np.where(b.index.tz_convert(LDN).hour < 8, "asia",
                                        np.where(b.index.tz_convert(NY).hour * 60 + b.index.tz_convert(NY).minute < 510,
                                                 "london", "newyork")))
    b["day"] = trading_day(b.index)
    b = b.dropna()
    out = {}
    for osc in ("rsi", "stoch", "cci", "mfi", "macd_h"):
        for s in ("asia", "london", "newyork"):
            x = b[b.sess == s]
            ics = x.groupby("day")[[osc, "fwd4"]].corr(method="spearman").xs(osc, level=1)["fwd4"].dropna()
            mu, t, n = tstat(ics.to_numpy())
            out[f"{osc}|{s}|ic_fwd4"] = {"mean_daily_ic": mu, "t_days": t, "days": n}
    # extremes: RSI 30/70 first cross, fade in ATR units over 4 and 16 bars, per session
    b["rsi_prev"] = b["rsi"].shift()
    for s in ("asia", "london", "newyork"):
        x = b[b.sess == s]
        lo_x = x[(x.rsi < 30) & (x.rsi_prev >= 30)]
        hi_x = x[(x.rsi > 70) & (x.rsi_prev <= 70)]
        for hz in (4, 16):
            fade = np.concatenate([lo_x[f"fwd{hz}"].to_numpy(), -hi_x[f"fwd{hz}"].to_numpy()])
            mu, t, n = tstat(fade)
            out[f"rsi_extreme_fade|{s}|{hz}bars"] = {"mean_atr": mu, "t_iid": t, "n": n}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--cal", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    m1 = load(a.raw)
    print("loaded", len(m1), m1.index.min(), m1.index.max())
    m15 = resample(m1, "15min")
    atr15 = wilder_atr(m15)
    d = daily_bars(m1)
    atr15_day = atr15.groupby(trading_day(atr15.index)).median().shift()    # prior-day median ATR15: known at the open
    days_all = pd.DatetimeIndex(d.index)
    disc_days = days_all[days_all < DISC_END.tz_localize(None)]
    conf_lo = pd.Timestamp("2018-01-01", tz="UTC")
    m1_disc = m1[m1.index < DISC_END + pd.Timedelta(days=1)]
    m15_disc = m15[m15.index < DISC_END + pd.Timedelta(days=1)]
    res: dict = {}
    res["sessions_disc"] = session_tables(m1_disc, disc_days[1:])
    res["anchors_disc"] = anchor_windows(m1_disc, disc_days[1:], atr15_day)
    res["or_london_30"] = opening_range(m15_disc, disc_days[1:], "london_open", "ny_open", 30)
    res["or_ny_30"] = opening_range(m15_disc, disc_days[1:], "ny_open", "ny_close", 30)
    res["or_ny_60"] = opening_range(m15_disc, disc_days[1:], "ny_open", "ny_close", 60)
    res["asia_sweep"] = asia_sweep(m15_disc, disc_days[1:])
    res["cost_hourly_2010_2017"] = hourly_cost(m15, atr15, pd.Timestamp("2010-01-01", tz="UTC"), DISC_END).round(3).to_dict()
    res["cost_hourly_2019_2025q3"] = hourly_cost(m15, atr15, pd.Timestamp("2019-01-01", tz="UTC"), HOLDOUT_START).round(3).to_dict()
    res["cost_hourly_2025"] = hourly_cost(m15, atr15, pd.Timestamp("2025-01-01", tz="UTC"), HOLDOUT_START).round(3).to_dict()
    res["dst_gap"] = dst_gap_check(m15, atr15)
    cal = pd.read_parquet(a.cal)
    res["calendar"] = calendar_effects(d, cal)
    res["multiday"] = multiday(d)
    res["variance_ratio"] = variance_ratios(d, m15)
    res["periodogram"] = periodogram(d)
    res["fib_levels"] = fib_test(m15_disc, atr15, disc_days[1:], d)
    res["fib_time"] = fib_time(d)
    res["oscillators"] = oscillators(m15, atr15)
    (a.out / "time_structure.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps(res, indent=1, default=str)[:200])
    _ = conf_lo


if __name__ == "__main__":
    main()
