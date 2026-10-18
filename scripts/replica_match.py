"""Per-plot match of the `smc_replica` features against locally pulled chart-indicator plot values.

Local only: reads the pull in ~/tmp/tv (bars_auth*.parquet, plots_auth*.parquet), which never enters the repo, and
prints aggregate match figures (precision/recall for events, max error for lines, exact-share for states). It reads no
return and computes no outcome. Table and rules: docs/research/prereg-tv-replica-2026-10-18.md.

    python scripts/replica_match.py [--dir ~/tmp/tv] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from goldbot.features import smc_replica as R

PULLS = {"15": "auth01_OANDA_XAUUSD_15", "60": "auth02_OANDA_XAUUSD_60", "240": "auth03_OANDA_XAUUSD_240",
         "1D": "auth04_OANDA_XAUUSD_1D"}
DAILY_FROM = "1921-01-01"                                                    # earlier daily rows are yearly/monthly


def _study_letter(n_plots: int) -> str:
    """A (structure overlay, 40 plots), B (S/R boxes, 19), C (money flow, 7), told apart by how many plots carry
    values, so no vendor name is needed here."""
    return "a" if n_plots >= 25 else ("b" if n_plots >= 10 else "c")


def load(root: Path, tf: str) -> pd.DataFrame:
    bars = pd.read_parquet(root / f"bars_{PULLS[tf]}.parquet").set_index("bar_time")
    bars = bars[["open", "high", "low", "close", "volume"]]
    p = pd.read_parquet(root / f"plots_{PULLS[tf]}.parquet")
    used = p[p["value"].notna()].groupby("pine_id")["plot_id"].nunique()
    letter = {pid: _study_letter(int(n)) for pid, n in used.items()}
    p["col"] = p["pine_id"].map(letter) + "_" + p["plot_id"].str.replace("plot_", "p")
    wide = p.pivot_table(index="bar_time", columns="col", values="value", aggfunc="first", dropna=False)
    return bars.join(wide).reset_index()


def _event(tv: Any, us: np.ndarray, mask: np.ndarray) -> dict[str, Any]:
    t = np.nan_to_num(np.asarray(tv, dtype=float)).astype(bool)[mask]
    m = np.asarray(us).astype(bool)[mask]
    tp, nt, nm = int((t & m).sum()), int(t.sum()), int(m.sum())
    return {"n_tv": nt, "n_replica": nm, "precision": tp / nm if nm else float(nt == 0),
            "recall": tp / nt if nt else float(nm == 0), "exact": nt == nm == tp}


def _line(tv: Any, us: np.ndarray, mask: np.ndarray) -> dict[str, Any]:
    t = np.asarray(tv, dtype=float)[mask]
    m = np.asarray(us, dtype=float)[mask]
    ok = ~np.isnan(t) & ~np.isnan(m)
    rel = np.abs(t[ok] - m[ok]) / np.maximum(np.abs(t[ok]), 1e-12)
    return {"nan_agree": float(np.mean(np.isnan(t) == np.isnan(m))), "max_rel": float(rel.max()) if len(rel) else 0.0,
            "exact": bool(np.all(np.isnan(t) == np.isnan(m)) and (rel <= 1e-6).all())}


def _state(tv: Any, us: np.ndarray, mask: np.ndarray) -> dict[str, Any]:
    t = np.nan_to_num(np.asarray(tv, dtype=float), nan=-1.0)[mask]
    m = np.asarray(us, dtype=float)[mask]
    return {"share_equal": float(np.mean(t == m)), "exact": bool((t == m).all())}


def match(d: pd.DataFrame, tf: str) -> dict[str, dict[str, Any]]:
    mask = (d["bar_time"] >= DAILY_FROM).to_numpy() if tf == "1D" else np.ones(len(d), dtype=bool)
    o, h, lo, c, v = (d[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume"))
    out: dict[str, dict[str, Any]] = {}
    sma_ema, ema = R.ma_ribbon(c, "sma_ema"), R.ma_ribbon(c, "ema")
    for col, key in (("a_p27", "ma10"), ("a_p29", "ma20"), ("a_p31", "ma50"), ("a_p33", "ma200")):
        out[f"A {key}"] = _line(d[col], sma_ema[key], mask)
    for col, key in (("b_p16", "ma5"), ("b_p8", "ma10"), ("b_p10", "ma20"), ("b_p12", "ma50"), ("b_p14", "ma200")):
        out[f"B {key}"] = _line(d[col], ema[key], mask)
    g50, g618 = R.range_retracement(h, lo, 50, (0.5, 0.618))
    out["A range 50%"], out["A range 61.8%"] = _line(d["a_p38"], g50, mask), _line(d["a_p39"], g618, mask)
    st = R.structure_breaks(o, h, lo, c)
    for i, key in enumerate(R.STRUCTURE_EVENTS):
        out[f"A {key}"] = _event(d[f"a_p{7 + i}"], st[key], mask)
    eqh, eql = R.equal_levels(h, lo, c)
    out["A eqh"], out["A eql"] = _event(d["a_p19"], eqh, mask), _event(d["a_p20"], eql, mask)
    fb, fs = R.fair_value_gaps(o, h, lo, c)
    out["A fvg_bull"], out["A fvg_bear"] = _event(d["a_p21"], fb, mask), _event(d["a_p22"], fs, mask)
    a_codes = {R.CANDLE_HIGH_BULL: 60, R.CANDLE_HIGH_BEAR: 61, R.CANDLE_LOW_VOL: 62, R.CANDLE_ENGULF: 63}
    b_codes = {R.CANDLE_HIGH_BULL: 3, R.CANDLE_HIGH_BEAR: 4, R.CANDLE_LOW_VOL: 5, R.CANDLE_ENGULF: 6, R.CANDLE_TRAVERSE: 7}
    cc, cct = R.candle_class(o, c, v), R.candle_class(o, c, v, traverse=True)
    out["A bar colour"] = _state(d["a_p37"], np.array([a_codes.get(int(x), -1) for x in cc]), mask)
    out["B bar colour"] = _state(d["b_p18"], np.array([b_codes.get(int(x), -1) for x in cct]), mask)
    out["B ema10 x ema20 up"] = _event(d["b_p4"], R.cross_over(ema["ma10"], ema["ma20"]), mask)
    out["B ema10 x ema20 down"] = _event(d["b_p6"], R.cross_under(ema["ma10"], ema["ma20"]), mask)
    if tf != "1D":                                   # B draws no channels on daily bars
        up, dn = R.sr_channel_breaks(o, h, lo, c)
        out["B sr_break_up"], out["B sr_break_dn"] = _event(d["b_p2"], up, mask), _event(d["b_p3"], dn, mask)
    m = R.money_flow(h, lo, c, v, 12)
    m = np.where(np.isfinite(m), m, np.nan)
    os_, ob_ = R.cross_under(m, np.full(len(m), 20.0)), R.cross_over(m, np.full(len(m), 80.0))
    out["C mfi_12"] = _line(d["c_p0"], m, mask)
    out["C oversold cross"], out["C overbought cross"] = _event(d["c_p4"], os_, mask), _event(d["c_p5"], ob_, mask)
    out["C either cross"] = _event(d["c_p6"], os_ | ob_, mask)
    out["C cross marker"] = _line(d["c_p2"], np.where(os_ | ob_, m, np.nan), mask)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default=str(Path.home() / "tmp" / "tv"))
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    root = Path(args.dir).expanduser()
    res = {tf: match(load(root, tf), tf) for tf in PULLS}
    for tf, rows in res.items():
        bad = {k: v for k, v in rows.items() if not v["exact"]}
        print(f"{tf}: {len(rows) - len(bad)}/{len(rows)} exact")
        for k, v in bad.items():
            print(f"   {k}: {v}")
    if args.json:
        Path(args.json).write_text(json.dumps(res, indent=1, default=float))


if __name__ == "__main__":
    main()
