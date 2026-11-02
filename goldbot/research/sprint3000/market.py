"""The sprint-3000 screen's market: Dukascopy 1m mid bars of one window, resampled ONCE per timeframe (15m 1h 4h 1d),
with each minute's session instance (the leak-free control draws inside it) and trading day.

Windows (sprint-3000 brief and windows amendment W2, frozen 2026-11-02 before any pre-2010 bar or result was read):
  explore   2010-01-01..2017-12-31   learn/screen
  learn_old 2003-01-01..2005-12-31   extra learning data (new trials only)
  embargo   2006-01                  not used
  test_old  2006-02-01..2009-12-31   held-out test, read once, only for confirm passers
  confirm   2018-01-01..2025-09-30   one test
  holdout   2025-10-01 onward        sealed: "Hard assert: nothing at/after 2025-10-01 is ever read"
"A trade belongs to the window of its ENTRY; warm-up may read earlier bars." The
loader never opens a year file at or after the window's end (so explore never opens 2018, and nothing ever opens 2026),
cuts every frame at the window end and asserts nothing at or after 2025-10-01 survived, independent of any setting. A
year file that is not on disk is reported as no data, never an error.

Bars: UTC-aligned 15m/1h/4h; 1d = the trading day rolling at 17:00 New York (NY+7 date). A bar's decision time `dec` is
its nominal close; the entry is the open of the first minute at or after it."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pydantic import Field, model_validator

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.data.timeutil import epoch_ns
from goldbot.research.arms import HOLDOUT_FROM

MIN = 60_000_000_000
HOUR = 60 * MIN
DAY = 24 * HOUR
TF_NS = {"15m": 15 * MIN, "1h": HOUR, "4h": 4 * HOUR}
NYZ = "America/New_York"
RAW_NAME = "xauusd_1m_dukascopy_{year}.parquet"
_COLS = ["ts_utc", "bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high", "ask_low", "ask_close"]


class Window(FrozenRecord):
    """A screen window: minutes are read from `data_from` (indicator warm-up) to `end` (exclusive); a trade counts when
    its decision is at or after `dec_from` and it fills before `end` (the window of its entry)."""
    name: str = Field(min_length=1)
    data_from: UtcTimestamp
    dec_from: UtcTimestamp
    end: UtcTimestamp

    @model_validator(mode="after")
    def _ordered(self) -> "Window":
        if not self.data_from <= self.dec_from < self.end:
            raise ValueError(f"window {self.name}: data_from <= dec_from < end does not hold")
        if self.end > HOLDOUT_FROM:
            raise ValueError(f"window {self.name} reaches the holdout (from {HOLDOUT_FROM.date()})")
        return self

    @property
    def entry_years(self) -> tuple[int, ...]:
        """The calendar years a trade of this window can enter in."""
        return tuple(range(self.dec_from.year, (self.end - pd.Timedelta(1, "ns")).year + 1))


def _w(name: str, data_from: str, dec_from: str, end: str) -> Window:
    ts = [pd.Timestamp(x, tz="UTC") for x in (data_from, dec_from, end)]
    return Window(name=name, data_from=ts[0], dec_from=ts[1], end=ts[2])


# amendment W2's table; `goldbot.research.windows` (feat/research-windows-w1) is not on main at this writing, so the
# table lives here and test_the_windows_are_amendment_w2s_table pins it. Warm-up (data_from) reads earlier bars only.
WINDOWS: dict[str, Window] = {
    "explore": _w("explore", "2010-01-01", "2010-01-01", "2018-01-01"),
    "learn_old": _w("learn_old", "2002-01-01", "2003-01-01", "2006-01-01"),
    "test_old": _w("test_old", "2005-01-01", "2006-02-01", "2010-01-01"),          # 2006-01 is the embargo
    "confirm": _w("confirm", "2016-01-01", "2018-01-01", str(HOLDOUT_FROM.date())),  # 2016-17: warm-up only
}


def assert_before_holdout(t_ns: np.ndarray) -> None:
    if len(t_ns) and int(np.max(t_ns)) >= HOLDOUT_FROM.value:
        raise AssertionError(f"holdout rows present (>= {HOLDOUT_FROM.date()}): the sprint-3000 screen never reads them")


def load_minutes(raw: Path, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Mid 1m bars in [start, end): columns t (epoch ns), o, h, l, c. Year files at or after `end` are never opened; a
    missing year file reads as no rows. Refuses an `end` past the holdout."""
    if end > HOLDOUT_FROM:
        raise ValueError(f"a read up to {end.date()} reaches the holdout (from {HOLDOUT_FROM.date()})")
    parts = []
    for y in range(start.year, end.year + 1):
        path = Path(raw) / RAW_NAME.format(year=y)
        if pd.Timestamp(f"{y}-01-01", tz="UTC") >= end or not path.exists():
            continue
        p = pd.read_parquet(path, columns=_COLS)
        ts = np.asarray(epoch_ns(pd.DatetimeIndex(p["ts_utc"])), dtype=np.int64)
        keep = (ts >= start.value) & (ts < end.value)
        f = pd.DataFrame({"t": ts[keep]})
        for k in ("open", "high", "low", "close"):
            f[k[0]] = (p[f"bid_{k}"].to_numpy(float)[keep] + p[f"ask_{k}"].to_numpy(float)[keep]) / 2
        parts.append(f)
    if not parts:
        return pd.DataFrame({"t": np.zeros(0, np.int64), **{k: np.zeros(0) for k in "ohlc"}})
    m = pd.concat(parts, ignore_index=True).sort_values("t", kind="stable").drop_duplicates("t")
    m = m.reset_index(drop=True)
    assert_before_holdout(m.t.to_numpy())
    assert not len(m) or int(m.t.max()) < end.value, "a row at or after the window end was read"
    return m


