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

Measurement definitions (fixed 2026-11-25 after a smoke test of two instrument-years, before the full grade run; the
thresholds did not change): a month is the month of the session day (a bar after 21:00 UTC on the 31st belongs to the
next month's first day); MAD is the median absolute deviation of the month's nonzero SIGNED 1m log returns; a stale run
breaks at a gap longer than the stale limit.

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
    """One HistData code over an era. `to` (YYYY or YYYY-MM, inclusive) ends it: HistData reuses a code for another
    series (UDXUSD is the Dow to 2018 and the US dollar index from 2019; GRXEUR leaves DAX levels after 2020-06),
    found by the era bands on 2026-11-25. Minutes after `to` are dropped and counted (out_of_era)."""

    code: str
    from_: int = Field(alias="from")
    to: int | str | None = None

    def covers(self, year: int) -> bool:
        return self.from_ <= year and (self.to is None or year <= int(str(self.to)[:4]))

    def end(self) -> pd.Timestamp | None:
        """Exclusive UTC end of the era (None: open)."""
        if self.to is None:
            return None
        return (_period(self.to, True) + 1).to_timestamp().tz_localize("UTC")


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


class FixSpec(FrozenRecord):
    """The independent daily fix a same-source year's closes are checked against (quant ruling 1, r11-v2).
    series: FRED ids -> exponent (value = prod(series ^ exponent), all observed at `at`, so a cross is exact);
    cap: "B" when the only official fix carries a CFD basis (cash index close, futures settlement); pending: why the
    fix is not available yet (the year grades P, never usable, until it is)."""

    series: dict[str, float] = Field(default_factory=dict)
    at: str | None = None            # "HH:MM Area/City", the fix's own timestamp
    cap: Literal["B"] | None = None
    pending: str | None = None
    why: str | None = None

    @model_validator(mode="after")
    def _one(self) -> FixSpec:
        if sum([bool(self.series), self.cap is not None, self.pending is not None]) != 1:
            raise ValueError("a fix is exactly one of: series (+ at), cap, pending")
        if self.series and not self.at:
            raise ValueError("a fix series needs its timestamp `at`")
        return self


class DstEra(FrozenRecord):
    from_: int = Field(alias="from", default=0)
    to: int | None = None
    rule: Literal["ny", "eet-7"]


class HistorySource(FrozenRecord):
    owner_name: str | None = None
    asset_class: Literal["fx", "metal", "index", "energy", "crypto", "dollar_index"]
    cfd_type: Literal["spot", "cash_index", "rolling_future", "synthetic_cross"]
    roll: str | None
    weekend: bool
    session_minutes: int
    dukascopy: DukascopySource | None
    histdata: list[HistDataSource] | None
    source_tz: dict[str, str]
    price_bands: list[tuple[int | str, int | str, float, float]]
    reference: list[ReferenceEra]
    synthetic: str | None
    fix: FixSpec | None = None

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
        if self.dukascopy is None and not self.histdata and not self.synthetic:
            raise ValueError("no source and no synthetic recipe")
        return self

    def histdata_for(self, year: int) -> HistDataSource | None:
        return next((h for h in self.histdata or [] if h.covers(year)), None)

    def histdata_years(self, first: int, last: int) -> list[int]:
        return [y for y in range(first, last + 1) if self.histdata_for(y) is not None]

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
    histdata_dst: list[DstEra] = Field(default_factory=list)
    instruments: dict[str, HistorySource]

    def dst_rule(self, year: int) -> str:
        """The one HistData stamp rule of the source era holding `year` (quant ruling 2)."""
        for e in self.histdata_dst:
            if e.from_ <= year and (e.to is None or year <= e.to):
                return e.rule
        raise ValueError(f"no HistData DST era covers {year}")

    @model_validator(mode="after")
    def _cut(self) -> HistorySources:
        if pd.Timestamp(self.holdout_cut) != HOLDOUT_CUT:
            raise ValueError(f"holdout_cut {self.holdout_cut} differs from the coded {HOLDOUT_CUT} (quant R10)")
        return self


def load_history_sources(path: str | Path = DEFAULT_HISTORY_SOURCES) -> HistorySources:
    return HistorySources.model_validate(load_yaml(path))


@lru_cache(maxsize=1)
def history_sources() -> HistorySources:
    """The map, read once per process on first use (never at import: a broken map must not stop gold's weekly pull)."""
    return load_history_sources(DEFAULT_HISTORY_SOURCES)


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
    nz = r[r != 0]
    if nz.size == 0:
        return pd.Series(out, index=close.index)
    mad = float(np.median(np.abs(nz - np.median(nz))))      # MAD of the signed returns (not of |r|)
    mad = mad if mad > 0 else float(np.median(np.abs(nz)))
    big = np.abs(r[:-1] - np.median(nz)) > R11["spike_mad_k"] * mad
    revert = (np.sign(r[1:]) == -np.sign(r[:-1])) & (np.abs(r[1:]) >= R11["spike_revert_frac"] * np.abs(r[:-1]))
    out[1:-1] = big & revert        # r[i] is the move into bar i + 1
    return pd.Series(out, index=close.index)


def stale_runs(ts: pd.Series, close: pd.Series, minutes: float | None = None) -> int:
    """Runs of an unchanged close spanning more than `minutes` across consecutive bars of one session day. A run also
    breaks at a gap longer than `minutes` (a daily break or a hole is a coverage matter, not a repeated price)."""
    limit = pd.Timedelta(minutes=minutes if minutes is not None else R11["stale_minutes"])
    t = pd.DatetimeIndex(pd.to_datetime(ts, utc=True))
    day = session_date(t)
    c = close.to_numpy()
    gap = np.diff(epoch_ns(t)) > limit.value
    new_run = np.r_[True, (c[1:] != c[:-1]) | (day[1:] != day[:-1]) | gap]
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
                reference_native: bool, weekend: bool = False, quarantined: bool = False,
                clock_offset: int | None = None) -> MonthGrade:
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
    month_clock_bad = clock_offset is not None and abs(clock_offset) > R11["clock_offset_max_min"]
    if quarantined or month_clock_bad:
        tier = "F"
        reasons.insert(0, "clock_quarantine" if quarantined else f"clock_month offset {clock_offset} min")
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


