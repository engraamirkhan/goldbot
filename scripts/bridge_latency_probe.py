"""The order-lane drill (BACKLOG 135, ADR 0056; RUNBOOK "Order lane"): how long the bridge takes to answer a tick read,
sampled every `--every` seconds for `--minutes`, run on the brain beside the engine. Read-only: `last_tick` only, no
order, no owner step. On a bridge whose entries share the terminal connection, a read that arrives while the terminal
is sending an entry waits for it (seconds); with the order lane up it does not. Compare the slow reads it lists with
the entries' times in the engine journal (`order_late`, fills): a lane that works shows no read over `--slow` during
an entry.

Prints counts, p50/p99/max and each slow read's UTC time; never the bridge URL or token (keyring, as the engine).
Usage: python scripts/bridge_latency_probe.py <account_id> [--minutes 240] [--every 0.5] [--slow 1.0]"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd  # noqa: E402

from goldbot.execution.bridge import BridgeError, BridgeUnavailable, RemoteBroker  # noqa: E402
from goldbot.ops import accounts  # noqa: E402


def summarise(samples: list[tuple[pd.Timestamp, float | None]], slow_s: float) -> list[str]:
    """Lines for the report: counts, quantiles of the answered reads, and each slow or failed read."""
    took = sorted(s for _, s in samples if s is not None)
    failed = [t for t, s in samples if s is None]
    lines = [f"reads {len(samples)}, answered {len(took)}, failed {len(failed)}"]
    if took:
        q = pd.Series(took)
        lines.append(f"latency p50 {q.quantile(0.5):.3f} s, p99 {q.quantile(0.99):.3f} s, max {took[-1]:.3f} s")
    lines += [f"slow {t.isoformat()} {s:.2f} s" for t, s in samples if s is not None and s > slow_s]
    lines += [f"failed {t.isoformat()}" for t in failed]
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("account_id")
    ap.add_argument("--minutes", type=float, default=240.0)
    ap.add_argument("--every", type=float, default=0.5)
    ap.add_argument("--slow", type=float, default=1.0)
    a = ap.parse_args(argv)
    acc = accounts.load_accounts(logins=False)[a.account_id]
    endpoint = accounts.bridge_endpoint(a.account_id)
    if endpoint is None:
        print(f"refused: no bridge endpoint for {a.account_id} in the keyring (mt5-bridge-url/-token)")
        return 1
    rb = RemoteBroker(endpoint[0], endpoint[1], name=f"mt5-remote-{a.account_id}-probe", timeout_s=10.0)
    rb.read_attempts = 1
    samples: list[tuple[pd.Timestamp, float | None]] = []
    end = time.monotonic() + a.minutes * 60
    while time.monotonic() < end:
        at, t0 = pd.Timestamp.now("UTC"), time.monotonic()
        try:
            rb.last_tick(acc.symbol)
            samples.append((at, time.monotonic() - t0))
        except (BridgeError, BridgeUnavailable):
            samples.append((at, None))
        time.sleep(max(a.every - (time.monotonic() - t0), 0.0))
    print("\n".join(summarise(samples, a.slow)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
