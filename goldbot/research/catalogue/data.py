"""Point-in-time bars for the catalogue screen (WS-3, consolidated plan 2026-11-07, quant A3).

One `Bars` per (instrument, timeframe, window): numpy arrays only, so detectors and the simulator never touch pandas
in the hot path. Every bar carries
  t   bar open (ns, UTC)          ta  the instant the bar is known (= its close; `visible_at` of the stored file)
  o h l c  mid prices             spr spread in price units (nan when unknown)
  co  close-only flag (high == low or no OHLC): stops on such a bar are checked and filled AT THE CLOSE
  seg source segment id (a change is a source cutover: level/lookback detectors restart there)
  vol tick volume (nan when unknown)

Sources
  gold 5m..4h  resampled from the Dukascopy 1m bid/ask files raw/xauusd_1m_dukascopy_<year>.parquet (mid = (bid+ask)/2)
  gold 1d      raw/daily/bars/xauusd_1d.parquet (21:00/22:00 UTC roll, built from the same 1m)
  non-gold     raw/daily/bars/<key>_4h.parquet and _1d.parquet (Dukascopy h1 mids, bid/ask spreads), 2003+
  1w           resampled from 1d by ISO week (known at the last daily close of the week)

Windows (amendment W2): the loader cuts every frame at the window end, refuses the holdout (HoldoutSealed) and
test_old unless the caller passes allow_test_old (only for CONFIRM passers, run.py). Warm-up reads earlier bars
(sprint-3000 precedent, quant B3); a trade belongs to the window of its decision."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from goldbot.data.timeutil import epoch_ns
from goldbot.research.windows import HOLDOUT_FROM, HoldoutSealed, TestOldSealed

HOLDOUT_NS = int(HOLDOUT_FROM.value)
MIN = 60_000_000_000
HOUR = 60 * MIN
DAY = 24 * HOUR
TF_NS = {"5m": 5 * MIN, "15m": 15 * MIN, "30m": 30 * MIN, "1h": HOUR, "4h": 4 * HOUR, "1d": DAY, "1w": 7 * DAY}
INTRADAY = ("5m", "15m", "30m", "1h", "4h")


def _ts(s: str) -> int:
    return int(pd.Timestamp(s, tz="UTC").value)


@dataclass(frozen=True)
class Win:
    name: str
    data_from: int      # first bar read (warm-up)
    dec_from: int       # first decision that counts
    end: int            # exclusive: nothing at or after is read


WINDOWS: dict[str, Win] = {
    "explore": Win("explore", _ts("2008-09-01"), _ts("2010-01-01"), _ts("2018-01-01")),
    "learn_pre": Win("learn_pre", _ts("2003-01-01"), _ts("2003-05-01"), _ts("2006-01-01")),
    "confirm": Win("confirm", _ts("2015-01-01"), _ts("2018-01-01"), HOLDOUT_NS),
    "test_old": Win("test_old", _ts("2004-01-01"), _ts("2006-02-01"), _ts("2010-01-01")),
}
# gold intraday warm-up is shorter (the 1m files are large); daily/weekly rules need up to 252 bars + 60
INTRADAY_WARMUP_NS = 120 * DAY


def window(name: str, *, allow_test_old: bool = False) -> Win:
    if name == "holdout":
        raise HoldoutSealed("the holdout (2025-10-01 onward) is sealed; the catalogue loader never reads it")
    if name == "test_old" and not allow_test_old:
        raise TestOldSealed("test_old is read once, only for CONFIRM passers (run.py --window test_old)")
    if name not in WINDOWS:
        raise ValueError(f"unknown window {name!r}")
    w = WINDOWS[name]
    if w.end > HOLDOUT_NS:
        raise HoldoutSealed(f"window {name} reaches the holdout")
    return w


def raw_dir() -> Path:
    """GOLDBOT_RAW, else <repo>/raw, else the main checkout's raw/ (a worktree has none: raw/ is not in git)."""
    env = os.environ.get("GOLDBOT_RAW")
    if env:
        return Path(env)
    here = Path(__file__).resolve().parents[3] / "raw"
    if here.exists():
        return here
    try:
        common = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=Path(__file__).parent,
                                capture_output=True, text=True, check=True).stdout.strip()
        return (Path(__file__).parent / common).resolve().parent / "raw"
    except (OSError, subprocess.CalledProcessError):
        return here


