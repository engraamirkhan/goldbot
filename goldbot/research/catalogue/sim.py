"""Event -> trade simulation on one cell's bars, with costs and the matched control twin (quant A2/A3).

Fills
  market   open of bar i+1 (never the signal bar: the decision is at its close)
  stop     first bar in i+1..i+valid whose high (long) / low (short) reaches px, at max(open, px) / min(open, px)
  limit    first bar in i+1..i+valid whose low (long) / high (short) reaches px, at min(open, px) / max(open, px)
  close-only bars (co): orders, stops and targets are checked and filled AT THE CLOSE
Exits, per bar from the fill bar: gap through the stop at the open (after the fill bar) -> open; stop touched ->
stop (+ overshoot); target touched -> target; both on one bar -> STOP (conservative); then the exit mask at the close;
then the time exit at the close of the hold-th bar; data end -> last close. One position per cell at a time: an event
whose fill would land before the open trade's exit is skipped.

R: risk = |fill - stop|; gross R = side (exit - fill) / risk. Cost (price units) = round trip + overshoot on stop
exits + swap per 21:00 UTC rollover held; cost R = cost / risk. Stress = 1.5 x cost.

Controls (A2), one twin per trade, simulated by the same walk:
  random_side  same fill bar and price, OPPOSITE side, mirrored stop/target distances, exit no later than the real
               trade's exit bar; the random-side twin's expected gross = (real + opposite) / 2, so the matched excess
               is (real - opposite) / 2
  random_time  same side, same risk as a fraction of price and same target R multiple, entered at the open of a
               seeded random bar of the same instrument/TF near the real fill (same UTC day intraday; +-20 bars 1d;
               +-8 bars 1w), held the same number of bars; excess = real - twin"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np

from goldbot.research.catalogue.core import Events
from goldbot.research.catalogue.data import DAY, HOUR, Bars

ROLL_OFFSET = 21 * HOUR          # swap is charged per 21:00 UTC rollover crossed (calendar nights: weekend = 3)
GOLD_RT_USD = 0.166              # cost_of_record cor-2026-10-31.2, ICM all sessions, round trip incl. $4.0/lot/side
GOLD_OVERSHOOT_USD = 0.11
SWAP_DEFAULT = 0.025             # %/yr/leg when not measured
SWAP_INDEX_LONG = 0.07           # index longs: SOFR + 2.5-3 % ~ 6.5-7 %/yr
STRESS = 1.5

# fallback round trip in price units = 3 x a typical raw spread (instruments.yaml max_spread_points x point) +
# commission; the cost actually charged is the worse of this and the Dukascopy spread of the fill bar
FALLBACK_RT = {
    "XAGUSD": 0.060, "XAUEUR": 1.50, "XAUJPY": 250.0, "XTIUSD": 0.09, "US500": 1.50, "USTEC": 4.50, "DE40": 3.0,
    "UK100": 3.0, "US30": 6.0, "USINDEX": 0.05, "BTCUSD": 20.0, "EURUSD": 0.00010, "GBPUSD": 0.00012,
    "AUDUSD": 0.00012, "NZDUSD": 0.00015, "USDJPY": 0.010, "USDCAD": 0.00015, "USDCHF": 0.00015,
    "AUDJPY": 0.016, "EURAUD": 0.00025, "GBPAUD": 0.00030, "EURGBP": 0.00012, "EURJPY": 0.015, "GBPJPY": 0.020,
}
FX_COMMISSION_FRAC = 7.0 / 100_000          # ICM $3.5/lot/side on 100k notional, as a fraction of notional (USD-ish)
INDICES = ("US500", "USTEC", "DE40", "UK100", "US30")


def round_trip(inst: str, price: float, spr: float) -> float:
    if inst == "XAUUSD":
        return GOLD_RT_USD
    fb = FALLBACK_RT.get(inst, 0.0003 * price)
    if len(inst) == 6 and inst not in ("XAGUSD", "XAUEUR", "XAUJPY", "XTIUSD", "BTCUSD"):
        fb += FX_COMMISSION_FRAC * price
    return max(fb, spr if np.isfinite(spr) else 0.0)


def overshoot(inst: str, rt: float) -> float:
    return GOLD_OVERSHOOT_USD if inst == "XAUUSD" else 0.5 * rt


def swap_rate(inst: str, side: int) -> float:
    return SWAP_INDEX_LONG if (inst in INDICES and side > 0) else SWAP_DEFAULT


def nights(t0: int, t1: int) -> int:
    return max(0, int((t1 - ROLL_OFFSET) // DAY - (t0 - ROLL_OFFSET) // DAY))


@dataclass
class Walk:
    exit_bar: int
    exit_px: float
    kind: int          # 0 time/end, 1 stop, 2 target, 3 mask
    t_exit: int


def walk(b: Bars, f: int, side: int, fill: float, stop: float, tgt: float, last: int,
         mask: np.ndarray | None, fill_is_open: bool) -> Walk:
    """Walk from fill bar f to `last` (inclusive). `fill_is_open`: the fill was at bar f's open (market), so bar f's
    full range follows the fill; for a stop/limit fill inside bar f the stop is still checked on bar f (conservative)."""
    o, h, lo, c, co = b.o, b.h, b.l, b.c, b.co
    for j in range(f, last + 1):
        if co[j]:
            if side * (c[j] - stop) <= 0:
                return Walk(j, c[j], 1, int(b.ta[j]))
            if np.isfinite(tgt) and side * (c[j] - tgt) >= 0:
                return Walk(j, c[j], 2, int(b.ta[j]))
        else:
            if j > f and side * (o[j] - stop) <= 0:
                return Walk(j, o[j], 1, int(b.t[j]))
            hit_stop = (lo[j] <= stop) if side > 0 else (h[j] >= stop)
            if hit_stop:
                return Walk(j, stop, 1, int(b.t[j]))
            if np.isfinite(tgt):
                if j > f and side * (o[j] - tgt) >= 0:
                    return Walk(j, o[j], 2, int(b.t[j]))
                hit_t = (h[j] >= tgt) if side > 0 else (lo[j] <= tgt)
                if hit_t:
                    return Walk(j, tgt, 2, int(b.t[j]))
        if mask is not None and mask[j]:
            return Walk(j, c[j], 3, int(b.ta[j]))
    return Walk(last, c[last], 0, int(b.ta[last]))


def _fill(b: Bars, i: int, side: int, kind: int, px: float, valid: int) -> tuple[int, float, bool] | None:
    n = len(b)
    if i + 1 >= n:
        return None
    if kind == 3:
        return i, b.c[i], False
    if kind == 0:
        f = i + 1
        return f, (b.c[f] if b.co[f] else b.o[f]), not b.co[f]
    for f in range(i + 1, min(n, i + 1 + max(1, valid))):
        if b.co[f]:
            if kind == 1 and side * (b.c[f] - px) >= 0:
                return f, b.c[f], False
            if kind == 2 and side * (b.c[f] - px) <= 0:
                return f, b.c[f], False
            continue
        if kind == 1:
            if (side > 0 and b.h[f] >= px) or (side < 0 and b.l[f] <= px):
                p = max(b.o[f], px) if side > 0 else min(b.o[f], px)
                return f, p, p == b.o[f]
        else:
            if (side > 0 and b.l[f] <= px) or (side < 0 and b.h[f] >= px):
                p = min(b.o[f], px) if side > 0 else max(b.o[f], px)
                return f, p, p == b.o[f]
    return None


def _u(*key: object) -> float:
    d = hashlib.sha256("|".join(map(str, key)).encode()).digest()
    return int.from_bytes(d[:8], "big") / 2**64


TRADE_COLS = ("i", "f", "x", "side", "t_dec", "t_fill", "t_exit", "fill", "risk", "gross", "cost", "kind", "nights",
              "ctrl_gross", "excess", "drift_adj")


def cost_price(inst: str, side: int, fill: float, spr: float, kind: int, t_fill: int, t_exit: int) -> float:
    rt = round_trip(inst, fill, spr)
    c = rt + (overshoot(inst, rt) if kind == 1 else 0.0)
    return c + nights(t_fill, t_exit) * swap_rate(inst, side) / 360.0 * fill


def simulate(b: Bars, ev: Events, *, control: str, seed_key: str, drift_per_ns: float = 0.0) -> np.ndarray:
    """Trades of one cell as a float array with columns TRADE_COLS (decisions before the window's dec_from and fills
    at or after its end are dropped; a trade still open at the data end exits at the last close)."""
    n = len(b)
    out: list[tuple[float, ...]] = []
    if not len(ev) or n < 3:
        return np.zeros((0, len(TRADE_COLS)))
    dec_from = int(b.meta.get("dec_from", -2**62))
    end = int(b.meta.get("end", 2**62))
    busy_until = -1
    for k in range(len(ev)):
        i = int(ev.i[k])
        if i >= n - 1 or int(b.ta[i]) < dec_from or int(b.ta[i]) >= end:
            continue
        side = int(ev.side[k])
        fr = _fill(b, i, side, int(ev.kind[k]), float(ev.px[k]), int(ev.valid[k]))
        if fr is None:
            continue
        f, fill, at_open = fr
        if f <= busy_until or int(b.t[f]) >= end:
            continue
        both = False
        if ev.oco is not None and ev.oco[k] and k + 1 < len(ev) and int(ev.i[k + 1]) == i:
            fr2 = _fill(b, i, int(ev.side[k + 1]), int(ev.kind[k + 1]), float(ev.px[k + 1]), int(ev.valid[k + 1]))
            if fr2 is not None and fr2[0] < f:
                continue                      # the other leg filled first: this one is cancelled
            both = fr2 is not None and fr2[0] == f
        elif k > 0 and ev.oco is not None and ev.oco[k - 1] and int(ev.i[k - 1]) == i:
            fr1 = _fill(b, i, int(ev.side[k - 1]), int(ev.kind[k - 1]), float(ev.px[k - 1]), int(ev.valid[k - 1]))
            if fr1 is not None and fr1[0] <= f:
                continue                      # the first leg filled first or on the same bar (handled there)
        stop = float(ev.stop[k]) if np.isfinite(ev.stop[k]) else fill - side * float(ev.stop_d[k])
        risk = side * (fill - stop)
        if not np.isfinite(risk) or risk <= 0:
            continue
        tgt = float(ev.tgt[k]) if np.isfinite(ev.tgt[k]) else (
            fill + side * float(ev.tgt_d[k]) if np.isfinite(ev.tgt_d[k]) else np.nan)
        if np.isfinite(tgt) and side * (tgt - fill) <= 0:
            continue
        start = f + 1 if int(ev.kind[k]) == 3 else f
        if start >= n:
            continue
        last = min(n - 1, start + int(ev.hold[k]) - 1)
        mask = ev.exit_long if side > 0 else ev.exit_short
        if both:                              # both OCO legs on one bar: full loss (card S20/S21 conservative rule)
            w = Walk(f, stop, 1, int(b.t[f]))
        else:
            w = walk(b, start, side, fill, stop, tgt, last, mask, at_open)
        busy_until = w.exit_bar
        gross = side * (w.exit_px - fill) / risk
        cost = cost_price(b.inst, side, fill, float(b.spr[f]), w.kind, int(b.t[f]), w.t_exit) / risk
        # ---- control twin
        if control == "random_side":
            cstop = fill + side * risk
            ctgt = fill - (tgt - fill) if np.isfinite(tgt) else np.nan
            clast = w.exit_bar if w.kind in (0, 3) else last
            cw = Walk(f, cstop, 1, int(b.t[f])) if both else walk(b, start, -side, fill, cstop, ctgt, clast, None,
                                                                   at_open)
            opp = -side * (cw.exit_px - fill) / risk
            ctrl = 0.5 * (gross + opp)
        else:
            hold_bars = max(1, w.exit_bar - start + 1)
            g = _pick_bar(b, f, seed_key, i)
            if g is None or g + hold_bars - 1 >= n:
                continue
            cfill = b.c[g] if b.co[g] else b.o[g]
            crisk = risk / fill * cfill
            cstop = cfill - side * crisk
            ctgt = cfill + side * (tgt - fill) / risk * crisk if np.isfinite(tgt) else np.nan
            cw = walk(b, g, side, cfill, cstop, ctgt, g + hold_bars - 1, None, not b.co[g])
            ctrl = side * (cw.exit_px - cfill) / crisk
        drift_adj = gross - side * drift_per_ns * (w.t_exit - int(b.t[f])) * fill / risk
        out.append((i, f, w.exit_bar, side, int(b.ta[i]), int(b.t[f]), w.t_exit, fill, risk, gross, cost, w.kind,
                    nights(int(b.t[f]), w.t_exit), ctrl, gross - ctrl, drift_adj))
    return np.array(out, float).reshape(-1, len(TRADE_COLS))


def _pick_bar(b: Bars, f: int, seed_key: str, i: int) -> int | None:
    tf = b.tf
    if tf in ("1d", "1w"):
        span = 20 if tf == "1d" else 8
        lo, hi = max(1, f - span), min(len(b) - 1, f + span)
    else:
        day = int(b.t[f]) // DAY
        lo = int(np.searchsorted(b.t, day * DAY))
        hi = int(np.searchsorted(b.t, (day + 1) * DAY)) - 1
        lo = max(lo, 1)
    if hi < lo:
        return None
    return lo + int(_u(seed_key, b.inst, b.tf, i) * (hi - lo + 1))


def window_drift_per_ns(b: Bars) -> float:
    """Mean log drift per ns over the window's decision span (for the drift-adjusted gross)."""
    dec_from = int(b.meta.get("dec_from", int(b.t[0]) if len(b) else 0))
    k = int(np.searchsorted(b.ta, dec_from))
    if len(b) - k < 2:
        return 0.0
    span = float(b.ta[-1] - b.ta[k])
    return float(np.log(b.c[-1] / b.c[k]) / span) if span > 0 and b.c[k] > 0 else 0.0
