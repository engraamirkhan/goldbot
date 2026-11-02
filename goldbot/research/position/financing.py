"""Financing by date and the venue scenarios (amendment W3.3).

W3.3: "Each rollover night charges or credits |notional| x rate_side(d) / 365, with the broker's triple day (Wednesday
for ICM XAUUSD). rate_long(d) = DTB3(d) + m_long. Longs pay. rate_short(d) = DTB3(d) - m_short, credited to shorts.
If it is negative, the short pays. DTB3 is the FRED 3-month T-bill, asof_join on available_utc, published the day
after its observation date." Markups: XAUUSD 0.74 %/yr each side (swap-icm-2026-11-02); "any symbol without measured
terms uses a markup of 2.5%/yr each side"; "For FX, the 3-month rate differential of the two currencies comes from
FRED's OECD 3-month interbank series (monthly, asof_join)". A non-USD index is financed at its home currency's rate.

Rates are read from the data lane's FRED files (value_date, value, available_utc) and joined on their STORED
available_utc, never on the value date; rows at or after 2025-10-01 (either stamp) are dropped at read. A symbol whose
financing currency is not USD needs its own frame in `Rates.per_symbol` (`build_rates`), and any symbol the engine does
not know how to finance is refused (KeyError): it is never financed at the USD rate by default.

Venues: "(A) ICM CFD, as above. This is the gating scenario. Reported only, never gated: (B) UK gold ETC. Long/flat
only, 0.12%/yr fee, no financing, round trip = C1 point RT + 5 bp. (C) CME futures. Long pays DTB3 and short earns
DTB3, with no markup. The round trip is the C1 point RT plus $2.00/oz a side for 1OZ, plus one round trip at each
monthly roll." W3 addendum: (S) venue A at max(point RT, the measured spread at the fill minute), report only.

The position held at a day's 21:00 UTC close carries that close's rollovers. Rollover nights per close: 1 on a weekday,
3 on the symbol's triple day, none on Saturday or Sunday, so a week sums to 7 nights and the rate is divided by 365.
The triple day is per symbol and refused when missing (FX and metals Wednesday, indices often Friday).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord
from goldbot.config import PositionSettings
from goldbot.data.store import asof_join
from goldbot.research.position.windows import HOLDOUT_FROM, HOLDOUT_FROM_UTC, assert_before_holdout

# how each U_16 market is financed (POS-1 section 1): the USD bill, an FX pair's (base, quote), an index's home currency
USD_FINANCED = frozenset({"XAUUSD", "XAGUSD", "XPTUSD", "WTI", "US500", "US100"})
FX_PAIRS: dict[str, tuple[str, str]] = {"EURUSD": ("EUR", "USD"), "USDJPY": ("USD", "JPY"), "GBPUSD": ("GBP", "USD"),
                                        "AUDUSD": ("AUD", "USD"), "USDCAD": ("USD", "CAD"), "USDCHF": ("USD", "CHF"),
                                        "NZDUSD": ("NZD", "USD")}
INDEX_HOME: dict[str, str] = {"GER40": "EUR", "JP225": "JPY", "UK100": "GBP"}


def rollover_nights(prev_dates: pd.DatetimeIndex, dates: pd.DatetimeIndex, *, triple_weekday: int) -> np.ndarray:
    """Nights charged between consecutive closes: the rollovers at the closes of every calendar date in
    [prev_date, date), 1 a weekday, 3 on `triple_weekday` (0 = Monday), 0 Saturday and Sunday."""
    d0 = pd.DatetimeIndex(prev_dates).to_numpy().astype("datetime64[D]")
    d1 = pd.DatetimeIndex(dates).to_numpy().astype("datetime64[D]")
    mask = ["0"] * 7
    mask[triple_weekday] = "1"
    return (np.busday_count(d0, d1, weekmask="1111100") + 2 * np.busday_count(d0, d1, weekmask="".join(mask))).astype(int)


def fred_frame(df: pd.DataFrame) -> pd.DataFrame:
    """A data-lane FRED frame (value_date, value, available_utc) -> (available_utc, rate_pct), cut before the holdout on
    both stamps. The stored available_utc is kept as is: the publication lag is the data lane's measurement."""
    if "available_utc" not in df.columns:
        raise ValueError("a rate series without available_utc is refused (W3.3: asof_join on available_utc)")
    vd = pd.DatetimeIndex(pd.to_datetime(df["value_date"]))
    vd = vd.tz_convert(None) if vd.tz is not None else vd
    av = pd.DatetimeIndex(pd.to_datetime(df["available_utc"], utc=True))
    keep = np.asarray((vd < HOLDOUT_FROM) & (av < HOLDOUT_FROM_UTC))
    out = pd.DataFrame({"available_utc": av[keep], "rate_pct": pd.to_numeric(df["value"], errors="coerce").to_numpy()[keep]})
    out = out.dropna().sort_values("available_utc").reset_index(drop=True)
    assert_before_holdout(out["available_utc"], what="rate rows")
    return out


