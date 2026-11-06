"""POS-1 specs as code (docs/research/pos1/prereg.md section 2; universe amendment docs/research/pos1/amendment-a1.md).

Every signal is `(symbol, panel) -> Series` over that symbol's daily dates, decided at the 21:00 UTC close from rows
known by then (the engine re-checks point in time on every run). Month-end is a calendar rule (the last business day
of the month), so a truncated panel never mistakes its last row for a month-end. Auxiliary series (DTB3, DFII10, USD
broad, ^GSPC) are read from the data lane's local files and cut at the symbol's last known close on `available_utc`.

Unit sizing (004, 027, 033): 0.5% of equity risked per unit at the rule's stop distance in ATR(20) (POS-1 027's
statement, applied to 004 and 033, which say "unit" without a figure).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from goldbot.data.daily import DAILY_ROOT, usd_broad
from goldbot.data.store import asof_join
from goldbot.research.position.engine import atr, ewma_vol
from goldbot.research.position.signals import Panel, PositionSpec

ROOT = Path(__file__).resolve().parents[3]
DAILY = DAILY_ROOT
RISK_PER_UNIT = 0.005

# amendment A1: the markets on disk (U_16 minus USDCAD, USDCHF, NZDUSD; AUDUSD ends 2014-05)
U_A1 = ("XAUUSD", "XAGUSD", "XPTUSD", "WTI", "US500", "US100", "GER40", "JP225", "UK100", "EURUSD", "USDJPY",
        "GBPUSD", "AUDUSD")
MIN_LIVE_A1 = 6
U_METALS = ("XAUUSD", "XAGUSD", "XPTUSD")


# ---------------------------------------------------------------- helpers
def _close(symbol: str, panel: Panel) -> pd.Series:
    return panel[symbol]["close"].astype(float)


def _r(c: pd.Series, n: int) -> pd.Series:
    return pd.Series(np.log((c / c.shift(n)).to_numpy(dtype=float)), index=c.index)


def _sign(x: pd.Series) -> pd.Series:
    return pd.Series(np.sign(x.to_numpy(dtype=float)), index=x.index).where(x.notna())


def _month_end(idx: pd.DatetimeIndex) -> np.ndarray:
    nxt = idx + pd.offsets.BDay(1)
    return np.asarray(nxt.month != idx.month)


def _on_month_end(v: pd.Series) -> pd.Series:
    return v.where(_month_end(pd.DatetimeIndex(v.index)))


def _cut(symbol: str, panel: Panel) -> pd.Timestamp:
    return pd.Timestamp(panel[symbol]["close_utc"].max())


@lru_cache(maxsize=8)
def _aux(name: str) -> pd.DataFrame:
    if name == "USD":
        df = usd_broad(DAILY)
    elif name == "GSPC":
        df = pd.read_parquet(DAILY / "yahoo_gspc.parquet")
        if "value" not in df.columns:
            df = df.rename(columns={"close": "value"})
        if "value_date" not in df.columns:
            df = df.rename(columns={"date": "value_date"})
    else:
        df = pd.read_parquet(DAILY / f"fred_{name}.parquet")
    out = pd.DataFrame({"available_utc": pd.to_datetime(df["available_utc"], utc=True),
                        "value_date": pd.to_datetime(df["value_date"]),
                        "value": pd.to_numeric(df["value"], errors="coerce")}).dropna()
    out = out[out["available_utc"] < pd.Timestamp("2025-10-01", tz="UTC")]
    # a weekly release publishes several value dates at one instant: the as-of value is the latest value date
    out = out.sort_values(["available_utc", "value_date"], kind="mergesort")
    return out.drop_duplicates("available_utc", keep="last").reset_index(drop=True)


def _aux_on(name: str, symbol: str, panel: Panel) -> pd.Series:
    """The aux series as known at each of the symbol's 21:00 UTC closes (asof on available_utc), cut at the panel."""
    a = _aux(name)
    a = a[a["available_utc"] <= _cut(symbol, panel)]
    bars = panel[symbol]
    left = pd.DataFrame({"ts_utc": pd.DatetimeIndex(bars["close_utc"]), "_pos": np.arange(len(bars))})
    got = asof_join(left, a[["available_utc", "value"]]).sort_values("_pos")
    return pd.Series(got["value"].to_numpy(dtype=float), index=bars.index)


def _unit_exposure(symbol: str, panel: Panel, stop_atr: float) -> pd.Series:
    bars = panel[symbol]
    a = pd.Series(atr(bars, 20), index=bars.index)
    return RISK_PER_UNIT * bars["close"] / (stop_atr * a)


