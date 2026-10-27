"""Combinations diagnostics for docs/research/time-structure/combinations.md (slice H, 2026-10-26).

Read-only, descriptive, NO trial and NO strategy backtest job. Data: Dukascopy 1m bars (release data-v1), mid prices.

Discipline (written before the first run):
* Outcomes are read on the DISCOVERY half 2010-01-01 .. 2017-12-31 only. Files for 2018+ are never opened.
* A bar is usable only after it closes (`available_utc` = last constituent minute + 1 min). A signal on bar i enters at
  the OPEN of bar i+1. Higher-timeframe context joins through `goldbot.data.store.asof_join` on `available_utc`.
* Outcome unit R = 1 ATR(14) of the signal bar of the stream's decision timeframe (fixed-horizon exit, no stop model).
* Costs (two cases, both labelled in the report; a cost audit is running):
    prior  = research prior, 0.75 $/oz round trip in 2010-17 dollars (spread + 0.37; cycles.md 2) divided by the
             signal bar's ATR in dollars;
    broker = measured-fill, 0.144 $/oz (spread ~0.10 + commission) converted at TODAY's median ATR per timeframe
             (as volatility-bands.md): 15m 8.0, 1h 17.7, 4h 36.6, 1d 87.6 $/oz.
* Part 1 (confluence): the base trade set is the F-1 rule (volatility-bands.md 9): union of Keltner/Bollinger/Donchian-20
  breakouts on 15m, traded WITH the break when Hurst128 is in its low tercile and AGAINST it when high. Filters are
  defined from mechanism BEFORE running (see FILTERS); every cell looked at is counted (printed as LOOKS).
* Part 2 (portfolio): ten stylised streams (STREAMS) from finished slice candidates and the exploration-arm families,
  daily R series, correlations, equal-risk / inverse-variance books, allocator and walk-forward switching, and a
  sign-randomised null book.
* Part 3 (meta-labelling): ridge logistic on <= 12 pre-listed features, year-by-year walk-forward, label-permutation null.

Usage: uv run python -I docs/research/candidates/time_structure/combinations_diag.py --raw ~/tmp/ts/raw --out ~/tmp/ts/comb-out
"""
from __future__ import annotations

import argparse
import itertools
import json
from math import erf, sqrt
from pathlib import Path

import numpy as np
import pandas as pd

from goldbot.allocator.rules import Regime, RuleAllocator
from goldbot.data.store import asof_join
from goldbot.data.timeutil import epoch_ns
from goldbot.features.survey import rolling_hurst
from goldbot.features.technical import adx, atr, rsi

DISC_START = pd.Timestamp("2010-01-01", tz="UTC")
DISC_END = pd.Timestamp("2018-01-01", tz="UTC")
NY, LDN, TKY = "America/New_York", "Europe/London", "Asia/Tokyo"
CUR_ATR = {"15m": 8.0, "1h": 17.7, "4h": 36.6, "1d": 87.6}
COST_PRIOR, COST_BROKER = 0.75, 0.144
MONTHS = 96.0
SEED = 20261026
PERM = 1000
LOOKS: list[str] = []          # every cell / statistic looked at on gold, counted for K


def look(name: str, n: int = 1) -> None:
    LOOKS.extend([name] * n)


# ------------------------------------------------------------------ data (same construction as volatility_bands_diag)
def load(raw: Path) -> pd.DataFrame:
    parts = []
    for y in range(2010, 2018):          # 2018+ files are never opened
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
    return (idx.tz_convert(NY) + pd.Timedelta(hours=7)).normalize().tz_localize(None)


def bars(m1: pd.DataFrame, rule: str | None) -> pd.DataFrame:
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
    b = b.dropna(subset=["close"]).rename(columns={"t_end": "available_utc"})
    b["available_utc"] = pd.to_datetime(b["available_utc"], utc=True)
    b.index.name = "ts_utc"
    return b


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def keltner(b: pd.DataFrame, a: pd.Series, n: int = 20, k: float = 2.0):
    m = ema(b["close"], n)
    return m, m + k * a, m - k * a


def bollinger(b: pd.DataFrame, n: int = 20, k: float = 2.0):
    m = b["close"].rolling(n, min_periods=n).mean()
    s = b["close"].rolling(n, min_periods=n).std(ddof=0)
    return m, m + k * s, m - k * s


def donchian(b: pd.DataFrame, n: int):
    hi, lo = b["high"].rolling(n).max().shift(1), b["low"].rolling(n).min().shift(1)
    return (hi + lo) / 2, hi, lo


def vwap_band(b: pd.DataFrame, k: float = 2.0, min_bars: int = 6):
    day = pd.Series(trading_day(b.index), index=b.index).to_numpy()
    v = b["tick_count"].replace(0, np.nan).fillna(1.0)
    tp = (b["high"] + b["low"] + b["close"]) / 3
    sv, spv, spv2 = v.groupby(day).cumsum(), (tp * v).groupby(day).cumsum(), (tp ** 2 * v).groupby(day).cumsum()
    vw = spv / sv
    sd = np.sqrt(np.maximum(spv2 / sv - vw ** 2, 0.0))
    ok = pd.Series(1, index=b.index).groupby(day).cumsum() >= min_bars
    return vw.where(ok), (vw + k * sd).where(ok), (vw - k * sd).where(ok)


def breaks(c: pd.Series, up: pd.Series, lo: pd.Series) -> np.ndarray:
    """+1 first close above the upper band, -1 first close below the lower band (from inside), else 0."""
    c1, up1, lo1 = c.shift(), up.shift(), lo.shift()
    valid = up.notna() & lo.notna()
    return np.where((c > up) & (c1 <= up1) & valid, 1, np.where((c < lo) & (c1 >= lo1) & valid, -1, 0))


def dedup(idx: np.ndarray, gap: int) -> np.ndarray:
    keep, last = [], -10 ** 9
    for i in idx:
        if i - last >= gap:
            keep.append(int(i))
            last = i
    return np.array(keep, dtype=int)


def terciles(x: pd.Series) -> np.ndarray:
    r = x.rolling(250, min_periods=100).rank(pct=True)
    return np.select([r < 1 / 3, r < 2 / 3, r >= 2 / 3], ["low", "mid", "high"], "na")


def pnorm_two(z: float) -> float:
    return 1 - erf(abs(z) / sqrt(2)) if np.isfinite(z) else float("nan")


