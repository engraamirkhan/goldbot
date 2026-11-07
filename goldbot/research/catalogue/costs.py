"""Catalogue cost model: EVAL-1 section 1 measured costs and the C2 financing sign
(docs/research/evaluation-audit-2026-11-07.md section (b); measured table config/catalogue_costs.yaml).

EVAL-1 section 1: "Spread and commission per instrument and session from measured broker data (MT5 `symbol_info` and
demo fills; the gold cost of record unchanged). ... Where no measurement exists, the current conservative fallback
stays (no cost is lowered without a measurement). Cost versions are pinned before the run."
C2: "Financing debit per night (positive = paid) = side x (r_quote - r_base or instrument carry) + markup_side.
markup_side >= 0 is the broker's measured markup for that side: `swap_long/short` minus the benchmark differential at
the measurement date, floored at 0. It is never a credit. Index CFDs on price series credit the long and debit the
short with the dividend yield, as of `available_utc`, or else use total-return series. Triple swap follows the
broker's reported weekday."

As implemented
  round trip   gold: the cost of record (GOLD_RT_USD, unchanged). Others, only when the spread sample covers at least
               MIN_MEASURED_DAYS (20) trading days on every broker it came from (quant ruling 2026-11-07: "a 3-5-day
               spread sample is NOT a pinned measurement; the fallback cost stays ... until 20 trading days have been
               measured, with the version and dates recorded"): max(measured rt_bps x price, the fill bar's Dukascopy
               spread + 2 x measured commission); rt_bps = median spread + 2 x commission per side (bps of price).
               Otherwise (fewer days, or no commission/spread): the frozen fallback, max(FALLBACK_RT (+ FX commission),
               the fill bar's spread). Swap terms are broker terms (symbol_info), not a sample: C2 uses them as is.
  overshoot    not measured outside gold: stays the fallback's (0.5 x the FALLBACK round trip; sim.overshoot)
  financing    %/yr of notional per night, / 365 (the broker convention of the measured file):
               benchmark  side x (r_quote - r_base) + markup_side; r asof the fill's `available_utc` (FRED, the same
                          series as the S11 detector); metals: base carry 0 (the lease rate is in the measured markup);
                          indices: base = dividend: no credit to the long (not measured: never lowered) and the short
                          is debited DIV_SHORT (a conservative assumption, not a measurement; DE40 is a total-return
                          index: 0)
               two_sided  futures-referenced CFDs (energies): no benchmark split is measured; each side pays the larger
                          of today's measured rate and half the two-sided markup (never today's carry as a credit)
               off        the broker charges no swap (USINDEX): 0
               missing    swap not measured (BTCUSD): the frozen fallback SWAP_FALLBACK per calendar night, both sides
  nights       rollovers at 21:00 UTC on Mon-Fri; the broker's triple day counts 3, Sat/Sun 0 (BTCUSD: every night 1)
  duplicates   an instrument measured on two brokers takes the larger cost of each field (stricter)
  fail closed  an instrument not in the table raises MissingCost; in a window model a benchmark rate unknown at the fill
               raises too. `default_model()` (synthetic tests only) sets every benchmark rate to 0."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from goldbot.research.catalogue.data import DAY, HOUR

TABLE_PATH = Path(__file__).resolve().parents[3] / "config" / "catalogue_costs.yaml"
ROLL_OFFSET = 21 * HOUR
GOLD_RT_USD = 0.166              # cost_of_record cor-2026-10-31.2, ICM all sessions, round trip incl. $4.0/lot/side
DAYS_PER_YEAR = 365.0            # measured file: "per night x 365 / (contract x mid)"
SWAP_FALLBACK = 2.5              # %/yr per side, the frozen fallback where no swap is measured
MIN_MEASURED_DAYS = 20           # a spread/commission measurement is pinned only from 20 trading days (quant ruling)
BENCH_SERIES = {"USD": "DTB3", "EUR": "IR3TIB01EZM156N", "JPY": "IR3TIB01JPM156N", "GBP": "IR3TIB01GBM156N",
                "AUD": "IR3TIB01AUM156N", "CAD": "IR3TIB01CAM156N", "CHF": "IR3TIB01CHM156N", "NZD": "IR3TIB01NZM156N"}
INDEX_CCY = {"US500": "USD", "USTEC": "USD", "US30": "USD", "DE40": "EUR", "UK100": "GBP", "JP225": "JPY"}
# index shorts pay the dividend (cash adjustments, not in swap: measured file comment). Not measured: conservative
# 2010-2017 averages, only ever a debit; DE40 (DAX) is a performance index, dividends are in the price.
DIV_SHORT = {"US500": 2.0, "USTEC": 1.1, "US30": 2.3, "DE40": 0.0, "UK100": 3.7, "JP225": 1.8}
METALS = ("XAU", "XAG", "XPT", "XPD")
WEEKDAYS = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}

# fallback round trip in price units = 3 x a typical raw spread (instruments.yaml max_spread_points x point) +
# commission: the frozen WS-3 harness values, kept where a field is not measured and for the stop overshoot
FALLBACK_RT = {
    "XAGUSD": 0.060, "XAUEUR": 1.50, "XAUJPY": 250.0, "XTIUSD": 0.09, "US500": 1.50, "USTEC": 4.50, "DE40": 3.0,
    "UK100": 3.0, "US30": 6.0, "USINDEX": 0.05, "BTCUSD": 20.0, "EURUSD": 0.00010, "GBPUSD": 0.00012,
    "AUDUSD": 0.00012, "NZDUSD": 0.00015, "USDJPY": 0.010, "USDCAD": 0.00015, "USDCHF": 0.00015,
    "AUDJPY": 0.016, "EURAUD": 0.00025, "GBPAUD": 0.00030, "EURGBP": 0.00012, "EURJPY": 0.015, "GBPJPY": 0.020,
}
FX_COMMISSION_FRAC = 7.0 / 100_000          # ICM $3.5/lot/side on 100k notional, as a fraction of notional
NON_FX6 = ("XAGUSD", "XAUEUR", "XAUJPY", "XTIUSD", "BTCUSD", "XBRUSD", "XNGUSD", "XPTUSD", "XAUAUD")


class MissingCost(LookupError):
    """An instrument or a benchmark rate the cost model has no value for: the run stops (fail closed)."""


def fallback_rt(inst: str, price: float) -> float:
    fb = FALLBACK_RT.get(inst, 0.0003 * price)
    if len(inst) == 6 and inst not in NON_FX6:
        fb += FX_COMMISSION_FRAC * price
    return fb


@dataclass(frozen=True)
class InstCost:
    rt_bps: float | None            # measured round trip (median spread + 2 x commission), bps of price
    commission_side_bps: float | None
    kind: str                       # benchmark | two_sided | off | missing
    long_pct: float | None          # measured today: long pays %/yr (negative = receives)
    short_credit_pct: float | None  # measured today: short receives %/yr (negative = pays)
    spread_days: int = 0            # trading days of the spread sample (the fewest of the brokers merged)
    markup_long: float = 0.0        # %/yr, floored at 0
    markup_short: float = 0.0
    two_sided: float = 0.0
    triple: int | None = None       # weekday of the triple swap (0 = Mon); None: every calendar night counts 1


def legs(inst: str) -> tuple[str | None, str]:
    """(base currency or None when the base leg is not a rate: metal carry 0 / index dividend, quote currency)."""
    if inst in INDEX_CCY:
        return None, INDEX_CCY[inst]
    if inst[:3] in METALS:
        return None, inst[3:]
    return inst[:3], inst[3:]


@dataclass(frozen=True)
class CostModel:
    version: str
    table: dict[str, InstCost]
    rates: dict[str, tuple[np.ndarray, np.ndarray]] = field(default_factory=dict)   # ccy -> (available_ns, %)
    synthetic: bool = False         # True: every benchmark rate is 0 (synthetic tests only)

    def inst(self, inst: str) -> InstCost:
        c = self.table.get(inst)
        if c is None:
            raise MissingCost(f"cost model {self.version}: {inst} is not in the measured cost table; the run stops")
        return c

    def rate(self, ccy: str, t: int) -> float:
        if self.synthetic:
            return 0.0
        if ccy not in self.rates:
            raise MissingCost(f"cost model {self.version}: no benchmark rate series for {ccy}")
        av, v = self.rates[ccy]
        k = int(np.searchsorted(av, t, "right")) - 1
        if k < 0 or not np.isfinite(v[k]):
            raise MissingCost(f"cost model {self.version}: {ccy} benchmark rate unknown at {t}")
        return float(v[k])

    def round_trip(self, inst: str, price: float, spr: float) -> float:
        """Round trip in price units (EVAL-1 section 1)."""
        c = self.inst(inst)
        if inst == "XAUUSD":
            return GOLD_RT_USD
        if c.rt_bps is None or c.commission_side_bps is None or c.spread_days < MIN_MEASURED_DAYS:
            return max(fallback_rt(inst, price), spr if np.isfinite(spr) else 0.0)
        hist = (spr if np.isfinite(spr) else 0.0) + 2 * c.commission_side_bps * 1e-4 * price
        return max(c.rt_bps * 1e-4 * price, hist)

    def debit_pct(self, inst: str, side: int, t: int) -> float:
        """C2 financing debit, %/yr of notional (positive = paid) of `side` at instant t."""
        c = self.inst(inst)
        if c.kind == "off":
            return 0.0
        if c.kind == "missing":
            return SWAP_FALLBACK
        if c.kind == "two_sided":
            today = c.long_pct if side > 0 else -(c.short_credit_pct or 0.0)
            return max(float(today or 0.0), 0.5 * c.two_sided)
        base, quote = legs(inst)
        diff = self.rate(quote, t) - (self.rate(base, t) if base else 0.0)
        out = side * diff + (c.markup_long if side > 0 else c.markup_short)
        if side < 0 and inst in INDEX_CCY:
            out += DIV_SHORT[inst]
        return out

    def swap_nights(self, inst: str, t0: int, t1: int) -> int:
        """Rollovers (21:00 UTC) after t0 and before t1, weighted: the broker's triple day 3, Sat/Sun 0."""
        k0 = (t0 - ROLL_OFFSET) // DAY + 1
        k1 = (t1 - 1 - ROLL_OFFSET) // DAY
        if k1 < k0:
            return 0
        triple = self.inst(inst).triple
        if triple is None:
            return int(k1 - k0 + 1)
        wd = (np.arange(k0, k1 + 1) + 3) % 7                 # epoch day 0 was a Thursday; Mon = 0
        return int(np.where(wd == triple, 3, np.where(wd < 5, 1, 0)).sum())

    def financing(self, inst: str, side: int, price: float, t0: int, t1: int) -> float:
        """Financing over a hold in price units (positive = paid; a credit is negative)."""
        n = self.swap_nights(inst, t0, t1)
        return n * self.debit_pct(inst, side, t0) / 100.0 / DAYS_PER_YEAR * price if n else 0.0