def _rank(x: np.ndarray) -> np.ndarray:
    return pd.Series(x).rank().to_numpy()


def offset_profile(ts_ns: np.ndarray, close: np.ndarray, hours: np.ndarray, ref_close: np.ndarray,
                   min_pairs: int = 100) -> dict[int, float]:
    """offset (minutes) -> RANK correlation of consecutive-hour log returns, 1m series shifted vs reference. Rank,
    not Pearson: one news hour (SNB 2015-01-15, March 2020) dominates a Pearson correlation and moved its peak to
    -33..+61 min on years whose every month peaks at 0 (diagnosed 2026-11-25 before any tier was used)."""
    span = int(R11["clock_search_min"])
    b = np.diff(np.log(ref_close))
    consecutive = np.diff(hours) == 3_600_000_000_000
    out: dict[int, float] = {}
    for k in range(-span, span + 1):
        a = np.diff(np.log(hourly_close_at_offset(ts_ns, close, hours, k)))
        m = consecutive & np.isfinite(a) & np.isfinite(b)
        if m.sum() < min_pairs:
            continue
        out[k] = float(np.corrcoef(_rank(a[m]), _rank(b[m]))[0, 1])
    return out


def month_offsets(ts_ns: np.ndarray, close: np.ndarray, ref: pd.DataFrame, weekend: bool = False) -> dict[str, int]:
    """Best offset per month (YYYY-MM of the session day): a partial-year shift (a DST rule applied on the wrong
    dates, a feed switch) hides inside a year-level peak at 0. Months with fewer than 50 hour pairs are left out."""
    t = pd.DatetimeIndex(pd.to_datetime(ref["ts_utc"], utc=True))
    month = day_of(t, weekend).to_period("M").astype(str)
    out: dict[str, int] = {}
    for mon in sorted(set(month)):
        sel = np.asarray(month == mon)
        r = ref.loc[sel]
        prof = offset_profile(ts_ns, close, epoch_ns_utc(r["ts_utc"]), r["close"].to_numpy(dtype="float64"),
                              min_pairs=50)
        if prof:
            out[mon] = max(prof, key=lambda k: prof[k])
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
    corr = offset_profile(ts_ns, close, hours, rc, min_pairs=100)
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