def session_bucket(h: np.ndarray) -> np.ndarray:
    return np.select([(h >= 22) | (h < 7), h < 12, h < 17], ["Asia", "London", "NY"], "Late")


# ------------------------------------------------------------------ context frames
class Ctx:
    pass


def build_ctx(m1: pd.DataFrame) -> Ctx:
    x = Ctx()
    x.b15, x.b1h, x.b4h, x.b1d = bars(m1, "15min"), bars(m1, "1h"), bars(m1, "4h"), bars(m1, None)
    b = x.b15
    x.a = atr(b, 14)
    x.av = pd.DatetimeIndex(b["available_utc"])
    x.n = len(b)
    x.fwd4 = ((b["close"].shift(-4) - b["open"].shift(-1)) / x.a).to_numpy()
    x.dkey = np.asarray(epoch_ns(pd.DatetimeIndex(trading_day(b.index)).tz_localize("UTC")))
    x.years = b.index.year.to_numpy()
    ret = np.log(b["close"]).diff().to_numpy()
    x.hurst = pd.Series(rolling_hurst(np.nan_to_num(ret)), index=b.index)
    x.hl = terciles(x.hurst)
    x.adx15 = adx(b, 14).to_numpy()
    x.rsi15 = rsi(b["close"], 14).to_numpy()
    x.atr_pct = (x.a.rolling(250, min_periods=100).rank(pct=True)).to_numpy()
    sma = b["close"].rolling(20).mean()
    sd = b["close"].rolling(20).std(ddof=0)
    x.bbz = ((b["close"] - sma) / sd).to_numpy()
    x.ret4 = (np.log(b["close"]).diff(4) / (x.a / b["close"])).to_numpy()
    # higher-timeframe Keltner positions via asof_join on available_utc
    j = pd.DataFrame({"ts_utc": x.av}).reset_index(drop=True)
    for name, hb in (("4h", x.b4h), ("1d", x.b1d)):
        ah = atr(hb, 14)
        m, u, _ = keltner(hb, ah)
        h = pd.DataFrame({"available_utc": pd.DatetimeIndex(hb["available_utc"]), f"kc{name}": ((hb["close"] - m) / (u - m)).to_numpy()})
        x.__dict__[f"kc{name}"] = asof_join(j, h, on="ts_utc", available_col="available_utc")[f"kc{name}"].to_numpy()
    # 1h ATR (for 4.5 h holds) and 1h ADX, ATR quartile (allocator)
    a1 = atr(x.b1h, 14)
    h1 = pd.DataFrame({"available_utc": pd.DatetimeIndex(x.b1h["available_utc"]), "atr1h": a1.to_numpy(),
                       "adx1h": adx(x.b1h, 14).to_numpy(),
                       "atrq": (a1.rolling(60 * 24, min_periods=24 * 20).rank(pct=True)).to_numpy()})
    jj = asof_join(j, h1, on="ts_utc", available_col="available_utc")
    x.atr1h, x.adx1h, x.atrq = jj["atr1h"].to_numpy(), jj["adx1h"].to_numpy(), jj["atrq"].to_numpy()
    # prior-day extremes (trading-day, known at the day's end): PDH/PDL joined on available_utc
    hb = x.b1d
    pd_ = pd.DataFrame({"available_utc": pd.DatetimeIndex(hb["available_utc"]), "pdh": hb["high"].to_numpy(), "pdl": hb["low"].to_numpy()})
    jp = asof_join(j, pd_, on="ts_utc", available_col="available_utc")
    x.pdh, x.pdl = jp["pdh"].to_numpy(), jp["pdl"].to_numpy()
    # clocks
    loc = x.av.tz_convert(NY)
    x.ny_min = (loc.hour * 60 + loc.minute).to_numpy()
    x.hour_utc = x.av.hour.to_numpy()
    x.sess = session_bucket(x.hour_utc)
    x.close = b["close"].to_numpy()
    return x


# ------------------------------------------------------------------ streams (each returns a trade frame)
def mk(x: Ctx, name: str, tf: str, idx: np.ndarray, dirs: np.ndarray, f: np.ndarray, atr_usd: np.ndarray,
       tkey: np.ndarray | None = None, av: pd.DatetimeIndex | None = None) -> pd.DataFrame:
    idx = np.asarray(idx, int)
    d = np.asarray(dirs, float)
    fr = np.asarray(f, float)
    ok = np.isfinite(fr) & np.isfinite(atr_usd)
    idx, d, fr, au = idx[ok], d[ok], fr[ok], np.asarray(atr_usd)[ok]
    gross = d * fr
    dk = (x.dkey[idx] if tkey is None else np.asarray(tkey)[ok])
    avs = (x.av[idx] if av is None else av[ok])
    return pd.DataFrame({"stream": name, "tf": tf, "idx": idx, "dir": d, "f": fr, "gross": gross,
                         "atr_usd": au, "cost_prior": COST_PRIOR / au, "cost_broker": COST_BROKER / CUR_ATR[tf],
                         "day": dk, "year": pd.DatetimeIndex(avs).year.to_numpy(), "sess": session_bucket(pd.DatetimeIndex(avs).hour.to_numpy()),
                         "avail": pd.DatetimeIndex(avs)})


def union_dir(x: Ctx) -> np.ndarray:
    b = x.b15
    ds = [breaks(b["close"], *keltner(b, x.a)[1:]), breaks(b["close"], *bollinger(b)[1:]), breaks(b["close"], *donchian(b, 20)[1:])]
    return np.sign(sum(ds)).astype(int)


def f1_dir(x: Ctx, du: np.ndarray) -> np.ndarray:
    return np.where(x.hl == "low", du, np.where(x.hl == "high", -du, 0))


def stream_F1(x, du):
    d = f1_dir(x, du)
    idx = dedup(np.flatnonzero((d != 0) & np.isfinite(x.fwd4)), 4)
    return mk(x, "F1", "15m", idx, d[idx], x.fwd4[idx], x.a.to_numpy()[idx])


def stream_F2(x, du):
    cond = (np.abs(x.kc4h) >= 1) & (np.sign(x.kc4h) == -du) & (du != 0)
    d = np.where(cond, -du, 0)
    idx = dedup(np.flatnonzero((d != 0) & np.isfinite(x.fwd4)), 4)
    return mk(x, "F2", "15m", idx, d[idx], x.fwd4[idx], x.a.to_numpy()[idx])


