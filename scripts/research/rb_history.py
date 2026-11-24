"""EVIDENCE-RB-HISTORY (docs/research/prereg-2026-11-24-rb-history.md): the red-box arms RB1H/RB4H and their random
ladders RBC1H/RBC4H on the W2 research windows, through the established history replay (`scripts/run_arm_backtest.py`
`run_arm`, the arm rules history and live share).

  count   outcome-blind signal counts on the learn window (the power check's inputs): candidates per arm, the
          one-at-a-time lower bound with the full hold blocking (no outcome read), distinct session days.
  run     the pre-registered read of ONE window (learn only in this card's first stage; confirm needs the quant's
          signature, test_old needs a confirm pass: `windows.allow_test_old_for`). Writes per-trade parquet + JSON.

Never reads the holdout (cut_at_holdout / arms guards). Bars come from read_bar_files directly: warm-up and exit tails
may include adjacent-window bars (test_old Sep-Dec 2009, confirm Jan 2018) for state only; no outcome from them is read. Market data stays local (--bars raw/)."""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from goldbot.data.resample import resample_bars  # noqa: E402
from goldbot.data.timeutil import utc_index  # noqa: E402
from goldbot.research import arm_rules as R  # noqa: E402
from goldbot.research import arms as A  # noqa: E402
from goldbot.research import windows as W  # noqa: E402

ARMS = ("RB1H", "RBC1H", "RB4H", "RBC4H")
PAIRS = {"1h": ("RB1H", "RBC1H"), "4h": ("RB4H", "RBC4H")}
RT_BP = 0.575            # W2 bps round trip ($0.23 at $4,000), cost of record scale
OVERSHOOT_BP = 1.75      # stop-exit overshoot in bp ($0.70 at $4,000)
WORKERS = 5


def _arm(arm_id: str) -> A.Arm:
    arms = A.load_arms(ROOT / "config" / "exploration_arms.yaml")
    a = next(x for x in arms if x.id == arm_id)
    # the rule's end_utc (2026-11-22) only bounds the live week; the windows below end before the holdout anyway
    return a


def _replayed_control() -> None:
    """History replays every control candidate (amendment 2026-11-15: "recorded skipped replay_only ... and replayed"):
    the REPLAY_ONLY skip only keeps the engine from sending it, so here it is cleared; NO_NEXT_BOX stays a skip for
    both the arm and its control (never sent by either)."""
    from goldbot.research.arm_rules_redbox import REPLAY_ONLY, redbox_control

    def replayed(arm: A.Arm, ctx: R.RuleContext) -> pd.DataFrame:
        out = redbox_control(arm, ctx)
        return out.assign(skip=out["skip"].where(out["skip"] != REPLAY_ONLY, None)) if len(out) else out
    R.RULE_FUNCS["redbox_control"] = replayed


def _bars(bars_dir: Path, window: str, year: int) -> tuple[pd.DataFrame, pd.Timestamp, pd.Timestamp]:
    """One calendar year of the window's entries, 120 days of warm-up (the channel state needs 313 bars: about 75
    calendar days at 4h) and 14 days of tail for the exits; nothing at or after the holdout."""
    from goldbot.data.release import read_bar_files
    lo, hi = W.bounds(window)
    lo, hi = max(lo, pd.Timestamp(f"{year}-01-01", tz="UTC")), min(hi, pd.Timestamp(f"{year + 1}-01-01", tz="UTC"))
    b = read_bar_files(bars_dir, year - 1, min(year + 1, W.HOLDOUT_FROM.year))
    b = A.cut_at_holdout(b)
    ts = utc_index(b["ts_utc"])
    b = b.loc[np.asarray((ts >= lo - pd.Timedelta(days=120)) & (ts < hi + pd.Timedelta(days=14)))].reset_index(drop=True)
    A.assert_no_holdout_rows(b)
    return b, lo, hi


