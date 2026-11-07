"""Engine-level entry refusals of exploration signals (docs/research/prereg-amendment-2026-11-07-kill-only.md).

The amendment's rules, as built here:

* "The block is an engine-level refusal with its own journalled reason (`exploration_refused:window_0207`), applied
  after the arm's rule has produced the signal and before any room, slot or cap check. It is not an edit of any arm's
  `window_utc`/`window_local`": `arm_hash` covers `params`, so an edit would be a new arm id and a new K row, and a
  narrowed rule would produce no signal to shadow. So each refusal here reads the signal the rule already produced
  (`Pending`) and its arm, and changes nothing about the arm.
* Entry path only. `first_refusal` is called from one place, the entry branch of `exploration._send` (every signal of
  `step`/`on_pass` and every pyramid add reaches the order core through it), right after the duplicate check. Never
  from `exploration.exits`, `exploration._pyramid` (the group's stop moves), `cost_probe._place` or the Friday rule
  (`Engine._weekend_rule`): "Exits, stops, targets, holds and the RiskGate are untouched; a position opened before
  02:00 keeps every exit" (amendment 3.1). A test pins the single call site.
* Fail closed. A refusal that raises refuses the entry as `refusal_error:<name>` (ERROR_PREFIX) and the caller
  journals it with the exception's type; the other refusals are not consulted (the entry is already refused). A
  refusal can only ever stop an entry: kill-only (amendment section 0).
* The shadow log is the existing untaken-candidate path: the caller records the refused signal through
  `exploration.candidate(..., blocked=<name>)` into `exploration_obs` (`taken = 0`, `blocked_reason = <name>`, side,
  `stop_dist`, `target_dist`, `max_hold_s`, `decision_ns`, `is_control`, broker), scored later by
  `exploration_data.label_candidates` (amendment 3.2). Controls in scope are refused and shadowed by the same path.
* Decision time is the rule's own decision close (`Pending.decision_utc` of a shifted signal, else `Pending.bar_ts`),
  in UTC; every window is inclusive of its start and exclusive of its end (amendment section 2: "02:00:00 is inside,
  07:00:00 is outside").

The refusals (`REFUSALS`, in order; the first that applies names the refusal):

* `window_0207` (amendment section 3): an in-scope gold arm's signal with decision time in [02:00, 07:00) UTC. In
  scope: gold arms (instrument XAUUSD; XAU crosses are not gold here) of every role but `cost`, and not the cost arms'
  dedicated controls (F1C, G1C): "Exempt: `role: cost` arms (F1A, G1A) and their dedicated controls (F1C, G1C)".
  Scope is the amendment's three accounts; the ADR 0062 weekend lane (an engine with a `weekend_window`) is outside it.
* `f1_pit` (amendment section 8): "F1A/F1C entries are refused outside 08:00-13:30 America/New_York (13:00-18:30 UTC
  under US winter time), as an engine-level refusal with the control on the same tz-aware window".
* `learning_icm_only`, `har_unavailable`, `har_low_range` (signed plan 2026-11-07 WS-6, `refusals_novel`): gold
  learning entries on ICM only (N-1a) and the N-3 HAR low-range-day abstention, fail closed once its job has run.

Adding a refusal: append an `EntryRefusal` to REFUSALS (its `name` is the journalled and recorded reason), with a
pre-registered rule that can only refuse, and a boundary test.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import time
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.engine import refusals_novel
from goldbot.instruments import GOLD
from goldbot.research.arms import COST_CONTROLS

if TYPE_CHECKING:
    from goldbot.engine.exploration import Arm, Pending
    from goldbot.engine.runner import Engine

log = logging.getLogger(__name__)

ERROR_PREFIX = "refusal_error:"     # a refusal that raised: the entry is refused under this prefix and its name
WINDOW_0207 = "window_0207"
WINDOW_0207_UTC = (time(2, 0), time(7, 0))          # [02:00, 07:00) UTC, by decision time (amendment 3.1)
F1_PIT = "f1_pit"
F1_PIT_WINDOW = (time(8, 0), time(13, 30))          # [08:00, 13:30) in F1_PIT_TZ (amendment section 8)
F1_PIT_TZ = ZoneInfo("America/New_York")
F1_PIT_ARMS = frozenset({"F1A", "F1C"})             # the F-1 cost arm and its control share the window
COST_CONTROL_IDS = frozenset(COST_CONTROLS.values())


class EntryRefusal(FrozenRecord):
    """One pre-registered entry refusal: `applies(eng, arm, signal)` is True when the signal may not enter; `name` is
    the reason journalled (`exploration_refused:<name>`) and recorded on the candidate (`blocked_reason`)."""
    name: str
    applies: Callable[..., bool]


def _decision_utc(s: Pending) -> pd.Timestamp:
    """The rule's own decision close: a shifted XC15 keeps it in `decision_utc` (its `bar_ts` is the later close it
    was released at), so a signal decided at 06:45 and released at 07:00 is still a 02:00-07:00 decision."""
    t = pd.Timestamp(s.decision_utc if s.decision_utc is not None else s.bar_ts)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def in_window_0207_scope(eng: Engine, arm: Arm) -> bool:
    """Amendment section 2, "Exploration arms in scope of rule 1": a gold arm (instrument XAUUSD) of role strategy,
    learning, owner_experiment or control, not a cost arm (`role: cost`) nor a cost arm's dedicated control (F1C, G1C),
    on an engine that is not the ADR 0062 weekend lane."""
    if getattr(eng.cfg, "weekend_window", None) is not None:
        return False
    return arm.instrument == GOLD and arm.spec.role != "cost" and arm.arm_id not in COST_CONTROL_IDS


def window_0207(eng: Engine, arm: Arm, s: Pending) -> bool:
    """Amendment 3.1: "every in-scope gold exploration signal ... with decision time in [02:00, 07:00) UTC is
    refused"."""
    if not in_window_0207_scope(eng, arm):
        return False
    lo, hi = WINDOW_0207_UTC
    return lo <= _decision_utc(s).time() < hi