def stream_F3(x):
    b = x.b4h
    a = atr(b, 14)
    d = breaks(b["close"], *donchian(b, 55)[1:])
    f = ((b["close"].shift(-4) - b["open"].shift(-1)) / a).to_numpy()
    idx = dedup(np.flatnonzero((d != 0) & np.isfinite(f)), 4)
    dk = np.asarray(epoch_ns(pd.DatetimeIndex(trading_day(b.index)).tz_localize("UTC")))
    return mk(x, "F3", "4h", idx, d[idx], f[idx], a.to_numpy()[idx], dk[idx], pd.DatetimeIndex(b["available_utc"])[idx])


def stream_V1(x):
    b = x.b15
    d = -breaks(b["close"], *vwap_band(b, 2.0, 6)[1:])
    idx = dedup(np.flatnonzero((d != 0) & np.isfinite(x.fwd4)), 4)
    return mk(x, "V1", "15m", idx, d[idx], x.fwd4[idx], x.a.to_numpy()[idx])


def stream_E1(x):
    b = x.b15
    e20, e50 = ema(b["close"], 20), ema(b["close"], 50)
    up = (e20 > e50) & (b["close"] > e50)
    dn = (e20 < e50) & (b["close"] < e50)
    lg = up & (b["low"] <= e20) & (b["close"] > e20) & (b["close"] > b["open"])
    sh = dn & (b["high"] >= e20) & (b["close"] < e20) & (b["close"] < b["open"])
    d = np.where(lg, 1, np.where(sh, -1, 0))
    idx = dedup(np.flatnonzero((d != 0) & np.isfinite(x.fwd4)), 4)
    return mk(x, "E1", "15m", idx, d[idx], x.fwd4[idx], x.a.to_numpy()[idx])


def _local_groups(x: Ctx, tz: str):
    loc = x.av.tz_convert(tz)
    return loc


def stream_O1(x):
    """London 30-min opening range (first two 15m bars from 08:00 London), first close beyond it within 8 bars, hold 16."""
    b = x.b15
    start = b.index.tz_convert(LDN)
    mins = (start.hour * 60 + start.minute).to_numpy()
    date = start.normalize().tz_localize(None)
    hi, lo, cl = b["high"].to_numpy(), b["low"].to_numpy(), b["close"].to_numpy()
    f16 = ((b["close"].shift(-16) - b["open"].shift(-1)) / x.a).to_numpy()
    rows_i, rows_d = [], []
    pos = pd.Series(np.arange(len(b)), index=date)
    for dte, g in pos.groupby(level=0):
        ii = g.to_numpy()
        m = mins[ii]
        if pd.Timestamp(dte).dayofweek >= 5:
            continue
        sel = ii[(m >= 480) & (m < 510)]
        if len(sel) != 2:
            continue
        rh, rl = hi[sel].max(), lo[sel].min()
        for j in range(sel[-1] + 1, min(sel[-1] + 9, len(b))):
            if cl[j] > rh:
                rows_i.append(j)
                rows_d.append(1)
                break
            if cl[j] < rl:
                rows_i.append(j)
                rows_d.append(-1)
                break
    idx = np.array(rows_i, int)
    return mk(x, "O1", "15m", idx, np.array(rows_d), f16[idx], x.a.to_numpy()[idx])


def stream_A1(x):
    """Failed Asia-range break fade (sessions.md A-1): Tokyo 08:00 -> London 08:00 range; first 15m close beyond it in
    London 08:00-11:00; if the close 30 min later is back inside, fade it (enter next open), hold 8 bars."""
    b = x.b15
    ts = b.index
    hi, lo, cl = b["high"].to_numpy(), b["low"].to_numpy(), b["close"].to_numpy()
    f8 = ((b["close"].shift(-8) - b["open"].shift(-1)) / x.a).to_numpy()
    ldn = ts.tz_convert(LDN)
    days = pd.DatetimeIndex(sorted(set(ldn.normalize().tz_localize(None))))
    tsv = ts.asi8 if False else np.asarray(epoch_ns(ts))
    rows_i, rows_d = [], []
    for dte in days:
        if dte.dayofweek >= 5:
            continue
        t_end = (pd.Timestamp(dte) + pd.Timedelta(hours=8)).tz_localize(LDN).tz_convert("UTC").value
        t_st = (pd.Timestamp(dte) + pd.Timedelta(hours=8)).tz_localize(TKY).tz_convert("UTC").value
        i0, i1 = np.searchsorted(tsv, t_st), np.searchsorted(tsv, t_end)
        if i1 - i0 < 20:
            continue
        rh, rl = hi[i0:i1].max(), lo[i0:i1].min()
        for j in range(i1, min(i1 + 12, len(b) - 4)):
            side = 1 if cl[j] > rh else (-1 if cl[j] < rl else 0)
            if side:
                k = j + 2
                if rl <= cl[k] <= rh:
                    rows_i.append(k)
                    rows_d.append(-side)
                break
    idx = np.array(rows_i, int)
    return mk(x, "A1", "15m", idx, np.array(rows_d), f8[idx], x.a.to_numpy()[idx])


def stream_A2(x):
    """NY pit continuation (sessions.md A-2): sign of r(08:30-09:00 NY), enter 09:00 NY open, exit close of the bar
    starting 13:15 NY (13:30). Unit = 1h ATR known at the signal."""
    b = x.b15
    start = b.index.tz_convert(NY)
    mins = (start.hour * 60 + start.minute).to_numpy()
    date = start.normalize().tz_localize(None)
    o, c = b["open"].to_numpy(), b["close"].to_numpy()
    rows = []
    pos = pd.Series(np.arange(len(b)), index=date)
    for dte, g in pos.groupby(level=0):
        if pd.Timestamp(dte).dayofweek >= 5:
            continue
        ii = g.to_numpy()
        m = mins[ii]
        s0 = ii[m == 510]
        s1 = ii[m == 525]
        s2 = ii[m == 540]
        sx = ii[m == 795]
        if len(s0) and len(s1) and len(s2) and len(sx) and s1[0] == s0[0] + 1 and s2[0] == s1[0] + 1:
            r = c[s1[0]] - o[s0[0]]
            if r != 0 and np.isfinite(x.atr1h[s1[0]]):
                rows.append((s1[0], np.sign(r), (c[sx[0]] - o[s2[0]]) / x.atr1h[s1[0]], x.atr1h[s1[0]]))
    r = np.array(rows)
    return mk(x, "A2", "1h", r[:, 0].astype(int), r[:, 1], r[:, 2], r[:, 3])