def _count_one(args: tuple[str, str, str, int]) -> dict[str, Any]:
    arm_id, bars_dir, window, year = args
    _replayed_control()
    arm = _arm(arm_id)
    b1, lo, hi = _bars(Path(bars_dir), window, year)
    bars = resample_bars(b1, arm.timeframe).reset_index(drop=True)
    from run_arm_backtest import load_blackout
    blo, bhi = load_blackout(Path(bars_dir) / "calendar")
    c = R.candidates(arm, R.RuleContext(bars=bars, b1=b1, blackout_lo=blo, blackout_hi=bhi))
    close = utc_index(bars["visible_at"])
    if len(c):
        t = close[c["idx"].to_numpy()]
        c = c[np.asarray((t >= lo) & (t < hi))].reset_index(drop=True)
    idx = np.sort(c["idx"].to_numpy(int)) if len(c) else np.zeros(0, int)
    px = bars["close"].to_numpy(float) if "close" in bars.columns else (bars["bid_close"] + bars["ask_close"]).to_numpy(float) / 2
    risk_bp = (c["risk_usd"].to_numpy(float) / px[c["idx"].to_numpy(int)] * 1e4) if len(c) else np.zeros(0)
    tgt = c["target_r"].to_numpy(float) if len(c) else np.zeros(0)
    hold = int(arm.params["hold_bars"])
    kept, last = [], -10**9
    for i in idx:                      # full-hold blocking: outcome-blind lower bound on max_open 1 trades
        if i > last:
            kept.append(i)
            last = i + hold
    sig_days = close[idx].tz_convert("America/New_York").normalize().nunique() if len(idx) else 0
    days = close[np.asarray(kept, int)].tz_convert("America/New_York").normalize().nunique() if kept else 0
    return {"arm": arm_id, "year": year, "signals": int(len(idx)), "signal_days": int(sig_days), "lower_bound_trades": len(kept),
            "lower_bound_days": int(days),
            "risk_bp_p50": float(np.median(risk_bp)) if len(risk_bp) else None,
            "cost_r_mean": float(np.mean(RT_BP / risk_bp)) if len(risk_bp) else None,
            "target_r_p50": float(np.median(tgt)) if len(tgt) else None}


def count(bars_dir: str, window: str) -> list[dict[str, Any]]:
    yrs = W.years(window)
    jobs = [(a, bars_dir, window, y) for a in ARMS for y in yrs]
    with ProcessPoolExecutor(WORKERS) as ex:
        return list(ex.map(_count_one, jobs))


def _bps_net(trades: pd.DataFrame) -> np.ndarray:
    """Primary cost (card section 4): move (swap included) less RT_BP of the entry, less OVERSHOOT_BP on a stop exit."""
    entry = trades["entry"].to_numpy(float)
    stop = (trades["barrier_hit"] == "stop").to_numpy(float)
    pnl = trades["move_usd"].to_numpy(float) - RT_BP * 1e-4 * entry - OVERSHOOT_BP * 1e-4 * entry * stop
    return pnl / trades["risk_usd"].to_numpy(float)


def _run_one(args: tuple[str, str, str, int, bool]) -> pd.DataFrame:
    """One (arm, year, population) job; its trades are cached under ~/tmp/rb-history/parts so a rerun resumes."""
    arm_id, bars_dir, window, year, every = args
    part = Path.home() / "tmp" / "rb-history" / "parts" / f"{window}_{arm_id}_{year}_{'every' if every else 'one'}.parquet"
    if part.exists():
        return pd.read_parquet(part)
    out = _run_job(arm_id, bars_dir, window, year, every)
    part.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(part, index=False)
    print(f"done {part.name} n={len(out)}", flush=True)
    return out


