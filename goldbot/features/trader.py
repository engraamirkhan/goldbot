"""Trader-toolkit feature families: session zones, market structure, fair value gaps and order blocks.

Owner's request (strategy-researcher "Trader toolkit"): each tool is a point-in-time feature, "a level, gap or block is
known only once its defining bars have closed", checked by the lookahead test, and reaches a model only through the P4
screen and a pre-registered trial. No specialist declares these columns; they are for research to screen.

Conventions (as the other structure features):
- a value at bar t uses bars that closed at or before t only (bar t included: features are computed on its close);
- distances are (close - level) / ATR14 at bar t, so positive means the close is above the level;
- ATR14 needs 14 bars, so every distance is NaN for the first 13 bars (warm-up); a level that does not exist yet
  (no previous session, no confirmed swing, no unfilled gap, no active block) is NaN, its count or flag is 0.

Definitions:
- Sessions: the UTC windows of `DEFAULT_SESSIONS.sessions_utc` (asia 23:00-07:00, london 07:00-12:30, newyork
  12:30-21:00). A bar belongs to the window holding its start; 21:00-23:00 belongs to none (current-session columns
  NaN there). One session instance = the bars of one window opening (an Asia session spans midnight).
- Day: the repo's feature-day (ends 13:30 America/New_York, `timeutil.feature_day`), as the 1d bars. Week: Sunday
  00:00 UTC weeks (`timeutil.floor_tf`, as the 1w bars).
- Swings: `structure.swing_points` with lag `ctx['swing_lag']` (5): a swing at bar j is known from bar j+lag. The
  distances to the last confirmed swing high/low are the swings family's `dist_last_swing_*_atr` (not duplicated).
- Liquidity sweep: bar t's high trades above a reference high and bar t closes back below it (mirror for lows). The
  reference levels are those known at the end of bar t-1: last confirmed swing high, previous session high and
  previous day high.
- Break of structure: the first close above the last confirmed, not yet broken swing high (+1), or below the last
  confirmed, not yet broken swing low (-1). Each swing is broken at most once.
- Fair value gap: three-bar imbalance formed when bar i closes; bullish if high[i-2] < low[i] (zone high[i-2]..low[i]),
  bearish if low[i-2] > high[i] (zone high[i]..low[i-2]); kept only if wider than `ctx['fvg_min_atr']` (0.1) x ATR
  at bar i. A later bar trading into the zone fills it partially (the zone shrinks to the part not yet traded); it is
  filled, and dropped, once a bar trades through its far edge (bullish: low <= high[i-2]; bearish: high >= low[i-2]).
  So unfilled bullish gaps are always below the close, bearish ones above it.
- Order block: a displacement bar has a body > `ctx['ob_displacement_atr']` (1.0) x ATR and closes beyond the last
  confirmed swing (bullish: close above the last swing high known at t-1). Its order block is the high-low range of the
  last opposite-colour candle in the `ctx['ob_search']` (5) bars before it, known from the displacement bar's close.
  A bullish block is invalidated when a later bar closes below its low, a bearish one when a bar closes above its high.
- Gaps and blocks older than `ctx['smc_max_age']` (400) bars are dropped, so the state is bounded and a live window
  of a few hundred bars sees the same book as the full history once warmed up. Ages count bars since the bar that made
  the gap or block known.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.data.timeutil import epoch_ns, feature_day, floor_tf
from goldbot.features.columns import Columns
from goldbot.features.registry import FeatureCtx, feature
from goldbot.features.structure import swing_points
from goldbot.features.technical import atr

DAY_NS = 86_400 * 1_000_000_000
SESSION_NAMES = tuple(DEFAULT_SESSIONS.sessions_utc)          # asia, london, newyork (session_id order)


def _utc(df: pd.DataFrame) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.to_datetime(df["ts_utc"], utc=True))


def session_instances(ts: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray]:
    """(session code per bar: index into SESSION_NAMES, -1 outside every window; the UTC start of the bar's session
    instance in epoch ns, -1 outside). Windows come from DEFAULT_SESSIONS.sessions_utc and may span midnight."""
    ns = epoch_ns(ts)
    code = np.full(len(ns), -1, dtype=np.int64)
    start = np.full(len(ns), -1, dtype=np.int64)
    tod = ns % DAY_NS
    for k, (a, b) in enumerate(DEFAULT_SESSIONS.sessions_utc.values()):
        so = (a.hour * 3600 + a.minute * 60) * 1_000_000_000
        length = ((b.hour * 3600 + b.minute * 60) * 1_000_000_000 - so) % DAY_NS
        since = (tod - so) % DAY_NS
        inside = since < length
        code[inside] = k
        start[inside] = ns[inside] - since[inside]
    return code, start


def _runs(key: np.ndarray) -> np.ndarray:
    """Run id per bar: increments whenever `key` changes from the previous bar."""
    if len(key) == 0:
        return np.zeros(0, dtype=np.int64)
    return np.concatenate(([0], np.cumsum(key[1:] != key[:-1]))).astype(np.int64)


def _previous_group(rid: np.ndarray, member: np.ndarray, high: np.ndarray, low: np.ndarray,
                    open_: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """High, low and open of the latest member run that started before each bar's own run (so it is complete: all
    its bars closed before the current bar). NaN where there is none."""
    n = len(rid)
    nan = np.full(n, np.nan)
    if not member.any():
        return nan, nan.copy(), nan.copy()
    g = pd.DataFrame({"rid": rid[member], "h": high[member], "l": low[member], "o": open_[member]}).groupby(
        "rid", sort=True).agg(h=("h", "max"), l=("l", "min"), o=("o", "first"))
    rids = g.index.to_numpy()
    pos = np.searchsorted(rids, rid, side="left") - 1
    ok = pos >= 0
    p = np.maximum(pos, 0)
    pick = [np.where(ok, g[c].to_numpy(dtype=float)[p], np.nan) for c in ("h", "l", "o")]
    return pick[0], pick[1], pick[2]


def _hlo(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """open, high, low, close as float arrays."""
    return (df["open"].to_numpy(dtype=float), df["high"].to_numpy(dtype=float), df["low"].to_numpy(dtype=float),
            df["close"].to_numpy(dtype=float))


def _session_levels(df: pd.DataFrame) -> dict[str, np.ndarray]:
    """Point-in-time session, day and week levels per bar (prices, not yet scaled)."""
    o, h, lo, _ = _hlo(df)
    ts = _utc(df)
    code, start = session_instances(ts)
    inside = code >= 0
    rid = _runs(start)
    out: dict[str, np.ndarray] = {}
    out["sess_high"] = pd.Series(np.where(inside, h, np.nan)).groupby(rid).cummax().to_numpy(dtype=float)
    out["sess_low"] = pd.Series(np.where(inside, lo, np.nan)).groupby(rid).cummin().to_numpy(dtype=float)
    out["sess_open"] = np.where(inside, pd.Series(o).groupby(rid).transform("first").to_numpy(dtype=float), np.nan)
    out["prev_sess_high"], out["prev_sess_low"], out["prev_sess_open"] = _previous_group(rid, inside, h, lo, o)
    for k, name in enumerate(SESSION_NAMES):
        out[f"{name}_high"], out[f"{name}_low"], _ = _previous_group(rid, code == k, h, lo, o)
    everything = np.ones(len(df), dtype=bool)
    day = _runs(epoch_ns(feature_day(ts)))
    out["pdh"], out["pdl"], _ = _previous_group(day, everything, h, lo, o)
    week = _runs(epoch_ns(floor_tf(ts, 7 * 86400)))
    out["pwh"], out["pwl"], _ = _previous_group(week, everything, h, lo, o)
    return out


# --------------------------------------------------------------------------- session zones
@feature("session_zones", "session", lookback=1000, signed={"prev_sess_pos": 0.0})
def f_session_zones(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    """Current and previous session high/low/open, each zone's last completed high/low, previous day and week
    high/low, as (close - level) / ATR; `prev_sess_pos` is +1 above the previous session's range, -1 below, 0 inside.
    Warm-up: ATR (13 bars), the first completed session/day/week (NaN until then)."""
    lv = _session_levels(df)
    c = df["close"].to_numpy(dtype=float)
    a = atr(df, 14).to_numpy(dtype=float)
    out = Columns(df.index)
    for key in ("sess_high", "sess_low", "sess_open", "prev_sess_high", "prev_sess_low", "prev_sess_open",
                *(f"{s}_{x}" for s in SESSION_NAMES for x in ("high", "low")), "pdh", "pdl", "pwh", "pwl"):
        out[f"{key}_dist_atr"] = (c - lv[key]) / a
    ph, pl = lv["prev_sess_high"], lv["prev_sess_low"]
    out["prev_sess_pos"] = np.where(c > ph, 1, np.where(c < pl, -1, 0))   # NaN levels compare False: 0 (no session)
    return out.frame()


# --------------------------------------------------------------------------- market structure
def _confirmed_swing_levels(df: pd.DataFrame, lag: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(swing-high confirmed at bar i?, its price, swing-low confirmed at bar i?, its price): a swing at j confirms
    at i = j + lag, once bars j-lag..j+lag have all closed."""
    is_high, is_low = swing_points(df, lag)
    hf = np.concatenate((np.zeros(lag, dtype=bool), is_high.to_numpy(dtype=bool)[: max(len(df) - lag, 0)]))[: len(df)]
    lf = np.concatenate((np.zeros(lag, dtype=bool), is_low.to_numpy(dtype=bool)[: max(len(df) - lag, 0)]))[: len(df)]
    h, lo = df["high"].to_numpy(dtype=float), df["low"].to_numpy(dtype=float)
    hp = np.concatenate((np.full(lag, np.nan), h[: max(len(df) - lag, 0)]))[: len(df)]
    lp = np.concatenate((np.full(lag, np.nan), lo[: max(len(df) - lag, 0)]))[: len(df)]
    return hf, hp, lf, lp


