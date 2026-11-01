"""Cross-asset lead-lag screen X1-X3 on 2010-2017 (docs/research/prereg-2026-11-01-cross-asset.md, frozen f2daf9b1).

Gold mid 1m (raw/xauusd_1m_dukascopy_<y>.parquet) against XAGUSD, EURUSD, USDJPY bid 1m (raw/xasset/, from
scripts/cross_asset_fetch.py). Lead bars reach the gold grid only through goldbot.data.store.asof_join on
available_utc = bar open + 1m with zero tolerance. One look per variant, variants run in parallel (<= 4 workers).

  uv run python docs/research/candidates/cross_asset/cross_asset_screen.py --gold-raw <dir with xauusd parquet>
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from multiprocessing import Pool
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from goldbot.data.store import asof_join
from goldbot.data.timeutil import epoch_ns

ROOT = Path(__file__).resolve().parents[4]
START, END = pd.Timestamp("2010-01-01", tz="UTC"), pd.Timestamp("2018-01-01", tz="UTC")
COST_VERSION = "cor-2026-10-31.2"
COST_MULT = 1.5
N_TWIN = 4
N_BOOT = 10_000
SEED = 20261101
K_BONF = 3
MIN = 60_000_000_000
LOOKBACK_DAYS = 20
Z_MIN = 2.0
CATCH_MAX = 0.5
HOLD_MIN = 15
STOP_ATR = 1.5
WIN_START, WIN_END = 7 * 60, 19 * 60 + 44      # decision bar opens, minutes of the UTC day, inclusive
VARIANTS: dict[str, list[str]] = {"X1": ["r_xag"], "X2": ["r_usd"], "X3": ["r_xag", "r_eur", "r_jpy"]}


# ---------------------------------------------------------------- data
@dataclass
class Gold:
    ts: np.ndarray        # int64 ns, 1m bar open
    o: np.ndarray
    h: np.ndarray
    lo: np.ndarray
    c: np.ndarray
    atr_end: np.ndarray
    atr: np.ndarray

    def atr15(self, t: int) -> float:
        i = int(np.searchsorted(self.atr_end, t, side="right")) - 1
        return float(self.atr[i]) if i >= 0 else float("nan")


def load_gold(raw: Path) -> Gold:
    b = pd.concat([pd.read_parquet(raw / f"xauusd_1m_dukascopy_{y}.parquet") for y in range(2010, 2018)],
                  ignore_index=True)
    ts = pd.DatetimeIndex(b["ts_utc"]).as_unit("ns")
    keep = (ts >= START) & (ts < END)
    b, ts = b.loc[keep].reset_index(drop=True), ts[keep]
    assert ts.max() < END, "a 2018+ bar was read"
    m = {k: ((b[f"bid_{k}"] + b[f"ask_{k}"]) / 2).to_numpy(float) for k in ("open", "high", "low", "close")}
    t = epoch_ns(ts)
    order = np.argsort(t, kind="stable")
    t = t[order]
    uniq = np.r_[True, np.diff(t) > 0]
    o, h, lo, c = (m[k][order][uniq] for k in ("open", "high", "low", "close"))
    t = t[uniq]
    f = pd.DataFrame({"o": o, "h": h, "l": lo, "c": c}, index=pd.DatetimeIndex(t, tz="UTC"))
    q = f.resample("15min").agg({"o": "first", "h": "max", "l": "min", "c": "last"}).dropna()
    pc = q["c"].shift(1)
    tr = pd.concat([q["h"] - q["l"], (q["h"] - pc).abs(), (q["l"] - pc).abs()], axis=1).max(axis=1)
    ends = epoch_ns(pd.DatetimeIndex(q.index)) + 15 * MIN
    return Gold(t, o, h, lo, c, ends, tr.rolling(14).mean().to_numpy(float))


def ret5(ts: np.ndarray, close: np.ndarray) -> np.ndarray:
    """ln(close_t / close_{t-5m}) from exact minute bars; NaN when the bar 5 minutes earlier is missing."""
    s = pd.Series(close, index=ts)
    lag = s.reindex(ts - 5 * MIN).to_numpy(float)
    return np.log(close / lag)


def load_lead(xdir: Path, code: str) -> pd.DataFrame:
    """One lead's 5m return keyed by available_utc (= bar open + 1m), ready for asof_join."""
    b = pd.concat([pd.read_parquet(xdir / f"{code}_1m_bid_{y}.parquet") for y in range(2010, 2018)], ignore_index=True)
    ts = epoch_ns(pd.DatetimeIndex(b["ts_utc"]).as_unit("ns"))
    order = np.argsort(ts, kind="stable")
    ts, c = ts[order], b["close"].to_numpy(float)[order]
    uniq = np.r_[True, np.diff(ts) > 0]
    ts, c = ts[uniq], c[uniq]
    assert ts.max() < END.value, "a 2018+ lead bar was read"
    return pd.DataFrame({"available_utc": pd.to_datetime(ts + MIN, utc=True), f"r_{code[:3]}": ret5(ts, c)})


