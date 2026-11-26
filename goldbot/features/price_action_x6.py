"""sr-v3 block X6, price action (opt-in, family `price_action_x6`; pre-registered in
docs/research/prereg-2026-11-26-sr-v3.md section 3.2, chosen label-blind before any Stage 0 / Stage L read).

Eight columns in three features, every one with BOUNDED memory by construction (no NEEDS_STATE): a fixed tail of
decision bars at least the declared per-column lookback (registry.COLUMN_LOOKBACKS, proved by
goldbot/research/column_lookback.py) reproduces the full-history value, so the engine needs no carried state, only a
window long enough (or, below 1h, the closed 4h bars of the X2 store; see the card's parity list). No EWM anywhere:
ATRs are simple means over a fixed count of bars, so the 1 %-weight horizon is the window itself.

Point in time: a value at row t reads bars that closed at or before row t's close only. A swing pivot is used only
once the bars that confirm it have closed; a 4h bucket only once its end is at or before the row's close (the
goldbot.features.mtf / price_action visibility rule). Pinned by prefix invariance (tests/test_price_action_x6.py).

* `x6_candles` (stateless): `x6_body_frac` = (close - open) / range, `x6_clv` = (2 close - high - low) / range (close
  location value; with body_frac it spans the upper/lower wick shares: clv = body_frac + (lower - upper) wick share),
  `x6_io_bar` = +1 outside bar, -1 inside bar, 0 otherwise (vs the previous bar). Zero-range bars give 0.
* `x6_structure` (pivots of MS_PIVOT bars each side on the decision bars, only pivots whose whole 2k+1 window lies in
  the last MS_WINDOW bars): `x6_ms_state` = +1 when the last two confirmed swing highs and lows are HH and HL, -1 when
  LH and LL, 0 otherwise or with fewer than two of either; `x6_ms_bos` = the direction of the latest structure break
  (a close crossing the last confirmed swing high up / swing low down) within BOS_DECAY bars, times
  1 - age / BOS_DECAY (1 on the break bar, 0 after BOS_DECAY bars).
* `x6_levels` (areas around swing pivots of LV_PIVOT bars each side on the last LV_WINDOW CLOSED bars of
  max(decision tf, 4h); below 4h the 4h bars are UTC-floored buckets of the decision bars; every area is
  pivot +- LV_HALF x ATR_htf, ATR_htf = mean high-low of the last LV_ATR closed htf bars): `x6_lv_dist_atr` =
  distance of the close to the nearest area (0 inside one) in ATR_htf, capped at LV_CAP (LV_CAP with no area);
  `x6_lv_room_net_atr` = (room up to the nearest area above - room down to the nearest area below) / 2, each in
  ATR_htf and capped at LV_CAP; `x6_lv_brk_state` = the latest event within the last LV_RETEST htf bars (in decision
  bars) of: +1 a close crossing above an area, -1 below; +2 a retest held (within LV_RETEST htf bars of an up-break
  of that area, the bar's low reaches the area and it closes above it), -2 mirrored; 0 none.

Excluded label-blind (card 3.2 X6): engulfing (an H-40 retired flag, literature null net of costs), touches of an
area (contested sign), distance to the last swing (overlaps donchian_pos_20 / dist_vwap48_atr).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.config import TF_SECONDS
from goldbot.data.timeutil import epoch_ns, floor_tf, utc_index
from goldbot.features.columns import Columns
from goldbot.features.price_action import decision_tf
from goldbot.features.registry import FEATURES, FeatureCtx, feature

NAME_CANDLES = "x6_candles"
NAME_STRUCTURE = "x6_structure"
NAME_LEVELS = "x6_levels"
NAMES = (NAME_CANDLES, NAME_STRUCTURE, NAME_LEVELS)
FAMILY = "price_action_x6"
VERSION = "1"

# frozen label-blind constants (a change is a new VERSION and a new block decision)
MS_PIVOT = 3          # structure pivots: extreme of 2 x 3 + 1 decision bars
MS_WINDOW = 60        # structure pivots considered: whole window inside the last 60 decision bars
BOS_DECAY = 24        # a structure break fades linearly to 0 over 24 decision bars
LV_PIVOT = 2          # level pivots: 5-bar fractal on the htf bars
LV_WINDOW = 30        # htf bars a level may come from (4h: about one trading week)
LV_ATR = 14           # ATR_htf = mean high-low of the last 14 closed htf bars
LV_HALF = 0.15        # every level is an area of +- 0.15 ATR_htf (the price_action library's level_half_atr)
LV_CAP = 5.0          # distances and rooms capped at 5 ATR_htf
LV_RETEST = 6         # break / retest state lives 6 htf bars (4h: one day)
HTF = "4h"
# The moment card rev 7 adopted X6 (the push that applied the #484 code review; recorded in ADR 0104's X6 amendment).
# X6 is legitimate only if no sr-v3 label read or Stage 0/L statistic was recorded before it (card rev 7); sr-v3 rows
# after it are the normal Stage 0 onward and never void the amendment.
X6_ADOPTED_UTC = "2026-11-26T10:45:00+00:00"

CANDLE_COLUMNS = ("x6_body_frac", "x6_clv", "x6_io_bar")
STRUCTURE_COLUMNS = ("x6_ms_state", "x6_ms_bos")
LEVEL_COLUMNS = ("x6_lv_dist_atr", "x6_lv_room_net_atr", "x6_lv_brk_state")
COLUMNS = (*CANDLE_COLUMNS, *STRUCTURE_COLUMNS, *LEVEL_COLUMNS)
SIGNED = {"x6_body_frac": 0.0, "x6_clv": 0.0, "x6_ms_state": 0.0, "x6_ms_bos": 0.0, "x6_lv_room_net_atr": 0.0,
          "x6_lv_brk_state": 0.0}
Arr = np.ndarray


def _ohlc(df: pd.DataFrame) -> tuple[Arr, Arr, Arr, Arr]:
    return tuple(df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))  # type: ignore[return-value]


def _tf(df: pd.DataFrame, ctx: FeatureCtx) -> str:
    return str(ctx.get("decision_tf") or decision_tf(utc_index(df["ts_utc"])))


# --------------------------------------------------------------------------- candles
def candles_frame(df: pd.DataFrame) -> pd.DataFrame:
    o, h, lo, c = _ohlc(df)
    rng = h - lo
    with np.errstate(invalid="ignore", divide="ignore"):
        body = np.where(rng > 0, (c - o) / rng, 0.0)
        clv = np.where(rng > 0, (2 * c - h - lo) / rng, 0.0)
    io = np.full(len(c), np.nan)
    if len(c) > 1:
        h1, l1 = h[:-1], lo[:-1]
        hh, ll = h[1:], lo[1:]
        inside = (hh <= h1) & (ll >= l1) & ((hh < h1) | (ll > l1))
        outside = (hh >= h1) & (ll <= l1) & ((hh > h1) | (ll < l1))
        io[1:] = outside.astype(float) - inside.astype(float)
    out = Columns(df.index)
    out["x6_body_frac"], out["x6_clv"], out["x6_io_bar"] = body, clv, io
    return out.frame()


# --------------------------------------------------------------------------- pivots
def pivots(h: Arr, lo: Arr, k: int) -> tuple[Arr, Arr]:
    """Indices j of swing highs / lows: high[j] above the k bars before it and at or above the k bars after it (lows
    mirrored). A pivot at j is known from row j + k on."""
    n = len(h)
    if n < 2 * k + 1:
        e = np.zeros(0, dtype=np.int64)
        return e, e
    wh = np.lib.stride_tricks.sliding_window_view(h, 2 * k + 1)
    wl = np.lib.stride_tricks.sliding_window_view(lo, 2 * k + 1)
    ch, cl = wh[:, k], wl[:, k]
    ph = (ch > wh[:, :k].max(axis=1)) & (ch >= wh[:, k + 1:].max(axis=1))
    pl = (cl < wl[:, :k].min(axis=1)) & (cl <= wl[:, k + 1:].min(axis=1))
    return np.flatnonzero(ph) + k, np.flatnonzero(pl) + k


def _last_two(idx: Arr, vals: Arr, t: Arr, k: int, window: int) -> tuple[Arr, Arr]:
    """(latest, previous) pivot value known at each row t whose 2k+1 window lies inside the last `window` rows; NaN
    where missing."""
    cnt = np.searchsorted(idx, t - k, side="right")
    first_ok = t - window + 1 + k
    last = np.full(len(t), np.nan)
    prev = np.full(len(t), np.nan)
    has1 = cnt >= 1
    j1 = idx[np.maximum(cnt - 1, 0)] if len(idx) else np.zeros(len(t), dtype=np.int64)
    ok1 = has1 & (j1 >= first_ok)
    last[ok1] = vals[j1[ok1]]
    has2 = cnt >= 2
    j2 = idx[np.maximum(cnt - 2, 0)] if len(idx) else np.zeros(len(t), dtype=np.int64)
    ok2 = has2 & (j2 >= first_ok)
    prev[ok2] = vals[j2[ok2]]
    return last, prev


# --------------------------------------------------------------------------- structure
def structure_frame(df: pd.DataFrame) -> pd.DataFrame:
    _, h, lo, c = _ohlc(df)
    n = len(c)
    t = np.arange(n)
    ih, il = pivots(h, lo, MS_PIVOT)
    sh1, sh0 = _last_two(ih, h, t, MS_PIVOT, MS_WINDOW)
    sl1, sl0 = _last_two(il, lo, t, MS_PIVOT, MS_WINDOW)
    with np.errstate(invalid="ignore"):
        up = (sh1 > sh0) & (sl1 > sl0)
        dn = (sh1 < sh0) & (sl1 < sl0)
    state = up.astype(float) - dn.astype(float)
    # structure break at row t: the close crosses the last swing high / low known at row t - 1
    ev = np.zeros(n)
    if n > 1:
        tp = t[1:] - 1
        hi_prev, _ = _last_two(ih, h, tp, MS_PIVOT, MS_WINDOW)
        lo_prev, _ = _last_two(il, lo, tp, MS_PIVOT, MS_WINDOW)
        with np.errstate(invalid="ignore"):
            bu = (c[1:] > hi_prev) & (c[:-1] <= hi_prev)
            bd = (c[1:] < lo_prev) & (c[:-1] >= lo_prev)
        ev[1:] = bu.astype(float) - bd.astype(float)
    bos = np.zeros(n)
    last_row, last_dir = -10 ** 9, 0.0
    for i in range(n):
        if ev[i] != 0:
            last_row, last_dir = i, ev[i]
        age = i - last_row
        bos[i] = last_dir * (1.0 - age / BOS_DECAY) if age < BOS_DECAY else 0.0
    out = Columns(df.index)
    out["x6_ms_state"], out["x6_ms_bos"] = state, bos
    return out.frame()


# --------------------------------------------------------------------------- levels
def _htf_bars(df: pd.DataFrame, tf: str) -> tuple[Arr, Arr, Arr, int]:
    """(high, low, row at which each htf bar is first visible, htf seconds). At or above 4h the decision bars are the
    htf bars (visible at their own row); below, UTC-floored 4h buckets visible at the first row whose close is at or
    after the bucket end."""
    _, h, lo, _ = _ohlc(df)
    sec = TF_SECONDS[tf]
    hsec = TF_SECONDS[HTF]
    if sec >= hsec:
        return h, lo, np.arange(len(h)), sec
    ts = utc_index(df["ts_utc"])
    close_ns = epoch_ns(ts) + sec * 1_000_000_000
    k = epoch_ns(floor_tf(ts, hsec))
    if not len(k):
        e = np.zeros(0)
        return e, e, e.astype(np.int64), hsec
    starts = np.flatnonzero(np.r_[True, k[1:] != k[:-1]])
    end_ns = k[starts] + hsec * 1_000_000_000
    avail = np.searchsorted(close_ns, end_ns, side="left")
    return np.maximum.reduceat(h, starts), np.minimum.reduceat(lo, starts), avail, hsec


def levels_frame(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    _, h, lo, c = _ohlc(df)
    n = len(c)
    hh, hl, avail, hsec = _htf_bars(df, tf)
    retest = LV_RETEST * max(1, hsec // TF_SECONDS[tf])          # in decision bars
    ih, il = pivots(hh, hl, LV_PIVOT)
    piv_j = np.r_[ih, il].astype(np.int64)
    piv_p = np.r_[hh[ih], hl[il]] if len(piv_j) else np.zeros(0)
    piv_id = np.arange(len(piv_j))                               # identity of an area across rows
    m_of = np.searchsorted(avail, np.arange(n), side="right")    # closed htf bars visible at each row
    dist = np.full(n, LV_CAP)
    room = np.zeros(n)
    state = np.zeros(n)
    rng_h = hh - hl
    breaks: dict[int, tuple[int, int]] = {}                      # area id -> (direction, row of the break)
    last_ev, last_row = 0.0, -10 ** 9
    cache_m, AL, AH, ids, atr = -1, np.zeros(0), np.zeros(0), np.zeros(0, dtype=np.int64), np.nan
    for i in range(n):
        m = int(m_of[i])
        if m != cache_m:
            cache_m = m
            atr = float(rng_h[m - LV_ATR:m].mean()) if m >= LV_ATR else np.nan
            ok = (piv_j - LV_PIVOT >= m - LV_WINDOW) & (piv_j + LV_PIVOT <= m - 1)
            hw = LV_HALF * atr if np.isfinite(atr) else 0.0
            AL, AH, ids = piv_p[ok] - hw, piv_p[ok] + hw, piv_id[ok]
            live = set(ids.tolist())
            breaks = {a: b for a, b in breaks.items() if a in live}
        ev = 0.0
        if np.isfinite(atr) and atr > 0:
            ci = c[i]
            if len(AL):
                gap = np.maximum(np.maximum(AL - ci, ci - AH), 0.0)
                dist[i] = min(float(gap.min()) / atr, LV_CAP)
            above = AL[AL > ci]
            below = AH[AH < ci]
            r_up = min(float((above.min() - ci) / atr), LV_CAP) if len(above) else LV_CAP
            r_dn = min(float((ci - below.max()) / atr), LV_CAP) if len(below) else LV_CAP
            room[i] = (r_up - r_dn) / 2.0
            if i > 0 and len(AL):
                cp = c[i - 1]
                up = (cp <= AH) & (ci > AH)
                dn = (cp >= AL) & (ci < AL)
                rt_up = rt_dn = False
                for a, (d, b) in breaks.items():
                    if not 1 <= i - b <= retest:
                        continue
                    k = int(np.flatnonzero(ids == a)[0])
                    if d > 0 and lo[i] <= AH[k] and ci > AH[k]:
                        rt_up = True
                    elif d < 0 and h[i] >= AL[k] and ci < AL[k]:
                        rt_dn = True
                if rt_up != rt_dn:
                    ev = 2.0 if rt_up else -2.0
                elif up.any() != dn.any():
                    ev = 1.0 if up.any() else -1.0
                for a in ids[up].tolist():
                    breaks[int(a)] = (1, i)
                for a in ids[dn].tolist():
                    breaks[int(a)] = (-1, i)
                breaks = {a: b for a, b in breaks.items() if i - b[1] < retest}
        else:
            dist[i] = np.nan
            room[i] = np.nan
        if ev != 0:
            last_ev, last_row = ev, i
        state[i] = last_ev if i - last_row < retest else 0.0
    out = Columns(df.index)
    out["x6_lv_dist_atr"], out["x6_lv_room_net_atr"], out["x6_lv_brk_state"] = dist, room, state
    return out.frame()


# --------------------------------------------------------------------------- lookbacks
def structural_lookback(column: str, tf: str) -> int:
    """The proved upper bound on the bars (current one included) `column` reads at `tf`, from the frozen constants;
    registry.COLUMN_LOOKBACKS declares exactly this; r = decision bars per htf bar (1 at or above 4h).
    - candles: 1 (body, clv), 2 (inside/outside).
    - ms_state: a pivot is used only when its whole 2k+1 window lies in the last MS_WINDOW bars: MS_WINDOW.
    - ms_bos: events of the last BOS_DECAY bars, each reading its previous close and the pivots known one bar before:
      BOS_DECAY + MS_WINDOW.
    - lv_dist / lv_room: areas and ATR_htf from the last LV_WINDOW closed htf bars: LV_WINDOW at or above 4h (the row
      closes its own htf bar); (LV_WINDOW + 1) x r below: a row q bars into the open bucket needs the 30 closed buckets
      whole plus those q bars, and a shorter tail cuts the oldest bucket partway (quant review of #484, measured).
    - lv_brk_state: events of the last LV_RETEST x r bars, each reading the areas then live (and breaks LV_RETEST x r
      bars before it, whose ATR window is shorter than LV_WINDOW): LV_RETEST + LV_WINDOW - 1 at or above 4h,
      (LV_RETEST + LV_WINDOW + 1) x r below."""
    sec = TF_SECONDS[tf]
    hsec = TF_SECONDS[HTF]
    r = max(1, hsec // sec)
    fixed = {"x6_body_frac": 1, "x6_clv": 1, "x6_io_bar": 2, "x6_ms_state": MS_WINDOW,
             "x6_ms_bos": BOS_DECAY + MS_WINDOW}
    if column in fixed:
        return fixed[column]
    if column in ("x6_lv_dist_atr", "x6_lv_room_net_atr"):
        return LV_WINDOW if sec >= hsec else (LV_WINDOW + 1) * r
    if column == "x6_lv_brk_state":
        return LV_RETEST + LV_WINDOW - 1 if sec >= hsec else (LV_RETEST + LV_WINDOW + 1) * r
    raise KeyError(column)


# --------------------------------------------------------------------------- registration
def _f_candles(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    return candles_frame(df)


def _f_structure(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    return structure_frame(df)


def _f_levels(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    return levels_frame(df, _tf(df, ctx))


def enable() -> list[str]:
    """Register the three X6 features (idempotent) and return their names. Opt-in (pipeline.OPT_IN_FEATURES), so the
    default feature set and its version hash are unchanged. Not NEEDS_STATE: the memory is bounded (module doc).
    `lookback` is the feature-level bound in decision bars at the worst tf (1m; its only reader is
    research/dense_labels.py); the per-column, per-tf lookbacks are in registry.COLUMN_LOOKBACKS."""
    if NAME_CANDLES in FEATURES:
        return list(NAMES)
    for name, fn, cols, lb in ((NAME_CANDLES, _f_candles, CANDLE_COLUMNS, 2),
                               (NAME_STRUCTURE, _f_structure, STRUCTURE_COLUMNS, MS_WINDOW + BOS_DECAY + 1),
                               (NAME_LEVELS, _f_levels, LEVEL_COLUMNS, (LV_RETEST + LV_WINDOW + 1) * 240)):
        signed = {k: v for k, v in SIGNED.items() if k in cols}
        feature(name, FAMILY, version=VERSION, lookback=lb, signed=signed)(fn)
    return list(NAMES)


__all__ = ["COLUMNS", "FAMILY", "NAMES", "SIGNED", "candles_frame", "enable", "levels_frame", "pivots",
           "structural_lookback", "structure_frame"]
