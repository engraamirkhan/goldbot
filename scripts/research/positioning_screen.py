"""Positioning and flows screen (frozen rule: docs/research/prereg-positioning-2026-11-01.md).

Screen window 2010-2017 by default (`--confirm` = the pre-registered 2018-01..2025-09 confirm, run only for a screen
survivor). COT and GLD files are parsed and stamped by goldbot.data.positioning (unchanged) and reach the daily decision
bars only through `asof_join` on `available_utc`. Costs: the cost of record pinned at cor-2026-10-31.2 (ADR 0064),
ICM New York cell; swap: the settings prior (long $0.60/oz a night, x3 Wednesday; short 0).

Records nothing in any registry. Writes statistics only (R units, no prices) to --out; the output is not committed.

    .venv/bin/python scripts/research/positioning_screen.py --bars raw/ --cot-dir ~/tmp/positioning/raw \
        --gld-csv ~/tmp/positioning/raw/gld_archive.csv --out ~/tmp/positioning/screen.json
"""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from goldbot.data.positioning import cot_frame, gld_frame, parse_cot, parse_gld_csv  # noqa: E402
from goldbot.data.release import read_bar_files  # noqa: E402
from goldbot.data.resample import mid, resample_bars  # noqa: E402
from goldbot.data.store import asof_join  # noqa: E402
from goldbot.labels.triple_barrier import rollover_nights  # noqa: E402
from goldbot.research.arms import load_costs  # noqa: E402
from goldbot.research.positioning_screen import (  # noqa: E402
    VARIANTS,
    WEEKLY,
    block_bootstrap_means,
    cot_signals,
    exit_bars,
    gld_signals,
    holm,
    matched_control,
    new_report_mask,
    trade_path,
)
from goldbot.specialists.time_series_momentum import (  # noqa: E402
    SLOW,
    TimeSeriesMomentumSpecialist,
    drop_stub_days,
)

COST_VERSION, BROKER, SESSION = "cor-2026-10-31.2", "icm", "newyork"
STOP_ATR = SLOW["stop_atr"]                                  # 1.5 x ATR(1d) = 1 R
SERVER_TZ, TRIPLE_WEEKDAY = "Europe/Athens", 2
SWAP = {"binding": {"long": 0.60, "short": 0.00}, "x1.5": {"long": 0.90, "short": 0.15}}
COST_MULT = {"binding": 1.0, "x1.5": 1.5}
B, BLOCK_WEEKS, SEED, ALPHA = 10_000, 4, 20261101, 0.05
CONTROL_DRAWS, CONTROL_SEED = 2_000, 20261102
MIN_TRADES, MIN_YEAR_TRADES, MIN_POS_YEARS, NET_FLOOR = 50, 5, 5, 0.05
WINDOWS = {"screen": ("2010-01-01", "2018-01-01", "2017-12-01", 2010, 2017),
           "confirm": ("2018-01-01", "2025-10-01", "2025-09-24", 2018, 2025)}


def load_positioning(cot_dir: Path, gld_csv: Path, start_year: int, cut: pd.Timestamp) -> pd.DataFrame:
    """COT (history bundle for <= 2016, yearly zips after) and GLD rows, PIT-stamped by the loader, cut at `cut`."""
    names = ["fut_disagg_txt_hist_2006_2016.zip", "disagg_hist_2006_2016.zip"]
    hist = next((cot_dir / n for n in names if (cot_dir / n).exists()), None)
    if hist is None:
        raise FileNotFoundError("COT history bundle 2006-2016 not found")
    frames = []
    for zp in [hist] + sorted(p for y in range(2017, cut.year + 1)
                              for p in cot_dir.glob(f"*disagg*{y}.zip") if "hist" not in p.name):
        z = zipfile.ZipFile(zp)
        for n in z.namelist():
            if n.lower().endswith((".txt", ".csv")):
                frames.append(parse_cot(z.read(n)))
    cot = pd.concat(frames, ignore_index=True).drop_duplicates("report_date", keep="last")
    vintage = pd.Timestamp("2026-11-01")
    pos = pd.concat([cot_frame(cot.sort_values("report_date"), vintage),
                     gld_frame(parse_gld_csv(gld_csv.read_text()), vintage)], ignore_index=True)
    pos["available_utc"] = pd.to_datetime(pos["available_utc"], utc=True)
    pos = pos[pos["available_utc"] < cut].reset_index(drop=True)
    assert pos["available_utc"].max() < cut, "a positioning row past the window leaked"
    return pos