# =============================================================================================== R11 v2 (quant ruling)
# Re-ruled 2026-11-25 (review of PR #453) under four conditions: a new id; each change justified by a documented
# failure mechanism with example minutes; the v1 grades kept next to v2 with a diff table; v2 frozen before any label is
# built. v1 above stays as it was so the v1 grades stay reproducible. Mechanisms behind each change:
# * same_source: from 2019 HistData's hourly closes equal Dukascopy's on most hours (DE40 2024 and USINDEX 2019:
#   agreement 1.000, rank correlation 0.99999), so the close check against Dukascopy is circular. Detector: share of
#   hour-ending 1m closes exactly equal to the Dukascopy hourly close (rounded to the source point); >= 0.50 is the same
#   source. Same-source years are checked against an independent fix at the fix's own timestamp (FRED H.10 noon New
#   York for FX); their best grade is "A-consistent", reported apart from A. Instruments whose only fix would carry a
#   CFD basis (indices, oil, USDX) cap at B; gold's fix (LBMA PM) is pending (not reachable 2026-11-25).
# * spike v2: the v1 rule fired on genuine news minutes (EURUSD 2025: 4-5 a month, e.g. NFP and CPI minutes where
#   Dukascopy's own hourly high/low contains the print). A v1 candidate is confirmed only when its close lies outside
#   the reference hour's [low - tol, high + tol].
# * range v2: v1's daily range ratio [0.90, 1.02] against a second feed failed months whose highs and lows agree to a
#   spread (gold 2010: 41-87 % of days), and could pass offsetting errors (a high too high and a low too high give a
#   ratio near 1). v2 compares each extreme separately, with a compression guard on the monthly median ratio.
# * DST: one stamp rule per source era, decided by majority across instruments on the US/EU gap weeks only (the
#   v1-era per-month fit let a 2-week gap mixture land at offset 0 in 6/12 synthetic seeds), plus a per-gap-week clock
#   check (offset 0 +-1 min in every gap week, else the instrument-year is F).
R11_V2_ID = "r11-v2-2026-11-25"
R11_V2: dict[str, float] = {
    **{k: R11[k] for k in ("coverage_median_min", "thin_day_coverage", "thin_day_share_max", "flat_share_max",
                           "stale_minutes", "stale_runs_max", "spike_mad_k", "spike_revert_frac", "spike_rate_max",
                           "close_bp", "close_day_share_min", "clock_offset_max_min", "clock_search_min")},
    "same_source_share": 0.50,
    "fix_bp": 10.0,
    "fix_day_share_min": 0.98,
    "fix_bar_max_age_min": 5.0,      # the 1m bar used for a fix must close within 5 min before the fix time
    "spike_tol_spread_mult": 2.0,    # tol = max(2 x median reference spread of the month, 2 bp of price)
    "spike_tol_bp": 2.0,
    "spike_boundary_min": 2.0,       # a candidate within 2 min of an hour boundary is checked against both hours
    "range2_tol_spread_mult": 3.0,   # tol = max(3 x median reference spread, 0.05 x median reference daily range)
    "range2_tol_range_frac": 0.05,
    "range2_day_share_min": 0.90,
    "range2_ratio_lo": 0.90,         # compression guard: monthly median of R / R_ref
    "range2_ratio_hi": 1.10,
    "gap_week_offset_max": 1.0,
    "gap_week_min_pairs": 20.0,
}


def gap_windows(year: int) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """UTC [start, end) windows of `year` in which New York time and EET-7 differ (the weeks between the US and EU
    daylight-saving switches). Derived from the tz database, not hard-coded dates."""
    hours = pd.date_range(f"{year}-01-01", f"{year + 1}-01-01", freq="h", tz="UTC", inclusive="left")
    ny = hours.tz_convert("America/New_York").tz_localize(None)
    eet7 = hours.tz_convert("Europe/Helsinki").tz_localize(None) - pd.Timedelta(hours=7)
    diff = np.asarray(ny != eet7)
    out: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    i = 0
    while i < len(diff):
        if diff[i]:
            j = i
            while j < len(diff) and diff[j]:
                j += 1
            out.append((hours[i], hours[j - 1] + pd.Timedelta(hours=1)))
            i = j
        else:
            i += 1
    return out