def load_fred(path: Path | str) -> pd.DataFrame:
    """`raw/daily/fred_<ID>.parquet`, filtered value_date < 2025-10-01 at read."""
    df = pd.read_parquet(path, columns=["value_date", "value", "available_utc"],
                         filters=[("value_date", "<", HOLDOUT_FROM)])
    return fred_frame(df)


def fx_differential(quote: pd.DataFrame, base: pd.DataFrame) -> pd.DataFrame:
    """BASE/QUOTE financing rate (rate frames, % a year): quote minus base, as of each publication of either. A long
    pair holds the base and borrows the quote, so it pays quote - base (+ markup); a short earns it (- markup)."""
    times = pd.DataFrame({"ts_utc": pd.DatetimeIndex(sorted(set(quote["available_utc"]) | set(base["available_utc"])))})
    q = asof_join(times, quote, prefix="q_")
    b = asof_join(times, base, prefix="b_")
    out = pd.DataFrame({"available_utc": times["ts_utc"], "rate_pct": q["q_rate_pct"] - b["b_rate_pct"]})
    return out.dropna().reset_index(drop=True)


class Rates(FrozenRecord):
    """The financing reference rate by date. USD-financed symbols read `series` (DTB3), or `constant_pct` before its
    first publication or when no series is on disk (`uses_constant`); every other symbol reads its own frame in
    `per_symbol` (an FX differential or an index's home rate) and is refused without one."""
    series: pd.DataFrame | None = None                         # available_utc, rate_pct
    per_symbol: dict[str, pd.DataFrame] = Field(default_factory=dict)
    constant_pct: float = 4.02

    @property
    def uses_constant(self) -> bool:
        return self.series is None

    def frame(self, symbol: str) -> pd.DataFrame | None:
        if symbol in self.per_symbol:
            return self.per_symbol[symbol]
        if symbol in USD_FINANCED:
            return self.series
        raise KeyError(f"no financing rate for {symbol}: FX and non-USD indices need Rates.per_symbol (build_rates)")

    def pct(self, symbol: str, at_utc: pd.DatetimeIndex) -> np.ndarray:
        """% a year as of each instant (`asof_join` on available_utc); the constant where nothing is published yet."""
        frame = self.frame(symbol)
        if frame is None or len(at_utc) == 0:
            return np.full(len(at_utc), self.constant_pct)
        left = pd.DataFrame({"ts_utc": pd.DatetimeIndex(at_utc), "_pos": np.arange(len(at_utc))})
        got = asof_join(left, frame[["available_utc", "rate_pct"]]).sort_values("_pos")
        return np.asarray(got["rate_pct"].fillna(self.constant_pct).to_numpy(dtype=float))