def _run_job(arm_id: str, bars_dir: str, window: str, year: int, every: bool) -> pd.DataFrame:
    from run_arm_backtest import load_blackout, run_arm
    _replayed_control()
    arm = _arm(arm_id)
    if every:       # the card's primary population: every replayed candidate (amendment review item 6), no slot limit
        arm = arm.model_copy(update={"max_open": 1_000_000})
    b1, lo, hi = _bars(Path(bars_dir), window, year)
    blackout = load_blackout(Path(bars_dir) / "calendar")
    trades, _ = run_arm(b1, arm, lo, hi - pd.Timedelta(days=1), A.load_costs(ROOT / "config" / "arm_costs.yaml"),
                        blackout)
    if trades.empty:
        return trades.assign(arm=arm_id)
    trades = trades[np.asarray(utc_index(trades["entry_ts"]) < hi)]
    trades = trades[np.asarray(utc_index(trades["entry_ts"]) >= lo)].reset_index(drop=True)
    return trades.assign(arm=arm_id, net_bp=_bps_net(trades), year=year, population="every" if every else "one")


def run(bars_dir: str, window: str, out: Path) -> pd.DataFrame:
    if window not in ("learn",):
        raise SystemExit("stage 1 of the card reads the learn window only; confirm waits for the quant's signature, "
                         "test_old for a confirm pass (windows.allow_test_old_for)")
    jobs = [(a, bars_dir, window, y, e) for e in (True, False) for a in ARMS for y in W.years(window)]
    with ProcessPoolExecutor(WORKERS) as ex:
        frames = list(ex.map(_run_one, jobs))
    t = pd.concat([f for f in frames if len(f)], ignore_index=True)
    out.mkdir(parents=True, exist_ok=True)
    t.to_parquet(out / f"rb_history_{window}.parquet", index=False)
    return t


BLOCK_DAYS = {"1h": 5, "4h": 10}     # stationary bootstrap mean block (trading days), about 2.5 x the hold
DRAWS, SEED = 2000, 20261124
Z_SCREEN = 1.959964                  # power_first.holm_first_z(0.05, 2): Holm rank 1 over the two timeframes


def _boot_idx(n_days: int, mean_block: int, rng: np.random.Generator) -> np.ndarray:
    """One stationary-bootstrap (Politis-Romano) resample of day positions 0..n_days-1, circular."""
    out = np.empty(n_days, dtype=int)
    p = 1.0 / mean_block
    i = int(rng.integers(n_days))
    for k in range(n_days):
        out[k] = i
        i = int(rng.integers(n_days)) if rng.random() < p else (i + 1) % n_days
    return out


def _day(ts: pd.Series) -> np.ndarray:
    """The session day of an entry: the New York date after the 17:00 roll (the broker's server day)."""
    t = utc_index(ts).tz_convert("America/New_York") + pd.Timedelta(hours=7)
    return np.asarray(t.normalize().tz_localize(None).to_numpy().astype("datetime64[D]"))


