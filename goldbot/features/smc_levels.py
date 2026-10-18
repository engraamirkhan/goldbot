"""Point-in-time structure context on top of `smc_replica`, built from the same bars and the same leg pivots; every
value at bar t uses bars closed at or before t only (all joins to bars via asof on `available_utc`).

* `trend_table(base, htf)` - the "trending?" table: one column per timeframe, +1 / -1 = sign(+DI - -DI) of that
  timeframe's Wilder DMI(14) (the study input says "based on ADX"). Each timeframe is computed on its own closed
  bars and joined to the base bars by asof on `available_utc` (a higher bar is visible only once it has closed).
  The pulled graphics carry the table only as one final snapshot (all four rows down); the rule reproduces it on
  all four timeframes, but a gate "ADX >= threshold" does not (15m, 1h, 4h ADX are under 20 at that bar), so no ADX
  gate is applied (`threshold` stays available). Not verifiable bar by bar: rule-only, snapshot-consistent.
* `swing_zones(...)` - trailing swing range (the highest high / lowest low since the latest swing pivot of each
  kind, reset when a new swing pivot confirms), its midpoint, and the premium / equilibrium / discount bands as
  fractions of the range: premium = top 5%, discount = bottom 5%, equilibrium = the 5% band around the midpoint
  (rule-only: drawings are not in the pulled plots). `strong_high` is 1 while the swing trend is bearish (the
  top was not broken, so it still caps the move), `strong_low` while it is bullish; the opposite extreme is "weak".
* `period_levels(bar_time, high, low, close)` - running (so far this session) day/week/month high and low
  (`day_high` ... `month_low`), the previous period's high/low (`pdh`/`pdl`, `pwh`/`pwl`, `pmh`/`pml`) and the daily
  floor pivots from the previous day's H/L/C (`pivot_pp`, `pivot_r1..3`, `pivot_s1..3`). The day boundary is the
  forex session roll, 17:00 America/New_York (21:00 UTC in summer, 22:00 in winter), where the charting site's daily
  bars open; a week runs Sunday 17:00 to Friday 17:00. Checked on the pulled window: the previous-day high is the
  chart's PDH label (4177.455) and PP / R1 reproduce the two short yellow lines of the owner's chart (4149.4 /
  4198.8 against about 4150 / 4200 read off the image); the other levels are rule-only.
* `pivot_features(close, atr, levels)` - distance of the close to each pivot in ATR, `day_bias` (+1 above PP, -1
  below) and the signed distance to the nearest pivot level.
* `ma_crosses(close)` - MA10/MA20 cross (EMA scheme, the blue triangles) and MA50/MA200 golden (+1) / death (-1)
  cross (EMA50 over SMA200, the yellow triangles).
* `swing_labels(high, low)` - HH / LH on each swing pivot high and HL / LL on each swing pivot low, stamped at the
  pivot's confirming bar.
* `fvg_projected(decision_available, lower)` - gaps found on a lower timeframe counted onto each higher decision
  bar: a lower-timeframe gap belongs to the first decision bar that closes at or after the gap's own close.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.data.timeutil import utc_index
from goldbot.features.smc_replica import (
    Arr,
    cross_over,
    cross_under,
    ema_seeded,
    fair_value_gaps,
    leg_pivots,
    rma_seeded,
    sma,
)

TREND_COLUMNS = {"15m": "trend_15m", "1h": "trend_1h", "4h": "trend_4h", "1d": "trend_1d"}
ADX_LEN = 14
PREMIUM_FRAC = 0.05                # top / bottom band width as a fraction of the swing range
EQUILIBRIUM_HALF = 0.025           # half width of the band around the midpoint


def adx_trend(high: Arr, low: Arr, close: Arr, length: int = ADX_LEN, threshold: float = 0.0) -> Arr:
    """+1 / -1 per bar by DI sign, 0 on warm-up or when ADX < threshold (default 0: no gate). Wilder DMI: smoothed +DM, -DM and true range, DI = 100 x
    smoothed DM / smoothed TR, ADX = Wilder average of DX."""
    h, lo, c = (np.asarray(x, dtype=float) for x in (high, low, close))
    n = len(c)
    up = np.concatenate(([0.0], np.diff(h)))
    dn = np.concatenate(([0.0], -np.diff(lo)))
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    pc = np.concatenate(([c[0]], c[:-1])) if n else c
    tr = np.maximum(h - lo, np.maximum(np.abs(h - pc), np.abs(lo - pc)))
    with np.errstate(invalid="ignore", divide="ignore"):
        str_ = rma_seeded(tr, length)
        pdi = 100.0 * rma_seeded(plus, length) / str_
        mdi = 100.0 * rma_seeded(minus, length) / str_
        dx = 100.0 * np.abs(pdi - mdi) / (pdi + mdi)
    adx = rma_seeded(np.where(np.isnan(dx), 0.0, dx), length)
    adx[: 2 * length - 1] = np.nan           # DX is defined from bar `length`; ADX seeds `length` DX values later
    with np.errstate(invalid="ignore"):
        ok = (adx >= threshold) if threshold > 0 else ~np.isnan(pdi - mdi)
        return np.where(ok, np.sign(pdi - mdi), 0.0)


def _ns(s: pd.Series) -> pd.Series:
    return pd.Series(utc_index(s).as_unit("ns"), index=s.index)


def trend_table(base: pd.DataFrame, htf: dict[str, pd.DataFrame], base_tf: str = "15m") -> pd.DataFrame:
    """`trend_<tf>` columns (see module docstring), aligned to base's index. `base` and each `htf[tf]` carry
    open/high/low/close and available_utc (when the bar's close became known)."""
    out = pd.DataFrame(index=base.index)
    out[TREND_COLUMNS[base_tf]] = adx_trend(base["high"].to_numpy(), base["low"].to_numpy(), base["close"].to_numpy())
    left = pd.DataFrame({"available_utc": _ns(base["available_utc"]).to_numpy(), "_row": np.arange(len(base))})
    left = left.sort_values("available_utc")
    for tf, bars in htf.items():
        t = adx_trend(bars["high"].to_numpy(), bars["low"].to_numpy(), bars["close"].to_numpy())
        right = pd.DataFrame({"available_utc": _ns(bars["available_utc"]).to_numpy(), "v": t})
        j = pd.merge_asof(left, right.sort_values("available_utc"), on="available_utc", direction="backward")
        col = np.zeros(len(base))
        col[j["_row"].to_numpy()] = j["v"].fillna(0.0).to_numpy()
        out[TREND_COLUMNS[tf]] = col
    return out.astype(int)


def swing_zones(high: Arr, low: Arr, trend_swing: Arr, swing_len: int = 50) -> pd.DataFrame:
    """Trailing swing range and zones per bar (NaN until both a swing top and bottom exist)."""
    h, lo = np.asarray(high, dtype=float), np.asarray(low, dtype=float)
    ph, pl = leg_pivots(h, lo, swing_len)
    n = len(h)
    top, btm = np.full(n, np.nan), np.full(n, np.nan)
    t = b = np.nan
    for i in range(n):
        if not np.isnan(ph[i]):
            t = ph[i]
        if not np.isnan(pl[i]):
            b = pl[i]
        if not np.isnan(t):
            t = max(t, h[i])
        if not np.isnan(b):
            b = min(b, lo[i])
        top[i], btm[i] = t, b
    rng = top - btm
    mid = (top + btm) / 2.0
    out = pd.DataFrame({
        "swing_top": top, "swing_btm": btm, "swing_mid": mid,
        "premium_lo": top - PREMIUM_FRAC * rng, "discount_hi": btm + PREMIUM_FRAC * rng,
        "equilibrium_lo": mid - EQUILIBRIUM_HALF * rng, "equilibrium_hi": mid + EQUILIBRIUM_HALF * rng,
    })
    ts = np.asarray(trend_swing)
    out["strong_high"] = (ts < 0).astype(int)
    out["strong_low"] = (ts > 0).astype(int)
    return out


def _session_keys(bar_time: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    """(day, week, month) keys of the forex session: time shifted so 17:00 New York becomes midnight."""
    t = utc_index(bar_time).tz_convert("America/New_York") + pd.Timedelta(hours=7)
    naive = t.tz_localize(None)
    idx = bar_time.index
    return (pd.Series(naive.floor("D"), index=idx), pd.Series(naive.to_period("W-SUN").start_time, index=idx),
            pd.Series(naive.to_period("M").start_time, index=idx))


def period_levels(bar_time: pd.Series, high: Arr, low: Arr, close: Arr) -> pd.DataFrame:
    """Running and previous day/week/month levels plus the daily floor pivots (module docstring). Every value at bar t
    uses bars up to t; the previous period is the one before the bar's own period (NaN for the first period)."""
    h, lo, c = (pd.Series(np.asarray(x, dtype=float), index=bar_time.index) for x in (high, low, close))
    out: dict[str, pd.Series] = {}
    keys = _session_keys(bar_time)
    for name, key, tag in zip(("day", "week", "month"), keys, ("d", "w", "m"), strict=True):
        out[f"{name}_high"] = h.groupby(key).cummax()
        out[f"{name}_low"] = lo.groupby(key).cummin()
        agg = pd.DataFrame({"h": h.groupby(key).max(), "l": lo.groupby(key).min(), "c": c.groupby(key).last()})
        prev = agg.shift(1)
        out[f"p{tag}h"] = key.map(prev["h"])
        out[f"p{tag}l"] = key.map(prev["l"])
        if tag == "d":
            ph, pl, pc = out["pdh"], out["pdl"], key.map(prev["c"])
            pp = (ph + pl + pc) / 3.0
            out.update(pivot_pp=pp, pivot_r1=2 * pp - pl, pivot_s1=2 * pp - ph, pivot_r2=pp + (ph - pl),
                       pivot_s2=pp - (ph - pl), pivot_r3=ph + 2 * (pp - pl), pivot_s3=pl - 2 * (ph - pp))
    return pd.DataFrame(out).astype(float)


PIVOT_NAMES = ("pp", "r1", "r2", "r3", "s1", "s2", "s3")


def pivot_features(close: Arr, atr: Arr, levels: pd.DataFrame) -> pd.DataFrame:
    """`pivot_<name>_dist_atr` = (close - level) / ATR for PP/R1-3/S1-3, `day_bias` (+1 above PP, -1 below) and
    `pivot_nearest_dist_atr` (signed distance to the nearest of the seven levels)."""
    c, a = np.asarray(close, dtype=float), np.asarray(atr, dtype=float)
    d = {f"pivot_{n}_dist_atr": (c - levels[f"pivot_{n}"].to_numpy()) / a for n in PIVOT_NAMES}
    m = np.column_stack(list(d.values()))
    absm = np.where(np.isnan(m), np.inf, np.abs(m))
    pick = np.take_along_axis(m, absm.argmin(axis=1)[:, None], 1)[:, 0]
    out = pd.DataFrame(d)
    out["day_bias"] = np.nan_to_num(np.sign(c - levels["pivot_pp"].to_numpy())).astype(int)
    out["pivot_nearest_dist_atr"] = np.where(np.isinf(absm.min(axis=1)), np.nan, pick)
    return out


def ma_crosses(close: Arr) -> pd.DataFrame:
    """`ma_cross_10_20` (EMA10 over / under EMA20: +1 / -1) and `ma_cross_50_200` (EMA50 over / under SMA200)."""
    c = np.asarray(close, dtype=float)
    e10, e20, e50, s200 = ema_seeded(c, 10), ema_seeded(c, 20), ema_seeded(c, 50), sma(c, 200)
    return pd.DataFrame({
        "ma_cross_10_20": cross_over(e10, e20).astype(int) - cross_under(e10, e20).astype(int),
        "ma_cross_50_200": cross_over(e50, s200).astype(int) - cross_under(e50, s200).astype(int)})


def swing_labels(high: Arr, low: Arr, swing_len: int = 50) -> pd.DataFrame:
    """`swing_high_label` +1 HH / -1 LH and `swing_low_label` +1 HL / -1 LL at each swing pivot's confirming bar."""
    ph, pl = leg_pivots(np.asarray(high, dtype=float), np.asarray(low, dtype=float), swing_len)
    out = pd.DataFrame({"swing_high_label": np.zeros(len(ph), dtype=int), "swing_low_label": np.zeros(len(ph), dtype=int)})
    for col, piv in (("swing_high_label", ph), ("swing_low_label", pl)):
        idx = np.flatnonzero(~np.isnan(piv))
        if len(idx) > 1:
            out.loc[idx[1:], col] = np.sign(np.diff(piv[idx])).astype(int)
    return out


def fvg_projected(decision_available: pd.Series, lower: pd.DataFrame) -> pd.DataFrame:
    """`fvg_bull_lower` / `fvg_bear_lower`: lower-timeframe gaps landing in each decision bar. `lower` has
    open/high/low/close and available_utc. A gap is known at its bar's close, so it counts in the first decision bar
    whose `available_utc` is at or after that."""
    bull, bear = fair_value_gaps(lower["open"].to_numpy(), lower["high"].to_numpy(), lower["low"].to_numpy(),
                                 lower["close"].to_numpy())
    dec = utc_index(decision_available).as_unit("ns").to_numpy()
    lo_av = utc_index(lower["available_utc"]).as_unit("ns").to_numpy()
    order = np.argsort(dec, kind="stable")
    pos = np.searchsorted(dec[order], lo_av, side="left")
    out = {"fvg_bull_lower": np.zeros(len(dec), dtype=int), "fvg_bear_lower": np.zeros(len(dec), dtype=int)}
    ok = pos < len(dec)
    for name, ev in (("fvg_bull_lower", bull), ("fvg_bear_lower", bear)):
        np.add.at(out[name], order[pos[ok & ev]], 1)
    return pd.DataFrame(out, index=decision_available.index)
