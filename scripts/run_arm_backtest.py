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
from goldbot.data.resample import mid, resample_bars  # noqa: E402
from goldbot.data.timeutil import utc_index  # noqa: E402
from goldbot.labels import SwapSpec, rollover_nights  # noqa: E402
from goldbot.labels.triple_barrier import BarrierSpec, triple_barrier_next_open_1m  # noqa: E402
from goldbot.research import arm_rules as R  # noqa: E402
from goldbot.research import arm_rules_clock_box as K  # noqa: E402
from goldbot.research import arm_rules_weekcut as W  # noqa: E402
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


def runner_labels(lab: pd.DataFrame, bars: pd.DataFrame, b1: pd.DataFrame, cands: pd.DataFrame,
                  swap: SwapSpec) -> pd.DataFrame:
    """H-80 BXR in history: the RUNNER exit replaces the fixed bracket. `walk_runner` (the function the engine trails
    BXR's live stop with) on the 1m mid path from the entry minute, R = the candidate's 1.5 ATR, the time exit at the
    hold's last decision close; swap from the entry minute to the exit. On the same entry and path its virtual twins
    (prereg: "BXT/BXF are computed on each BXR fill's 1m path from the real fill price") exit at the STRUCT target
    (`struct_r`) and at FIXED 2 R with the 1 R stop unmoved: `twin_<mode>_*` columns, never trades of their own."""
    if lab.empty:
        return lab
    t1 = utc_index(b1["ts_utc"])
    m = mid(b1)[["ts_utc", "open", "high", "low", "close"]]
    close, bar_ts = utc_index(bars["visible_at"]), utc_index(bars["ts_utc"])
    c = cands.drop_duplicates("idx")
    risk = dict(zip(c["idx"].to_numpy(dtype=int).tolist(), c["risk_usd"].to_numpy(dtype=float).tolist(), strict=True))
    hold_of = dict(zip(c["idx"].to_numpy(dtype=int).tolist(), (c["max_bars"].to_numpy(dtype=int) + 1).tolist(),
                       strict=True))
    struct = dict(zip(c["idx"].to_numpy(dtype=int).tolist(), c["struct_r"].to_numpy(dtype=float).tolist(), strict=True))
    entry_ts = utc_index(lab["entry_ts"])
    k0s = t1.searchsorted(entry_ts, side="left")
    out = lab.copy()
    cols: dict[str, list[Any]] = {}

    def put(key: str, v: Any) -> None:
        cols.setdefault(key, []).append(v)

    for j, (i, side, entry) in enumerate(zip(lab["idx"].to_numpy(dtype=int).tolist(),
                                              lab["side"].to_numpy(dtype=int).tolist(),
                                              lab["entry"].to_numpy(dtype=float).tolist(), strict=True)):
        r, hold = risk[i], hold_of[i]
        closes = close[i + 1:i + 1 + hold]
        k1 = int(t1.searchsorted(closes[-1], side="left")) + 1 if len(closes) else len(t1)
        path = m.iloc[int(k0s[j]):k1].reset_index(drop=True)
        t0 = entry_ts[j:j + 1]

        def walk(target_r: float, path: pd.DataFrame = path, closes: pd.DatetimeIndex = closes, entry: float = entry,
                 side: int = side, r: float = r, t0: pd.DatetimeIndex = t0) -> tuple[str, float, float, pd.Timestamp]:
            w = K.walk_runner(path, closes, entry, side, r, r / K.BOX_STOP_ATR, target_r=target_r)
            at = pd.Timestamp(w["at"]) if w["at"] is not None else t0[0]
            nights = rollover_nights(t0, pd.DatetimeIndex([at]), swap.server_tz, swap.triple_weekday)[0]
            move = side * (float(w["exit"]) - entry) + nights * float(swap.usd_per_oz(np.array([side]))[0])
            return str(w["kind"]), float(w["exit"]), move, at

        kind, px, move, at = walk(K.NO_TARGET_R)
        t_exit = min(int(close.searchsorted(at, side="left")), i + hold, len(close) - 1)
        put("barrier_hit", kind)
        put("exit", px)
        put("ret", move / entry)
        put("t_exit", t_exit)
        put("ts_exit", bar_ts[t_exit])
        for mode, tgt in (("struct", struct[i]), ("fixed", K.BOX_TARGET_R)):
            tk, tpx, tmove, _ = walk(tgt)
            put(f"twin_{mode}_target_r", tgt)
            put(f"twin_{mode}_hit", tk)
            put(f"twin_{mode}_exit_px", tpx)
            put(f"twin_{mode}_move_usd", tmove)
    for key, v in cols.items():
        out[key] = v
    return out


