"""History source map (D1) and era quality (D4, quant R11): clock check, hourly agreement, OHLC tiers per month.

Everything here is outcome-blind: it reads prices of one source against an independent reference, never a label, a
return-after-signal or a P&L. The thresholds are frozen in `R11` (id `R11_ID`) and pinned by
tests/test_era_quality.py, committed before any grade was computed (lane W1-E, 2026-11-25); changing one is a new id
and a quant sign-off, never a quiet edit after seeing grades.

Tiers per instrument x month (quant R11, rulings 2026-11-25):
* A: barrier (high/low) labels and intraday features. Every check below passes.
* B: close checks pass, a range check fails or cannot be evaluated: close-to-close labels and daily features only.
* C: excluded (a close check fails, or there is no independent reference to check against).
* F: quarantined (the instrument-year failed the clock check); kept on disk under _quarantine, never loaded.
Close checks: close vs the reference within 10 bp on >= 98 % of days; no stale close run > 30 min in session; reverting
spikes > 15 x MAD <= 1 per 10,000 bars. Range checks: median session-minute coverage >= 85 % and <= 5 % of days below
60 %; flat bars (O=H=L=C) <= 20 %; 1m daily range within [0.90, 1.02] x the reference's range on >= 95 % of days. The
range ratio needs a native reference (one Dukascopy leg): the high/low of a cross built from legs is not the cross's
high/low, so on a synthetic reference the month can reach B, never A.

Day convention: the daily bar of goldbot.data.daily (closes 21:00 UTC; `session_date`). Session days are Mon-Fri
(every day for a `weekend: true` instrument). Days excluded from the day-share denominators, decided by calendar and
reference data only: 25 Dec, 1 Jan, and days the reference itself holds under 60 % of its session hours.
"""
from __future__ import annotations

import datetime as dt
from functools import lru_cache
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import Field, model_validator

from goldbot.base import FrozenRecord
from goldbot.config import ROOT, load_yaml
from goldbot.data.daily import session_date
from goldbot.data.timeutil import epoch_ns

DEFAULT_HISTORY_SOURCES = ROOT / "config" / "history_sources.yaml"
HOLDOUT_CUT = pd.Timestamp("2025-10-01", tz="UTC")   # quant R10 / D21: research stores end strictly before this


# ---------------------------------------------------------------------------------------------------- D1 source map
def _period(v: int | str, end: bool) -> pd.Period:
    s = str(v)
    if len(s) == 4:
        return pd.Period(f"{s}-12" if end else f"{s}-01", freq="M")
    return pd.Period(s, freq="M")


class DukascopySource(FrozenRecord):
    code: str
    point: float
    from_: str = Field(alias="from")


class HistDataSource(FrozenRecord):
    code: str
    from_: int = Field(alias="from")


class ReferenceEra(FrozenRecord):
    """An independent hourly reference from month `from` on (until the next newer era)."""

    from_: str = Field(alias="from")
    legs: dict[str, float] = Field(default_factory=dict)
    scale: float = 1.0
    dukascopy_1m: bool = False

    @property
    def native(self) -> bool:
        """True when the reference's high/low are the instrument's own (one leg, exponent 1, or the 1m files)."""
        return self.dukascopy_1m or (len(self.legs) == 1 and next(iter(self.legs.values())) == 1.0)

    @model_validator(mode="after")
    def _one_kind(self) -> ReferenceEra:
        if self.dukascopy_1m == bool(self.legs):
            raise ValueError("a reference era is either dukascopy_1m or legs, exactly one")
        return self