def stream_B1(x):
    """Friday long (cycles.md B-1): trading-day bar of a Friday, open -> close, over the lagged 1d ATR."""
    b = x.b1d
    a = atr(b, 14).shift(1)
    key = pd.DatetimeIndex(trading_day(b.index))
    fri = np.flatnonzero(key.dayofweek == 4)
    f = ((b["close"] - b["open"]) / a).to_numpy()
    idx = fri[np.isfinite(f[fri])]
    dk = np.asarray(epoch_ns(pd.DatetimeIndex(key).tz_localize("UTC")))
    return mk(x, "B1", "1d", idx, np.ones(len(idx)), f[idx], a.to_numpy()[idx], dk[idx], pd.DatetimeIndex(b.index)[idx])


def stream_TS(x):
    """Slow tsmom (arms W1B/H-01 stylised): every 5th trading day, sign of the 60-day return, hold 5 days."""
    b = x.b1d
    a = atr(b, 14)
    c = b["close"]
    sgn = np.sign((c / c.shift(60) - 1).to_numpy())
    f = ((c.shift(-5) - b["open"].shift(-1)) / a).to_numpy()
    allidx = np.arange(len(b))
    idx = allidx[(allidx % 5 == 0) & np.isfinite(f) & np.isfinite(sgn) & (sgn != 0)]
    key = pd.DatetimeIndex(trading_day(b.index))
    dk = np.asarray(epoch_ns(pd.DatetimeIndex(key).tz_localize("UTC")))
    return mk(x, "TS", "1d", idx, sgn[idx], f[idx], a.to_numpy()[idx], dk[idx], pd.DatetimeIndex(b["available_utc"])[idx])



def stream_D1(x):
    """Slice D D-1 (Asia trend persistence), 1h form: sign of the 120-bar vol-scaled return, |z| > 0.5, signal in
    00:00-07:00 UTC, one per 8 bars, hold 8 bars. Spec read from oscillators-trend-volume.md D-1."""
    b = x.b1h
    a = atr(b, 14)
    c = b["close"]
    lr = np.log(c).diff()
    z = (np.log(c).diff(120) / (lr.rolling(120).std() * np.sqrt(120))).to_numpy()
    f = ((c.shift(-8) - b["open"].shift(-1)) / a).to_numpy()
    h = pd.DatetimeIndex(b["available_utc"]).hour.to_numpy()
    d = np.where((np.abs(z) > 0.5) & (h < 7), np.sign(z), 0)
    idx = dedup(np.flatnonzero((d != 0) & np.isfinite(f)), 8)
    dk = np.asarray(epoch_ns(pd.DatetimeIndex(trading_day(b.index)).tz_localize("UTC")))
    return mk(x, "D1", "1h", idx, d[idx], f[idx], a.to_numpy()[idx], dk[idx], pd.DatetimeIndex(b["available_utc"])[idx])


def stream_C1(x):
    """Slice C C-1 DAILY-BAR PROXY (not the 1m first-touch of the source): short at the next open after a trading day
    whose low pierces the prior 30 days' low; hold 5 days; unit 1d ATR."""
    b = x.b1d
    a = atr(b, 14)
    d = np.where(b["low"] < b["low"].rolling(30).min().shift(1), -1, 0)
    f = ((b["close"].shift(-5) - b["open"].shift(-1)) / a).to_numpy()
    idx = dedup(np.flatnonzero((d != 0) & np.isfinite(f)), 5)
    dk = np.asarray(epoch_ns(pd.DatetimeIndex(trading_day(b.index)).tz_localize("UTC")))
    return mk(x, "C1", "1d", idx, d[idx], f[idx], a.to_numpy()[idx], dk[idx], pd.DatetimeIndex(b["available_utc"])[idx])

STREAM_FAMILY = {"F1": "breakout", "F2": "breakout", "O1": "session_open", "A2": "session_open", "F3": "trend",
                 "TS": "trend", "E1": "trend", "D1": "trend", "C1": "trend", "V1": "mean_reversion", "A1": "mean_reversion", "B1": "calendar"}
SELECTED = ["F1", "F2", "F3", "A1", "A2", "B1", "D1", "C1"]       # candidates surfaced by the finished slices' discovery looks
PRIOR = ["E1", "V1", "O1", "TS"]                      # families specified before any look (arms / roster)


# ------------------------------------------------------------------ statistics
def day_t(r: np.ndarray, days: np.ndarray) -> float:
    n = len(r)
    if n < 5:
        return float("nan")
    mu = r.mean()
    s = pd.Series(r - mu).groupby(days).sum()
    G = len(s)
    se = np.sqrt(G / max(G - 1, 1) * (s ** 2).sum()) / n
    return float(mu / se) if se > 0 else float("nan")


def bh_keep(p: np.ndarray, q: float = 0.10) -> np.ndarray:
    m = len(p)
    o = np.argsort(p)
    thr = q * (np.arange(1, m + 1) / m)
    ok = p[o] <= thr
    k = np.max(np.flatnonzero(ok)) + 1 if ok.any() else 0
    keep = np.zeros(m, bool)
    keep[o[:k]] = True
    return keep


def md(rows: list[dict], cols: list[str]) -> str:
    out = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
    for r in rows:
        out += "| " + " | ".join(str(r.get(c, "")) for c in cols) + " |\n"
    return out


def fm(v, d=3):
    return "" if v is None or (isinstance(v, float) and not np.isfinite(v)) else (f"{v:+.{d}f}" if isinstance(v, float) else str(v))


# ------------------------------------------------------------------ part 1: confluence
def filters(x: Ctx, idx: np.ndarray, d: np.ndarray) -> dict[str, np.ndarray]:
    """Pre-specified from mechanism (written before the first run). Each returns a boolean mask over the trade set.
    New candidates from slices C, D, G plug in here as extra entries (name -> mask), nothing else changes."""
    kc4, kc1 = x.kc4h[idx], x.kc1d[idx]
    ny = (x.ny_min[idx] >= 8 * 60) & (x.ny_min[idx] < 13 * 60 + 30)           # NY local 08:00-13:30 (pit window: 45% of day variance)
    return {
        "htf4h": np.sign(kc4) == d,                                              # Elder triple screen: 4h side agrees
        "htf1d": np.sign(kc1) == d,                                              # owner rule: intraday agrees with the daily chart
        "ny_pit": ny,                                                            # clock: NY pit window (local clock)
        "vol_hi": x.atr_pct[idx] >= 0.5,                                         # range forecast proxy: ATR15 rank >= median
        "osc": ((x.rsi15[idx] - 50) * d) > 0,                                    # oscillator: RSI14 on the trade's side of 50
        "room": np.where(d > 0, (x.pdh[idx] - x.close[idx]), (x.close[idx] - x.pdl[idx])) >= x.a.to_numpy()[idx],   # key level: >= 1 ATR to prior-day extreme
    }


