"""Key levels: point-in-time multi-timeframe support / resistance ZONES (owner 2026-11-02: "identify key levels,
support and resistance on all time frames"; sprint-3000 brief, ADDENDUM A, frozen definition).

Design rule: "a zone exists only from the close of the bar that confirms it". Appending bars never changes a past
zone or a past row (tests/test_key_levels.py proves it on prefixes); other bars join the per-bar frame asof on its
`available_utc`.

* Levels (candidates):
  - confirmed swing highs / lows on 15m, 1h, 4h, 1d and 1w (`structure.swing_points`, lag SWING_LAG): a swing at bar
    j is known at the close (`visible_at`) of bar j + SWING_LAG. Timeframes finer than the input bars are skipped;
    1d is the feature-day and 1w the Sunday week of `resample_bars`.
  - the previous day / week / month high and low (`smc_levels.period_levels`, 17:00 New York roll): the six current
    values, read from the finest bar that closes at the evaluation (the previous period is complete by then).
  - $50 / $100 round numbers within ROUND_RANGE_ATR x ATR(14, 1d) of the close.
* Weight: timeframe weight 15m 1 / 1h 2 / 4h 3 / 1d 4 / 1w 5 (a month level counts as 1w: 5; a $50 number 1, a $100
  number 2), age-decayed for swings: halved every HALF_LIFE_DAYS of its timeframe and dropped below 1/8 (three
  half-lives). Period and round levels do not age (they are the current ones).
* Zones: the levels sorted by price, grouped greedily from the lowest so no zone is wider than CLUSTER_ATR x ATR(14,
  1d) (single linkage would chain a busy range into one wide band). `strength` = the sum of its members' weights
  (timeframe weight x touches, age-decayed), `price` the weight-averaged price, `tfs` the timeframes it spans,
  `touches` its non-round members.
* `strength_pct`: the zone's percentile among the zones of the trailing PCT_WINDOW_DAYS calendar days (the book at the
  first evaluation of each UTC day is the day's sample; today's sample is not in its own pool). None until
  PCT_MIN_DAYS days are sampled.
* Role: support while the close is above the zone, resistance below, inside within it. A zone is born a resistance
  (a high) or a support (a low) by its weight-majority member; `flipped` = its role is now the opposite (a close went
  through it).
* Clock: the book is evaluated at every close of the finest bar of 15m and the input's own timeframe (every level
  appears on such a close). A bar of the input sees the book of the last evaluation at or before its own close.

Read-only research and display context: nothing here sizes, gates or places an order.
"""
from __future__ import annotations

from collections import deque
from typing import Literal, NamedTuple

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.config import tf_seconds
from goldbot.data.resample import BAR_COLUMNS, mid, resample_bars
from goldbot.data.timeutil import epoch_ns, utc_index
from goldbot.features.registry import FEATURES, NEEDS_STATE, FeatureCtx, feature
from goldbot.features.smc_levels import period_levels
from goldbot.features.structure import swing_points
from goldbot.features.technical import atr

__all__ = ["FEATURE_COLUMNS", "KeyZone", "KeyZones", "cluster_levels", "decayed_weight", "LevelFilter", "enable", "key_level_frame", "key_level_frames",
           "role_of", "round_levels", "tf_bits", "tf_names", "zones_asof"]

SWING_TFS = ("15m", "1h", "4h", "1d", "1w")
LEVEL_TFS = (*SWING_TFS, "1M")                 # 1M: the previous month's high / low
ROUND = "round"
TF_WEIGHT = {"15m": 1, "1h": 2, "4h": 3, "1d": 4, "1w": 5, "1M": 5}
TF_RANK = {ROUND: 0, **{tf: i + 1 for i, tf in enumerate(LEVEL_TFS)}}
TF_BIT = {**{tf: 1 << i for i, tf in enumerate(LEVEL_TFS)}, ROUND: 1 << len(LEVEL_TFS)}
HALF_LIFE_DAYS = {"15m": 3.0, "1h": 7.0, "4h": 20.0, "1d": 60.0, "1w": 180.0}
PRUNE_HALF_LIVES = 3.0                         # a swing older than three half-lives (weight < 1/8) is dropped
SWING_LAG = 5
CLUSTER_ATR = 0.25                             # zone width cap, x ATR(14, 1d)
ATR_N = 14
ROUND_STEP = 50.0
ROUND_RANGE_ATR = 3.0
PCT_WINDOW_DAYS = 250
PCT_MIN_DAYS = 20
MATURE_DAYS = 540                              # = the longest swing life (1w: 3 x 180 days): the book is complete
GRID_TF = "15m"
DAY_NS = 86_400 * 10**9
PERIODS = (("pdh", "1d", 1), ("pdl", "1d", -1), ("pwh", "1w", 1), ("pwl", "1w", -1), ("pmh", "1M", 1), ("pml", "1M", -1))
FEATURE_NAME = "key_levels"
FEATURE_COLUMNS = ("kl_sup_dist_atr", "kl_res_dist_atr", "kl_sup_strength_pct", "kl_res_strength_pct")
_TOP_RANK = np.array([0] + [int(b).bit_length() for b in range(1, 1 << len(LEVEL_TFS))], dtype=np.int8)
_TF_MASK = (1 << len(LEVEL_TFS)) - 1