def cut_labels(lab: pd.DataFrame, bars: pd.DataFrame, b1: pd.DataFrame, cands: pd.DataFrame,
               swap: SwapSpec) -> pd.DataFrame:
    """A rule under the engine's Friday weekend cut (`arm_rules.follows_friday_cut`: WFRI, arms 8.14 (2); PDC4 and
    DOND, 8.15) in history: "the read is the realised outcome under the cut". `walk_cut` (the engine's weekend rule on
    the 1m mid path: a loser closed at the cut, a winner's stop tightened to half its open profit) replaces the plain
    bracket, from the entry minute to the candidate's own (week-capped) time exit at the close of bar i + hold, R the
    candidate's stop, its target; swap from the entry minute to the exit."""
    if lab.empty:
        return lab
    m = mid(b1)[["ts_utc", "open", "high", "low", "close"]]
    t1 = utc_index(b1["ts_utc"])
    close, bar_ts = utc_index(bars["visible_at"]), utc_index(bars["ts_utc"])
    c = cands.drop_duplicates("idx")
    ids = c["idx"].to_numpy(dtype=int).tolist()
    hold = dict(zip(ids, c["max_bars"].to_numpy(dtype=int).tolist(), strict=True))
    risk = dict(zip(ids, c["risk_usd"].to_numpy(dtype=float).tolist(), strict=True))
    target = dict(zip(ids, c["target_r"].to_numpy(dtype=float).tolist(), strict=True))
    cut = dict(zip(ids, utc_index(c["cut_at"]), strict=True))
    entry_ts = utc_index(lab["entry_ts"])
    k0s = t1.searchsorted(entry_ts, side="left")
    cols: dict[str, list[Any]] = {k: [] for k in ("barrier_hit", "exit", "ret", "t_exit", "ts_exit")}
    for j, (i, side, entry) in enumerate(zip(lab["idx"].to_numpy(dtype=int).tolist(),
                                              lab["side"].to_numpy(dtype=int).tolist(),
                                              lab["entry"].to_numpy(dtype=float).tolist(), strict=True)):
        last = min(i + 1 + hold[i], len(close) - 1)
        k1 = int(t1.searchsorted(close[last], side="left"))
        path = m.iloc[int(k0s[j]):k1].reset_index(drop=True)
        w = W.walk_cut(path, entry, side, risk[i], cut[i], close[last], target[i])
        at = pd.Timestamp(w["at"]) if w["at"] is not None else entry_ts[j]
        nights = rollover_nights(entry_ts[j:j + 1], pd.DatetimeIndex([at]), swap.server_tz, swap.triple_weekday)[0]
        move = side * (float(w["exit"]) - entry) + nights * float(swap.usd_per_oz(np.array([side]))[0])
        t_exit = min(int(close.searchsorted(at, side="left")), last)
        for key, v in (("barrier_hit", str(w["kind"])), ("exit", float(w["exit"])), ("ret", move / entry),
                       ("t_exit", t_exit), ("ts_exit", bar_ts[t_exit])):
            cols[key].append(v)
    return lab.assign(**cols)