# ------------------------------------------------------------------ the table
def build_table(measured: dict[str, Any]) -> dict[str, Any]:
    """The committed table from the measured file (aggregates only: no account, login or host). Two brokers: the larger
    cost of each field."""
    out: dict[str, dict[str, Any]] = {}
    for e in measured["instruments"]:
        inst = e["instrument"]
        sp, cm, fi = e.get("spread") or {}, e.get("commission") or {}, e.get("financing") or {}
        comm = cm.get("per_side_bps")
        med = sp.get("median_bps")
        rt = round(med + 2 * comm, 4) if med is not None and comm is not None else None
        if not fi:
            kind = "missing"
        elif "swap mode disabled" in str(fi.get("swap_note", "")):
            kind = "off"
        elif "benchmark" in fi:
            kind = "benchmark"
        else:
            kind = "two_sided"
        row = {"rt_bps": rt, "spread_bps": med, "commission_side_bps": comm, "kind": kind,
               "spread_days": int(sp.get("days") or 0), "spread_window": {e["broker"]: sp.get("window")},
               "long_pct": fi.get("long_cost_pct_yr"), "short_credit_pct": fi.get("short_credit_pct_yr"),
               "markup_long": max(0.0, float(fi.get("markup_long_pct") or 0.0)),
               "markup_short": max(0.0, float(fi.get("markup_short_pct") or 0.0)),
               "two_sided": max(0.0, float(fi.get("two_sided_markup_pct_yr") or 0.0)),
               "triple": fi.get("triple_swap_day"), "brokers": [e["broker"]],
               "benchmark_pct": fi.get("benchmark_pct")}
        if inst in out:
            row = _worse(out[inst], row)
        out[inst] = row
    sha = hashlib.sha256(json.dumps(measured["instruments"], sort_keys=True).encode()).hexdigest()
    return {"version": measured["cost_version"], "source": "measured_costs_1107.json", "source_rule": measured["rule"],
            "benchmarks": {k: v["pct"] for k, v in measured["benchmarks"].items()},
            "instruments_sha256": sha, "instruments": dict(sorted(out.items()))}