Role = Literal["support", "resistance", "inside"]


def tf_bits(names: tuple[str, ...] | list[str]) -> int:
    """The bit mask of a list of timeframe names (TF_BIT)."""
    out = 0
    for n in names:
        out |= TF_BIT[n]
    return out


def tf_names(bits: int) -> tuple[str, ...]:
    """The timeframe names of a bit mask, finest first, `round` last."""
    return tuple(n for n, b in TF_BIT.items() if bits & b)


def _top_tf(bits: int) -> str:
    r = int(_TOP_RANK[bits & _TF_MASK])
    return LEVEL_TFS[r - 1] if r else ROUND


def decayed_weight(tf: str, age_ns: np.ndarray) -> np.ndarray:
    """TF_WEIGHT[tf] halved every HALF_LIFE_DAYS[tf] of age."""
    return TF_WEIGHT[tf] * np.exp2(-np.asarray(age_ns, dtype=float) / (HALF_LIFE_DAYS[tf] * DAY_NS))


def round_levels(close: float, atr_1d: float) -> tuple[np.ndarray, np.ndarray]:
    """$50 multiples within ROUND_RANGE_ATR x ATR(1d) of the close and their weights ($100: 2, $50: 1)."""
    lo, hi = close - ROUND_RANGE_ATR * atr_1d, close + ROUND_RANGE_ATR * atr_1d
    p = np.arange(np.ceil(lo / ROUND_STEP), np.floor(hi / ROUND_STEP) + 1) * ROUND_STEP
    return p, np.where(np.mod(p, 2 * ROUND_STEP) == 0, 2.0, 1.0)


class Clusters(NamedTuple):
    """Zones of one evaluation, sorted by price (the hot loop's plain arrays; KeyZone is the published record)."""
    price: np.ndarray
    lo: np.ndarray
    hi: np.ndarray
    strength: np.ndarray
    bits: np.ndarray
    touches: np.ndarray
    origin: np.ndarray        # weight-signed: > 0 born a resistance (a high), < 0 a support (a low)


def cluster_levels(prices: np.ndarray, weights: np.ndarray, bits: np.ndarray, origin: np.ndarray,
                   width: float) -> Clusters:
    """Group levels into zones no wider than `width`, greedily from the lowest price (module docstring)."""
    order = np.argsort(prices, kind="stable")
    p, w, b, o = prices[order], weights[order], bits[order], origin[order]
    n = len(p)
    if n == 0:
        e = np.empty(0)
        return Clusters(e, e, e, e, np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64), e)
    starts = [0]
    i = int(np.searchsorted(p, p[0] + width, side="right"))
    while i < n:
        starts.append(i)
        i = int(np.searchsorted(p, p[i] + width, side="right"))
    s = np.asarray(starts)
    ends = np.append(s[1:], n) - 1
    strength = np.add.reduceat(w, s)
    level = (b & _TF_MASK) != 0
    return Clusters(price=np.add.reduceat(w * p, s) / strength, lo=p[s], hi=p[ends], strength=strength,
                    bits=np.bitwise_or.reduceat(b.astype(np.int64), s),
                    touches=np.add.reduceat(level.astype(np.int64), s), origin=np.add.reduceat(w * o, s))


def role_of(z: Clusters, k: int, close: float) -> tuple[Role, bool]:
    """(role, flipped) of zone k for a close (module docstring)."""
    if close > z.hi[k]:
        return "support", bool(z.origin[k] > 0)
    if close < z.lo[k]:
        return "resistance", bool(z.origin[k] < 0)
    return "inside", False


