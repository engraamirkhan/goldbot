"""CPU and RAM profile of the exploration arms at a 15m close, per host (arms expansion 2026-10-26, lane L7).

What it times, per sampled 15m close, exactly as `goldbot.engine.exploration.step` runs it: for every timeframe of the
host's 15m+ arms whose bar closes now, the engine's own bar slice (`exploration._bars`, LOOKBACK bars of the arm's
timeframe, 1d from the whole window) and every arm's rule on it. Existing arms run `arm_rules.candidates` from the frozen
spec; the wave-1 specialist arms run the specialist path `arm_rules.tsmom_rule` uses (context h1/h4/d1 from the 1m
bars, `build_decision_frame`, `candidates_in_context`, `barrier_atr`), which the L1 adapter copies. The input is the
engine's in-memory window: the last `--bars-in-memory` 1m bars (runner `max_bars_in_memory`, about 40 trading days).

A rule that raises is recorded under `errors` and timed as it failed (smc_structure refuses any series that does not
start at its pinned replica start: it needs the engine's ReplicaShadow state, not this plain path). RAM: tracemalloc's
transient peak of one close and the resident growth after the closes, per host.

`--window full` also gives every rule the whole window (what a longer warm-up slice would cost): a bound for L1-L5.

Hosts run in separate engine processes; each host's figure is its own single-threaded seconds per close. Scale to the
brain with `--cpu-factor` (this machine's single-thread speed over the brain's) and compare with the 10 s budget.

Usage: uv run scripts/profile_arm_rules.py --bars ~/tmp/ts/raw [--closes 96] [--window engine|full|both]
       [--hosts icm-demo,vantage-demo] [--cpu-factor 3.0] [--json out.json]
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import resource
import subprocess
import sys
import time
import tracemalloc
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from goldbot.config import tf_seconds  # noqa: E402
from goldbot.data.resample import resample_bars  # noqa: E402
from goldbot.engine import exploration as X  # noqa: E402
from goldbot.research import arm_rules as R  # noqa: E402
from goldbot.research.arms import Arm, load_arms  # noqa: E402

BUDGET_S = 10.0                      # per host per 15m close (plan 2026-10-26 section 5)
BARS_IN_MEMORY = 60_000              # engine.runner EngineConfig.max_bars_in_memory (~40 trading days of 1m)
ARMS_FILE = ROOT / "config" / "exploration_arms.yaml"
ACCOUNTS_FILE = ROOT / "config" / "accounts.yaml"

# Wave 1 (plan section 3): label -> (host, specialist family, timeframe, config). The ids come with L6; labels here.
WAVE1: dict[str, tuple[str, str, str, dict[str, Any]]] = {
    "box_break@15m": ("icm-demo", "box_break", "15m", {"rung": "L0"}),
    "smc_tvr1@15m": ("icm-demo", "smc_structure", "15m", {"rule": "tvr1"}),
    "eq_liquidity_run@15m": ("icm-demo", "eq_liquidity_run", "15m", {}),
    "session_open@15m": ("icm-demo", "session_open", "15m", {}),
    "intraday_momentum_ny@15m": ("icm-demo", "intraday_momentum", "15m", {"session": "newyork"}),
    "intraday_momentum_ldn@15m": ("icm-demo", "intraday_momentum", "15m", {"session": "london"}),
    "asia_drift@1h": ("icm-demo", "asia_drift", "1h", {}),
    "mean_reversion@15m": ("icm-demo", "mean_reversion", "15m", {}),
    "mean_reversion@1h": ("icm-demo", "mean_reversion", "1h", {}),
    "box_rip@1h": ("vantage-demo", "box_rip", "1h", {"rung": "L0"}),
    "box_retest@1h": ("vantage-demo", "box_retest", "1h", {"rung": "L0"}),
    "fakey@1h": ("vantage-demo", "fakey", "1h", {"rung": "L0"}),
    "trend_level_signal@1h": ("vantage-demo", "trend_level_signal", "1h", {"rung": "L0"}),
    "smc_tvr2@15m": ("vantage-demo", "smc_structure", "15m", {"rule": "tvr2"}),
    "breakout@1h": ("vantage-demo", "breakout", "1h", {}),
    "breakout@15m": ("vantage-demo", "breakout", "15m", {}),
    "trend@1h": ("vantage-demo", "trend", "1h", {}),
}
# the 1h random controls (C1H on icm-demo, VC1H on vantage-demo): I0's geometry on 1h closes (plan section 3)
CONTROL_1H = {"minutes": [0], "window_utc": ["07:00", "19:00"], "stop_atr": 1.5, "target_r": 2.0, "hold_bars": 48}


@dataclass
class Case:
    label: str
    tf: str
    run: Callable[[R.RuleContext], pd.DataFrame]
    wave1: bool = False


@dataclass
class HostResult:
    host: str
    arms: int
    wave1: int
    per_close_s: list[float] = field(default_factory=list)
    close_ts: list[pd.Timestamp] = field(default_factory=list)
    per_arm_s: dict[str, list[float]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def summary(self, cpu_factor: float) -> dict[str, Any]:
        pc = np.asarray(self.per_close_s or [0.0])
        worst = float(pc.max()) * cpu_factor
        return {"host": self.host, "arms": self.arms, "wave1_arms": self.wave1, "closes": len(self.per_close_s),
                "median_s": round(float(np.median(pc)), 3), "p95_s": round(float(np.percentile(pc, 95)), 3),
                "max_s": round(float(pc.max()), 3),
                "worst_close": str(self.close_ts[int(pc.argmax())]) if self.close_ts else None, "max_s_brain": round(worst, 3), "budget_s": BUDGET_S,
                "within_budget": worst <= BUDGET_S,
                "slowest_arms_max_s": dict(sorted(((k, round(max(v), 3)) for k, v in self.per_arm_s.items()),
                                                  key=lambda kv: -kv[1])[:6]),
                "errors": self.errors}


def specialist_rule(family: str, tf: str, config: dict[str, Any]) -> Callable[[R.RuleContext], pd.DataFrame]:
    """The specialist path of `arm_rules.tsmom_rule` for any family: its own candidates and its barrier stop."""
    from goldbot.features.technical import atr
    from goldbot.research.pipeline import build_decision_frame
    from goldbot.specialists import SPECIALISTS

    spec = SPECIALISTS[family](timeframe=tf, **config)

    def run(ctx: R.RuleContext) -> pd.DataFrame:
        context = {lab: resample_bars(ctx.b1, t) for lab, t in (("h1", "1h"), ("h4", "4h"), ("d1", "1d"))}
        m, x = build_decision_frame(ctx.bars.reset_index(drop=True), context)
        cands = spec.candidates_in_context(m, x, context, None)
        if cands.empty:
            return R.empty()
        own = spec.barrier_atr(m, context)
        a = (atr(m, 14) if own is None else own).to_numpy(dtype=float)
        ls = spec.label_spec
        idx = cands["idx"].to_numpy(dtype=int)
        return R._frame(idx, cands["side"].to_numpy(), ls.stop_atr * a[idx], ls.target_atr / ls.stop_atr,
                        ls.max_bars + 1)
    return run


def frozen_rule(arm: Arm) -> Callable[[R.RuleContext], pd.DataFrame]:
    def run(ctx: R.RuleContext) -> pd.DataFrame:
        return R.candidates(arm, ctx)
    return run


def host_arm_ids(accounts: dict[str, Any], host: str) -> list[str]:
    acc = (accounts.get("accounts") or {}).get(host) or {}
    return list(((acc.get("exploration") or {}).get("arms") or {}).keys())


def host_cases(host: str, *, arms_file: Path = ARMS_FILE, accounts_file: Path = ACCOUNTS_FILE,
               wave1: bool = True) -> list[Case]:
    """The host's 15m+ arms the engine evaluates at a 15m close (sub-15m arms run on their own minute path), plus
    its wave-1 additions."""
    spec = {a.id: a for a in load_arms(arms_file)}
    accounts = yaml.safe_load(accounts_file.read_text()) or {}
    out: list[Case] = []
    for aid in host_arm_ids(accounts, host):
        arm = spec.get(aid)
        if arm is None or arm.status != "run" or arm.timeframe in X.SUB_15M or arm.rule not in R.RULE_FUNCS:
            continue
        out.append(Case(aid, arm.timeframe, frozen_rule(arm)))
    if wave1:
        for label, (h, fam, tf, cfg) in WAVE1.items():
            if h == host:
                out.append(Case(label, tf, specialist_rule(fam, tf, cfg), wave1=True))
        ctl = Arm(id=("C1H" if host == "icm-demo" else "VC1H"), rule="random_bracket", timeframe="1h",
                  params=CONTROL_1H)
        out.append(Case(ctl.id, "1h", frozen_rule(ctl), wave1=True))
    return out


def peak_rss_mb() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / 2**20 if sys.platform == "darwin" else r / 2**10      # bytes on macOS, KiB on Linux


def rss_mb() -> float:
    """The process's resident set now (MB)."""
    status = Path("/proc/self/status")
    if status.exists():
        for line in status.read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True, check=True)
    return int(out.stdout.strip()) / 1024


