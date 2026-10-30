"""H-84 macro-driven 4h/1d direction: screen on 2010-2017 (frozen rule in docs/research/prereg-2026-11-03-h84-macro.md).

Four variants, one daily decision per stub-free feature-day (COMEX 13:30 New York settlement), read on the first 4h
bar whose close is at or after it, entry at that bar's mid close; H-01's barriers (stop 1.5 x ATR(1d), target 3.0 x
ATR(1d) = 2 R, time 124 four-hour bars), one position at a time, stops filled at the worse of the stop and the bar's
open (gaps). Every macro value reaches a bar only through `asof_join` on `available_utc` at the decision bar's close.

    V1 RY   side = -sign(20-obs change of DFII10)
    V2 USD  side = -sign(20-obs % change of DTWEXBGS)
    V3 H06  H-01 slow TSMOM candidates kept when both changes agree with the side (the coded `slow_macro` preset)
    V4 VRP  long when GVZ^2 - HAR-RV^2 >= the 2/3 point of its own previous 252 daily decision values (min 126)

No 2018+ bar or macro row is loaded (asserted). Records nothing in any registry. Writes statistics only (R units, no
prices) to the JSON path given; the output is not committed.

    .venv/bin/python scripts/research/h84_macro_screen.py --bars raw/ \
        --macro ~/tmp/macro-pit/macro/macro_fred.parquet --out ~/tmp/h84/result.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from goldbot.data.macro import driver_frames  # noqa: E402
from goldbot.data.release import read_bar_files, read_macro_files  # noqa: E402
from goldbot.data.resample import mid, resample_bars  # noqa: E402
from goldbot.data.store import asof_join  # noqa: E402
from goldbot.features.macro import har_rv2_annualised  # noqa: E402
from goldbot.labels.triple_barrier import rollover_nights  # noqa: E402
from goldbot.specialists.time_series_momentum import (  # noqa: E402
    SLOW,
    TimeSeriesMomentumSpecialist,
    drop_stub_days,
)

CUT = pd.Timestamp("2018-01-01", tz="UTC")           # nothing at or after this instant is loaded
LAST_SIGNAL = pd.Timestamp("2017-12-01", tz="UTC")   # decision bar close before this; open trades close on the last bar
STOP_ATR, TARGET_ATR, MAX_BARS = SLOW["stop_atr"], SLOW["target_atr"], SLOW["max_bars"]   # 1.5, 3.0, 124
SERVER_TZ, TRIPLE_WEEKDAY = "Europe/Athens", 2
# binding cost per trade: max(0.23 $/oz all-in at mid [config/arm_costs.yaml],
#                             half the 4h-bar mean spread in + out + 0.37 $/oz [H-01 settings prior])
ALL_IN_USD, EXTRA_USD = 0.23, 0.37
SWAP = {"binding": {"long": 0.60, "short": 0.00}, "x1.5": {"long": 0.90, "short": 0.15}}   # $/oz per night paid
COST_MULT = {"binding": 1.0, "x1.5": 1.5}
VRP_WINDOW, VRP_MIN, VRP_Q = 252, 126, 2 / 3
B, BLOCK_WEEKS, SEED, ALPHA = 10_000, 4, 20261103, 0.05
MIN_TRADES, MIN_YEAR_TRADES, MIN_POS_YEARS = 60, 10, 5
VARIANTS = ("V1_RY", "V2_USD", "V3_H06", "V4_VRP")


def load(bars_dir: Path, macro_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    b = read_bar_files(bars_dir, 2010, 2017)
    b = b[pd.to_datetime(b["ts_utc"], utc=True) < CUT].reset_index(drop=True)
    assert pd.to_datetime(b["visible_at"], utc=True).max() <= CUT, "a 2018 bar leaked"
    m = read_macro_files(macro_path)
    m = m[(pd.to_datetime(m["available_utc"], utc=True) < CUT)
          & (pd.to_datetime(m["value_date"]) < pd.Timestamp("2018-01-01"))].reset_index(drop=True)
    assert pd.to_datetime(m["available_utc"], utc=True).max() < CUT, "a 2018 macro row leaked"
    return b, m


def decision_frame(b: pd.DataFrame, macro: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """4h mid bars with the daily ATR, and the daily decision bars with every variant's side."""
    h4 = resample_bars(b, "4h", drop_late=True)
    d1 = resample_bars(b, "1d")
    m4 = mid(h4).reset_index(drop=True)
    context = {"d1": d1}
    spec = TimeSeriesMomentumSpecialist(**TimeSeriesMomentumSpecialist.presets["slow_macro"])
    m4["atr_d"] = spec.barrier_atr(m4, context).to_numpy(dtype=float)
    close_t = pd.DatetimeIndex(pd.to_datetime(m4["visible_at"], utc=True))
    m4["close_t"] = close_t
    daily = mid(drop_stub_days(d1.reset_index(drop=True)))
    pos = spec._visible_on(m4, daily)
    first = (pos >= 0) & (pos != np.concatenate(([-1], pos[:-1])))
    agree = spec.macro_agreement(m4, macro)                  # asof_join on available_utc at the bar's close
    probe = pd.DataFrame({"ts_utc": close_t.as_unit("ns"), "_row": np.arange(len(m4))})
    gvz_f = driver_frames(macro)["macro_gvz"]
    gvz = asof_join(probe, gvz_f[["available_utc", "macro_gvz"]]).sort_values("_row")["macro_gvz"].to_numpy(float)
    vrp = gvz ** 2 - har_rv2_annualised(m4)
    dec = pd.DataFrame({"idx": np.flatnonzero(first)})
    dec["close_t"] = close_t[dec["idx"].to_numpy()]
    ry = agree["real_yield_chg20"].to_numpy()[dec["idx"]]
    usd = agree["dollar_chg20"].to_numpy()[dec["idx"]]
    dec["V1_RY"] = np.where(np.isfinite(ry), -np.sign(ry), 0).astype(int)
    dec["V2_USD"] = np.where(np.isfinite(usd), -np.sign(usd), 0).astype(int)
    h06 = spec.candidates_in_context(m4, pd.DataFrame(index=m4.index), context, {"macro": macro})
    s06 = pd.Series(h06["side"].to_numpy(), index=h06["idx"].to_numpy())
    dec["V3_H06"] = s06.reindex(dec["idx"].to_numpy()).fillna(0).astype(int).to_numpy()
    v = vrp[dec["idx"].to_numpy()]
    side4 = np.zeros(len(dec), dtype=int)
    for i in range(len(dec)):
        prior = v[max(0, i - VRP_WINDOW):i]
        prior = prior[np.isfinite(prior)]
        if np.isfinite(v[i]) and len(prior) >= VRP_MIN and np.mean(prior < v[i]) >= VRP_Q:
            side4[i] = 1
    dec["V4_VRP"] = side4
    dec = dec[dec["close_t"] < LAST_SIGNAL].reset_index(drop=True)
    info = {"n_4h_bars": len(m4), "n_daily_decisions": len(dec), "h06_candidates_total": int(len(h06)),
            "median_spread_4h_usd": float(np.nanmedian(m4["spread"])),
            "median_atr_d_usd": float(np.nanmedian(m4["atr_d"])),
            "signal_days": {v: int((dec[v] != 0).sum()) for v in VARIANTS}}
    return m4, dec, info