# ---------------------------------------------------------------- signal
def rolling_beta(day: np.ndarray, y: np.ndarray, x: np.ndarray, sample: np.ndarray,
                 lookback: int = LOOKBACK_DAYS) -> tuple[dict[int, np.ndarray], dict[int, float]]:
    """Per day D: OLS (no intercept) of y on x over the sampled rows of the previous `lookback` days (strictly before
    D), and the residual sd. Days without a full lookback are absent."""
    ok = sample & np.isfinite(y) & np.isfinite(x).all(1)
    days = np.unique(day)
    p = x.shape[1]
    xtx = np.zeros((len(days), p, p))
    xty = np.zeros((len(days), p))
    yy = np.zeros(len(days))
    nn = np.zeros(len(days))
    pos = np.searchsorted(days, day[ok])
    xs, ys = x[ok], y[ok]
    np.add.at(xtx, pos, xs[:, :, None] * xs[:, None, :])
    np.add.at(xty, pos, xs * ys[:, None])
    np.add.at(yy, pos, ys * ys)
    np.add.at(nn, pos, 1.0)
    betas: dict[int, np.ndarray] = {}
    sds: dict[int, float] = {}
    for i in range(lookback, len(days)):
        a, b, s, n = xtx[i - lookback:i].sum(0), xty[i - lookback:i].sum(0), yy[i - lookback:i].sum(), nn[i - lookback:i].sum()
        if n <= p + 10:
            continue
        beta = np.linalg.solve(a, b)
        rss = s - 2 * beta @ b + beta @ a @ beta
        betas[int(days[i])] = beta
        sds[int(days[i])] = float(np.sqrt(max(rss, 0.0) / (n - p)))
    return betas, sds


