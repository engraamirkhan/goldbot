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

Risk floor (EVAL-1 precondition iii, audit D1): "stop distance >= max(0.25 x ATR20 of the decision timeframe,
3 x round-trip cost), else the event is not traded (counted as an event, not as a trade)". ATR20 is the Wilder ATR at
the decision bar i; an event whose ATR20 is not yet defined is not traded either. Without it a flat holiday bar gave a
risk of 0.01 points and one trade of +9,331 R (S21-V2, US30 2013-12-26).

R: risk = |fill - stop|; gross R = side (exit - fill) / risk. Cost (price units) = round trip + overshoot on stop
exits + swap per 21:00 UTC rollover held; cost R = cost / risk. Stress = 1.5 x cost.

Controls (A2), one twin per trade, simulated by the same walk:
  random_side  same fill bar and price, OPPOSITE side, mirrored stop/target distances, exit no later than the real
               trade's exit bar; the random-side twin's expected gross = (real + opposite) / 2, so the matched excess
               is (real - opposite) / 2. When both OCO legs trigger on one bar the REAL trade is a full loss (card rule)
               but the opposite twin is walked like any other: forcing it to -1 too (audit D2) zeroed the excess on
               exactly the bars that cost the straddle, while the surviving straddles were selected on the bar's range
  random_time  same side, same risk as a fraction of price and the same target R multiple ON THE TRADE'S SIDE (a short
               twin's target is below its entry; audit D3), entered at the open of a seeded random bar of the same
               instrument/TF AFTER the real fill, held the same number of bars; excess = real - twin. Intraday: the
               same time of day on one of the next 7 calendar days that has that bar (same session, independent of
               the signal); 1d: one of the next 20 bars; 1w: the next 8. Never before the fill: a twin drawn from the
               bars that made the signal inherits its selection (audit D4: a reversal entry's twin bought into the
               move that triggered it)"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np

from goldbot.research.catalogue.core import Events, atr
from goldbot.research.catalogue.data import DAY, HOUR, Bars

