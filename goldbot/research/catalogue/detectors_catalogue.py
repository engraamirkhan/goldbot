"""Catalogue detectors S02-S30 (top30_catalogue_1107.json; S01 closed, S28 data-gated). One function per family;
`p` carries the variant's parameters from specs.yaml. Simplifications against the card text are listed per variant in
specs.yaml `impl_notes` and in the pre-registration, so the reader knows exactly what was tested.

Per-cell detectors: fn(b, p, ctx) -> Events. Cross-sectional: fn(bs, p, ctx) -> dict[inst, Events] (XS_ prefix)."""
from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

from goldbot.research.catalogue import core as K
from goldbot.research.catalogue.core import EB, NAN, Ctx, Events
from goldbot.research.catalogue.data import DAY, HOUR, Bars, asof_ns
from goldbot.research.catalogue.sim import round_trip


def _hour(ta: np.ndarray) -> np.ndarray:
    return (ta // HOUR) % 24


def _date(ta: np.ndarray) -> np.ndarray:
    """UTC day number of the instant just before ta (a bar closing at 00:00 belongs to the previous day)."""
    return (ta - 1) // DAY


CASH = {"US500": (12, 20), "USTEC": (12, 20), "US30": (12, 20), "DE40": (8, 16), "UK100": (8, 16)}


def _cash_days(b: Bars) -> dict[str, np.ndarray]:
    """Per 4h bar: running cash-session OHLC of its UTC day (bars opening in [open_h, close_h)), and the daily
    cash-session ATR20 known at that bar (previous completed cash days only, plus today's when the session closed)."""
    oh, ch = CASH[b.inst]
    hr_open = (b.t // HOUR) % 24
    day = b.t // DAY
    in_cash = (hr_open >= oh) & (hr_open < ch)
    n = len(b)
    co, chh, cl, cc = (np.full(n, np.nan) for _ in range(4))
    done = np.zeros(n, bool)                       # this bar closes the cash session
    rows = []                                      # completed cash days: (day, o, h, l, c, bar index of close)
    cur = None
    for j in range(n):
        if in_cash[j]:
            if cur is None or cur[0] != day[j]:
                cur = [day[j], b.o[j], b.h[j], b.l[j], b.c[j]]
            else:
                cur[2] = max(cur[2], b.h[j])
                cur[3] = min(cur[3], b.l[j])
                cur[4] = b.c[j]
            co[j], chh[j], cl[j], cc[j] = cur[1], cur[2], cur[3], cur[4]
            if (b.ta[j] // HOUR) % 24 == ch % 24:
                done[j] = True
                rows.append((cur[0], cur[1], cur[2], cur[3], cur[4], j))
    atr_at = np.full(n, np.nan)
    prev_c = np.full(n, np.nan)
    prev_h = np.full(n, np.nan)
    prev_l = np.full(n, np.nan)
    if rows:
        r = np.array(rows, float)
        pc = np.concatenate([[r[0, 4]], r[:-1, 4]])
        tr = np.maximum(r[:, 2], pc) - np.minimum(r[:, 3], pc)
        a20 = K.wilder(tr, 20)
        jj = r[:, 5].astype(int)
        k = np.searchsorted(jj, np.arange(n), side="right") - 1      # last completed cash day at or before bar
        ok = k >= 0
        atr_at[ok] = a20[k[ok]]
        prev_c[ok], prev_h[ok], prev_l[ok] = r[k[ok], 4], r[k[ok], 2], r[k[ok], 3]
    return {"o": co, "h": chh, "l": cl, "c": cc, "done": done, "atr": atr_at, "prev_c": prev_c, "prev_h": prev_h,
            "prev_l": prev_l, "in_cash": in_cash}


# ------------------------------------------------------------------ S02 IBS (4h-built cash session)
def s02_ibs(b: Bars, p: dict, ctx: Ctx) -> Events:
    if b.inst not in CASH:
        return K.empty()
    cd = _cash_days(b)
    rng = cd["h"] - cd["l"]
    with np.errstate(invalid="ignore", divide="ignore"):
        ibs = np.where(rng > 0, (cd["c"] - cd["l"]) / rng, np.nan)
    close_bar = cd["done"]
    eb = EB()
    if p.get("exit") == "ibs60":
        eb.masks(close_bar & (ibs > 0.60), None)
        hold = 18
    else:
        eb.masks(close_bar, close_bar)
        hold = 12
    side = int(p.get("side", 1))
    thr = float(p["ibs"])
    for j in np.flatnonzero(close_bar):
        a = cd["atr"][j]
        if not np.isfinite(a) or not np.isfinite(ibs[j]):
            continue
        if (side > 0 and ibs[j] < thr) or (side < 0 and ibs[j] > thr):
            eb.add(int(j), side, stop_d=2.5 * a, hold=hold)
    return eb.build()


# ------------------------------------------------------------------ S03 vol-conditioned 3-day reversal
def _yz(b: Bars, n: int) -> np.ndarray:
    o, h, lo, c = b.o, b.h, b.l, b.c
    pc = K.shift(c, 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        ro = np.log(o / pc)
        rc = np.log(c / o)
        rs = np.log(h / c) * np.log(h / o) + np.log(lo / c) * np.log(lo / o)
    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    vo = K.rstd(np.nan_to_num(ro), n) ** 2
    vc = K.rstd(np.nan_to_num(rc), n) ** 2
    vrs = K.sma(np.nan_to_num(rs), n)
    return np.sqrt(np.maximum(vo + k * vc + (1 - k) * vrs, 0))


def _pctile_rank(x: np.ndarray, n: int) -> np.ndarray:
    """Rank of x[j] within x[j-n+1..j] as a fraction (causal)."""
    out = np.full(len(x), np.nan)
    for j in range(n - 1, len(x)):
        w = x[j - n + 1:j + 1]
        w = w[np.isfinite(w)]
        if len(w) >= n // 2 and np.isfinite(x[j]):
            out[j] = float(np.mean(w <= x[j]))
    return out


def s03_reversal(b: Bars, p: dict, ctx: Ctx) -> Events:
    yz = _yz(b, 20)
    pr = _pctile_rank(yz, 504)
    r1 = np.diff(np.log(b.c), prepend=np.nan)
    sd20 = K.rstd(np.nan_to_num(r1), 20)
    ret3 = np.log(b.c / K.shift(b.c, 3))
    z3 = ret3 / (np.sqrt(3) * sd20)
    a = K.atr(b, 20)
    gate = pr >= float(p["gate"])
    side = int(p.get("side", 1))
    zt = float(p["z"])
    eb = EB().masks(z3 > 0, z3 < 0)
    for j in np.flatnonzero(gate & np.isfinite(z3) & np.isfinite(a)):
        if (side > 0 and z3[j] <= -zt) or (side < 0 and z3[j] >= zt):
            eb.add(int(j), side, stop_d=2.0 * a[j], hold=5)
    return eb.build()


# ------------------------------------------------------------------ S04 opening gap on the 4h cash open
def s04_gap(b: Bars, p: dict, ctx: Ctx) -> Events:
    if b.inst not in CASH:
        return K.empty()
    oh, ch = CASH[b.inst]
    cd = _cash_days(b)
    hr_close = _hour(b.ta)
    eb = EB()
    g = float(p["g"])
    fade = p.get("mode") == "fade"
    for j in np.flatnonzero(hr_close == oh):          # the bar ending at the cash open: decide, fill at next open
        a, pc, ph, pl = cd["atr"][j], cd["prev_c"][j], cd["prev_h"][j], cd["prev_l"][j]
        if not (np.isfinite(a) and np.isfinite(pc)) or j + 1 >= len(b):
            continue
        open_px = b.c[j]
        gap = open_px - pc
        if abs(gap) < g * a:
            continue
        hold = max(1, (ch - oh) // 4)
        if not fade:
            eb.add(int(j), 1 if gap > 0 else -1, stop_d=1.0 * a, hold=hold)
        elif pl <= open_px <= ph:
            eb.add(int(j), -1 if gap > 0 else 1, stop_d=1.0 * a, tgt=pc, hold=hold)
    return eb.build()


# ------------------------------------------------------------------ calendar helpers (exogenous schedules in ctx)
def _bar_dates(b: Bars) -> np.ndarray:
    return _date(b.ta)


_EVER = np.iinfo(np.int64).min
_NEVER = np.iinfo(np.int64).max
Sched = dict[int, list[tuple[int, int]]]


def _published_days(ctx: Ctx, name: str, days: np.ndarray) -> Sched:
    """{day: [(published, withdrawn) ns intervals]} of a scheduled set, `days` aligned with ctx.events[name]. A ctx
    without `avail` / `until` for the set (a legacy or synthetic ctx) reads every date as known throughout and never
    withdrawn (BACKLOG 152)."""
    av = ctx.avail.get(name)
    av = np.full(len(days), _EVER, np.int64) if av is None else np.asarray(av, np.int64)
    un = ctx.until.get(name)
    un = np.full(len(days), _NEVER, np.int64) if un is None else np.asarray(un, np.int64)
    out: Sched = {}
    for d, a, u in zip(days.tolist(), av.tolist(), un.tolist()):
        out.setdefault(int(d), []).append((int(a), int(u)))
    return out


def _on(s: Sched, day: int, t: int) -> bool:
    """True when `day` is on the published schedule at instant t (published <= t < withdrawn)."""
    return any(a <= t < u for a, u in s.get(day, ()))


def s05_preholiday(b: Bars, p: dict, ctx: Ctx) -> Events:
    """A holiday counts at bar j only once published (available_utc <= the bar's close, BACKLOG 152)."""
    name = f"holiday_{_exchange(b.inst)}"
    hol = ctx.events.get(name)
    if hol is None or not len(hol):
        return K.empty()
    d = _bar_dates(b)
    hs = _published_days(ctx, name, np.asarray(hol))
    a = K.atr(b, 20)
    s200 = K.sma(b.c, 200)
    eb = EB()
    for j in range(len(b)):
        # day j is T-2 when the next weekday is T-1 (a trading day) and the weekday after that is a holiday
        now = int(b.ta[j])
        t1 = _next_weekday(int(d[j]))
        if _on(hs, t1, now):
            continue
        h = _next_weekday(t1)
        if not _on(hs, h, now) or not np.isfinite(a[j]):
            continue
        if p.get("trend") and not (b.c[j] > s200[j]):
            continue
        eb.add(j, 1, stop_d=2.0 * a[j], hold=1)
    return eb.build()


def _next_weekday(day: int) -> int:
    d = day + 1
    while (d + 3) % 7 >= 5:        # day 0 = 1970-01-01 (Thursday): (d+3)%7 -> Mon=0..Sun=6
        d += 1
    return d


def _exchange(inst: str) -> str:
    return {"DE40": "DE", "UK100": "UK"}.get(inst, "US")


def s06_macro_day(b: Bars, p: dict, ctx: Ctx) -> Events:
    ev = ctx.events.get(p["events"])
    if ev is None or not len(ev):
        return K.empty()
    ev_days = _published_days(ctx, p["events"], (np.asarray(ev) - 5 * HOUR) // DAY)  # US date (ET ~ UTC-5)
    d = _bar_dates(b)
    a = K.atr(b, 20)
    eb = EB()
    for j in range(len(b) - 1):
        now = int(b.ta[j])
        if _on(ev_days, _next_weekday(int(d[j])), now) and np.isfinite(a[j]):
            eb.add(j, 1, stop_d=2.0 * a[j], hold=1)
    return eb.build()


def s07_fomc_cycle(b: Bars, p: dict, ctx: Ctx) -> Events:
    """Day 0 = the scheduled statement day (exogenous, known in advance). Week-0 entry = the close two weekdays before
    day 0; later legs count trading bars since the last meeting day already seen (causal), skipped when the next
    scheduled meeting is fewer than 10 calendar days ahead."""
    ev = ctx.events.get("fomc_statement")
    if ev is None or not len(ev):
        return K.empty()
    d = _bar_dates(b)
    pub = _published_days(ctx, "fomc_statement", (np.asarray(ev) - 5 * HOUR) // DAY)
    iv = [(day, a, u) for day, spans in pub.items() for a, u in spans]
    days = np.array([x[0] for x in iv], dtype=np.int64)
    days_av = np.array([x[1] for x in iv], dtype=np.int64)
    days_un = np.array([x[2] for x in iv], dtype=np.int64)
    a = K.atr(b, 20)
    eb = EB()
    weeks = p["weeks"]
    last_z = -1
    for j in range(len(b)):
        now = int(b.ta[j])                     # a meeting date counts only once published (BACKLOG 152)
        if _on(pub, int(d[j]), now):
            last_z = j
        if not np.isfinite(a[j]):
            continue
        if 0 in weeks and _on(pub, _next_weekday(_next_weekday(int(d[j]))), now):
            eb.add(j, 1, stop_d=2.5 * a[j], hold=5)
            continue
        if last_z < 0:
            continue
        known = np.unique(days[(days_av <= now) & (now < days_un)])
        k = int(np.searchsorted(known, d[j], side="right"))
        nxt = int(known[k]) if k < len(known) else 10**9
        for wk in weeks:
            if wk and j - last_z == 10 * (wk // 2) - 2 and nxt - int(d[j]) >= 10:
                eb.add(j, 1, stop_d=2.5 * a[j], hold=5)
    return eb.build()


# ------------------------------------------------------------------ S08 USD around the London fix (4h)
USD_BASE = {"USDJPY", "USDCAD", "USDCHF"}
USD_QUOTE = {"EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"}


def s08_fix(b: Bars, p: dict, ctx: Ctx) -> Events:
    if b.inst not in USD_BASE | USD_QUOTE:
        return K.empty()
    long_usd = 1 if b.inst in USD_BASE else -1
    a = K.atr(b, 20)
    hr = _hour(b.ta)
    eb = EB()
    for j in range(len(b) - 1):
        if not np.isfinite(a[j]):
            continue
        if p.get("leg_a", True) and hr[j] == int(p.get("a_from", 8)):
            eb.add(j, long_usd, stop_d=1.5 * a[j], hold=(16 - int(p.get("a_from", 8))) // 4)
        if p.get("leg_b", True) and hr[j] == 16:
            eb.add(j, -long_usd, stop_d=1.5 * a[j], hold=1)
    return eb.build()


# ------------------------------------------------------------------ S11 time-series carry + trend (FX)
RATES = {"USD": "DTB3", "EUR": "IR3TIB01EZM156N", "JPY": "IR3TIB01JPM156N", "GBP": "IR3TIB01GBM156N",
         "AUD": "IR3TIB01AUM156N", "CAD": "IR3TIB01CAM156N", "CHF": "IR3TIB01CHM156N", "NZD": "IR3TIB01NZM156N"}


def _rate(ctx: Ctx, ccy: str, t: np.ndarray) -> np.ndarray:
    av, v = ctx.aux[f"rate_{ccy}"]
    return asof_ns(t, av, v)


def s11_carry_trend(b: Bars, p: dict, ctx: Ctx) -> Events:
    base, quote = b.inst[:3], b.inst[3:]
    if base not in RATES or quote not in RATES or f"rate_{base}" not in ctx.aux:
        return K.empty()
    carry = _rate(ctx, base, b.ta) - _rate(ctx, quote, b.ta)
    m = 2.5
    a = K.atr(b, 20)
    if p.get("carver"):
        r1 = np.diff(np.log(b.c), prepend=np.nan)
        vol = K.rstd(np.nan_to_num(r1), 60) * np.sqrt(260) * 100
        fc = np.clip(carry / vol * 30, -20, 20)
        friday = ((_date(b.ta) + 3) % 7) == 4
        lon, sho = fc > 5, fc < -5
        eb = EB().masks(friday & ~lon, friday & ~sho)
        for j in np.flatnonzero(friday & (lon | sho) & np.isfinite(a)):
            eb.add(int(j), 1 if lon[j] else -1, stop_d=3 * a[j])
        return eb.build()
    L = int(p["L"])
    mom = b.c / K.shift(b.c, L) - 1
    lon = (carry > m) & (mom > 0)
    sho = (carry < -m) & (mom < 0)
    # exit when the condition has failed for 5 consecutive closes
    nl = K.bars_since(lon) >= 5
    ns = K.bars_since(sho) >= 5
    eb = EB().masks(nl, ns)
    for j in np.flatnonzero((lon | sho) & np.isfinite(a)):
        eb.add(int(j), 1 if lon[j] else -1, stop_d=3 * a[j])
    return eb.build()


# ------------------------------------------------------------------ cross-sectional helpers (monthly)
def _month_end(ta: np.ndarray) -> np.ndarray:
    d = _date(ta)
    nd = np.array([_next_weekday(int(x)) for x in d])
    m0 = pd.to_datetime(d, unit="D").month
    m1 = pd.to_datetime(nd, unit="D").month
    return np.asarray(m0 != m1)


def _xs_book(bs: dict[str, Bars], score: dict[str, np.ndarray], frac: float, p: dict,
             long_ok: dict[str, np.ndarray] | None = None) -> dict[str, Events]:
    """Month-end rank of `score` across instruments (each at its own month-end bar; the universe is ranked at the
    latest close common to all, i.e. per UTC month-end date). Top `frac` long, bottom `frac` short; exit at the next
    month-end close; stop 3 x ATR20."""
    me: dict[str, dict[int, int]] = {}
    atrs = {k: K.atr(b, 20) for k, b in bs.items()}
    for k, b in bs.items():
        idx = np.flatnonzero(_month_end(b.ta))
        me[k] = {int(_date(b.ta[j:j + 1])[0]): int(j) for j in idx}
    dates = sorted(set().union(*[set(v) for v in me.values()])) if me else []
    builders = {k: EB().masks(_month_end(b.ta), _month_end(b.ta)) for k, b in bs.items()}
    for d in dates:
        cand = []
        for k in bs:
            j = me[k].get(d)
            if j is None:
                continue
            s = score[k][j]
            if np.isfinite(s) and np.isfinite(atrs[k][j]):
                cand.append((s, k, j))
        if len(cand) < 3:
            continue
        cand.sort()
        q = max(1, int(np.floor(len(cand) * frac)))
        for s, k, j in cand[-q:]:
            if long_ok is None or long_ok[k][j]:
                builders[k].add(j, 1, stop_d=3 * atrs[k][j], hold=int(p.get("hold", 23)))
        for s, k, j in cand[:q]:
            builders[k].add(j, -1, stop_d=3 * atrs[k][j], hold=int(p.get("hold", 23)))
    return {k: v.build() for k, v in builders.items()}


def _r_skip(b: Bars, lb: int, skip: int = 21) -> np.ndarray:
    return b.c / K.shift(b.c, lb) * 0 + K.shift(b.c, skip) / K.shift(b.c, lb) - 1


CLASSES = {
    "commodity": ("XAUUSD", "XAGUSD", "XTIUSD"),
    "index": ("US500", "USTEC", "DE40", "UK100", "US30"),
    "fx": ("EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDJPY", "USDCAD", "USDCHF"),
}


def _fx_currency_scores(bs: dict[str, Bars], fn: Callable[[Bars], np.ndarray]) -> dict[str, np.ndarray]:
    """Score per USD pair = score of its non-USD currency vs USD (sign flipped for USD-base pairs)."""
    out = {}
    for k, b in bs.items():
        s = fn(b)
        out[k] = s if k in USD_QUOTE else -s
    return out


def xs15_momentum(bs: dict[str, Bars], p: dict, ctx: Ctx) -> dict[str, Events]:
    lb = int(p["lb"])
    out: dict[str, Events] = {}
    rf = ctx.aux.get("rate_USD")
    for cls, members in CLASSES.items():
        sub = {k: bs[k] for k in members if k in bs}
        if len(sub) < 3:
            continue
        if cls == "fx":
            score = _fx_currency_scores(sub, lambda b: _r_skip(b, lb))
        else:
            score = {k: _r_skip(b, lb) for k, b in sub.items()}
        long_ok = None
        if p.get("abs_filter") and rf is not None:
            long_ok = {}
            for k, b in sub.items():
                r12 = b.c / K.shift(b.c, 252) - 1
                long_ok[k] = r12 * 100 > asof_ns(b.ta, rf[0], rf[1])
        out.update(_xs_book(sub, score, 1 / 3, p, long_ok))
    return out


ALL_XS = ("XAUUSD", "XAGUSD", "XTIUSD", "US500", "USTEC", "DE40", "UK100", "US30", "EURUSD", "GBPUSD", "AUDUSD",
          "NZDUSD", "USDJPY", "USDCAD", "USDCHF", "AUDJPY", "EURAUD", "GBPAUD", "EURGBP", "EURJPY", "GBPJPY",
          "USINDEX", "BTCUSD", "XAUEUR", "XAUJPY")


def xs16_52wk(bs: dict[str, Bars], p: dict, ctx: Ctx) -> dict[str, Events]:
    lb = int(p.get("lb", 252))
    score = {k: b.c / K.rmax(b.h, lb) for k, b in bs.items()}
    if p.get("within_class"):
        out: dict[str, Events] = {}
        for cls, members in CLASSES.items():
            sub = {k: bs[k] for k in members if k in bs}
            if len(sub) >= 3:
                out.update(_xs_book(sub, {k: score[k] for k in sub}, 1 / 3, p))
        return out
    return _xs_book(bs, score, 1 / 3, p)


def s16_ts_fresh(b: Bars, p: dict, ctx: Ctx) -> Events:
    lb = int(p.get("lb", 252))
    hi = K.rmax(b.h, lb)
    lo20 = K.rmin(b.l, 20)
    below = b.c < 0.90 * hi
    recent_dip = K.bars_since(below) <= 60
    cross = (b.c >= 0.98 * hi) & (K.shift(b.c, 1) < 0.98 * K.shift(hi, 1))
    eb = EB().masks(b.c < K.shift(lo20, 1), None)
    for j in np.flatnonzero(cross & recent_dip & np.isfinite(lo20)):
        eb.add(int(j), 1, stop=float(lo20[j]), hold=30)
    return eb.build()


def xs17_seasonal(bs: dict[str, Bars], p: dict, ctx: Ctx) -> dict[str, Events]:
    """Same-calendar-month seasonality. Monthly returns history = the long daily close series (ctx.aux
    'long_<inst>': (available_ns, close, is_futures_segment)), futures-sourced months excluded."""
    yrs, mn = int(p["years"]), int(p["min_years"])
    stats: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for k, b in bs.items():
        lg = ctx.aux.get(f"long_{k}")
        me = _month_end(b.ta)
        mean = np.full(len(b), np.nan)
        tst = np.full(len(b), np.nan)
        if lg is None:
            stats[k] = (mean, tst)
            continue
        av, cl, fut = lg
        s = pd.Series(cl, index=pd.to_datetime(av, unit="ns"))
        fm = pd.Series(fut, index=s.index).resample("ME").max()
        mc = s.resample("ME").last()
        mr = pd.Series(np.log((mc / mc.shift(1)).to_numpy(float)), index=mc.index)
        bad = fm.astype(bool) | fm.shift(1).fillna(1).astype(bool)
        mr[bad] = np.nan
        if ctx.window != "test_old":     # quant C4: TEST-OLD's own monthly outcomes never feed the other windows
            mi = pd.DatetimeIndex(mr.index)
            mr[(mi >= pd.Timestamp("2006-02-01")) & (mi < pd.Timestamp("2010-01-01"))] = np.nan
        mr_t = mr.index + pd.Timedelta(days=1)          # a month's return is known after its last day
        for j in np.flatnonzero(me):
            now = pd.Timestamp(int(b.ta[j]), unit="ns")
            nxt_month = (now + pd.offsets.MonthBegin(1)).month
            hist = mr[(mr_t <= now) & (pd.DatetimeIndex(mr.index).month == nxt_month)].dropna().iloc[-yrs:]
            if len(hist) >= mn:
                mean[j] = hist.mean()
                sd = hist.std(ddof=1)
                tst[j] = mean[j] / (sd / np.sqrt(len(hist))) if sd > 0 else np.nan
        stats[k] = (mean, tst)
    if p.get("xs"):
        return _xs_book(bs, {k: v[0] for k, v in stats.items()}, 1 / 3, p)
    out = {}
    for k, b in bs.items():
        mean, tst = stats[k]
        a = K.atr(b, 20)
        me = _month_end(b.ta)
        eb = EB().masks(me, me)
        for j in np.flatnonzero(me & np.isfinite(tst) & np.isfinite(a)):
            if tst[j] >= 1.0:
                eb.add(int(j), 1, stop_d=2.5 * a[j], hold=23)
            elif tst[j] <= -1.0:
                eb.add(int(j), -1, stop_d=2.5 * a[j], hold=23)
        out[k] = eb.build()
    return out


# ------------------------------------------------------------------ S20 NR7 / ID-NR4
def s20_nr(b: Bars, p: dict, ctx: Ctx) -> Events:
    tr = K.true_range(b)
    a = K.atr(b, 20)
    nlb = int(p["nr"])
    nr = tr <= K.rmin(tr, nlb)
    inside = (b.h < K.shift(b.h, 1)) & (b.l > K.shift(b.l, 1))
    setup = nr & inside if p.get("inside") else nr
    s50 = K.sma(b.c, 50)
    eb = EB()
    for j in np.flatnonzero(setup & np.isfinite(a)):
        rng = b.h[j] - b.l[j]
        if rng < 4 * round_trip(b.inst, b.c[j], b.spr[j], ctx.aux.get("costs"), int(b.ta[j])):
            continue
        bl, ss = b.h[j] + 0.05 * a[j], b.l[j] - 0.05 * a[j]
        smin = 0.3 * a[j]
        lstop = min(b.l[j], bl - smin)
        sstop = max(b.h[j], ss + smin)
        long = dict(kind=1, px=bl, valid=1, stop=lstop, tgt=bl + 1.5 * a[j], hold=2)
        short = dict(kind=1, px=ss, valid=1, stop=sstop, tgt=ss - 1.5 * a[j], hold=2)
        if p.get("trend"):
            if not np.isfinite(s50[j]):
                continue
            if b.c[j] > s50[j]:
                eb.add(int(j), 1, **long)
            else:
                eb.add(int(j), -1, **short)
        else:
            eb.oco_pair(int(j), long=long, short=short)
    return eb.build()


# ------------------------------------------------------------------ S21 open-range stretch breakout
def s21_stretch(b: Bars, p: dict, ctx: Ctx) -> Events:
    tr = K.true_range(b)
    a14 = K.atr(b, 14)
    k = float(p["k"])
    if p.get("crabel"):
        st = K.sma(np.minimum(np.abs(b.o - b.h), np.abs(b.o - b.l)), 10)
    else:
        st = tr
    eb = EB()
    for j in np.flatnonzero(np.isfinite(st) & np.isfinite(a14)):
        o_next = b.c[j]                     # continuous CFD bars: the next open is the close of bar j
        up, dn = o_next + k * st[j], o_next - k * st[j]
        risk = min(up - dn, 1.5 * a14[j])
        if risk <= 0:
            continue
        eb.oco_pair(int(j), long=dict(kind=1, px=up, valid=1, stop=up - risk, hold=1),
                    short=dict(kind=1, px=dn, valid=1, stop=dn + risk, hold=1))
    return eb.build()


# ------------------------------------------------------------------ S22 squeeze
def s22_squeeze(b: Bars, p: dict, ctx: Ctx) -> Events:
    m20 = K.sma(b.c, 20)
    sd = K.rstd(b.c, 20)
    up, lo = m20 + 2 * sd, m20 - 2 * sd
    e20 = K.ema(b.c, 20)
    a = K.atr(b, 20)
    hold = 30 if b.tf == "4h" else 10
    eb = EB().masks(b.c < lo, b.c > up)
    if p.get("bandwidth"):
        bw = (up - lo) / m20
        low120 = bw <= K.rmin(bw, 120) * 1.0000001
        recent = K.bars_since(low120) <= 6
        for jb in map(int, np.flatnonzero(recent & np.isfinite(a) & np.isfinite(e20))):
            if b.c[jb] > up[jb] and e20[jb] < b.c[jb]:
                eb.add(jb, 1, stop=e20[jb], tgt_d=2 * a[jb], hold=hold)
            elif b.c[jb] < lo[jb] and e20[jb] > b.c[jb]:
                eb.add(jb, -1, stop=e20[jb], tgt_d=2 * a[jb], hold=hold)
        return eb.build()
    km = float(p["kc"])
    kup, klo = e20 + km * a, e20 - km * a
    sq = (up < kup) & (lo > klo)
    run = 0
    start = 0
    for j in range(len(b)):
        if sq[j]:
            if run == 0:
                start = j
            run += 1
            continue
        if run >= 6 and np.isfinite(a[j]) and np.isfinite(e20[j]):
            rh, rl = float(np.max(b.h[start:j])), float(np.min(b.l[start:j]))
            bl, ss = rh + 0.1 * a[j], rl - 0.1 * a[j]
            long = dict(kind=1, px=bl, valid=3, stop=min(e20[j], bl - 0.3 * a[j]), tgt=bl + 2 * a[j], hold=hold)
            short = dict(kind=1, px=ss, valid=3, stop=max(e20[j], ss + 0.3 * a[j]), tgt=ss - 2 * a[j], hold=hold)
            eb.oco_pair(j, long=long, short=short)
        run = 0
    return eb.build()


# ------------------------------------------------------------------ S23 Turtle Soup
def s23_soup(b: Bars, p: dict, ctx: Ctx) -> Events:
    n = 20
    A = int(p["A"])
    a = K.atr(b, 20)
    rng = K.rmax(b.h, n) - K.rmin(b.l, n)
    eb = EB()
    for j in range(n + 1, len(b)):
        if not np.isfinite(a[j]):
            continue
        wl = b.l[j - n:j]
        wh = b.h[j - n:j]
        pl, ph = float(wl.min()), float(wh.max())
        if b.l[j] < pl and (j - (j - n + int(np.argmin(wl)))) >= A:
            lvl = pl + 0.1 * a[j]
            stop = b.l[j] - 0.1 * a[j]
            if b.c[j] >= lvl:
                eb.add(j, 1, stop=stop, tgt_d=0.5 * rng[j], hold=5)
            elif p.get("plus_one"):
                eb.add(j, 1, kind=1, px=lvl, valid=1, stop=stop, tgt=lvl + 0.5 * rng[j], hold=5)
        if b.h[j] > ph and (j - (j - n + int(np.argmax(wh)))) >= A:
            lvl = ph - 0.1 * a[j]
            stop = b.h[j] + 0.1 * a[j]
            if b.c[j] <= lvl:
                eb.add(j, -1, stop=stop, tgt_d=0.5 * rng[j], hold=5)
            elif p.get("plus_one"):
                eb.add(j, -1, kind=1, px=lvl, valid=1, stop=stop, tgt=lvl - 0.5 * rng[j], hold=5)
    return eb.build()


# ------------------------------------------------------------------ S24 Holy Grail
def s24_grail(b: Bars, p: dict, ctx: Ctx) -> Events:
    ax, pdi, ndi = K.adx(b, 14)
    e = K.ema(b.c, int(p.get("ema", 20)))
    a = K.atr(b, 20)
    amin = float(p["adx"])
    above5 = np.ones(len(b), bool)
    below5 = np.ones(len(b), bool)
    for k in range(1, 6):
        above5 &= K.shift(b.c, k) > K.shift(e, k)
        below5 &= K.shift(b.c, k) < K.shift(e, k)
    rising = ax > K.shift(ax, 3)
    down3 = (ax < K.shift(ax, 1)) & (K.shift(ax, 1) < K.shift(ax, 2)) & (K.shift(ax, 2) < K.shift(ax, 3))
    eb = EB().masks(down3, down3)
    for j in np.flatnonzero((ax > amin) & rising & np.isfinite(a) & np.isfinite(e)):
        if pdi[j] > ndi[j] and above5[j] and b.l[j] <= e[j]:
            px = b.h[j] + 0.05 * a[j]
            stop = float(np.min(b.l[max(0, j - 2):j + 1])) - 0.1 * a[j]
            eb.add(int(j), 1, kind=1, px=px, valid=3, stop=stop, tgt=px + 2 * (px - stop), hold=15)
        elif ndi[j] > pdi[j] and below5[j] and b.h[j] >= e[j]:
            px = b.l[j] - 0.05 * a[j]
            stop = float(np.max(b.h[max(0, j - 2):j + 1])) + 0.1 * a[j]
            eb.add(int(j), -1, kind=1, px=px, valid=3, stop=stop, tgt=px - 2 * (stop - px), hold=15)
    return eb.build()


# ------------------------------------------------------------------ S26 fear spike (realised vol z)
def s26_fear(b: Bars, p: dict, ctx: Ctx) -> Events:
    rv = _yz(b, 21)
    z = (rv - K.sma(rv, 252)) / K.rstd(rv, 252)
    a14 = K.atr(b, 14)
    eb = EB().masks(rv < K.sma(rv, 10), None)
    for j in np.flatnonzero((z > float(p["z"])) & np.isfinite(a14)):
        eb.add(int(j), 1, stop_d=3 * a14[j], hold=10)
    return eb.build()


# ------------------------------------------------------------------ S27 multi-speed EWMA trend
def s27_ewma(b: Bars, p: dict, ctx: Ctx) -> Events:
    speeds = [(8, 24), (16, 48)] if p.get("fast") else [(8, 24), (16, 48), (32, 96)]
    sd63 = K.rstd(b.c, 63)
    us = []
    for s, lng in speeds:
        x = (K.ema(b.c, s) - K.ema(b.c, lng)) / sd63
        y = x / K.rstd(np.nan_to_num(x), 252)
        us.append(y * np.exp(-y ** 2 / 4) / 0.89)
    S = np.mean(us, axis=0)
    gi, go = float(p["gin"]), float(p["gout"])
    a = K.atr(b, 20)
    eb = EB().masks(S < go, S > -go)
    for j in np.flatnonzero(np.isfinite(S) & np.isfinite(a)):
        if S[j] > gi:
            eb.add(int(j), 1, stop_d=3 * a[j])
        elif S[j] < -gi:
            eb.add(int(j), -1, stop_d=3 * a[j])
    return eb.build()


# ------------------------------------------------------------------ S30 FX XS 1-month momentum (currencies)
def xs30_fx_mom(bs: dict[str, Bars], p: dict, ctx: Ctx) -> dict[str, Events]:
    L = int(p["months"]) * 21
    sub = {k: b for k, b in bs.items() if k in USD_BASE | USD_QUOTE}
    if len(sub) < 3:
        return {}
    score = _fx_currency_scores(sub, lambda b: b.c / K.shift(b.c, L) - 1)
    frac = 2 / max(len(sub), 1)
    return _xs_book(sub, score, frac, p)


CATALOGUE_DETECTORS = {
    "S02": s02_ibs, "S03": s03_reversal, "S04": s04_gap, "S05": s05_preholiday, "S06": s06_macro_day,
    "S07": s07_fomc_cycle, "S08": s08_fix, "S11": s11_carry_trend, "S16TS": s16_ts_fresh, "S20": s20_nr,
    "S21": s21_stretch, "S22": s22_squeeze, "S23": s23_soup, "S24": s24_grail, "S26": s26_fear, "S27": s27_ewma,
}
CATALOGUE_XS = {"S15": xs15_momentum, "S16XS": xs16_52wk, "S17": xs17_seasonal, "S30": xs30_fx_mom}
_ = (NAN,)