class KeyZone(FrozenRecord):
    price: float
    lo: float
    hi: float
    strength: float
    strength_pct: float | None          # percentile among the trailing 250 days' zones; None while warming up
    tfs: tuple[str, ...]                # timeframes it spans, finest first ("round": a $50/$100 number)
    top_tf: str
    touches: int
    role: Role
    flipped: bool


class KeyZones(FrozenRecord):
    """The book at `as_of` (the last evaluation at or before the asked time); `price` is the last close then."""
    as_of: UtcTimestamp | None
    price: float | None
    atr_1d: float | None
    zones: tuple[KeyZone, ...]          # every zone, by price
    history_days: float | None = None   # input history behind the book (MATURE_DAYS and more: complete)
    pct_ready: bool = False             # strength_pct has its PCT_MIN_DAYS of samples

    def nearest(self, n: int = 3) -> tuple[list[KeyZone], list[KeyZone]]:
        """The n nearest zones above the price (nearest first) and at or below it (nearest first)."""
        if self.price is None:
            return [], []
        p = self.price
        return [z for z in self.zones if z.price > p][:n], [z for z in reversed(self.zones) if z.price <= p][:n]


# ------------------------------------------------------------------------------------------------ inputs
def _raw(bars: pd.DataFrame) -> pd.DataFrame:
    """BAR_COLUMNS bars (bid/ask) from either raw bars or mid bars (open/high/low/close: bid = ask = mid).

    A bar visible later than its natural close (a corrected minute, data/quarantine.py: `visible_at` = when it was
    written) is dropped, as `resample_bars(drop_late=True)` drops it from decision bars: no level can rest on values
    nobody saw at that close. ValueError when `visible_at` then goes backwards (the frame is not in time order)."""
    if "bid_close" in bars.columns:
        out = bars[[c for c in BAR_COLUMNS if c in bars.columns]].copy()
    else:
        out = pd.DataFrame({"ts_utc": bars["ts_utc"]})
        for c in ("open", "high", "low", "close"):
            out[f"bid_{c}"] = out[f"ask_{c}"] = bars[c].to_numpy(dtype=float)
    ts = utc_index(out["ts_utc"])
    out["ts_utc"] = ts
    if "visible_at" in bars.columns:
        out["visible_at"] = utc_index(bars["visible_at"])
    else:
        out["visible_at"] = ts + pd.Timedelta(seconds=_step_s(ts))
    for c in ("tick_count", "spread_mean", "spread_max"):
        if c not in out.columns:
            out[c] = 0.0
    kept = np.arange(len(out))
    if len(out):
        on_time = np.asarray(pd.DatetimeIndex(out["visible_at"]) <= ts + pd.Timedelta(seconds=_step_s(ts)))
        out, kept = out[on_time], kept[on_time]
        vis = epoch_ns(pd.DatetimeIndex(out["visible_at"]))
        if len(vis) > 1 and bool((np.diff(vis) < 0).any()):
            raise ValueError("key levels need bars in time order: visible_at goes backwards")
    res = out[BAR_COLUMNS].reset_index(drop=True)
    res.attrs["kept"] = kept                       # positions of the input bars kept (key_level_frames' rows)
    return res


def _step_s(ts: pd.DatetimeIndex) -> int:
    d = np.diff(epoch_ns(ts[:2000])) // 10**9
    return int(np.median(d)) if len(d) else 60


def _base_tf_s(raw: pd.DataFrame) -> int:
    return _step_s(utc_index(raw["ts_utc"]))


class _Inputs(NamedTuple):
    base_close: np.ndarray        # mid close of each input bar
    base_vis: np.ndarray          # its visible_at, int64 ns
    grid_vis: np.ndarray          # evaluation times (int64 ns, increasing)
    grid_close: np.ndarray
    periods: np.ndarray           # (len(grid), 6) PERIODS values at each evaluation (NaN = none)
    atr_1d: np.ndarray            # ATR(14, 1d) at each evaluation (NaN = warming up)
    c_price: np.ndarray           # swing candidates, sorted by known time
    c_known: np.ndarray
    c_expire: np.ndarray
    c_tf: np.ndarray              # index into SWING_TFS
    c_origin: np.ndarray
    start_ns: int                 # the first input bar's open: the book is mature MATURE_DAYS after it