def memory_pass(b1: pd.DataFrame, cases: list[Case], closes: list[pd.Timestamp]) -> dict[str, float]:
    """RAM per close: the transient peak of the allocations one close makes (tracemalloc, numpy included) and what
    stays resident after the closes (RSS growth: caches the long-running engine would keep)."""
    gc.collect()
    rss_a = rss_mb()
    tracemalloc.start()
    peak = 0.0
    for close in closes:
        tracemalloc.reset_peak()
        base = tracemalloc.get_traced_memory()[0]
        profile(b1, cases, [close], full=False, host="mem")
        peak = max(peak, (tracemalloc.get_traced_memory()[1] - base) / 2**20)
    tracemalloc.stop()
    gc.collect()
    return {"transient_peak_mb_per_close": round(peak, 1), "retained_rss_mb": round(rss_mb() - rss_a, 1)}


def load_window(src: Path, n: int) -> pd.DataFrame:
    files = sorted(src.glob("xauusd_1m_*.parquet")) if src.is_dir() else [src]
    if not files:
        raise SystemExit(f"no xauusd_1m_*.parquet under {src}")
    parts: list[pd.DataFrame] = []
    for f in reversed(files):
        parts.insert(0, pd.read_parquet(f))
        if sum(len(p) for p in parts) >= n:
            break
    b = pd.concat(parts, ignore_index=True).sort_values("ts_utc")
    return b.tail(n).reset_index(drop=True)


