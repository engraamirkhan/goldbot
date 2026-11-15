"""The red-box week (owner directive 2026-11-15; docs/research/prereg-amendment-2026-11-15-redbox-week.md): "trade
every red box break and close to the next red box on 15m, 1h, 4h", on demo, as a learning-class family (ADR 0061
amendment 2026-11-02) with a replay-only control per arm. One function per rule, used by history and live
(`arm_rules.RULE_FUNCS`); a decision on the close of bar t reads bars <= t only.

* Red boxes: the owner's box indicator as we rebuilt it, the replica's support/resistance channels
  (`smc_replica.sr_channel_breaks`, the owner's chart settings `DEFAULT_PARAMS`) on the arm's OWN timeframe, the boxes
  in force at bar t as the `box_break_close` roster reads them (`trace[t]`). The channel state at t depends on the last
  `smc_replica.context_bars()` bars only, so LOOKBACK_BARS decision bars give live the history run's boxes.
* `redbox_break` (RB15/RB1H/RB4H), the amendment's rule 1, verbatim: "a candle BODY CLOSES through a live replica box
  (close above the box top = long, below the bottom = short), enter at next open, target = the next box beyond in that
  direction, stop just beyond the broken box". Long when close[t-1] <= top < close[t] (short mirrored on the bottom);
  several boxes crossed: the broken box is the farthest crossed (the roster's choice). NO filter: no money flow, no
  trend, no golden zone, no break depth gate; each of those is recorded on the candidate as a review feature
  (FEATURES), never read by the decision.
* Stop: the broken box's far side minus BUFFER_ATR x ATR14 of the arm's bars for a long (plus, short): the roster's
  structural stop (`box_break_close.BUFFER_ATR`), without its 1.5 ATR cap (a cap would be a filter). R = |close - stop|.
* Target: "the next box beyond", the nearest box edge strictly beyond the close in the trade direction, the broken box
  excluded (a box above a long's close gives its bottom; a box the close sits inside gives its top). No such edge: the
  candidate is recorded skipped NO_NEXT_BOX (journalled and labelled by replay, never sent) with a hold-only target.
* Hold: the roster's HOLD_BARS (15m 96, 1h 48, 4h 24). The arm's dollar stop band (`min_stop_usd`/`max_stop_usd`) and
  the common filters (rollover, Friday from 19:00 UTC) apply as to every arm; a band skip is recorded, never resized.
* End: no candidate decided at or after `params.end_utc` (2026-11-22 22:00 UTC, the week's end and the review's start).
* `redbox_control` (RBC15/RBC1H/RBC4H), rule 3: the same break, stop, target and hold on a seeded random ladder with the
  real boxes' spacing (`random_ladder`: the widths and gaps permuted, the ladder re-anchored), and every candidate
  recorded skipped REPLAY_ONLY: the engine journals and records it (`exploration._skipped`, mu 0) and
  `exploration_data.label_candidates` replays it, so the control never places an order (amendment: "no broker orders").
"""
from __future__ import annotations

import hashlib
from typing import Any

import numpy as np
import pandas as pd

from goldbot.features import smc_replica as smc
from goldbot.features.technical import atr
from goldbot.research.arm_rules import SEED_PREFIX, RuleContext, empty
from goldbot.research.arms import Arm

REDBOX_RULES = ("redbox_break", "redbox_control")
TIMEFRAMES = ("15m", "1h", "4h")
BRACKET = "redbox"                  # params.per_trade_bracket: the arm and its control build the bracket the same way
NO_NEXT_BOX = "no_next_box"         # a break with no box beyond it: recorded, never sent
REPLAY_ONLY = "replay_only"         # every control candidate: recorded and replayed, never sent
NO_TARGET_R = 50.0                  # a skipped no-next-box candidate's target (the hold decides its replay)
# decision bars live hands the rule: the channel state's whole memory (smc_replica.context_bars, 313) and margin
LOOKBACK_BARS = dict.fromkeys(REDBOX_RULES, smc.context_bars() + 17)
FEATURES = ("box_top", "box_bottom", "box_height", "break_depth_pct", "next_box", "mfi", "mfi_confirms",
            "trend_swing", "golden_zone", "close_in_box", "n_boxes")