def decision_frame(b: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """4h mid bars with ATR(1d) and the daily decision bars (H-84's construction)."""
    h4 = resample_bars(b, "4h", drop_late=True)
    d1 = resample_bars(b, "1d")
    m4 = mid(h4).reset_index(drop=True)
    spec = TimeSeriesMomentumSpecialist(**TimeSeriesMomentumSpecialist.presets["slow"])
    atr = spec.barrier_atr(m4, {"d1": d1})
    assert atr is not None
    m4["atr_d"] = atr.to_numpy(dtype=float)
    m4["close_t"] = pd.DatetimeIndex(pd.to_datetime(m4["visible_at"], utc=True)).as_unit("ns")
    daily = mid(drop_stub_days(d1.reset_index(drop=True)))
    pos = spec._visible_on(m4, daily)
    first = (pos >= 0) & (pos != np.concatenate(([-1], pos[:-1])))
    dec = pd.DataFrame({"idx": np.flatnonzero(first)})
    dec["close_t"] = m4["close_t"].to_numpy()[dec["idx"].to_numpy()]
    dec["exit_idx"] = exit_bars(dec["idx"].to_numpy(), len(m4))
    return m4, dec


def join_signals(dec: pd.DataFrame, pos: pd.DataFrame) -> pd.DataFrame:
    probe = pd.DataFrame({"ts_utc": dec["close_t"].to_numpy(), "_row": np.arange(len(dec))})
    cot = asof_join(probe, cot_signals(pos)).sort_values("_row")
    gld = asof_join(probe, gld_signals(pos)).sort_values("_row")
    out = dec.copy()
    out["report_date"] = cot["report_date"].to_numpy()
    out["weekly"] = new_report_mask(out["report_date"])
    for v in ("P1_EXT", "P2_FLOW"):
        out[v] = np.where(out["weekly"], cot[v].fillna(0).to_numpy(), 0).astype(int)
    out["P3_ETF"] = gld["P3_ETF"].fillna(0).astype(int).to_numpy()
    return out


def price(t: pd.DataFrame, m4: pd.DataFrame, rt: float, os_usd: float) -> pd.DataFrame:
    ct = pd.DatetimeIndex(m4["close_t"])
    t["entry_t"] = ct[t["idx"].to_numpy()]
    t["nights"] = rollover_nights(ct[t["idx"].to_numpy()], ct[t["xbar"].to_numpy()], SERVER_TZ, TRIPLE_WEEKDAY)
    base = rt + np.where(t["hit"] == "stop", os_usd, 0.0)
    for name, mult in COST_MULT.items():
        sw = np.where(t["side"] > 0, SWAP[name]["long"], SWAP[name]["short"])
        t[f"net_{name}"] = t["gross"] - (mult * base + t["nights"] * sw) / t["r_usd"]
    t["net_noswap"] = t["gross"] - base / t["r_usd"]
    t["cost_r"] = base / t["r_usd"]
    t["swap_r"] = t["nights"] * np.where(t["side"] > 0, SWAP["binding"]["long"], 0.0) / t["r_usd"]
    return t


def run_trades(m4: pd.DataFrame, dec: pd.DataFrame, sides: np.ndarray, one_at_a_time: bool) -> pd.DataFrame:
    o, h, lo, c = (m4[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    a = m4["atr_d"].to_numpy(float)
    rows, busy = [], -1
    for k, (idx, xi, side) in enumerate(zip(dec["idx"].to_numpy(), dec["exit_idx"].to_numpy(), sides)):
        if side == 0 or not np.isfinite(a[idx]) or a[idx] <= 0 or idx + 1 > len(m4) - 1:
            continue
        if one_at_a_time and idx + 1 <= busy:
            continue
        r = STOP_ATR * a[idx]
        g, xbar, hit = trade_path(o, h, lo, c, int(idx), int(xi), int(side), r)
        busy = xbar
        rows.append({"k": k, "idx": int(idx), "xbar": xbar, "side": int(side), "r_usd": r, "gross": g, "hit": hit,
                     "truncated": bool(xi == len(m4) - 1)})
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=Path, required=True)
    ap.add_argument("--cot-dir", type=Path, required=True)
    ap.add_argument("--gld-csv", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--confirm", nargs="*", default=None, help="run the confirm window for these variants only")
    a = ap.parse_args()
    window = "confirm" if a.confirm is not None else "screen"
    start, cut_s, last_s, y0, y1 = WINDOWS[window]
    cut, start_t, last_signal = (pd.Timestamp(x, tz="UTC") for x in (cut_s, start, last_s))
    variants = tuple(a.confirm) if a.confirm else VARIANTS
    costs = load_costs(ROOT / "config" / "arm_costs.yaml")
    assert costs.of_record is not None
    costs.require_version(COST_VERSION)
    rt, os_usd = costs.charged_round_trip_usd(BROKER, SESSION), costs.overshoot_charge_usd(BROKER, SESSION)

    b = read_bar_files(a.bars, y0, y1)
    b = b[pd.to_datetime(b["ts_utc"], utc=True) < cut].reset_index(drop=True)
    assert pd.to_datetime(b["visible_at"], utc=True).max() <= cut, "a bar past the window leaked"
    pos = load_positioning(a.cot_dir, a.gld_csv, y0, cut)
    m4, dec = decision_frame(b)
    dec = join_signals(dec, pos)
    dec = dec[(dec["close_t"] >= start_t) & (dec["close_t"] < last_signal)].reset_index(drop=True)

    weeks = pd.period_range(start, pd.Timestamp(cut_s) - pd.Timedelta(days=1), freq="W-SUN").start_time
    rng, crng = np.random.default_rng(SEED), np.random.default_rng(CONTROL_SEED)
    # control pools: every eligible decision bar's independent net R as a long and as a short
    pools: dict[bool, dict[str, np.ndarray]] = {}
    for weekly in (True, False):
        d = dec[dec["weekly"]] if weekly else dec
        out = {}
        for s, nm in ((1, "long"), (-1, "short")):
            t = price(run_trades(m4, d.reset_index(drop=True), np.full(len(d), s), False), m4, rt, os_usd)
            out[nm] = t["net_binding"].to_numpy()
            out["years"] = pd.DatetimeIndex(t["entry_t"]).year.to_numpy()
        pools[weekly] = out
    cols = ["gross", "net_binding", "net_x1.5", "net_noswap"]
    res: dict[str, Any] = {"window": window, "cost_version": COST_VERSION, "rt_usd": rt, "overshoot_usd": os_usd,
                           "info": {"n_4h_bars": len(m4), "n_decisions": len(dec),
                                    "n_weekly_decisions": int(dec["weekly"].sum()),
                                    "median_atr_d_usd": float(np.nanmedian(m4["atr_d"])),
                                    "signal_days": {v: int((dec[v] != 0).sum()) for v in VARIANTS}},
                           "variants": {}}
    pvals: dict[str, float] = {}
    ret5 = np.full(len(dec), np.nan)
    c4 = m4["close"].to_numpy(float)
    di = dec["idx"].to_numpy()
    ret5[5:] = np.sign(c4[di[5:]] - c4[di[:-5]])
    for v in variants:
        t = price(run_trades(m4, dec, dec[v].to_numpy(), True), m4, rt, os_usd)
        r: dict[str, Any] = {"n": int(len(t))}
        if len(t):
            wk = pd.DatetimeIndex(t["entry_t"]).tz_convert("UTC").tz_localize(None).to_period("W-SUN").start_time
            g = t.assign(wk=wk).groupby("wk")
            sums = np.stack([g[c].sum().reindex(weeks, fill_value=0.0).to_numpy() for c in cols], axis=1)
            cnt = g.size().reindex(weeks, fill_value=0).to_numpy()
            bm = block_bootstrap_means(sums, cnt, B, BLOCK_WEEKS, rng)
            obs = t[cols].mean().to_numpy()
            se = bm.std(axis=0, ddof=1)
            p_net = (1 + np.sum(bm[:, 1] - obs[1] >= obs[1])) / (B + 1)
            pvals[v] = float(p_net)
            years = pd.DatetimeIndex(t["entry_t"]).year.to_numpy()
            pool = pools[WEEKLY[v]]
            ctrl = matched_control(years, t["side"].to_numpy(), pool["years"], pool["long"], pool["short"],
                                   CONTROL_DRAWS, crng)
            by_year = {int(y): {"n": int((years == y).sum()), "net": float(t.loc[years == y, "net_binding"].mean()),
                                "gross": float(t.loc[years == y, "gross"].mean())} for y in np.unique(years)}
            pos_years = sum(1 for y in range(y0, y1 + 1)
                            if by_year.get(y, {}).get("n", 0) >= MIN_YEAR_TRADES and by_year[y]["net"] > 0)
            side = {nm: {"n": int((t["side"] == s).sum()),
                         "gross": float(t.loc[t["side"] == s, "gross"].mean()) if (t["side"] == s).any() else None,
                         "net": float(t.loc[t["side"] == s, "net_binding"].mean()) if (t["side"] == s).any() else None}
                    for nm, s in (("long", 1), ("short", -1))}
            r5 = ret5[t["k"].to_numpy()]
            ok = np.isfinite(r5)
            r.update({
                "gross_mean": float(obs[0]), "gross_t": float(obs[0] / se[0]),
                "net_mean": float(obs[1]), "net_se": float(se[1]), "p_net": float(p_net),
                "net_x1_5": float(obs[2]), "net_noswap": float(obs[3]),
                "control_mean": float(ctrl.mean()), "control_q95": float(np.quantile(ctrl, 0.95)),
                "control_pctile_of_obs": float(np.mean(ctrl < obs[1])),
                "cost_r_mean": float(t["cost_r"].mean()), "swap_r_mean": float(t["swap_r"].mean()),
                "nights_mean": float(t["nights"].mean()), "r_usd_median": float(t["r_usd"].median()),
                "hits": t["hit"].value_counts().to_dict(), "truncated": int(t["truncated"].sum()),
                "pos_years": pos_years, "by_year": by_year, "by_side": side,
                "side_vs_prior5d_return_corr": float(np.corrcoef(t["side"].to_numpy()[ok], r5[ok])[0, 1])
                if ok.sum() > 2 else None,
            })
        res["variants"][v] = r
    adj = holm(pvals) if window == "screen" else pvals
    for v in variants:
        r = res["variants"][v]
        if v not in adj:
            r["verdict"] = "FAIL (no trades)"
            continue
        r["p_adj"] = adj[v]
        checks = {"n>=50": r["n"] >= MIN_TRADES, "p<0.05": adj[v] < ALPHA, "pos_years>=5": r["pos_years"] >= MIN_POS_YEARS,
                  "net_x1.5>0": r["net_x1_5"] > 0, "net>=+0.05R": r["net_mean"] >= NET_FLOOR,
                  "net>control_q95": r["net_mean"] > r["control_q95"]}
        if window == "screen":
            checks["gross_t>=2"] = r["gross_t"] >= 2
        r["checks"] = checks
        r["verdict"] = "PASS" if all(checks.values()) else "FAIL"
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=2, default=str))
    print(json.dumps({"info": res["info"], **{v: {k: res["variants"][v].get(k) for k in
                      ("n", "gross_mean", "gross_t", "net_mean", "p_net", "p_adj", "net_x1_5", "net_noswap",
                       "control_q95", "pos_years", "verdict")} for v in variants}}, indent=1, default=str))


if __name__ == "__main__":
    main()
