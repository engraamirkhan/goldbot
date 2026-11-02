"""Financing by date and the venue scenarios (amendment W3.3).

W3.3: "Each rollover night charges or credits |notional| x rate_side(d) / 365, with the broker's triple day (Wednesday
for ICM XAUUSD). rate_long(d) = DTB3(d) + m_long. Longs pay. rate_short(d) = DTB3(d) - m_short, credited to shorts.
If it is negative, the short pays. DTB3 is the FRED 3-month T-bill, asof_join on available_utc, published the day
after its observation date." Markups: XAUUSD 0.74 %/yr each side (swap-icm-2026-11-02); "any symbol without measured
terms uses a markup of 2.5%/yr each side"; FX uses the 3-month rate differential of its two currencies.

Venues: "(A) ICM CFD, as above. This is the gating scenario. Reported only, never gated: (B) UK gold ETC. Long/flat
only, 0.12%/yr fee, no financing, round trip = C1 point RT + 5 bp. (C) CME futures. Long pays DTB3 and short earns
DTB3, with no markup. The round trip is the C1 point RT plus $2.00/oz a side for 1OZ, plus one round trip at each
monthly roll."

The position held at a day's 21:00 UTC close carries that close's rollover (the fill is at the next 1m open, after it).
Rollover nights per close: 1 on a weekday, 3 on the triple day, none on Saturday or Sunday, so a week sums to 7
calendar nights and the rate is divided by 365.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord
from goldbot.config import PositionSettings
from goldbot.data.store import asof_join


def rollover_nights(prev_dates: pd.DatetimeIndex, dates: pd.DatetimeIndex, *, triple_weekday: int = 2) -> np.ndarray:
    """Nights charged between consecutive closes: the rollovers at the closes of every calendar date in
    [prev_date, date), 1 a weekday, 3 on `triple_weekday` (0 = Monday), 0 Saturday and Sunday."""
    d0 = pd.DatetimeIndex(prev_dates).to_numpy().astype("datetime64[D]")
    d1 = pd.DatetimeIndex(dates).to_numpy().astype("datetime64[D]")
    mask = ["0"] * 7
    mask[triple_weekday] = "1"
    return (np.busday_count(d0, d1, weekmask="1111100") + 2 * np.busday_count(d0, d1, weekmask="".join(mask))).astype(int)


def rate_frame(obs: pd.DataFrame, *, value_col: str = "rate_pct") -> pd.DataFrame:
    """A FRED daily series (date, value in % a year) -> (available_utc, rate_pct): an observation is usable from the
    21:00 UTC close of the day after its date (W3.3: "published the day after its observation date")."""
    dates = pd.DatetimeIndex(pd.to_datetime(obs["date"]))
    dates = dates.tz_convert(None) if dates.tz is not None else dates
    avail = (dates.normalize() + pd.Timedelta(days=1, hours=21)).tz_localize("UTC")
    out = pd.DataFrame({"available_utc": avail, "rate_pct": pd.to_numeric(obs[value_col], errors="coerce").to_numpy()})
    return out.dropna().sort_values("available_utc").reset_index(drop=True)


def fx_differential(quote: pd.DataFrame, base: pd.DataFrame) -> pd.DataFrame:
    """BASE/QUOTE financing rate (rate frames, % a year): quote minus base, as of each publication. A long pair holds
    the base and borrows the quote, so it pays quote - base (+ markup); a short earns it (- markup)."""
    times = pd.DataFrame({"ts_utc": pd.DatetimeIndex(sorted(set(quote["available_utc"]) | set(base["available_utc"])))})
    q = asof_join(times, quote, prefix="q_")
    b = asof_join(times, base, prefix="b_")
    out = pd.DataFrame({"available_utc": times["ts_utc"], "rate_pct": q["q_rate_pct"] - b["b_rate_pct"]})
    return out.dropna().reset_index(drop=True)


class Rates(FrozenRecord):
    """The financing reference rate by date: DTB3 (`series`), a per-symbol override (FX differentials) and the
    constant used before the first publication or when no series is on disk (`uses_constant`)."""
    series: pd.DataFrame | None = None                         # available_utc, rate_pct
    per_symbol: dict[str, pd.DataFrame] = Field(default_factory=dict)
    constant_pct: float = 4.02

    @property
    def uses_constant(self) -> bool:
        return self.series is None

    def pct(self, symbol: str, at_utc: pd.DatetimeIndex) -> np.ndarray:
        """% a year as of each instant (`asof_join` on available_utc); the constant where nothing is published yet."""
        frame = self.per_symbol.get(symbol, self.series)
        if frame is None or len(at_utc) == 0:
            return np.full(len(at_utc), self.constant_pct)
        left = pd.DataFrame({"ts_utc": pd.DatetimeIndex(at_utc), "_pos": np.arange(len(at_utc))})
        got = asof_join(left, frame[["available_utc", "rate_pct"]]).sort_values("_pos")
        return np.asarray(got["rate_pct"].fillna(self.constant_pct).to_numpy(dtype=float))


class Venue(FrozenRecord):
    """One W3.3 venue scenario: what a rollover night and a round trip cost on it."""
    name: str
    gates: bool                                   # only scenario A gates a result
    use_short_rate: bool                          # the reference rate enters rate_long / rate_short
    markup_long_pct: float = Field(ge=0)          # default markups, % a year (B: the ETC fee)
    markup_short_pct: float = Field(ge=0)
    markups: dict[str, tuple[float, float]] = Field(default_factory=dict)   # measured (long, short) by symbol
    extra_rt_bps: float = Field(0.0, ge=0)        # added to the C1 point round trip
    long_only: bool = False                       # B: a short exposure is refused
    monthly_roll: bool = False                    # C: one round trip on the held position at each month's last day
    triple_weekday: int = Field(2, ge=0, le=4)

    def markup(self, symbol: str) -> tuple[float, float]:
        return self.markups.get(symbol, (self.markup_long_pct, self.markup_short_pct))

    def financing_pnl(self, symbol: str, notional: np.ndarray, rate_pct: np.ndarray, nights: np.ndarray) -> np.ndarray:
        """P&L (units of equity, credit positive) of `notional` (signed, held over the rollovers) for `nights`."""
        ml, ms = self.markup(symbol)
        r = rate_pct if self.use_short_rate else np.zeros_like(rate_pct)
        per_night = np.asarray(nights, dtype=float) / 365.0 / 100.0
        n = np.asarray(notional, dtype=float)
        return np.asarray(-np.maximum(n, 0.0) * (r + ml) * per_night + np.maximum(-n, 0.0) * (r - ms) * per_night)


def venue_a(*, markup_long_pct: float = 2.5, markup_short_pct: float = 2.5,
            markups: dict[str, tuple[float, float]] | None = None, triple_weekday: int = 2) -> Venue:
    return Venue(name="A ICM CFD", gates=True, use_short_rate=True, markup_long_pct=markup_long_pct,
                 markup_short_pct=markup_short_pct, markups=dict(markups or {}), triple_weekday=triple_weekday)


def venue_b(*, fee_pct: float = 0.12, extra_rt_bps: float = 5.0) -> Venue:
    return Venue(name="B UK ETC", gates=False, use_short_rate=False, markup_long_pct=fee_pct, markup_short_pct=0.0,
                 extra_rt_bps=extra_rt_bps, long_only=True)


def venue_c(*, fee_usd_per_side: float = 2.0, reference_price: float = 4000.0) -> Venue:
    return Venue(name="C CME futures", gates=False, use_short_rate=True, markup_long_pct=0.0, markup_short_pct=0.0,
                 extra_rt_bps=2 * fee_usd_per_side / reference_price * 1e4, monthly_roll=True)


def venue_a_from_settings(s: PositionSettings) -> Venue:
    return venue_a(markup_long_pct=s.default_markup_pct, markup_short_pct=s.default_markup_pct,
                   markups=dict(s.markup_pct_by_symbol), triple_weekday=s.triple_weekday)


def venues_from_settings(s: PositionSettings, *, long_only: bool) -> dict[str, Venue]:
    """A always, C always, B for long/flat specs only (POS-1 section 1)."""
    out = {"A": venue_a_from_settings(s)}
    if long_only:
        out["B"] = venue_b(fee_pct=s.etc_fee_pct, extra_rt_bps=s.etc_extra_rt_bps)
    out["C"] = venue_c(fee_usd_per_side=s.cme_fee_usd_per_side, reference_price=s.cost_reference_price)
    return out