def confluence(x: Ctx, F1: pd.DataFrame, rng) -> dict:
    idx, d = F1["idx"].to_numpy(), F1["dir"].to_numpy()
    r = F1["gross"].to_numpy()
    days = F1["day"].to_numpy()
    cp, cb = F1["cost_prior"].to_numpy(), F1["cost_broker"].to_numpy()
    fl = filters(x, idx, d)
    names = list(fl)
    base_mu = r.mean()
    n0 = len(r)
    res, masks = [], {}
    for k in (1, 2, 3):
        for combo in itertools.combinations(names, k):
            m = np.logical_and.reduce([fl[c] for c in combo])
            masks[combo] = m
    # random same-size subsets of the base
    for combo, m in masks.items():
        n = int(m.sum())
        if n < 30:
            continue
        rr = r[m]
        mu = rr.mean()
        draws = np.array([r[rng.choice(n0, n, replace=False)].mean() for _ in range(PERM)])
        z = (mu - draws.mean()) / draws.std(ddof=1)
        ys = F1["year"].to_numpy()[m]
        yp = sum(1 for y in np.unique(ys) if rr[ys == y].mean() > 0 and (ys == y).sum() >= 3)
        yt = sum(1 for y in np.unique(ys) if (ys == y).sum() >= 3)
        res.append({"combo": "+".join(combo), "k": len(combo), "n": n, "share": n / n0, "per_month": n / MONTHS,
                    "gross": mu, "t_day": day_t(rr, days[m]), "z_vs_random_subset": z, "p": pnorm_two(z),
                    "net_prior": mu - cp[m].mean(), "net_broker": mu - cb[m].mean(), "r_per_month_gross": n * mu / MONTHS,
                    "yrs_pos": f"{yp}/{yt}"})
        look(f"confluence:{'+'.join(combo)}")
    P = np.array([q["p"] for q in res])
    keep = bh_keep(P)
    for q, kk in zip(res, keep):
        q["bh"] = "Y" if kk else ""
    # pair interaction and increment
    inter = []
    D = np.unique(days)
    dpos = {v: i for i, v in enumerate(D)}
    di = np.array([dpos[v] for v in days])
    for a_, b_ in itertools.combinations(names, 2):
        A, B = fl[a_], fl[b_]
        cells = [A & B, A & ~B, ~A & B, ~A & ~B]
        S = np.zeros((len(D), 4, 2))
        for c_, mm in enumerate(cells):
            np.add.at(S[:, c_, 0], di[mm], r[mm])
            np.add.at(S[:, c_, 1], di[mm], 1)

        def I_of(Ss):
            tot = Ss.sum(0)
            m11, m10, m01, m00 = [tot[i, 0] / max(tot[i, 1], 1) for i in range(4)]
            mA = (tot[0, 0] + tot[1, 0]) / max(tot[0, 1] + tot[1, 1], 1)
            mB = (tot[0, 0] + tot[2, 0]) / max(tot[0, 1] + tot[2, 1], 1)
            m0 = tot[:, 0].sum() / max(tot[:, 1].sum(), 1)
            return m11 - mA - mB + m0, mA, mB, m11
        i0, mA, mB, m11 = I_of(S)
        boots = np.array([I_of(S[rng.integers(0, len(D), len(D))])[0] for _ in range(300)])
        lo_, hi_ = np.percentile(boots, [2.5, 97.5])
        inter.append({"pair": f"{a_}+{b_}", "m_A": mA, "m_B": mB, "m_AB": m11, "m_0": base_mu, "interaction": i0,
                      "ci_lo": lo_, "ci_hi": hi_, "n_AB": int(cells[0].sum()), "sig": "Y" if (lo_ > 0 or hi_ < 0) else ""})
        look(f"interaction:{a_}+{b_}")
    # single-filter marginals (flag shares, correlation among filters)
    F = np.vstack([fl[n_].astype(float) for n_ in names])
    corr = np.corrcoef(F)
    # pre-specified stacks, each judged as ONE strategy
    stacks = {"STACK-A (Hurst switch + 4h agrees + ATR rank >= median)": fl["htf4h"] & fl["vol_hi"],
              "STACK-B (Hurst switch + NY pit window + ATR rank >= median)": fl["ny_pit"] & fl["vol_hi"]}
    st = []
    for nm, m in stacks.items():
        rr = r[m]
        st.append({"stack": nm, "n": int(m.sum()), "per_month": m.sum() / MONTHS, "gross": rr.mean(), "t_day": day_t(rr, days[m]),
                   "net_prior": rr.mean() - cp[m].mean(), "net_broker": rr.mean() - cb[m].mean(), "r_per_month_gross": m.sum() * rr.mean() / MONTHS,
                   "yrs_pos": f"{sum(1 for y in np.unique(F1['year'].to_numpy()[m]) if rr[F1['year'].to_numpy()[m] == y].mean() > 0)}/8"})
        look(f"stack:{nm[:7]}")
    # selection-bias guard: sign-randomised null of the base rule (fraction of random-direction trade sets with mean >= obs)
    null = np.array([(r * np.sign(rng.random(n0) - (1 - (d > 0).mean()))).mean() for _ in range(PERM)])
    base = {"n": n0, "per_month": n0 / MONTHS, "gross": base_mu, "net_prior": base_mu - cp.mean(), "net_broker": base_mu - cb.mean(),
            "t_day": day_t(r, days), "null_mean": float(null.mean()), "null_sd": float(null.std())}
    # per-filter marginal rows (single) are in res with k=1
    return {"base": base, "cells": res, "inter": inter, "filter_corr": pd.DataFrame(corr, index=names, columns=names),
            "flag_share": {n_: float(fl[n_].mean()) for n_ in names}, "stacks": st}


# ------------------------------------------------------------------ part 2: portfolio
def daily_matrix(T: pd.DataFrame, col: str, days_all: np.ndarray) -> pd.DataFrame:
    s = T.groupby(["day", "stream"])[col].sum().unstack("stream").reindex(days_all).fillna(0.0)
    return s