def f1_pit(eng: Engine, arm: Arm, s: Pending) -> bool:
    """Amendment section 8: F1A and F1C are refused outside [08:00, 13:30) America/New_York at their decision time
    (tz-aware, so the window moves with US DST)."""
    if arm.arm_id not in F1_PIT_ARMS:
        return False
    lo, hi = F1_PIT_WINDOW
    return not lo <= _decision_utc(s).tz_convert(F1_PIT_TZ).time() < hi


REFUSALS: tuple[EntryRefusal, ...] = (
    EntryRefusal(name=WINDOW_0207, applies=window_0207),
    EntryRefusal(name=F1_PIT, applies=f1_pit),
    # signed plan 2026-11-07 WS-6 (`refusals_novel`, prereg-2026-11-07-n3-har.md): gold learning entries on ICM only
    # (N-1a), then N-3's fail-closed availability check and its low-range day
    EntryRefusal(name=refusals_novel.LEARNING_ICM_ONLY, applies=refusals_novel.learning_icm_only),
    EntryRefusal(name=refusals_novel.HAR_UNAVAILABLE, applies=refusals_novel.har_unavailable),
    EntryRefusal(name=refusals_novel.HAR_LOW_RANGE, applies=refusals_novel.har_low_range),
)


class Refused(FrozenRecord):
    """Why an entry is refused: the refusal's name, or ERROR_PREFIX + its name with the exception's type in `error`."""
    reason: str
    error: str | None = None


def first_refusal(eng: Engine, arm: Arm, s: Pending,
                  refusals: tuple[EntryRefusal, ...] | None = None) -> Refused | None:
    """The first of `refusals` (REFUSALS by default, read at call time) that refuses the signal `s` of `arm`, or None
    when every one lets it through. Fail closed: a refusal that raises refuses the entry (`refusal_error:<name>`)."""
    for r in REFUSALS if refusals is None else refusals:
        try:
            hit: Any = r.applies(eng, arm, s)
        except Exception as exc:
            log.warning("entry refusal %s raised on arm %s (%s): entry refused", r.name, arm.arm_id,
                        type(exc).__name__)
            return Refused(reason=ERROR_PREFIX + r.name, error=type(exc).__name__)
        if hit:
            return Refused(reason=r.name)
    return None