def _tf_mid(raw: pd.DataFrame, tf: str, base_s: int) -> pd.DataFrame:
    if tf_seconds(tf) == base_s:
        return mid(raw)
    return mid(resample_bars(raw, tf))


def _inputs(raw: pd.DataFrame) -> _Inputs:
    base_s = _base_tf_s(raw)
    base = mid(raw)
    grid_tf = GRID_TF if base_s <= tf_seconds(GRID_TF) else next(
        (tf for tf in SWING_TFS if tf_seconds(tf) == base_s), None)
    grid = _tf_mid(raw, grid_tf, base_s) if grid_tf else base
    grid_vis = epoch_ns(utc_index(grid["visible_at"]))
    lv = period_levels(grid["ts_utc"], grid["high"].to_numpy(), grid["low"].to_numpy(), grid["close"].to_numpy())
    periods = lv[[k for k, _, _ in PERIODS]].to_numpy(dtype=float)
    d1 = mid(resample_bars(raw, "1d")) if base_s < tf_seconds("1d") else base
    d_vis = epoch_ns(utc_index(d1["visible_at"]))
    d_atr = atr(d1, ATR_N).to_numpy()
    k = np.searchsorted(d_vis, grid_vis, side="right") - 1
    atr_1d = np.where(k >= 0, d_atr[np.clip(k, 0, None)], np.nan) if len(d_atr) else np.full(len(grid_vis), np.nan)
    price, known, tfi, orig = [], [], [], []
    for i, tf in enumerate(SWING_TFS):
        if tf_seconds(tf) < base_s:
            continue
        b = grid if tf == grid_tf else _tf_mid(raw, tf, base_s)
        if len(b) <= 2 * SWING_LAG:
            continue
        vis = epoch_ns(utc_index(b["visible_at"]))
        is_hi, is_lo = swing_points(b, SWING_LAG)
        for flag, col, sign in ((is_hi, "high", 1), (is_lo, "low", -1)):
            j = np.flatnonzero(flag.to_numpy()[: len(b) - SWING_LAG])
            price.append(b[col].to_numpy(dtype=float)[j])
            known.append(vis[j + SWING_LAG])
            tfi.append(np.full(len(j), i, dtype=np.int64))
            orig.append(np.full(len(j), sign, dtype=np.int64))
    c_price = np.concatenate(price) if price else np.empty(0)
    c_known = np.concatenate(known) if known else np.empty(0, dtype=np.int64)
    c_tf = np.concatenate(tfi) if tfi else np.empty(0, dtype=np.int64)
    c_origin = np.concatenate(orig) if orig else np.empty(0, dtype=np.int64)
    order = np.lexsort((c_origin, c_tf, c_known))           # by known time; a fixed order for equal times
    life = np.array([HALF_LIFE_DAYS[tf] * PRUNE_HALF_LIVES * DAY_NS for tf in SWING_TFS])
    c_price, c_known, c_tf, c_origin = c_price[order], c_known[order], c_tf[order], c_origin[order]
    return _Inputs(base_close=base["close"].to_numpy(dtype=float), base_vis=epoch_ns(utc_index(base["visible_at"])),
                   grid_vis=grid_vis, grid_close=grid["close"].to_numpy(dtype=float), periods=periods, atr_1d=atr_1d,
                   c_price=c_price, c_known=c_known, c_expire=c_known + life[c_tf].astype(np.int64), c_tf=c_tf,
                   c_origin=c_origin, start_ns=int(epoch_ns(utc_index(raw["ts_utc"]))[0]))


# ------------------------------------------------------------------------------------------------ the book
_SWING_W = np.array([TF_WEIGHT[tf] for tf in SWING_TFS], dtype=float)
_SWING_HL = np.array([HALF_LIFE_DAYS[tf] * DAY_NS for tf in SWING_TFS], dtype=float)
_SWING_BIT = np.array([TF_BIT[tf] for tf in SWING_TFS], dtype=np.int64)
_PERIOD_W = np.array([TF_WEIGHT[tf] for _, tf, _ in PERIODS], dtype=float)
_PERIOD_BIT = np.array([TF_BIT[tf] for _, tf, _ in PERIODS], dtype=np.int64)
_PERIOD_ORIGIN = np.array([s for _, _, s in PERIODS], dtype=np.int64)


