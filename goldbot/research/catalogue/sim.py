"""Event -> trade simulation on one cell's bars, with costs and the matched control twin (quant A2/A3).

Fills
  market   open of bar i+1 (never the signal bar: the decision is at its close)
  stop     first bar in i+1..i+valid whose high (long) / low (short) reaches px, at max(open, px) / min(open, px)
  limit    first bar in i+1..i+valid whose low (long) / high (short) reaches px, at min(open, px) / max(open, px)
  close-only bars (co): orders, stops and targets are checked and filled AT THE CLOSE
Fill bar from finer bars (quant C10, audit D5): "the fill bar is resolved from 1m bars wherever they exist; otherwise
the unit is excluded and listed (dispatcher.unit_trades); stop-entry fills pay the measured stop overshoot of the
cost of record, the same as stop exits". A stop/limit order filled INSIDE bar f (not at its open) is filled at the
first finer bar of f that reaches px (at max/min(that bar's open, px)) and walked on the finer bars of f from there;
from bar f+1 the walk goes on as below. The random-side twin starts from the same finer bar. Without finer bars the
fill bar's stop is checked against its whole range, including the part before the fill: B04 lost 0.49 R/trade on
zero-edge data (false kills). A fill bar whose finer bars never reach px (source mismatch) keeps that whole-bar rule
and is counted ("fine_miss").
Exits, per bar from the fill bar: gap through the stop at the open (after the fill bar) -> open; stop touched ->
stop (+ overshoot); target touched -> target; both on one bar -> STOP (conservative); then the exit mask at the close;
then the time exit at the close of the hold-th bar; data end -> last close. One position per cell at a time: an event
whose fill would land before the open trade's exit is skipped.

Risk floor (EVAL-1 precondition iii, audit D1): "stop distance >= max(0.25 x ATR20 of the decision timeframe,
3 x round-trip cost), else the event is not traded (counted as an event, not as a trade)". ATR20 is the Wilder ATR at
the decision bar i; an event whose ATR20 is not yet defined is not traded either. Without it a flat holiday bar gave a
risk of 0.01 points and one trade of +9,331 R (S21-V2, US30 2013-12-26).

Order risk (quant C7, eval1_signoff_1107): "The floor is decided once, at the signal bar, on the nominal order price
(market orders: the decision close). A filled order is never dropped. A fill at or beyond its stop exits at the fill
bar's price at once, as a loss of at least -1 R." The frozen fix checked the floor again at the fill and dropped a fill
through the stop: the worst fills a limit order gets vanished (net up 0.01-0.05 R/trade). A fill at or past its target
exits at once at the fill too (a take-profit already through is closed at market).

R: risk = the ORDER's stop distance |order price - stop| (market: the decision close; a distance stop: stop_d), the
risk the position is sized on when the order is placed; gross R = side (exit - fill) / risk. A fill that gaps toward
the stop therefore risks less than 1 R and one that gaps past a stop entry more; measuring R on the fill's own distance
would turn a gap to just short of the stop into the D1 tiny-risk blow-up the floor exists to stop. Cost (price units) =
round trip + overshoot on stop exits + overshoot on stop-entry fills (C10) + swap per 21:00 UTC rollover held; cost R = cost / risk. Stress = 1.5 x cost.

Controls (A2), one twin per trade, simulated by the same walk:
  random_side  same fill bar and price, OPPOSITE side, mirrored stop/target distances, exit no later than the real
               trade's exit bar; the random-side twin's expected gross = (real + opposite) / 2, so the matched excess
               is (real - opposite) / 2. When both OCO legs trigger on one bar the REAL trade is a full loss (card rule)
               but the opposite twin is walked like any other: forcing it to -1 too (audit D2) zeroed the excess on
               exactly the bars that cost the straddle, while the surviving straddles were selected on the bar's range
  random_time  same side, same risk as a fraction of price and the same target R multiple ON THE TRADE'S SIDE (a short
               twin's target is below its entry; audit D3), entered at the open of a seeded random bar of the same
               instrument/TF AFTER the real fill, held the same number of bars; excess = real - twin. No twin bar (the hold does not
               fit in the data): the real trade is kept, its control and excess are NaN (left out of the excess
               statistic only; item 4). Intraday: the
               same time of day on one of the next 7 calendar days that has that bar (same session,
               independent of the signal); 1d: one of the next 20 bars; 1w: the next 8. Never before the fill: a twin drawn from the
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
         mask: np.ndarray | None, fill_is_open: bool, j0: int | None = None) -> Walk:
    """Walk from fill bar f (or from bar `j0` > f, when bar f was walked on finer bars) to `last` (inclusive).
    `fill_is_open`: the fill was at bar f's open (market), so bar f's full range follows the fill; for a stop/limit
    fill inside bar f the stop is still checked on bar f (conservative; the no-finer-bars case)."""
    o, h, lo, c, co = b.o, b.h, b.l, b.c, b.co
    for j in range(f if j0 is None else j0, last + 1):
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


INTRABAR = (1, 2)               # stop and limit orders: may fill inside a bar


@dataclass
class FineFill:
    """An order resolved on the finer bars of its fill bar: first finer bar m, the end of the bar's finer bars z."""
    m: int
    z: int
    fill: float