def build_rates(symbols: Iterable[str], usd: pd.DataFrame | None, currency: Mapping[str, pd.DataFrame], *,
                constant_pct: float = 4.02) -> Rates:
    """Rates for a universe: USD symbols on `usd` (DTB3); FX pairs on quote - base and non-USD indices on their home
    rate, from `currency` (ISO code -> rate frame; "USD" defaults to `usd`). A missing currency is refused."""
    cur = dict(currency)
    if usd is not None:
        cur.setdefault("USD", usd)
    per: dict[str, pd.DataFrame] = {}
    for s in symbols:
        if s in USD_FINANCED:
            continue
        if s in FX_PAIRS:
            need: tuple[str, ...] = FX_PAIRS[s]
        elif s in INDEX_HOME:
            need = (INDEX_HOME[s],)
        else:
            raise KeyError(f"no financing rule for {s}")
        missing = [c for c in need if c not in cur]
        if missing:
            raise KeyError(f"no {'/'.join(missing)} 3-month rate for {s}: refused, never financed at the USD rate")
        per[s] = fx_differential(cur[need[1]], cur[need[0]]) if len(need) == 2 else cur[need[0]]
    return Rates(series=usd, per_symbol=per, constant_pct=constant_pct)


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
    fill_spread: bool = False                     # S: round trip = max(point RT, the fill minute's measured spread)
    triple_weekday: dict[str, int] = Field(default_factory=dict)   # per symbol; a symbol not listed is refused

    def markup(self, symbol: str) -> tuple[float, float]:
        return self.markups.get(symbol, (self.markup_long_pct, self.markup_short_pct))

    def triple(self, symbol: str) -> int:
        if symbol not in self.triple_weekday:
            raise KeyError(f"no triple rollover day for {symbol} (research.position.triple_weekday_by_symbol)")
        return self.triple_weekday[symbol]

    def financing_pnl(self, symbol: str, notional: np.ndarray, rate_pct: np.ndarray, nights: np.ndarray) -> np.ndarray:
        """P&L (units of equity, credit positive) of `notional` (signed, held over the rollovers) for `nights`."""
        ml, ms = self.markup(symbol)
        r = rate_pct if self.use_short_rate else np.zeros_like(rate_pct)
        per_night = np.asarray(nights, dtype=float) / 365.0 / 100.0
        n = np.asarray(notional, dtype=float)
        return np.asarray(-np.maximum(n, 0.0) * (r + ml) * per_night + np.maximum(-n, 0.0) * (r - ms) * per_night)


DEFAULT_TRIPLE = {"XAUUSD": 2}


def venue_a(*, markup_long_pct: float = 2.5, markup_short_pct: float = 2.5,
            markups: dict[str, tuple[float, float]] | None = None,
            triple_weekday: Mapping[str, int] | None = None) -> Venue:
    return Venue(name="A ICM CFD", gates=True, use_short_rate=True, markup_long_pct=markup_long_pct,
                 markup_short_pct=markup_short_pct, markups=dict(markups or {}),
                 triple_weekday=dict(triple_weekday if triple_weekday is not None else DEFAULT_TRIPLE))


def venue_b(*, fee_pct: float = 0.12, extra_rt_bps: float = 5.0, triple_weekday: Mapping[str, int] | None = None) -> Venue:
    return Venue(name="B UK ETC", gates=False, use_short_rate=False, markup_long_pct=fee_pct, markup_short_pct=0.0,
                 extra_rt_bps=extra_rt_bps, long_only=True,
                 triple_weekday=dict(triple_weekday if triple_weekday is not None else DEFAULT_TRIPLE))


def venue_c(*, fee_usd_per_side: float = 2.0, reference_price: float = 4000.0,
            triple_weekday: Mapping[str, int] | None = None) -> Venue:
    return Venue(name="C CME futures", gates=False, use_short_rate=True, markup_long_pct=0.0, markup_short_pct=0.0,
                 extra_rt_bps=2 * fee_usd_per_side / reference_price * 1e4, monthly_roll=True,
                 triple_weekday=dict(triple_weekday if triple_weekday is not None else DEFAULT_TRIPLE))


def venue_a_from_settings(s: PositionSettings) -> Venue:
    return venue_a(markup_long_pct=s.default_markup_pct, markup_short_pct=s.default_markup_pct,
                   markups=dict(s.markup_pct_by_symbol), triple_weekday=s.triple_weekday_by_symbol)


def venues_from_settings(s: PositionSettings, *, long_only: bool) -> dict[str, Venue]:
    """A always (gates), S always (A at the fill minute's spread), B for long/flat specs only (POS-1 section 1), C."""
    a = venue_a_from_settings(s)
    out = {"A": a, "S": a.model_copy(update={"name": "S ICM CFD at the fill-minute spread", "gates": False,
                                              "fill_spread": True})}
    if long_only:
        out["B"] = venue_b(fee_pct=s.etc_fee_pct, extra_rt_bps=s.etc_extra_rt_bps, triple_weekday=s.triple_weekday_by_symbol)
    out["C"] = venue_c(fee_usd_per_side=s.cme_fee_usd_per_side, reference_price=s.cost_reference_price,
                       triple_weekday=s.triple_weekday_by_symbol)
    return out