def simulate(m4: pd.DataFrame, dec: pd.DataFrame, col: str) -> pd.DataFrame:
    o, h, lo, c = (m4[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    a, spr = m4["atr_d"].to_numpy(float), m4["spread"].to_numpy(float)
    n, rows, busy = len(m4), [], -1
    for idx, side in zip(dec["idx"].to_numpy(), dec[col].to_numpy()):
        if side == 0 or idx + 1 > n - 1 or not np.isfinite(a[idx]) or a[idx] <= 0 or idx + 1 <= busy:
            continue
        entry, r = c[idx], STOP_ATR * a[idx]
        stop, tgt = entry - side * r, entry + side * TARGET_ATR * a[idx]
        last = min(idx + MAX_BARS, n - 1)
        ex, xbar, hit = c[last], last, "time"
        for j in range(idx + 1, last + 1):
            if side > 0:
                if lo[j] <= stop:
                    ex, xbar, hit = min(o[j], stop), j, "stop"
                    break
                if h[j] >= tgt:
                    ex, xbar, hit = tgt, j, "target"
                    break
            else:
                if h[j] >= stop:
                    ex, xbar, hit = max(o[j], stop), j, "stop"
                    break
                if lo[j] <= tgt:
                    ex, xbar, hit = tgt, j, "target"
                    break
        busy = xbar
        rows.append({"idx": idx, "xbar": xbar, "side": int(side), "r_usd": r, "gross": side * (ex - entry) / r,
                     "hit": hit, "truncated": bool(hit == "time" and last < idx + MAX_BARS),
                     "spread_rt": 0.5 * (spr[idx] + spr[xbar])})
    t = pd.DataFrame(rows)
    if t.empty:
        return t
    ct = pd.DatetimeIndex(m4["close_t"])
    t["entry_t"] = ct[t["idx"].to_numpy()]
    t["nights"] = rollover_nights(ct[t["idx"].to_numpy()], ct[t["xbar"].to_numpy()], SERVER_TZ, TRIPLE_WEEKDAY)
    base_cost = np.maximum(ALL_IN_USD, t["spread_rt"].to_numpy() + EXTRA_USD)
    for name, mult in COST_MULT.items():
        sw = np.where(t["side"] > 0, SWAP[name]["long"], SWAP[name]["short"])
        t[f"net_{name}"] = t["gross"] - (mult * base_cost + t["nights"] * sw) / t["r_usd"]
    t["net_noswap"] = t["gross"] - base_cost / t["r_usd"]
    t["cost_r"] = base_cost / t["r_usd"]
    t["swap_r"] = t["nights"] * np.where(t["side"] > 0, SWAP["binding"]["long"], SWAP["binding"]["short"]) / t["r_usd"]
    return t


def weekly(t: pd.DataFrame, cols: list[str], weeks: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray]:
    wk = pd.DatetimeIndex(t["entry_t"]).tz_convert("UTC").tz_localize(None).to_period("W-SUN").start_time
    g = t.assign(wk=wk).groupby("wk")
    sums = np.stack([g[c].sum().reindex(weeks, fill_value=0.0).to_numpy() for c in cols], axis=1)
    cnt = g.size().reindex(weeks, fill_value=0).to_numpy()
    return sums, cnt


def bootstrap(sums: np.ndarray, cnt: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Circular block bootstrap over calendar weeks (block BLOCK_WEEKS); B x k means per trade."""
    nw = len(cnt)
    nb = int(np.ceil(nw / BLOCK_WEEKS))
    starts = rng.integers(0, nw, size=(B, nb))
    idx = (starts[:, :, None] + np.arange(BLOCK_WEEKS)[None, None, :]).reshape(B, -1)[:, :nw] % nw
    s = sums[idx].sum(axis=1)
    n = cnt[idx].sum(axis=1)
    return s / np.maximum(n, 1)[:, None]


def holm(p: dict[str, float]) -> dict[str, float]:
    keys = sorted(p, key=lambda k: p[k])
    m, out, run = len(keys), {}, 0.0
    for i, k in enumerate(keys):
        run = max(run, min(1.0, (m - i) * p[k]))
        out[k] = run
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=Path, required=True)
    ap.add_argument("--macro", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    b, macro = load(a.bars, a.macro)
    m4, dec, info = decision_frame(b, macro)
    weeks = pd.period_range("2010-01-04", "2017-12-31", freq="W-SUN").start_time
    rng = np.random.default_rng(SEED)
    res: dict[str, Any] = {"info": info, "variants": {}}
    cols = ["gross", "net_binding", "net_x1.5", "net_noswap"]
    pvals: dict[str, float] = {}
    for v in VARIANTS:
        t = simulate(m4, dec, v)
        r: dict[str, Any] = {"n": int(len(t))}
        if len(t):
            sums, cnt = weekly(t, cols, weeks)
            bm = bootstrap(sums, cnt, rng)
            obs = t[cols].mean().to_numpy()
            se = bm.std(axis=0, ddof=1)
            p_net = (1 + np.sum(bm[:, 1] - obs[1] >= obs[1])) / (B + 1)
            pvals[v] = float(p_net)
            yrs = t.assign(y=pd.DatetimeIndex(t["entry_t"]).year).groupby("y")
            by_year = {int(y): {"n": int(len(g)), "net": float(g["net_binding"].mean()),
                                "gross": float(g["gross"].mean())} for y, g in yrs}
            pos_years = sum(1 for y in range(2010, 2018)
                            if by_year.get(y, {}).get("n", 0) >= MIN_YEAR_TRADES and by_year[y]["net"] > 0)
            side = {s: {"n": int((t["side"] == k).sum()),
                        "gross": float(t.loc[t["side"] == k, "gross"].mean()) if (t["side"] == k).any() else None,
                        "net": float(t.loc[t["side"] == k, "net_binding"].mean()) if (t["side"] == k).any() else None}
                    for s, k in (("long", 1), ("short", -1))}
            r.update({
                "gross_mean": float(obs[0]), "gross_t": float(obs[0] / se[0]),
                "net_mean": float(obs[1]), "net_se": float(se[1]),
                "net_lb95_unadj": float(obs[1] - np.quantile(bm[:, 1] - obs[1], 0.95)),
                "p_net": float(p_net), "net_x1_5": float(obs[2]), "net_noswap": float(obs[3]),
                "cost_r_mean": float(t["cost_r"].mean()), "swap_r_mean": float(t["swap_r"].mean()),
                "nights_mean": float(t["nights"].mean()), "bars_held_mean": float((t["xbar"] - t["idx"]).mean()),
                "r_usd_median": float(t["r_usd"].median()),
                "hits": t["hit"].value_counts().to_dict(), "truncated": int(t["truncated"].sum()),
                "pos_years": pos_years, "by_year": by_year, "by_side": side,
                "net_sum_per_decision_day": float(t["net_binding"].sum() / max(1, len(dec))),
            })
        res["variants"][v] = r
    adj = holm(pvals)
    for v in VARIANTS:
        r = res["variants"][v]
        if v not in adj:
            r["verdict"] = "FAIL (no trades)"
            continue
        r["p_holm"] = adj[v]
        checks = {"n>=60": r["n"] >= MIN_TRADES, "holm_p<0.05": adj[v] < ALPHA, "gross_t>=2": r["gross_t"] >= 2,
                  "pos_years>=5": r["pos_years"] >= MIN_POS_YEARS, "net_x1.5>0": r["net_x1_5"] > 0}
        r["checks"] = checks
        r["verdict"] = "PASS" if all(checks.values()) else "FAIL"
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=2, default=str))
    print(json.dumps({v: {k: res["variants"][v].get(k) for k in
                          ("n", "gross_mean", "gross_t", "net_mean", "p_net", "p_holm", "net_x1_5", "pos_years",
                           "verdict")} for v in VARIANTS}, indent=1))


if __name__ == "__main__":
    main()