def sharpe(v: np.ndarray) -> float:
    s = v.std(ddof=1)
    return float(v.mean() / s * np.sqrt(252)) if s > 0 else float("nan")


def portfolio(x: Ctx, T: pd.DataFrame, rng) -> dict:
    days_all = np.unique(x.dkey)
    wk = pd.DatetimeIndex(days_all.astype("datetime64[ns]")).dayofweek < 5
    days_all = days_all[wk]
    out = {}
    names = [s for s in STREAM_FAMILY if s in set(T["stream"])]
    T = T.copy()
    T["net_prior"] = T["gross"] - T["cost_prior"]
    T["net_broker"] = T["gross"] - T["cost_broker"]
    M = {c: daily_matrix(T, c, days_all)[names] for c in ("gross", "net_prior", "net_broker")}
    # per-stream summary
    rows = []
    for s in names:
        t = T[T["stream"] == s]
        rows.append({"stream": s, "family": STREAM_FAMILY[s], "tf": t["tf"].iloc[0], "n": len(t), "per_month": len(t) / MONTHS,
                     "gross": t["gross"].mean(), "t_day": day_t(t["gross"].to_numpy(), t["day"].to_numpy()),
                     "net_prior": t["net_prior"].mean(), "net_broker": t["net_broker"].mean(),
                     "sharpe_gross": sharpe(M["gross"][s].to_numpy()), "sharpe_broker": sharpe(M["net_broker"][s].to_numpy()),
                     "sharpe_prior": sharpe(M["net_prior"][s].to_numpy()),
                     "sel": "selected" if s in SELECTED else "prior"})
    out["streams"] = rows
    # correlation (daily gross R, zero-filled), full sample and co-active days
    G = M["gross"]
    C = G.corr()
    out["corr"] = C
    off = C.to_numpy()[np.triu_indices(len(names), 1)]
    out["corr_mean"] = float(off.mean())
    out["corr_abs_mean"] = float(np.abs(off).mean())
    act = (G != 0)
    co = pd.DataFrame(index=names, columns=names, dtype=float)
    for a_ in names:
        for b_ in names:
            both = act[a_] & act[b_]
            co.loc[a_, b_] = np.corrcoef(G.loc[both, a_], G.loc[both, b_])[0, 1] if both.sum() >= 30 and a_ != b_ else (1.0 if a_ == b_ else np.nan)
    out["corr_co"] = co
    out["coactive_days"] = pd.DataFrame({b: {a: int((act[a] & act[b]).sum()) for a in names} for b in names})
    # regime and session correlation
    b1d = x.b1d
    ad = atr(b1d, 14)
    key = np.asarray(epoch_ns(pd.DatetimeIndex(trading_day(b1d.index)).tz_localize("UTC")))
    vt = pd.Series(terciles(ad / b1d["close"]), index=key).shift(1)
    adx_d = pd.Series(np.select([adx(b1d, 14).shift(1) < 18, adx(b1d, 14).shift(1) < 25], ["<18", "18-25"], ">25"), index=key)
    reg = {}
    for lab, ser in (("vol", vt), ("adx1d", adx_d)):
        s_ = ser.reindex(days_all)
        for val in sorted(set(s_.dropna()) - {"na"}):
            sel = (s_ == val).to_numpy()
            if sel.sum() < 150:
                continue
            cc = G[sel].corr().to_numpy()
            reg[f"{lab}={val}"] = {"days": int(sel.sum()), "mean_rho": float(cc[np.triu_indices(len(names), 1)].mean()),
                                   "avg_stream_sharpe_gross": float(np.nanmean([sharpe(G[sel][n_].to_numpy()) for n_ in names]))}
    for sess in ("Asia", "London", "NY", "Late"):
        Ts = T[T["sess"] == sess]
        Ms = daily_matrix(Ts, "gross", days_all).reindex(columns=names).fillna(0.0)
        ok = [n_ for n_ in names if (Ms[n_] != 0).sum() >= 40]
        if len(ok) >= 3:
            cc = Ms[ok].corr().to_numpy()
            reg[f"entry session={sess}"] = {"days": len(ok), "mean_rho": float(cc[np.triu_indices(len(ok), 1)].mean()), "avg_stream_sharpe_gross": float(np.nanmean([sharpe(Ms[n_].to_numpy()) for n_ in ok])),
                                           "streams": ",".join(ok)}
    out["regimes"] = reg
    # books
    def book_stats(cols: list[str], label: str) -> dict:
        rows_ = {"label": label, "N": len(cols)}
        for c in ("gross", "net_prior", "net_broker"):
            X = M[c][cols]
            sd = X.std(ddof=1).replace(0, np.nan)
            w_er = 1.0 / sd                      # equal risk (vol parity)
            w_iv = 1.0 / sd ** 2                 # inverse variance
            ew = X.sum(axis=1)
            er = (X * w_er).sum(axis=1)
            iv = (X * w_iv).sum(axis=1)
            sh_each = [sharpe(X[k].to_numpy()) for k in cols]
            rho = X.corr().to_numpy()[np.triu_indices(len(cols), 1)].mean() if len(cols) > 1 else 0
            pred = np.nanmean(sh_each) * sqrt(len(cols) / (1 + (len(cols) - 1) * rho))
            rows_.update({f"{c}_avg_single": float(np.nanmean(sh_each)), f"{c}_best_single": float(np.nanmax(sh_each)), f"{c}_book_eqR": sharpe(ew.to_numpy()),
                          f"{c}_book_eqrisk": sharpe(er.to_numpy()), f"{c}_book_invvar": sharpe(iv.to_numpy()), f"{c}_sqrtN_pred": float(pred), f"{c}_rho": float(rho)})
        return rows_
    out["books"] = [book_stats(names, "all 12"), book_stats(SELECTED, "selected 8 (F1,F2,F3,A1,A2,B1,D1,C1)"), book_stats(PRIOR, "prior 4 (E1,V1,O1,TS)")]
    look("portfolio books", 3)
    # sign-randomised null books: each stream's trades get random direction (same long share), compare book Sharpe (gross, equal-risk)
    nulls = {"all 12": [], "prior 4": []}
    Tn = T[["stream", "f", "dir", "day"]].copy()
    long_share = Tn.groupby("stream")["dir"].apply(lambda s: (s > 0).mean())
    for _ in range(200):
        sg = np.where(rng.random(len(Tn)) < Tn["stream"].map(long_share).to_numpy(), 1.0, -1.0)
        Tn["g"] = sg * Tn["f"].to_numpy()
        Mn = Tn.groupby(["day", "stream"])["g"].sum().unstack("stream").reindex(days_all).fillna(0.0)[names]
        sd = Mn.std(ddof=1).replace(0, np.nan)
        nulls["all 12"].append(sharpe((Mn / sd).sum(axis=1).to_numpy()))
        nulls["prior 4"].append(sharpe((Mn[PRIOR] / sd[PRIOR]).sum(axis=1).to_numpy()))
    out["null_books"] = {k: {"mean": float(np.mean(v)), "sd": float(np.std(v)), "p95": float(np.percentile(v, 95))} for k, v in nulls.items()}
    # allocator (rule table) applied to the stream families, at each trading day's start
    a1h = pd.DataFrame({"ts": x.av, "adx": x.adx1h, "q": x.atrq, "day": x.dkey})
    first = a1h.groupby("day")[["adx", "q"]].first().astype(float).reindex(days_all)
    alloc = RuleAllocator()
    W = pd.DataFrame(0.0, index=days_all, columns=names)
    for dd, rw in first.iterrows():
        if not np.isfinite(rw["adx"]) or not np.isfinite(rw["q"]):
            fw = {"trend": 0.0, "mean_reversion": 0.0, "breakout": 0.0, "session_open": 0.0}
        else:
            fw = alloc.weights(Regime(adx_1h=float(rw["adx"]), atr_1h_quartile=int(min(3, rw["q"] * 4)), vol_tercile=1,
                                      minutes_to_tier1=None, minutes_since_tier1=None))
        fw["calendar"] = 1.0
        for s in names:
            W.loc[dd, s] = fw.get(STREAM_FAMILY[s], 0.0)
    al = {}
    for c in ("gross", "net_prior", "net_broker"):
        X = M[c]
        sd = X.std(ddof=1).replace(0, np.nan)
        base_eq = (X / sd).sum(axis=1)
        gated = ((X / sd) * W).sum(axis=1)
        al[c] = {"equal_risk": sharpe(base_eq.to_numpy()), "allocator_gated": sharpe(gated.to_numpy())}
        if c == "net_broker":
            # permutation control: shuffle the weight rows across days (keeps weight marginals, breaks the date link)
            sh = []
            for _ in range(300):
                Wp = W.iloc[rng.permutation(len(W))].set_axis(W.index)
                sh.append(sharpe(((X / sd) * Wp).sum(axis=1).to_numpy()))
            al[c]["perm_mean"] = float(np.mean(sh))
            al[c]["perm_sd"] = float(np.std(sh))
    al["avg_weight_by_family"] = {f: float(np.mean([W[s].mean() for s in names if STREAM_FAMILY[s] == f])) for f in set(STREAM_FAMILY.values())}
    al["days_all_zero"] = float((W.sum(axis=1) == 0).mean())
    out["allocator"] = al
    look("allocator gate", 1)
    # walk-forward expert selection (weights from trailing 2y net-broker Sharpe, floor 0), evaluated 2012-2017
    yrs = pd.DatetimeIndex(days_all.astype("datetime64[ns]")).year.to_numpy()
    X = M["net_broker"]
    sd_all = X.std(ddof=1).replace(0, np.nan)
    wf, ew_ = [], []
    for y in range(2012, 2018):
        tr = X[(yrs >= y - 2) & (yrs < y)]
        w = pd.Series([max(sharpe(tr[s].to_numpy()), 0.0) if np.isfinite(sharpe(tr[s].to_numpy())) else 0.0 for s in names], index=names)
        te = X[yrs == y]
        w = w / w.sum() if w.sum() > 0 else w
        wf.append(((te / sd_all) * w).sum(axis=1))
        ew_.append((te / sd_all).sum(axis=1) / len(names))
    wf, ew_ = pd.concat(wf), pd.concat(ew_)
    out["walkforward"] = {"wf_sharpe_net_broker": sharpe(wf.to_numpy()), "equal_risk_sharpe_same_period": sharpe(ew_.to_numpy())}
    look("walk-forward switching", 1)
    return out