class HistorySource(FrozenRecord):
    owner_name: str | None = None
    asset_class: Literal["fx", "metal", "index", "energy", "crypto", "dollar_index"]
    cfd_type: Literal["spot", "cash_index", "rolling_future", "synthetic_cross"]
    roll: str | None
    weekend: bool
    session_minutes: int
    dukascopy: DukascopySource | None
    histdata: HistDataSource | None
    source_tz: dict[str, str]
    price_bands: list[tuple[int | str, int | str, float, float]]
    reference: list[ReferenceEra]
    synthetic: str | None

    @model_validator(mode="after")
    def _bands(self) -> HistorySource:
        for a, b, lo, hi in self.price_bands:
            if not 0 < lo < hi or hi / lo >= 10:
                raise ValueError(f"band {a}-{b} [{lo}, {hi}]: needs 0 < low < high and high/low < 10 "
                                 "(a x10 point error must fall outside)")
            if _period(a, False) > _period(b, True):
                raise ValueError(f"band {a}-{b} runs backwards")
        if self.cfd_type == "rolling_future" and not self.roll:
            raise ValueError("a rolling_future needs its roll calendar")
        if self.dukascopy is None and self.histdata is None and not self.synthetic:
            raise ValueError("no source and no synthetic recipe")
        return self

    def band(self, month: pd.Period | dt.date | str) -> tuple[float, float] | None:
        p = pd.Period(month, freq="M")
        for a, b, lo, hi in self.price_bands:
            if _period(a, False) <= p <= _period(b, True):
                return (lo, hi)
        return None

    def price_ok(self, median: float, month: pd.Period | dt.date | str) -> bool:
        """A month's median close lies in its era's band (False when no band covers the month: fail closed)."""
        band = self.band(month)
        return band is not None and band[0] <= median <= band[1]

    def reference_for(self, month: pd.Period | dt.date | str) -> ReferenceEra | None:
        p = pd.Period(month, freq="M")
        eras = sorted(self.reference, key=lambda e: pd.Period(e.from_, freq="M"), reverse=True)
        return next((e for e in eras if pd.Period(e.from_, freq="M") <= p), None)


class HistorySources(FrozenRecord):
    version: int
    holdout_cut: str
    instruments: dict[str, HistorySource]

    @model_validator(mode="after")
    def _cut(self) -> HistorySources:
        if pd.Timestamp(self.holdout_cut) != HOLDOUT_CUT:
            raise ValueError(f"holdout_cut {self.holdout_cut} differs from the coded {HOLDOUT_CUT} (quant R10)")
        return self


def load_history_sources(path: str | Path = DEFAULT_HISTORY_SOURCES) -> HistorySources:
    return HistorySources.model_validate(load_yaml(path))


@lru_cache(maxsize=1)
def history_sources() -> HistorySources:
    return load_history_sources()


def drop_holdout(frame: pd.DataFrame, ts_col: str = "ts_utc") -> tuple[pd.DataFrame, int]:
    """(rows strictly before HOLDOUT_CUT, rows dropped). The research partition never stores a sealed minute."""
    keep = pd.to_datetime(frame[ts_col], utc=True) < HOLDOUT_CUT
    return frame.loc[keep].reset_index(drop=True), int((~keep).sum())


# ----------------------------------------------------------------------------------------------- R11, frozen first
R11_ID = "r11-v1-2026-11-25"
R11: dict[str, float] = {
    "coverage_median_min": 0.85,     # median session-minute coverage per day
    "thin_day_coverage": 0.60,       # a day below this is "thin"
    "thin_day_share_max": 0.05,      # share of thin days allowed
    "flat_share_max": 0.20,          # O=H=L=C bars
    "stale_minutes": 30.0,           # an unchanged close for longer than this, in session, is stale
    "stale_runs_max": 0.0,
    "spike_mad_k": 15.0,             # |r| > k x MAD(nonzero 1m returns) and the next return reverts
    "spike_revert_frac": 0.5,        # ... by at least this fraction of the spike, with the opposite sign
    "spike_rate_max": 1e-4,          # spikes per bar
    "range_ratio_lo": 0.90,
    "range_ratio_hi": 1.02,
    "range_day_share_min": 0.95,
    "close_bp": 10.0,                # |log(close / reference close)| in basis points
    "close_day_share_min": 0.98,
    "clock_offset_max_min": 1.0,     # D2: clock offset per instrument-year
    "clock_search_min": 120.0,       # offsets searched, +-
    "h1_agree_spread_mult": 2.0,     # D2: |h1 close - reference h1 close| <= 2 x median reference spread
    "h1_agree_share_min": 0.99,
}