def gap_week_offsets(ts_ns: np.ndarray, close: np.ndarray, ref: pd.DataFrame, year: int) -> dict[str, int | None]:
    """Best clock offset (rank correlation, +-120 min) inside each gap window: window start date -> offset, None when
    the window holds fewer than gap_week_min_pairs hour pairs (market shut, no reference)."""
    t = pd.DatetimeIndex(pd.to_datetime(ref["ts_utc"], utc=True))
    out: dict[str, int | None] = {}
    for a, b in gap_windows(year):
        sel = np.asarray((t >= a) & (t < b))
        r = ref.loc[sel]
        prof = offset_profile(ts_ns, close, epoch_ns_utc(r["ts_utc"]), r["close"].to_numpy(dtype="float64"),
                              min_pairs=int(R11_V2["gap_week_min_pairs"])) if sel.sum() else {}
        out[str(a.date())] = max(prof, key=lambda k: prof[k]) if prof else None
    return out


def gap_week_score(ts_ns: np.ndarray, close: np.ndarray, ref: pd.DataFrame, year: int) -> float | None:
    """Rank correlation at offset 0 over the gap-window hours only (the hours where the two stamp rules differ)."""
    t = pd.DatetimeIndex(pd.to_datetime(ref["ts_utc"], utc=True))
    sel = np.zeros(len(t), dtype=bool)
    for a, b in gap_windows(year):
        sel |= np.asarray((t >= a) & (t < b))
    if sel.sum() < R11_V2["gap_week_min_pairs"]:
        return None
    r = ref.loc[sel]
    hours = epoch_ns_utc(r["ts_utc"])
    a_ = np.diff(np.log(hourly_close_at_offset(ts_ns, close, hours, 0)))
    b_ = np.diff(np.log(r["close"].to_numpy(dtype="float64")))
    m = (np.diff(hours) == 3_600_000_000_000) & np.isfinite(a_) & np.isfinite(b_)
    if m.sum() < R11_V2["gap_week_min_pairs"]:
        return None
    return float(np.corrcoef(_rank(a_[m]), _rank(b_[m]))[0, 1])


def same_source_share(ts_ns: np.ndarray, close: np.ndarray, ref: pd.DataFrame, point: float) -> float | None:
    """Share of reference hours whose Dukascopy close equals, to the source point, the close of the last 1m bar of the
    hour. The hour-ending minute is the one 1m close the hourly cache carries, so it is the exact-equality sample."""
    if not len(ref):
        return None
    hc = hourly_close_at_offset(ts_ns, close, epoch_ns_utc(ref["ts_utc"]), 0)
    rc = ref["close"].to_numpy(dtype="float64")
    ok = np.isfinite(hc) & np.isfinite(rc)
    if ok.sum() < 100:
        return None
    return float((np.round(hc[ok] * point) == np.round(rc[ok] * point)).mean())


def spike_confirmed(bars: pd.DataFrame, candidates: pd.Series, ref: pd.DataFrame) -> pd.Series:
    """v2: a v1 candidate is confirmed when its close lies outside [ref_low - tol, ref_high + tol] of the reference
    hour holding it (the union of both hours within spike_boundary_min of a boundary); tol = max(2 x the month's median
    reference spread, 2 bp of price). Synthetic references carry leg bounds (high = prod of leg highs for positive
    exponents and leg lows for negative ones), so a cross print outside every leg combination is confirmed. A candidate
    whose hour has no reference stays unconfirmed (the month then fails on no_reference anyway)."""
    out = pd.Series(False, index=bars.index)
    idx = candidates[candidates].index
    if not len(idx) or not len(ref):
        return out
    r = ref.set_index(pd.DatetimeIndex(pd.to_datetime(ref["ts_utc"], utc=True)))
    spread = r["spread"].astype("float64") if "spread" in r else pd.Series(np.nan, index=r.index)
    month = pd.DatetimeIndex(r.index).tz_localize(None).to_period("M")
    med_spread = spread.groupby(month).median()
    edge = pd.Timedelta(minutes=R11_V2["spike_boundary_min"])
    ts_all = pd.DatetimeIndex(pd.to_datetime(bars["ts_utc"], utc=True))
    closes = bars["bid_close"].to_numpy(dtype="float64")
    pos = {lab: k for k, lab in enumerate(bars.index)}
    for i in idx:
        t = ts_all[pos[i]]
        h = t.floor("h")
        hrs = [h]
        if t - h < edge:
            hrs.append(h - pd.Timedelta(hours=1))
        if h + pd.Timedelta(hours=1) - t <= edge:
            hrs.append(h + pd.Timedelta(hours=1))
        got = r.loc[r.index.isin(hrs)]
        if got.empty:
            continue
        c = float(closes[pos[i]])
        ms = med_spread.get(t.tz_localize(None).to_period("M"), np.nan)
        tol = max(R11_V2["spike_tol_spread_mult"] * (ms if np.isfinite(ms) else 0.0), R11_V2["spike_tol_bp"] * 1e-4 * c)
        out.at[i] = c > float(got["high"].max()) + tol or c < float(got["low"].min()) - tol
    return out