def read(trades: pd.DataFrame, col: str = "net_bp") -> dict[str, Any]:
    """The card's section 6 statistics per timeframe: arm and control mean net R, delta, paired day-block bootstrap
    (arm and control resampled on the same session days), years, stress, one-at-a-time population (reported)."""
    out: dict[str, Any] = {}
    for tf, (a_id, c_id) in PAIRS.items():
        a, c = trades[trades["arm"] == a_id], trades[trades["arm"] == c_id]
        da, dc = _day(a["entry_ts"]), _day(c["entry_ts"])
        days = np.unique(np.concatenate([da, dc]))
        pos_a, pos_c = np.searchsorted(days, da), np.searchsorted(days, dc)
        sa, na = np.bincount(pos_a, a[col].to_numpy(float), len(days)), np.bincount(pos_a, None, len(days))
        sc, nc = np.bincount(pos_c, c[col].to_numpy(float), len(days)), np.bincount(pos_c, None, len(days))
        rng = np.random.default_rng(SEED + (0 if tf == "1h" else 1))
        ma, dd = [], []
        for _ in range(DRAWS):
            ix = _boot_idx(len(days), BLOCK_DAYS[tf], rng)
            x, y = sa[ix].sum() / max(na[ix].sum(), 1), sc[ix].sum() / max(nc[ix].sum(), 1)
            ma.append(x)
            dd.append(x - y)
        ma_, dd_ = np.array(ma), np.array(dd)
        m_a, m_c = float(a[col].mean()), float(c[col].mean())
        d = m_a - m_c
        se_a, se_d = float(ma_.std(ddof=1)), float(dd_.std(ddof=1))
        yrs = sorted(set(a["year"]) | set(c["year"]))
        ya = {int(y): float(a.loc[a["year"] == y, col].mean()) for y in yrs}
        yc = {int(y): float(c.loc[c["year"] == y, col].mean()) for y in yrs}
        stress = float(a["net_r_os"].mean())
        res = {
            "n_arm": len(a), "n_control": len(c), "days": len(days),
            "gross_arm": float(a["gross_r"].mean()), "gross_control": float(c["gross_r"].mean()),
            "net_arm": m_a, "net_control": m_c, "delta": d,
            "se_arm": se_a, "se_delta": se_d, "t_arm": m_a / se_a, "t_delta": d / se_d,
            "ci95_arm": [m_a - 1.96 * se_a, m_a + 1.96 * se_a], "ci95_delta": [d - 1.96 * se_d, d + 1.96 * se_d],
            "ub90_arm": m_a + 1.2816 * se_a, "ub90_delta": d + 1.2816 * se_d,
            "stress_net_arm": stress, "stress_net_control": float(c["net_r_os"].mean()),
            "years_arm_pos": sum(v > 0 for v in ya.values()), "years_delta_pos": sum(ya[y] - yc[y] > 0 for y in ya),
            "by_year_arm": ya, "by_year_control": yc,
            "stop_share_arm": float((a["barrier_hit"] == "stop").mean()),
            "target_share_arm": float((a["barrier_hit"] == "target").mean()),
            "risk_bp_p50_arm": float(np.median(a["risk_usd"] / a["entry"] * 1e4)),
        }
        yrs_a = sum(v > 0 for v in ya.values())
        yrs_d = sum(ya[y] - yc[y] > 0 for y in ya)
        cont = (d / se_d >= Z_SCREEN and m_a / se_a >= Z_SCREEN and stress > 0 and yrs_a >= 5 and yrs_d >= 5)
        stop = (se_d <= 0.06 and (d + 1.2816 * se_d < 0.05 or m_a + 1.2816 * se_a < 0))
        res["verdict"] = "CONTINUE" if cont else "STOP" if stop else "INCONCLUSIVE"
        out[tf] = res
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("count", "run", "read"))
    ap.add_argument("--bars", default=str(Path.home() / "myproject" / "goldbot" / "raw"))
    ap.add_argument("--window", default="learn")
    ap.add_argument("--out", default=str(Path.home() / "tmp" / "rb-history"))
    a = ap.parse_args()
    if a.mode == "count":
        rows = count(a.bars, a.window)
        df = pd.DataFrame(rows)
        print(df.to_string())
        print(df.groupby("arm")[["signals", "signal_days", "lower_bound_trades", "lower_bound_days"]].sum().to_string())
        print(df.groupby("arm")[["risk_bp_p50", "cost_r_mean", "target_r_p50"]].median().to_string())
        Path(a.out).mkdir(parents=True, exist_ok=True)
        df.to_csv(Path(a.out) / f"counts_{a.window}.csv", index=False)
    elif a.mode == "run":
        run(a.bars, a.window, Path(a.out))
    else:
        t = pd.read_parquet(Path(a.out) / f"rb_history_{a.window}.parquet")
        res = {pop: read(t[t["population"] == pop].reset_index(drop=True)) for pop in ("every", "one")}
        (Path(a.out) / f"read_{a.window}.json").write_text(json.dumps(res, indent=2, default=float))
        print(json.dumps(res, indent=2, default=float))


if __name__ == "__main__":
    main()
