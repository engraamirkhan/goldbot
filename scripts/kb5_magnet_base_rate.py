"""H-49 stage 1 (docs/research/prereg-kb-2026-10-18.md section 2): are equal highs/lows "magnets"? Counts and base rates
only; no trade, return in dollars or P&L is computed.

For every first approach to an equal high/low (`eq_liquidity_run.approaches`: close 0.3-1.0 x ATR14 below an unhit equal
high, age 4-96 bars) and to a single confirmed pivot that is not an equal level (the control), measure
P(price trades through the level within 16 bars) and the 4-bar forward move after the cross, in ATR14 and signed toward
the level. Each equal-level approach is matched to the control approaches of the same cell (distance in 0.1 ATR bins,
hour of day, ATR14 tercile of the period) and the lift is the mean of (equal - matched control) over the matched
approaches, with a seeded 2,000-draw bootstrap 95% interval.

Reading rule (decided before any outcome): lift >= 5 percentage points with a 95% CI excluding 0 in BOTH 2010-2017 and
2018-2025-09 -> stage 2 (`eq_liquidity_run`) runs; otherwise retire "EQH/EQL as magnets" and stop (no trial).

  python scripts/fetch_data_release.py --download-only        # raw/xauusd_1m_dukascopy_<year>.parquet
  python scripts/kb5_magnet_base_rate.py --bars raw --out-json docs/research/kb5-stage1.json

Commit that JSON: stage 2 (`eq_liquidity_run`) dispatches only when it is committed and its stored lifts pass the
reading (`eq_liquidity_run.stage1_refusal`).

Bars are cut at `research.holdout_from` before resampling: the holdout is never loaded.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.config import load_settings  # noqa: E402
from goldbot.data.release import read_bar_files  # noqa: E402
from goldbot.data.resample import mid, resample_bars  # noqa: E402
from goldbot.features.technical import atr  # noqa: E402
from goldbot.research import h16  # noqa: E402
from goldbot.specialists import eq_liquidity_run as eq  # noqa: E402

HORIZON = 16                     # bars after the approach in which the level must trade through
FORWARD = 4                      # bars after the crossing bar for the forward move
MIN_LIFT = eq.STAGE1_MIN_LIFT    # the reading is eq.stage1_passes, shared with the stage-2 dispatch gate
PERIODS = (("2010-2017", pd.Timestamp("2010-01-01", tz="UTC"), pd.Timestamp("2018-01-01", tz="UTC")),
           ("2018-2025-09", pd.Timestamp("2018-01-01", tz="UTC"), pd.Timestamp("2026-01-01", tz="UTC")))
BOOTSTRAP, SEED = 2000, 20261018


def outcomes(m: pd.DataFrame, ap: pd.DataFrame) -> pd.DataFrame:
    """`ap` plus: cross (price traded through within HORIZON bars), fwd4 (move of the close FORWARD bars after the
    crossing bar, in ATR14 at the approach, positive toward the level's side), hour, atr. Approaches without HORIZON
    bars after them are dropped."""
    h, lo, c = (m[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    a = atr(m, 14).to_numpy(dtype=float)
    ts = pd.DatetimeIndex(pd.to_datetime(m["ts_utc"], utc=True))
    n = len(m)
    ap = ap[ap["idx"] + HORIZON < n].reset_index(drop=True)
    cross = np.zeros(len(ap), dtype=bool)
    fwd = np.full(len(ap), np.nan)
    for r, (t, side, level) in enumerate(zip(ap["idx"].to_numpy(), ap["side"].to_numpy(), ap["level"].to_numpy())):
        seg = slice(t + 1, t + HORIZON + 1)
        thru = (h[seg] > level) if side > 0 else (lo[seg] < level)
        if thru.any():
            cross[r] = True
            k = t + 1 + int(np.argmax(thru))
            if k + FORWARD < n:
                fwd[r] = side * (c[k + FORWARD] - c[k]) / a[t]
    out = ap.assign(cross=cross.astype(float), fwd4=fwd, hour=ts[ap["idx"].to_numpy()].hour.to_numpy(),
                    atr=a[ap["idx"].to_numpy()])
    out["time"] = ts[ap["idx"].to_numpy()]
    return out


def matched_lift(eqa: pd.DataFrame, ctl: pd.DataFrame, col: str, rng: np.random.Generator) -> dict[str, Any]:
    """Mean of (equal - same-cell control mean) over the equal approaches that have a control in their cell, with a
    bootstrap 95% interval. Each draw resamples both sides: the matched equal approaches, and the control approaches
    within each matched cell (so every cell keeps its size and the control mean's own sampling error is in the
    interval; quant review 2026-10-19: resampling the equal side alone made it about 10% too narrow). Cells: 0.1-ATR
    distance bin x hour x ATR14 tercile."""
    cuts = np.nanquantile(np.concatenate([eqa["atr"], ctl["atr"]]), [1 / 3, 2 / 3]) if len(eqa) + len(ctl) else [0, 0]

    def cell(df: pd.DataFrame) -> pd.Series:
        tercile = np.searchsorted(cuts, df["atr"].to_numpy(dtype=float))
        return pd.Series(list(zip((df["dist_atr"] / 0.1).astype(int), df["hour"], tercile)), index=df.index)
    c = ctl.assign(cell=cell(ctl)).dropna(subset=[col])
    ctl_mean = c.groupby("cell")[col].mean()
    e = eqa.assign(cell=cell(eqa)).dropna(subset=[col])
    ref = e["cell"].map(ctl_mean)
    ok = ref.notna().to_numpy()
    diff = (e[col] - ref).to_numpy(dtype=float)[ok]
    out: dict[str, Any] = {"matched": int(ok.sum()), "equal_mean": float(e[col].mean()) if len(e) else float("nan"),
                           "control_mean": float(ctl[col].mean()) if len(ctl) else float("nan")}
    if not len(diff):
        return {**out, "lift": float("nan"), "ci95": [float("nan"), float("nan")]}
    cells = pd.Index(e["cell"][ok].unique())
    code = cells.get_indexer(e["cell"][ok])
    vals = e[col].to_numpy(dtype=float)[ok]
    by_cell = c.groupby("cell")[col]
    ctl_boot = np.stack([rng.choice(by_cell.get_group(k).to_numpy(dtype=float), size=(BOOTSTRAP, by_cell.size()[k]),
                                    replace=True).mean(axis=1) for k in cells])           # (cells, draws)
    pick = rng.integers(0, len(vals), size=(BOOTSTRAP, len(vals)))
    boots = (vals[pick] - ctl_boot[code[pick], np.arange(BOOTSTRAP)[:, None]]).mean(axis=1)
    return {**out, "lift": float(diff.mean()), "ci95": [float(np.quantile(boots, 0.025)), float(np.quantile(boots, 0.975))]}


def diagnose(m: pd.DataFrame) -> dict[str, Any]:
    """Stage 1 on 15m mid bars (`ts_utc`, open/high/low/close): counts, base rates and the matched lift per period, and
    the pre-registered verdict."""
    m = m.reset_index(drop=True)
    eqh, eql = eq.smc.equal_levels(m["high"].to_numpy(dtype=float), m["low"].to_numpy(dtype=float),
                                   m["close"].to_numpy(dtype=float), length=eq.PIVOT_LEN)
    ph, pl = eq.confirmed_pivots(m)
    equal = outcomes(m, eq.approaches(m))
    control = outcomes(m, eq.approaches(m, (ph & ~eqh, pl & ~eql)))
    res: dict[str, Any] = {"equal_highs": int(eqh.sum()), "equal_lows": int(eql.sum()), "periods": {}}
    for name, lo_t, hi_t in PERIODS:
        e = equal[(equal["time"] >= lo_t) & (equal["time"] < hi_t)]
        k = control[(control["time"] >= lo_t) & (control["time"] < hi_t)]
        rng = np.random.default_rng(SEED)
        cross = matched_lift(e, k, "cross", rng)
        res["periods"][name] = {"equal_approaches": len(e), "control_approaches": len(k),
                                "cross_rate_equal": float(e["cross"].mean()) if len(e) else float("nan"),
                                "cross_rate_control": float(k["cross"].mean()) if len(k) else float("nan"),
                                "cross_lift": cross, "fwd4_lift": matched_lift(e, k, "fwd4", rng)}
    res["rule_ref"] = eq.STAGE1_RULE_REF
    res["stage2_runs"] = eq.stage1_passes(res)        # the same reading the stage-2 dispatch gate recomputes
    return res


def markdown(r: dict[str, Any]) -> str:
    lines = [f"Equal highs {r['equal_highs']:,}, equal lows {r['equal_lows']:,}. Counts and base rates only, no P&L.", "",
             "| Period | Equal | Control | P(cross) equal | P(cross) control | Lift | 95% CI | Matched |",
             "|---|---:|---:|---:|---:|---:|---|---:|"]
    for name, p in r["periods"].items():
        cl = p["cross_lift"]
        lines.append(f"| {name} | {p['equal_approaches']:,} | {p['control_approaches']:,} | {p['cross_rate_equal']:.3f} | "
                     f"{p['cross_rate_control']:.3f} | {cl['lift']:+.3f} | [{cl['ci95'][0]:+.3f}, {cl['ci95'][1]:+.3f}] | "
                     f"{cl['matched']:,} |")
    lines += ["", f"Pre-registered reading (lift >= {MIN_LIFT:.0%} with CI above 0 in both periods): "
              + ("PASS, stage 2 runs." if r["stage2_runs"] else "FAIL, retire EQH/EQL as magnets; no trial.")]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--bars", type=Path, required=True, help="folder of xauusd_1m_dukascopy_<year>.parquet")
    ap.add_argument("--out-json", type=Path, default=None)
    args = ap.parse_args()
    holdout_from = load_settings().research.holdout_from
    if holdout_from is None:
        raise SystemExit("research.holdout_from is not set: refusing to run without a holdout cut")
    b1 = h16.cut_before_holdout(read_bar_files(args.bars, 2010, pd.Timestamp(holdout_from).year), holdout_from)
    r = diagnose(mid(resample_bars(b1, "15m").reset_index(drop=True)))
    if args.out_json:
        args.out_json.write_text(json.dumps(r, indent=2))
    print(markdown(r))


if __name__ == "__main__":
    main()