def check(arm: Arm) -> None:
    """Load-time refusal of a row the engine cannot run as the amendment registers it."""
    from goldbot.specialists.box_break_close import HOLD_BARS
    p = arm.params
    if arm.timeframe not in TIMEFRAMES:
        raise ValueError(f"{arm.rule} runs on {TIMEFRAMES}, not {arm.timeframe}")
    if p.get("per_trade_bracket") != BRACKET:
        raise ValueError(f"{arm.rule} needs params.per_trade_bracket {BRACKET!r}")
    if p.get("hold_bars") != HOLD_BARS[arm.timeframe]:
        raise ValueError(f"{arm.rule} on {arm.timeframe} holds {HOLD_BARS[arm.timeframe]} bars (roster HOLD_BARS)")
    try:
        end = pd.Timestamp(p["end_utc"])
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError(f"{arm.rule} needs params.end_utc, an ISO UTC instant") from exc
    if end.tzinfo is None:
        raise ValueError(f"{arm.rule} params.end_utc must carry its zone (UTC)")


def next_edge(tops: np.ndarray, bots: np.ndarray, price: float, side: int, broken: int) -> float:
    """The nearest box edge strictly beyond `price` in direction `side`, box `broken` excluded; NaN when none."""
    keep = np.arange(len(tops)) != broken
    edges = np.concatenate([tops[keep], bots[keep]])
    beyond = edges[edges > price] if side > 0 else edges[edges < price]
    if not len(beyond):
        return float("nan")
    return float(beyond.min() if side > 0 else beyond.max())


def crossing(prev: float, close: float, tops: np.ndarray, bots: np.ndarray) -> tuple[int, int]:
    """(side, broken box) of a close through the boxes: +1 when prev <= top < close for some box (the highest such
    top is broken), -1 when prev >= bottom > close (the lowest such bottom); (0, -1) for none."""
    if not len(tops) or not (np.isfinite(prev) and np.isfinite(close)):
        return 0, -1
    up = (prev <= tops) & (close > tops)
    dn = (prev >= bots) & (close < bots)
    if up.any() and not dn.any():
        return 1, int(np.argmax(np.where(up, tops, -np.inf)))
    if dn.any() and not up.any():
        return -1, int(np.argmin(np.where(dn, bots, np.inf)))
    return 0, -1


def events(o: np.ndarray, c: np.ndarray, a: np.ndarray, ladders: list[tuple[np.ndarray, np.ndarray]],
           hold_bars: int) -> pd.DataFrame:
    """Every break and close on `ladders[t]` (the boxes in force at bar t): the candidate frame (idx, side, risk_usd,
    target_r, max_bars), the geometry columns and `skip` (NO_NEXT_BOX or None). Bars without a finite ATR give none."""
    from goldbot.specialists.box_break_close import BUFFER_ATR
    rows: list[dict[str, Any]] = []
    for t in range(1, len(c)):
        tops, bots = ladders[t]
        tops, bots = np.asarray(tops, float), np.asarray(bots, float)
        side, k = crossing(float(c[t - 1]), float(c[t]), tops, bots)
        if not side or not (np.isfinite(a[t]) and a[t] > 0):
            continue
        stop = bots[k] - BUFFER_ATR * a[t] if side > 0 else tops[k] + BUFFER_ATR * a[t]
        risk = side * (c[t] - stop)
        if not risk > 0:
            continue
        target = next_edge(tops, bots, float(c[t]), side, k)
        height = float(tops[k] - bots[k])
        edge = tops[k] if side > 0 else bots[k]
        depth = 100.0 * side * (c[t] - edge) / height if height > 0 else float("nan")
        inside = bool(((bots < c[t]) & (c[t] < tops)).any())
        rows.append({"idx": t, "side": side, "risk_usd": float(risk),
                     "target_r": side * (target - c[t]) / risk if np.isfinite(target) else NO_TARGET_R,
                     "max_bars": hold_bars - 1, "skip": None if np.isfinite(target) else NO_NEXT_BOX,
                     "box_top": float(tops[k]), "box_bottom": float(bots[k]), "box_height": height,
                     "break_depth_pct": depth, "next_box": target, "close_in_box": inside, "n_boxes": len(tops)})
    if not rows:
        return _empty()
    return pd.DataFrame(rows)


def _empty() -> pd.DataFrame:
    out = empty().assign(skip=pd.Series(dtype=object))
    for f in FEATURES:
        out[f] = pd.Series(dtype=float)
    return out


