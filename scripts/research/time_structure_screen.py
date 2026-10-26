"""Time-structure screen for XAUUSD (research slice B, docs/research/time-structure/cycles.md).

Descriptive, pre-holdout diagnostics only: rolling-window momentum / mean reversion / range position / volatility
regime (30/60/90 d), day of week, month of year, turn of month, month and quarter end, COMEX expiry dates, scheduled
US events (pre-FOMC drift, NFP, CPI), festival windows, hour of day. Every calendar effect is compared with
randomised-date controls of the same size (and the same weekday or clock time where it matters), and every row
carries its effective n. Nothing here is a trial: it reads no label, spends no budget and is not a verdict.

Data: release data-v1 1m bars (scripts/fetch_data_release.py, --download-only), mid of bid/ask close, stamped at
`visible_at` (bar close, so a return from t to t' uses only bars visible at t). Default window is the discovery half 2010-01-01..2017-12-31 (README 4.1); `--end` widens it, never past
`research.holdout_from` (2025-10-01). US events: release calendar-v1 `us_calendar.parquet`.

Usage: uv run python scripts/research/time_structure_screen.py --bars-dir DIR --calendar FILE [--draws 5000]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from goldbot.data.cme_holidays import cme_metals_holidays  # noqa: E402
from goldbot.data.timeutil import epoch_ns  # noqa: E402

CUTOFF = pd.Timestamp("2025-10-01", tz="UTC")  # research.holdout_from: never read
NY = "America/New_York"
BP = 1e4
RNG = np.random.default_rng(20261026)

# Frozen public date tables (typed from the published calendars; the data engineer should verify, see cycles.md).
DIWALI = ["2010-11-05", "2011-10-26", "2012-11-13", "2013-11-03", "2014-10-23", "2015-11-11", "2016-10-30",
          "2017-10-19", "2018-11-07", "2019-10-27", "2020-11-14", "2021-11-04", "2022-10-24", "2023-11-12",
          "2024-11-01"]
CNY = ["2010-02-14", "2011-02-03", "2012-01-23", "2013-02-10", "2014-01-31", "2015-02-19", "2016-02-08",
       "2017-01-28", "2018-02-16", "2019-02-05", "2020-01-25", "2021-02-12", "2022-02-01", "2023-01-22",
       "2024-02-10", "2025-01-29"]


def load_mid(bars_dir: Path) -> pd.Series:
    parts = []
    for f in sorted(bars_dir.glob("xauusd_1m_dukascopy_*.parquet")):
        year = int(f.stem.rsplit("_", 1)[1])
        if year > CUTOFF.year:
            continue
        d = pd.read_parquet(f, columns=["visible_at", "bid_close", "ask_close"])
        d["visible_at"] = pd.to_datetime(d["visible_at"], utc=True)
        d = d[d["visible_at"] < CUTOFF]
        parts.append(pd.Series(((d.bid_close + d.ask_close) / 2).to_numpy(), index=pd.DatetimeIndex(d.visible_at)))
    s = pd.concat(parts).sort_index()
    return s[~s.index.duplicated(keep="last")].dropna()


def daily_close(mid: pd.Series) -> pd.Series:
    """Last mid of each New York trading day (17:00 New York roll). Index = the date the session closes on."""
    ny = mid.index.tz_convert(NY) + pd.Timedelta(hours=7)  # bars from 17:00 belong to the next date: label = close date
    day = pd.DatetimeIndex(ny.tz_localize(None).normalize())
    g = mid.groupby(day)
    c = g.last()[g.size() >= 200]
    return c[c.index.dayofweek < 5]


def nw_t(x: np.ndarray, lag: int) -> float:
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 10:
        return float("nan")
    e = x - x.mean()
    v = e @ e / n
    for k in range(1, min(lag, n - 1) + 1):
        v += 2 * (1 - k / (lag + 1)) * (e[k:] @ e[:-k]) / n
    return float(x.mean() / np.sqrt(max(v, 1e-30) / n))


def fmt(x: float, d: int = 2) -> str:
    return "nan" if x is None or not np.isfinite(x) else f"{x:.{d}f}"


def md(rows: list[list[Any]], head: list[str]) -> str:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


# ---------------------------------------------------------------- A: rolling windows
def rolling_windows(c: pd.Series) -> None:
    lp = np.log(c)
    r1 = lp.diff()
    print("\n## A. Rolling-window signals (daily close, forward return from the NEXT close)\n")
    for H in (5, 20):
        fwd = lp.shift(-H) - lp
        base = fwd.mean() * BP
        rows = []
        for L in (20, 30, 60, 90, 120, 250):
            s = lp - lp.shift(L)
            d = pd.DataFrame({"s": s, "f": fwd}).dropna()
            sg = np.sign(d.s)
            tsm = (sg * d.f) * BP
            up, dn = d.f[d.s > 0] * BP, d.f[d.s < 0] * BP
            # reversal: sign of z-score of price vs L-day mean
            ma = lp.rolling(L).mean()
            sd = r1.rolling(L).std() * np.sqrt(L)
            z = ((lp - ma) / sd)
            dz = pd.DataFrame({"z": z, "f": fwd}).dropna()
            top, bot = dz.f[dz.z > 1] * BP, dz.f[dz.z < -1] * BP
            rows.append([L, len(d), fmt(len(d) / H, 0), fmt(tsm.mean()), fmt(nw_t(tsm.to_numpy(), 2 * H)),
                         fmt(up.mean()), fmt(dn.mean()), fmt(top.mean()) + f" ({len(top)})",
                         fmt(bot.mean()) + f" ({len(bot)})"])
        print(f"\nForward {H} d. Unconditional mean {base:.1f} bp. TSMOM = sign(L-day return) x forward return, "
              f"NW lag {2*H}; eff n = N/{H}.\n")
        print(md(rows, ["L d", "N", "eff n", "TSMOM bp", "NW t", "fwd|up bp", "fwd|down bp", "z>1 fwd bp (n)",
                        "z<-1 fwd bp (n)"]))
        # range position
        rows = []
        for L in (30, 60, 90):
            hi, lo = c.rolling(L).max(), c.rolling(L).min()
            p = (c - lo) / (hi - lo)
            q = pd.qcut(p, 5, labels=False, duplicates="drop")
            d = pd.DataFrame({"q": q, "f": fwd * BP}).dropna()
            m = d.groupby("q").f.mean()
            spread = (d.f[d.q == 4].reset_index(drop=True).mean() - d.f[d.q == 0].mean())
            brk = (c >= hi) & (c.shift(1) < hi.shift(1))  # new L-day closing high
            bk = (fwd * BP)[brk].dropna()
            # cluster: count breakout episodes separated by >= H days
            ep = int((pd.Series(np.where(brk, np.arange(len(c)), np.nan)).dropna().diff().fillna(99) >= H).sum())
            rows.append([L, " / ".join(fmt(v, 1) for v in m.to_list()), fmt(spread, 1),
                         f"{len(bk)} ({ep} ep)", fmt(bk.mean(), 1), fmt(nw_t(bk.to_numpy(), H), 2)])
        print(f"\nPosition in L-day closing range (quintiles 0..4 low to high), forward {H} d mean bp; and "
              f"new L-day closing high.\n")
        print(md(rows, ["L d", "Q0..Q4 fwd bp", "Q4-Q0 bp", "new-high N (episodes)", "new-high fwd bp", "NW t"]))
    # volatility regime
    print("\n### Volatility regime by window (point-in-time expanding percentile, 250 d burn-in)\n")
    fwd5 = lp.shift(-5) - lp
    rows = []
    for L in (30, 60, 90):
        rv = r1.rolling(L).std() * np.sqrt(252)
        pct = rv.expanding(250).rank(pct=True)
        ter = pd.cut(pct, [0, 1 / 3, 2 / 3, 1.0001], labels=[0, 1, 2], include_lowest=True)
        d = pd.DataFrame({"t": ter, "ab": fwd5.abs() * BP, "f": fwd5 * BP, "rv": rv}).dropna()
        g = d.groupby("t", observed=True)
        rows.append([L, " / ".join(fmt(v, 0) for v in g.ab.mean().to_list()),
                     " / ".join(fmt(v, 1) for v in g.f.mean().to_list()),
                     fmt(nw_t(d.f[d.t == 2].to_numpy(), 10) - 0, 2), fmt(rv.iloc[-1] * 100, 1)])
    print(md(rows, ["L d", "fwd 5d |ret| bp, low/mid/high vol", "fwd 5d ret bp low/mid/high",
                    "NW t high-vol fwd ret", "last rv % (ann.)"]))
    ratio = (r1.rolling(30).std() / r1.rolling(90).std())
    d = pd.DataFrame({"r": ratio, "ab": fwd5.abs() * BP}).dropna()
    hi_, lo_ = d.ab[d.r > 1.25].mean(), d.ab[d.r < 0.8].mean()
    print(f"\nrv30/rv90 > 1.25 (n={int((d.r>1.25).sum())}): fwd 5d |ret| {hi_:.0f} bp; < 0.8 (n={int((d.r<0.8).sum())}): "
          f"{lo_:.0f} bp; all {d.ab.mean():.0f} bp")


# ---------------------------------------------------------------- B: calendar effects in daily returns
def random_block_p(r: np.ndarray, idx_sets: list[np.ndarray], draws: int, stat=np.mean) -> tuple[float, float, float]:
    """Observed mean of r over the union of event index sets vs random contiguous blocks of the same length,
    one block per event set at a uniformly random position. Returns observed, control mean, two-sided p."""
    obs_idx = np.concatenate(idx_sets)
    obs = r[obs_idx].mean()
    lens = [len(s) for s in idx_sets]
    n = len(r)
    sims = np.empty(draws)
    for k in range(draws):
        st = RNG.integers(0, n - max(lens), size=len(lens))
        sims[k] = np.concatenate([r[a:a + m] for a, m in zip(st, lens)]).mean()
    p = (np.sum(np.abs(sims - sims.mean()) >= abs(obs - sims.mean())) + 1) / (draws + 1)
    return obs, sims.mean(), p


def calendar_effects(c: pd.Series, draws: int, cal_path: Path) -> None:
    r = np.log(c).diff().dropna()
    rb = r * BP
    idx = r.index
    print("\n## B. Calendar effects on daily returns (close-to-close, 17:00 New York roll)\n")
    print(f"N days {len(r)}, mean {rb.mean():.2f} bp/day, sd {rb.std():.0f} bp; "
          f"t of the mean {rb.mean()/(rb.std()/np.sqrt(len(rb))):.2f}\n")
    # day of week
    rows = []
    half = idx < pd.Timestamp("2014-01-01")  # sub-periods of the window read
    for dw, nm in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri"]):
        m = idx.dayofweek == dw
        x = rb[m]
        rows.append([nm, int(m.sum()), fmt(x.mean()), fmt(x.mean() / (x.std() / np.sqrt(len(x)))),
                     fmt(rb[m & half].mean()), fmt(rb[m & ~half].mean()), fmt(x.abs().mean(), 0)])
    print("### Day of week (day labelled by its closing date; Monday includes the Sunday-evening session)\n")
    print(md(rows, ["day", "N", "mean bp", "t", "first half", "second half", "mean |ret| bp"]))
    # Friday vs the other days: permutation of weekday labels within calendar years (keeps the yearly drift)
    fri = idx.dayofweek == 4
    obs = rb[fri].mean() - rb[~fri].mean()
    yrs = idx.year.to_numpy()
    arr_all = rb.to_numpy()
    sims = np.empty(draws)
    for k in range(draws):
        lab = fri.copy()
        for y in np.unique(yrs):
            m = np.where(yrs == y)[0]
            lab[m] = RNG.permutation(fri[m])
        sims[k] = arr_all[lab].mean() - arr_all[~lab].mean()
    p = (np.sum(np.abs(sims) >= abs(obs)) + 1) / (draws + 1)
    byy = rb[fri].groupby(rb[fri].index.year).mean() - rb[~fri].groupby(rb[~fri].index.year).mean()
    nfp_days = set(pd.read_parquet(cal_path).query("event_type == 'nfp' and status == 'scheduled'")
                   .event_utc.dt.tz_convert(NY).dt.tz_localize(None).dt.normalize())
    is_nfp = np.array([d in nfp_days for d in idx])
    print(f"\nFriday minus other days: {obs:.1f} bp/day, within-year label permutation p {p:.3f}, "
          f"years with Friday > others {int((byy > 0).sum())}/{len(byy)}; Friday excluding NFP days "
          f"{rb[fri & ~is_nfp].mean():.1f} bp (n {int((fri & ~is_nfp).sum())}), NFP Fridays "
          f"{rb[fri & is_nfp].mean():.1f} bp (n {int((fri & is_nfp).sum())})")
    # month of year
    rows = []
    for mth in range(1, 13):
        m = idx.month == mth
        x = rb[m]
        yr = x.groupby(x.index.year).sum()
        rows.append([mth, int(m.sum()), len(yr), fmt(x.mean()), fmt(x.mean() / (x.std() / np.sqrt(len(x)))),
                     fmt(yr.mean(), 0), fmt((yr > 0).mean(), 2), fmt(rb[m & half].mean()), fmt(rb[m & ~half].mean())])
    print("\n### Month of year (monthly sum is bp per calendar month; t on daily returns, no cluster adjustment, "
          "so read the hit rate over years)\n")
    print(md(rows, ["month", "N d", "years", "mean bp/day", "t", "mean bp/month", "share of years > 0",
                    "first half", "second half"]))
    q4 = np.isin(idx.month, [10, 11, 12])
    ja = idx.month == 1
    print(f"\nQ4 mean {rb[q4].mean():.2f} bp/d vs rest {rb[~q4].mean():.2f}; Jan {rb[ja].mean():.2f}; "
          f"Aug-Sep {rb[np.isin(idx.month,[8,9])].mean():.2f}")
    # turn of month / month end / quarter end
    ym = idx.year * 12 + idx.month
    arr = rb.to_numpy()
    firsts = pd.Series(np.arange(len(idx))).groupby(ym).first().to_numpy()
    lasts = pd.Series(np.arange(len(idx))).groupby(ym).last().to_numpy()
    def sets(back: int, fwd: int, months: set[int] | None = None) -> list[np.ndarray]:
        out = []
        for f, la, mm in zip(firsts[1:], lasts[:-1], pd.Series(idx.month).groupby(ym).first().to_numpy()[:-1]):
            if months and mm not in months:
                continue
            out.append(np.arange(la - back + 1, f + fwd))
        return out
    rows = []
    for nm, back, fwd, months in [("TOM -1..+3 (Ariel/Lakonishok-Smidt)", 1, 4, None), ("TOM -2..+2", 2, 3, None),
                                  ("last 3 days of month", 3, 1, None), ("quarter end -3..+1",
                                                                        3, 2, {3, 6, 9, 12})]:
        s = sets(back, fwd, months)
        s = [x for x in s if x.min() >= 0 and x.max() < len(arr)]
        obs, ctl, p = random_block_p(arr, s, draws)
        by_year = pd.Series([arr[x].mean() for x in s], index=[idx[x[0]].year for x in s]).groupby(level=0).mean()
        rows.append([nm, len(s), sum(len(x) for x in s), fmt(obs), fmt(ctl), fmt(p, 3),
                     fmt((by_year > 0).mean(), 2)])
    print("\n### Turn of month, month end, quarter end (mean daily bp inside the window vs random same-length "
          f"blocks, {draws} draws; eff n = number of windows)\n")
    print(md(rows, ["window", "eff n (windows)", "days", "obs bp/d", "random-block bp/d", "p (two-sided)",
                    "share of years > 0"]))
    # first trading day / year turn
    fy = [np.where(idx.year == y)[0][0] for y in sorted(set(idx.year)) if y > idx.year.min()]
    print(f"\nFirst trading day of year (n={len(fy)}): mean {arr[fy].mean():.1f} bp, "
          f"share>0 {np.mean(arr[fy]>0):.2f}")


# ---------------------------------------------------------------- COMEX expiry
def bdays(year: int, month: int) -> pd.DatetimeIndex:
    d = pd.date_range(f"{year}-{month:02d}-01", periods=pd.Period(f"{year}-{month:02d}").days_in_month)
    hol = {pd.Timestamp(h) for yy in (year,) for h in cme_metals_holidays(yy)}
    return pd.DatetimeIndex([x for x in d if x.dayofweek < 5 and x not in hol])


def comex_dates(first: int, last: int) -> dict[str, list[pd.Timestamp]]:
    out: dict[str, list[pd.Timestamp]] = {"fnd": [], "ltd": [], "opx": []}
    for y in range(first, last + 1):
        for m in range(1, 13):
            b = bdays(y, m)
            out["opx"].append(b[-4])  # standard monthly option: 4th last business day (approximation, see doc)
            if m % 2 == 0:  # delivery months Feb, Apr, Jun, Aug, Oct, Dec
                out["ltd"].append(b[-3])
                py, pm = (y, m - 1) if m > 1 else (y - 1, 12)
                out["fnd"].append(bdays(py, pm)[-1])
    return out


def comex_expiry(c: pd.Series, draws: int) -> None:
    r = (np.log(c).diff().dropna() * BP)
    idx = r.index
    arr = r.to_numpy()
    absarr = np.abs(arr)
    dates = comex_dates(idx.min().year, idx.max().year)
    print("\n## C. COMEX gold expiry dates (rule-derived, CME metals holiday list; approximation, see doc)\n")
    rows = []
    for nm, ds in dates.items():
        pos = np.array([idx.get_loc(d) for d in ds if d in idx])
        for off, lab in ((-1, "d-1"), (0, "d"), (1, "d+1")):
            ii = pos + off
            ii = ii[(ii >= 0) & (ii < len(arr))]
            obs = arr[ii].mean()
            sims = np.array([arr[RNG.integers(0, len(arr), len(ii))].mean() for _ in range(draws)])
            sa = np.array([absarr[RNG.integers(0, len(arr), len(ii))].mean() for _ in range(draws)])
            p = (np.sum(np.abs(sims - sims.mean()) >= abs(obs - sims.mean())) + 1) / (draws + 1)
            pa = (np.sum(sa >= absarr[ii].mean()) + 1) / (draws + 1)
            rows.append([nm, lab, len(ii), fmt(obs), fmt(p, 3), fmt(absarr[ii].mean(), 0), fmt(absarr.mean(), 0),
                         fmt(pa, 3)])
    print(md(rows, ["date", "offset", "n", "mean bp", "p (random days)", "mean |ret| bp", "all-days |ret|",
                    "p (|ret| higher)"]))


# ---------------------------------------------------------------- D: scheduled events on 1m bars
def window_ret(mid_ns: np.ndarray, mid: np.ndarray, t0_ns: np.ndarray, t1_ns: np.ndarray) -> np.ndarray:
    """log(mid at t1) - log(mid at t0), each the last visible mid at or before the stamp; NaN if either stamp
    is more than 15 min past the last bar (closed market)."""
    i0 = np.searchsorted(mid_ns, t0_ns, side="right") - 1
    i1 = np.searchsorted(mid_ns, t1_ns, side="right") - 1
    ok = (i0 >= 0) & (i1 >= 0) & (t0_ns - mid_ns[np.clip(i0, 0, None)] <= 15 * 60e9) & \
        (t1_ns - mid_ns[np.clip(i1, 0, None)] <= 15 * 60e9)
    out = np.full(len(t0_ns), np.nan)
    out[ok] = np.log(mid[i1[ok]]) - np.log(mid[i0[ok]])
    return out * BP


def scheduled_events(mid: pd.Series, cal_path: Path, draws: int) -> None:
    cal = pd.read_parquet(cal_path)
    cal = cal[(cal.status == "scheduled") & (cal.event_utc < CUTOFF)]
    mid_ns, mv = epoch_ns(mid.index), mid.to_numpy()
    tier1 = cal[cal.tier == 1].event_utc
    t1_days = set(tier1.dt.tz_convert(NY).dt.normalize().dt.tz_localize(None))
    all_days = pd.bdate_range(mid.index.min().tz_convert(None).normalize(), CUTOFF.tz_convert(None) - pd.Timedelta(days=1))
    print("\n## D. Scheduled US events on 1m bars (windows relative to the announcement; controls = same New York "
          "clock time and weekday on days with no tier-1 event within one day)\n")
    wins = {"pre24h": (-24 * 60, 0), "pre60m": (-60, 0), "post30m": (0, 30), "post4h": (0, 240),
            "post24h": (0, 1440)}
    for et in ("fomc_statement", "nfp", "cpi", "pce", "ism_manufacturing"):
        ev = cal[cal.event_type == et].event_utc.reset_index(drop=True)
        if ev.empty:
            continue
        loc = ev.dt.tz_convert(NY)
        clock = loc.dt.strftime("%H:%M").mode()[0]
        evd = loc.dt.tz_localize(None).dt.normalize()
        pool = [d for d in all_days if all(((d + pd.Timedelta(days=k)) not in t1_days) for k in (-1, 0, 1))]
        pool_s = pd.Series(pool)
        ctl_loc = (pool_s + pd.to_timedelta(f"{clock}:00")).dt.tz_localize(NY, ambiguous="NaT", nonexistent="NaT")
        ctl_ok = ctl_loc.notna()
        pool_s, ctl_utc = pool_s[ctl_ok].reset_index(drop=True), ctl_loc[ctl_ok].dt.tz_convert("UTC")
        rows = []
        for per, mask_e, mask_c in (("all", np.ones(len(ev), bool), np.ones(len(pool_s), bool)),
                                    ("first half", (evd < "2014-01-01").to_numpy(),
                                     (pool_s < "2014-01-01").to_numpy()),
                                    ("second half", (evd >= "2014-01-01").to_numpy(),
                                     (pool_s >= "2014-01-01").to_numpy())):
            for wn, (a, b) in wins.items():
                e_ns = epoch_ns(pd.DatetimeIndex(ev[mask_e]))
                c_ns = epoch_ns(pd.DatetimeIndex(ctl_utc[mask_c]))
                re_ = window_ret(mid_ns, mv, e_ns + int(a * 60e9), e_ns + int(b * 60e9))
                rc = window_ret(mid_ns, mv, c_ns + int(a * 60e9), c_ns + int(b * 60e9))
                we = evd[mask_e].dt.dayofweek.to_numpy()[~np.isnan(re_)]
                wc = pool_s[mask_c].dt.dayofweek.to_numpy()[~np.isnan(rc)]
                re_, rc = re_[~np.isnan(re_)], rc[~np.isnan(rc)]
                if len(re_) < 8 or len(rc) < 30:
                    continue
                byw = {w: rc[wc == w] for w in np.unique(we)}
                if any(len(v) < 5 for v in byw.values()):
                    continue
                cnt = {w: int((we == w).sum()) for w in byw}
                sims = np.array([sum(RNG.choice(byw[w], cnt[w]).sum() for w in byw) / len(re_)
                                 for _ in range(draws)])
                rc = np.concatenate([byw[w] for w in byw])
                p = (np.sum(np.abs(sims - sims.mean()) >= abs(re_.mean() - sims.mean())) + 1) / (draws + 1)
                se = re_.std() / np.sqrt(len(re_))
                rows.append([per, wn, len(re_), fmt(re_.mean()), fmt(re_.mean() / se), fmt(rc.mean()), fmt(p, 3),
                             fmt(np.abs(re_).mean(), 0), fmt(np.abs(rc).mean(), 0), fmt((re_ > 0).mean(), 2)])
        print(f"\n### {et} (clock {clock} New York, n events {len(ev)})\n")
        print(md(rows, ["period", "window", "n", "mean bp", "t", "control mean bp", "p", "mean |ret|", "control |ret|",
                        "share > 0"]))


# ---------------------------------------------------------------- E: festivals
def festivals(c: pd.Series, draws: int) -> None:
    lp = np.log(c)
    ix = lp.index
    print("\n## E. Festival windows (trading-day windows; controls = same window length at random start, one per "
          "festival; eff n = festivals)\n")
    rows = []
    for nm, ds in (("Diwali", DIWALI), ("Chinese New Year", CNY)):
        for lab, a, b in (("run-up d-15..d-1", -15, -1), ("d-5..d-1", -5, -1), ("d..d+10", 0, 10),
                          ("d-15..d+10", -15, 10)):
            vals = []
            for d in ds:
                t = pd.Timestamp(d)
                if t >= CUTOFF.tz_convert(None):
                    continue
                k = ix.searchsorted(t)
                i0, i1 = k + a - 1, k + b
                if i0 < 0 or i1 >= len(ix):
                    continue
                vals.append((lp.iloc[i1] - lp.iloc[i0]) * BP)
            vals = np.array(vals)
            span = b - a + 1
            sims = np.empty(draws)
            for j in range(draws):
                st = RNG.integers(0, len(lp) - span - 1, len(vals))
                sims[j] = np.mean(lp.to_numpy()[st + span] - lp.to_numpy()[st]) * BP
            p = (np.sum(np.abs(sims - sims.mean()) >= abs(vals.mean() - sims.mean())) + 1) / (draws + 1)
            rows.append([nm, lab, len(vals), fmt(vals.mean()), fmt(vals.mean() / (vals.std() / np.sqrt(len(vals)))),
                         fmt(sims.mean()), fmt(p, 3), fmt((vals > 0).mean(), 2)])
    print(md(rows, ["festival", "window", "eff n", "mean bp", "t", "random-window bp", "p", "share > 0"]))


# ---------------------------------------------------------------- F: hour of day
def hour_of_day(mid: pd.Series) -> None:
    h = mid.resample("1h").last().dropna()
    r = np.log(h).diff().dropna() * BP
    r = r[(r.index.hour >= 0)]
    print("\n## F. Hour-of-day drift (1h returns by UTC hour of the bar close; gaps over 2 h dropped)\n")
    gap = h.index.to_series().diff().dt.total_seconds().reindex(r.index) <= 7200
    r = r[gap.fillna(False)]
    rows = []
    for hr in range(24):
        x = r[r.index.hour == hr]
        if len(x) < 100:
            continue
        yr = x.groupby(x.index.year).mean()
        rows.append([hr, len(x), fmt(x.mean(), 2), fmt(x.mean() / (x.std() / np.sqrt(len(x))), 2),
                     fmt((yr > 0).mean(), 2), fmt(x.abs().mean(), 1)])
    print(md(rows, ["UTC hr (close)", "N", "mean bp", "t", "share of years > 0", "mean |ret| bp"]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars-dir", required=True, type=Path)
    ap.add_argument("--calendar", required=True, type=Path)
    ap.add_argument("--draws", type=int, default=5000)
    ap.add_argument("--end", default="2017-12-31",
                    help="last date read (default: the discovery half; research/time-structure/README 4.1)")
    ap.add_argument("--skip-events", action="store_true")
    a = ap.parse_args()
    global CUTOFF
    CUTOFF = min(CUTOFF, pd.Timestamp(a.end, tz="UTC") + pd.Timedelta(days=1))
    mid = load_mid(a.bars_dir)
    c = daily_close(mid)
    print(f"# time-structure screen: {mid.index.min()} .. {mid.index.max()}, {len(mid)} 1m mids, {len(c)} days; "
          f"last close {c.iloc[-1]:.2f}; holdout >= {CUTOFF.date()} not read")
    rolling_windows(c)
    calendar_effects(c, a.draws, a.calendar)
    comex_expiry(c, a.draws)
    festivals(c, a.draws)
    hour_of_day(mid)
    if not a.skip_events:
        scheduled_events(mid, a.calendar, a.draws)


if __name__ == "__main__":
    main()