ROLL_OFFSET = 21 * HOUR          # swap is charged per 21:00 UTC rollover crossed (calendar nights: weekend = 3)
GOLD_RT_USD = 0.166              # cost_of_record cor-2026-10-31.2, ICM all sessions, round trip incl. $4.0/lot/side
GOLD_OVERSHOOT_USD = 0.11
SWAP_DEFAULT = 0.025             # %/yr/leg when not measured
SWAP_INDEX_LONG = 0.07           # index longs: SOFR + 2.5-3 % ~ 6.5-7 %/yr
STRESS = 1.5
RISK_FLOOR_ATR = 0.25            # EVAL-1 (iii): stop distance >= max(0.25 x ATR20, 3 x round trip)
RISK_FLOOR_RT = 3.0
RISK_FLOOR_ATR_N = 20
TWIN_DAYS = 7                    # intraday random-time twin: same time of day on one of the next 7 calendar days
TWIN_SPAN = {"1d": 20, "1w": 8}  # daily/weekly random-time twin: one of the next N bars

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
    return max(0, int((t1 - 1 - ROLL_OFFSET) // DAY - (t0 - ROLL_OFFSET) // DAY))   # an exit AT 21:00 pays no swap


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


def _count(counts: dict[str, int] | None, key: str) -> None:
    if counts is not None:
        counts[key] = counts.get(key, 0) + 1


def _oco_sibling(ev: Events, k: int) -> int | None:
    """The other leg of the OCO pair event k belongs to (pairs are (long, short) rows on the same bar), else None."""
    if ev.oco is None:
        return None
    if ev.oco[k] and k + 1 < len(ev) and int(ev.i[k + 1]) == int(ev.i[k]):
        return k + 1
    if k > 0 and ev.oco[k - 1] and int(ev.i[k - 1]) == int(ev.i[k]):
        return k - 1
    return None


def _leg(ev: Events, k: int, fill: float) -> tuple[float, float, float] | None:
    """(stop, risk, target) of event k filled at `fill`; None when the stop or target is on the wrong side."""
    side = int(ev.side[k])
    stop = float(ev.stop[k]) if np.isfinite(ev.stop[k]) else fill - side * float(ev.stop_d[k])
    risk = side * (fill - stop)
    if not np.isfinite(risk) or risk <= 0:
        return None
    tgt = float(ev.tgt[k]) if np.isfinite(ev.tgt[k]) else (
        fill + side * float(ev.tgt_d[k]) if np.isfinite(ev.tgt_d[k]) else np.nan)
    if np.isfinite(tgt) and side * (tgt - fill) <= 0:
        return None
    return stop, risk, tgt


def _nominal_ok(b: Bars, ev: Events, k: int, a20: np.ndarray) -> bool:
    """Leg k's stop distance at its order price clears the risk floor (decided at the signal bar, before any fill)."""
    i = int(ev.i[k])
    px = float(ev.px[k]) if np.isfinite(ev.px[k]) else float(b.c[i])
    leg = _leg(ev, k, px)
    return leg is not None and leg[1] >= risk_floor(b.inst, px, float(b.spr[min(i + 1, len(b) - 1)]), float(a20[i]))


def _opposite(b: Bars, start: int, side: int, fill: float, risk: float, tgt: float, last: int,
              at_open: bool) -> float:
    """R of the opposite-side twin of a trade: same bar and price, mirrored stop and target distances."""
    ctgt = fill - (tgt - fill) if np.isfinite(tgt) else np.nan
    cw = walk(b, start, -side, fill, fill + side * risk, ctgt, last, None, at_open)
    return -side * (cw.exit_px - fill) / risk


def _u(*key: object) -> float:
    d = hashlib.sha256("|".join(map(str, key)).encode()).digest()
    return int.from_bytes(d[:8], "big") / 2**64


TRADE_COLS = ("i", "f", "x", "side", "t_dec", "t_fill", "t_exit", "fill", "risk", "gross", "cost", "kind", "nights",
              "ctrl_gross", "excess", "drift_adj")


def cost_price(inst: str, side: int, fill: float, spr: float, kind: int, t_fill: int, t_exit: int) -> float:
    rt = round_trip(inst, fill, spr)
    c = rt + (overshoot(inst, rt) if kind == 1 else 0.0)
    return c + nights(t_fill, t_exit) * swap_rate(inst, side) / 360.0 * fill


def risk_floor(inst: str, fill: float, spr: float, atr20: float) -> float:
    """Smallest tradable stop distance (price units): max(0.25 x ATR20, 3 x round trip); nan when ATR20 is unknown."""
    if not np.isfinite(atr20):
        return float("nan")
    return max(RISK_FLOOR_ATR * atr20, RISK_FLOOR_RT * round_trip(inst, fill, spr))


def simulate(b: Bars, ev: Events, *, control: str, seed_key: str, drift_per_ns: float = 0.0,
             counts: dict[str, int] | None = None) -> np.ndarray:
    """Trades of one cell as a float array with columns TRADE_COLS (decisions before the window's dec_from and fills
    at or after its end are dropped; a trade still open at the data end exits at the last close). `counts`, when
    given, gets "risk_floor" += the events not traded because their stop was inside the risk floor."""
    n = len(b)
    out: list[tuple[float, ...]] = []
    if not len(ev) or n < 3:
        return np.zeros((0, len(TRADE_COLS)))
    dec_from = int(b.meta.get("dec_from", -2**62))
    end = int(b.meta.get("end", 2**62))
    busy_until = -1
    a20 = atr(b, RISK_FLOOR_ATR_N)
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
        fr2: tuple[int, float, bool] | None = None
        sib = _oco_sibling(ev, k)
        if sib is not None and not all(_nominal_ok(b, ev, q, a20) for q in (k, sib)):
            _count(counts, "risk_floor")      # an OCO pair is placed whole or not at all
            continue
        if sib is not None and sib > k:
            fr2 = _fill(b, i, int(ev.side[sib]), int(ev.kind[sib]), float(ev.px[sib]), int(ev.valid[sib]))
            if fr2 is not None and fr2[0] < f:
                continue                      # the other leg filled first: this one is cancelled
            both = fr2 is not None and fr2[0] == f
        elif sib is not None:
            fr1 = _fill(b, i, int(ev.side[sib]), int(ev.kind[sib]), float(ev.px[sib]), int(ev.valid[sib]))
            if fr1 is not None and fr1[0] <= f:
                continue                      # the first leg filled first or on the same bar (handled there)
        leg = _leg(ev, k, fill)
        if leg is None:
            continue
        stop, risk, tgt = leg
        floor = risk_floor(b.inst, fill, float(b.spr[f]), float(a20[i]))
        if not risk >= floor:                 # nan floor (ATR20 unknown) is not traded either
            _count(counts, "risk_floor")
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
            clast = w.exit_bar if w.kind in (0, 3) else last
            opp = _opposite(b, start, side, fill, risk, tgt, clast, at_open)
            if both and fr2 is not None and sib is not None:
                # the same bar touched the other leg too: the twin also takes the opposite of that leg, so every leg
                # the market touched has its opposite counted once (unbiased on a martingale; audit D2)
                leg2 = _leg(ev, sib, fr2[1])
                if leg2 is not None:
                    side2 = int(ev.side[sib])
                    last2 = min(n - 1, start + int(ev.hold[sib]) - 1)
                    opp += _opposite(b, start, side2, fr2[1], leg2[1], leg2[2], last2, fr2[2])
            ctrl = 0.5 * (gross + opp)
        else:
            hold_bars = max(1, w.exit_bar - start + 1)
            g = _pick_bar(b, f, seed_key, i, hold_bars)
            if g is None:
                continue
            cfill = b.c[g] if b.co[g] else b.o[g]
            crisk = risk / fill * cfill
            cstop = cfill - side * crisk
            ctgt = cfill + (tgt - fill) / risk * crisk if np.isfinite(tgt) else np.nan     # tgt - fill carries the side
            cw = walk(b, g, side, cfill, cstop, ctgt, g + hold_bars - 1, None, not b.co[g])
            ctrl = side * (cw.exit_px - cfill) / crisk
        drift_adj = gross - side * drift_per_ns * (w.t_exit - int(b.t[f])) * fill / risk
        out.append((i, f, w.exit_bar, side, int(b.ta[i]), int(b.t[f]), w.t_exit, fill, risk, gross, cost, w.kind,
                    nights(int(b.t[f]), w.t_exit), ctrl, gross - ctrl, drift_adj))
    return np.array(out, float).reshape(-1, len(TRADE_COLS))


def twin_candidates(b: Bars, f: int, hold_bars: int) -> np.ndarray:
    """Bars the random-time twin of a trade filled at bar f may start at: strictly after f, and its whole hold fits in
    the data. Intraday: the bar opening at the same time of day 1..TWIN_DAYS calendar days later (where one exists);
    1d/1w: the next TWIN_SPAN bars."""
    n = len(b)
    if b.tf in TWIN_SPAN:
        g = np.arange(f + 1, min(n, f + 1 + TWIN_SPAN[b.tf]))
    else:
        want = int(b.t[f]) + np.arange(1, TWIN_DAYS + 1, dtype=np.int64) * DAY
        g = np.searchsorted(b.t, want)
        g = g[g < n]
        g = g[b.t[g] == want[: len(g)]]
    return g[g + hold_bars - 1 < n]


def _pick_bar(b: Bars, f: int, seed_key: str, i: int, hold_bars: int) -> int | None:
    g = twin_candidates(b, f, hold_bars)
    if not len(g):
        return None
    return int(g[int(_u(seed_key, b.inst, b.tf, i) * len(g))])


def window_drift_per_ns(b: Bars) -> float:
    """Mean log drift per ns over the window's decision span (for the drift-adjusted gross)."""
    dec_from = int(b.meta.get("dec_from", int(b.t[0]) if len(b) else 0))
    k = int(np.searchsorted(b.ta, dec_from))
    if len(b) - k < 2:
        return 0.0
    span = float(b.ta[-1] - b.ta[k])
    return float(np.log(b.c[-1] / b.c[k]) / span) if span > 0 and b.c[k] > 0 else 0.0