def _book(inp: _Inputs, k: int, active: np.ndarray) -> Clusters | None:
    """The zones at evaluation k from the active swing candidates (indices, increasing). None while ATR warms up."""
    a = inp.atr_1d[k]
    if not np.isfinite(a) or a <= 0:
        return None
    e = inp.grid_vis[k]
    tf = inp.c_tf[active]
    w_sw = _SWING_W[tf] * np.exp2(-(e - inp.c_known[active]) / _SWING_HL[tf])
    per = inp.periods[k]
    ok = np.isfinite(per)
    rp, rw = round_levels(float(inp.grid_close[k]), float(a))
    prices = np.concatenate((inp.c_price[active], per[ok], rp))
    weights = np.concatenate((w_sw, _PERIOD_W[ok], rw))
    bits = np.concatenate((_SWING_BIT[tf], _PERIOD_BIT[ok], np.full(len(rp), TF_BIT[ROUND], dtype=np.int64)))
    origin = np.concatenate((inp.c_origin[active], _PERIOD_ORIGIN[ok], np.zeros(len(rp), dtype=np.int64)))
    return cluster_levels(prices, weights, bits, origin, CLUSTER_ATR * float(a))


def _active_at(inp: _Inputs, e: int) -> np.ndarray:
    return np.flatnonzero((inp.c_known <= e) & (inp.c_expire > e))


class _Pool:
    """The trailing PCT_WINDOW_DAYS days' zone strengths (one sample per UTC day: the day's first book)."""

    def __init__(self) -> None:
        self.days: deque[tuple[int, np.ndarray]] = deque()
        self.sorted: np.ndarray | None = None

    def roll(self, day: int) -> None:
        while self.days and self.days[0][0] < day - PCT_WINDOW_DAYS:
            self.days.popleft()
        self.sorted = np.sort(np.concatenate([s for _, s in self.days])) if len(self.days) >= PCT_MIN_DAYS else None

    def pct(self, strength: np.ndarray) -> np.ndarray:
        if self.sorted is None or len(self.sorted) == 0:
            return np.full(len(strength), np.nan)
        return 100.0 * np.searchsorted(self.sorted, strength, side="right") / len(self.sorted)


def _first_of_day(grid_vis: np.ndarray) -> np.ndarray:
    day = grid_vis // DAY_NS
    return np.concatenate(([True], day[1:] != day[:-1])) if len(day) else np.empty(0, dtype=bool)


