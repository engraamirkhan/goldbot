"""Breakout (B), false-breakout (F) and chart-pattern (C) detectors (patterns_top30_1107.json; B01/B02/B03/B06/F01
are screened once under S16/S20/S21/S22/S23). Pivots are the common ZZ(k) zigzag (core.zigzag): a pivot is usable only
from its confirmation bar. SR(n) levels are core.sr_level. Every detector is a per-cell fn(b, p, ctx) -> Events."""
from __future__ import annotations

import numpy as np

from goldbot.research.catalogue import core as K
from goldbot.research.catalogue.core import EB, Ctx, Events
from goldbot.research.catalogue.data import DAY, HOUR, MIN, TF_NS, Bars


def _tf_min(b: Bars) -> int:
    return int(TF_NS[b.tf] // MIN)


def _hour(ta: np.ndarray) -> np.ndarray:
    return (ta // HOUR) % 24


def _grid(b: Bars, p: dict, price: float) -> float:
    if b.inst == "XAUUSD":
        return float(p.get("gold_grid", 50.0))
    lg = np.floor(np.log10(price))
    fx = len(b.inst) == 6 and b.inst not in ("XAGUSD", "XAUEUR", "XAUJPY", "XTIUSD", "BTCUSD")
    return float(10 ** (lg - 2)) if fx else float(10 ** (lg - 1))


# ------------------------------------------------------------------ B04 round-number cascade / F03 rejection
def b04_round_break(b: Bars, p: dict, ctx: Ctx) -> Events:
    a = K.atr(b, 14)
    gate = None
    if p.get("adx"):
        ax, _, _ = K.adx(b, 14)
        gate = ax > 20
    eb = EB()
    hh20 = K.rmax(b.h, 20)
    ll20 = K.rmin(b.l, 20)
    for j in range(21, len(b) - 1):
        if not np.isfinite(a[j]) or (gate is not None and not gate[j]):
            continue
        g = _grid(b, p, b.c[j])
        up = np.ceil(b.c[j] / g) * g
        dn = np.floor(b.c[j] / g) * g
        if up - b.c[j] <= 0.5 * a[j] and hh20[j - 1] < up and b.h[j] < up:
            if p.get("confirm"):
                pass
            else:
                eb.add(j, 1, kind=1, px=up + 0.1 * a[j], valid=6, stop=up - 0.5 * a[j],
                       tgt=up + 0.1 * a[j] + 1.5 * a[j], hold=12)
        if b.c[j] - dn <= 0.5 * a[j] and ll20[j - 1] > dn and b.l[j] > dn:
            if not p.get("confirm"):
                eb.add(j, -1, kind=1, px=dn - 0.1 * a[j], valid=6, stop=dn + 0.5 * a[j],
                       tgt=dn - 0.1 * a[j] - 1.5 * a[j], hold=12)
        if p.get("confirm"):
            # close-confirmed: first close beyond L + 0.1 ATR after a setup within the last 6 bars, L untouched before
            pc = b.c[j - 1]
            gp = _grid(b, p, pc)
            L_up = np.ceil(pc / gp) * gp
            if b.c[j] > L_up + 0.1 * a[j] and pc < L_up and L_up - pc <= 0.5 * a[j] and hh20[j - 1] < L_up:
                eb.add(j, 1, stop=L_up - 0.5 * a[j], tgt_d=1.5 * a[j], hold=12)
            L_dn = np.floor(pc / gp) * gp
            if b.c[j] < L_dn - 0.1 * a[j] and pc > L_dn and pc - L_dn <= 0.5 * a[j] and ll20[j - 1] > L_dn:
                eb.add(j, -1, stop=L_dn + 0.5 * a[j], tgt_d=1.5 * a[j], hold=12)
    return eb.build()


def f03_round_reject(b: Bars, p: dict, ctx: Ctx) -> Events:
    a = K.atr(b, 14)
    hours = _hour(b.ta)
    eb = EB()
    for j in range(11, len(b)):
        if not np.isfinite(a[j]):
            continue
        if p.get("ldn_ny") and not (7 <= hours[j] < 21):
            continue
        g = _grid(b, p, b.c[j])
        L = np.round(b.h[j] / g) * g
        approach_up = b.h[j] - float(np.min(b.l[j - 10:j + 1])) >= 1.5 * a[j]
        pen_ok = (b.h[j] >= L - 0.1 * a[j]) and (b.h[j] <= L + 0.3 * a[j])
        if p.get("penetrate"):
            pen_ok = pen_ok and b.h[j] > L
        if approach_up and pen_ok and b.c[j] < L - 0.1 * a[j]:
            eb.add(j, -1, stop=L + 0.5 * a[j], tgt_d=1.5 * a[j], hold=10)
        L2 = np.round(b.l[j] / g) * g
        approach_dn = float(np.max(b.h[j - 10:j + 1])) - b.l[j] >= 1.5 * a[j]
        pen2 = (b.l[j] <= L2 + 0.1 * a[j]) and (b.l[j] >= L2 - 0.3 * a[j])
        if p.get("penetrate"):
            pen2 = pen2 and b.l[j] < L2
        if approach_dn and pen2 and b.c[j] > L2 + 0.1 * a[j]:
            eb.add(j, 1, stop=L2 - 0.5 * a[j], tgt_d=1.5 * a[j], hold=10)
    return eb.build()


# ------------------------------------------------------------------ B05 SR break-and-retest / F02 false break
def _vol_ok(b: Bars, j: int) -> bool:
    v = b.vol
    if not np.isfinite(v[j]) or j < 20:
        return False
    med = np.nanmedian(v[j - 20:j])
    return bool(np.isfinite(med) and v[j] > 1.5 * med)


def b05_break_retest(b: Bars, p: dict, ctx: Ctx) -> Events:
    a = K.atr(b, 14)
    piv = K.zigzag(b, K.zz_k(b.tf), a)
    nmin = int(p["n"])
    eb = EB()
    for j in range(30, len(b)):
        if not np.isfinite(a[j]):
            continue
        for typ, side in ((1, 1), (-1, -1)):
            lvl = K.sr_level(piv, j - 1, typ, nmin, a[j - 1])
            if not np.isfinite(lvl):
                continue
            broke = side * (b.c[j] - lvl) >= 0.25 * a[j] and side * (b.c[j - 1] - lvl) < 0.25 * a[j - 1]
            if not broke:
                continue
            if p.get("vol") and not _vol_ok(b, j):
                continue
            if p.get("no_retest"):
                eb.add(j, side, stop=lvl - side * 0.75 * a[j], tgt_d=2.0 * a[j], hold=20)
            else:
                px = lvl + side * 0.1 * a[j]
                eb.add(j, side, kind=2, px=px, valid=8, stop=lvl - side * 0.75 * a[j], tgt=px + side * 2.0 * a[j],
                       hold=20)
    return eb.build()


def f02_false_break(b: Bars, p: dict, ctx: Ctx) -> Events:
    a = K.atr(b, 14)
    piv = K.zigzag(b, K.zz_k(b.tf), a)
    nmin = int(p["n"])
    adx_ok = None
    if p.get("no_trend"):
        ax, _, _ = K.adx(b, 14)
        adx_ok = ~(ax > 25)
    eb = EB()
    for j in range(30, len(b)):
        if not np.isfinite(a[j]) or (adx_ok is not None and not adx_ok[j]):
            continue
        for typ, side in ((1, -1), (-1, 1)):            # resistance pierced -> short fade; support -> long
            for back in range(0, 3):                     # penetration bar j-back, close back inside by bar j
                q = j - back
                lvl = K.sr_level(piv, q - 1, typ, nmin, a[q - 1])
                if not np.isfinite(lvl):
                    continue
                ext = b.h[q] if typ == 1 else b.l[q]
                pen = (ext - lvl) * typ
                if not (0.1 * a[q] <= pen <= 1.0 * a[q]):
                    continue
                inside = (lvl - b.c[j]) * typ >= 0.1 * a[j]
                if not inside:
                    continue
                if any((lvl - b.c[r]) * typ >= 0.1 * a[r] for r in range(q, j)):
                    continue                             # the close back inside happened earlier: already taken
                if p.get("wick"):
                    rng = b.h[q] - b.l[q]
                    wick = (b.h[q] - max(b.o[q], b.c[q])) if typ == 1 else (min(b.o[q], b.c[q]) - b.l[q])
                    if rng <= 0 or wick < 0.5 * rng:
                        continue
                extreme = float(np.max(b.h[q:j + 1]) if typ == 1 else np.min(b.l[q:j + 1]))
                stop = extreme + typ * 0.2 * a[j]
                if abs(stop - b.c[j]) < 0.5 * a[j]:
                    stop = b.c[j] + typ * 0.5 * a[j]
                eb.add(j, side, stop=stop, tgt_d=2.0 * abs(stop - b.c[j]), hold=15)
                break
    return eb.build()


# ------------------------------------------------------------------ B07 triangles (and the base for F04)
def _triangles(b: Bars, piv: K.Pivots, a: np.ndarray, five: bool, kinds: tuple[str, ...]) -> list[tuple]:
    """(break bar, side, stop, target, hold, upper-line fn params, pattern) for every confirmed triangle breakout."""
    out = []
    npv = len(piv.bar)
    need = 5 if five else 4
    for m in range(need, npv + 1):
        jc = int(piv.conf[m - 1])
        idx = list(range(m - need, m))
        bars_ = piv.bar[idx]
        pr = piv.price[idx]
        ty = piv.typ[idx]
        if bars_[-1] - bars_[0] > 60:
            continue
        hs = [(int(bars_[q]), float(pr[q])) for q in range(need) if ty[q] == 1]
        ls = [(int(bars_[q]), float(pr[q])) for q in range(need) if ty[q] == -1]
        if len(hs) < 2 or len(ls) < 2:
            continue
        (h1b, h1), (h2b, h2) = hs[-2], hs[-1]
        (l1b, l1), (l2b, l2) = ls[-2], ls[-1]
        aa = a[jc]
        if not np.isfinite(aa):
            continue
        sym = h2 < h1 - 0.25 * aa and l2 > l1 + 0.25 * aa
        asc = abs(h2 - h1) <= 0.25 * aa and l2 > l1 + 0.25 * aa
        desc = abs(l2 - l1) <= 0.25 * aa and h2 < h1 - 0.25 * aa
        typ = "sym" if sym else "asc" if asc else "desc" if desc else None
        if typ is None or typ not in kinds:
            continue
        su = (h2 - h1) / max(h2b - h1b, 1)
        sl = (l2 - l1) / max(l2b - l1b, 1)
        apex: float
        if su >= sl:
            apex = 10**9
        else:
            apex = h1b + (h1 - l1 - sl * (h1b - l1b)) / (sl - su) if (sl - su) != 0 else 10**9
        height = h1 - l1
        span = int(bars_[-1] - bars_[0])
        end = min(len(b) - 1, jc + 2 * span + 20)
        for j in range(jc, end + 1):
            if j > apex - 3:
                break
            upj = h1 + su * (j - h1b)
            loj = l1 + sl * (j - l1b)
            if upj <= loj:
                break
            if b.c[j] > upj + 0.1 * a[j] and typ in ("sym", "asc"):
                stop = max(min(loj, b.c[j] - 0.6 * a[j]), b.c[j] - 1.0 * a[j]) if loj < b.c[j] else b.c[j] - a[j]
                out.append((j, 1, stop, min(height, 3 * a[j]), 2 * span, upj, loj, "tri"))
                break
            if b.c[j] < loj - 0.1 * a[j] and typ in ("sym", "desc"):
                stop = min(max(upj, b.c[j] + 0.6 * a[j]), b.c[j] + 1.0 * a[j]) if upj > b.c[j] else b.c[j] + a[j]
                out.append((j, -1, stop, min(height, 3 * a[j]), 2 * span, upj, loj, "tri"))
                break
            if j > jc and K_new_pivot(piv, j, m):
                break
    return out


def K_new_pivot(piv: K.Pivots, j: int, m: int) -> bool:
    return piv.known_at(j) > m


def b07_triangle(b: Bars, p: dict, ctx: Ctx) -> Events:
    a = K.atr(b, 14)
    piv = K.zigzag(b, K.zz_k(b.tf), a)
    kinds = ("asc", "desc") if p.get("asc_desc") else ("sym", "asc", "desc")
    s50 = K.sma(b.c, 50)
    eb = EB()
    for j, side, stop, tg, hold, *_ in _triangles(b, piv, a, bool(p.get("five")), kinds):
        if p.get("trend") and not (np.isfinite(s50[j]) and side * (b.c[j] - s50[j]) > 0):
            continue
        eb.add(j, side, stop=stop, tgt_d=tg, hold=max(hold, 5))
    return eb.build()


# ------------------------------------------------------------------ news jump continuation / failure (gold 5m-30m)
def _news(b: Bars, p: dict, ctx: Ctx, fade: bool) -> Events:
    ev = ctx.events.get(p["events"])
    if ev is None or not len(ev):
        return K.empty()
    a = K.atr(b, 20)
    tfm = _tf_min(b)
    scale1h = np.sqrt(60 / tfm)
    scale15 = np.sqrt(15 / tfm)
    eb = EB()
    for T in ev:
        k0 = int(np.searchsorted(b.t, T))                # first bar opening at/after the release
        if k0 >= len(b) or k0 < 13 or b.t[k0] - T > tfm * MIN:
            continue
        pre = np.flatnonzero((b.t >= T - 60 * MIN) & (b.t < T))
        if len(pre) == 0:
            continue
        rh, rl = float(np.max(b.h[pre])), float(np.min(b.l[pre]))
        mid = (rh + rl) / 2
        aj = a[k0 - 1]
        if not np.isfinite(aj):
            continue
        hold = max(1, int(4 * 60 // tfm))
        if not fade:
            delay = int(p.get("delay_min", 5))
            j = int(np.searchsorted(b.ta, T + delay * MIN))          # first bar closing at/after T+delay
            if j >= len(b) or j < k0:
                continue
            for side in (1, -1):
                beyond = (b.c[j] - rh) if side > 0 else (rl - b.c[j])
                if beyond >= 0.25 * aj:
                    a15 = aj * scale15
                    sd = min(abs(b.c[j] - mid), 1.0 * a15)
                    sd = max(sd, 0.6 * a15)
                    eb.add(j, side, stop_d=sd, tgt_d=2.0 * aj * scale1h, hold=hold)
            continue
        # fade: by T+30 the first post-release bar's breakout direction reversed and a close is back inside R
        first_dir = 1 if b.c[k0] > rh else -1 if b.c[k0] < rl else 0
        if first_dir == 0:
            continue
        if p.get("big_spike"):
            spike = (np.max(b.h[k0:k0 + 1]) - rh) if first_dir > 0 else (rl - np.min(b.l[k0:k0 + 1]))
            if spike < 1.5 * aj * scale1h:
                continue
        jend = int(np.searchsorted(b.ta, T + 30 * MIN, side="right"))
        for j in range(k0 + 1, min(jend, len(b))):
            back = (b.c[j] < mid) if first_dir > 0 else (b.c[j] > mid)
            if p.get("any_inside"):
                back = (b.c[j] < rh) if first_dir > 0 else (b.c[j] > rl)
            if back:
                ext = float(np.max(b.h[k0:j + 1]) if first_dir > 0 else np.min(b.l[k0:j + 1]))
                stop = ext + first_dir * 0.2 * aj * scale15
                tgt = (rl - 0.5 * aj * scale1h) if first_dir > 0 else (rh + 0.5 * aj * scale1h)
                risk = abs(stop - b.c[j])
                if abs(tgt - b.c[j]) < 1.5 * risk:
                    tgt = b.c[j] - first_dir * 1.5 * risk
                eb.add(j, -first_dir, stop=stop, tgt=tgt, hold=hold)
                break
    return eb.build()


def b08_news_break(b: Bars, p: dict, ctx: Ctx) -> Events:
    return _news(b, p, ctx, fade=False)


def f10_news_fade(b: Bars, p: dict, ctx: Ctx) -> Events:
    return _news(b, p, ctx, fade=True)


# ------------------------------------------------------------------ B09 breakaway gap
def b09_gap(b: Bars, p: dict, ctx: Ctx) -> Events:
    a = K.atr(b, 14)
    g = float(p["g"])
    s50 = K.sma(b.c, 50)
    hold = 3 if b.tf == "1w" else 5
    eb = EB()
    for j in range(1, len(b)):
        if not np.isfinite(a[j]):
            continue
        if b.o[j] > b.h[j - 1] + g * a[j - 1] and b.c[j] > b.o[j] and b.l[j] > b.h[j - 1]:
            if not p.get("trend") or (np.isfinite(s50[j]) and b.c[j] > s50[j]):
                eb.add(j, 1, stop=b.h[j - 1], tgt_d=2 * a[j], hold=hold)
        if b.o[j] < b.l[j - 1] - g * a[j - 1] and b.c[j] < b.o[j] and b.h[j] < b.l[j - 1]:
            if not p.get("trend") or (np.isfinite(s50[j]) and b.c[j] < s50[j]):
                eb.add(j, -1, stop=b.l[j - 1], tgt_d=2 * a[j], hold=hold)
    return eb.build()


# ------------------------------------------------------------------ B10 floor pivots (gold intraday)
def b10_pivots(b: Bars, p: dict, ctx: Ctx) -> Events:
    roll = 22 * HOUR
    dkey = (b.t - roll) // DAY                        # trading day rolling at 22:00 UTC
    a1h = K.atr(b, 14) * np.sqrt(60 / _tf_min(b))
    hr = _hour(b.t)
    hr_close = _hour(b.ta)
    eb = EB()
    days = np.unique(dkey)
    starts = np.searchsorted(dkey, days)
    ends = np.append(starts[1:], len(b))
    for q in range(1, len(days)):
        if days[q] - days[q - 1] > 4:
            continue
        s0, e0 = starts[q - 1], ends[q - 1]
        H, L, C = float(np.max(b.h[s0:e0])), float(np.min(b.l[s0:e0])), float(b.c[e0 - 1])
        P = (H + L + C) / 3
        R1, S1 = 2 * P - L, 2 * P - H
        s, e = starts[q], ends[q]
        at7 = [j for j in range(s, e) if hr_close[j] == 7]
        if not at7:
            continue
        j7 = at7[0]
        bias = 1 if b.c[j7] > P else -1 if b.c[j7] < P else 0
        if bias == 0:
            continue
        lim = 17 if p.get("ldn_ny") else 22
        need = 2 if p.get("twice") else 1
        cnt = 0
        for j in range(j7 + 1, e):
            if hr[j] >= lim or hr_close[j] > lim or not np.isfinite(a1h[j]):
                break
            lvl = R1 if bias > 0 else S1
            if bias * (b.c[j] - lvl) > 0:
                cnt += 1
                if cnt >= need:
                    sd = min(abs(b.c[j] - P), 1.0 * a1h[j])
                    sd = max(sd, 0.5 * a1h[j])
                    tgt = (P + (H - L)) if bias > 0 else (P - (H - L))
                    if bias * (tgt - b.c[j]) < 1.5 * sd:
                        break
                    end_bars = int((days[q] * DAY + roll + DAY - b.ta[j]) // TF_NS[b.tf])
                    eb.add(j, bias, stop_d=sd, tgt=tgt, hold=max(1, end_bars))
                    break
            else:
                cnt = 0
    return eb.build()


# ------------------------------------------------------------------ F05 80-20, F06 Oops, F07 weekly sweep (daily)
def f05_8020(b: Bars, p: dict, ctx: Ctx) -> Events:
    a = K.atr(b, 14)
    band = float(p["band"])
    s200 = K.sma(b.c, 200)
    eb = EB()
    for j in range(1, len(b)):
        rng = b.h[j] - b.l[j]
        if not np.isfinite(a[j]) or rng < 0.8 * a[j]:
            continue
        top, bot = b.h[j] - band * rng, b.l[j] + band * rng
        if b.o[j] >= top and b.c[j] <= bot:            # opened high, closed low -> long setup for day j+1
            if p.get("trend") and not (b.c[j] > s200[j]):
                continue
            eb.add(j, 1, kind=1, px=b.l[j], valid=1, stop=b.l[j] - 1.1 * a[j], tgt=b.l[j] + a[j], hold=2)
        if b.o[j] <= bot and b.c[j] >= top:
            if p.get("trend") and not (b.c[j] < s200[j]):
                continue
            eb.add(j, -1, kind=1, px=b.h[j], valid=1, stop=b.h[j] + 1.1 * a[j], tgt=b.h[j] - a[j], hold=2)
    return eb.build()


def f07_week_sweep(b: Bars, p: dict, ctx: Ctx) -> Events:
    a = K.atr(b, 14)
    period = "M" if p.get("month") else "W"
    d = (b.ta - 1) // DAY
    if period == "W":
        key = (d + 3) // 7                            # ISO-ish week number (Monday start)
    else:
        import pandas as pd
        dt = pd.to_datetime(d, unit="D")
        key = np.asarray(dt.year * 12 + dt.month)
    wd = (d + 3) % 7
    eb = EB()
    uk, starts = np.unique(key, return_index=True)
    ends = np.append(starts[1:], len(b))
    for q in range(1, len(uk)):
        s0, e0 = starts[q - 1], ends[q - 1]
        PH, PL = float(np.max(b.h[s0:e0])), float(np.min(b.l[s0:e0]))
        for j in range(starts[q], ends[q]):
            if not np.isfinite(a[j]):
                continue
            if p.get("monwed") and wd[j] > 2:
                continue
            rng = b.h[j] - b.l[j]
            if 0.1 * a[j] <= b.h[j] - PH <= 1.0 * a[j] and b.c[j] < PH:
                if p.get("reject") and not (b.c[j] <= b.l[j] + 0.3 * rng):
                    continue
                stop = b.h[j] + 0.2 * a[j]
                risk = stop - b.c[j]
                tgt = max(PL, b.c[j] - 0.5 * (PH - PL)) if PH - PL > 0 else np.nan
                if not np.isfinite(tgt) or b.c[j] - tgt < 1.5 * risk:
                    tgt = b.c[j] - 1.5 * risk
                eb.add(j, -1, stop=stop, tgt=tgt, hold=5)
            if 0.1 * a[j] <= PL - b.l[j] <= 1.0 * a[j] and b.c[j] > PL:
                if p.get("reject") and not (b.c[j] >= b.h[j] - 0.3 * rng):
                    continue
                stop = b.l[j] - 0.2 * a[j]
                risk = b.c[j] - stop
                tgt = min(PH, b.c[j] + 0.5 * (PH - PL))
                if tgt - b.c[j] < 1.5 * risk:
                    tgt = b.c[j] + 1.5 * risk
                eb.add(j, 1, stop=stop, tgt=tgt, hold=5)
    return eb.build()


# ------------------------------------------------------------------ F08 key outside reversal, F09 BB re-entry
def f08_outside(b: Bars, p: dict, ctx: Ctx) -> Events:
    n = int(p["n"])
    a = K.atr(b, 14)
    hh = K.rmax(b.h, n)
    ll = K.rmin(b.l, n)
    eb = EB()
    for j in range(n + 1, len(b)):
        if not np.isfinite(a[j]):
            continue
        if p.get("big") and (b.h[j] - b.l[j]) < 1.5 * a[j]:
            continue
        if b.h[j] >= hh[j] and b.h[j] > hh[j - 1] and b.c[j] < b.l[j - 1]:
            stop = b.h[j] + 0.1 * a[j]
            eb.add(j, -1, stop=stop, tgt_d=1.5 * (stop - b.c[j]), hold=5)
        if b.l[j] <= ll[j] and b.l[j] < ll[j - 1] and b.c[j] > b.h[j - 1]:
            stop = b.l[j] - 0.1 * a[j]
            eb.add(j, 1, stop=stop, tgt_d=1.5 * (b.c[j] - stop), hold=5)
    return eb.build()


def f09_bb_reentry(b: Bars, p: dict, ctx: Ctx) -> Events:
    m = K.sma(b.c, 20)
    sd = K.rstd(b.c, 20)
    k = float(p["k"])
    up, lo = m + k * sd, m - k * sd
    a = K.atr(b, 14)
    gate = None
    if p.get("low_adx"):
        ax, _, _ = K.adx(b, 14)
        gate = ax < 20
    eb = EB()
    for j in range(21, len(b)):
        if not np.isfinite(a[j]) or (gate is not None and not gate[j]):
            continue
        if b.c[j - 1] > up[j - 1] and b.c[j] < up[j] and b.c[j] < b.c[j - 1]:
            stop = max(b.h[j - 1], b.h[j]) + 0.2 * a[j]
            if b.c[j] - m[j] >= 1.2 * (stop - b.c[j]):
                eb.add(j, -1, stop=stop, tgt=m[j], hold=10)
        if b.c[j - 1] < lo[j - 1] and b.c[j] > lo[j] and b.c[j] > b.c[j - 1]:
            stop = min(b.l[j - 1], b.l[j]) - 0.2 * a[j]
            if m[j] - b.c[j] >= 1.2 * (b.c[j] - stop):
                eb.add(j, 1, stop=stop, tgt=m[j], hold=10)
    return eb.build()


# ------------------------------------------------------------------ C01 head and shoulders, C02 double top/bottom
def _hs(b: Bars, piv: K.Pivots, a: np.ndarray, s200: np.ndarray, p: dict) -> list[tuple]:
    out = []
    npv = len(piv.bar)
    for m in range(5, npv + 1):
        idx = list(range(m - 5, m))
        ty = piv.typ[idx]
        pr = piv.price[idx]
        br = piv.bar[idx]
        jc = int(piv.conf[m - 1])
        aa = a[jc]
        if not np.isfinite(aa):
            continue
        span = int(br[-1] - br[0])
        if not 15 <= span <= 80:
            continue
        for top in (True, False):
            if top and list(ty) != [1, -1, 1, -1, 1]:
                continue
            if not top and list(ty) != [-1, 1, -1, 1, -1]:
                continue
            s = 1 if top else -1
            P1, P2, P3, P4, P5 = pr
            head = (P3 - max(P1, P5)) if top else (min(P1, P5) - P3)
            if head < 0.5 * aa:
                continue
            if abs(P1 - P5) > 1.0 * aa or abs(P2 - P4) > 1.0 * aa:
                continue
            if m - 6 >= 0 and s * (P1 - piv.price[m - 6]) <= 0:
                continue                               # P1 preceded by a trend into the pattern
            if p.get("counter_trend") and np.isfinite(s200[jc]) and s * (b.c[jc] - s200[jc]) < 0:
                continue
            slope = (P4 - P2) / max(br[3] - br[1], 1)
            for j in range(jc, min(len(b), jc + 21)):
                if j > jc and piv.known_at(j) > m:
                    break
                if (top and b.h[j] > P3) or (not top and b.l[j] < P3):
                    break
                neck = P2 + slope * (j - br[1])
                if s * (neck - b.c[j]) >= 0.1 * a[j]:
                    stop = P5 + s * 0.2 * a[j]
                    tg = min(abs(P3 - (P2 + slope * (br[2] - br[1]))), 3 * a[j])
                    out.append((j, -s, stop, tg, 2 * span, "hs", P5, P3))
                    break
    return out


def c01_hs(b: Bars, p: dict, ctx: Ctx) -> Events:
    a = K.atr(b, 14)
    s200 = K.sma(b.c, 200)
    piv = _lmw_pivots(b, a) if p.get("lmw") else K.zigzag(b, K.zz_k(b.tf), a)
    eb = EB()
    for j, side, stop, tg, hold, *_ in _hs(b, piv, a, s200, p):
        eb.add(j, side, stop=stop, tgt_d=tg, hold=max(hold, 5))
    return eb.build()


def _doubles(b: Bars, piv: K.Pivots, a: np.ndarray, p: dict) -> list[tuple]:
    out = []
    tol = float(p.get("tol", 0.5))
    for m in range(3, len(piv.bar) + 1):
        idx = [m - 3, m - 2, m - 1]
        ty = list(piv.typ[idx])
        pr = piv.price[idx]
        br = piv.bar[idx]
        jc = int(piv.conf[m - 1])
        aa = a[jc]
        if not np.isfinite(aa):
            continue
        for top in (True, False):
            if ty != ([1, -1, 1] if top else [-1, 1, -1]):
                continue
            s = 1 if top else -1
            H1, L1, H2 = pr
            if abs(H2 - H1) > tol * aa or not 10 <= br[2] - br[0] <= 60:
                continue
            if s * (H1 - L1) < 2 * aa:
                continue
            if p.get("vol_div") and not (np.isfinite(b.vol[br[2]]) and b.vol[br[2]] < b.vol[br[0]]):
                continue
            ext = max(H1, H2) if top else min(H1, H2)
            for j in range(jc, min(len(b), jc + 21)):
                if j > jc and piv.known_at(j) > m:
                    break
                if s * ((b.h[j] if top else b.l[j]) - ext) > 0.25 * a[j]:
                    break
                if s * (L1 - b.c[j]) >= 0.1 * a[j]:
                    stop = ext + s * 0.2 * a[j]
                    tg = min(abs(ext - L1), 3 * a[j])
                    out.append((j, -s, stop, tg, 2 * int(br[2] - br[0]), "dbl", ext, L1))
                    break
    return out


def c02_double(b: Bars, p: dict, ctx: Ctx) -> Events:
    a = K.atr(b, 14)
    piv = K.zigzag(b, K.zz_k(b.tf), a)
    eb = EB()
    for j, side, stop, tg, hold, *_ in _doubles(b, piv, a, p):
        eb.add(j, side, stop=stop, tgt_d=tg, hold=max(hold, 5))
    return eb.build()


# ------------------------------------------------------------------ F04 busted patterns (bases: C01, C02, B07)
def f04_busted(b: Bars, p: dict, ctx: Ctx) -> Events:
    a = K.atr(b, 14)
    piv = K.zigzag(b, K.zz_k(b.tf), a)
    s200 = K.sma(b.c, 200)
    bases = []
    if p.get("hs", True):
        bases += [(j, s, st, "hs", ref_hi) for j, s, st, tg, hold, _k, ref_hi, _r in _hs(b, piv, a, s200, {})]
        bases += [(j, s, st, "dbl", ext) for j, s, st, tg, hold, _k, ext, _l in _doubles(b, piv, a, {"tol": 0.5})]
    if p.get("tri", True):
        bases += [(j, s, st, "tri", (up if s < 0 else lo)) for j, s, st, tg, hold, up, lo, _k in
                  _triangles(b, piv, a, False, ("sym", "asc", "desc"))]
    eb = EB()
    for j0, side, stop0, kind, bound in bases:
        lvl = b.c[j0]
        for j in range(j0 + 1, min(len(b), j0 + 11)):
            moved = side * (b.c[j] - lvl) >= (1.0 * a[j0])
            if moved:
                break
            if -side * (b.c[j] - stop0) > 0:          # closed beyond the opposite boundary: bust
                ext = float(np.max(b.h[j0:j + 1]) if side > 0 else np.min(b.l[j0:j + 1]))
                stop = ext + side * 0.2 * a[j]
                eb.add(j, -side, stop=stop, tgt_d=min(abs(stop0 - lvl) * 2, 3 * a[j]), hold=20)
                break
    return eb.build()


# ------------------------------------------------------------------ C03 flags and pennants
def c03_flag(b: Bars, p: dict, ctx: Ctx) -> Events:
    a = K.atr(b, 14)
    piv = K.zigzag(b, K.zz_k(b.tf), a)
    retr = float(p.get("retr", 0.5))
    tmult = float(p.get("tgt", 0.5))
    eb = EB()
    for m in range(1, len(piv.bar) + 1):
        pb, pp, pt, jc = int(piv.bar[m - 1]), float(piv.price[m - 1]), int(piv.typ[m - 1]), int(piv.conf[m - 1])
        s = 1 if pt == 1 else -1                       # pole ends at a confirmed high (bull) / low (bear)
        if pb < 10 or not np.isfinite(a[pb]):
            continue
        base = float(np.min(b.l[pb - 10:pb + 1]) if s > 0 else np.max(b.h[pb - 10:pb + 1]))
        pole = s * (pp - base)
        if pole < 3 * a[pb]:
            continue
        for j in range(max(jc, pb + 4), min(len(b), pb + 21)):
            seg = slice(pb + 1, j)
            if j - pb - 1 < 4:
                continue
            hi, lo = b.h[seg], b.l[seg]
            if s * (pp - (np.min(lo) if s > 0 else np.max(hi))) > retr * pole:
                break
            x = np.arange(len(hi), dtype=float)
            sh = np.polyfit(x, hi, 1)
            sl_ = np.polyfit(x, lo, 1)
            width = (sh[1] + sh[0] * x[-1]) - (sl_[1] + sl_[0] * x[-1])
            flag = (s * sh[0] <= 0 and s * sl_[0] <= 0) or (sh[0] < 0 < sl_[0])
            if not flag or width > 1.5 * a[j]:
                continue
            line = (sh[1] + sh[0] * (x[-1] + 1)) if s > 0 else (sl_[1] + sl_[0] * (x[-1] + 1))
            if s * (b.c[j] - line) > 0:
                stop = (float(np.min(lo)) - 0.1 * a[j]) if s > 0 else (float(np.max(hi)) + 0.1 * a[j])
                eb.add(j, s, stop=stop, tgt_d=min(tmult * pole, 4 * a[j]), hold=20)
                break
    return eb.build()


# ------------------------------------------------------------------ C04 LMW ensemble
def _lmw_pivots(b: Bars, a: np.ndarray, hmult: float = 0.3) -> K.Pivots:
    """LMW extrema as pivots known 3 bars after the window end that first revealed them (common.kernel_LMW)."""
    seen: dict[tuple[int, int], tuple[float, int]] = {}
    for j in range(35, len(b)):
        for bar, price, typ in K.lmw_extrema(b.c, j, 35, hmult):
            if (bar, typ) not in seen:
                seen[(bar, typ)] = (price, j + 3)
    items = sorted(((conf, bar, price, typ) for (bar, typ), (price, conf) in seen.items()))
    items = [it for it in items if it[0] < len(b)]
    out_b: list[int] = []
    out_p: list[float] = []
    out_t: list[int] = []
    out_c: list[int] = []
    for conf, bar, price, typ in items:
        if out_t and out_t[-1] == typ:
            continue                       # keep the first-known extremum: a later replacement would rewrite history
        if out_b and bar <= out_b[-1]:
            continue
        out_b.append(bar)
        out_p.append(price)
        out_t.append(typ)
        out_c.append(conf)
    return K.Pivots(np.array(out_b, np.int64), np.array(out_p), np.array(out_t, np.int8), np.array(out_c, np.int64))


def c04_lmw(b: Bars, p: dict, ctx: Ctx) -> Events:
    a = K.atr(b, 14)
    piv = _lmw_pivots(b, a, float(p.get("h", 0.3)))
    reversal_only = bool(p.get("reversal_only"))
    eb = EB()
    for m in range(5, len(piv.bar) + 1):
        E = piv.price[m - 5:m]
        T = piv.typ[m - 5:m]
        j = int(piv.conf[m - 1])
        if j >= len(b) or not np.isfinite(a[j]):
            continue
        mean15 = (E[0] + E[4]) / 2
        mean24 = (E[1] + E[3]) / 2
        side = 0
        if T[0] == 1:   # E1 max
            if E[2] > E[0] and E[2] > E[4] and abs(E[0] - mean15) <= 0.015 * mean15 and \
                    abs(E[1] - mean24) <= 0.015 * mean24:
                side = -1                                  # HS
            elif E[0] < E[2] < E[4] and E[1] > E[3]:
                side = -1                                  # BTOP
            elif not reversal_only and E[0] > E[2] > E[4] and E[1] < E[3]:
                side = 1 if b.c[j] > E[4] else -1          # TTOP: breakout side of the last extremum
            elif abs(E[0] - E[2]) <= 0.015 * E[0] and abs(E[2] - E[4]) <= 0.015 * E[0]:
                side = 0                                   # RTOP: rectangle, excluded
        else:           # E1 min
            if E[2] < E[0] and E[2] < E[4] and abs(E[0] - mean15) <= 0.015 * mean15 and \
                    abs(E[1] - mean24) <= 0.015 * mean24:
                side = 1                                   # IHS
            elif E[0] > E[2] > E[4] and E[1] < E[3]:
                side = 1                                   # BBOT
            elif not reversal_only and E[0] < E[2] < E[4] and E[1] > E[3]:
                side = 1 if b.c[j] > E[4] else -1          # TBOT
        if side == 0 and m >= 4:
            E3 = piv.price[m - 3:m]
            T3 = piv.typ[m - 3:m]
            if T3[0] == 1 and abs(E3[0] - E3[2]) <= 0.015 * E3[0]:
                side = -1                                  # DTOP (two tops)
            elif T3[0] == -1 and abs(E3[0] - E3[2]) <= 0.015 * E3[0]:
                side = 1                                   # DBOT
        if side:
            eb.add(j, side, stop_d=1.0 * a[j], tgt_d=2.0 * a[j], hold=10)
    return eb.build()


# ------------------------------------------------------------------ C05 candlestick 3-day reversals
def c05_candles(b: Bars, p: dict, ctx: Ctx) -> Events:
    a = K.atr(b, 14)
    s5 = K.sma(b.c, 5)
    piv = K.zigzag(b, K.zz_k(b.tf), a) if p.get("at_level") else None
    stars_only = bool(p.get("stars_only"))
    eb = EB()
    o, h, lo, c = b.o, b.h, b.l, b.c
    for j in range(8, len(b)):
        if not np.isfinite(a[j]):
            continue
        aa = a[j]
        down = all(c[j - 3 - k] < s5[j - 3 - k] for k in range(3))
        up = all(c[j - 3 - k] > s5[j - 3 - k] for k in range(3))
        b1, b2, b3 = j - 2, j - 1, j
        bull = bear = False
        if down:
            mstar = (o[b1] - c[b1] >= 0.6 * aa and abs(c[b2] - o[b2]) <= 0.25 * aa and max(o[b2], c[b2]) <= c[b1]
                     and c[b3] > o[b3] and c[b3] > (o[b1] + c[b1]) / 2)
            three_in = (o[b1] > c[b1] and c[b2] > o[b2] and max(o[b2], c[b2]) < o[b1] and min(o[b2], c[b2]) > c[b1]
                        and c[b3] > o[b1])
            three_out = (o[b1] > c[b1] and c[b2] > o[b2] and o[b2] <= c[b1] and c[b2] >= o[b1] and c[b3] > c[b2])
            bull = mstar or (not stars_only and (three_in or three_out))
        if up:
            estar = (c[b1] - o[b1] >= 0.6 * aa and abs(c[b2] - o[b2]) <= 0.25 * aa and min(o[b2], c[b2]) >= c[b1]
                     and c[b3] < o[b3] and c[b3] < (o[b1] + c[b1]) / 2)
            three_in = (c[b1] > o[b1] and o[b2] > c[b2] and max(o[b2], c[b2]) < c[b1] and min(o[b2], c[b2]) > o[b1]
                        and c[b3] < o[b1])
            three_out = (c[b1] > o[b1] and o[b2] > c[b2] and o[b2] >= c[b1] and c[b2] <= o[b1] and c[b3] < c[b2])
            bear = estar or (not stars_only and (three_in or three_out))
        for side, ok in ((1, bull), (-1, bear)):
            if not ok:
                continue
            if piv is not None:
                lvl = K.sr_level(piv, j, -side, 2, aa)
                ext = float(np.min(lo[b1:b3 + 1]) if side > 0 else np.max(h[b1:b3 + 1]))
                if not np.isfinite(lvl) or abs(ext - lvl) > 0.5 * aa:
                    continue
            ext = float(np.min(lo[b1:b3 + 1]) if side > 0 else np.max(h[b1:b3 + 1]))
            stop = ext - side * 0.1 * aa
            risk = abs(c[j] - stop)
            if p.get("no_target"):
                eb.add(j, side, stop=stop, hold=2)
            else:
                eb.add(j, side, stop=stop, tgt_d=1.5 * risk, hold=2)
    return eb.build()


# ------------------------------------------------------------------ C06 wedges, C07 broadening
def c06_wedge(b: Bars, p: dict, ctx: Ctx) -> Events:
    a = K.atr(b, 14)
    piv = K.zigzag(b, K.zz_k(b.tf), a)
    need = int(p.get("pivots", 5))
    eb = EB()
    for m in range(need, len(piv.bar) + 1):
        idx = list(range(m - need, m))
        br, pr, ty = piv.bar[idx], piv.price[idx], piv.typ[idx]
        jc = int(piv.conf[m - 1])
        aa = a[jc]
        if not np.isfinite(aa):
            continue
        hs = [(br[q], pr[q]) for q in range(need) if ty[q] == 1]
        ls = [(br[q], pr[q]) for q in range(need) if ty[q] == -1]
        if len(hs) < 2 or len(ls) < 2:
            continue
        su = (hs[-1][1] - hs[0][1]) / max(hs[-1][0] - hs[0][0], 1) / aa
        sl = (ls[-1][1] - ls[0][1]) / max(ls[-1][0] - ls[0][0], 1) / aa
        w0 = (hs[0][1] - ls[0][1])
        w1 = (hs[-1][1] - ls[-1][1])
        if w0 <= 0 or w1 > 0.6 * w0:
            continue
        rising = sl > su > 0
        falling = su < sl < 0
        if not (rising or falling):
            continue
        if p.get("prior_trend"):
            start = int(br[0])
            pre = b.c[max(0, start - 20):start + 1]
            if len(pre) < 2 or (rising and pre[-1] - pre.min() < 3 * aa) or (falling and pre.max() - pre[-1] < 3 * aa):
                continue
        span = int(br[-1] - br[0])
        for j in range(jc, min(len(b), jc + 2 * span + 1)):
            if j > jc and piv.known_at(j) > m:
                break
            if rising:
                lo_line = ls[0][1] + sl * aa * (j - ls[0][0])
                if b.c[j] < lo_line - 0.1 * a[j]:
                    stop = hs[-1][1] + 0.2 * a[j]
                    tgt = max(ls[0][1], b.c[j] - 3 * a[j])
                    if tgt < b.c[j]:
                        eb.add(j, -1, stop=stop, tgt=tgt, hold=2 * span)
                    break
            else:
                hi_line = hs[0][1] + su * aa * (j - hs[0][0])
                if b.c[j] > hi_line + 0.1 * a[j]:
                    stop = ls[-1][1] - 0.2 * a[j]
                    tgt = min(hs[0][1], b.c[j] + 3 * a[j])
                    if tgt > b.c[j]:
                        eb.add(j, 1, stop=stop, tgt=tgt, hold=2 * span)
                    break
    return eb.build()


def c07_broadening(b: Bars, p: dict, ctx: Ctx) -> Events:
    a = K.atr(b, 14)
    piv = K.zigzag(b, K.zz_k(b.tf), a)
    need = 4 if p.get("four") else 5
    eb = EB()
    for m in range(need, len(piv.bar) + 1):
        idx = list(range(m - need, m))
        pr, ty = piv.price[idx], list(piv.typ[idx])
        jc = int(piv.conf[m - 1])
        if not np.isfinite(a[jc]):
            continue
        for top in (True, False):
            pat = [1, -1, 1, -1, 1] if top else [-1, 1, -1, 1, -1]
            if ty != pat[-need:]:
                continue
            s = 1 if top else -1
            if need == 5:
                H1, L1, H2, L2, H3 = pr
                ok = s * (H2 - H1) > 0 and s * (H3 - H2) > 0 and s * (L1 - L2) > 0
            else:
                L1, H2, L2, H3 = pr
                ok = s * (H3 - H2) > 0 and s * (L1 - L2) > 0
            if not ok:
                continue
            trig = H2 if p.get("h3_reject") else L2
            for j in range(jc, min(len(b), jc + 16)):
                if j > jc and piv.known_at(j) > m:
                    break
                if s * (trig - b.c[j]) >= 0.1 * a[j]:
                    stop = H3 + s * 0.2 * a[j]
                    tgt_d = min(abs(H3 - L2) * 0.5 + abs(b.c[j] - L2), 3 * a[j])
                    eb.add(j, -s, stop=stop, tgt_d=tgt_d, hold=20)
                    break
    return eb.build()


# ------------------------------------------------------------------ C08 cup with handle
def c08_cup(b: Bars, p: dict, ctx: Ctx) -> Events:
    a = K.atr(b, 14)
    piv = K.zigzag(b, K.zz_k(b.tf), a)
    s200 = K.sma(b.c, 200)
    weekly = b.tf == "1w"
    eb = EB()
    for m in range(2, len(piv.bar) + 1):
        if piv.typ[m - 2] != 1 or piv.typ[m - 1] != -1:
            continue
        A_b, A, B_b, B = int(piv.bar[m - 2]), float(piv.price[m - 2]), int(piv.bar[m - 1]), float(piv.price[m - 1])
        jc = int(piv.conf[m - 1])
        aa = a[jc]
        if not np.isfinite(aa):
            continue
        depth = A - B
        if weekly:
            if not 0.12 <= depth / A <= 0.35:
                continue
        elif not 3 * aa <= depth <= 8 * aa:
            continue
        C_b = None
        for j in range(jc, min(len(b), A_b + 151)):
            if b.h[j] > A + 0.5 * aa:
                break
            if b.h[j] >= A - 0.5 * aa:
                C_b = j
                break
        if C_b is None:
            continue
        L = C_b - A_b
        if not (30 <= L <= 150 or weekly) or not (A_b + L / 3 <= B_b <= A_b + 2 * L / 3):
            continue
        lower = b.c[A_b:C_b + 1] <= B + 0.25 * depth
        if lower.mean() < 0.30:
            continue
        need_handle = not p.get("no_handle")
        h_low = np.inf
        for j in range(C_b + 1, min(len(b), C_b + 26)):
            h_low = min(h_low, b.l[j])
            if A - h_low > depth / 3:
                break
            if b.c[j] > b.h[C_b] + 0.1 * a[j]:
                if need_handle and j - C_b < 5:
                    break
                if p.get("trend") and not (np.isfinite(s200[j]) and b.c[j] > s200[j]):
                    break
                stop = (h_low if need_handle else B) - 0.1 * a[j]
                tgt = min(A + 0.5 * depth, b.c[j] + 4 * a[j])
                if tgt > b.c[j]:
                    eb.add(j, 1, stop=stop, tgt=tgt, hold=40)
                break
    return eb.build()


# ------------------------------------------------------------------ C09 harmonic (Gartley / Bat / ABCD)
def c09_harmonic(b: Bars, p: dict, ctx: Ctx) -> Events:
    a = K.atr(b, 14)
    piv = K.zigzag(b, K.zz_k(b.tf), a)
    kind = p["kind"]
    eb = EB()
    for m in range(4, len(piv.bar) + 1):
        X, A, B, C = (float(x) for x in piv.price[m - 4:m])
        ty = piv.typ[m - 4:m]
        jc = int(piv.conf[m - 1])
        if not np.isfinite(a[jc]) or jc + 1 >= len(b):
            continue
        bull = ty[0] == -1             # X low, A high, B low, C high -> D below (long at D)
        XA, AB, BC = abs(A - X), abs(B - A), abs(C - B)
        if XA <= 0 or AB <= 0:
            continue
        if kind == "abcd":
            if not 0.618 <= BC / AB <= 0.786:
                continue
            D = C - AB if bull else C + AB
            stop = D - a[jc] if bull else D + a[jc]
        else:
            lo_b, hi_b, d_r = (0.568, 0.668, 0.786) if kind == "gartley" else (0.382, 0.5, 0.886)
            if not lo_b <= AB / XA <= hi_b or not 0.382 <= BC / AB <= 0.886:
                continue
            D = A - d_r * XA if bull else A + d_r * XA
            CD = abs(C - D)
            if not 1.27 <= CD / BC <= 1.618:
                continue
            stop = X - 0.5 * a[jc] if bull else X + 0.5 * a[jc]
        side = 1 if bull else -1
        if side * (b.c[jc] - D) <= 0 or side * (D - stop) <= 0:
            continue
        if p.get("at_level"):
            lvl = K.sr_level(piv, jc, -side, 2, a[jc])
            if not np.isfinite(lvl) or abs(lvl - D) > 0.5 * a[jc]:
                continue
        tgt = D + side * 0.618 * abs(A - D)
        eb.add(jc, side, kind=2, px=D, valid=20, stop=stop, tgt=tgt, hold=20)
    return eb.build()


# ------------------------------------------------------------------ C10 pin bar at a tested level
def c10_pin(b: Bars, p: dict, ctx: Ctx) -> Events:
    a = K.atr(b, 14)
    piv = K.zigzag(b, K.zz_k(b.tf), a) if not p.get("no_level") else None
    ll20 = K.rmin(b.l, 20)
    hh20 = K.rmax(b.h, 20)
    rmin = float(p.get("range", 0.8))
    eb = EB()
    for j in range(21, len(b)):
        aa = a[j]
        rng = b.h[j] - b.l[j]
        if not np.isfinite(aa) or rng < rmin * aa:
            continue
        body_lo, body_hi = min(b.o[j], b.c[j]), max(b.o[j], b.c[j])
        bull = (body_lo - b.l[j]) >= 2 / 3 * rng and body_lo >= b.l[j] + 2 / 3 * rng
        bear = (b.h[j] - body_hi) >= 2 / 3 * rng and body_hi <= b.h[j] - 2 / 3 * rng
        for side, ok in ((1, bull), (-1, bear)):
            if not ok:
                continue
            if piv is None:
                if side > 0 and not b.l[j] <= ll20[j - 1]:
                    continue
                if side < 0 and not b.h[j] >= hh20[j - 1]:
                    continue
            else:
                lvl = K.sr_level(piv, j - 1, -side, 2, a[j - 1])
                if not np.isfinite(lvl):
                    continue
                pierce = (lvl - b.l[j]) if side > 0 else (b.h[j] - lvl)
                if not (0 <= pierce <= 0.5 * aa) or side * (b.c[j] - lvl) <= 0:
                    continue
            stop = (b.l[j] - 0.1 * aa) if side > 0 else (b.h[j] + 0.1 * aa)
            eb.add(j, side, stop=stop, tgt_d=2 * abs(b.c[j] - stop), hold=10)
    return eb.build()


PATTERN_DETECTORS = {
    "B04": b04_round_break, "B05": b05_break_retest, "B07": b07_triangle, "B08": b08_news_break, "B09": b09_gap,
    "B10": b10_pivots, "F02": f02_false_break, "F03": f03_round_reject, "F04": f04_busted, "F05": f05_8020,
    "F07": f07_week_sweep, "F08": f08_outside, "F09": f09_bb_reentry, "F10": f10_news_fade,
    "C01": c01_hs, "C02": c02_double, "C03": c03_flag, "C04": c04_lmw, "C05": c05_candles, "C06": c06_wedge,
    "C07": c07_broadening, "C08": c08_cup, "C09": c09_harmonic, "C10": c10_pin,
}
