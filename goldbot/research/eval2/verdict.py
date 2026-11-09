"""EVAL-2 graded verdicts (prereg 2026-11-09 sections 1, 2 and 4.8; method v6 sections 1b, 3 and 7).

"What the owner sees (graded verdicts, weekly, each with net £/day at demo size and its 80 % interval)" ... "The owner
sees rungs and £, not 0/N." The ladder, highest rung reached: NOT TESTED -> NO SIGNAL (L1 retire) -> COST-BOUND (L2
carried nothing) -> TRADABLE ON HISTORY (L2 carry; an L3 read that did not reject stays here) -> CONFIRMED (L3; or
CONFIRM-SEEN when every forwarded cell is CONFIRM-SEEN: decided on TEST-OLD + the forward holdout + L4) -> ON TRACK /
DRIFTING (L4, after a confirm; DRIFTING is shown at any rung) ; KILLED (L4) wins over every rung. LIVE-READY is L5,
the owner's: never set here.

CONFIRM-SEEN (prereg section 1): "trigger class or defining geometry looked at on that window ... on the same
instrument at the same or lower TF, or excess overlap e >= 15 pp". HOLDOUT-SEEN: "trigger class matches a rule scored
on bars >= 2025-10-01 (P1, P2, P6, P8-A) or e >= 15 pp"; "fixed in the card before L2, never changed". e = o - b
(method section 1b): o the share of the cell's explore instances with a same-side signal of the rule as read, b that
rule's chance base rate over the same window.

DSR (prereg section 2): "DSR K = 3,471 + 17,862 = 21,333 (sensitivity 20,151). Reported, not gated, before L5."

Readings (listed for the quant-reviewer): the frozen inputs carry no probe magnitude, so an L2 that carries nothing
reads COST-BOUND (the COST-BOUND / EXIT-DRAG split needs the probe excess against cost); INSUFFICIENT (power) is set
by the caller that declines the L3 read (power < 0.3), as no frozen input says so; the £ view carries only what the
inputs give: the 80 % lower bound from L3's lb80_net_gate (at 1.5x deciding cost, so conservative); the point estimate
and the upper bound are NaN until an amendment adds them.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, Protocol

import numpy as np

from goldbot.research.catalogue.stats import deflated_sharpe
from goldbot.research.eval2.confirm import L3Result
from goldbot.research.eval2.hier import L2Decision

DSR_K: Final = 21_333                 # registry n_trials_effective 3,471 + snooping ledger 17,862 (ADR 0078)
DSR_K_SENSITIVITY: Final = 20_151     # 3,471 + 16,680 (real-only views)
OVERLAP_SEEN_PP: Final = 15.0         # excess overlap e = o - b >= 15 pp -> SEEN
OVERLAP_FLAG_N: Final = 20            # cells with n < 20 are flagged (point estimate used at any n)


class Verdict(StrEnum):
    NOT_TESTED = "NOT TESTED"
    INSUFFICIENT = "INSUFFICIENT (power)"
    NO_SIGNAL = "NO SIGNAL"
    COST_BOUND = "COST-BOUND"
    EXIT_DRAG = "EXIT-DRAG"
    TRADABLE = "TRADABLE ON HISTORY"
    CONFIRM_SEEN = "CONFIRM-SEEN"
    CONFIRMED = "CONFIRMED"
    ON_TRACK = "ON TRACK"
    DRIFTING = "DRIFTING"
    KILLED = "KILLED"
    LIVE_READY = "LIVE-READY"


class HoldoutStatus(StrEnum):
    SEEN = "HOLDOUT-SEEN"
    FRESH = "HOLDOUT-FRESH"


class ForwardStateLike(Protocol):
    """The field of Lane 5A's `eval2.forward.ForwardState` the verdict reads (structural, so this module does not
    import the forward lane)."""

    @property
    def status(self) -> str: ...


@dataclass(frozen=True, slots=True)
class FamilyVerdict:
    family: str
    verdict: Verdict
    holdout: HoldoutStatus
    confirm_seen_cells: tuple[str, ...]
    net_gbp_day: float
    net_gbp_day_lo80: float
    net_gbp_day_hi80: float
    reason: str


def excess_overlap_pp(o: float, b: float) -> float:
    """e = o - b in percentage points (o, b are shares in [0, 1])."""
    for name, v in (("o", o), ("b", b)):
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"overlap share {name} must be in [0, 1], got {v}")
    return round(100.0 * (o - b), 9)


def is_confirm_seen(*, trigger_seen: bool, o: float, b: float) -> bool:
    """CONFIRM-SEEN for a window: the trigger class or defining geometry was looked at there (`trigger_seen`), or the
    excess overlap e = o - b is >= 15 pp."""
    return bool(trigger_seen or excess_overlap_pp(o, b) >= OVERLAP_SEEN_PP)


def holdout_status(*, trigger_matches_holdout_rule: bool, o: float, b: float) -> HoldoutStatus:
    """HOLDOUT-SEEN when the trigger class matches a rule scored on bars >= 2025-10-01 (P1, P2, P6, P8-A), or the
    excess overlap with such a rule is >= 15 pp; else HOLDOUT-FRESH. Fixed in the card before L2."""
    seen = trigger_matches_holdout_rule or excess_overlap_pp(o, b) >= OVERLAP_SEEN_PP
    return HoldoutStatus.SEEN if seen else HoldoutStatus.FRESH


def dsr_at_k(daily_x: np.ndarray) -> dict[str, Any]:
    """The deflated Sharpe at DSR K 21,333 and at the sensitivity K 20,151 (`stats.deflated_sharpe`). Reported, never
    gated before L5."""
    x = np.asarray(daily_x, float)
    return {"k": DSR_K, "dsr": deflated_sharpe(x, DSR_K)["dsr"], "k_sensitivity": DSR_K_SENSITIVITY,
            "dsr_sensitivity": deflated_sharpe(x, DSR_K_SENSITIVITY)["dsr"]}


def _rung(probe_no_signal: bool | None, l2: L2Decision | None, l3: L3Result | None,
          confirm_seen: set[str]) -> tuple[Verdict, str]:
    if probe_no_signal:
        return Verdict.NO_SIGNAL, "L1: pooled probe 90 % UB below the cheapest cell's deciding cost at power >= 0.8"
    if l2 is None:
        if probe_no_signal is None:
            return Verdict.NOT_TESTED, "no probe and no L2 read"
        return Verdict.NOT_TESTED, "probe read, no L2 fit yet"
    if not l2.carried:
        return Verdict.COST_BOUND, (f"L2 carried no cell ({len(l2.killed)} killed, {len(l2.held)} held); "
                                    "COST-BOUND vs EXIT-DRAG needs the probe excess against cost")
    if l3 is None:
        return Verdict.TRADABLE, f"L2 carried {', '.join(l2.carried)}; no L3 read"
    if not l3.rejected:
        return Verdict.TRADABLE, (f"L3 on {l3.window} batch {l3.batch_j} (alpha {l3.alpha_j:.3g}) did not reject: "
                                  f"p {l3.p_cv_net:.3g}, p(excess) {l3.p_excess:.3g}, LB80 {l3.lb80_net_gate:+.3f} R")
    fwd = l3.cells_forwarded
    if fwd and all(c in confirm_seen for c in fwd):
        return Verdict.CONFIRM_SEEN, (f"L3 rejected on {l3.window}; every forwarded cell is CONFIRM-SEEN: needs the "
                                      "forward holdout and L4")
    return Verdict.CONFIRMED, f"L3 rejected on {l3.window} batch {l3.batch_j}; forwarded {', '.join(fwd)}"


def family_verdict(*, family: str, probe_no_signal: bool | None, l2: L2Decision | None, l3: L3Result | None,
                   l4: ForwardStateLike | None, holdout: HoldoutStatus, confirm_seen_cells: Sequence[str],
                   gbp_per_r: float, signals_per_day: float) -> FamilyVerdict:
    """The family's rung (module docstring) with its £/day view at demo size (`gbp_per_r` x `signals_per_day` x net R
    per signal)."""
    seen = tuple(str(c) for c in confirm_seen_cells)
    verdict, reason = _rung(probe_no_signal, l2, l3, set(seen))
    status = str(l4.status) if l4 is not None else None
    if status == "KILLED":
        verdict, reason = Verdict.KILLED, f"L4 forward e-process killed it (history rung was {verdict.value})"
    elif status == "DRIFTING":
        verdict, reason = Verdict.DRIFTING, f"L4 forward drifting (history rung {verdict.value})"
    elif status == "ON TRACK" and verdict in (Verdict.CONFIRMED, Verdict.CONFIRM_SEEN):
        verdict, reason = Verdict.ON_TRACK, f"L4 on track after {verdict.value}"
    lo = l3.lb80_net_gate * gbp_per_r * signals_per_day if l3 is not None and np.isfinite(l3.lb80_net_gate) \
        else math.nan
    return FamilyVerdict(family, verdict, holdout, seen, math.nan, lo, math.nan, reason)