def sample_closes(b1: pd.DataFrame, n: int) -> list[pd.Timestamp]:
    """The last `n` 15m closes in the window that have a 1m bar closing exactly then (the engine's `_closed_through`)."""
    vis = pd.DatetimeIndex(b1["visible_at"]).as_unit("ns")
    on15 = vis[(vis.minute % 15 == 0) & (vis.second == 0)]
    return [pd.Timestamp(t) for t in on15[-n:]]


def profile(b1: pd.DataFrame, cases: list[Case], closes: list[pd.Timestamp], *, full: bool,
            host: str) -> HostResult:
    """One host: per close, the engine's `step` loop (bar slices per timeframe + every arm's rule), timed."""
    res = HostResult(host=host, arms=len(cases), wave1=sum(c.wave1 for c in cases))
    zero = np.zeros(0, dtype=np.int64)
    for close in closes:
        t0 = time.perf_counter()
        complete = b1[b1["visible_at"] <= close]
        for tf in sorted({c.tf for c in cases}, key=tf_seconds):
            if tf != "1d" and int(close.timestamp()) % tf_seconds(tf):
                continue
            bars: pd.DataFrame | None
            sub: pd.DataFrame | None
            if full:
                bars = resample_bars(complete, tf) if tf != "1m" else complete
                bars = bars[bars["visible_at"] <= close].reset_index(drop=True)
                if bars.empty or pd.Timestamp(bars["visible_at"].iloc[-1]) != close:
                    bars = None
                sub = complete.reset_index(drop=True)
            else:
                bars = X._bars(complete, tf, close)
                sub = None if bars is None else complete[complete["ts_utc"] >= pd.Timestamp(
                    bars["ts_utc"].iloc[0])].reset_index(drop=True)
            if bars is None or sub is None:
                continue
            ctx = R.RuleContext(bars=bars, b1=sub, blackout_lo=zero, blackout_hi=zero)
            for c in (c for c in cases if c.tf == tf):
                a0 = time.perf_counter()
                try:
                    c.run(ctx)
                except Exception as exc:          # the engine journals and goes on; so does the profile
                    res.errors[c.label] = f"{type(exc).__name__}: {exc}"[:200]
                res.per_arm_s.setdefault(c.label, []).append(time.perf_counter() - a0)
        res.per_close_s.append(time.perf_counter() - t0)
        res.close_ts.append(close)
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bars", type=Path, required=True, help="a 1m parquet or a folder of xauusd_1m_*.parquet")
    ap.add_argument("--bars-in-memory", type=int, default=BARS_IN_MEMORY)
    ap.add_argument("--closes", type=int, default=96, help="last N 15m closes of the window (96 = one day)")
    ap.add_argument("--hosts", default="icm-demo,vantage-demo")
    ap.add_argument("--window", choices=("engine", "full", "both"), default="both")
    ap.add_argument("--cpu-factor", type=float, default=1.0, help="brain seconds per local second (single thread)")
    ap.add_argument("--no-wave1", action="store_true", help="only the arms in the config today")
    ap.add_argument("--json", type=Path)
    a = ap.parse_args(argv)

    b1 = load_window(a.bars.expanduser(), a.bars_in_memory)
    closes = sample_closes(b1, a.closes)
    gc.collect()
    out: dict[str, Any] = {"window": [str(b1["ts_utc"].iloc[0]), str(b1["ts_utc"].iloc[-1])], "bars_1m": len(b1),
                           "closes": len(closes), "cpu_factor": a.cpu_factor, "rss_mb_after_load": round(rss_mb(), 1),
                           "runs": []}
    modes = ("engine", "full") if a.window == "both" else (a.window,)
    for host in a.hosts.split(","):
        cases = host_cases(host, wave1=not a.no_wave1)
        warm = profile(b1, cases, closes[-4:], full=False, host=host)   # imports, caches: the engine is long-running
        for mode in modes:
            r = profile(b1, cases, closes, full=(mode == "full"), host=host)
            s = {"mode": mode, **r.summary(a.cpu_factor), "cold_first_close_s": round(warm.per_close_s[0], 3)}
            out["runs"].append(s)
            print(json.dumps(s), flush=True)
        hours = [t for t in closes if t.minute == 0][-3:]
        mem = {"host": host, **memory_pass(b1, cases, hours)}
        out.setdefault("memory", []).append(mem)
        print(json.dumps(mem), flush=True)
    out["peak_rss_mb"] = round(peak_rss_mb(), 1)
    out["rss_mb_end"] = round(rss_mb(), 1)
    out["go"] = all(r["within_budget"] for r in out["runs"] if r["mode"] == "engine")
    print(json.dumps({k: v for k, v in out.items() if k != "runs"}))
    if a.json:
        a.json.write_text(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
