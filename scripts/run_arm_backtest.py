"""One exploration arm (ARM-SPEC 2026-10-25) on one block of the data-v1 1m bars: per-trade net R (parquet) and a
summary (JSON).

Usage: python scripts/run_arm_backtest.py --arm <id> --block <start>:<end> [--bars raw] [--calendar raw-calendar] [--out out]
       python scripts/run_arm_backtest.py --list-arms        (JSON array of runnable arm ids, for the workflow matrix)

Nothing at or after 2025-10-01 is read (goldbot.research.arms: files past 2025 are never opened, the bars are cut and the
cut is asserted). Rules are the live rule code (goldbot.specialists) or the ARM-SPEC's written rules
(goldbot.research.arm_rules). Barriers are searched on 1m bars (`triple_barrier_next_open_1m`: entry at the OPEN of the first 1m
bar after the decision close, stop first when both fall in one minute) on mid prices; R is the candidate's own stop distance; costs are the frozen cost
model (config/arm_costs.yaml: $0.23 round trip, plus swap per rollover) and net R is reported with a stop-exit overshoot
of $0 and of $0.70."""
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
from goldbot.data.release import read_bar_files, read_us_calendar_files  # noqa: E402
from goldbot.data.resample import resample_bars  # noqa: E402
from goldbot.data.timeutil import utc_index  # noqa: E402
from goldbot.labels import SwapSpec  # noqa: E402
from goldbot.labels.triple_barrier import BarrierSpec, triple_barrier_next_open_1m  # noqa: E402
from goldbot.research import arm_rules as R  # noqa: E402
from goldbot.research import arms as A  # noqa: E402

WARMUP_DAYS = 400          # feature history before the block (indicators, 1d tsmom lookbacks); entries stay in the block
TAIL_DAYS = 14             # bars after the block so a trade entered at its end can exit (still capped by the holdout)
ARMS_FILE = ROOT / "config" / "exploration_arms.yaml"
COSTS_FILE = ROOT / "config" / "arm_costs.yaml"


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


def mid_priced(bars: pd.DataFrame) -> pd.DataFrame:
    """Bars with bid = ask = mid: the frozen round trip already contains the spread, so it is never charged twice."""
    out = bars.copy()
    for k in ("open", "high", "low", "close"):
        out[f"bid_{k}"] = out[f"ask_{k}"] = (bars[f"bid_{k}"].astype(float) + bars[f"ask_{k}"].astype(float)) / 2
    return out


def limit_open(labels: pd.DataFrame, max_open: int) -> pd.DataFrame:
    """Keep a candidate only while fewer than `max_open` kept positions are still open (entry <= their exit bar)."""
    if labels.empty:
        return labels
    lab = labels.sort_values("t_entry", kind="stable")
    keep: list[int] = []
    exits: list[int] = []
    for i, (e, x) in enumerate(zip(lab["t_entry"].to_numpy(), lab["t_exit"].to_numpy(), strict=True)):
        exits = [t for t in exits if t >= e]
        if len(exits) < max_open:
            keep.append(i)
            exits.append(int(x))
    return lab.iloc[keep].reset_index(drop=True)