def spike_mask(close: pd.Series) -> pd.Series:
    """Reverting spikes: the bar whose 1m log return exceeds k x MAD of the month's nonzero returns and is followed by a
    return of the opposite sign reverting at least `spike_revert_frac` of it. MAD over nonzero returns because a thin
    feed's median absolute return is 0. Errors: the caller quarantines these rows (kept, logged), never drops them."""
    c = close.to_numpy(dtype="float64")
    out = np.zeros(len(c), dtype=bool)
    if len(c) < 3:
        return pd.Series(out, index=close.index)
    r = np.diff(np.log(c))
    nz = np.abs(r[r != 0])
    if nz.size == 0:
        return pd.Series(out, index=close.index)
    mad = float(np.median(np.abs(nz - np.median(nz))))
    mad = mad if mad > 0 else float(np.median(nz))
    big = np.abs(r[:-1]) > R11["spike_mad_k"] * mad
    revert = (np.sign(r[1:]) == -np.sign(r[:-1])) & (np.abs(r[1:]) >= R11["spike_revert_frac"] * np.abs(r[:-1]))
    out[1:-1] = big & revert        # r[i] is the move into bar i + 1
    return pd.Series(out, index=close.index)


def stale_runs(ts: pd.Series, close: pd.Series, minutes: float | None = None) -> int:
    """Runs of an unchanged close spanning more than `minutes` across consecutive bars of one session day."""
    limit = pd.Timedelta(minutes=minutes if minutes is not None else R11["stale_minutes"])
    t = pd.DatetimeIndex(pd.to_datetime(ts, utc=True))
    day = session_date(t)
    c = close.to_numpy()
    new_run = np.r_[True, (c[1:] != c[:-1]) | (day[1:] != day[:-1])]
    run_id = np.cumsum(new_run)
    g = pd.Series(t).groupby(run_id)
    span = g.max() - g.min()
    return int((span > limit).sum())


def day_of(ts: pd.Series | pd.DatetimeIndex, weekend: bool = False) -> pd.DatetimeIndex:
    """The session day a bar starting at `ts` belongs to: daily.session_date (21:00 UTC close, weekend rolled to
    Monday); for a 7-day instrument (`weekend: true`) the UTC calendar date (crypto's canonical day, D15)."""
    if weekend:
        return pd.DatetimeIndex(pd.to_datetime(ts, utc=True)).tz_localize(None).normalize()
    return session_date(ts)


def _excluded_day(d: pd.Timestamp) -> bool:
    return (d.month, d.day) in ((12, 25), (1, 1))


def daily_from_minutes(bars: pd.DataFrame, weekend: bool = False) -> pd.DataFrame:
    """1m bid bars (ts_utc, bid_open/high/low/close) -> per session_date: high, low, close, minutes, flat."""
    t = pd.DatetimeIndex(pd.to_datetime(bars["ts_utc"], utc=True))
    f = pd.DataFrame({"date": day_of(t, weekend), "high": bars["bid_high"].to_numpy(), "low": bars["bid_low"].to_numpy(),
                      "close": bars["bid_close"].to_numpy(),
                      "flat": ((bars["bid_open"] == bars["bid_high"]) & (bars["bid_high"] == bars["bid_low"])
                               & (bars["bid_low"] == bars["bid_close"])).to_numpy()})
    g = f.groupby("date", sort=True)
    return pd.DataFrame({"high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last(),
                         "minutes": g.size(), "flat": g["flat"].sum()})


def daily_from_hourly(ref: pd.DataFrame, weekend: bool = False) -> pd.DataFrame:
    """Reference hourly (ts_utc, high, low, close) -> per session_date: high, low, close, hours."""
    t = pd.DatetimeIndex(pd.to_datetime(ref["ts_utc"], utc=True))
    f = pd.DataFrame({"date": day_of(t, weekend), "high": ref["high"].to_numpy(), "low": ref["low"].to_numpy(),
                      "close": ref["close"].to_numpy()})
    g = f.groupby("date", sort=True)
    return pd.DataFrame({"high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last(),
                         "hours": g.size()})


class MonthGrade(FrozenRecord):
    key: str
    month: str
    tier: Literal["A", "B", "C", "F"]
    reasons: list[str]
    bars: int
    days: int
    coverage_median: float | None
    thin_day_share: float | None
    flat_share: float | None
    stale_runs: int
    spikes: int
    range_day_share: float | None
    close_day_share: float | None
    reference: Literal["native", "synthetic", "none"]
    r11_id: str = R11_ID


