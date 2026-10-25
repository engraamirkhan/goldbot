"""One exploration arm on one block of the data-v1 1m bars: per-trade net R (parquet) and a summary (JSON).

Usage: python scripts/run_arm_backtest.py --arm <id> --block <start>:<end> [--bars raw] [--out out]
       python scripts/run_arm_backtest.py --list-arms        (JSON array of arm ids, for the workflow matrix)

Nothing at or after 2025-10-01 is read (goldbot.research.arms: files past 2025 are never opened, the bars are cut and
the cut is asserted). Costs are priced per era (config/era_costs.yaml) on top of the spread the fills already pay and
the swap prior. Specialist arms run the live rule code (goldbot.specialists) through research.pipeline.prepare."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from goldbot.data.release import read_bar_files  # noqa: E402
from goldbot.data.resample import mid, resample_bars  # noqa: E402
from goldbot.data.timeutil import utc_index  # noqa: E402
from goldbot.features.technical import atr  # noqa: E402
from goldbot.labels import SwapSpec, one_at_a_time, triple_barrier  # noqa: E402
from goldbot.labels.triple_barrier import BarrierSpec  # noqa: E402
from goldbot.research import arms as A  # noqa: E402

WARMUP_DAYS = 120          # feature history before the block (never trades: entries are kept inside the block)
TAIL_DAYS = 14             # bars after the block so a trade entered at its end can exit (still capped by the holdout)


def load_block_bars(folder: Path, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """1m bars for the block plus warmup and tail, every row before the holdout (asserted)."""
    A.assert_block_allowed(start, end)
    last_year = min(int((end + pd.Timedelta(days=TAIL_DAYS)).year), A.LAST_DAY.year)
    b = read_bar_files(folder, int((start - pd.Timedelta(days=WARMUP_DAYS)).year), last_year)
    lo, hi = start - pd.Timedelta(days=WARMUP_DAYS), end + pd.Timedelta(days=TAIL_DAYS + 1)
    ts = utc_index(b["ts_utc"])
    b = b.loc[np.asarray((ts >= lo) & (ts < hi))].reset_index(drop=True)
    b = A.cut_at_holdout(b)
    A.assert_no_holdout_rows(b)
    return b


def _labels_random(arm: A.Arm, bars: pd.DataFrame, swap: SwapSpec) -> tuple[pd.DataFrame, pd.Series, float]:
    a = atr(mid(bars).reset_index(drop=True), 14)
    spec = BarrierSpec(target_atr=arm.target_atr, stop_atr=arm.stop_atr, max_bars=arm.max_bars, name=arm.id)
    lab = one_at_a_time(triple_barrier(bars, A.random_candidates(arm, len(bars)), spec, a, swap=swap))
    return lab, a, arm.stop_atr


def _labels_specialist(arm: A.Arm, bars: pd.DataFrame, b1: pd.DataFrame, swap: SwapSpec) -> tuple[pd.DataFrame, pd.Series, float]:
    from goldbot.research.pipeline import prepare
    from goldbot.specialists import SPECIALISTS
    spec = SPECIALISTS[str(arm.family)](timeframe=arm.timeframe, **arm.config)
    ctx_tfs = ("1h", "4h", "1d")
    context = {({"1h": "h1", "4h": "h4", "1d": "d1"})[tf]: resample_bars(b1, tf) for tf in ctx_tfs}
    prep = prepare(spec, bars, context, swap=swap, bars_1m=b1 if spec.needs_bars_1m else None)
    lab = prep.labels
    a = atr(mid(bars).reset_index(drop=True), 14)
    return lab, a, float(spec.label_spec.stop_atr)


def run_arm(b1: pd.DataFrame, arm: A.Arm, start: pd.Timestamp, end: pd.Timestamp,
            costs: A.EraCosts) -> tuple[pd.DataFrame, dict[str, Any]]:
    A.assert_no_holdout_rows(b1)
    bars = b1.reset_index(drop=True) if arm.timeframe == "1m" else resample_bars(b1, arm.timeframe).reset_index(drop=True)
    swap = SwapSpec.shipped()
    if arm.kind == "random":
        lab, a, stop_atr = _labels_random(arm, bars, swap)
    else:
        lab, a, stop_atr = _labels_specialist(arm, bars, b1, swap)
    cols = ["ts_utc", "ts_exit", "side", "entry", "net_r", "gross_r", "extra_cost_usd", "barrier_hit"]
    if lab.empty:
        trades = pd.DataFrame(columns=cols)
    else:
        lab = lab.reset_index(drop=True)
        idx = lab["idx"].to_numpy().astype(int)
        entry = lab["entry"].to_numpy(dtype=float)
        risk = stop_atr * a.to_numpy()[idx] / entry
        entry_ts = utc_index(lab["ts_utc"])
        extra = A.extra_cost_usd(entry_ts, bars["spread_mean"].to_numpy()[idx], costs)
        net = (lab["ret"].to_numpy(dtype=float) - extra / entry) / risk
        trades = pd.DataFrame({"ts_utc": entry_ts, "ts_exit": utc_index(lab["ts_exit"]), "side": lab["side"].to_numpy(),
                               "entry": entry, "net_r": net, "gross_r": lab["ret"].to_numpy(dtype=float) / risk,
                               "extra_cost_usd": extra, "barrier_hit": lab["barrier_hit"].to_numpy()})
        keep = np.asarray((entry_ts >= start) & (entry_ts < end + pd.Timedelta(days=1)))
        trades = trades.loc[keep].reset_index(drop=True)
    A.assert_no_holdout_rows(trades, "ts_exit")
    n = len(trades)
    summary = {"arm": arm.id, "block": f"{start.date()}:{end.date()}", "n_trades": n,
               "mean_net_r": float(trades["net_r"].mean()) if n else None,
               "sum_net_r": float(trades["net_r"].sum()) if n else 0.0,
               "win_rate": float((trades["net_r"] > 0).mean()) if n else None,
               "mean_extra_cost_usd": float(trades["extra_cost_usd"].mean()) if n else None,
               "bars_from": str(utc_index(b1["ts_utc"]).min()), "bars_to": str(utc_index(b1["ts_utc"]).max()),
               "holdout_from": str(A.HOLDOUT_FROM.date())}
    return trades, summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm")
    ap.add_argument("--block")
    ap.add_argument("--list-arms", action="store_true")
    ap.add_argument("--arms-file", default=str(ROOT / "config" / "exploration_arms.yaml"))
    ap.add_argument("--era-costs", default=str(ROOT / "config" / "era_costs.yaml"))
    ap.add_argument("--bars", default="raw")
    ap.add_argument("--out", default="arm_out")
    args = ap.parse_args()
    loaded = A.load_arms(Path(args.arms_file))
    if args.list_arms:
        print(json.dumps([a.id for a in loaded]))
        return
    if not args.arm or not args.block:
        ap.error("--arm and --block are required")
    arm = next((a for a in loaded if a.id == args.arm), None)
    if arm is None:
        raise SystemExit(f"unknown arm {args.arm!r}; known: {[a.id for a in loaded]}")
    start, end = A.parse_block(args.block)
    b1 = load_block_bars(Path(args.bars), start, end)
    if b1.empty:
        raise SystemExit("no bars for the block")
    trades, summary = run_arm(b1, arm, start, end, A.load_era_costs(Path(args.era_costs)))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{arm.id}__{start.date()}_{end.date()}"
    trades.to_parquet(out / f"{stem}.parquet", index=False)
    (out / f"{stem}.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
