"""Frozen detector interface (WS-3, frozen 2026-11-07 11:00 UTC) and the point-in-time indicators detectors share.

A detector is `fn(b: Bars, p: dict, ctx: Ctx) -> Events` (one cell) or, for cross-sectional families,
`fn(bs: dict[str, Bars], p: dict, ctx: Ctx) -> dict[str, Events]`. Event k is DECIDED at the close of bar `i[k]`
(`Bars.ta[i]`) from bars 0..i[k] only; the simulator fills it no earlier than bar i[k]+1. Every indicator here is
causal (value at bar j uses bars <= j); the truncation-prefix test in tests/test_catalogue_lookahead.py checks every
detector against that contract.

Event fields
  i       signal bar             side    +1 long / -1 short
  kind    0 market at the next bar's open, 1 stop order at px, 2 limit order at px
  valid   bars the order lives (from i+1)
  stop    absolute stop price, or nan and stop_d = distance from the fill
  tgt     absolute target, or nan and tgt_d = distance from the fill (nan both: no target)
  hold    time exit at the close of the hold-th bar counted from the fill bar
  kind 3  market AT the signal bar's close (fill = c[i] at ta[i]; only for Dukascopy-derived bars, whose close is a
          tradable CFD price at that instant); the walk starts at bar i+1 and `hold` counts from i+1
  oco     1 on the first leg of a one-cancels-other pair (the next event, same bar, opposite side)
  exit_long / exit_short  optional per-bar masks: exit at the close of the first bar >= fill where the mask is True"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from goldbot.research.catalogue.data import Bars

NAN = float("nan")


@dataclass
class Ctx:
    window: str
    events: dict[str, np.ndarray] = field(default_factory=dict)     # name -> release instants (ns)
    aux: dict[str, Any] = field(default_factory=dict)              # fred series etc. (available_ns, values)


@dataclass
class Events:
    i: np.ndarray
    side: np.ndarray
    kind: np.ndarray
    px: np.ndarray
    valid: np.ndarray
    stop: np.ndarray
    stop_d: np.ndarray
    tgt: np.ndarray
    tgt_d: np.ndarray
    hold: np.ndarray
    exit_long: np.ndarray | None = None
    exit_short: np.ndarray | None = None
    oco: np.ndarray | None = None            # 1 where the event is one leg of an OCO pair with the next event (same i)

    def __len__(self) -> int:
        return len(self.i)

    def table(self) -> np.ndarray:
        """Rows (i, side, kind, px, valid, stop, stop_d, tgt, tgt_d, hold) for comparisons (nan-safe via nan_to_num)."""
        return np.column_stack([self.i, self.side, self.kind, self.px, self.valid, self.stop, self.stop_d, self.tgt,
                                self.tgt_d, self.hold]).astype(float) if len(self) else np.zeros((0, 10))

    def subset(self, keep: np.ndarray) -> "Events":
        return Events(self.i[keep], self.side[keep], self.kind[keep], self.px[keep], self.valid[keep], self.stop[keep],
                      self.stop_d[keep], self.tgt[keep], self.tgt_d[keep], self.hold[keep], self.exit_long,
                      self.exit_short, None if self.oco is None else self.oco[keep])


class EB:
    """Event builder: detectors append rows, then .build(n_bars)."""

    def __init__(self) -> None:
        self.rows: list[tuple[float, ...]] = []
        self.exit_long: np.ndarray | None = None
        self.exit_short: np.ndarray | None = None

    def add(self, i: int, side: int, *, kind: int = 0, px: float = NAN, valid: int = 1, stop: float = NAN,
            stop_d: float = NAN, tgt: float = NAN, tgt_d: float = NAN, hold: int = 10**9, oco: int = 0) -> None:
        if not (np.isfinite(stop) or np.isfinite(stop_d)):
            return
        if np.isfinite(stop_d) and stop_d <= 0:
            return
        if kind in (1, 2) and not np.isfinite(px):
            return
        self.rows.append((i, side, kind, px, valid, stop, stop_d, tgt, tgt_d, hold, oco))

    def oco_pair(self, i: int, *, long: dict, short: dict) -> None:
        """Two stop orders, first fill cancels the other; if both trigger on one bar the trade is a full loss."""
        n0 = len(self.rows)
        self.add(i, 1, oco=1, **long)
        if len(self.rows) == n0:
            self.add(i, -1, **short)
            return
        n1 = len(self.rows)
        self.add(i, -1, oco=1, **short)
        if len(self.rows) == n1:
            r = self.rows[-1]
            self.rows[-1] = r[:-1] + (0,)

    def masks(self, exit_long: np.ndarray | None, exit_short: np.ndarray | None) -> "EB":
        self.exit_long, self.exit_short = exit_long, exit_short
        return self

    def build(self) -> Events:
        a = np.array(self.rows, float).reshape(-1, 11)
        if len(a):
            a = a[np.lexsort((-a[:, 1], a[:, 0]))]     # same bar: long first, so an OCO pair is (long, short)
        return Events(i=a[:, 0].astype(np.int64), side=a[:, 1].astype(np.int8), kind=a[:, 2].astype(np.int8),
                      px=a[:, 3], valid=a[:, 4].astype(np.int64), stop=a[:, 5], stop_d=a[:, 6], tgt=a[:, 7],
                      tgt_d=a[:, 8], hold=a[:, 9].astype(np.int64), exit_long=self.exit_long,
                      exit_short=self.exit_short, oco=a[:, 10].astype(np.int8))


def empty() -> Events:
    return EB().build()


Detector = Callable[[Bars, dict, Ctx], Events]


# ------------------------------------------------------------------ causal indicators
def sma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    from numpy.lib.stride_tricks import sliding_window_view
    out[n - 1:] = sliding_window_view(np.asarray(x, float), n).mean(axis=1)    # nan inside a window -> nan
    return out


def ema(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    a = 2.0 / (n + 1)
    v = float(np.mean(x[:n]))
    out[n - 1] = v
    for j in range(n, len(x)):
        v = a * x[j] + (1 - a) * v
        out[j] = v
    return out


def wilder(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    v = float(np.nanmean(x[:n]))
    out[n - 1] = v
    for j in range(n, len(x)):
        v = v + (x[j] - v) / n
        out[j] = v
    return out


def true_range(b: Bars) -> np.ndarray:
    pc = np.concatenate([[b.c[0]], b.c[:-1]])
    return np.maximum(b.h, pc) - np.minimum(b.l, pc)


def atr(b: Bars, n: int) -> np.ndarray:
    return wilder(true_range(b), n)


def rstd(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    from numpy.lib.stride_tricks import sliding_window_view
    out[n - 1:] = sliding_window_view(np.asarray(x, float), n).std(axis=1, ddof=1)
    return out


def rmax(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    from numpy.lib.stride_tricks import sliding_window_view
    out[n - 1:] = sliding_window_view(np.asarray(x, float), n).max(axis=1)
    return out


def rmin(x: np.ndarray, n: int) -> np.ndarray:
    return -rmax(-np.asarray(x, float), n)


def shift(x: np.ndarray, k: int = 1) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if k < len(x):
        out[k:] = x[:-k] if k else x
    return out


def rsi(x: np.ndarray, n: int) -> np.ndarray:
    d = np.diff(np.asarray(x, float), prepend=np.nan)
    up, dn = np.where(d > 0, d, 0.0), np.where(d < 0, -d, 0.0)
    up[0] = dn[0] = np.nan
    au, ad = wilder(up[1:], n), wilder(dn[1:], n)
    out = np.full(len(x), np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        out[1:] = np.where(ad == 0, 100.0, 100 - 100 / (1 + au / ad))
    return out


def adx(b: Bars, n: int = 14) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    up = np.diff(b.h, prepend=np.nan)
    dn = -np.diff(b.l, prepend=np.nan)
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = true_range(b)
    s_tr, s_p, s_n = wilder(tr[1:], n), wilder(pdm[1:], n), wilder(ndm[1:], n)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100 * s_p / s_tr
        ndi = 100 * s_n / s_tr
        dx = 100 * np.abs(pdi - ndi) / (pdi + ndi)
    a = np.full(len(b), np.nan)
    first = np.flatnonzero(np.isfinite(dx))
    if len(first):
        f0 = first[0]
        a[1 + f0:] = wilder(np.nan_to_num(dx[f0:]), n)
    P = np.concatenate([[np.nan], pdi])
    N = np.concatenate([[np.nan], ndi])
    return a, P, N


def bars_since(mask: np.ndarray) -> np.ndarray:
    """Bars since the mask was last True at or before each bar (large when never)."""
    idx = np.where(mask, np.arange(len(mask)), -10**9)
    last = np.maximum.accumulate(idx) if len(idx) else idx
    return np.arange(len(mask)) - last


# ------------------------------------------------------------------ zigzag pivots (common.pivot_zigzag)
@dataclass
class Pivots:
    """Confirmed ZZ(k) pivots in confirmation order: extreme bar, price, type (+1 high, -1 low), confirmation bar."""
    bar: np.ndarray
    price: np.ndarray
    typ: np.ndarray
    conf: np.ndarray

    def known_at(self, j: int) -> int:
        """How many pivots are confirmed at or before bar j."""
        return int(np.searchsorted(self.conf, j, side="right"))


def zigzag(b: Bars, k: float, a: np.ndarray | None = None) -> Pivots:
    """ATR14-reversal zigzag on closed bars: a swing high H is confirmed at the first later bar whose low <= H - k*ATR14
    (ATR at that bar); pivots are known only from their confirmation bar."""
    a = atr(b, 14) if a is None else a
    n = len(b)
    bars: list[int] = []
    prices: list[float] = []
    typs: list[int] = []
    confs: list[int] = []
    h, lo = b.h, b.l
    j0 = int(np.argmax(np.isfinite(a))) if np.isfinite(a).any() else n
    if j0 >= n:
        return Pivots(np.zeros(0, np.int64), np.zeros(0), np.zeros(0, np.int8), np.zeros(0, np.int64))
    hi_bar, lo_bar = j0, j0
    trend = 0
    for j in range(j0 + 1, n):
        aj = a[j]
        if not np.isfinite(aj):
            continue
        if trend >= 0:
            if h[j] >= h[hi_bar]:
                hi_bar = j
            if trend == 0 and lo[j] <= lo[lo_bar]:
                lo_bar = j
            if lo[j] <= h[hi_bar] - k * aj and hi_bar < j:
                bars.append(hi_bar)
                prices.append(h[hi_bar])
                typs.append(1)
                confs.append(j)
                trend = -1
                lo_bar = j
                continue
        if trend <= 0:
            if lo[j] <= lo[lo_bar]:
                lo_bar = j
            if trend == 0 and h[j] >= h[hi_bar]:
                hi_bar = j
            if h[j] >= lo[lo_bar] + k * aj and lo_bar < j:
                bars.append(lo_bar)
                prices.append(lo[lo_bar])
                typs.append(-1)
                confs.append(j)
                trend = 1
                hi_bar = j
    return Pivots(np.array(bars, np.int64), np.array(prices), np.array(typs, np.int8), np.array(confs, np.int64))


def zz_k(tf: str) -> float:
    return 2.0 if tf in ("1d", "1w") else 2.5


def sr_level(piv: Pivots, j: int, typ: int, n_min: int, a_j: float, lookback: int = 120, min_age: int = 5) -> float:
    """SR(n) at bar j (common.level_cluster): mean of >= n_min confirmed pivots of type `typ` within 0.25*ATR of each
    other inside the last `lookback` bars, newest touch >= min_age bars old. Returns the level nearest the last close
    cluster (nan if none). Uses only pivots confirmed at or before j."""
    m = piv.known_at(j)
    if m < n_min or not np.isfinite(a_j):
        return NAN
    sel = (piv.typ[:m] == typ) & (piv.bar[:m] >= j - lookback) & (piv.bar[:m] <= j - min_age)
    p = piv.price[:m][sel]
    if len(p) < n_min:
        return NAN
    p = np.sort(p)
    best = NAN
    best_n = 0
    lo = 0
    for hi in range(len(p)):
        while p[hi] - p[lo] > 0.25 * a_j:
            lo += 1
        cnt = hi - lo + 1
        if cnt >= n_min and cnt >= best_n:
            best_n, best = cnt, float(np.mean(p[lo:hi + 1]))
    return best


def sr_levels_all(piv: Pivots, j: int, typ: int, n_min: int, a_j: float, lookback: int = 120,
                  min_age: int = 5) -> list[float]:
    """Every SR(n) cluster level at bar j (greedy, non-overlapping)."""
    m = piv.known_at(j)
    if m < n_min or not np.isfinite(a_j):
        return []
    sel = (piv.typ[:m] == typ) & (piv.bar[:m] >= j - lookback) & (piv.bar[:m] <= j - min_age)
    p = np.sort(piv.price[:m][sel])
    out: list[float] = []
    k = 0
    while k < len(p):
        e = k
        while e + 1 < len(p) and p[e + 1] - p[k] <= 0.25 * a_j:
            e += 1
        if e - k + 1 >= n_min:
            out.append(float(np.mean(p[k:e + 1])))
        k = e + 1
    return out


# ------------------------------------------------------------------ LMW kernel extrema (common.kernel_LMW)
def lmw_extrema(c: np.ndarray, j: int, l: int = 35, hmult: float = 0.3) -> list[tuple[int, float, int]]:  # noqa: E741
    """Kernel-smoothed local extrema of closes in the window [j-l+1, j] (only bars <= j): list of
    (bar, raw close extreme within +-1 bar, +1 max / -1 min). Bandwidth = hmult x a CV-like rule of thumb
    (Silverman on the time index), deterministic and causal."""
    if j + 1 < l:
        return []
    y = c[j - l + 1:j + 1]
    x = np.arange(l, dtype=float)
    h = hmult * 1.06 * np.std(x) * l ** (-0.2) * 3.0
    h = max(h, 0.8)
    w = np.exp(-0.5 * ((x[:, None] - x[None, :]) / h) ** 2)
    m = (w @ y) / w.sum(axis=1)
    d = np.sign(np.diff(m))
    out: list[tuple[int, float, int]] = []
    for q in range(1, len(d)):
        if d[q] == 0 or d[q - 1] == 0 or d[q] == d[q - 1]:
            continue
        typ = 1 if d[q - 1] > 0 else -1
        lo_, hi_ = max(0, q - 1), min(l - 1, q + 1)
        seg = y[lo_:hi_ + 1]
        r = int(np.argmax(seg) if typ == 1 else np.argmin(seg)) + lo_
        out.append((j - l + 1 + r, float(y[r]), typ))
    return out
