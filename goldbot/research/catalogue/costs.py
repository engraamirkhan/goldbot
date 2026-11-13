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
  round trip   gold (backlog 146): the cost of record cell of the fill instant's session, the worse of the brokers
               (config/cost_of_record.yaml at the version `deciding.cost_of_record` pins, else MissingCost). Sessions
               are the calendar's city clocks (goldbot.data.calendar DEFAULT_SESSIONS): asia Tokyo 08:00 (23:00 UTC) to
               London 08:00, london London 08:00 to New York 08:30, newyork New York 08:30 to 17:00; the thin hours
               from the New York close to the Tokyo open are asia (session_label). A broker's cell on fewer than
               MIN_COST_CELL_FILLS (30) fills reads no lower than its pooled `all` cell, a missing cell reads the pooled
               cell, and no session is charged less than the flat GOLD_RT_USD ($0.166, the charge before 146): no cost
               is lowered without a measurement. cor-2026-10-31.2: asia 0.204, london 0.293, newyork 0.263 (Vantage
               London and New York are dearer than ICM's 0.164 and 0.166). A fill without an instant pays the dearest
               session. Others, only when the spread sample covers at least
               MIN_MEASURED_DAYS (20) trading days on every broker it came from (quant ruling 2026-11-07: "a 3-5-day
               spread sample is NOT a pinned measurement; the fallback cost stays ... until 20 trading days have been
               measured, with the version and dates recorded"): max(measured rt_bps x price, the fill bar's Dukascopy
               spread + 2 x measured commission); rt_bps = median spread + 2 x commission per side (bps of price).
               Otherwise (fewer days, or no commission/spread): max(the frozen fallback FALLBACK_RT (+ FX commission),
               the short sample's rt_bps or median spread, the fill bar's spread): a short sample never lowers the
               fallback, it can raise it (quant review 1). Swap terms are broker terms (symbol_info), not a sample: C2 uses them as is.
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
               missing    swap not measured: BTCUSD pays SWAP_FALLBACK_CRYPTO (20 %/yr) per calendar night both sides,
                          any other instrument the frozen SWAP_FALLBACK
  nights       rollovers at 17:00 New York (21:00 UTC in summer, 22:00 UTC in winter) on Mon-Fri; the broker's triple
               day counts 3, Sat/Sun 0 (BTCUSD: every night 1)
  duplicates   an instrument measured on two brokers takes the larger cost of each field (stricter)
  fail closed  an instrument not in the table raises MissingCost; in a window model a benchmark rate unknown at the fill
               raises too. `default_model()` (synthetic tests only) sets every benchmark rate to 0.

EVAL-2 deciding cost (method section 4, prereg 2026-11-09 section 4.2; pin in config/catalogue_costs.yaml `deciding`):
  "Deciding cost = max(today R, bp-engine R on the window read) plus overshoot x (stop-exit share + stop-entry share),
  scaled in bp with the round trip on the bp engine, plus swap. ... Until S5 passes per broker and timeframe, the
  deciding cost includes the measured positive fill-vs-replay gap in $, converted at decision-time ATR. 'Positive'
  means fills worse than replay v2; a fill better than replay earns no credit. Gates are read at 1.5x that."
  "Stress line: the 2010-17 $-constant cost ... It is reported, never gated. A cell net < 0 under it is tagged
  COST-SENSITIVE."
  As implemented (gold only; any other instrument has no ATR pin and raises MissingCost; R = $ / (stop_atr x ATR)):
  today      the all-in round trip ($0.230, cor-2026-10-31.2) over today's pinned ATR at the decision timeframe
  bp engine  the same round trip in bp of the pin price (0.565 bp), charged at the decision price and decision ATR
  overshoot  $0.145 per stop leg, in the same branch as the round trip that won the max (x 0.145 / 0.230)
  swap       C2 financing over the hold (strict-interior rollovers, #343), floored at 0: a credit is never taken
  S5 gap     max(0, measured gap $) at the decision ATR per (broker, band) until "broker:band" is in s5_passed
  stress     the $-constant round trip ($0.230) at min(decision ATR, the 2010-17 era median ATR): 15m 0.151 R at a
             1-ATR stop in every window
  deciding_cost replaces, never adds to, the sim's cost_r."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Final, Literal

import numpy as np
import yaml

from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.research.catalogue.data import DAY

TABLE_PATH = Path(__file__).resolve().parents[3] / "config" / "catalogue_costs.yaml"
COR_PATH = TABLE_PATH.parent / "cost_of_record.yaml"   # the gold cost of record, by broker x session
ROLL_TZ = "America/New_York"     # the rollover is 17:00 New York: 21:00 UTC in summer (EDT), 22:00 UTC in winter
ROLL_HOUR = 17
GOLD_RT_USD = 0.166              # cost_of_record cor-2026-10-31.2, ICM all sessions, round trip incl. $4.0/lot/side:
                                 # the flat charge before backlog 146, now the floor of every session's cell
GOLD_SESSIONS = tuple(DEFAULT_SESSIONS.sessions_local)       # asia, london, newyork
GOLD_THIN_SESSION = "asia"       # calendar session_label: the hours outside every window share Asia's liquidity
SESSION_CLOCK_YEARS = (1960, 2060)   # the session clock's span; an instant outside it raises MissingCost
DAYS_PER_YEAR = 365.0            # measured file: "per night x 365 / (contract x mid)"
SWAP_FALLBACK = 2.5              # %/yr per side, the frozen fallback where no swap is measured
SWAP_FALLBACK_CRYPTO = 20.0      # %/yr per side, unmeasured crypto CFD (typical 10-25); never lowered without 20 days of measured swap
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


GATE_COST_MULT: Final = 1.5      # EVAL-2: gates are read at 1.5x the deciding cost
ATR_PIN: Final = "atr-pin-2026-11-09.2"   # must equal config/catalogue_costs.yaml deciding.pin (else MissingCost)
DECIDING_MARKER = "# --- deciding cost"   # costs.main keeps the hand-maintained block from this line on
WINDOWS_EVAL2 = ("explore", "test_old", "confirm", "forward")


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
    spread_bps: float | None = None # the sample's median spread, bps of price
    markup_long: float = 0.0        # %/yr, floored at 0
    markup_short: float = 0.0
    two_sided: float = 0.0
    triple: int | None = None       # weekday of the triple swap (0 = Mon); None: every calendar night counts 1


@lru_cache(maxsize=1)
def _roll_table() -> tuple[int, np.ndarray]:
    """(first epoch day, rollover instant ns per day): 17:00 America/New_York on each date 1990-2040."""
    import pandas as pd
    d = pd.date_range("1990-01-01", "2040-12-31", freq="D")
    roll = (d + pd.Timedelta(hours=ROLL_HOUR)).tz_localize(ROLL_TZ).tz_convert("UTC")
    from goldbot.data.timeutil import epoch_ns
    return int(epoch_ns(d[:1].tz_localize("UTC"))[0] // DAY), np.asarray(epoch_ns(roll), np.int64)


def rollover_table() -> tuple[int, np.ndarray]:
    """Public 17:00 New York rollover clock, the one the swap counts (#343): (first epoch day d0, rollover instant ns
    per New York date from 1990-01-01 to 2040-12-31; index = epoch day - d0). 21:00 UTC in summer (EDT), 22:00 UTC in
    winter. Lane 1's session_day uses it, so sessions and swap nights share one clock. Read-only: do not mutate."""
    return _roll_table()


def rollovers_between(t0: int, t1: int) -> np.ndarray:
    """New York dates whose 17:00 rollover falls strictly inside (t0, t1). A position filled at the rollover instant
    opened after it, and one closed at it closed before it; neither pays that night."""
    d0, r = _roll_table()
    a = int(np.searchsorted(r, t0, "right"))     # a rollover AT the fill: the fill is the first tick after it, not held
    b = int(np.searchsorted(r, t1, "left"))      # a rollover AT the exit: the exit is the last tick before it, not held
    return np.arange(a, b, dtype=np.int64) + d0


def hold_nights(t0: int, t1: int) -> int:
    """Calendar rollovers held (unweighted), the reported hold length."""
    return int(len(rollovers_between(t0, t1)))


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
    gold_session_rt: dict[str, float] = field(default_factory=dict)   # session -> gold round trip (gold_session_rt)

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

    def round_trip(self, inst: str, price: float, spr: float, t_ns: int | None = None) -> float:
        """Round trip in price units (EVAL-1 section 1). Gold: the cost of record cell of the session holding the fill
        instant `t_ns` (backlog 146), the dearest session without one; other instruments ignore the instant."""
        c = self.inst(inst)
        if inst == "XAUUSD":
            if not self.gold_session_rt:
                raise MissingCost(f"cost model {self.version}: no gold cost of record by session; the run stops")
            if t_ns is None:
                return max(self.gold_session_rt.values())
            return self.gold_session_rt[gold_session(t_ns)]
        bar = spr if np.isfinite(spr) else 0.0
        if c.rt_bps is None or c.commission_side_bps is None or c.spread_days < MIN_MEASURED_DAYS:
            # a short sample never lowers the fallback; it can raise it (quant review 1)
            return max(fallback_rt(inst, price), (c.rt_bps or c.spread_bps or 0.0) * 1e-4 * price, bar)
        hist = (spr if np.isfinite(spr) else 0.0) + 2 * c.commission_side_bps * 1e-4 * price
        return max(c.rt_bps * 1e-4 * price, hist)

    def debit_pct(self, inst: str, side: int, t: int) -> float:
        """C2 financing debit, %/yr of notional (positive = paid) of `side` at instant t."""
        c = self.inst(inst)
        if c.kind == "off":
            return 0.0
        if c.kind == "missing":
            return SWAP_FALLBACK_CRYPTO if inst == "BTCUSD" else SWAP_FALLBACK
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
        """Rollovers (17:00 New York) strictly inside (t0, t1) (`rollovers_between`), weighted: the broker's triple day 3, Sat/Sun 0.
        An instrument without a triple day counts every night 1."""
        k = rollovers_between(t0, t1)
        if not len(k):
            return 0
        triple = self.inst(inst).triple
        if triple is None:
            return int(len(k))
        wd = (k + 3) % 7                                     # NY date of the rollover; epoch day 0 a Thursday, Mon = 0
        return int(np.where(wd == triple, 3, np.where(wd < 5, 1, 0)).sum())

    def financing(self, inst: str, side: int, price: float, t0: int, t1: int) -> float:
        """Financing over a hold in price units (positive = paid; a credit is negative)."""
        n = self.swap_nights(inst, t0, t1)
        return n * self.debit_pct(inst, side, t0) / 100.0 / DAYS_PER_YEAR * price if n else 0.0


# ------------------------------------------------------------------ EVAL-2 deciding cost and stress line
@dataclass(frozen=True, slots=True)
class DecidingCost:
    r: float                # total at 1.0x (gates read GATE_COST_MULT x r)
    today_r: float
    bp_engine_r: float
    overshoot_r: float
    swap_r: float
    s5_gap_r: float         # 0.0 once S5 passes for (broker, tf band)
    pin: str


def _pin(inst: str, tf: str) -> dict[str, Any]:
    """The `deciding` block of the cost table, checked against ATR_PIN, for a pinned (inst, tf); fail closed."""
    pin: dict[str, Any] | None = load_table().get("deciding")
    if not pin or pin.get("pin") != ATR_PIN:
        raise MissingCost(f"deciding cost: config pin {(pin or {}).get('pin')} is not the code's {ATR_PIN}")
    if inst != "XAUUSD":
        raise MissingCost(f"deciding cost: {inst} has no ATR pin ({ATR_PIN} is gold only); the run stops")
    if tf not in pin["atr_today_usd"]:
        raise MissingCost(f"deciding cost: {tf} is not pinned in {ATR_PIN}")
    return pin


def _stop_usd(atr_decision: float, stop_atr: float) -> float:
    stop = atr_decision * stop_atr
    if not (np.isfinite(stop) and atr_decision > 0 and stop_atr > 0):
        raise ValueError(f"stop distance needs a positive finite ATR and stop_atr, got {atr_decision} x {stop_atr}")
    return float(stop)


def deciding_cost(inst: str, tf: str, *, side: int, price: float, atr_decision: float, stop_atr: float,
                  t_fill_ns: int, t_exit_ns: int, stop_exit_share: float, stop_entry_share: float,
                  window: Literal["explore", "test_old", "confirm", "forward"], broker: str = "icm",
                  costs: CostModel | None = None) -> DecidingCost:
    """EVAL-2 deciding cost in R at 1.0x (module docstring): max(today, bp engine) + overshoot x (stop-exit share +
    stop-entry share) + swap (never a credit) + the positive S5 gap until S5 passes. Every term is >= 0. `costs`: the
    window's model for the swap; required (None raises MissingCost). deciding_cost replaces, never adds to, the sim's
    cost_r."""
    if costs is None:
        raise MissingCost("deciding cost needs the window's CostModel")
    if window not in WINDOWS_EVAL2:
        raise ValueError(f"deciding cost: unknown window {window!r}")
    for share in (stop_exit_share, stop_entry_share):
        if not 0.0 <= share <= 1.0:
            raise ValueError(f"deciding cost: a stop share must be in [0, 1], got {share}")
    pin = _pin(inst, tf)
    stop = _stop_usd(atr_decision, stop_atr)
    rt, osh = float(pin["rt_usd"]), float(pin["overshoot_usd"])
    today_r = rt / (stop_atr * float(pin["atr_today_usd"][tf]))
    bp_engine_r = rt / float(pin["pin_price_usd"]) * price / stop
    overshoot_r = max(today_r, bp_engine_r) * osh / rt * (stop_exit_share + stop_entry_share)
    swap_r = max(0.0, costs.financing(inst, side, price, t_fill_ns, t_exit_ns)) / stop
    gaps = pin["s5_gap_usd"].get(broker)
    if gaps is None:
        raise MissingCost(f"deciding cost: no S5 fill-vs-replay gap for broker {broker!r}")
    band = pin["s5_band"][tf]
    s5_gap_r = 0.0 if f"{broker}:{band}" in (pin.get("s5_passed") or []) else max(0.0, float(gaps[band])) / stop
    total = max(today_r, bp_engine_r) + overshoot_r + swap_r + s5_gap_r
    return DecidingCost(r=total, today_r=today_r, bp_engine_r=bp_engine_r, overshoot_r=overshoot_r, swap_r=swap_r,
                        s5_gap_r=s5_gap_r, pin=ATR_PIN)


def stress_cost(inst: str, tf: str, *, atr_decision: float, stop_atr: float) -> float:
    """The stress line in R: today's $-constant round trip at the 2010-17 level, stress_rt_usd / (stop_atr x
    min(atr_decision, era ATR)): 15m 0.151 R at a 1-ATR stop. Reported, never gated, and not the deciding cost."""
    pin = _pin(inst, tf)
    _stop_usd(atr_decision, stop_atr)
    return float(pin["stress_rt_usd"]) / (stop_atr * min(atr_decision, float(pin["era_atr_2010_17_usd"][tf])))


def cost_sensitive(net_at_stress_r: float) -> bool:
    """COST-SENSITIVE: the cell's net under the stress line is below 0 (the tag goes to the owner with any L5 case)."""
    return net_at_stress_r < 0


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
            spread_days=int(r.get("spread_days") or 0), spread_bps=r.get("spread_bps"), long_pct=r["long_pct"],
            short_credit_pct=r["short_credit_pct"], markup_long=float(r["markup_long"]),
            markup_short=float(r["markup_short"]), two_sided=float(r["two_sided"]),
            triple=None if inst == "BTCUSD" or not r.get("triple") else WEEKDAYS[r["triple"]])
    pin = (doc.get("deciding") or {}).get("cost_of_record")
    if pin is None:
        raise MissingCost(f"cost table {doc['version']}: no `deciding.cost_of_record` pin for the gold sessions")
    return CostModel(version=str(doc["version"]), table=table, rates=dict(rates or {}), synthetic=rates is None,
                     gold_session_rt=gold_session_rt(load_cost_of_record(), str(pin)))


def gold_session_rt(cor: dict[str, Any], pin: str) -> dict[str, float]:
    """Gold's round trip per session from the cost of record (backlog 146): the worse of the brokers' cells; a cell on
    fewer than MIN_COST_CELL_FILLS fills reads no lower than its broker's pooled cell, a missing cell reads the pooled
    cell, and every session is floored at the flat GOLD_RT_USD. Only XAUUSD cells (`cost_of_record.cell_key`: a bare
    broker) are read. A table other than the pinned version raises MissingCost (cost versions are pinned before the
    run)."""
    from goldbot.base import load_record
    from goldbot.research.arms import ALL_SESSIONS, MIN_COST_CELL_FILLS, CostOfRecord
    rec = load_record(CostOfRecord, cor, source="cost_of_record")
    if rec.version != pin:
        raise MissingCost(f"cost of record {rec.version} is not the pinned {pin}; the run stops")
    out = dict.fromkeys(GOLD_SESSIONS, GOLD_RT_USD)
    for broker, cells in rec.cells.items():
        if ":" in broker:                              # another instrument's cells (`broker:<instrument>`)
            continue
        pooled = cells[ALL_SESSIONS].round_trip_usd
        for s in GOLD_SESSIONS:
            c = cells.get(s)
            if c is None:
                rt = pooled
            elif c.n_fills < MIN_COST_CELL_FILLS:      # thin: it can raise the pooled read, never lower it
                rt = max(c.round_trip_usd, pooled)
            else:
                rt = c.round_trip_usd
            out[s] = max(out[s], rt)
    return out


@lru_cache(maxsize=1)
def _session_clock() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(open ns, close ns, session index) of every session instance on every date in SESSION_CLOCK_YEARS, sorted by
    open: the calendar's own windows (`SessionWindow.bounds_ns`, city clocks per date), which must not overlap."""
    import pandas as pd
    lo, hi = (int(pd.Timestamp(f"{y}-01-01").value) // DAY for y in SESSION_CLOCK_YEARS)
    days = np.arange(lo, hi, dtype=np.int64)
    bounds = [w.bounds_ns(days) for w in DEFAULT_SESSIONS.sessions_local.values()]
    opens = np.concatenate([o for o, _ in bounds])
    closes = np.concatenate([c for _, c in bounds])
    codes = np.repeat(np.arange(len(bounds), dtype=np.int64), len(days))
    order = np.argsort(opens, kind="stable")
    opens, closes, codes = opens[order], closes[order], codes[order]
    if not (closes[:-1] <= opens[1:]).all():
        raise ValueError("session windows overlap: the gold cost cannot name one session per instant")
    return opens, closes, codes


def gold_session(t_ns: int) -> str:
    """The session holding instant `t_ns` (epoch ns UTC), as `DEFAULT_SESSIONS.session_label` names it: the window's
    name inside [open, close), "asia" outside every window. An instant outside SESSION_CLOCK_YEARS raises MissingCost."""
    opens, closes, codes = _session_clock()
    if not opens[0] <= t_ns < closes[-1]:
        raise MissingCost(f"instant {t_ns} is outside the session clock {SESSION_CLOCK_YEARS}")
    j = int(np.searchsorted(opens, t_ns, "right")) - 1
    return GOLD_SESSIONS[int(codes[j])] if j >= 0 and t_ns < closes[j] else GOLD_THIN_SESSION


@lru_cache(maxsize=1)
def load_cost_of_record(path: str = str(COR_PATH)) -> dict[str, Any]:
    """The gold cost of record (config/cost_of_record.yaml), read once."""
    doc: dict[str, Any] = yaml.safe_load(Path(path).read_text())
    return doc


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
    old = TABLE_PATH.read_text() if TABLE_PATH.exists() else ""
    keep = old[old.index(DECIDING_MARKER):] if DECIDING_MARKER in old else ""   # the hand-maintained deciding block
    TABLE_PATH.write_text(head + yaml.safe_dump(doc, sort_keys=False, width=120) + keep)
    print(f"wrote {TABLE_PATH} ({len(doc['instruments'])} instruments, {doc['version']})")


if __name__ == "__main__":
    main()