# ---------------------------------------------------------------- member signals
def sig_001(symbol: str, panel: Panel) -> pd.Series:
    return _on_month_end(_sign(_r(_close(symbol, panel), 252)))


def sig_002(symbol: str, panel: Panel) -> pd.Series:
    c = _close(symbol, panel)
    return (_sign(_r(c, 21)) + _sign(_r(c, 63)) + _sign(_r(c, 252))) / 3.0


def _turtle_state(bars: pd.DataFrame, entry: int = 55, exit_: int = 20) -> pd.Series:
    """+1/-1/0: enter on a close beyond the prior `entry`-day high/low, exit on a close beyond the prior `exit_`-day
    opposite extreme (causal; the engine's 2 x ATR stop is applied on top)."""
    c, h, lo = (bars[k].to_numpy(float) for k in ("close", "high", "low"))
    hi_e = pd.Series(h).rolling(entry).max().shift(1).to_numpy()
    lo_e = pd.Series(lo).rolling(entry).min().shift(1).to_numpy()
    hi_x = pd.Series(h).rolling(exit_).max().shift(1).to_numpy()
    lo_x = pd.Series(lo).rolling(exit_).min().shift(1).to_numpy()
    s = np.full(len(c), np.nan)
    pos = 0.0
    for i in range(len(c)):
        if not np.isfinite(hi_e[i]):
            continue
        if pos > 0 and c[i] < lo_x[i]:
            pos = 0.0
        elif pos < 0 and c[i] > hi_x[i]:
            pos = 0.0
        if pos <= 0 and c[i] > hi_e[i]:
            pos = 1.0
        elif pos >= 0 and c[i] < lo_e[i]:
            pos = -1.0
        s[i] = pos
    return pd.Series(s, index=bars.index)


def sig_004(symbol: str, panel: Panel) -> pd.Series:
    return _turtle_state(panel[symbol]) * _unit_exposure(symbol, panel, 2.0)


def sig_006(symbol: str, panel: Panel) -> pd.Series:
    c = _close(symbol, panel)
    return _sign(c.rolling(50).mean() - c.rolling(200).mean())


def sig_008(symbol: str, panel: Panel) -> pd.Series:
    c = _close(symbol, panel)
    sma = c.rolling(200).mean()
    return (c > sma).astype(float).where(sma.notna())


def _bill_12m(symbol: str, panel: Panel) -> pd.Series:
    """The 12-month bill return as known at each close: DTB3 compounded daily over the last 252 trading days."""
    r = _aux_on("DTB3", symbol, panel) / 100.0
    return np.exp(np.log1p(r / 252.0).rolling(252).sum()) - 1.0


def sig_015(symbol: str, panel: Panel) -> pd.Series:
    c = _close(symbol, panel)
    r12 = c / c.shift(252) - 1.0
    b = _bill_12m(symbol, panel)
    v = (r12 > b).astype(float).where(r12.notna() & b.notna())
    return _on_month_end(v)


def sig_016(symbol: str, panel: Panel) -> pd.Series:
    c = _close(symbol, panel)
    ry = _aux_on("DFII10", symbol, panel)
    usd = _aux_on("USD", symbol, panel)
    parts = [_sign(_r(c, 21)), _sign(_r(c, 63)), _sign(_r(c, 252)),
             _sign(c.rolling(50).mean() - c.rolling(200).mean()),
             -_sign(ry - ry.shift(20)), -_sign(usd / usd.shift(20) - 1.0)]
    return pd.concat(parts, axis=1).sum(axis=1, min_count=6) / 6.0


def _hold_001(symbol: str, panel: Panel) -> pd.Series:
    return sig_001(symbol, panel).ffill()


def _ens_055_raw(symbol: str, panel: Panel) -> pd.Series:
    t = _turtle_state(panel[symbol])
    return (_hold_001(symbol, panel) + sig_002(symbol, panel) + t + sig_006(symbol, panel)
            + sig_008(symbol, panel)) / 5.0


def sig_055(symbol: str, panel: Panel) -> pd.Series:
    return _ens_055_raw(symbol, panel)


def sig_056(symbol: str, panel: Panel) -> pd.Series:
    """max(0, 055 exposure) capped at 1x: the 055 signal vol-scaled to 10%/yr here (sizing unit), then clipped."""
    e = _ens_055_raw(symbol, panel) * (0.10 / ewma_vol(_close(symbol, panel), 60))
    return e.clip(lower=0.0, upper=1.0)