# ------------------------------------------------------------------ part 3: meta-labelling
def ridge_logit(X: np.ndarray, y: np.ndarray, lam: float = 5.0, it: int = 30) -> np.ndarray:
    Xb = np.hstack([np.ones((len(X), 1)), X])
    w = np.zeros(Xb.shape[1])
    P = lam * np.eye(Xb.shape[1])
    P[0, 0] = 0
    for _ in range(it):
        p = 1 / (1 + np.exp(-np.clip(Xb @ w, -30, 30)))
        g = Xb.T @ (p - y) + P @ w
        H = (Xb * (p * (1 - p))[:, None]).T @ Xb + P + 1e-6 * np.eye(len(w))
        w = w - np.linalg.solve(H, g)
    return w


def auc(p: np.ndarray, y: np.ndarray) -> float:
    r = pd.Series(p).rank().to_numpy()
    n1 = y.sum()
    n0 = len(y) - n1
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)) if n1 and n0 else float("nan")


def meta(x: Ctx, F1: pd.DataFrame, rng) -> dict:
    idx, d = F1["idx"].to_numpy(), F1["dir"].to_numpy()
    loc = x.ny_min[idx] / 60.0
    feats = {
        "hurst128": x.hurst.to_numpy()[idx], "adx15": x.adx15[idx], "atr_rank": x.atr_pct[idx],
        "rsi_side": (x.rsi15[idx] - 50) * d / 50, "bbz_side": np.clip(x.bbz[idx] * d, -4, 4), "ret4_side": np.clip(x.ret4[idx] * d, -3, 3),
        "kc4h_side": np.clip(x.kc4h[idx] * d, -3, 3), "kc1d_side": np.clip(x.kc1d[idx] * d, -3, 3),
        "hour_sin": np.sin(2 * np.pi * loc / 24), "hour_cos": np.cos(2 * np.pi * loc / 24), "dir": d,
        "with_break": np.where(x.hl[idx] == "low", 1.0, -1.0),
    }
    X = np.column_stack(list(feats.values())).astype(float)
    ok = np.isfinite(X).all(axis=1)
    X, y_g, r = X[ok], (F1["gross"].to_numpy()[ok] > 0).astype(float), F1["gross"].to_numpy()[ok]
    yrs = F1["year"].to_numpy()[ok]
    res = []
    top_means, base_means, aucs = [], [], []
    for yv in range(2013, 2018):
        tr, te = yrs < yv, yrs == yv
        mu, sd = X[tr].mean(0), X[tr].std(0) + 1e-9
        w = ridge_logit((X[tr] - mu) / sd, y_g[tr])
        p = 1 / (1 + np.exp(-(np.hstack([np.ones((te.sum(), 1)), (X[te] - mu) / sd]) @ w)))
        ptr = 1 / (1 + np.exp(-(np.hstack([np.ones((tr.sum(), 1)), (X[tr] - mu) / sd]) @ w)))
        thr = np.median(ptr)
        top = p >= thr
        aucs.append(auc(p, y_g[te]))
        top_means.append(r[te][top].mean())
        base_means.append(r[te].mean())
        res.append({"test_year": yv, "n": int(te.sum()), "auc": aucs[-1], "mean_all": base_means[-1], "mean_top_half": top_means[-1],
                    "n_top": int(top.sum())})
    # label-permutation null: the whole walk-forward on shuffled labels
    null_lift, null_auc = [], []
    for _ in range(30):
        tl, au = [], []
        for yv in range(2013, 2018):
            tr, te = yrs < yv, yrs == yv
            mu, sd = X[tr].mean(0), X[tr].std(0) + 1e-9
            yp = rng.permutation(y_g[tr])
            w = ridge_logit((X[tr] - mu) / sd, yp)
            p = 1 / (1 + np.exp(-(np.hstack([np.ones((te.sum(), 1)), (X[te] - mu) / sd]) @ w)))
            ptr = 1 / (1 + np.exp(-(np.hstack([np.ones((tr.sum(), 1)), (X[tr] - mu) / sd]) @ w)))
            top = p >= np.median(ptr)
            tl.append(r[te][top].mean() - r[te].mean())
            au.append(auc(p, y_g[te]))
        null_lift.append(np.mean(tl))
        null_auc.append(np.mean(au))
    look("meta-label walk-forward (1 model, 12 features)", 1)
    lift = float(np.mean(np.array(top_means) - np.array(base_means)))
    # importance: standardised coefficients on the full fit (descriptive only)
    mu, sd = X.mean(0), X.std(0) + 1e-9
    w = ridge_logit((X - mu) / sd, y_g)
    return {"per_year": res, "mean_auc": float(np.mean(aucs)), "lift_top_half": lift, "null_lift_mean": float(np.mean(null_lift)),
            "null_lift_sd": float(np.std(null_lift)), "null_auc_mean": float(np.mean(null_auc)), "null_auc_sd": float(np.std(null_auc)),
            "coef": dict(zip(feats.keys(), [float(v) for v in w[1:]])), "n_features": X.shape[1], "n": int(len(r)), "base_rate": float(y_g.mean())}