@dataclass
class Bars:
    inst: str
    tf: str
    t: np.ndarray
    ta: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray  # noqa: E741
    c: np.ndarray
    spr: np.ndarray
    co: np.ndarray
    seg: np.ndarray
    vol: np.ndarray
    meta: dict = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.c)

    def slice(self, a: int, b: int) -> "Bars":
        return Bars(self.inst, self.tf, self.t[a:b], self.ta[a:b], self.o[a:b], self.h[a:b], self.l[a:b], self.c[a:b],
                    self.spr[a:b], self.co[a:b], self.seg[a:b], self.vol[a:b], meta=dict(self.meta))

    def truncate(self, n: int) -> "Bars":
        """The first n bars: what a detector could know at bar n-1's close (truncation-prefix look-ahead test)."""
        return self.slice(0, n)


def assert_sealed(ta: np.ndarray, t: np.ndarray | None = None) -> None:
    if len(ta) and (int(np.max(ta)) > HOLDOUT_NS or (t is not None and int(np.max(t)) >= HOLDOUT_NS)):
        raise HoldoutSealed("a holdout bar reached the catalogue loader output")


def _seg_ids(src: pd.Series) -> np.ndarray:
    base = src.astype(str).str.replace(r"\(.*\)$", "", regex=True).str.strip()
    return pd.factorize(base)[0].astype(np.int32)


def _frame_to_bars(inst: str, tf: str, df: pd.DataFrame) -> Bars:
    t = epoch_ns(df["t"])
    ta = epoch_ns(df["ta"])
    o, h, l, c = (df[k].to_numpy(float) for k in ("o", "h", "l", "c"))  # noqa: E741
    co = ~np.isfinite(h) | ~np.isfinite(l) | (h <= l)
    h = np.where(np.isfinite(h), h, c)
    l = np.where(np.isfinite(l), l, c)  # noqa: E741
    o = np.where(np.isfinite(o), o, c)
    spr = df["spr"].to_numpy(float) if "spr" in df else np.full(len(df), np.nan)
    vol = df["vol"].to_numpy(float) if "vol" in df else np.full(len(df), np.nan)
    seg = _seg_ids(df["src"]) if "src" in df else np.zeros(len(df), np.int32)
    return Bars(inst, tf, t, ta, o, h, l, c, spr, co, seg, vol)


def _cut(df: pd.DataFrame, w: Win, tf: str) -> pd.DataFrame:
    lo = w.data_from if tf not in INTRADAY or tf == "4h" else max(w.data_from, w.dec_from - INTRADAY_WARMUP_NS)
    t = epoch_ns(df["t"])
    ta = epoch_ns(df["ta"])
    return df[(t >= lo) & (ta <= w.end) & (t < w.end)].reset_index(drop=True)


# ------------------------------------------------------------------ gold from 1m
def _gold_minutes(years: range) -> pd.DataFrame:
    raw = raw_dir()
    parts = []
    for y in years:
        if y >= HOLDOUT_FROM.year + 1:
            continue
        f = raw / f"xauusd_1m_dukascopy_{y}.parquet"
        if not f.exists():
            continue
        d = pd.read_parquet(f, columns=["ts_utc", "bid_open", "bid_high", "bid_low", "bid_close", "ask_open",
                                        "ask_high", "ask_low", "ask_close", "tick_count"])
        parts.append(d)
    if not parts:
        return pd.DataFrame()
    d = pd.concat(parts, ignore_index=True)
    d = d[d["ts_utc"] < HOLDOUT_FROM]
    m = pd.DataFrame({"t": d["ts_utc"]})
    for k in ("open", "high", "low", "close"):
        m[k] = (d[f"bid_{k}"].to_numpy(float) + d[f"ask_{k}"].to_numpy(float)) / 2
    m["spr"] = d["ask_close"].to_numpy(float) - d["bid_close"].to_numpy(float)
    m["vol"] = d["tick_count"].to_numpy(float)
    return m.sort_values("t").drop_duplicates("t").reset_index(drop=True)


