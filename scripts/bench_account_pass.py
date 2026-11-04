"""Multi-book pass latency (live 2026-11-04, icm-probe: "pass budget 5.0 s spent after 8|16|19 of 20 books").

A paper demo engine with icm-probe's books (gold + its 19 instruments, `config/accounts.yaml`) and its registered
exploration arms (`exploration.config_for`, each book's own `book_config`) is fed one `books.account_pass` a second
over synthetic market hours, every book on its own synthetic quotes with `--days` of 1m history (the engine holds
about 62 trading days). Each pass is timed on the host clock. Every bridge call is counted, and `--latency-ms` makes
each take that long (a remote MT5 bridge's round trip, which the paper broker does not have). `--profile` writes
cProfile stats of every pass to ~/tmp/bench_account_pass.prof and prints the top cumulative costs. Nothing leaves the
process (paper broker, temp state dir). The budget is the account pass's, `books.PASS_BUDGET_S`.

    uv run python scripts/bench_account_pass.py [--minutes 20] [--days 20] [--latency-ms 0] [--profile [--profile-after 16]]
"""
from __future__ import annotations

import argparse
import cProfile
import pstats
import statistics
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from goldbot.data.resample import ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine import Engine, EngineConfig, books, exploration
from goldbot.engine.books import BookSpec, using
from goldbot.execution.broker import AccountInfo, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.instruments import load_instruments
from goldbot.ops.accounts import load_accounts
from goldbot.ops.services.engine import engine_pass

ACCOUNT = "icm-probe"
START = pd.Timestamp("2025-03-05 10:00:30", tz="UTC")     # a Wednesday, inside every book's session
BRIDGE_CALLS = ("symbol_info", "account", "get_bars", "last_tick", "margin_required", "loss_per_lot",
                "trading_sessions", "place_order", "modify", "close", "positions", "ticks_many", "account_positions",
                "deals_since")


class _Terminal(PaperBroker):
    """The paper terminal, demo trade mode, every symbol quoted by `set_quote` (the engine's tick hook does nothing)."""

    latency_s = 0.0

    def __init__(self, *a: Any, **k: Any) -> None:
        super().__init__(*a, **k)
        self.calls: Counter[str] = Counter()

    def on_tick(self, tick: Tick) -> None:
        return None

    def ticks_many(self, symbols: Any, since: pd.Timestamp) -> dict[str, list[Tick]]:
        """Every symbol's current quote: the feed's `since` is the host clock, years after the synthetic quotes."""
        return {s: [t] for s in symbols if (t := self._ticks.get(s)) is not None}


def _counted(name: str) -> Any:
    inner = _Terminal.__dict__.get(name) or getattr(PaperBroker, name)

    def call(self: _Terminal, *a: Any, **k: Any) -> Any:
        self.calls[name] += 1
        if self.latency_s:
            time.sleep(self.latency_s)
        got = inner(self, *a, **k)
        return got.model_copy(update={"trade_mode": "demo"}) if isinstance(got, AccountInfo) else got
    return call


for _name in BRIDGE_CALLS:
    setattr(_Terminal, _name, _counted(_name))


def _specs() -> tuple[tuple[BookSpec, ...], Any]:
    acc = load_accounts(logins=False)[ACCOUNT]
    cfg = exploration.config_for(acc)
    reg = load_instruments()
    out = []
    for key in acc.instruments:
        spec = reg.get(key)
        out.append(BookSpec(key=key, symbol=key, store_symbol=spec.store_symbol, magic_block=spec.magic_block,
                            digits=2, point=0.01, contract_size=spec.contract_size,
                            exploration_config=exploration.book_config(cfg, key), instrument=spec))
    return tuple(out), exploration.book_config(cfg, "XAUUSD")