# ------------------------------------------------------------------ main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(SEED)
    m1 = load(a.raw)
    assert m1.index.max() < DISC_END
    x = build_ctx(m1)
    du = union_dir(x)
    T = pd.concat([stream_F1(x, du), stream_F2(x, du), stream_F3(x), stream_V1(x), stream_E1(x), stream_O1(x), stream_A1(x),
                   stream_A2(x), stream_B1(x), stream_TS(x), stream_D1(x), stream_C1(x)], ignore_index=True)
    F1 = T[T["stream"] == "F1"].reset_index(drop=True)
    res: dict = {"n_trades": T.groupby("stream").size().to_dict()}
    c = confluence(x, F1, rng)
    res["confluence"] = {k: (v.round(3).to_dict() if isinstance(v, pd.DataFrame) else v) for k, v in c.items()}
    p = portfolio(x, T, rng)
    res["portfolio"] = {k: (v.round(3).to_dict() if isinstance(v, pd.DataFrame) else v) for k, v in p.items()}
    res["meta"] = meta(x, F1, rng)
    res["looks"] = {"total": len(LOOKS), "by_kind": pd.Series([k.split(":")[0] for k in LOOKS]).value_counts().to_dict()}
    (a.out / "combinations.json").write_text(json.dumps(res, indent=1, default=float))
    # human-readable tables
    t = []
    b = c["base"]
    t.append("BASE F1 " + json.dumps(b, default=float))
    cols = ["combo", "n", "share", "per_month", "gross", "t_day", "z_vs_random_subset", "bh", "net_prior", "net_broker", "r_per_month_gross", "yrs_pos"]
    rows = []
    for q in c["cells"]:
        rows.append({k: (fm(q[k]) if isinstance(q[k], float) else q.get(k, "")) for k in cols})
    t.append("CELLS\n" + md(rows, cols))
    cols2 = ["pair", "m_A", "m_B", "m_AB", "interaction", "ci_lo", "ci_hi", "n_AB", "sig"]
    t.append("INTER\n" + md([{k: (fm(q[k]) if isinstance(q[k], float) else q[k]) for k in cols2} for q in c["inter"]], cols2))
    t.append("FILTERCORR\n" + c["filter_corr"].round(2).to_string())
    t.append("FLAGSHARE " + json.dumps(c["flag_share"]))
    t.append("STACKS\n" + md([{k: (fm(v) if isinstance(v, float) else v) for k, v in s.items()} for s in c["stacks"]], list(c["stacks"][0].keys())))
    t.append("STREAMS\n" + md([{k: (fm(v) if isinstance(v, float) else v) for k, v in s.items()} for s in p["streams"]], list(p["streams"][0].keys())))
    t.append("CORR\n" + p["corr"].round(2).to_string())
    t.append("CORR_COACTIVE\n" + p["corr_co"].round(2).to_string())
    t.append("COACTIVE_DAYS\n" + p["coactive_days"].to_string())
    t.append("corr_mean %.3f abs %.3f" % (p["corr_mean"], p["corr_abs_mean"]))
    t.append("REGIMES " + json.dumps(p["regimes"], default=float, indent=1))
    t.append("BOOKS " + json.dumps(p["books"], default=float, indent=1))
    t.append("NULLBOOKS " + json.dumps(p["null_books"]))
    t.append("ALLOC " + json.dumps(p["allocator"], default=float, indent=1))
    t.append("WALKFWD " + json.dumps(p["walkforward"]))
    t.append("META " + json.dumps(res["meta"], default=float, indent=1))
    t.append("LOOKS " + json.dumps(res["looks"]))
    t.append("TRADES " + json.dumps(res["n_trades"]))
    (a.out / "tables.txt").write_text("\n\n".join(t))
    print("\n\n".join(t))


if __name__ == "__main__":
    main()