def _resample_minutes(m: pd.DataFrame, tf: str) -> pd.DataFrame:
    step = TF_NS[tf]
    key = epoch_ns(m["t"]) // step * step
    g = m.groupby(key, sort=True)
    out = pd.DataFrame({"o": g["open"].first(), "h": g["high"].max(), "l": g["low"].min(), "c": g["close"].last(),
                        "spr": g["spr"].mean(), "vol": g["vol"].sum()})
    out["t"] = pd.to_datetime(out.index.to_numpy(np.int64), utc=True)
    out["ta"] = pd.to_datetime(out.index.to_numpy(np.int64) + step, utc=True)
    out["src"] = "dukascopy_1m:XAUUSD"
    return out.reset_index(drop=True)


FINE_INSTRUMENTS = ("XAUUSD",)   # instruments with 1m bars: the fill bar of a stop/limit entry is resolved there (C10)


def load_minutes(win: str, *, allow_test_old: bool = False) -> Bars:
    """Gold 1m mid bars of one window (from the intraday warm-up start to the window end): the finer bars that resolve
    stop/limit fills inside a bar of any gold timeframe (quant C10). Raises on the holdout."""
    w = window(win, allow_test_old=allow_test_old)
    lo = max(w.data_from, w.dec_from - INTRADAY_WARMUP_NS)
    y0 = pd.Timestamp(lo, unit="ns").year
    y1 = pd.Timestamp(w.end - 1, unit="ns").year
    m = _gold_minutes(range(y0, y1 + 1))
    t = epoch_ns(m["t"]) if len(m) else np.zeros(0, np.int64)
    keep = (t >= lo) & (t + MIN <= w.end)
    m, t = m[keep], t[keep]
    n = len(t)
    o, h, l, c = (m[k].to_numpy(float) for k in ("open", "high", "low", "close"))  # noqa: E741
    b = Bars("XAUUSD", "1m", t, t + MIN, o, h, l, c, m["spr"].to_numpy(float), np.zeros(n, bool),
             np.zeros(n, np.int32), m["vol"].to_numpy(float))
    assert_sealed(b.ta, b.t)
    b.meta.update(window=w.name, dec_from=w.dec_from, end=w.end)
    return b


# ------------------------------------------------------------------ stored daily/4h bars
BAR_KEYS = {  # instrument -> file key in raw/daily/bars
    "XAUUSD": "xauusd", "XAGUSD": "xagusd", "XAUEUR": "xaueur", "XAUJPY": "xaujpy", "XTIUSD": "xtiusd",
    "US500": "us500", "USTEC": "ustec", "DE40": "de40", "UK100": "uk100", "US30": "us30", "USINDEX": "usindex",
    "BTCUSD": "btcusd", "EURUSD": "eurusd", "GBPUSD": "gbpusd", "AUDUSD": "audusd", "NZDUSD": "nzdusd",
    "USDJPY": "usdjpy", "USDCAD": "usdcad", "USDCHF": "usdchf", "AUDJPY": "audjpy", "EURAUD": "euraud",
    "GBPAUD": "gbpaud", "EURGBP": "eurgbp", "EURJPY": "eurjpy", "GBPJPY": "gbpjpy",
}


@lru_cache(maxsize=64)
def _stored(inst: str, tf: str) -> pd.DataFrame:
    f = raw_dir() / "daily" / "bars" / f"{BAR_KEYS[inst]}_{tf}.parquet"
    d = pd.read_parquet(f)
    out = pd.DataFrame({"t": d["ts_utc"], "ta": d["visible_at"], "o": d["open"], "h": d["high"], "l": d["low"],
                        "c": d["close"], "spr": d["spread_mean"], "vol": d.get("volume", np.nan), "src": d["source"]})
    out = out[out["ta"] <= HOLDOUT_FROM].sort_values("t").drop_duplicates("t").reset_index(drop=True)
    return out