def combine_legs(legs: dict[str, pd.DataFrame], exps: dict[str, float], scale: float = 1.0) -> pd.DataFrame:
    """Hourly reference of a cross from its legs (each indexed by ts_utc with high, low, close, spread):
    close = scale x prod(close^e); high/low are the BOUNDS a true cross high/low cannot exceed (leg high for a positive
    exponent, leg low for a negative one, and the reverse for the low); spread propagates in relative terms.
    The bounds are what spike v2 checks against; they are not the cross's high/low (no range check on them)."""
    h: pd.DataFrame | None = None
    for c, e in exps.items():
        v = legs[c]
        hi, lo = (v["high"], v["low"]) if e > 0 else (v["low"], v["high"])
        part = pd.DataFrame({"close": v["close"] ** e, "high": hi ** e, "low": lo ** e,
                             "rel": abs(e) * v["spread"] / v["close"]})
        h = part if h is None else pd.DataFrame({
            "close": h["close"] * part["close"], "high": h["high"] * part["high"], "low": h["low"] * part["low"],
            "rel": h["rel"] + part["rel"]}).dropna(subset=["close"])
    assert h is not None
    for c in ("close", "high", "low"):
        h[c] = h[c] * scale
    h["spread"] = h["rel"] * h["close"]
    return h.drop(columns="rel").rename_axis("ts_utc")


def range_v2(d: pd.DataFrame, ref_daily: pd.DataFrame, ref_month: pd.DataFrame) -> tuple[float | None, float | None]:
    """(share of days whose high AND low each lie within tol of the reference's, monthly median of R / R_ref).
    tol = max(3 x the month's median reference spread, 0.05 x the month's median reference daily range)."""
    ref = ref_daily.reindex(d.index)
    ok = ref["high"].notna() & ref["low"].notna()
    if not ok.any():
        return None, None
    rr = (ref.loc[ok, "high"] - ref.loc[ok, "low"])
    sp = float(np.nanmedian(ref_month["spread"].to_numpy(dtype="float64"))) if "spread" in ref_month and \
        ref_month["spread"].notna().any() else 0.0
    tol = max(R11_V2["range2_tol_spread_mult"] * sp, R11_V2["range2_tol_range_frac"] * float(rr.median()))
    hit = ((d.loc[ok, "high"] - ref.loc[ok, "high"]).abs() <= tol) & ((d.loc[ok, "low"] - ref.loc[ok, "low"]).abs() <= tol)
    ratio = ((d.loc[ok, "high"] - d.loc[ok, "low"]) / rr).replace([np.inf, -np.inf], np.nan).dropna()
    return float(hit.mean()), (float(ratio.median()) if len(ratio) else None)