def grade_month(key: str, month: str, bars: pd.DataFrame, ref_daily: pd.DataFrame | None, session_minutes: int,
                reference_native: bool, weekend: bool = False, quarantined: bool = False) -> MonthGrade:
    """One instrument-month's R11 tier. `bars`: the month's 1m bid bars BEFORE spike quarantine (the spike rate is a
    check on the raw feed). `ref_daily`: daily_from_hourly of the reference for at least these session dates."""
    reasons: list[str] = []
    n = len(bars)
    spikes = int(spike_mask(bars["bid_close"]).sum()) if n else 0
    stale = stale_runs(bars["ts_utc"], bars["bid_close"]) if n else 0
    d = daily_from_minutes(bars, weekend) if n else pd.DataFrame(columns=["high", "low", "close", "minutes", "flat"])
    if not weekend:
        d = d[pd.DatetimeIndex(d.index).dayofweek < 5]
    keep = [not _excluded_day(pd.Timestamp(x)) for x in d.index]
    d = d[keep]
    ref = None
    if ref_daily is not None and len(ref_daily):
        ref = ref_daily.reindex(d.index)
        thin_ref = ref["hours"].fillna(0) < R11["thin_day_coverage"] * session_minutes / 60.0
        d, ref = d[~thin_ref.to_numpy()], ref[~thin_ref.to_numpy()]
    cov = (d["minutes"] / session_minutes).clip(upper=1.0) if len(d) else pd.Series(dtype=float)
    cov_med = float(cov.median()) if len(cov) else None
    thin = float((cov < R11["thin_day_coverage"]).mean()) if len(cov) else None
    flat = float(d["flat"].sum() / max(int(d["minutes"].sum()), 1)) if len(d) else None
    range_share = close_share = None
    kind: Literal["native", "synthetic", "none"] = "none"
    if ref is not None and ref["close"].notna().any():
        kind = "native" if reference_native else "synthetic"
        ok = ref["close"].notna()
        bp = np.abs(np.log(d.loc[ok, "close"] / ref.loc[ok, "close"])) * 1e4
        close_share = float((bp <= R11["close_bp"]).mean())
        if reference_native:
            rr = (d.loc[ok, "high"] - d.loc[ok, "low"]) / (ref.loc[ok, "high"] - ref.loc[ok, "low"])
            rr = rr.replace([np.inf, -np.inf], np.nan).dropna()
            range_share = float(((rr >= R11["range_ratio_lo"]) & (rr <= R11["range_ratio_hi"])).mean()) if len(rr) \
                else None
    close_ok = (close_share is not None and close_share >= R11["close_day_share_min"]
                and stale <= R11["stale_runs_max"] and (spikes / max(n, 1)) <= R11["spike_rate_max"])
    if close_share is None:
        reasons.append("no_reference")
    elif close_share < R11["close_day_share_min"]:
        reasons.append(f"close_vs_ref {close_share:.3f}")
    if stale > R11["stale_runs_max"]:
        reasons.append(f"stale_runs {stale}")
    if spikes / max(n, 1) > R11["spike_rate_max"]:
        reasons.append(f"spikes {spikes}/{n}")
    range_ok = True
    if cov_med is None or cov_med < R11["coverage_median_min"]:
        range_ok = False
        reasons.append(f"coverage_median {cov_med}")
    if thin is None or thin > R11["thin_day_share_max"]:
        range_ok = False
        reasons.append(f"thin_days {thin}")
    if flat is None or flat > R11["flat_share_max"]:
        range_ok = False
        reasons.append(f"flat {flat}")
    if range_share is None:
        range_ok = False
        reasons.append("range_not_evaluable" if kind != "native" else "range_no_days")
    elif range_share < R11["range_day_share_min"]:
        range_ok = False
        reasons.append(f"range_vs_ref {range_share:.3f}")
    tier: Literal["A", "B", "C", "F"]
    if quarantined:
        tier = "F"
        reasons.insert(0, "clock_quarantine")
    elif n == 0:
        tier = "C"
        reasons.insert(0, "no_bars")
    else:
        tier = "A" if close_ok and range_ok else "B" if close_ok else "C"
    return MonthGrade(key=key, month=month, tier=tier, reasons=reasons, bars=n, days=len(d), coverage_median=cov_med,
                      thin_day_share=thin, flat_share=flat, stale_runs=stale, spikes=spikes,
                      range_day_share=range_share, close_day_share=close_share, reference=kind)


