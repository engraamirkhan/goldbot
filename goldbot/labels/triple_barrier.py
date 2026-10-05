"""Spread-adjusted triple-barrier labels with uniqueness weights (López de Prado ch. 3-4).

Entry is at the ask for longs / bid for shorts on the bar *after* the signal bar (no same-bar fills);
exits are at bid for longs / ask for shorts, so a win must clear the full round-trip spread.
The barrier search uses only bars after entry, so labels can never see the signal bar's own future.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord


class BarrierSpec(FrozenRecord):
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
    bid_h, bid_l, bid_c = bars["bid_high"].to_numpy(), bars["bid_low"].to_numpy(), bars["bid_close"].to_numpy()
    ask_h, ask_l, ask_c = bars["ask_high"].to_numpy(), bars["ask_low"].to_numpy(), bars["ask_close"].to_numpy()
    a = atr.to_numpy()
    rows = []
    for idx, side in zip(signals["idx"].to_numpy().astype(int), signals["side"].to_numpy().astype(int)):
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
        bar_ts = pd.DatetimeIndex(pd.to_datetime(bars["ts_utc"], utc=True))
        out["ts_utc"] = bar_ts[out["idx"].to_numpy()]
        out["ts_exit"] = bar_ts[out["t_exit"].to_numpy()]
    return out


def uniqueness_weights(labels: pd.DataFrame, n_bars: int) -> pd.Series:
    """Average inverse concurrency over each label's life, times |ret| (return attribution)."""
    if labels.empty:
        return pd.Series(dtype=float)
    conc = np.zeros(n_bars + 1)
    for s, e in zip(labels["t_entry"].to_numpy(), labels["t_exit"].to_numpy()):
        conc[s:e + 1] += 1
    w = np.array([np.mean(1.0 / conc[s:e + 1]) for s, e in zip(labels["t_entry"].to_numpy(), labels["t_exit"].to_numpy())])
    w = w * (labels["ret"].abs().to_numpy() + 1e-6)
    w = w / w.mean()
    return pd.Series(w, index=labels.index, name="weight")