def signal_spread_extra(lab: pd.DataFrame, bars: pd.DataFrame, b1: pd.DataFrame, cands: pd.DataFrame,
                        costs: A.ArmCosts) -> np.ndarray:
    """KA15 stratum T (arms 8.13 condition 2): "T is charged at the spread measured at signal time on both legs: the
    23:00 UTC entry and the 23:30 UTC exit". The bid-ask spread at the entry minute's open and at the close of the
    minute ending at the time exit, less the p90 spread already inside the frozen round trip, never below 0 (the
    frozen round trip is the floor). 0 for every other row and every arm without strata."""
    extra = np.zeros(len(lab))
    if "stratum" not in cands.columns or lab.empty:
        return extra
    c = cands.drop_duplicates("idx")
    ids = c["idx"].to_numpy(dtype=int).tolist()
    stratum = dict(zip(ids, c["stratum"].astype(str).tolist(), strict=True))
    max_bars = dict(zip(ids, c["max_bars"].to_numpy(dtype=int).tolist(), strict=True))
    t1 = utc_index(b1["ts_utc"])
    close = utc_index(bars["visible_at"])
    k_ins = t1.searchsorted(utc_index(lab["entry_ts"]), side="left")
    for j, i in enumerate(lab["idx"].to_numpy(dtype=int).tolist()):
        if stratum[i] != "T":
            continue
        k_in = int(k_ins[j])
        exit_close = close[min(i + 1 + max_bars[i], len(close) - 1)]
        k_out = max(int(t1.searchsorted(exit_close, side="left")) - 1, k_in)
        s_in = float(b1["ask_open"].iloc[k_in] - b1["bid_open"].iloc[k_in])
        s_out = float(b1["ask_close"].iloc[k_out] - b1["bid_close"].iloc[k_out])
        extra[j] = max(0.0, s_in + s_out - costs.spread_both_legs_usd)
    return extra


def _stat(t: pd.DataFrame, col: str) -> dict[str, Any]:
    k = len(t)
    return {"mean": float(t[col].mean()) if k else None, "sum": float(t[col].sum()) if k else 0.0,
            "win_rate": float((t[col] > 0).mean()) if k else None}