def sig_033(symbol: str, panel: Panel) -> pd.Series:
    """US500 (^GSPC) daily return below its trailing 250-day 5th percentile: long for 10 trading days, unit-sized."""
    g = _aux_on("GSPC", symbol, panel)
    r = g / g.shift(1) - 1.0
    r = r.where(g.ne(g.shift(1)))                    # a stale as-of value is not a new day's return
    thr = r.rolling(250, min_periods=200).quantile(0.05).shift(1)
    trig = (r < thr).to_numpy()
    on = np.zeros(len(r))
    left = 0
    for i in range(len(r)):
        if trig[i]:
            left = 10
        if left > 0:
            on[i] = 1.0
            left -= 1
    return pd.Series(on, index=r.index) * _unit_exposure(symbol, panel, 2.5)


def sig_045(symbol: str, panel: Panel) -> pd.Series:
    c = _close(symbol, panel)
    rv = (np.log(c).diff() ** 2).rolling(21).mean() * 252.0
    return _on_month_end(np.minimum(2.0, 0.10 ** 2 / rv))


def sig_060(symbol: str, panel: Panel) -> pd.Series:
    """Month-end: hold the metal with the highest r126 if it beats the 6-month bill return, else flat (vol-scaled)."""
    live = [m for m in U_METALS if m in panel]
    r126 = pd.DataFrame({m: (_close(m, panel) / _close(m, panel).shift(126) - 1.0) for m in live})
    idx = panel[symbol].index
    r126 = r126.reindex(idx)
    bill6 = (np.exp(np.log1p(_aux_on("DTB3", symbol, panel) / 100.0 / 252.0).rolling(126).sum()) - 1.0)
    best = r126.fillna(-np.inf).idxmax(axis=1)
    top = r126.max(axis=1, skipna=True)
    v = ((best == symbol) & (top > bill6)).astype(float).where(r126[symbol].notna() & bill6.notna())
    return _on_month_end(v)


def sig_057(symbol: str, panel: Panel) -> pd.Series:
    """026's signal weighted by 1 / (simple 60-day vol, lagged one day), sizing unit; the book is scaled after."""
    c = _close(symbol, panel)
    vol = (np.log(c).diff().rolling(60).std() * np.sqrt(252.0)).shift(1)
    return sig_002(symbol, panel) * (0.10 / vol)


def sig_027(symbol: str, panel: Panel) -> pd.Series:
    return sig_004(symbol, panel)


# ---------------------------------------------------------------- specs
XAU = ("XAUUSD",)
GOLD_SPECS = [
    PositionSpec(id="POS1-001", symbols=XAU, signal=sig_001),
    PositionSpec(id="POS1-002", symbols=XAU, signal=sig_002, band=1 / 3),
    PositionSpec(id="POS1-004", symbols=XAU, signal=sig_004, sizing="unit", stop_atr=2.0),
    PositionSpec(id="POS1-006", symbols=XAU, signal=sig_006),
    PositionSpec(id="POS1-008", symbols=XAU, signal=sig_008, long_only=True),
    PositionSpec(id="POS1-015", symbols=XAU, signal=sig_015, long_only=True),
    PositionSpec(id="POS1-016", symbols=XAU, signal=sig_016, band=1 / 6),
    PositionSpec(id="POS1-033", symbols=XAU, signal=sig_033, sizing="unit", stop_atr=2.5, long_only=True),
    PositionSpec(id="POS1-055", symbols=XAU, signal=sig_055),
    PositionSpec(id="POS1-056", symbols=XAU, signal=sig_056, sizing="unit", long_only=True),
    PositionSpec(id="POS1-045", symbols=XAU, signal=sig_045, sizing="unit", long_only=True),
]
METAL_SPECS = [
    PositionSpec(id="POS1-025", symbols=U_METALS, signal=sig_002, band=1 / 3, min_live=2),
    PositionSpec(id="POS1-060", symbols=U_METALS, signal=sig_060, long_only=True),
]
BOOK_SPECS = [
    PositionSpec(id="POS1-026A", symbols=U_A1, signal=sig_002, band=1 / 3, min_live=MIN_LIVE_A1, book_target_vol=0.10),
    PositionSpec(id="POS1-027A", symbols=U_A1, signal=sig_027, sizing="unit", stop_atr=2.0, min_live=MIN_LIVE_A1),
    PositionSpec(id="POS1-057A", symbols=U_A1, signal=sig_057, sizing="unit", band=1 / 3, min_live=MIN_LIVE_A1,
                 book_target_vol=0.10),
]
SPECS = GOLD_SPECS + METAL_SPECS + BOOK_SPECS
