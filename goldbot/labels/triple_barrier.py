"""Spread-adjusted triple-barrier labels with uniqueness weights (López de Prado ch. 3-4).

Entry is at the ask for longs / bid for shorts on the bar *after* the signal bar (no same-bar fills);
exits are at bid for longs / ask for shorts, so a win must clear the full round-trip spread.
The barrier search uses only bars after entry, so labels can never see the signal bar's own future.

Swap (overnight financing, optional): a position pays or earns the broker's swap for every server-day rollover
(midnight on the broker's server clock) it is held through, three times on the broker's triple day (Wednesday for
XAUUSD at most brokers: it covers the weekend). The position is open from the signal bar's close to the exit bar's
close (conservative for a barrier hit inside the exit bar). The charge is in USD per lot per night with the broker's
sign (negative = paid) and enters `ret` as a fraction of the entry price, like every other cost.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord


class BarrierSpec(FrozenRecord):
    target_atr: float
    stop_atr: float
    max_bars: int
    name: str = "custom"


class SwapSpec(FrozenRecord):
    """Overnight financing per side in USD per lot (contract_oz ounces) per night, broker sign (negative = paid)."""
    long_usd_per_lot: float
    short_usd_per_lot: float
    triple_weekday: int = Field(2, ge=0, le=4)   # 0 = Monday; the rollover after this server day is charged 3 nights
    server_tz: str = "Europe/Athens"             # the broker's server clock: its midnight is the rollover
    contract_oz: float = Field(100.0, gt=0)

    def usd_per_oz(self, side: np.ndarray) -> np.ndarray:
        """Swap per oz per night for each side (+1 long / -1 short)."""
        return np.where(np.asarray(side) > 0, self.long_usd_per_lot, self.short_usd_per_lot) / self.contract_oz


def _server_days(ts: pd.DatetimeIndex, server_tz: str) -> np.ndarray:
    return np.asarray(ts.tz_convert(server_tz).tz_localize(None).normalize().to_numpy().astype("datetime64[D]"))


def rollover_nights(entry_utc: pd.DatetimeIndex, exit_utc: pd.DatetimeIndex, server_tz: str,
                    triple_weekday: int) -> np.ndarray:
    """Nights of swap charged to a position open from `entry_utc` to `exit_utc` (strictly across each rollover): one
    for each server midnight in between that ends a weekday, three for the one ending `triple_weekday`, none for the
    ones ending Saturday or Sunday (the triple pays for the weekend)."""
    entry_utc, exit_utc = pd.DatetimeIndex(entry_utc), pd.DatetimeIndex(exit_utc)
    if len(entry_utc) == 0:
        return np.zeros(0, dtype=int)
    d0 = _server_days(entry_utc, server_tz)
    d1 = _server_days(exit_utc - pd.Timedelta(1, "ns"), server_tz)
    d1 = np.maximum(d0, d1)
    # a rollover at the start of server day d charges the night of day d - 1: count those days in [d0, d1)
    weekdays = np.busday_count(d0, d1, weekmask="1111100")
    mask = ["0"] * 7
    mask[triple_weekday] = "1"
    triples = np.busday_count(d0, d1, weekmask="".join(mask))
    return (weekdays + 2 * triples).astype(int)


def _bar_close_times(bars: pd.DataFrame) -> pd.DatetimeIndex:
    """When each bar closes: its `visible_at` (store schema), else the open plus the median bar spacing."""
    if "visible_at" in bars:
        return pd.DatetimeIndex(pd.to_datetime(bars["visible_at"], utc=True))
    ts = pd.DatetimeIndex(pd.to_datetime(bars["ts_utc"], utc=True))
    step = pd.Series(ts).diff().median() if len(ts) > 1 else pd.Timedelta(0)
    return ts + step


def triple_barrier(bars: pd.DataFrame, signals: pd.DataFrame, spec: BarrierSpec, atr: pd.Series,
                   swap: SwapSpec | None = None) -> pd.DataFrame:
    """
    bars: ts_utc, bid_high, bid_low, bid_close, ask_high, ask_low, ask_close (store schema), positional index 0..n-1
    signals: frame with columns `idx` (position of signal bar) and `side` (+1 long / -1 short)
    atr: ATR per bar (same index as bars)
    Returns one row per signal: label (+1 target, -1 stop, 0 time-out), ret (signed net return), t_exit (position),
    bars_held, entry, exit, barrier_hit. With `swap`, also swap_nights (nights charged, triple counted three times)
    and swap_ret (their total as a signed return, already included in ret); without it the output is unchanged.
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
        if swap is not None:
            close = _bar_close_times(bars)
            nights = rollover_nights(close[out["idx"].to_numpy()], close[out["t_exit"].to_numpy()], swap.server_tz,
                                     swap.triple_weekday)
            out["swap_nights"] = nights
            out["swap_ret"] = nights * swap.usd_per_oz(out["side"].to_numpy()) / out["entry"].to_numpy(dtype=float)
            out["ret"] = out["ret"] + out["swap_ret"]
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


def one_at_a_time(labels: pd.DataFrame) -> pd.DataFrame:
    """Keep a candidate only once the previously kept one has exited (one position per agent, as the engine and
    the shadow book trade). Rules that fire on runs of consecutive bars otherwise yield heavily overlapping labels:
    the effective sample is far smaller than the row count and neighbouring rows share their price path, which
    inflates any statistic (and the shifted-label leakage check) computed on them."""
    if labels.empty:
        return labels
    lab = labels.sort_values("t_entry")
    keep, busy_until = [], -1
    for i, (e, x) in enumerate(zip(lab["t_entry"].to_numpy(), lab["t_exit"].to_numpy())):
        if e > busy_until:
            keep.append(i)
            busy_until = x
    return lab.iloc[keep].reset_index(drop=True)