def signals(f: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Rows (decision minutes) where the lead implies a gold move >= Z_MIN residual sd and gold has done at most
    CATCH_MAX of it; with side = sign(pred)."""
    t = epoch_ns(pd.DatetimeIndex(f["ts_utc"]))
    bar_open = t - MIN
    mod = (bar_open // MIN) % 1440
    dow = pd.DatetimeIndex(pd.to_datetime(bar_open, utc=True)).dayofweek.to_numpy()
    inwin = (mod >= WIN_START) & (mod <= WIN_END) & (dow < 5)
    day = bar_open // (1440 * MIN)
    x = f[cols].to_numpy(float)
    y = f["r_gold"].to_numpy(float)
    betas, sds = rolling_beta(day, y, x, inwin & (mod % 5 == 0))
    pred = np.full(len(f), np.nan)
    sd = np.full(len(f), np.nan)
    for d, beta in betas.items():
        m = day == d
        pred[m] = x[m] @ beta
        sd[m] = sds[d]
    with np.errstate(invalid="ignore", divide="ignore"):
        catch = y / pred
        fire = inwin & np.isfinite(pred) & np.isfinite(y) & (np.abs(pred) >= Z_MIN * sd) & (catch <= CATCH_MAX)
    out = pd.DataFrame({"t_dec": t[fire], "k": f["k"].to_numpy()[fire], "side": np.sign(pred[fire]).astype(int),
                        "z": (pred / sd)[fire], "day": day[fire]})
    return out[out.side != 0].reset_index(drop=True)


# ---------------------------------------------------------------- trades
def load_costs() -> dict[str, dict[str, float]]:
    cor = yaml.safe_load((ROOT / "config" / "cost_of_record.yaml").read_text())
    assert cor["version"] == COST_VERSION, cor["version"]
    return {s: {"rt": float(v["round_trip_usd"]), "ov": float(v["overshoot_usd"])}
            for s, v in cor["cells"]["icm"].items() if s != "all"}


def session(t: int) -> str:
    mod = (t // MIN) % 1440
    return "asia" if mod < 7 * 60 else ("london" if mod < 12 * 60 + 30 else "newyork")


def walk_time_stop(o: np.ndarray, h: np.ndarray, lo: np.ndarray, side: int, entry: float, risk: float,
                   exit_open: float) -> tuple[float, bool]:
    """Gross $/oz of a trade held over the minutes o/h/lo (entry minute first, the exit minute excluded), stopped at
    entry -+ risk (checked from the entry minute; a gapped stop fills at that minute's open), else out at exit_open."""
    if side > 0:
        stop = entry - risk
        for j in range(len(o)):
            if lo[j] <= stop:
                return min(stop, o[j]) - entry, True
        return exit_open - entry, False
    stop = entry + risk
    for j in range(len(o)):
        if h[j] >= stop:
            return entry - max(stop, o[j]), True
    return entry - exit_open, False


def trade(g: Gold, costs: dict[str, dict[str, float]], t_dec: int, side: int) -> dict[str, Any] | None:
    """Entry at the open of the first gold minute at or after the decision (within 5 min), time exit at the open of the
    first minute at or after entry + HOLD_MIN, stop STOP_ATR x ATR15."""
    k = int(np.searchsorted(g.ts, t_dec, side="left"))
    if k >= len(g.ts) or g.ts[k] - t_dec > 5 * MIN:
        return None
    atr = g.atr15(t_dec)
    if not np.isfinite(atr) or atr <= 0:
        return None
    risk = STOP_ATR * atr
    kx = int(np.searchsorted(g.ts, g.ts[k] + HOLD_MIN * MIN, side="left"))
    if kx >= len(g.ts) or g.ts[kx] - g.ts[k] > (HOLD_MIN + 30) * MIN:
        return None
    entry = float(g.o[k])
    gross, stopped = walk_time_stop(g.o[k:kx], g.h[k:kx], g.lo[k:kx], side, entry, risk, float(g.o[kx]))
    c = costs[session(int(g.ts[k]))]
    cost = COST_MULT * c["rt"] + (COST_MULT * c["ov"] if stopped else 0.0)
    return {"t_entry": int(g.ts[k]), "t_exit": int(g.ts[kx]), "side": side, "gross_usd": gross,
            "net_usd": gross - cost, "risk": risk, "gross_R": gross / risk, "R": (gross - cost) / risk,
            "stopped": stopped}


def h_u(*key: object) -> float:
    return int.from_bytes(hashlib.sha256("|".join(map(str, key)).encode()).digest()[:8], "big") / 2 ** 64


def run_variant(g: Gold, costs: dict[str, dict[str, float]], sig: pd.DataFrame, variant: str,
                day_minutes: dict[int, np.ndarray]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    real: list[dict[str, Any]] = []
    twins: list[dict[str, Any]] = []
    busy_until = -1
    t_dec_a = sig["t_dec"].to_numpy(np.int64)
    side_a = sig["side"].to_numpy(np.int64)
    day_a = sig["day"].to_numpy(np.int64)
    z_a = sig["z"].to_numpy(float)
    for t_dec, sd, day, z in zip(t_dec_a.tolist(), side_a.tolist(), day_a.tolist(), z_a.tolist()):
        if t_dec < busy_until:
            continue
        tr = trade(g, costs, t_dec, sd)
        if tr is None:
            continue
        busy_until = tr["t_exit"]
        tr["day"] = day
        tr["z"] = z
        idx = len(real)
        real.append(tr)
        cand = day_minutes.get(day, np.empty(0, dtype=np.int64))
        for i in range(N_TWIN):
            for attempt in range(20):
                if len(cand) == 0:
                    break
                u = h_u(variant, day, idx, i, attempt)
                t = int(cand[min(int(u * len(cand)), len(cand) - 1)])
                side = 1 if h_u(variant, day, idx, i, attempt, "side") < 0.5 else -1
                tw = trade(g, costs, t, side)
                if tw is not None:
                    tw["day"] = day
                    twins.append(tw)
                    break
    return real, twins


# ---------------------------------------------------------------- statistics
def boot_p(day: np.ndarray, r: np.ndarray, rng: np.random.Generator) -> tuple[float, float]:
    """Day-block bootstrap of the per-trade mean: (5% quantile, one-sided p of mean <= 0)."""
    u, inv = np.unique(day, return_inverse=True)
    s = np.bincount(inv, weights=r)
    n = np.bincount(inv).astype(float)
    idx = rng.integers(0, len(u), size=(N_BOOT, len(u)))
    m = s[idx].sum(1) / n[idx].sum(1)
    return float(np.quantile(m, 0.05)), float((m <= 0).mean())


def stats(real: list[dict[str, Any]], twins: list[dict[str, Any]], rng: np.random.Generator) -> dict[str, Any]:
    if not real:
        return {"n": 0, "pass": False}
    df, tw = pd.DataFrame(real), pd.DataFrame(twins)
    r = df.R.to_numpy(float)
    lb, p_a = boot_p(df.day.to_numpy(), r, rng)
    rs = df.groupby("day").R.sum()
    ts_ = tw.groupby("day").R.sum() / N_TWIN
    d = (rs - ts_.reindex(rs.index).fillna(0.0)).to_numpy(float)
    idx = rng.integers(0, len(d), size=(N_BOOT, len(d)))
    dm = d[idx].mean(1)
    p_b = float((dm <= 0).mean())
    yr = df.groupby(pd.to_datetime(df.t_entry, utc=True).dt.year).R.mean()
    n_days = len(rs)
    out: dict[str, Any] = {
        "n": len(r), "trades_per_week": len(r) / 417.4, "mean_R": float(r.mean()), "lb90_mean_R": lb,
        "p_a": p_a, "p_a_bonf3": min(1.0, K_BONF * p_a), "gross_mean_R": float(df.gross_R.mean()),
        "gross_mean_usd": float(df.gross_usd.mean()), "net_mean_usd": float(df.net_usd.mean()),
        "mean_risk_usd": float(df.risk.mean()), "hit_rate": float((df.net_usd > 0).mean()),
        "gross_hit_rate": float((df.gross_usd > 0).mean()), "stop_rate": float(df.stopped.mean()),
        "twin_n": len(tw), "twin_mean_R": float(tw.R.mean()), "twin_gross_mean_usd": float(tw.gross_usd.mean()),
        "diff_per_day_R": float(d.mean()), "diff_per_trade_R": float(d.sum() / len(r)), "days": n_days,
        "p_b": p_b, "p_b_bonf3": min(1.0, K_BONF * p_b), "years_pos": int((yr > 0).sum()),
        "year_means_R": {str(k): round(float(v), 4) for k, v in zip(yr.index, yr.to_numpy(float))},
    }
    out["pass"] = bool(out["p_a_bonf3"] < 0.10 and out["p_b_bonf3"] < 0.10 and out["years_pos"] >= 5)
    return out


# ---------------------------------------------------------------- driver
_G: Gold | None = None
_F: pd.DataFrame | None = None


def _init(gold_raw: str, xdir: str) -> None:
    global _G, _F
    _G = load_gold(Path(gold_raw))
    f = pd.DataFrame({"ts_utc": pd.to_datetime(_G.ts + MIN, utc=True), "k": np.arange(len(_G.ts)),
                      "r_gold": ret5(_G.ts, _G.c)})
    for code, col in (("xagusd", "r_xag"), ("eurusd", "r_eur"), ("usdjpy", "r_jpy")):
        lead = load_lead(Path(xdir), code).rename(columns={f"r_{code[:3]}": col})
        f = asof_join(f, lead, tolerance=pd.Timedelta(0))
    f["r_usd"] = (f["r_jpy"] - f["r_eur"]) / 2
    _F = f


def _run(variant: str) -> tuple[str, dict[str, Any]]:
    assert _G is not None and _F is not None
    g, f = _G, _F
    costs = load_costs()
    sig = signals(f, VARIANTS[variant])
    mod = (g.ts // MIN) % 1440
    dow = pd.DatetimeIndex(pd.to_datetime(g.ts, utc=True)).dayofweek.to_numpy()
    ok = (mod >= WIN_START) & (mod <= WIN_END) & (dow < 5)
    dec = g.ts[ok] + MIN
    dd = (g.ts[ok]) // (1440 * MIN)
    cut = np.r_[0, np.flatnonzero(np.diff(dd)) + 1, len(dd)]
    day_minutes = {int(dd[a]): dec[a:b] for a, b in zip(cut[:-1], cut[1:])}
    real, twins = run_variant(g, costs, sig, variant, day_minutes)
    res = stats(real, twins, np.random.default_rng([SEED, list(VARIANTS).index(variant)]))
    res["n_signal_minutes"] = len(sig)
    lead_cov = float(f[VARIANTS[variant]].notna().to_numpy().all(axis=1).mean())
    res["lead_coverage"] = lead_cov
    return variant, res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold-raw", default=str(ROOT / "raw"))
    ap.add_argument("--xasset", default=str(ROOT / "raw" / "xasset"))
    ap.add_argument("--out", default=str(ROOT / "raw" / "xasset" / "results.json"))
    a = ap.parse_args()
    with Pool(3, initializer=_init, initargs=(a.gold_raw, a.xasset)) as p:
        res = dict(p.map(_run, list(VARIANTS)))
    Path(a.out).write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