def fine_fill(fine: Bars, b: Bars, f: int, side: int, kind: int, px: float) -> FineFill | None:
    """The first finer bar inside bar f that reaches the order price, and the fill (stop: max/min(open, px); limit:
    min/max(open, px)); None when no finer bar of f reaches it or the fill lies outside bar f's [low, high] (a source
    mismatch: the caller keeps the whole-bar rule and counts a fine_miss). Bar f's finer bars end at its close or at
    the next bar's open, whichever is first (quant re-verify R2: a late close stamp must not reach into bar f+1)."""
    a = int(np.searchsorted(fine.t, b.t[f], "left"))
    end = int(b.t[f + 1]) if f + 1 < len(b) else int(b.ta[f])
    z = int(np.searchsorted(fine.t, min(int(b.ta[f]), end), "left"))
    if z <= a:
        return None
    up = (kind == 1) == (side > 0)                 # buy stop / sell limit trigger on the high
    hit = fine.h[a:z] >= px if up else fine.l[a:z] <= px
    if not hit.any():
        return None
    m = a + int(np.argmax(hit))
    o = float(fine.o[m])
    fill = max(o, px) if up else min(o, px)
    if not (b.l[f] <= fill <= b.h[f]):
        return None
    return FineFill(m, z, fill)


def _fine_exit(fine: Bars, ff: FineFill, f: int, side: int, stop: float, tgt: float) -> Walk | None:
    """The exit inside bar f on its finer bars from the fill bar m (same rules as `walk`; the fill bar m itself is still
    checked whole: the residual pre-fill part is one finer bar)."""
    o, h, lo = fine.o[ff.m:ff.z], fine.h[ff.m:ff.z], fine.l[ff.m:ff.z]
    later = np.arange(len(o)) > 0
    gs = later & (side * (o - stop) <= 0)
    hs = lo <= stop if side > 0 else h >= stop
    if np.isfinite(tgt):
        gt = later & (side * (o - tgt) >= 0)
        ht = h >= tgt if side > 0 else lo <= tgt
    else:
        gt = ht = np.zeros(len(o), bool)
    hit = gs | hs | gt | ht
    if not hit.any():
        return None
    k = int(np.argmax(hit))
    t = int(fine.t[ff.m + k])
    if gs[k]:
        return Walk(f, float(o[k]), 1, t)
    if hs[k]:
        return Walk(f, stop, 1, t)
    if gt[k]:
        return Walk(f, float(o[k]), 2, t)
    return Walk(f, tgt, 2, t)


def walk_fine(b: Bars, fine: Bars, ff: FineFill, f: int, side: int, fill: float, stop: float, tgt: float, last: int,
              mask: np.ndarray | None) -> Walk:
    """Walk of an order filled inside bar f at finer bar ff.m: the rest of bar f on its finer bars, then `walk`."""
    w = _fine_exit(fine, ff, f, side, stop, tgt)
    if w is not None:
        return w
    if mask is not None and mask[f]:
        return Walk(f, float(b.c[f]), 3, int(b.ta[f]))
    if last <= f:
        return Walk(f, float(b.c[f]), 0, int(b.ta[f]))
    return walk(b, f, side, fill, stop, tgt, last, mask, True, j0=f + 1)


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


def _levels(ev: Events, k: int, fill: float) -> tuple[float, float]:
    """(stop, target) of event k filled at `fill`: absolute prices, or distances from the fill (target nan: none)."""
    side = int(ev.side[k])
    stop = float(ev.stop[k]) if np.isfinite(ev.stop[k]) else fill - side * float(ev.stop_d[k])
    tgt = float(ev.tgt[k]) if np.isfinite(ev.tgt[k]) else (
        fill + side * float(ev.tgt_d[k]) if np.isfinite(ev.tgt_d[k]) else np.nan)
    return stop, tgt


