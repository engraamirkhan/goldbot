"""Spread-adjusted triple-barrier labels with uniqueness weights (López de Prado ch. 3-4).

Entry is at the ask for longs / bid for shorts on the bar *after* the signal bar (no same-bar fills);
exits are at bid for longs / ask for shorts, so a win must clear the full round-trip spread.
The barrier search uses only bars after entry, so labels can never see the signal bar's own future.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class BarrierSpec:
    target_atr: float
    stop_atr: float
    max_bars: int
    name: str = "custom"


def triple_barrier(bars: pd.DataFrame, signals: pd.DataFrame, spec: BarrierSpec, atr: pd.Series) -> pd.DataFrame:
    """
    bars: ts_utc, bid_high, bid_low, bid_close, ask_high, ask_low, ask_close (store schema), positional index 0..n-1
    signals: frame with columns `idx` (position of signal bar) and `side` (+1 long / -1 short)
    atr: ATR per bar (same index as bars)
    Returns one row per signal: label (+1 target, -1 stop, 0 time-out), ret (signed net return), t_exit (position),
    bars_held, entry, exit, barrier_hit.
    """
    n = len(bars)
    bid_h, bid_l, bid_c = bars["bid_high"].values, bars["bid_low"].values, bars["bid_close"].values
    ask_h, ask_l, ask_c = bars["ask_high"].values, bars["ask_low"].values, bars["ask_close"].values
    a = atr.values
    rows = []
    for idx, side in zip(signals["idx"].values.astype(int), signals["side"].values.astype(int)):
        e = idx + 1
        if e >= n or not np.isfinite(a[idx]) or a[idx] <= 0:
            continue
        entry = ask_c[idx] if side > 0 else bid_c[idx]  # fill at next bar open ~ signal close + spread side
        tgt = entry + side * spec.target_atr * a[idx]
        stp = entry - side * spec.stop_atr * a[idx]
        last = min(e + spec.max_bars, n - 1)
        label, exit_px, t_exit, hit = 0, None, last, "time"
        for j in range(e, last + 1):
            if side > 0:
                hit_stop = bid_l[j] <= stp
                hit_tgt = bid_h[j] >= tgt
            else:
                hit_stop = ask_h[j] >= stp
                hit_tgt = ask_l[j] <= tgt
            if hit_stop and hit_tgt:
                # both in one bar: conservative, assume stop first
                label, exit_px, t_exit, hit = -1, stp, j, "stop"
                break
            if hit_stop:
                label, exit_px, t_exit, hit = -1, stp, j, "stop"
                break
            if hit_tgt:
                label, exit_px, t_exit, hit = 1, tgt, j, "target"
                break
        if exit_px is None:
            exit_px = bid_c[last] if side > 0 else ask_c[last]
            label = int(np.sign(side * (exit_px - entry)))
            hit = "time"
        ret = side * (exit_px - entry) / entry
        rows.append({"idx": idx, "side": side, "t_entry": e, "t_exit": t_exit, "bars_held": t_exit - e + 1,
                     "entry": entry, "exit": exit_px, "ret": ret, "label": label, "barrier_hit": hit,
                     "target_hit": int(hit == "target")})
    out = pd.DataFrame(rows)
    if not out.empty:
        out["ts_utc"] = bars["ts_utc"].values[out["idx"].values]
        out["ts_exit"] = bars["ts_utc"].values[out["t_exit"].values]
    return out


def uniqueness_weights(labels: pd.DataFrame, n_bars: int) -> pd.Series:
    """Average inverse concurrency over each label's life, times |ret| (return attribution)."""
    if labels.empty:
        return pd.Series(dtype=float)
    conc = np.zeros(n_bars + 1)
    for s, e in zip(labels["t_entry"].values, labels["t_exit"].values):
        conc[s:e + 1] += 1
    w = np.array([np.mean(1.0 / conc[s:e + 1]) for s, e in zip(labels["t_entry"].values, labels["t_exit"].values)])
    w = w * (labels["ret"].abs().values + 1e-6)
    w = w / w.mean()
    return pd.Series(w, index=labels.index, name="weight")
