"""Interfaces this slice (nulls, novelty, gen_combine, scripts/autodisc.py) codes against, plus minimal stand-ins.

REPLACE AT INTEGRATION (ADR 0058 phase 1, backlog 140). spec/ledger/windows are real (foundation slice). Still
stand-ins, owned by the other slices:
  - `OutcomeTensor` (bar x side x exit net R)      -> `autodisc.outcomes` (stand-in: the dataclass below)
  - `evaluate_spec` (floors, fold fitness, stress) -> `autodisc.evaluate` (stand-in: `stub_evaluate`)
  - `MaskProvider` (predicate -> point-in-time mask) -> `autodisc.primitives` over `goldbot/features`
Nothing here reads market data. Everything is in R units on the explore window only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from goldbot.research.autodisc.spec import ExitTemplate, Predicate

# The fixed exit menu (design 2): {stop 1.0 ATR / 1.5 ATR / structural} x {target 1R / 2R}. The design says "6";
# the time-exit variant is not in this count. ASSUMPTION flagged in the hand-back; the evaluator owns the real menu.
EXIT_MENU: tuple[str, ...] = ("atr_1.0_1R", "atr_1.0_2R", "atr_1.5_1R", "atr_1.5_2R", "structural_1R",
                              "structural_2R")
N_FOLDS = 4

FloatArr = NDArray[np.float64]
BoolArr = NDArray[np.bool_]
IntArr = NDArray[np.int64]


def exit_key(e: ExitTemplate) -> str:
    return f"{e.stop}_{e.target}"


def exit_template(key: str) -> ExitTemplate:
    stop, target = key.rsplit("_", 1)
    return ExitTemplate(stop=stop, target=target)  # type: ignore[arg-type]


@dataclass(frozen=True)
class OutcomeTensor:
    """STAND-IN for `autodisc.outcomes`. `net_r[bar, side, exit]` (side 0 = long, 1 = short) is the triple-barrier
    outcome net of the conservative cost, in R; `cost_r[bar]` is the round-trip cost in R (for the 1.5x stress);
    `day_id` the session-day cluster; `fold_id` in 0..N_FOLDS-1 the purged CV fold. All explore window only."""
    timeframe: str
    net_r: FloatArr
    cost_r: FloatArr
    day_id: IntArr
    fold_id: IntArr

    @property
    def n_bars(self) -> int:
        return int(self.net_r.shape[0])

    @property
    def n_days(self) -> int:
        return int(self.day_id.max()) + 1 if self.day_id.size else 0


class MaskProvider(Protocol):
    """`autodisc.primitives` stand-in interface: point-in-time boolean masks over one tensor's bars."""
    def family_entries(self, family: str, timeframe: str) -> tuple[BoolArr, IntArr]:
        """(entry mask, side index 0/1 per bar) of an existing family's rule."""
    def predicate_mask(self, pred: Predicate, timeframe: str) -> BoolArr: ...


@dataclass(frozen=True)
class EvalResult:
    n_events: int
    mean_r: float          # net mean R
    fitness: float         # min over folds of the day-clustered net t
    stress_mean_r: float   # net mean R at 1.5x the cost
    ok: bool               # floors passed (events, >0 folds populated)


def _cluster_t(r: FloatArr, day: IntArr, n_days: int) -> float:
    """Day-clustered t of the mean: sum(r) / sqrt(sum over days of (day sum)^2)."""
    if r.size < 2:
        return 0.0
    ds = np.bincount(day, weights=r, minlength=n_days)
    m = float(r.mean())      # centred, so a large mean does not inflate the variance estimate
    dc = np.bincount(day, minlength=n_days).astype(float)
    ds_c = ds - m * dc
    denom = float(np.sqrt(np.sum(ds_c * ds_c)))
    return float(r.sum() / denom) if denom > 0 else 0.0


def stub_evaluate(t: OutcomeTensor, mask: BoolArr, side: IntArr, exit_idx: int, min_events: int = 300) -> EvalResult:
    """STAND-IN for `autodisc.evaluate`: floors, min-fold clustered net t, 1.5x cost stress. No plateau/drift/
    side-split (the real evaluator has them); enough to exercise the null calibration end to end."""
    idx = np.flatnonzero(mask)
    if idx.size < min_events:
        return EvalResult(int(idx.size), 0.0, -np.inf, 0.0, False)
    r = t.net_r[idx, side[idx], exit_idx]
    fold = t.fold_id[idx]
    day = t.day_id[idx]
    ts = []
    for f in range(N_FOLDS):
        sel = fold == f
        if sel.sum() < 2:
            return EvalResult(int(idx.size), float(r.mean()), -np.inf, 0.0, False)
        ts.append(_cluster_t(r[sel], day[sel], t.n_days))
    stress = float((r - 0.5 * t.cost_r[idx]).mean())
    return EvalResult(int(idx.size), float(r.mean()), float(min(ts)), stress, True)