def order_risk(b: Bars, ev: Events, k: int, a20: np.ndarray) -> float | None:
    """Event k's risk at its order price (market: the decision close), decided at the signal bar before any fill (C7);
    None when the stop or target is on the wrong side of the order price or the stop is inside the risk floor."""
    i = int(ev.i[k])
    side = int(ev.side[k])
    px = float(ev.px[k]) if np.isfinite(ev.px[k]) else float(b.c[i])
    stop, tgt = _levels(ev, k, px)
    risk = side * (px - stop)
    if not np.isfinite(risk) or risk <= 0 or (np.isfinite(tgt) and side * (tgt - px) <= 0):
        return None
    return risk if risk >= risk_floor(b.inst, px, float(b.spr[i]), float(a20[i])) else None


def _opposite(b: Bars, start: int, side: int, fill: float, dist: float, risk: float, tgt: float, last: int,
              at_open: bool, fine: Bars | None = None, ff: FineFill | None = None) -> float:
    """R (on the real trade's order risk) of the opposite-side twin of a trade: same bar and price (and the same finer
    fill bar `ff`, when resolved), mirrored stop distance `dist` and target distance."""
    ctgt = fill - (tgt - fill) if np.isfinite(tgt) else np.nan
    if fine is not None and ff is not None:
        cw = walk_fine(b, fine, ff, start, -side, fill, fill + side * dist, ctgt, last, None)
    else:
        cw = walk(b, start, -side, fill, fill + side * dist, ctgt, last, None, at_open)
    return -side * (cw.exit_px - fill) / risk


def _u(*key: object) -> float:
    d = hashlib.sha256("|".join(map(str, key)).encode()).digest()
    return int.from_bytes(d[:8], "big") / 2**64


TRADE_COLS = ("i", "f", "x", "side", "t_dec", "t_fill", "t_exit", "fill", "risk", "gross", "cost", "kind", "nights",
              "ctrl_gross", "excess", "drift_adj")


def cost_price(inst: str, side: int, fill: float, spr: float, kind: int, t_fill: int, t_exit: int,
               stop_entry: bool = False) -> float:
    """Round trip + the stop overshoot on a stop exit (kind 1) and on a stop-entry fill (C10) + swap per rollover."""
    rt = round_trip(inst, fill, spr)
    c = rt + (overshoot(inst, rt) if kind == 1 else 0.0) + (overshoot(inst, rt) if stop_entry else 0.0)
    return c + nights(t_fill, t_exit) * swap_rate(inst, side) / 360.0 * fill


def risk_floor(inst: str, fill: float, spr: float, atr20: float) -> float:
    """Smallest tradable stop distance (price units): max(0.25 x ATR20, 3 x round trip); nan when ATR20 is unknown."""
    if not np.isfinite(atr20):
        return float("nan")
    return max(RISK_FLOOR_ATR * atr20, RISK_FLOOR_RT * round_trip(inst, fill, spr))