def _worse(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    def mx(x: Any, y: Any) -> Any:
        return y if x is None else x if y is None else max(x, y)
    w = dict(a)
    for k in ("rt_bps", "spread_bps", "commission_side_bps", "long_pct", "markup_long", "markup_short", "two_sided"):
        w[k] = mx(a[k], b[k])
    sc = [v for v in (a["short_credit_pct"], b["short_credit_pct"]) if v is not None]
    w["short_credit_pct"] = min(sc) if sc else None          # the short receives less
    if "missing" in (a["kind"], b["kind"]):
        w["kind"] = a["kind"] if b["kind"] == "missing" else b["kind"]
    w["brokers"] = sorted(set(a["brokers"]) | set(b["brokers"]))
    w["spread_days"] = min(a["spread_days"], b["spread_days"])
    w["spread_window"] = {**a["spread_window"], **b["spread_window"]}
    return w


def model_from_table(doc: dict[str, Any], rates: dict[str, tuple[np.ndarray, np.ndarray]] | None = None) -> CostModel:
    table = {}
    for inst, r in doc["instruments"].items():
        table[inst] = InstCost(
            rt_bps=r["rt_bps"], commission_side_bps=r["commission_side_bps"], kind=r["kind"],
            spread_days=int(r.get("spread_days") or 0), long_pct=r["long_pct"],
            short_credit_pct=r["short_credit_pct"], markup_long=float(r["markup_long"]),
            markup_short=float(r["markup_short"]), two_sided=float(r["two_sided"]),
            triple=None if inst == "BTCUSD" or not r.get("triple") else WEEKDAYS[r["triple"]])
    return CostModel(version=str(doc["version"]), table=table, rates=dict(rates or {}), synthetic=rates is None)


@lru_cache(maxsize=1)
def load_table(path: str = str(TABLE_PATH)) -> dict[str, Any]:
    doc: dict[str, Any] = yaml.safe_load(Path(path).read_text())
    return doc


@lru_cache(maxsize=1)
def default_model() -> CostModel:
    """The measured table with every benchmark rate 0: for synthetic tests only (the screen passes window_model)."""
    return model_from_table(load_table())


def window_model(win: str, *, allow_test_old: bool = False) -> CostModel:
    """The measured table with the window's point-in-time benchmark rates (FRED, available_utc, cut at the window end)."""
    from goldbot.research.catalogue.data import fred
    rates = {}
    for ccy, series in BENCH_SERIES.items():
        av, v = fred(series, win, allow_test_old=allow_test_old)
        o = np.argsort(av, kind="stable")
        rates[ccy] = (np.asarray(av)[o], np.asarray(v, float)[o])
    return model_from_table(load_table(), rates)


def main(argv: list[str] | None = None) -> None:
    """python -m goldbot.research.catalogue.costs <measured_costs.json>: (re)write config/catalogue_costs.yaml."""
    import sys
    src = Path((argv or sys.argv[1:])[0])
    doc = build_table(json.loads(src.read_text()))
    head = ("# Catalogue measured costs (EVAL-1 section 1, C2). GENERATED by `python -m goldbot.research.catalogue.costs"
            " <measured_costs.json>`;\n# do not edit by hand. Aggregates only (no account, login or host). rt_bps ="
            " median spread + 2 x commission per side, bps of\n# price; long_pct: the long pays %/yr; short_credit_pct:"
            " the short receives %/yr; markups %/yr floored at 0 (C2).\n")
    TABLE_PATH.write_text(head + yaml.safe_dump(doc, sort_keys=False, width=120))
    print(f"wrote {TABLE_PATH} ({len(doc['instruments'])} instruments, {doc['version']})")


if __name__ == "__main__":
    main()