def _boxes(bars: pd.DataFrame) -> list[tuple[np.ndarray, np.ndarray]]:
    p = smc.DEFAULT_PARAMS
    trace: list[tuple[np.ndarray, np.ndarray]] = []
    o, h, lo, c = (bars[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    smc.sr_channel_breaks(o, h, lo, c, p.sr_pivot_len, p.sr_width_pct, p.sr_max_channels, p.sr_lookback,
                          p.sr_range_window, trace)
    return trace


def _features(bars: pd.DataFrame, out: pd.DataFrame) -> pd.DataFrame:
    """The roster's filters, recorded and never read (rule 1, "RECORD the MFI state, swing trend, golden-zone flag"):
    the money flow at t and whether the roster's confirmation held for the side, the swing trend, the golden zone."""
    from goldbot.specialists.box_break_close import MFI_HIGH, MFI_LOW, MFI_WINDOW
    if out.empty:
        return out
    p = smc.DEFAULT_PARAMS
    h, lo, c = (bars[k].to_numpy(float) for k in ("high", "low", "close"))
    vol = (bars["tick_count"] if "tick_count" in bars.columns else bars["volume"]).to_numpy(float)
    mfi = smc.money_flow(h, lo, c, vol, p.mfi_len)
    frame = smc.replica_frame(bars, p)
    trend = frame["trend_swing"].to_numpy(float)
    f1, f2 = frame["range_f1_dist_atr"].to_numpy(float), frame["range_f2_dist_atr"].to_numpy(float)
    at, side = out["idx"].to_numpy(int), out["side"].to_numpy(int)
    conf = []
    for t, s in zip(at, side, strict=True):
        w = mfi[max(0, t - MFI_WINDOW + 1):t + 1]
        if t < 1 or not (np.isfinite(mfi[t]) and np.isfinite(mfi[t - 1])) or not np.isfinite(w).any():
            conf.append(False)
        elif s > 0:
            conf.append(bool(np.nanmin(w) <= MFI_LOW and mfi[t] > mfi[t - 1]))
        else:
            conf.append(bool(np.nanmax(w) >= MFI_HIGH and mfi[t] < mfi[t - 1]))
    with np.errstate(invalid="ignore"):
        golden = np.isfinite(f1[at]) & np.isfinite(f2[at]) & (f1[at] * f2[at] < 0)
    return out.assign(mfi=mfi[at], mfi_confirms=conf, trend_swing=trend[at], golden_zone=golden)


def _decide(arm: Arm, ctx: RuleContext, ladder_of: Any) -> pd.DataFrame:
    bars = ctx.mid
    if len(bars) < 2:
        return _empty()
    trace = _boxes(bars)
    ladders = [ladder_of(tops, bots) for tops, bots in trace]
    o, c = bars["open"].to_numpy(float), bars["close"].to_numpy(float)
    out = events(o, c, atr(bars, 14).to_numpy(float), ladders, int(arm.params["hold_bars"]))
    if out.empty:
        return out
    end = pd.Timestamp(arm.params["end_utc"]).tz_convert("UTC")
    out = out[np.asarray(ctx.close_ts[out["idx"].to_numpy(int)] < end)].reset_index(drop=True)
    return _features(bars, out) if len(out) else _empty()


def redbox_break(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """RB15/RB1H/RB4H (module docstring): every break and close of the arm's own-timeframe red boxes."""
    return _decide(arm, ctx, lambda tops, bots: (tops, bots))


def random_ladder(arm_id: str, tops: np.ndarray, bots: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """A seeded random ladder with the real ladder's spacing (amendment rule 3, the card's control (a) on boxes): the
    boxes sorted by bottom, their widths and the gaps between neighbours permuted, the lowest bottom moved by U(-g, g)
    (g the median |gap|, else the median width). Seeded on (arm, the boxes rounded to cents): the same box set gives the
    same ladder at every bar and in history and live; a new set draws anew."""
    tops, bots = np.asarray(tops, float), np.asarray(bots, float)
    if not len(tops):
        return tops, bots
    order = np.argsort(bots, kind="stable")
    t, b = tops[order], bots[order]
    key = f"{SEED_PREFIX}|redbox|{arm_id}|" + ",".join(f"{x:.2f}:{y:.2f}" for x, y in zip(b, t, strict=True))
    rng = np.random.default_rng(int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big"))
    widths = rng.permutation(t - b)
    gaps = rng.permutation(b[1:] - t[:-1]) if len(t) > 1 else np.zeros(0)
    g = float(np.median(np.abs(gaps))) if len(gaps) else float(np.median(widths))
    lo = float(b[0]) + float(rng.uniform(-g, g)) if g > 0 else float(b[0])
    new_b, new_t = np.empty(len(t)), np.empty(len(t))
    for i in range(len(t)):
        new_b[i] = lo if i == 0 else new_t[i - 1] + gaps[i - 1]
        new_t[i] = new_b[i] + widths[i]
    return new_t, new_b


def redbox_control(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """RBC15/RBC1H/RBC4H (module docstring): the arm's rule on `random_ladder`, every candidate REPLAY_ONLY."""
    out = _decide(arm, ctx, lambda tops, bots: random_ladder(arm.id, tops, bots))
    if out.empty:
        return out
    return out.assign(skip=[REPLAY_ONLY if pd.isna(s) else s for s in out["skip"]])