def twin_summary(trades: pd.DataFrame, arm_id: str) -> dict[str, Any]:
    """H-80: the paired twins on BXR's own entries (prereg R1 reads BXR - BXF and BXT - BXF). Each twin is charged as
    a real fill: its move carries the swap of each rollover held and its net R the frozen round trip (spread both
    legs, slippage, commission) and the overshoot (`runner_labels`, `run_arm`). Quant review C4: a pair whose BXR did
    not reach its own exit (`open`: the data ended first) is censored and dropped, as is a twin that did not reach its
    own; `n_trades` counts the kept pairs."""
    out: dict[str, Any] = {}
    for mode, tid in K.twin_ids(arm_id).items():
        kept = trades[(trades["barrier_hit"] != "open") & (trades[f"twin_{mode}_hit"] != "open")]
        out[tid] = {"n_trades": len(kept), "n_dropped_censored": len(trades) - len(kept),
                    "gross_r": _stat(kept, f"twin_{mode}_gross_r"), "net_r_overshoot_0": _stat(kept, f"twin_{mode}_net_r"),
                    "net_r_overshoot_0.70": _stat(kept, f"twin_{mode}_net_r_os")}
    return out


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
        swap = SwapSpec.shipped()
        lab = triple_barrier_next_open_1m(mid_priced(bars), mid_priced(b1), sig, spec, risk, swap=swap)
        runner = K.is_runner(arm)
        if runner:
            lab = runner_labels(lab, bars, b1, cands, swap)
        elif R.follows_friday_cut(arm):
            lab = cut_labels(lab, bars, b1, cands, swap)
        lab = limit_open(lab, arm.max_open)
        if len(lab):
            idx = lab["idx"].to_numpy().astype(int)
            entry = lab["entry"].to_numpy(dtype=float)
            r_usd = risk.to_numpy()[idx]
            move = lab["ret"].to_numpy(dtype=float) * entry
            stop = (lab["barrier_hit"] == "stop").to_numpy()
            extra = signal_spread_extra(lab, bars, b1, cands, costs)
            os_lo, os_hi = min(costs.overshoot_scenarios_usd), max(costs.overshoot_scenarios_usd)
            trades = pd.DataFrame({
                "ts_utc": close[idx], "entry_ts": utc_index(lab["entry_ts"]), "ts_exit": utc_index(lab["ts_exit"]), "side": lab["side"].to_numpy(),
                "entry": entry, "risk_usd": r_usd, "move_usd": move, "gross_r": move / r_usd,
                "net_r": A.net_r(move, r_usd, stop, costs, os_lo, extra),
                "net_r_os": A.net_r(move, r_usd, stop, costs, os_hi, extra), "barrier_hit": lab["barrier_hit"].to_numpy(),
                "exit_px": lab["exit"].to_numpy(dtype=float)})
            if "stratum" in cands.columns:
                trades["stratum"] = cands.set_index("idx")["stratum"].reindex(idx).to_numpy()
                trades["spread_extra_usd"] = extra
            if runner:
                for mode in ("struct", "fixed"):
                    tmove = lab[f"twin_{mode}_move_usd"].to_numpy(dtype=float)
                    tstop = (lab[f"twin_{mode}_hit"] == "stop").to_numpy()
                    trades[f"twin_{mode}_target_r"] = lab[f"twin_{mode}_target_r"].to_numpy(dtype=float)
                    trades[f"twin_{mode}_hit"] = lab[f"twin_{mode}_hit"].to_numpy()
                    trades[f"twin_{mode}_exit_px"] = lab[f"twin_{mode}_exit_px"].to_numpy(dtype=float)
                    trades[f"twin_{mode}_gross_r"] = tmove / r_usd
                    trades[f"twin_{mode}_net_r"] = A.net_r(tmove, r_usd, tstop, costs, os_lo)
                    trades[f"twin_{mode}_net_r_os"] = A.net_r(tmove, r_usd, tstop, costs, os_hi)
    A.assert_no_holdout_rows(trades, "ts_exit")
    n = len(trades)

    def stat(col: str, rows: pd.DataFrame | None = None) -> dict[str, Any]:
        return _stat(trades if rows is None else rows, col)
    extra_summary: dict[str, Any] = {}
    if K.is_runner(arm) and n:
        extra_summary["twins"] = twin_summary(trades, arm.id)
    if "stratum" in trades.columns and n:
        # arms 8.13 condition 3: the family net without T and each stratum's share of fills (descriptive, no decision)
        extra_summary["strata"] = {str(k): float(v) for k, v in trades["stratum"].value_counts(normalize=True).items()}
        extra_summary["net_r_without_T"] = stat("net_r", trades[trades["stratum"] != "T"])
    summary = {"arm": arm.id, "config_hash": arm.config_hash, "block": f"{start.date()}:{end.date()}", "n_trades": n,
               **extra_summary,
               "n_candidates_in_data": n_cands, "gross_r": stat("gross_r"),
               "net_r_overshoot_0": stat("net_r"), "net_r_overshoot_0.70": stat("net_r_os"),
               "round_trip_usd": costs.round_trip_usd, "overshoot_scenarios_usd": list(costs.overshoot_scenarios_usd),
               "bars_from": str(utc_index(b1["ts_utc"]).min()), "bars_to": str(utc_index(b1["ts_utc"]).max()),
               "holdout_from": str(A.HOLDOUT_FROM.date()), "blackout_windows": int(len(lo)),
               "entry_note": "entry at the open (mid) of the first 1m bar at or after the decision close; barriers searched from that bar"}
    return trades, summary


def load_blackout(folder: Path) -> tuple[np.ndarray, np.ndarray]:
    """Tier-1 blackout windows from the calendar-v1 files in `folder` (scripts/fetch_data_release.py --us-calendar)."""
    cal = read_us_calendar_files(folder)
    if cal.empty:
        raise SystemExit(f"no calendar rows in {folder}: release calendar-v1 is missing or empty (run the data-us-calendar "
                         "workflow, which needs the FRED_API_KEY secret); the blackout cannot be applied")
    return R.blackout_windows(cal)


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
        blackout = load_blackout(Path(args.calendar))
    trades, summary = run_arm(b1, arm, start, end, A.load_costs(Path(args.costs)), blackout)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{arm.id}__{start.date()}_{end.date()}"
    trades.to_parquet(out / f"{stem}.parquet", index=False)
    (out / f"{stem}.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
