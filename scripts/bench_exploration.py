"""Exploration pass latency (plan 2026-10-25, lane L2): p99 of `exploration.on_pass` <= 50 ms at 50 arms.

A paper demo probe_only engine with N arms (the built-in `donchian` and `roc` rules on 1m, 5m, 15m and 1h) is fed one
pass a second over synthetic market hours, as the run loop does: each tick joins `eng.ticks`, the completed minutes
join the bars every 10 s and at every 15m close (`Engine._rebuild_bars`), and the pass is timed. Orders go through
the real RiskGate and the paper broker, so entry passes are in the sample. Exits are the engine's own and not timed.

    uv run python scripts/bench_exploration.py [--arms 50] [--hours 6] [--p99-ms 50]
"""
from __future__ import annotations

import argparse
import statistics
import sys
import tempfile
import time

import numpy as np
import pandas as pd

from goldbot.data.resample import ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine import Engine, EngineConfig, exploration
from goldbot.engine.exploration import Arm, ExplorationConfig, Pot
from goldbot.execution.broker import AccountInfo, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.risk import RiskLimits
from goldbot.telegram.approvals import ApprovalCenter

TFS = ("1m", "5m", "15m", "1h")


class _Terminal(PaperBroker):
    def account(self) -> AccountInfo:
        return super().account().model_copy(update={"trade_mode": "demo"})


def _arms(n: int) -> list[Arm]:
    out = []
    for i in range(n):
        tf = TFS[i % len(TFS)]
        fam = "donchian" if i % 2 == 0 else "roc"
        style = "scalp" if tf in ("1m", "5m") else "intraday"
        out.append(Arm(arm_id=f"bench-{i:03d}", family=fam, params={"n": 5 + i % 20, "k": 0.5}, tf=tf,  # type: ignore[arg-type]
                       side_rule="follow" if i % 3 else "fade", stop=1.0, hold_bars=3, target=1.5, max_open=1,
                       daily_gbp=100.0, style=style))
    return out


def run(n_arms: int, hours: float) -> list[float]:
    arms = _arms(n_arms)
    cfg = ExplorationConfig(enabled=True, budget_share=0.9, max_open_total=18,
                            pots={"scalp": Pot(gbp=2_000, max_open=8), "intraday": Pot(gbp=2_000, max_open=10)},
                            arms=tuple(arms))
    pb = _Terminal(equity=10_000, adverse_slip_points=0.0)
    limits = RiskLimits(probe_only=True, exploration_arms={a.arm_id: 1_000.0 for a in arms}, max_positions=50,
                        daily_cap=0.5, max_risk_per_trade=0.2)
    eng = Engine(EngineConfig(account_id="bench", broker_name="icm", mode="demo", approval_mode="auto",
                              declared_class="raw", state_dir=tempfile.mkdtemp(), probe_only=True,
                              exploration_config=cfg), pb, [], {}, ApprovalCenter({1}), limits=limits)
    start = pd.Timestamp("2025-03-04 08:00", tz="UTC")
    hist = synthetic_ticks("2025-03-01", "2025-03-04 08:00", ticks_per_minute=1, seed=3)
    eng.bars_1m = ticks_to_1m(hist[hist["ts_utc"] < start])
    live = synthetic_ticks(start.isoformat(), (start + pd.Timedelta(hours=hours)).isoformat(), ticks_per_minute=60,
                           seed=4)
    eng._closed_through = start
    first = Tick(ts_utc=start, bid=float(live["bid"].iloc[0]), ask=float(live["ask"].iloc[0]))
    pb.on_tick(first)
    eng._refresh_account(first)
    times: list[float] = []
    for k, (ts, bid, ask) in enumerate(zip(live["ts_utc"], live["bid"], live["ask"])):
        t = Tick(ts_utc=ts, bid=float(bid), ask=float(ask))
        pb.on_tick(t)
        eng._last_tick = t
        eng.ticks.append(t)
        close = t.ts_utc.floor("15min")
        if close > eng._closed_through:
            eng._rebuild_bars(until=close)
            eng._closed_through = close
            eng._refresh_account(t)
        elif k % 10 == 0:
            eng._rebuild_bars()
        eng._retry_closes(t.ts_utc)              # the 1m/5m holds (exploration.exits), untimed
        if k % 30 == 0:                          # broker stops and targets leave the open table, untimed (the engine
            eng._sweep_closed({p.position_id for p in pb.positions()})   # sweeps at bar closes: see the hand-back)
        t0 = time.perf_counter()
        exploration.on_pass(eng, t)
        times.append((time.perf_counter() - t0) * 1000.0)
    sent = sum(1 for d in eng.decisions if str(d.get("action", "")).startswith("exploration:"))
    print(f"orders sent {sent}, positions open at the end {len(eng.open)}")
    return times


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arms", type=int, default=50)
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--p99-ms", type=float, default=50.0)
    a = ap.parse_args()
    times = run(a.arms, a.hours)
    p50, p99, worst = (float(np.percentile(times, q)) for q in (50, 99, 100))
    print(f"{a.arms} arms, {len(times)} passes: p50 {p50:.3f} ms, p99 {p99:.3f} ms, max {worst:.1f} ms, "
          f"mean {statistics.fmean(times):.3f} ms")
    return 0 if p99 <= a.p99_ms else 1


if __name__ == "__main__":
    sys.exit(main())