def run_arm(b1: pd.DataFrame, arm: A.Arm, start: pd.Timestamp, end: pd.Timestamp, costs: A.ArmCosts,
            blackout: tuple[np.ndarray, np.ndarray] | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    A.assert_no_holdout_rows(b1)
    b1 = b1.reset_index(drop=True)
    bars = b1 if arm.timeframe == "1m" else resample_bars(b1, arm.timeframe).reset_index(drop=True)
    lo, hi = blackout if blackout is not None else (np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64))
    cands = R.candidates(arm, R.RuleContext(bars=bars, b1=b1, blackout_lo=lo, blackout_hi=hi))
    n_cands = len(cands)
    close = utc_index(bars["visible_at"])
    if n_cands:
        t = close[cands["idx"].to_numpy()]
        cands = cands[np.asarray((t >= start) & (t < end + pd.Timedelta(days=1)))].reset_index(drop=True)
    cols = ["ts_utc", "entry_ts", "ts_exit", "side", "entry", "risk_usd", "move_usd", "gross_r", "net_r", "net_r_os", "barrier_hit"]
    trades = pd.DataFrame(columns=cols)
    if len(cands):
        risk_arr = np.full(len(bars), np.nan)
        risk_arr[cands["idx"].to_numpy()] = cands["risk_usd"].to_numpy()
        risk = pd.Series(risk_arr, index=bars.index)
        sig = cands.assign(target_atr=cands["target_r"])[["idx", "side", "max_bars", "target_atr"]]
        spec = BarrierSpec(target_atr=1.0, stop_atr=1.0, max_bars=1, name=arm.id)
        lab = triple_barrier_next_open_1m(mid_priced(bars), mid_priced(b1), sig, spec, risk, swap=SwapSpec.shipped())
        lab = limit_open(lab, arm.max_open)
        if len(lab):
            idx = lab["idx"].to_numpy().astype(int)
            entry = lab["entry"].to_numpy(dtype=float)
            r_usd = risk.to_numpy()[idx]
            move = lab["ret"].to_numpy(dtype=float) * entry
            stop = (lab["barrier_hit"] == "stop").to_numpy()
            os_hi = max(costs.overshoot_scenarios_usd)
            trades = pd.DataFrame({
                "ts_utc": close[idx], "entry_ts": utc_index(lab["entry_ts"]), "ts_exit": utc_index(lab["ts_exit"]), "side": lab["side"].to_numpy(),
                "entry": entry, "risk_usd": r_usd, "move_usd": move, "gross_r": move / r_usd,
                "net_r": A.net_r(move, r_usd, stop, costs, min(costs.overshoot_scenarios_usd)),
                "net_r_os": A.net_r(move, r_usd, stop, costs, os_hi), "barrier_hit": lab["barrier_hit"].to_numpy()})
    A.assert_no_holdout_rows(trades, "ts_exit")
    n = len(trades)

    def stat(col: str) -> dict[str, Any]:
        return {"mean": float(trades[col].mean()) if n else None, "sum": float(trades[col].sum()) if n else 0.0,
                "win_rate": float((trades[col] > 0).mean()) if n else None}
    summary = {"arm": arm.id, "config_hash": arm.config_hash, "block": f"{start.date()}:{end.date()}", "n_trades": n,
               "n_candidates_in_data": n_cands, "gross_r": stat("gross_r"),
               "net_r_overshoot_0": stat("net_r"), "net_r_overshoot_0.70": stat("net_r_os"),
               "round_trip_usd": costs.round_trip_usd, "overshoot_scenarios_usd": list(costs.overshoot_scenarios_usd),
               "bars_from": str(utc_index(b1["ts_utc"]).min()), "bars_to": str(utc_index(b1["ts_utc"]).max()),
               "holdout_from": str(A.HOLDOUT_FROM.date()), "blackout_windows": int(len(lo)),
               "entry_note": "entry at the open (mid) of the first 1m bar at or after the decision close; barriers searched from that bar"}
    return trades, summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm")
    ap.add_argument("--block")
    ap.add_argument("--list-arms", action="store_true")
    ap.add_argument("--arms-file", default=str(ARMS_FILE))
    ap.add_argument("--costs", default=str(COSTS_FILE))
    ap.add_argument("--bars", default="raw")
    ap.add_argument("--calendar", default="raw-calendar", help="the calendar-v1 files (tier-1 blackout)")
    ap.add_argument("--no-calendar", action="store_true", help="run without the blackout (recorded in the summary)")
    ap.add_argument("--out", default="arm_out")
    args = ap.parse_args()
    loaded = A.load_arms(Path(args.arms_file))
    if args.list_arms:
        print(json.dumps([a.id for a in loaded if a.status == "run"]))
        return
    if not args.arm or not args.block:
        ap.error("--arm and --block are required")
    arm = next((a for a in loaded if a.id == args.arm), None)
    if arm is None:
        raise SystemExit(f"unknown arm {args.arm!r}; known: {[a.id for a in loaded]}")
    if arm.status != "run":
        raise SystemExit(f"{arm.id} is blocked: {arm.note}")
    start, end = A.parse_block(args.block)
    b1 = load_block_bars(Path(args.bars), start, end)
    if b1.empty:
        raise SystemExit("no bars for the block")
    blackout = None
    if not args.no_calendar:
        cal = read_us_calendar_files(Path(args.calendar))
        if cal.empty:
            raise SystemExit(f"no calendar rows in {args.calendar}: the blackout cannot be applied (--no-calendar to run without)")
        blackout = R.blackout_windows(cal)
    trades, summary = run_arm(b1, arm, start, end, A.load_costs(Path(args.costs)), blackout)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{arm.id}__{start.date()}_{end.date()}"
    trades.to_parquet(out / f"{stem}.parquet", index=False)
    (out / f"{stem}.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
