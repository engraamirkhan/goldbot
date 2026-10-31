"""Speed checks that do not read a wall-clock budget (FR-3, ROADMAP 5a flaky list: #207, #212).

An absolute "answers within 300 ms at p95" assertion measures the runner, not the code: a shared CI worker or a laptop
under 40 parallel lanes stalls one of 20 samples past any fixed budget, and the failure lands on an unrelated PR. What
the code owes is a complexity class (an index, one pass, no per-row work in a loop). That is measured against work
done in the same process a moment apart: the same machine speed and load cancel in the ratio, and the minimum of
several runs drops the samples a scheduler stalled. A scan that went from one pass to a pass per row moves the ratio
by 100x or more, far outside the margin; a slow runner does not move it. The product's absolute latency budget is
watched in production (the API's own timings), not asserted in a unit test.
"""
from __future__ import annotations

import time
from typing import Any, Callable


def best_of(fn: Callable[[], Any], runs: int = 5) -> float:
    """Fastest of `runs` calls, in seconds (the scheduler only ever adds time, so the minimum is the cleanest sample)."""
    best = float("inf")
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t0)
    return best


def cost_vs_scan(work: Callable[[], Any], scan: Callable[[], Any], runs: int = 5) -> float:
    """`work` as a multiple of one plain pass over the same rows (`scan`), both best-of-`runs`, measured interleaved."""
    w, s = float("inf"), float("inf")
    for _ in range(runs):
        w = min(w, best_of(work, 1))
        s = min(s, best_of(scan, 1))
    return w / max(s, 1e-9)
