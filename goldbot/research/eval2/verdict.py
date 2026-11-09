"""EVAL-2 graded verdicts (prereg 2026-11-09 sections 1, 2 and 4.8 as amended in section 10; method v6 sections
1b, 3 and 7).

"What the owner sees (graded verdicts, weekly, each with net £/day at demo size and its 80 % interval)" ... "The owner
sees rungs and £, not 0/N." Amendment 10.3 (A17, signed by the quant-reviewer and the research director 2026-11-09),
the rungs in order, with KILLED and DRIFTING from L4 as before:
1. NO SIGNAL if the L1 retire holds.
2. INSUFFICIENT (power) if `l0_power_ok` is False; or if L2 carried a cell, `l3` is None and `power_l3` < 0.3. An L3
   read while `power_l3` < 0.3 raises (read condition breached).
3. L2 carried nothing: INSUFFICIENT (power) when no cell has P(theta > 0) >= carry_p and the pooled probe one-sided
   90 % lower bound is <= 0 (no information); else EXIT-DRAG when the pooled probe excess >= `cheapest_cost_atr`;
   else COST-BOUND.
4. TRADABLE ON HISTORY on an L2 carry with no L3, or an L3 that rejected with no forwarded cell ("family rejected at
   alpha_j, no cell survives Holm"). An L3 that did not reject reads NOT CONFIRMED (RD condition 2): closed, never
   re-read on TEST-OLD or 2018-25, never on the live-candidate list; it stays in the registry, K, the snooping ledger
   and the weekly rung counts.
5. CONFIRM-SEEN when the family rejected and every forwarded cell is CONFIRM-SEEN; otherwise CONFIRMED (the reason
   lists any forwarded CONFIRM-SEEN cell, each still needing the forward holdout).
6. LIVE-READY is never set here (L5, the owner's record). ON TRACK shows only after CONFIRMED or CONFIRM-SEEN.

£/day at demo size = gbp_per_r x signals_per_day x net R per signal: with L3 its `cv_net` and 80 % interval
(out-of-sample, TEST-OLD, 1.0x deciding cost); `lb80_net_gate` stays the gate value and is never the £ interval.
With L2 only the amendment asks for the card-weighted posterior net mean (theta + twin net) of the carried cells:
`CellPosterior` carries theta, not the twin net, so that figure cannot be formed inside the frozen types; it is NaN
and the reason labels the rung "explore, shrunk, in-sample" until the twin net reaches the verdict (open item).

CONFIRM-SEEN (prereg section 1): "trigger class or defining geometry looked at on that window ... on the same
instrument at the same or lower TF, or excess overlap e >= 15 pp". HOLDOUT-SEEN: "trigger class matches a rule scored
on bars >= 2025-10-01 (P1, P2, P6, P8-A) or e >= 15 pp"; "fixed in the card before L2, never changed". e = o - b
(method section 1b).

DSR (prereg section 2): "DSR K = 3,471 + 17,862 = 21,333 (sensitivity 20,151). Reported, not gated, before L5."
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
from goldbot.research.eval2.hier import CARRY_P_MIN, CellPosterior, L2Decision
from goldbot.research.eval2.probe import ProbeResult, pooled_bound, pooled_lower_bound

DSR_K: Final = 21_333                 # registry n_trials_effective 3,471 + snooping ledger 17,862 (ADR 0078)
DSR_K_SENSITIVITY: Final = 20_151     # 3,471 + 16,680 (real-only views)
OVERLAP_SEEN_PP: Final = 15.0         # excess overlap e = o - b >= 15 pp -> SEEN
OVERLAP_FLAG_N: Final = 20            # cells with n < 20 are flagged (point estimate used at any n)
POWER_L3_MIN: Final = 0.3             # L3 read condition: power >= 0.3 at the L2 shrunk estimate, never lowered


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
    NOT_CONFIRMED = "NOT CONFIRMED"     # amendment 10.3 rung 4 (RD condition 2): L3 read, not rejected; closed


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


def _no_carry(posts: Sequence[CellPosterior] | None, probes: Sequence[ProbeResult] | None,
              cheapest_cost_atr: float | None, l2: L2Decision) -> tuple[Verdict, str]:
    head = f"L2 carried no cell ({len(l2.killed)} killed, {len(l2.held)} held)"
    informed_l2 = any(p.p_pos >= CARRY_P_MIN for p in posts or ())
    lb = pooled_lower_bound(probes) if probes else math.nan
    informed_l1 = bool(np.isfinite(lb) and lb > 0)
    if not informed_l2 and not informed_l1:
        return Verdict.INSUFFICIENT, (f"{head}; no cell at P(theta > 0) >= {CARRY_P_MIN} and the pooled probe 90 % "
                                      f"lower bound is {lb:+.3f} ATR (<= 0 or not read): no information")
    point = pooled_bound(probes)[0] if probes else math.nan
    if cheapest_cost_atr is not None and np.isfinite(point) and point >= cheapest_cost_atr:
        return Verdict.EXIT_DRAG, (f"{head}; the pooled probe excess {point:+.3f} ATR is above the cheapest deciding "
                                   f"cost {cheapest_cost_atr:.3f} ATR: the exits lose it (route: exits-researcher)")
    return Verdict.COST_BOUND, (f"{head}; information found (probe {point:+.3f} ATR), below cost "
                                "(route: timeframe, session or order type)")


def _rung(*, probe_no_signal: bool | None, l2: L2Decision | None, l3: L3Result | None, confirm_seen: set[str],
          l0_power_ok: bool | None, power_l3: float | None, posts: Sequence[CellPosterior] | None,
          probes: Sequence[ProbeResult] | None, cheapest_cost_atr: float | None) -> tuple[Verdict, str]:
    low_power = power_l3 is not None and power_l3 < POWER_L3_MIN
    if l3 is not None and low_power:
        raise ValueError(f"an L3 read under power {power_l3:.2f} < {POWER_L3_MIN} breaches the read condition")
    if probe_no_signal:
        return Verdict.NO_SIGNAL, "L1: pooled probe 90 % UB below the cheapest cell's deciding cost at power >= 0.8"
    if l0_power_ok is False:
        return Verdict.INSUFFICIENT, "L0: the card's power floor does not fit the window's expected n"
    if l2 is None:
        if probe_no_signal is None:
            return Verdict.NOT_TESTED, "no probe and no L2 read"
        return Verdict.NOT_TESTED, "probe read, no L2 fit yet"
    if not l2.carried:
        return _no_carry(posts, probes, cheapest_cost_atr, l2)
    if l3 is None:
        if low_power:
            return Verdict.INSUFFICIENT, (f"L2 carried {', '.join(l2.carried)}; TEST-OLD power {power_l3:.2f} < "
                                          f"{POWER_L3_MIN} at the shrunk estimate: neither window is read")
        return Verdict.TRADABLE, (f"L2 carried {', '.join(l2.carried)} (explore, shrunk, in-sample); no L3 read")
    if not l3.rejected:
        return Verdict.NOT_CONFIRMED, (f"L3 on {l3.window} batch {l3.batch_j} (alpha {l3.alpha_j:.3g}) did not reject: "
                                       f"p {l3.p_cv_net:.3g}, p(excess) {l3.p_excess:.3g}, LB80 "
                                       f"{l3.lb80_net_gate:+.3f} R; closed, never re-read, never a live candidate")
    fwd = l3.cells_forwarded
    if not fwd:
        return Verdict.TRADABLE, (f"family rejected at alpha_j {l3.alpha_j:.3g}, no cell survives Holm; TEST-OLD is "
                                  "read once, so it is not re-read")
    seen_fwd = [c for c in fwd if c in confirm_seen]
    if len(seen_fwd) == len(fwd):
        return Verdict.CONFIRM_SEEN, (f"L3 rejected on {l3.window}; every forwarded cell is CONFIRM-SEEN: needs the "
                                      "forward holdout and L4")
    extra = f"; CONFIRM-SEEN (need the forward holdout): {', '.join(seen_fwd)}" if seen_fwd else ""
    return Verdict.CONFIRMED, f"L3 rejected on {l3.window} batch {l3.batch_j}; forwarded {', '.join(fwd)}{extra}"


def family_verdict(*, family: str, probe_no_signal: bool | None, l2: L2Decision | None, l3: L3Result | None,
                   l4: ForwardStateLike | None, holdout: HoldoutStatus, confirm_seen_cells: Sequence[str],
                   gbp_per_r: float, signals_per_day: float, l0_power_ok: bool | None, power_l3: float | None,
                   posts: Sequence[CellPosterior] | None, probes: Sequence[ProbeResult] | None,
                   cheapest_cost_atr: float | None) -> FamilyVerdict:
    """The family's rung (module docstring, amendment 10.3) with its £/day view at demo size (`gbp_per_r` x
    `signals_per_day` x net R per signal)."""
    seen = tuple(str(c) for c in confirm_seen_cells)
    verdict, reason = _rung(probe_no_signal=probe_no_signal, l2=l2, l3=l3, confirm_seen=set(seen),
                            l0_power_ok=l0_power_ok, power_l3=power_l3, posts=posts, probes=probes,
                            cheapest_cost_atr=cheapest_cost_atr)
    status = str(l4.status) if l4 is not None else None
    if status == "KILLED":
        verdict, reason = Verdict.KILLED, f"L4 forward e-process killed it (history rung was {verdict.value})"
    elif status == "DRIFTING":
        verdict, reason = Verdict.DRIFTING, f"L4 forward drifting (history rung {verdict.value})"
    elif status == "ON TRACK" and verdict in (Verdict.CONFIRMED, Verdict.CONFIRM_SEEN):
        verdict, reason = Verdict.ON_TRACK, f"L4 on track after {verdict.value}"
    scale = gbp_per_r * signals_per_day
    net = lo = hi = math.nan
    if l3 is not None:
        net, lo, hi = l3.cv_net * scale, l3.cv_net_lo80 * scale, l3.cv_net_hi80 * scale
        reason += "; £/day: TEST-OLD control-variate net at 1.0x deciding cost, 80 % interval (out-of-sample)"
    elif l2 is not None and l2.carried:
        reason += ("; £/day: explore, shrunk, in-sample view needs each carried cell's twin net (not in CellPosterior):"
                   " not shown")
    return FamilyVerdict(family, verdict, holdout, seen, net, lo, hi, reason)