def fix_close_share(bars: pd.DataFrame, fix: pd.DataFrame) -> tuple[float | None, int]:
    """(share of fix days whose 1m close, of the bar closing at or before the fix time and at most fix_bar_max_age_min
    earlier, lies within fix_bp of the fix; days compared). `fix`: fix_utc (tz-aware), value."""
    if not len(fix) or not len(bars):
        return None, 0
    close_t = epoch_ns_utc(bars["ts_utc"]) + 60_000_000_000            # a bar is known at its close
    f_t = epoch_ns_utc(fix["fix_utc"])
    j = np.searchsorted(close_t, f_t, side="right") - 1
    ok = (j >= 0)
    age = np.full(len(f_t), np.inf)
    age[ok] = (f_t[ok] - close_t[j[ok]]) / 60e9
    use = ok & (age <= R11_V2["fix_bar_max_age_min"])
    if not use.any():
        return None, 0
    c = bars["bid_close"].to_numpy(dtype="float64")[j[use]]
    bp = np.abs(np.log(c / fix["value"].to_numpy(dtype="float64")[use])) * 1e4
    return float((bp <= R11_V2["fix_bp"]).mean()), int(use.sum())


def touch_extremes(bars: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """High and low a label factory may use for barrier touches: bars flagged as spikes (v1 candidates or v2
    confirmed, `dq_flag` starting with warning:spike) are left out (NaN), so a confirmed spike crossing a barrier never
    makes a touch (quant ruling 3)."""
    spike = bars.get("dq_flag", pd.Series("", index=bars.index)).astype(str).str.startswith("warning:spike")
    return bars["bid_high"].where(~spike), bars["bid_low"].where(~spike)


Tier2 = Literal["A", "A-consistent", "B", "C", "F", "P"]


class MonthGradeV2(FrozenRecord):
    key: str
    month: str
    tier: Tier2
    reasons: list[str]
    bars: int
    days: int
    same_source: bool
    coverage_median: float | None
    thin_day_share: float | None
    flat_share: float | None
    stale_runs: int
    spike_candidates: int
    spikes_confirmed: int
    range2_share: float | None
    range2_ratio_median: float | None
    close_day_share: float | None
    fix_share: float | None
    fix_days: int
    fix_status: str
    reference: Literal["native", "synthetic", "none"]
    rv: float | None
    r11_id: str = R11_V2_ID


def grade_month_v2(key: str, month: str, bars: pd.DataFrame, ref: pd.DataFrame | None, session_minutes: int,
                   reference_native: bool, *, weekend: bool = False, quarantined: bool = False,
                   clock_offset: int | None = None, same_source: bool = False, fix: pd.DataFrame | None = None,
                   fix_status: str = "none") -> MonthGradeV2:
    """One instrument-month's R11 v2 tier. `ref`: the reference HOURLY bars of at least this month (ts_utc, high, low,
    close, spread). `fix_status`: "ok" (fix given), "cap_b" (only fix carries a CFD basis), "pending" (not fetched)."""
    reasons: list[str] = []
    n = len(bars)
    cand = pd.Series(False, index=bars.index)
    if n:
        cand = spike_mask(bars["bid_close"])
    conf = spike_confirmed(bars, cand, ref) if (ref is not None and len(ref)) else pd.Series(False, index=bars.index)
    stale = stale_runs(bars["ts_utc"], bars["bid_close"]) if n else 0
    d = daily_from_minutes(bars, weekend) if n else pd.DataFrame(columns=["high", "low", "close", "minutes", "flat"])
    if not weekend:
        d = d[pd.DatetimeIndex(d.index).dayofweek < 5]
    d = d[[not _excluded_day(pd.Timestamp(x)) for x in d.index]]
    ref_daily = daily_from_hourly(ref, weekend) if (ref is not None and len(ref)) else None
    rd = None
    if ref_daily is not None:
        rd = ref_daily.reindex(d.index)
        thin_ref = (rd["hours"].fillna(0) < R11_V2["thin_day_coverage"] * session_minutes / 60.0).to_numpy()
        d, rd = d[~thin_ref], rd[~thin_ref]
    cov = (d["minutes"] / session_minutes).clip(upper=1.0) if len(d) else pd.Series(dtype=float)
    cov_med = float(cov.median()) if len(cov) else None
    thin = float((cov < R11_V2["thin_day_coverage"]).mean()) if len(cov) else None
    flat = float(d["flat"].sum() / max(int(d["minutes"].sum()), 1)) if len(d) else None
    kind: Literal["native", "synthetic", "none"] = "none"
    close_share = r2_share = r2_ratio = None
    if rd is not None and rd["close"].notna().any():
        kind = "native" if reference_native else "synthetic"
        okc = rd["close"].notna()
        bp = np.abs(np.log(d.loc[okc, "close"] / rd.loc[okc, "close"])) * 1e4
        close_share = float((bp <= R11_V2["close_bp"]).mean())
        if reference_native and ref is not None:
            r2_share, r2_ratio = range_v2(d, ref_daily, ref) if ref_daily is not None else (None, None)
    fix_share, fix_days = fix_close_share(bars, fix) if fix is not None else (None, 0)
    lr = np.diff(np.log(bars["bid_close"].to_numpy(dtype="float64"))) if n > 2 else np.array([])
    rv = float(np.sqrt(np.sum(lr ** 2))) if len(lr) else None
    # close checks
    rate_ok = (int(conf.sum()) / max(n, 1)) <= R11_V2["spike_rate_max"] or same_source
    if same_source:
        if fix_status == "ok":
            close_ok_core = fix_share is not None and fix_share >= R11_V2["fix_day_share_min"]
            if not close_ok_core:
                reasons.append(f"fix_vs_close {fix_share}")
        elif fix_status == "cap_b":
            close_ok_core = close_share is not None and close_share >= R11_V2["close_day_share_min"]
        else:
            close_ok_core = False
            reasons.append("independent_fix_pending")
    else:
        close_ok_core = close_share is not None and close_share >= R11_V2["close_day_share_min"]
        if close_share is None:
            reasons.append("no_reference")
        elif not close_ok_core:
            reasons.append(f"close_vs_ref {close_share:.3f}")
    if stale > R11_V2["stale_runs_max"]:
        reasons.append(f"stale_runs {stale}")
    if not rate_ok:
        reasons.append(f"spikes_confirmed {int(conf.sum())}/{n}")
    close_ok = close_ok_core and stale <= R11_V2["stale_runs_max"] and rate_ok
    range_ok = True
    if cov_med is None or cov_med < R11_V2["coverage_median_min"]:
        range_ok = False
        reasons.append(f"coverage_median {cov_med}")
    if thin is None or thin > R11_V2["thin_day_share_max"]:
        range_ok = False
        reasons.append(f"thin_days {thin}")
    if flat is None or flat > R11_V2["flat_share_max"]:
        range_ok = False
        reasons.append(f"flat {flat}")
    if r2_share is None:
        range_ok = False
        reasons.append("range_not_evaluable" if kind != "native" else "range_no_days")
    else:
        if r2_share < R11_V2["range2_day_share_min"]:
            range_ok = False
            reasons.append(f"range2_extremes {r2_share:.3f}")
        if r2_ratio is None or not R11_V2["range2_ratio_lo"] <= r2_ratio <= R11_V2["range2_ratio_hi"]:
            range_ok = False
            reasons.append(f"range2_compression {r2_ratio}")
    tier: Tier2
    clock_bad = clock_offset is not None and abs(clock_offset) > R11_V2["clock_offset_max_min"]
    if quarantined or clock_bad:
        tier = "F"
        reasons.insert(0, "clock_quarantine" if quarantined else f"clock_month offset {clock_offset} min")
    elif n == 0:
        tier = "C"
        reasons.insert(0, "no_bars")
    elif same_source and fix_status == "pending" and stale <= R11_V2["stale_runs_max"]:
        tier = "P"                    # cannot be graded until the independent fix exists; never usable meanwhile
    else:
        tier = "A" if close_ok and range_ok else "B" if close_ok else "C"
        if same_source and tier == "A":
            tier = "A-consistent" if fix_status == "ok" else "B"
        if same_source and fix_status == "cap_b" and tier == "A-consistent":
            tier = "B"
    if same_source:
        reasons.append(f"same_source fix={fix_status}")
    return MonthGradeV2(key=key, month=month, tier=tier, reasons=reasons, bars=n, days=len(d), same_source=same_source,
                        coverage_median=cov_med, thin_day_share=thin, flat_share=flat, stale_runs=stale,
                        spike_candidates=int(cand.sum()), spikes_confirmed=int(conf.sum()), range2_share=r2_share,
                        range2_ratio_median=r2_ratio, close_day_share=close_share, fix_share=fix_share,
                        fix_days=fix_days, fix_status=fix_status, reference=kind, rv=rv)