def _weekly(d: pd.DataFrame) -> pd.DataFrame:
    """ISO weeks of a daily frame, keyed by the week of the bar's close (ta - 1ns); known at the week's last close."""
    ta = pd.to_datetime(d["ta"], utc=True) - pd.Timedelta(1, "ns")
    wk = (ta.dt.tz_localize(None).dt.to_period("W-FRI")).astype(str)
    g = d.groupby(wk.to_numpy(), sort=False)
    out = pd.DataFrame({"t": g["t"].first(), "ta": g["ta"].last(), "o": g["o"].first(), "h": g["h"].max(),
                        "l": g["l"].min(), "c": g["c"].last(), "spr": g["spr"].mean(), "vol": g["vol"].sum(),
                        "src": g["src"].last()})
    return out.sort_values("t").reset_index(drop=True)


def load(inst: str, tf: str, win: str, *, allow_test_old: bool = False) -> Bars:
    """The bars of one cell for one window (warm-up included). Raises on the holdout; never returns a bar whose close
    is after the window end."""
    w = window(win, allow_test_old=allow_test_old)
    if inst == "XAUUSD" and tf in INTRADAY and tf != "4h":
        y0 = pd.Timestamp(max(w.data_from, w.dec_from - INTRADAY_WARMUP_NS), unit="ns").year
        y1 = pd.Timestamp(w.end - 1, unit="ns").year
        m = _gold_minutes(range(y0, y1 + 1))
        df = _resample_minutes(m, tf) if len(m) else pd.DataFrame(columns=["t", "ta", "o", "h", "l", "c", "spr",
                                                                           "vol", "src"])
    elif tf == "1w":
        df = _weekly(_stored(inst, "1d"))
    else:
        df = _stored(inst, tf)
    df = _cut(df, w, tf)
    b = _frame_to_bars(inst, tf, df)
    assert_sealed(b.ta, b.t)
    b.meta.update(window=w.name, dec_from=w.dec_from, end=w.end)
    return b


# ------------------------------------------------------------------ aux series (point in time)
def fred(series: str, win: str, *, allow_test_old: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """(available_utc ns, value) of one FRED series, cut at the window end; join with asof_ns."""
    w = window(win, allow_test_old=allow_test_old)
    d = pd.read_parquet(raw_dir() / "daily" / f"fred_{series}.parquet")
    av = epoch_ns(pd.to_datetime(d["available_utc"], utc=True))
    keep = av < w.end
    return av[keep], d["value"].to_numpy(float)[keep]


def us_events(win: str, types: tuple[str, ...], *, allow_test_old: bool = False) -> np.ndarray:
    """Scheduled release instants (ns) of calendar-v1 event types, each known (available_utc) before it happens."""
    return us_events_avail(win, types, allow_test_old=allow_test_old)[0]


def us_events_avail(win: str, types: tuple[str, ...], *, allow_test_old: bool = False
                    ) -> tuple[np.ndarray, np.ndarray]:
    """(release instants, first publication instant of each) in ns: the instants of `us_events` with the earliest
    available_utc any calendar row gives that instant (BACKLOG 152: ctx carries it so detectors read a date only once
    published, and S6 cuts on it)."""
    w = window(win, allow_test_old=allow_test_old)
    f = raw_dir() / "calendar" / "us_calendar.parquet"
    if not f.exists():
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    d = pd.read_parquet(f)
    d = d[d["event_type"].isin(types)]
    ev = epoch_ns(pd.to_datetime(d["event_utc"], utc=True))
    av = epoch_ns(pd.to_datetime(d["available_utc"], utc=True))
    ok = (ev < w.end) & (av <= ev)
    ev, av = ev[ok], av[ok]
    u, inv = np.unique(ev, return_inverse=True)
    first = np.full(len(u), np.iinfo(np.int64).max, dtype=np.int64)
    np.minimum.at(first, inv, av)
    return u, first


def asof_ns(t_query: np.ndarray, t_avail: np.ndarray, values: np.ndarray) -> np.ndarray:
    """The latest value whose availability is <= each query instant (nan before the first)."""
    order = np.argsort(t_avail, kind="stable")
    ta, v = t_avail[order], values[order]
    k = np.searchsorted(ta, t_query, side="right") - 1
    out = np.where(k >= 0, v[np.clip(k, 0, None)], np.nan)
    return out
