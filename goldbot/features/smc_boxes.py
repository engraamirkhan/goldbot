"""Level-to-level features for the support/resistance boxes and order blocks (owner notes 2 and 4; rules in
docs/research/indicator-coverage-2026-10-18.md). Point in time: bar t uses the boxes in force at t and bars up to t,
evaluated on the bar close only.

* Box strategy (lesson 2). A BREAK is a candle that closes `min_depth` (default 0.2, research range 0.2-0.3) of the
  box height through the box from the side it came from: `sr_break_depth` is the signed depth of the close (+ up
  through a box above the previous close, - down through a box below; 1.0 = at the far edge, more = cleared) and
  `sr_break` is +1 / -1 when |depth| >= `min_depth`. A RIP (reaction in price) is a touch without a close through:
  the wick reaches the near edge of a box and the close stays outside the `min_depth` band, `sr_rip` -1 under a box
  above, +1 over a box below. `mfi_regime` (when given) is attached at that bar: "always check the MFI first".
* Targets and stops: `next_box_up_atr` / `next_box_down_atr` are the ATR distances from the close to the nearest box
  edge above / below (take-profit candidates, "target is always the next box"); `broken_box_far_edge_atr` on a break
  bar is the distance back to the far edge of the box just broken (stop candidate); `in_golden_zone` is 1 while the
  close sits between the 50% and 61.8% retracement of the last swing (the owner's no-trade zone).
* `order_blocks` - internal and swing order blocks, rule-only (drawings are not in the pulled plots; the owner has
  them switched off): at a BOS/CHoCH the block is the bar with the lowest low (bull) / highest high (bear) between the
  broken pivot and the break bar; bars at least 2 x the volatility measure wide use their opposite extreme; the 5
  most recent unmitigated blocks are kept per kind and one is mitigated when the close crosses its far edge. The
  volatility measure is ATR(200) or the cumulative mean range.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.features.smc_replica import Arr, atr_seeded, leg_pivots

MIN_DEPTH = 0.2


def sr_box_features(high: Arr, low: Arr, close: Arr, atr: Arr, boxes: list[tuple[Arr, Arr]],
                    golden_a: Arr, golden_b: Arr, mfi_regime: Arr | None = None,
                    min_depth: float = MIN_DEPTH) -> pd.DataFrame:
    """The box columns for bars with `boxes[i]` = (tops, bottoms) in force at bar i (`trace` of
    `smc_replica.sr_channel_breaks`) and the two retracement levels."""
    h, lo, c, a = (np.asarray(x, dtype=float) for x in (high, low, close, atr))
    n = len(c)
    depth, rip = np.zeros(n), np.zeros(n, dtype=int)
    nu, nd, far = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    for i in range(1, n):
        tops, bots = boxes[i]
        if not len(tops):
            continue
        ht = tops - bots
        a_up, a_dn = bots > c[i], tops < c[i]
        if a_up.any():
            nu[i] = (bots[a_up].min() - c[i]) / a[i]
        if a_dn.any():
            nd[i] = (c[i] - tops[a_dn].max()) / a[i]
        cp = c[i - 1]
        up_d = np.where((cp < bots) & (c[i] > bots) & (ht > 0), (c[i] - bots) / np.where(ht > 0, ht, 1.0), 0.0)
        dn_d = np.where((cp > tops) & (c[i] < tops) & (ht > 0), (tops - c[i]) / np.where(ht > 0, ht, 1.0), 0.0)
        ku, kd = int(up_d.argmax()), int(dn_d.argmax())
        if up_d[ku] >= dn_d[kd] and up_d[ku] > 0:
            depth[i] = up_d[ku]
            if depth[i] >= min_depth:
                far[i] = (c[i] - bots[ku]) / a[i]
        elif dn_d[kd] > 0:
            depth[i] = -dn_d[kd]
            if -depth[i] >= min_depth:
                far[i] = (tops[kd] - c[i]) / a[i]
        if abs(depth[i]) < min_depth:
            reach_up = (bots > cp) & (h[i] >= bots) & (c[i] < bots + min_depth * ht)
            reach_dn = (tops < cp) & (lo[i] <= tops) & (c[i] > tops - min_depth * ht)
            if reach_up.any() and (not reach_dn.any() or c[i] < cp):
                rip[i] = -1
            elif reach_dn.any():
                rip[i] = 1
    with np.errstate(invalid="ignore"):
        gz = (c >= np.minimum(golden_a, golden_b)) & (c <= np.maximum(golden_a, golden_b))
    out = pd.DataFrame({"sr_break_depth": depth, "sr_break": np.where(np.abs(depth) >= min_depth, np.sign(depth), 0).astype(int),
                        "sr_rip": rip, "next_box_up_atr": nu, "next_box_down_atr": nd,
                        "broken_box_far_edge_atr": far, "in_golden_zone": gz.astype(int)})
    if mfi_regime is not None:
        out["mfi_regime"] = np.asarray(mfi_regime, dtype=int)
    return out


def order_blocks(high: Arr, low: Arr, close: Arr, events: dict[str, Arr], internal_len: int = 5,
                 swing_len: int = 50, keep: int = 5, atr_len: int = 200, mean_range: bool = False) -> pd.DataFrame:
    """Order blocks per scale (`internal`, `swing`) from the break events (`events` = the `bos_*` / `choch_*`
    columns of `smc_replica`). Columns `ob_<scale>_bull_top/btm`, `_bear_top/btm` (most recent unmitigated block,
    NaN when none), `_bull_n`, `_bear_n` (unmitigated count) and `_bull_break`, `_bear_break` (a block mitigated on
    this bar)."""
    h, lo, c = (np.asarray(x, dtype=float) for x in (high, low, close))
    n = len(c)
    pc = np.concatenate(([c[0]], c[:-1])) if n else c
    tr = np.maximum(h - lo, np.maximum(np.abs(h - pc), np.abs(lo - pc)))
    vol = np.cumsum(tr) / np.arange(1, n + 1) if mean_range else atr_seeded(h, lo, c, atr_len)
    with np.errstate(invalid="ignore"):
        wide = (h - lo) >= 2.0 * vol
    ph, pl = np.where(wide, lo, h), np.where(wide, h, lo)                # parsed high / low
    cols: dict[str, Arr] = {}
    for scale, length in (("internal", internal_len), ("swing", swing_len)):
        piv_h, piv_l = leg_pivots(h, lo, length)
        bull_ev = np.asarray(events[f"bos_bull_{scale}"], dtype=bool) | np.asarray(events[f"choch_bull_{scale}"], dtype=bool)
        bear_ev = np.asarray(events[f"bos_bear_{scale}"], dtype=bool) | np.asarray(events[f"choch_bear_{scale}"], dtype=bool)
        bulls: list[tuple[float, float]] = []                            # (top, btm), newest first
        bears: list[tuple[float, float]] = []
        top_i = btm_i = -1
        res = {k: np.full(n, np.nan) for k in ("bull_top", "bull_btm", "bear_top", "bear_btm")}
        cnt = {k: np.zeros(n) for k in ("bull_n", "bear_n", "bull_break", "bear_break")}
        for i in range(n):
            if not np.isnan(piv_h[i]):
                top_i = i - length
            if not np.isnan(piv_l[i]):
                btm_i = i - length
            if bull_ev[i] and top_i >= 0 and i > top_i:
                a = top_i + int(np.argmin(pl[top_i:i]))
                bulls.insert(0, (float(ph[a]), float(pl[a])))
                del bulls[keep:]
            if bear_ev[i] and btm_i >= 0 and i > btm_i:
                a = btm_i + int(np.argmax(ph[btm_i:i]))
                bears.insert(0, (float(ph[a]), float(pl[a])))
                del bears[keep:]
            kept = [b for b in bulls if c[i] >= b[1]]
            cnt["bull_break"][i], bulls = float(len(kept) < len(bulls)), kept
            kept = [b for b in bears if c[i] <= b[0]]
            cnt["bear_break"][i], bears = float(len(kept) < len(bears)), kept
            cnt["bull_n"][i], cnt["bear_n"][i] = len(bulls), len(bears)
            if bulls:
                res["bull_top"][i], res["bull_btm"][i] = bulls[0]
            if bears:
                res["bear_top"][i], res["bear_btm"][i] = bears[0]
        for k, v in {**res, **cnt}.items():
            cols[f"ob_{scale}_{k}"] = v
    return pd.DataFrame(cols)