def years_on_disk(raw: Path, years: tuple[int, ...]) -> tuple[int, ...]:
    return tuple(y for y in years if (Path(raw) / RAW_NAME.format(year=y)).exists())


class Tape(FrozenRecord):
    """The 1m mid tape: epoch-ns minute stamps and OHLC, sorted, unique."""
    t: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray  # noqa: E741 - OHLC naming
    c: np.ndarray

    @classmethod
    def of(cls, t: np.ndarray, o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray) -> Tape:
        return cls(t=np.asarray(t, np.int64), o=np.asarray(o, float), h=np.asarray(h, float), l=np.asarray(lo, float),
                   c=np.asarray(c, float))


class Bars(FrozenRecord):
    """Bars of one timeframe: `dec` = decision time (nominal close, epoch ns), OHLC, and `tday` = the trading day
    (days since epoch of the NY+7 date) of each bar's last minute."""
    tf: str
    dec: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray  # noqa: E741
    c: np.ndarray
    tday: np.ndarray

    @classmethod
    def of(cls, tf: str, dec: np.ndarray, o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray,
           tday: np.ndarray | None = None) -> Bars:
        dec = np.asarray(dec, np.int64)
        td = trading_day(dec - 1) if tday is None else np.asarray(tday, np.int64)
        return cls(tf=tf, dec=dec, o=np.asarray(o, float), h=np.asarray(h, float), l=np.asarray(lo, float),
                   c=np.asarray(c, float), tday=td)


def trading_day(t_ns: np.ndarray) -> np.ndarray:
    """Days since epoch of the NY+7 date (the trading day that rolls at 17:00 New York)."""
    if not len(t_ns):
        return np.zeros(0, np.int64)
    ny = pd.DatetimeIndex(pd.to_datetime(np.asarray(t_ns, np.int64), utc=True)).tz_convert(NYZ).tz_localize(None)
    return np.asarray((ny + pd.Timedelta(hours=7)).normalize().to_numpy().astype("datetime64[D]").astype(np.int64))


def _ny_close(days: np.ndarray) -> np.ndarray:
    """17:00 New York on each trading day (days since epoch), as UTC epoch ns: the 1d bar's close."""
    local = pd.DatetimeIndex(pd.to_datetime(days.astype(np.int64) * DAY)) + pd.Timedelta(hours=17)
    return np.asarray(epoch_ns(local.tz_localize(NYZ, ambiguous=True, nonexistent="shift_forward")), dtype=np.int64)


def _resample(tape: Tape, key: np.ndarray, tday: np.ndarray, tf: str, dec_of: Any) -> Bars:
    if not len(key):
        z = np.zeros(0)
        return Bars.of(tf, np.zeros(0, np.int64), z, z, z, z, np.zeros(0, np.int64))
    first = np.r_[0, np.flatnonzero(np.diff(key)) + 1]
    last = np.r_[first[1:] - 1, len(key) - 1]
    return Bars.of(tf, dec_of(key[first]), tape.o[first], np.maximum.reduceat(tape.h, first),
                   np.minimum.reduceat(tape.l, first), tape.c[last], tday[last])


class Market(FrozenRecord):
    """One window's tape, bars per timeframe (resampled once), each minute's session code (-1 outside), the last
    minute index of its session run (the control's draw range) and its trading day. `memo` caches indicators per
    process (a spec's ATR(14) on 1h is computed once for all specs that use it)."""
    window: Window
    tape: Tape
    code: np.ndarray
    sstart: np.ndarray
    run_end: np.ndarray
    tday: np.ndarray
    bars: dict[str, Bars]
    years_missing: tuple[int, ...] = ()
    memo: dict[Any, Any] = Field(default_factory=dict)

    @classmethod
    def from_minutes(cls, m: pd.DataFrame, window: Window, years_missing: tuple[int, ...] = ()) -> Market:
        t = m.t.to_numpy(np.int64)
        assert_before_holdout(t)
        if len(t) and int(t.max()) >= window.end.value:
            raise AssertionError(f"minutes at or after the window end {window.end.date()}")
        tape = Tape.of(t, m.o.to_numpy(), m.h.to_numpy(), m.l.to_numpy(), m.c.to_numpy())
        code, sstart = DEFAULT_SESSIONS.session_windows(pd.DatetimeIndex(pd.to_datetime(t, utc=True)))
        chg = np.r_[True, (np.diff(code) != 0) | (np.diff(sstart) != 0)] if len(t) else np.zeros(0, bool)
        starts = np.flatnonzero(chg)
        run_end = np.r_[starts[1:] - 1, len(t) - 1][np.cumsum(chg) - 1] if len(t) else np.zeros(0, np.int64)
        tday = trading_day(t)
        bars = {tf: _resample(tape, t // sec, tday, tf, lambda k, s=sec: (k + 1) * s) for tf, sec in TF_NS.items()}
        bars["1d"] = _resample(tape, tday, tday, "1d", _ny_close)
        return cls(window=window, tape=tape, code=code, sstart=sstart, run_end=np.asarray(run_end, np.int64),
                   tday=tday, bars=bars, years_missing=years_missing)

    @classmethod
    def load(cls, raw: Path, window: Window) -> Market:
        missing = tuple(y for y in window.entry_years if y not in years_on_disk(raw, (y,)))
        return cls.from_minutes(load_minutes(raw, window.data_from, window.end), window, missing)

    @property
    def data_last_utc(self) -> str:
        return pd.Timestamp(int(self.tape.t.max()), tz="UTC").isoformat() if len(self.tape.t) else ""