def simulate(b: Bars, ev: Events, *, control: str, seed_key: str, drift_per_ns: float = 0.0,
             counts: dict[str, int] | None = None, fine: Bars | None = None) -> np.ndarray:
    """Trades of one cell as a float array with columns TRADE_COLS (decisions before the window's dec_from and fills
    at or after its end are dropped; a trade still open at the data end exits at the last close). `fine`: finer bars
    of the same instrument (1m) that resolve stop/limit fills inside a bar (C10). `counts`, when given, gets
    "risk_floor" += the events not traded because their stop was inside the risk floor, and "fine_miss" += the
    intrabar fills whose finer bars never reach the order price."""
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
        risk = order_risk(b, ev, k, a20)
        sib = _oco_sibling(ev, k)
        if risk is None or (sib is not None and order_risk(b, ev, sib, a20) is None):
            _count(counts, "risk_floor")      # decided at the signal bar; an OCO pair is placed whole or not at all
            continue
        fr = _fill(b, i, side, int(ev.kind[k]), float(ev.px[k]), int(ev.valid[k]))
        if fr is None:
            continue
        f, fill, at_open = fr
        if f <= busy_until or int(b.t[f]) >= end:
            continue
        both = False
        fr2: tuple[int, float, bool] | None = None
        if sib is not None and sib > k:
            fr2 = _fill(b, i, int(ev.side[sib]), int(ev.kind[sib]), float(ev.px[sib]), int(ev.valid[sib]))
            if fr2 is not None and fr2[0] < f:
                continue                      # the other leg filled first: this one is cancelled
            both = fr2 is not None and fr2[0] == f
        elif sib is not None:
            fr1 = _fill(b, i, int(ev.side[sib]), int(ev.kind[sib]), float(ev.px[sib]), int(ev.valid[sib]))
            if fr1 is not None and fr1[0] <= f:
                continue                      # the first leg filled first or on the same bar (handled there)
        ff: FineFill | None = None
        kind_k = int(ev.kind[k])
        if fine is not None and kind_k in INTRABAR and not at_open and not b.co[f]:
            ff = fine_fill(fine, b, f, side, kind_k, float(ev.px[k]))
            if ff is None:
                _count(counts, "fine_miss")
            else:
                fill = ff.fill
        stop, tgt = _levels(ev, k, fill)
        dist = side * (fill - stop)           # the fill's own stop distance (<= 0: filled through the stop)
        start = f + 1 if int(ev.kind[k]) == 3 else f
        if start >= n:
            continue
        last = min(n - 1, start + int(ev.hold[k]) - 1)
        mask = ev.exit_long if side > 0 else ev.exit_short
        t_fill = int(fine.t[ff.m]) if fine is not None and ff is not None else int(b.t[f])
        instant = False                       # exits at the fill: through its stop or past its target (C7)
        if both:                              # both OCO legs on one bar: full loss (card S20/S21 conservative rule)
            w = Walk(f, stop, 1, t_fill)
        elif dist <= 0:
            w, instant = Walk(f, fill, 1, t_fill), True
        elif np.isfinite(tgt) and side * (tgt - fill) <= 0:
            w, instant = Walk(f, fill, 2, t_fill), True
        elif fine is not None and ff is not None:
            w = walk_fine(b, fine, ff, f, side, fill, stop, tgt, last, mask)
        else:
            w = walk(b, start, side, fill, stop, tgt, last, mask, at_open)
        busy_until = w.exit_bar
        gross = side * (w.exit_px - fill) / risk
        if instant and w.kind == 1:
            gross = min(-1.0, gross)          # a fill at or beyond its stop: a loss of at least -1 R
        cost = cost_price(b.inst, side, fill, float(b.spr[f]), w.kind, t_fill, w.t_exit, kind_k == 1) / risk
        # ---- control twin
        if control == "random_side":
            clast = w.exit_bar if w.kind in (0, 3) else last
            # an instant exit: the opposite at the same price and instant is closed at once too (0 R)
            opp = 0.0 if instant else _opposite(b, start, side, fill, dist, risk, tgt, clast, at_open, fine, ff)
            if both and fr2 is not None and sib is not None:
                # the same bar touched the other leg too: the twin also takes the opposite of that leg, so every leg
                # the market touched has its opposite counted once (unbiased on a martingale; audit D2)
                side2 = int(ev.side[sib])
                kind2 = int(ev.kind[sib])
                ff2 = fine_fill(fine, b, f, side2, kind2, float(ev.px[sib])) if (
                    fine is not None and kind2 in INTRABAR and not fr2[2] and not b.co[f]) else None
                fill2 = ff2.fill if ff2 is not None else fr2[1]
                stop2, tgt2 = _levels(ev, sib, fill2)
                dist2 = side2 * (fill2 - stop2)
                risk2 = order_risk(b, ev, sib, a20)
                if dist2 > 0 and risk2 is not None:
                    last2 = min(n - 1, start + int(ev.hold[sib]) - 1)
                    opp += _opposite(b, start, side2, fill2, dist2, risk2, tgt2, last2, fr2[2], fine, ff2)
            ctrl = 0.5 * (gross + opp)
        else:
            hold_bars = max(1, w.exit_bar - start + 1)
            g = _pick_bar(b, f, seed_key, i, hold_bars)
            if g is None:
                ctrl = np.nan                 # no room for a twin: the real trade is kept (item 4)
            else:
                cfill = b.c[g] if b.co[g] else b.o[g]
                crisk = risk / fill * cfill
                cstop = cfill - side * crisk
                ctgt = cfill + (tgt - fill) / risk * crisk if np.isfinite(tgt) else np.nan    # tgt - fill: side
                cw = walk(b, g, side, cfill, cstop, ctgt, g + hold_bars - 1, None, not b.co[g])
                ctrl = side * (cw.exit_px - cfill) / crisk
        drift_adj = gross - side * drift_per_ns * (w.t_exit - t_fill) * fill / risk
        out.append((i, f, w.exit_bar, side, int(b.ta[i]), t_fill, w.t_exit, fill, risk, gross, cost, w.kind,
                    nights(t_fill, w.t_exit), ctrl, gross - ctrl, drift_adj))
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