def build(days: int) -> tuple[Engine, _Terminal, dict[str, pd.DataFrame]]:
    """The engine, its paper terminal and each symbol's live ticks (1 a second from START)."""
    specs, gold_cfg = _specs()
    pb = _Terminal(equity=10_000, adverse_slip_points=0.0)
    eng = Engine(EngineConfig(account_id=ACCOUNT, broker_name="icm", mode="demo", magic_base=260500,
                              approval_mode="auto", declared_class="raw", state_dir=tempfile.mkdtemp(),
                              probe_only=True, exploration_config=gold_cfg, instruments=specs), pb, [], {})
    eng.clock = lambda: START
    live: dict[str, pd.DataFrame] = {}
    first = (START - pd.Timedelta(days=days)).isoformat()
    for k, sym in enumerate(eng.books):
        hist = synthetic_ticks(first, START.floor("min").isoformat(), ticks_per_minute=1, seed=10 + k)
        live[sym] = synthetic_ticks(START.floor("min").isoformat(),
                                    (START + pd.Timedelta(hours=2)).isoformat(), ticks_per_minute=60, seed=100 + k)
        with using(eng, eng.books[sym]):
            eng.bars_1m = ticks_to_1m(hist)
            eng._closed_through = START.floor("min")
    return eng, pb, live


def run(minutes: float, days: int, latency_s: float, profile: bool, profile_after: float = 0.0) -> tuple[list[tuple[pd.Timestamp, float]], int,
                                                                              Counter[str]]:
    eng, pb, live = build(days)
    pb.latency_s = latency_s
    pb.calls.clear()
    idx = {s: pd.DatetimeIndex(df["ts_utc"]) for s, df in live.items()}
    feed = books.QuoteFeed(pb)                                 # as the run loop: one batched quote read a pass
    out: list[tuple[pd.Timestamp, float]] = []
    prof = cProfile.Profile() if profile else None
    clock = [START]
    eng.clock = lambda: clock[0]
    for k in range(int(minutes * 60)):
        now = clock[0] = START + pd.Timedelta(seconds=k)
        for sym, df in live.items():
            i = max(int(idx[sym].searchsorted(now, side="right")) - 1, 0)
            pb.set_quote(sym, Tick(ts_utc=now, bid=float(df["bid"].iloc[i]), ask=float(df["ask"].iloc[i])))
        t0 = time.perf_counter()
        on = prof if prof is not None and k >= profile_after * 60 else None
        if on is not None:
            on.enable()
        books.account_pass(eng, pb, engine_pass, feed, budget_s=1e9)     # every book, every pass: the whole cost
        if on is not None:
            on.disable()
        out.append((now, time.perf_counter() - t0))
    if prof is not None:
        path = Path.home() / "tmp" / "bench_account_pass.prof"
        path.parent.mkdir(parents=True, exist_ok=True)
        prof.dump_stats(str(path))
        pstats.Stats(prof).sort_stats("cumulative").print_stats(45)
    return out, len(eng.books), pb.calls


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--minutes", type=float, default=20.0)
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--latency-ms", type=float, default=0.0)
    ap.add_argument("--profile", action="store_true")
    ap.add_argument("--profile-after", type=float, default=0.0, help="profile only passes this many minutes in (warm)")
    a = ap.parse_args()
    times, n_books, calls = run(a.minutes, a.days, a.latency_ms / 1000.0, a.profile, a.profile_after)
    secs = [s for _, s in times]
    minute = [s for ts, s in times if ts.second in (1, 2, 3)]       # the passes that decide a closed minute
    p50, p99, worst = (float(np.percentile(secs, q)) for q in (50, 99, 100))
    print(f"{len(secs)} passes over {n_books} books: p50 {p50 * 1000:.0f} ms, p99 {p99 * 1000:.0f} ms, "
          f"max {worst * 1000:.0f} ms, mean {statistics.fmean(secs) * 1000:.0f} ms; minute-close passes max "
          f"{max(minute) * 1000:.0f} ms; budget {books.PASS_BUDGET_S:.1f} s")
    print("slowest:", ", ".join(f"{ts.strftime('%H:%M:%S')} {s * 1000:.0f} ms"
                                for ts, s in sorted(times, key=lambda x: -x[1])[:8]))
    print("bridge calls per pass:", ", ".join(f"{k} {v / len(secs):.2f}" for k, v in calls.most_common()))
    return 0 if worst <= books.PASS_BUDGET_S else 1


if __name__ == "__main__":
    sys.exit(main())