# ------------------------------------------------------------------------------------------------------ clock check
def hourly_close_at_offset(ts_ns: np.ndarray, close: np.ndarray, hours_ns: np.ndarray, offset_min: int) -> np.ndarray:
    """The close of each reference hour [H, H+60) taken from 1m bars shifted by `offset_min` minutes (stamp s counts as
    s + offset): the last bar with H <= s + offset < H + 60 min; NaN when the hour holds none. ts_ns sorted."""
    off = np.int64(offset_min) * 60_000_000_000
    hour = np.int64(3_600_000_000_000)
    j = np.searchsorted(ts_ns + off, hours_ns + hour, side="left") - 1
    out = np.full(len(hours_ns), np.nan)
    ok = j >= 0
    jj = j[ok]
    inside = (ts_ns[jj] + off) >= hours_ns[ok]
    vals = np.full(ok.sum(), np.nan)
    vals[inside] = close[jj[inside]]
    out[ok] = vals
    return out


class ClockCheck(FrozenRecord):
    key: str
    year: int
    offset_min: int | None
    corr_at_offset: float | None
    corr_at_zero: float | None
    hours: int
    agree_share: float | None
    agree_tol: float | None
    passed: bool
    reason: str


def clock_check(key: str, year: int, ts_ns: np.ndarray, close: np.ndarray, ref: pd.DataFrame) -> ClockCheck:
    """Offset (minutes) that maximises the correlation of hourly log returns between the 1m series (shifted) and the
    reference hourly closes; robust to a level basis (index CFD vs future). Pass: |offset| <= clock_offset_max_min.
    Also the D2 agreement: share of hours with |close - reference close| <= 2 x the median reference spread, at offset
    0 (reported; the clock decides quarantine). `ref`: ts_utc (hour start), close, spread (NaN allowed)."""
    if len(ref) < 200 or len(ts_ns) < 1000:
        return ClockCheck(key=key, year=year, offset_min=None, corr_at_offset=None, corr_at_zero=None, hours=len(ref),
                          agree_share=None, agree_tol=None, passed=False, reason="no_reference")
    hours = epoch_ns_utc(ref["ts_utc"])
    rc = ref["close"].to_numpy(dtype="float64")
    span = int(R11["clock_search_min"])
    corr: dict[int, float] = {}
    for k in range(-span, span + 1):
        hc = hourly_close_at_offset(ts_ns, close, hours, k)
        a, b = np.diff(np.log(hc)), np.diff(np.log(rc))
        consecutive = np.diff(hours) == 3_600_000_000_000
        m = consecutive & np.isfinite(a) & np.isfinite(b)
        if m.sum() < 100:
            continue
        corr[k] = float(np.corrcoef(a[m], b[m])[0, 1])
    if not corr:
        return ClockCheck(key=key, year=year, offset_min=None, corr_at_offset=None, corr_at_zero=None,
                          hours=len(ref), agree_share=None, agree_tol=None, passed=False, reason="no_overlap")
    best = max(corr, key=lambda k: corr[k])
    h0 = hourly_close_at_offset(ts_ns, close, hours, 0)
    spread = (ref["spread"].astype("float64") if "spread" in ref else pd.Series(np.nan, index=ref.index)).to_numpy()
    med = float(np.nanmedian(spread)) if np.isfinite(spread).any() else float("nan")
    ok = np.isfinite(h0) & np.isfinite(rc)
    agree = tol = None
    if np.isfinite(med) and med > 0 and ok.any():
        tol = R11["h1_agree_spread_mult"] * med
        agree = float((np.abs(h0[ok] - rc[ok]) <= tol).mean())
    passed = abs(best) <= R11["clock_offset_max_min"]
    return ClockCheck(key=key, year=year, offset_min=best, corr_at_offset=corr[best], corr_at_zero=corr.get(0),
                      hours=int(ok.sum()), agree_share=agree, agree_tol=tol, passed=passed,
                      reason="ok" if passed else f"offset {best} min")


def epoch_ns_utc(ts: pd.Series | pd.DatetimeIndex) -> np.ndarray:
    return np.asarray(epoch_ns(pd.DatetimeIndex(pd.to_datetime(ts, utc=True))), dtype="int64")
