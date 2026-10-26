"""Session / time-structure measurements for XAUUSD on the 1m Dukascopy bars (release data-v1).

Reusable read-only study behind docs/research/time-structure/sessions.md. It registers nothing and runs no backtest job.
Point in time: a price "at T" is the close of the last 1m bar that STARTED before T (the bar is visible at start + 1m),
and every rule enters at or after the stamp it is decided on. Costs are the measured $/oz round trip
(docs/research/cost-model-1m-2026-10-25.md), not the Dukascopy spread (which is ~8x wider than our broker's).
Usage: uv run python docs/research/candidates/time_structure/session_structure.py <dir with xauusd_1m_dukascopy_YYYY.parquet> [section ...]
Sections: dst hourly events asia gao decomp orb spread tf   (default: all)
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from goldbot.data.timeutil import epoch_ns  # noqa: E402

MIN = 60_000_000_000
COST_C, COST_K = 0.144, 0.23          # measured round trip, central / conservative, $ per oz
import os  # noqa: E402

# Default = the DISCOVERY half only (time-structure README 4.1). Override with GB_START / GB_END for activity/cost tables.
START, END = os.environ.get("GB_START", "2010-01-01"), os.environ.get("GB_END", "2018-01-01")
RNG = np.random.default_rng(20261026)


class Bars:
    def __init__(self, folder: Path, start: str = START, end: str = END):
        fr = []
        for p in sorted(folder.glob("xauusd_1m_dukascopy_*.parquet")):
            fr.append(pd.read_parquet(p, columns=["ts_utc", "bid_high", "bid_low", "bid_close", "ask_high",
                                                  "ask_low", "ask_close", "spread_mean"]))
        d = pd.concat(fr).drop_duplicates("ts_utc").sort_values("ts_utc")
        d = d[(d.ts_utc >= pd.Timestamp(start, tz="UTC")) & (d.ts_utc < pd.Timestamp(end, tz="UTC"))]
        self.ts = epoch_ns(pd.DatetimeIndex(d.ts_utc))
        self.c = ((d.bid_close + d.ask_close) / 2).to_numpy()
        self.h = ((d.bid_high + d.ask_high) / 2).to_numpy()
        self.l = ((d.bid_low + d.ask_low) / 2).to_numpy()
        self.sp = d.spread_mean.to_numpy()
        self.dates = pd.bdate_range(start, pd.Timestamp(end) - pd.Timedelta(days=1))

    def px(self, t: np.ndarray, stale_min: int = 10) -> np.ndarray:
        """Price at time t (ns): close of the last bar that started strictly before t, NaN if older than stale_min."""
        i = np.searchsorted(self.ts, t, side="left") - 1
        ok = (i >= 0) & (t - self.ts[np.clip(i, 0, None)] <= stale_min * MIN)
        out = np.where(ok, self.c[np.clip(i, 0, None)], np.nan)
        return out

    def hl(self, t0: int, t1: int) -> tuple[float, float, float]:
        """High, low and last close over bars starting in [t0, t1)."""
        a, b = np.searchsorted(self.ts, [t0, t1])
        if b - a < 0.5 * (t1 - t0) / MIN:
            return np.nan, np.nan, np.nan
        return self.h[a:b].max(), self.l[a:b].min(), self.c[b - 1]


def local_ns(dates: pd.DatetimeIndex, tz: str, hh: int, mm: int = 0) -> np.ndarray:
    ts = (dates + pd.Timedelta(hours=hh, minutes=mm)).tz_localize(tz).tz_convert("UTC")
    return epoch_ns(pd.DatetimeIndex(ts))


def bps(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return (np.log(b) - np.log(a)) * 1e4


def tstat(x: np.ndarray) -> tuple[int, float, float]:
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 3:
        return n, np.nan, np.nan
    return n, float(x.mean()), float(x.mean() / (x.std(ddof=1) / np.sqrt(n)))


def md(df: pd.DataFrame, floatfmt: str = ".2f") -> str:
    f = floatfmt.lstrip(".")
    head = "| " + " | ".join([str(df.index.name or "")] + [str(c) for c in df.columns]) + " |"
    sep = "|" + "---|" * (len(df.columns) + 1)

    def cell(v: object) -> str:
        if isinstance(v, (float, np.floating)):
            return "" if not np.isfinite(v) else format(v, "." + f if not f.endswith("d") else "d")
        return str(v)
    body = ["| " + " | ".join([str(i)] + [cell(v) for v in r]) + " |" for i, r in zip(df.index, df.to_numpy())]
    return "\n".join([head, sep, *body])


# ------------------------------------------------------------------------------------------ DST regimes
def regimes(dates: pd.DatetimeIndex) -> pd.Series:
    """ss = UK and US both on summer time, ww = both on winter, gap = UK on GMT while the US is still on EDT
    (the weeks after the last Sunday of October and before the second Sunday of March)."""
    noon = (dates + pd.Timedelta(hours=12))
    uk = np.array([t.tz_localize("Europe/London").dst().total_seconds() > 0 for t in noon])
    us = np.array([t.tz_localize("America/New_York").dst().total_seconds() > 0 for t in noon])
    r = np.where(uk & us, "ss", np.where(~uk & ~us, "ww", np.where(us & ~uk, "gap", "gap_uk_only")))
    return pd.Series(r, index=dates)


def sec_dst(b: Bars) -> None:
    reg = regimes(b.dates)
    print("### DST regimes (weekdays)\n")
    print(md(reg.value_counts().rename("weekdays").to_frame(), "d"), "\n")
    # half-hour profile of mean |30m return| (bps) by UTC half hour and regime
    rows = {}
    for name in ("ss", "ww", "gap"):
        days = b.dates[reg.to_numpy() == name]
        t0 = (days.normalize().view("i8") if False else epoch_ns(pd.DatetimeIndex(days.tz_localize("UTC"))))
        prof = {}
        for k in range(12 * 2 + 1):          # 06:00 to 18:00 UTC in half hours
            a = t0 + (6 * 60 + 30 * k) * MIN
            prof[f"{(6 * 60 + 30 * k) // 60:02d}:{(30 * k) % 60:02d}"] = np.nanmean(np.abs(bps(b.px(a), b.px(a + 30 * MIN))))
        rows[name] = prof
    df = pd.DataFrame(rows)
    print("Mean |30-minute return| in bps by UTC half-hour start (sample period):\n")
    print(md(df), "\n")
    print("Same, divided by each regime's own mean (removes the level difference of the few gap weeks):\n")
    print(md(df / df.mean()), "\n")


# ------------------------------------------------------------------------------------------ hourly / tf profile
def sec_hourly(b: Bars) -> None:
    ts = pd.to_datetime(b.ts, unit="ns", utc=True)
    df = pd.DataFrame({"h": ts.hour, "rng": (b.h - b.l), "sp": b.sp, "yr": ts.year}, index=ts)
    recent = df[df.index >= str(pd.Timestamp(END, tz="UTC") - pd.Timedelta(days=365))]
    g = recent.groupby("h").agg(rng1m=("rng", "mean"), spd=("sp", "median"))
    print("### Hour of day, last 12 months of the sample: mean 1m range ($) and Dukascopy median spread ($, shape only)\n")
    print(md(g, ".3f"), "\n")


def sec_tf(b: Bars) -> None:
    """Mean bar range ($) and cost as a share of it, by session and bar size, last 12 months of the sample (UK/US local sessions)."""
    ts = pd.to_datetime(b.ts, unit="ns", utc=True)
    s = pd.DataFrame({"h": b.h, "l": b.l, "c": b.c}, index=ts)
    s = s[s.index >= str(pd.Timestamp(END, tz="UTC") - pd.Timedelta(days=365))]
    out = {}
    for tf in ("15min", "1h", "4h"):
        o = s.resample(tf).agg({"h": "max", "l": "min"}).dropna()
        o["rng"] = o.h - o.l
        idx = o.index
        lon = idx.tz_convert("Europe/London")
        ny = idx.tz_convert("America/New_York")
        lh = lon.hour + lon.minute / 60
        nh = ny.hour + ny.minute / 60
        sess = np.where((lh >= 8) & (nh < 8.5), "london", np.where((nh >= 8.5) & (nh < 17), "newyork",
                        np.where((idx.tz_convert("Asia/Tokyo").hour >= 8) & (lh < 8), "asia", "other")))
        r = o.groupby(sess).rng.mean()
        out[tf] = r
        out[f"{tf}_cost_pct"] = COST_C / r * 100
    print("### Mean bar range ($) by session and timeframe, last 12 months of the sample; cost_pct = $0.144 round trip / mean range\n")
    print(md(pd.DataFrame(out)), "\n")


# ------------------------------------------------------------------------------------------ event study
EVENTS = {   # name: (tz, hh, mm, kind)
    "Tokyo open 09:00 JST": ("Asia/Tokyo", 9, 0, "open"),
    "Shanghai AM auction 10:15 CST": ("Asia/Shanghai", 10, 15, "fix"),
    "London open 08:00": ("Europe/London", 8, 0, "open"),
    "LBMA AM fix 10:30 London": ("Europe/London", 10, 30, "fix"),
    "NY data / COMEX pit open 08:30 NY": ("America/New_York", 8, 30, "open"),
    "US equity open 09:30 NY": ("America/New_York", 9, 30, "open"),
    "LBMA PM fix 15:00 London": ("Europe/London", 15, 0, "fix"),
    "COMEX settlement 13:30 NY": ("America/New_York", 13, 30, "close"),
    "LSE close 16:30 London": ("Europe/London", 16, 30, "close"),
    "NY close / CME break 17:00 NY": ("America/New_York", 17, 0, "close"),
}


def event_stats(b: Bars, t: np.ndarray, kind: str) -> dict[str, float]:
    p = {k: b.px(t + k * MIN) for k in (-60, -30, -15, 0, 15, 30, 60, 90, 120)}
    pre30, post15, post60 = bps(p[-30], p[0]), bps(p[0], p[15]), bps(p[0], p[60])
    a15 = np.nanmean(np.abs(post15))
    pre15 = np.nanmean(np.abs(bps(p[-15], p[0])))
    if kind == "open":                        # first 30 min direction vs the next 60 min
        x, y = bps(p[0], p[30]), bps(p[30], p[90])
    else:                                     # last 30 min before the stamp vs the next 30 min
        x, y = pre30, bps(p[0], p[30])
    ok = np.isfinite(x) & np.isfinite(y)
    cont = np.sign(x[ok]) * y[ok]
    n, m, t_ = tstat(cont)
    return {"n": n, "abs15_after": a15, "abs15_before": pre15, "cont_bps": m, "cont_t": t_,
            "hit": float(np.mean(cont > 0)), "abs60_after": np.nanmean(np.abs(post60))}


def sec_events(b: Bars) -> None:
    rows = {}
    for name, (tz, hh, mm, kind) in EVENTS.items():
        t = local_ns(b.dates, tz, hh, mm)
        rows[name] = event_stats(b, t, kind) | {"kind": kind}
    df = pd.DataFrame(rows).T
    # randomised-time control: the same statistics at 400 random UTC minutes of day between 00:00 and 21:00
    base = epoch_ns(pd.DatetimeIndex(b.dates.tz_localize("UTC")))
    ctl = {"open": [], "fix": [], "close": []}
    for _ in range(400):
        t = base + int(RNG.integers(0, 21 * 60)) * MIN
        for k in ("open", "close"):
            ctl[k].append(event_stats(b, t, k))
    cdf = {k: pd.DataFrame(v) for k, v in ctl.items() if v}
    print("### Event study: 15-minute |return| after vs before, and direction persistence (bps; sample period)\n")
    print("cont_bps = mean of sign(x)*y: for opens x = first 30 min, y = next 60 min; for fixes/closes x = 30 min "
          "before the stamp, y = 30 min after (positive = continuation, negative = reversal).\n")
    print(md(df.drop(columns="kind").astype(float), ".3f"), "\n")
    c = pd.concat({k: v[["abs15_after", "abs15_before", "cont_bps", "cont_t", "hit"]].describe().loc[["mean", "std", "min", "max"]]
                   for k, v in cdf.items()})
    print("Randomised-time control (400 random UTC minutes, 00:00-21:00; 'open' form and 'close' form):\n")
    print(md(c, ".3f"), "\n")
    for name, row in df.iterrows():
        k = "open" if row["kind"] == "open" else "close"
        d = cdf[k]
        pct = float((d.cont_bps < row.cont_bps).mean() * 100)
        pv = float((d.abs15_after < row.abs15_after).mean() * 100)
        print(f"- {name}: cont percentile vs control {pct:.0f}; abs15 percentile {pv:.0f}")
    print()


# ------------------------------------------------------------------------------------------ Asia range -> London
def asia_london(b: Bars, t_open: np.ndarray, look_min: int, hold: int, tol_bps: float = 0.0) -> dict[str, float]:
    """Range = high/low of [t_open - look_min, t_open). From t_open on 15m grid, the first 15m close beyond the range
    (within 180 min) is the signal; enter at that close. breakout = trade with it, fade = against it, hold minutes."""
    br, fr = [], []
    for T in t_open:
        hi, lo, _ = b.hl(T - look_min * MIN, T)
        if not np.isfinite(hi):
            continue
        for k in range(1, 13):
            e = T + 15 * k * MIN
            cl = b.px(np.array([e]))[0]
            if not np.isfinite(cl):
                continue
            side = 1 if cl > hi else (-1 if cl < lo else 0)
            if side:
                x = b.px(np.array([e + hold * MIN]))[0]
                if np.isfinite(x):
                    br.append(side * bps(cl, x) - 0.0)
                    # fade after a failed break: close back inside the range within 2 bars of the break
                    back = b.px(np.array([e + 30 * MIN]))[0]
                    if np.isfinite(back) and lo <= back <= hi:
                        fr.append(-side * bps(back, b.px(np.array([e + 30 * MIN + hold * MIN]))[0]))
                break
    br_a, fr_a = np.array(br), np.array(fr)
    n, m, t_ = tstat(br_a)
    n2, m2, t2 = tstat(fr_a)
    return {"n_break": n, "break_bps": m, "break_t": t_, "n_fail": n2, "failfade_bps": m2, "failfade_t": t2}


def sec_asia(b: Bars) -> None:
    lon = local_ns(b.dates, "Europe/London", 8, 0)
    print("### Asia range then London: breakout vs fade (Asia = Tokyo 08:00 to London 08:00, i.e. 8-9 h lookback)\n")
    print("break_bps = mean signed return (bps, gross) of trading WITH the first 15m close beyond the range, held `hold` "
          "minutes; failfade = after the break closes back inside the range 30 min later, fade it from there. "
          "Negative break_bps = the fade is the profitable side. 1 bp is ~$0.40 at current price, cost ~0.36 bp.\n")
    rows = {}
    tok = local_ns(b.dates, "Asia/Tokyo", 8, 0)
    for hold in (60, 120, 240):
        r = asia_london(b, lon, 540, hold)   # 9 h covers Tokyo 08:00 to London 08:00 in the widest case
        rows[f"London open, hold {hold}m"] = r
    base = epoch_ns(pd.DatetimeIndex(b.dates.tz_localize("UTC")))
    ctl = []
    for _ in range(60):
        T = base + int(RNG.integers(6 * 60, 15 * 60)) * MIN
        ctl.append(asia_london(b, T, 540, 120))
    cdf = pd.DataFrame(ctl)
    print(md(pd.DataFrame(rows).T, ".3f"), "\n")
    print("Control (60 random start minutes 06:00-15:00 UTC, same 9 h lookback, hold 120m): "
          f"break_bps mean {cdf.break_bps.mean():.3f}, sd {cdf.break_bps.std():.3f}, min {cdf.break_bps.min():.3f}, "
          f"max {cdf.break_bps.max():.3f}; failfade mean {cdf.failfade_bps.mean():.3f}, sd {cdf.failfade_bps.std():.3f}\n")
    _ = tok


# ------------------------------------------------------------------------------------------ Gao-Han-Li-Zhou
def gao(b: Bars, t_open: np.ndarray, t_last: np.ndarray) -> dict[str, float]:
    """r_first = [open, open+30m], r_12 = [last-60m, last-30m], r_last = [last-30m, last]; last given as the END stamp."""
    r1 = bps(b.px(t_open), b.px(t_open + 30 * MIN))
    r12 = bps(b.px(t_last - 60 * MIN), b.px(t_last - 30 * MIN))
    rl = bps(b.px(t_last - 30 * MIN), b.px(t_last))
    ok = np.isfinite(r1) & np.isfinite(rl) & np.isfinite(r12)
    r1, r12, rl = r1[ok], r12[ok], rl[ok]
    n = len(r1)
    X = np.column_stack([np.ones(n), r1])
    beta, *_ = np.linalg.lstsq(X, rl, rcond=None)
    res = rl - X @ beta
    cov = np.linalg.inv(X.T @ X) @ (X.T * res ** 2) @ X @ np.linalg.inv(X.T @ X)   # White
    X2 = np.column_stack([np.ones(n), r1, r12])
    b2, *_ = np.linalg.lstsq(X2, rl, rcond=None)
    res2 = rl - X2 @ b2
    cov2 = np.linalg.inv(X2.T @ X2) @ (X2.T * res2 ** 2) @ X2 @ np.linalg.inv(X2.T @ X2)
    cont = np.sign(r1) * rl
    return {"n": n, "beta_first": beta[1], "t_first": beta[1] / np.sqrt(cov[1, 1]), "beta_first_m": b2[1],
            "t_first_m": b2[1] / np.sqrt(cov2[1, 1]), "beta_12": b2[2], "t_12": b2[2] / np.sqrt(cov2[2, 2]),
            "R2_pct": 100 * (1 - res.var() / rl.var()), "sign_hit": float(np.mean(cont > 0)),
            "sign_pnl_bps": float(cont.mean())}


def sec_gao(b: Bars) -> None:
    pairs = {
        "London open 08:00 -> LSE close 16:30": (("Europe/London", 8, 0), ("Europe/London", 16, 30)),
        "NY 08:30 -> COMEX settle 13:30": (("America/New_York", 8, 30), ("America/New_York", 13, 30)),
        "NY 08:30 -> NY close 17:00": (("America/New_York", 8, 30), ("America/New_York", 17, 0)),
        "US equity open 09:30 -> equity close 16:00": (("America/New_York", 9, 30), ("America/New_York", 16, 0)),
        "Tokyo 09:00 -> Tokyo-session end 15:00 JST": (("Asia/Tokyo", 9, 0), ("Asia/Tokyo", 15, 0)),
        "Tokyo 09:00 -> London open 08:00": (("Asia/Tokyo", 9, 0), ("Europe/London", 8, 0)),
    }
    rows = {}
    for name, (o, c) in pairs.items():
        rows[name] = gao(b, local_ns(b.dates, *o), local_ns(b.dates, *c))
    base = epoch_ns(pd.DatetimeIndex(b.dates.tz_localize("UTC")))
    ctl = []
    for _ in range(400):    # random open, random span 4-9 h, same last-30 form
        o = base + int(RNG.integers(0, 12 * 60)) * MIN
        ctl.append(gao(b, o, o + int(RNG.integers(240, 540)) * MIN))
    cdf = pd.DataFrame(ctl)
    print("### First-half-hour momentum predicting the last half-hour (Gao-Han-Li-Zhou form), sample period, bps\n")
    print("beta_first: OLS of r_last on r_first (White t). *_m: with the penultimate half-hour r_12 as well. "
          "sign_pnl_bps = mean of sign(r_first)*r_last gross (cost ~0.36 bp).\n")
    print(md(pd.DataFrame(rows).T, ".3f"), "\n")
    print("Randomised control (400 random opens, random 4-9 h spans): beta_first mean "
          f"{cdf.beta_first.mean():.3f}, sd {cdf.beta_first.std():.3f}, 2.5%/97.5% "
          f"{cdf.beta_first.quantile(.025):.3f}/{cdf.beta_first.quantile(.975):.3f}; |t_first| > 2 in "
          f"{(cdf.t_first.abs() > 2).mean() * 100:.0f}% of draws; sign_pnl mean {cdf.sign_pnl_bps.mean():.4f}, "
          f"sd {cdf.sign_pnl_bps.std():.4f}\n")


# ------------------------------------------------------------------------------------------ overnight vs intraday
def sec_decomp(b: Bars) -> None:
    d = b.dates
    tok = local_ns(d, "Asia/Tokyo", 8, 0) + 5 * MIN     # +5 min: on UK/US winter time the feed is still in its break at 08:00 JST
    lon = local_ns(d, "Europe/London", 8, 0)
    nyo = local_ns(d, "America/New_York", 8, 30)
    nyc = local_ns(d, "America/New_York", 17, 0)
    seg = {
        "Asia (Tokyo 08:00 -> London 08:00)": bps(b.px(tok), b.px(lon)),
        "London (08:00 -> NY 08:30)": bps(b.px(lon), b.px(nyo)),
        "NY (08:30 -> 17:00 NY)": bps(b.px(nyo), b.px(nyc)),
    }
    nxt_tok = np.append(tok[1:], tok[-1] + 24 * 60 * MIN)
    gapseg = bps(b.px(nyc), b.px(nxt_tok))            # NY close to the next Tokyo open (weekend for Fridays)
    seg["NY close -> next Tokyo 08:00 (incl. weekend on Fri)"] = gapseg
    seg["Overnight: NY close -> NY 08:30 next day"] = bps(b.px(nyc[:-1]), b.px(nyo[1:])).tolist() + [np.nan] if False else \
        np.append(bps(b.px(nyc[:-1]), b.px(nyo[1:])), np.nan)
    seg["Intraday: NY 08:30 -> 17:00"] = seg["NY (08:30 -> 17:00 NY)"]
    yr = d.year.to_numpy()
    rows = {}
    for k, v in seg.items():
        n, m, t = tstat(v)
        rows[k] = {"n": n, "mean_bps": m, "t": t, "sd_bps": np.nanstd(v), "hit_up": float(np.mean(v[np.isfinite(v)] > 0)),
                   **{str(y): np.nanmean(v[yr == y]) for y in range(int(START[:4]), int(END[:4]) + (END[5:7] != "01" or END[8:] != "01"))}}
    print("### Return decomposition by segment (mid, bps, Mon-Fri local dates, sample period)\n")
    print(md(pd.DataFrame(rows).T, ".2f"), "\n")
    # randomised-time control for the drift: mean return of random windows of the same length as each segment
    base = epoch_ns(pd.DatetimeIndex(d.tz_localize("UTC")))
    ctl_rows = {}
    for name, dur in {"Asia-length 9.0 h": 540, "London-length 4.5 h": 270, "NY-length 8.5 h": 510, "gap-length 6 h": 360}.items():
        m = []
        for _ in range(300):
            T = base + int(RNG.integers(0, 24 * 60)) * MIN
            m.append(np.nanmean(bps(b.px(T), b.px(T + dur * MIN))))
        m = np.array(m)
        ctl_rows[name] = {"ctl_mean_bps": m.mean(), "ctl_sd_across_draws": m.std(), "ctl_p2.5": np.quantile(m, .025),
                          "ctl_p97.5": np.quantile(m, .975)}
    print("Randomised-time control: mean return (bps) of 300 random windows of the segment's length (random start minute, "
          "same weekdays). Tells how much of each segment mean is just gold's 2020-26 uptrend:\n")
    print(md(pd.DataFrame(ctl_rows).T, ".2f"), "\n")
    # correlation overnight vs next intraday (Lou-Polk-Skouras tug of war)
    ov = seg["Overnight: NY close -> NY 08:30 next day"]
    # overnight to date i+1 is (nyc[i] -> nyo[i+1]) stored at index i; pair with intraday of day i+1
    intr = seg["Intraday: NY 08:30 -> 17:00"][1:]
    o2 = ov[:-1]
    ok = np.isfinite(o2) & np.isfinite(intr)
    c = np.corrcoef(o2[ok], intr[ok])[0, 1]
    # reversal trade: fade overnight sign with the NY session, gross bps
    pnl = -np.sign(o2[ok]) * intr[ok]
    n, m, t = tstat(pnl)
    print(f"Overnight (NY close to NY 08:30) vs the next NY 08:30-17:00 return: corr {c:.3f}, n {ok.sum()}; fading the "
          f"overnight sign through the NY session pays {m:.3f} bps gross (t {t:.2f}); cost ~0.36 bp.\n")


# ------------------------------------------------------------------------------------------ opening range breakout
def orb_trades(b: Bars, t_open: np.ndarray, win: int, stop_mode: str, exit_mode: str, hold_min: int = 240,
               cap_bps: float = 60.0) -> np.ndarray:
    """One trade per session. Range = [open, open+win). First 15m-grid close (>= end of range) beyond the range within
    120 min is the signal; enter at that close (price known at the stamp). Stop = opposite side (stop_mode 'opp') or
    range mid ('mid'); exit = 2R target ('t2'), or time exit after hold_min ('time'); stop first when a bar spans both.
    Returns net-of-central-cost R (cost = COST_C / risk$)."""
    out = []
    for T in t_open:
        hi, lo, _ = b.hl(T, T + win * MIN)
        if not np.isfinite(hi) or hi <= lo:
            continue
        for k in range(0, 120 // 15 + 1):
            e = T + (win + 15 * k) * MIN
            cl = b.px(np.array([e]))[0]
            if not np.isfinite(cl):
                continue
            side = 1 if cl > hi else (-1 if cl < lo else 0)
            if not side:
                continue
            stop = lo if (side == 1 and stop_mode == "opp") else hi if (side == -1 and stop_mode == "opp") else (hi + lo) / 2
            risk = abs(cl - stop)
            if risk < 1.0 or risk / cl * 1e4 > cap_bps:
                break
            a = np.searchsorted(b.ts, e)                 # bars starting at/after the entry stamp
            z = np.searchsorted(b.ts, e + hold_min * MIN)
            if z - a < 5:
                break
            H, L, C = b.h[a:z], b.l[a:z], b.c[a:z]
            if side == 1:
                hit_s, hit_t = L <= stop, H >= cl + 2 * risk
            else:
                hit_s, hit_t = H >= stop, L <= cl - 2 * risk
            si = np.argmax(hit_s) if hit_s.any() else 10 ** 9
            ti = np.argmax(hit_t) if (exit_mode == "t2" and hit_t.any()) else 10 ** 9
            if si == 10 ** 9 and ti == 10 ** 9:
                r = side * (C[-1] - cl) / risk
            elif si <= ti:
                r = -1.0
            else:
                r = 2.0
            out.append(r - COST_C / risk)
            break
    return np.array(out)


def sec_orb(b: Bars) -> None:
    sessions = {"London 08:00": ("Europe/London", 8, 0), "NY 08:30": ("America/New_York", 8, 30)}
    base = epoch_ns(pd.DatetimeIndex(b.dates.tz_localize("UTC")))
    rows, crows = {}, []
    for sname, (tz, hh, mm) in sessions.items():
        t_open = local_ns(b.dates, tz, hh, mm)
        for win in (15, 30, 60):
            for sm in ("opp", "mid"):
                for em in ("t2", "time"):
                    r = orb_trades(b, t_open, win, sm, em)
                    n, m, t = tstat(r)
                    # control: same rule from random minutes of the 06:00-15:00 UTC window
                    cm = []
                    for _ in range(40):
                        T = base + int(RNG.integers(6 * 60, 15 * 60)) * MIN
                        cr = orb_trades(b, T, win, sm, em)
                        cm.append(cr.mean() if len(cr) > 50 else np.nan)
                    cm = np.array(cm)
                    rows[f"{sname} win{win} stop-{sm} exit-{em}"] = {
                        "n": n, "netR": m, "t": t, "n_per_mo": n / (len(b.dates) / 21.7),
                        "ctl_mean": np.nanmean(cm), "ctl_sd": np.nanstd(cm), "pct_vs_ctl": float(np.nanmean(cm < m) * 100)}
    print("### Opening-range breakout variants, net of the central measured cost ($0.144), sample period\n")
    print("netR = mean R per trade after subtracting $0.144 / risk$; costs charged at the entry price only. "
          "pct_vs_ctl = share of 40 random-start controls (same rule) with a lower mean netR.\n")
    print(md(pd.DataFrame(rows).T, ".3f"), "\n")
    _ = crows


# ------------------------------------------------------------------------------------------ spread near the close
def sec_spread(b: Bars) -> None:
    ts = pd.to_datetime(b.ts, unit="ns", utc=True)
    s = pd.Series(b.sp, index=ts)
    s = s[s.index >= str(pd.Timestamp(END, tz="UTC") - pd.Timedelta(days=365))]
    ny = s.index.tz_convert("America/New_York")
    mins = (ny.hour * 60 + ny.minute).to_numpy()
    med = s[(mins >= 9 * 60) & (mins < 16 * 60)].median()
    rows = {}
    for lab, a, z in [("NY 16:00-16:30", 960, 990), ("16:30-16:55", 990, 1015), ("16:55-17:00", 1015, 1020),
                      ("18:00-18:05 (Globex reopen)", 1080, 1085), ("18:05-18:30", 1085, 1110), ("18:30-19:00", 1110, 1140), ("19:00-20:00", 1140, 1200),
                      ("Tokyo 09:00-09:15 JST", None, None)]:
        if a is None:
            tj = s.index.tz_convert("Asia/Tokyo")
            m = (tj.hour * 60 + tj.minute).to_numpy()
            x = s[(m >= 540) & (m < 555)]
        else:
            x = s[(mins >= a) & (mins < z)]
        rows[lab] = {"n_min": len(x), "median_spread": x.median(), "ratio_to_NY_day": x.median() / med}
    print("### Spread around the NY close (Dukascopy, last 12 months of the sample; RELATIVE shape only, broker spread is ~8x tighter)\n")
    print(f"Reference: median spread 09:00-16:00 NY = ${med:.3f}\n")
    print(md(pd.DataFrame(rows).T, ".3f"), "\n")


SECTIONS = {"dst": sec_dst, "hourly": sec_hourly, "tf": sec_tf, "events": sec_events, "asia": sec_asia,
            "gao": sec_gao, "decomp": sec_decomp, "orb": sec_orb, "spread": sec_spread}


def main() -> None:
    folder = Path(sys.argv[1])
    want = sys.argv[2:] or list(SECTIONS)
    b = Bars(folder)
    print(f"bars {len(b.ts):,} from {pd.Timestamp(b.ts[0])} to {pd.Timestamp(b.ts[-1])}; weekdays {len(b.dates)}\n")
    for k in want:
        SECTIONS[k](b)


if __name__ == "__main__":
    main()