def _last_known_before(flag: np.ndarray, px: np.ndarray) -> np.ndarray:
    """Price of the last confirmed swing known at the end of the previous bar (NaN before the first)."""
    s = pd.Series(np.where(flag, px, np.nan)).ffill().shift(1)
    return s.to_numpy(dtype=float)


@feature("market_structure", "structure", lookback=1000,
         signed={"sweep_dir": 0.0, "bos_event": 0.0, "bos_dir": 0.0})
def f_market_structure(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    """Liquidity sweeps of prior highs/lows and breaks of structure (definitions in the module docstring).
    `sweep_high` = 1 when the bar swept a reference high and closed back below it, `sweep_low` the mirror,
    `sweep_dir` = sweep_low - sweep_high (+1 bullish sweep). `bos_event` is +1/-1 on the breaking bar, `bos_dir` the
    sign of the latest break (0 before the first), `bars_since_bos` NaN before the first break."""
    lag = int(ctx.get("swing_lag", 5))
    _, h, lo, c = _hlo(df)
    n = len(df)
    hf, hp, lf, lp = _confirmed_swing_levels(df, lag)
    lv = _session_levels(df)
    sweep_h = np.zeros(n, dtype=np.int64)
    sweep_l = np.zeros(n, dtype=np.int64)
    prev_sh, prev_sl = _last_known_before(hf, hp), _last_known_before(lf, lp)
    for ref in (prev_sh, lv["prev_sess_high"], lv["pdh"]):
        sweep_h |= (h > ref) & (c < ref)
    for ref in (prev_sl, lv["prev_sess_low"], lv["pdl"]):
        sweep_l |= (lo < ref) & (c > ref)
    event = np.zeros(n, dtype=np.int64)
    level_h = level_l = np.nan
    live_h = live_l = False
    for i in range(n):
        if live_h and c[i] > level_h:
            event[i], live_h = 1, False
        elif live_l and c[i] < level_l:
            event[i], live_l = -1, False
        if hf[i]:
            level_h, live_h = hp[i], True
        if lf[i]:
            level_l, live_l = lp[i], True
    last = pd.Series(np.where(event != 0, event, np.nan)).ffill()
    at = pd.Series(np.where(event != 0, np.arange(n, dtype=float), np.nan)).ffill()
    out = Columns(df.index)
    out["sweep_high"] = sweep_h
    out["sweep_low"] = sweep_l
    out["sweep_dir"] = sweep_l - sweep_h
    out["bos_event"] = event
    out["bos_dir"] = last.fillna(0).to_numpy(dtype=np.int64)
    out["bars_since_bos"] = np.arange(n, dtype=float) - at.to_numpy(dtype=float)
    return out.frame()


# --------------------------------------------------------------------------- fair value gaps
def _nearest_report(n: int) -> dict[str, np.ndarray]:
    return {"dist": np.full(n, np.nan), "age": np.full(n, np.nan), "size": np.full(n, np.nan), "count": np.zeros(n)}


@feature("fair_value_gaps", "smc", lookback=450)
def f_fair_value_gaps(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    """Nearest unfilled bullish (below the close) and bearish (above) fair value gap: distance from the close to the
    gap's near edge in ATR (>= 0), its age in bars, its unfilled size in ATR, and the number of unfilled gaps."""
    min_atr = float(ctx.get("fvg_min_atr", 0.1))
    max_age = int(ctx.get("smc_max_age", 400))
    _, h, lo, c = _hlo(df)
    a = atr(df, 14).to_numpy(dtype=float)
    n = len(df)
    bull, bear = _nearest_report(n), _nearest_report(n)
    up: list[list[float]] = []      # [bottom, top (lowered by partial fills), born]
    dn: list[list[float]] = []      # [bottom (raised by partial fills), top, born]
    for i in range(n):
        if up and lo[i] < max(g[1] for g in up):
            up = [[g[0], min(g[1], lo[i]), g[2]] for g in up if lo[i] > g[0]]
        if dn and h[i] > min(g[0] for g in dn):
            dn = [[max(g[0], h[i]), g[1], g[2]] for g in dn if h[i] < g[1]]
        if i >= 2 and np.isfinite(a[i]):
            if lo[i] - h[i - 2] > min_atr * a[i]:
                up.append([h[i - 2], lo[i], float(i)])
            if lo[i - 2] - h[i] > min_atr * a[i]:
                dn.append([h[i], lo[i - 2], float(i)])
        if up and up[0][2] < i - max_age:
            up = [g for g in up if g[2] >= i - max_age]
        if dn and dn[0][2] < i - max_age:
            dn = [g for g in dn if g[2] >= i - max_age]
        if up:
            g = max(up, key=lambda x: (x[1], x[2]))          # highest top = nearest below; ties: the newest
            bull["dist"][i], bull["age"][i], bull["size"][i] = (c[i] - g[1]) / a[i], i - g[2], (g[1] - g[0]) / a[i]
        if dn:
            g = min(dn, key=lambda x: (x[0], -x[2]))         # lowest bottom = nearest above
            bear["dist"][i], bear["age"][i], bear["size"][i] = (g[0] - c[i]) / a[i], i - g[2], (g[1] - g[0]) / a[i]
        bull["count"][i], bear["count"][i] = len(up), len(dn)
    out = Columns(df.index)
    for side, r in (("bull", bull), ("bear", bear)):
        out[f"fvg_{side}_dist_atr"] = r["dist"]
        out[f"fvg_{side}_age"] = r["age"]
        out[f"fvg_{side}_size_atr"] = r["size"]
        out[f"fvg_{side}_count"] = r["count"]
    return out.frame()


# --------------------------------------------------------------------------- order blocks
@feature("order_blocks", "smc", lookback=1000)
def f_order_blocks(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    """Nearest active bullish and bearish order block: (close - block high) / ATR for a bullish block (negative when
    the close is inside it), (block low - close) / ATR for a bearish one, its age in bars since the displacement that
    made it known, and the number of active blocks. Nearest = smallest distance from the close to the zone (0 inside),
    ties to the newest."""
    lag = int(ctx.get("swing_lag", 5))
    k = float(ctx.get("ob_displacement_atr", 1.0))
    search = int(ctx.get("ob_search", 5))
    max_age = int(ctx.get("smc_max_age", 400))
    o, h, lo, c = _hlo(df)
    a = atr(df, 14).to_numpy(dtype=float)
    n = len(df)
    hf, hp, lf, lp = _confirmed_swing_levels(df, lag)
    prev_sh, prev_sl = _last_known_before(hf, hp), _last_known_before(lf, lp)
    body = c - o
    bull_disp = (body > k * a) & (c > prev_sh)       # NaN ATR or no swing yet compare False
    bear_disp = (-body > k * a) & (c < prev_sl)
    bull, bear = _nearest_report(n), _nearest_report(n)
    up: list[tuple[float, float, int, int]] = []     # (low, high, candle, born)
    dn: list[tuple[float, float, int, int]] = []
    for i in range(n):
        up = [b for b in up if c[i] >= b[0] and b[3] >= i - max_age]      # closed below its low: invalidated
        dn = [b for b in dn if c[i] <= b[1] and b[3] >= i - max_age]
        for disp, book, opposite in ((bull_disp, up, body < 0), (bear_disp, dn, body > 0)):
            if not disp[i]:
                continue
            for j in range(i - 1, max(i - 1 - search, -1), -1):
                if opposite[j]:
                    if all(b[2] != j for b in book):
                        book.append((float(lo[j]), float(h[j]), j, i))
                    break
        if up:
            b = min(up, key=lambda x: (max(c[i] - x[1], 0.0), -x[3]))
            bull["dist"][i], bull["age"][i] = (c[i] - b[1]) / a[i], i - b[3]
        if dn:
            b = min(dn, key=lambda x: (max(x[0] - c[i], 0.0), -x[3]))
            bear["dist"][i], bear["age"][i] = (b[0] - c[i]) / a[i], i - b[3]
        bull["count"][i], bear["count"][i] = len(up), len(dn)
    out = Columns(df.index)
    for side, r in (("bull", bull), ("bear", bear)):
        out[f"ob_{side}_dist_atr"] = r["dist"]
        out[f"ob_{side}_age"] = r["age"]
        out[f"ob_{side}_count"] = r["count"]
    return out.frame()
