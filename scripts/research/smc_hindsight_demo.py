"""SMC no-hindsight demo (docs/research/time-structure/smc-orderblocks.md). NOT a registry trial, no backtest on gold.

1. Hindsight traps on a driftless random walk (true edge = 0): lag-free swings and "respected" FVG zones versus the
   causal rule. Any positive R on a hindsight row is bias, by construction.
2. Event frequency (counts only, no outcome) of causal detectors on bars given with --bars (parquet with bar_time,
   open, high, low, close); bars from the holdout (2025-10-01) are cut before anything is computed.
3. The randomised-level and randomised-time control generators the doc requires (same distance, same frequency).

Usage: uv run python scripts/research/smc_hindsight_demo.py [--bars PATH] [--n 60000] [--seed 7]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from goldbot.data.timeutil import epoch_ns, utc_index  # noqa: E402

HOLDOUT_FROM = pd.Timestamp("2025-10-01", tz="UTC")


def random_walk_bars(n: int, seed: int = 7) -> pd.DataFrame:
    """Driftless OHLC bars (hourly stamps); each bar is 8 gaussian sub-steps so highs and lows are realistic."""
    rng = np.random.default_rng(seed)
    path = 1000 + np.cumsum(rng.normal(0, 0.3, size=(n, 8)).ravel()).reshape(n, 8)
    o = np.concatenate(([1000.0], path[:-1, -1]))
    t = pd.date_range("2020-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"bar_time": t, "open": o, "high": np.maximum(path.max(axis=1), o),
                         "low": np.minimum(path.min(axis=1), o), "close": path[:, -1]})


def atr(df: pd.DataFrame, n: int = 14) -> np.ndarray:
    pc = df["close"].shift(1).fillna(df["open"])
    tr = np.maximum(df["high"] - df["low"], np.maximum((df["high"] - pc).abs(), (df["low"] - pc).abs()))
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean().to_numpy()


def swing_highs(high: np.ndarray, k: int) -> np.ndarray:
    """True at bar i when high[i] is the unique max of bars i-k..i+k. KNOWN only at the close of bar i+k."""
    n = len(high)
    out = np.zeros(n, bool)
    for i in range(k, n - k):
        w = high[i - k:i + k + 1]
        out[i] = high[i] == w.max() and (w == high[i]).sum() == 1
    return out


def fvgs(df: pd.DataFrame) -> pd.DataFrame:
    """Causal three-bar gaps, known at the close of bar t. Columns t, side (+1 bull), lo, hi (the zone)."""
    h, lo = df["high"].to_numpy(), df["low"].to_numpy()
    rows = []
    for t in range(2, len(df)):
        if lo[t] > h[t - 2]:
            rows.append((t, 1, h[t - 2], lo[t]))
        elif h[t] < lo[t - 2]:
            rows.append((t, -1, h[t], lo[t - 2]))
    return pd.DataFrame(rows, columns=["t", "side", "lo", "hi"])


def bracket(h: np.ndarray, lo: np.ndarray, i0: int, side: int, entry: float, stop: float, target: float,
            max_bars: int = 48) -> float:
    """R of a limit order placed at the close of bar i0. nan = never filled. On the fill bar only the stop is checked
    (intrabar order unknown); stop first if both sit in one later bar (the engine's pessimistic convention)."""
    risk = abs(entry - stop)
    fill = -1
    for j in range(i0 + 1, min(len(h), i0 + 1 + max_bars)):
        if fill < 0:
            if (side > 0 and lo[j] <= entry) or (side < 0 and h[j] >= entry):
                fill = j
            else:
                continue
        if (side > 0 and lo[j] <= stop) or (side < 0 and h[j] >= stop):
            return -1.0
        if j > fill and ((side > 0 and h[j] >= target) or (side < 0 and lo[j] <= target)):
            return abs(target - entry) / risk
    return np.nan if fill < 0 else 0.0


def demo_traps(df: pd.DataFrame) -> dict[str, float]:
    h, lo, c = (df[x].to_numpy() for x in ("high", "low", "close"))
    a = atr(df)
    out: dict[str, float] = {}
    k, fwd = 5, 10
    sh = np.flatnonzero(swing_highs(h, k))
    sh = sh[(sh + k + fwd < len(c)) & ~np.isnan(a[sh])]
    out["T1 swing high, short at close of bar i (peeks k=5 bars): mean 10-bar gain, ATR"] = float(
        np.mean((c[sh] - c[sh + fwd]) / a[sh]))
    out["T1 causal: short at close of bar i+k, when the swing is known: mean 10-bar gain, ATR"] = float(
        np.mean((c[sh + k] - c[sh + k + fwd]) / a[sh]))
    allr, respected = [], []
    for t, side, zlo, zhi in fvgs(df).itertuples(index=False):
        if np.isnan(a[t]) or zhi - zlo < 0.05 * a[t]:
            continue
        w = zhi - zlo
        entry, stop, tgt = (zhi, zlo, zhi + 2 * w) if side > 0 else (zlo, zhi, zlo - 2 * w)
        r = bracket(h, lo, t, side, entry, stop, tgt)
        if np.isnan(r):
            continue
        allr.append(r)
        cl = c[t + 1:t + 51]
        if not ((cl < zlo).any() if side > 0 else (cl > zhi).any()):
            respected.append(r)
    out[f"T2 FVG causal, every filled zone (limit at near edge, stop far edge, 2x width), mean R, n={len(allr)}"] = \
        float(np.mean(allr))
    out[f"T2 FVG 'respected' (zones never closed through in 50 bars: what a chart review shows), mean R, n={len(respected)}"] = \
        float(np.mean(respected))
    return out


def event_counts(df: pd.DataFrame) -> dict[str, float]:
    """Counts per month, no outcome: causal FVGs and previous-day-extreme sweeps closed back inside."""
    d = df[utc_index(df["bar_time"]) < HOLDOUT_FROM].reset_index(drop=True)
    t = utc_index(d["bar_time"])
    months = max((t[-1] - t[0]).days / 30.4, 1.0)
    day = epoch_ns(t - pd.Timedelta(hours=22)) // (24 * 3600 * 10**9)      # forex day roll, winter 22:00 UTC
    g = pd.DataFrame({"day": day, "h": d["high"].to_numpy(), "l": d["low"].to_numpy(), "c": d["close"].to_numpy()})
    ph = g["day"].map(g.groupby("day")["h"].max().shift(1)).to_numpy()
    pl = g["day"].map(g.groupby("day")["l"].min().shift(1)).to_numpy()
    sw_hi = (g["h"].to_numpy() > ph) & (g["c"].to_numpy() < ph)
    sw_lo = (g["l"].to_numpy() < pl) & (g["c"].to_numpy() > pl)
    return {"bars": float(len(d)), "months": round(months, 1), "FVG per month": len(fvgs(d)) / months,
            "prev-day high/low sweep, close back inside, per month": (sw_hi.sum() + sw_lo.sum()) / months}


def control_random_level(events: pd.DataFrame, df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Same event bars and the same |distance| from the close, with the level on a random side: removes the level's
    identity, keeps frequency and distance distribution. events: columns bar (position), level."""
    c = df["close"].to_numpy()[events["bar"].to_numpy()]
    out = events.copy()
    out["level"] = c + rng.choice([-1, 1], len(out)) * np.abs(events["level"].to_numpy() - c)
    return out


def control_random_time(events: pd.DataFrame, df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Same level offset (in ATR), re-timed to a random bar with the same UTC hour and weekday: removes the time
    identity, keeps frequency by hour and weekday and the offset distribution."""
    t = utc_index(df["bar_time"])
    key = (t.hour * 7 + t.dayofweek).to_numpy()
    pools = {int(k): np.flatnonzero(key == k) for k in np.unique(key)}
    a, c = atr(df), df["close"].to_numpy()
    nb, nl = [], []
    for b, lv in zip(events["bar"], events["level"]):
        off = (lv - c[b]) / a[b]
        j = int(rng.choice(pools[int(key[b])]))
        nb.append(j)
        nl.append(c[j] + off * a[j])
    out = events.copy()
    out["bar"], out["level"] = nb, nl
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--bars", default=None)
    a = ap.parse_args()
    print(f"== hindsight traps on a driftless random walk, {a.n} bars (true edge 0) ==")
    for k, v in demo_traps(random_walk_bars(a.n, a.seed)).items():
        print(f"{v:+.3f}  {k}")
    if a.bars:
        print("== causal event frequency (counts only; holdout cut):", a.bars)
        for k, v in event_counts(pd.read_parquet(a.bars)).items():
            print(f"{v:10.1f}  {k}")


if __name__ == "__main__":
    main()