# ------------------------------------------------------------------------------------------------ public API
def zones_asof(bars_1m: pd.DataFrame, t: pd.Timestamp | str) -> KeyZones:
    """Every key zone known at `t`, from the input bars visible by then (raw bid/ask or mid; 1m gives all
    timeframes). Same values as `key_level_frame` at a bar closing at `t`."""
    t = pd.Timestamp(t)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    raw = _raw(bars_1m)
    raw = raw[raw["visible_at"] <= t].reset_index(drop=True)
    empty = KeyZones(as_of=None, price=None, atr_1d=None, zones=())
    if raw.empty:
        return empty
    inp = _inputs(raw)
    t_ns = int(epoch_ns(pd.DatetimeIndex([t]))[0])
    k = int(np.searchsorted(inp.grid_vis, t_ns, side="right")) - 1
    if k < 0:
        return empty
    pool = _Pool()
    first = np.flatnonzero(_first_of_day(inp.grid_vis))
    today = inp.grid_vis[k] // DAY_NS
    for f in first[(inp.grid_vis[first] // DAY_NS >= today - PCT_WINDOW_DAYS) & (first < k + 1)]:
        day = inp.grid_vis[f] // DAY_NS
        if day == today:
            break
        z = _book(inp, int(f), _active_at(inp, int(inp.grid_vis[f])))
        if z is not None:
            pool.days.append((int(day), z.strength))
    pool.roll(int(today))
    z = _book(inp, k, _active_at(inp, int(inp.grid_vis[k])))
    price = float(inp.base_close[-1])
    as_of = pd.Timestamp(int(inp.grid_vis[k]), unit="ns", tz="UTC")
    days = (int(inp.grid_vis[k]) - inp.start_ns) / DAY_NS
    if z is None:
        return KeyZones(as_of=as_of, price=price, atr_1d=None, zones=(), history_days=days)
    pct = pool.pct(z.strength)
    zones = []
    for i in range(len(z.price)):
        role, flipped = role_of(z, i, price)
        b = int(z.bits[i])
        zones.append(KeyZone(price=float(z.price[i]), lo=float(z.lo[i]), hi=float(z.hi[i]),
                             strength=float(z.strength[i]),
                             strength_pct=None if np.isnan(pct[i]) else float(pct[i]), tfs=tf_names(b),
                             top_tf=_top_tf(b), touches=int(z.touches[i]), role=role, flipped=flipped))
    return KeyZones(as_of=as_of, price=price, atr_1d=float(inp.atr_1d[k]), zones=tuple(zones), history_days=days,
                    pct_ready=pool.sorted is not None)


FRAME_SIDES = ("sup", "res")
FRAME_FIELDS = ("price", "lo", "hi", "strength_pct", "tfs", "dist_atr")


class LevelFilter(NamedTuple):
    """Which zones a frame looks at: strength_pct >= min_strength_pct (0: every zone, warm-up included) and spanning a
    timeframe >= min_tf (None: any, round numbers included)."""
    min_strength_pct: float = 0.0
    min_tf: str | None = None


def key_level_frame(bars: pd.DataFrame, *, min_strength_pct: float = 0.0, min_tf: str | None = None,
                    at: pd.Series | pd.DatetimeIndex | None = None) -> pd.DataFrame:
    """Per input bar (or per time in `at`): the nearest zone above the close (`res_*`) and at or below it (`sup_*`)
    among the zones LevelFilter(min_strength_pct, min_tf) keeps: price, lo, hi, strength_pct, tfs (TF_BIT mask),
    distance in ATR(14, 1d) (>= 0 both sides); `atr_1d`; `close`; `pct_ready` (strength_pct has its PCT_MIN_DAYS
    of samples); `book_mature` (MATURE_DAYS of input history: every swing a full history would hold is in the book);
    `available_utc` = the bar's close (or the `at` time), the key to join it asof. NaN where no such zone exists."""
    f = LevelFilter(min_strength_pct, min_tf)
    return key_level_frames(bars, [f], at=at)[f]


def key_level_frames(bars: pd.DataFrame, filters: list[LevelFilter],
                     at: pd.Series | pd.DatetimeIndex | None = None) -> dict[LevelFilter, pd.DataFrame]:
    """`key_level_frame` for several filters in one pass over the book (one book, many views: the book is the
    expensive part, not the lookups). `at` (increasing): evaluate only at these times, e.g. decision-bar closes; the
    close is the last input bar visible by then and the book the last evaluation at or before it."""
    raw = _raw(bars)
    base_vis = epoch_ns(utc_index(raw["visible_at"]))
    if at is None:
        times, index = pd.DatetimeIndex(raw["visible_at"]), bars.index[raw.attrs["kept"]]
    else:
        times = utc_index(pd.Series(at))
        index = pd.RangeIndex(len(times))
    row_vis = epoch_ns(times)
    n = len(row_vis)
    outs = {f: {f"{s}_{x}": (np.zeros(n, dtype=np.int64) if x == "tfs" else np.full(n, np.nan))
                for s in FRAME_SIDES for x in FRAME_FIELDS} for f in dict.fromkeys(filters)}
    atr_col, close = np.full(n, np.nan), np.full(n, np.nan)
    ready, mature = np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)
    if n and len(raw):
        inp = _inputs(raw)
        src = np.searchsorted(base_vis, row_vis, side="right") - 1              # the last input bar visible then
        close = np.where(src >= 0, inp.base_close[np.clip(src, 0, None)], np.nan)
        _fill_frames(inp, row_vis, close, outs, atr_col, ready)
        mature = row_vis - inp.start_ns >= MATURE_DAYS * DAY_NS
    res = {}
    for f, out in outs.items():
        cols: dict[str, object] = {"ts_utc": raw["ts_utc"].to_numpy()} if at is None else {}
        frame = pd.DataFrame({**cols, "available_utc": times, **out, "atr_1d": atr_col, "close": close,
                              "pct_ready": ready, "book_mature": mature})
        frame.index = index
        if at is None and len(index) < len(bars):            # a dropped (corrected) bar's row: no zone, not ready
            frame = frame.reindex(bars.index)
            frame["available_utc"] = utc_index(bars["visible_at"]) if "visible_at" in bars else frame["available_utc"]
            for c in ("pct_ready", "book_mature"):
                frame[c] = frame[c].eq(True)
            for c in ("sup_tfs", "res_tfs"):
                frame[c] = frame[c].fillna(0).astype(np.int64)
        res[f] = frame
    return res


def _fill_frames(inp: _Inputs, row_vis: np.ndarray, row_close: np.ndarray,
                 outs: dict[LevelFilter, dict[str, np.ndarray]], atr_col: np.ndarray, ready: np.ndarray) -> None:
    ev = np.searchsorted(inp.grid_vis, row_vis, side="right") - 1          # each row's evaluation (rows sorted)
    bounds = np.searchsorted(ev, np.arange(len(inp.grid_vis) + 1), side="left")
    first = _first_of_day(inp.grid_vis)
    pool = _Pool()
    active = np.empty(0, dtype=np.int64)
    ptr = 0
    ranks = {f: TF_RANK[f.min_tf] if f.min_tf else 0 for f in outs}
    for k in range(len(inp.grid_vis)):
        e = inp.grid_vis[k]
        hi_ptr = int(np.searchsorted(inp.c_known, e, side="right"))
        active = np.concatenate((active[inp.c_expire[active] > e], np.arange(ptr, hi_ptr)))
        ptr = hi_ptr
        r0, r1 = bounds[k], bounds[k + 1]
        if not first[k] and r0 == r1:
            continue                                   # no row and no daily sample reads this book
        if first[k]:
            pool.roll(int(e // DAY_NS))
        z = _book(inp, k, active)
        if z is None:
            continue
        pct = pool.pct(z.strength)
        if first[k]:
            pool.days.append((int(e // DAY_NS), z.strength))
        if r0 == r1:
            continue
        a = inp.atr_1d[k]
        atr_col[r0:r1] = a
        ready[r0:r1] = pool.sorted is not None
        x = row_close[r0:r1]
        rows = np.arange(r0, r1)
        top = _TOP_RANK[z.bits & _TF_MASK]
        for f, out in outs.items():
            keep = np.ones(len(z.price), dtype=bool)
            if f.min_strength_pct > 0:
                keep &= pct >= f.min_strength_pct
            if ranks[f] > 0:
                keep &= top >= ranks[f]
            if keep.any():
                _nearest(out, rows, x, a, z.price[keep], z.lo[keep], z.hi[keep], pct[keep], z.bits[keep])


def _nearest(out: dict[str, np.ndarray], rows: np.ndarray, x: np.ndarray, a: float, c: np.ndarray, lo: np.ndarray,
             hi: np.ndarray, pc: np.ndarray, bt: np.ndarray) -> None:
    i = np.searchsorted(c, x, side="right")
    for side, sel, pick in (("res", i < len(c), i), ("sup", i > 0, i - 1)):
        sel = sel & np.isfinite(x)
        if not sel.any():
            continue
        j, rr = pick[sel], rows[sel]
        out[f"{side}_price"][rr] = c[j]
        out[f"{side}_lo"][rr] = lo[j]
        out[f"{side}_hi"][rr] = hi[j]
        out[f"{side}_strength_pct"][rr] = pc[j]
        out[f"{side}_tfs"][rr] = bt[j]
        out[f"{side}_dist_atr"][rr] = (c[j] - x[sel]) / a if side == "res" else (x[sel] - c[j]) / a


# ------------------------------------------------------------------------------------------------ registered feature
def f_key_levels(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    """Distance (ATR(14, 1d)) and strength percentile of the nearest support and resistance zone: four summary
    columns, so a model stays well inside the 40-feature cap."""
    fr = key_level_frame(df)
    return pd.DataFrame({"kl_sup_dist_atr": fr["sup_dist_atr"].to_numpy(), "kl_res_dist_atr": fr["res_dist_atr"].to_numpy(),
                         "kl_sup_strength_pct": fr["sup_strength_pct"].to_numpy(),
                         "kl_res_strength_pct": fr["res_strength_pct"].to_numpy()}, index=df.index)


def enable() -> str:
    """Register the opt-in `key_levels` feature (idempotent) and return its name. Opt-in (pipeline.OPT_IN_FEATURES):
    the default feature version the live engine builds never changes. NEEDS_STATE: the zones remember up to 540 days
    (1w swings) and the 250-day percentile pool, so a fixed tail cannot reproduce them; built on the whole series."""
    if FEATURE_NAME not in FEATURES:
        feature(FEATURE_NAME, "structure", lookback=0, tags=(NEEDS_STATE,))(f_key_levels)
    return FEATURE_NAME
