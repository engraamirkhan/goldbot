"""The sprint-3000 controls.

GATE (amendment C1): "Null = random-SIDE twin at the same minute with the same stop/exit, or drift-adjusted zero; pass
needs excess >= 0." The engine takes the random-side twin at its exact expectation: the trade itself and its opposite
(same fill minute, stop distance and exit rule, the other side), half each, so no draw adds noise. It also reports the
drift-adjusted gross (side x in-window drift x hold subtracted).

DIAGNOSTIC (C1: "Same-side twins are an entry-timing diagnostic only"): the sprint-3000 leak-free twins, drawn here.
They keep the side, stop and exit and enter at or after the real fill inside the same session instance (with fewer
than `min_left` minutes left in it, inside the next `fallback` minutes). Their excess measures timing, not direction:
the C1 audit found that gating on them rejects a pure directional edge by construction."""
from __future__ import annotations

import hashlib

import numpy as np

from goldbot.research.sprint3000.market import MIN


def h_u(*key: object) -> float:
    """A uniform draw in [0, 1) fixed by `key`."""
    return int.from_bytes(hashlib.sha256("|".join(map(str, key)).encode()).digest()[:8], "big") / 2 ** 64


def leak_free_minutes(spec_id: str, trade_no: int, k0: int, run_end: np.ndarray, t: np.ndarray, n: int,
                      min_left: int = 30, fallback: int = 60) -> list[int]:
    """Minute indices of `n` same-side timing twins of the trade filled at minute k0: never before it."""
    hi = int(run_end[k0])
    if hi - k0 + 1 < min_left:
        hi = int(np.searchsorted(t, t[k0] + fallback * MIN, side="left")) - 1
    hi = max(hi, k0)
    return [k0 + int(h_u(spec_id, trade_no, j) * (hi - k0 + 1)) for j in range(n)]
