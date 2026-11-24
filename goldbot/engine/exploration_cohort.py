"""The demo learning cohort in the engine (ADR 0095; docs/research/prereg-amendment-2026-11-24-demo-learning-cohort.md).

Owner 2026-11-24, "it is quite concerning that it is not trading at all"; team decision (PD + research director + CFO):
the retired arms the self-retrain judges "send REAL DEMO ORDERS again on icm-probe only, 0.01 lot, as a capped
learning cohort, while their controls stay replay_only". The account names its cohort (accounts.yaml
`exploration.cohort`, `ops.accounts.LearningCohort`, refused at load off a demo probe_only account and for an arm that
is a control or not at the minimum lot). In the engine:

* At the start (`start`) a named arm joins only when it is configured here, its frozen row is retired
  (`retired_day`: the cohort is for retired arms; their retirement stands for promotion and learning weights), it
  is gold, not a control (role control or a control_share), in `control_mode: broker` (a replay_only row is
  structurally unable to send and stays so), not hash-refused, invalid or on a dark or 1d/1w timeframe, and today is
  inside since..until. A member is taken out of `retired` and of the unqualified refusals (journalled
  `exploration_arm_cohort` with the rule); a named arm that does not join stays retired (journalled
  `exploration_arm_cohort_refused` with why).
* The cohort's own caps replace the evidence stops the arms were retired by: for a member the engine applies no
  daily-review retirement (`refresh_retired`), cost-read completion (`refresh_cost_reads`), H-81 cost or edge-cap
  suspension (`cost_suspended`) and no learning-class start or read (`exploration_learning`). Every other cap binds as
  for any arm: the RiskGate, the per-trade cap, the exploration budget, the daily entry budget, slots, the global and
  margin caps, the account's daily and weekly limits.
* Once a minute, and again whenever a member's position leaves the book (`refresh`), this account's ledger is read: each member's net P&L over the fills sent since `since`
  and the cohort's net P&L over the fills sent since this ISO week's Monday 00:00 UTC (`read`; a close without a
  readable P&L counts as a loss at its stop plus its round trip, never as 0). A member whose loss reaches
  `arm_loss_cap_gbp` retires for good (`exploration_arm_cohort_loss_cap`); past `until` every member retires
  (`exploration_arm_cohort_expired`). A stopped member records replay-only again (`Exploration.recording`), as it
  did before the cohort, so the self-retrain's candidate rows never stop. A week loss at `week_loss_cap_gbp` holds every member until next Monday
  (journalled `exploration_cohort_week_cap` once a week; the next week's read starts from 0).
* At the send (`send_refusal`, in `exploration._send`): nothing until a read has succeeded (`cohort_unread`, fail
  closed); at most `max_open_per_arm` open per member (`cohort_max_open`); the member's loss + its open risk at its
  stops + the new trade's loss and round trip must stay below its cap (`cohort_loss_cap`), and the week's loss + every
  member's open risk + the new trade's below the weekly cap (`cohort_week_cap`).
* The fills carry `evidence_class` "learning_cohort" (never "qualified"): demo learning evidence, never a
  qualification and never in a family ranking.

Entries only: nothing here touches an open position, a stop, a target or an exit.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Collection, Mapping
from typing import TYPE_CHECKING, Any, NamedTuple

import pandas as pd

from goldbot.instruments import GOLD

if TYPE_CHECKING:
    from goldbot.engine.exploration import Arm, Exploration
    from goldbot.engine.runner import Engine
    from goldbot.ops.accounts import LearningCohort

log = logging.getLogger(__name__)

COHORT_RULE = "demo learning cohort (prereg-amendment-2026-11-24-demo-learning-cohort.md, ADR 0095)"
EVIDENCE_CLASS = "learning_cohort"
ADMITTED = "exploration_arm_cohort"
REFUSED = "exploration_arm_cohort_refused"
LOSS_CAP = "exploration_arm_cohort_loss_cap"
EXPIRED = "exploration_arm_cohort_expired"
WEEK_CAP = "exploration_cohort_week_cap"
UNREAD = "exploration_cohort_unread"


def members(ex: Any) -> frozenset[str]:
    """The arms admitted to the cohort at the start (empty for an engine without one, or a stand-in state)."""
    return getattr(ex, "cohort", frozenset())


def week_start(now: pd.Timestamp) -> pd.Timestamp:
    """Monday 00:00 UTC of `now`'s ISO week: the weekly cap's read starts here."""
    day = pd.Timestamp(now).tz_convert("UTC").normalize()
    return day - pd.Timedelta(days=day.weekday())


def refusal(eng: Engine, ex: Exploration, arm: Arm, cohort: LearningCohort, today: pd.Timestamp) -> str | None:
    """Why a named arm may not join the cohort at this start, None when it may (the module docstring's rule)."""
    from goldbot.engine.long_bars import LONG_TFS
    cfg = ex.cfg
    if eng.cfg.mode != "demo" or not eng.cfg.probe_only:
        return "not a demo probe_only account"
    if not arm.spec.retired:
        return "its frozen row is not retired (the cohort is for retired arms)"
    if arm.instrument != GOLD:
        return "not a gold arm"
    if arm.spec.role == "control" or arm.control_share is not None:
        return "a control (controls stay replay_only)"
    if arm.spec.control_mode != "broker":
        return f"control_mode {arm.spec.control_mode} (never sends)"
    if arm.adds or arm.unit_lots is not None:
        return "not at the minimum lot"
    if arm.arm_id in cfg.hash_refused or arm.arm_id in cfg.invalid:
        return "its row is hash-refused or invalid"
    if arm.tf not in set(eng.cfg.timeframes_live) or arm.tf in LONG_TFS:
        return f"timeframe {arm.tf} is dark or 1d/1w"
    if today < pd.Timestamp(cohort.since, tz="UTC"):
        return f"the cohort starts on {cohort.since}"
    if today > pd.Timestamp(cohort.until, tz="UTC"):
        return f"the cohort ended after {cohort.until}"
    return None


def start(eng: Engine, ex: Exploration) -> None:
    """At the engine's start, after the spec retirements, the unqualified refusals and the dark timeframes: the named
    arms that may join leave `retired` and the unqualified refusals; each is journalled, joined or refused."""
    cohort = ex.cfg.cohort
    if cohort is None:
        return
    today = _today(eng)
    joined: set[str] = set()
    for arm_id in sorted(cohort.arms):
        arm = ex.arms.get(arm_id)
        why = "not configured on this account (or blocked in the spec)" if arm is None else \
            refusal(eng, ex, arm, cohort, today)
        if why is not None:
            log.warning("cohort arm %s refused: %s", arm_id, why)
            eng.decisions.append({"ts": time.time(), "agent": "explore:" + arm_id, "action": REFUSED, "arm": arm_id,
                                  "why": why, "rule": COHORT_RULE})
            continue
        joined.add(arm_id)
        log.warning("exploration arm %s sends demo orders as a member of the learning cohort (%s)", arm_id,
                    COHORT_RULE)
        eng.decisions.append({"ts": time.time(), "agent": "explore:" + arm_id, "action": ADMITTED, "arm": arm_id,
                              "rule": COHORT_RULE, "since": cohort.since, "until": cohort.until,
                              "arm_loss_cap": cohort.arm_loss_cap_gbp, "week_loss_cap": cohort.week_loss_cap_gbp,
                              "max_open": cohort.max_open_per_arm})
    ex.cohort = frozenset(joined)
    ex.retired = ex.retired - ex.cohort
    for a in joined:
        ex.unqualified.pop(a, None)


def _today(eng: Engine) -> pd.Timestamp:
    """The engine clock's UTC day at the start (`Engine.clock`, the host clock)."""
    return pd.Timestamp(eng.clock()).tz_convert("UTC").normalize()


class CohortRead(NamedTuple):
    loss: dict[str, float]      # member -> its net loss since `since` (>= 0)
    week_pnl: float             # the cohort's net P&L since this week's Monday 00:00 UTC (and since `since`)
    fills: int                  # closed fills read this week


def read(conn: Any, cohort: LearningCohort, arms: Collection[str], now: pd.Timestamp,
         caps: Mapping[str, float], accounts: Collection[str] | None = None) -> CohortRead:
    """The cohort's ledger read at `now` (`exploration_learning.read`'s rule per arm: every filled close sent on or after
    the start; a close without a readable P&L is a loss at its stop plus its round trip, `caps` its per-trade cap when
    its stop loss was not recorded). `accounts`: only these accounts' rows (None: every account in the ledger)."""
    from goldbot.engine.exploration_learning import read as arm_read
    since = pd.Timestamp(cohort.since, tz="UTC")
    week = max(week_start(now), since)
    loss: dict[str, float] = {}
    week_pnl, fills = 0.0, 0
    for a in sorted(arms):
        cap = float(caps.get(a, max(caps.values(), default=0.0)))
        loss[a] = max(0.0, -arm_read(conn, a, cap, since, accounts).pnl)
        got = arm_read(conn, a, cap, week, accounts)
        week_pnl += got.pnl
        fills += got.fills + got.censored
    return CohortRead(loss, round(week_pnl, 2), fills)


def refresh(eng: Engine, ex: Exploration, now: pd.Timestamp) -> None:
    """Once a minute: read the cohort's ledger; retire a member at its loss cap, every member past `until`; hold every
    member while the week's loss is at the weekly cap. A failed read keeps `cohort_ok` False (every member refused)
    and is journalled once until a read succeeds."""
    from goldbot.engine.exploration_learning import _stop
    cohort = ex.cfg.cohort if members(ex) else None      # membership first: a stand-in state has no cohort
    if cohort is None or not members(ex):
        return
    # once a minute, and again as soon as a member's position leaves the book (its close is then read before the
    # member's next entry, not up to a minute later: a 5m arm can stop out and signal again on one bar close)
    tags = {"explore:" + a for a in members(ex)}
    key = (now.floor("min"), tuple(sorted(pid for pid, t in eng.open.items() if t.agent_id in tags)))
    if ex.cohort_read_at == key:
        return
    ex.cohort_read_at = key
    live = sorted(members(ex) - ex.retired)
    try:
        with ex.db.connection() as conn:
            got = read(conn, cohort, members(ex), now, eng.gate.limits.exploration_arms, [eng.cfg.account_id])
    except Exception as exc:
        ex.cohort_ok = False
        log.warning("learning cohort's ledger unreadable (%s): cohort entries refused", type(exc).__name__)
        if not ex.cohort_warned:
            ex.cohort_warned = True
            eng.decisions.append({"ts": now.isoformat(), "agent": None, "action": UNREAD, "arms": live,
                                  "error": type(exc).__name__})
        return
    ex.cohort_ok, ex.cohort_warned = True, False
    ex.cohort_loss, ex.cohort_week_loss = got.loss, max(0.0, -got.week_pnl)
    stops: dict[str, tuple[str, str, dict[str, Any]]] = {}
    expired = now.tz_convert("UTC").normalize() > pd.Timestamp(cohort.until, tz="UTC")
    for a in live:
        detail: dict[str, Any] = {"loss": round(got.loss.get(a, 0.0), 2), "loss_cap": cohort.arm_loss_cap_gbp}
        if expired:
            stops[a] = (EXPIRED, f"{COHORT_RULE}: the cohort ended after {cohort.until}", detail)
        elif got.loss.get(a, 0.0) >= cohort.arm_loss_cap_gbp - 1e-9:
            stops[a] = (LOSS_CAP, f"{COHORT_RULE}: arm loss cap {cohort.arm_loss_cap_gbp:g} reached", detail)
    _stop(eng, ex, stops, now)
    # a stopped member records replay-only again, as it did before the cohort: the self-retrain's rows never stop
    ex.recording = ex.recording | frozenset(stops)
    week = week_start(now)
    if ex.cohort_week_loss >= cohort.week_loss_cap_gbp - 1e-9 and ex.cohort_week_told != week:
        ex.cohort_week_told = week
        log.warning("learning cohort held until next Monday: week loss %.2f at the cap %.2f", ex.cohort_week_loss,
                    cohort.week_loss_cap_gbp)
        eng.decisions.append({"ts": now.isoformat(), "agent": None, "action": WEEK_CAP, "rule": COHORT_RULE,
                              "week_from": week.isoformat(), "week_loss": round(ex.cohort_week_loss, 2),
                              "week_loss_cap": cohort.week_loss_cap_gbp,
                              "until": (week + pd.Timedelta(days=7)).isoformat()})


def max_open_refusal(ex: Exploration, arm_id: str, open_now: int) -> str | None:
    """`cohort_max_open` when the member holds `max_open_per_arm` positions already, None otherwise."""
    cohort = ex.cfg.cohort if arm_id in members(ex) else None
    if cohort is None or arm_id not in members(ex):
        return None
    return "cohort_max_open" if open_now >= cohort.max_open_per_arm else None


def send_refusal(ex: Exploration, arm_id: str, new_loss: float, held: Mapping[str, float | None]) -> str | None:
    """Why a member's entry losing `new_loss` at its stop (with its round trip) is refused, None when it may go:
    unread, its own cap (its loss + its open risk + the new trade), the weekly cap (the week's loss + every member's
    open risk + the new trade). `held`: open risk at the stops by arm id (`exploration._Pass.held`)."""
    cohort = ex.cfg.cohort if arm_id in members(ex) else None
    if cohort is None or arm_id not in members(ex):
        return None
    lost, week = ex.cohort_loss.get(arm_id), ex.cohort_week_loss
    if not ex.cohort_ok or lost is None or week is None:
        return "cohort_unread"
    open_arm = held.get(arm_id, 0.0)
    open_all = [held.get(a, 0.0) for a in members(ex)]
    if open_arm is None or any(v is None for v in open_all):
        return "open_risk_unknown"
    if lost + open_arm + new_loss >= cohort.arm_loss_cap_gbp - 1e-9:
        return "cohort_loss_cap"
    if week + sum(v or 0.0 for v in open_all) + new_loss >= cohort.week_loss_cap_gbp - 1e-9:
        return "cohort_week_cap"
    return None


def rooms(ex: Exploration, arm_id: str, cost: float, held: Mapping[str, float | None]) -> dict[str, float]:
    """What a member's new trade may lose at its stop under each cohort cap, after its round trip `cost`: the bounds
    `exploration._send` hands the order core, so a stop the RiskGate floors wider is judged again on the loss at the
    gate's stop (as every other room). Empty for an arm outside the cohort; call after `send_refusal` passed."""
    cohort = ex.cfg.cohort if arm_id in members(ex) else None
    if cohort is None or arm_id not in members(ex):
        return {}
    open_all = sum(held.get(a, 0.0) or 0.0 for a in members(ex))
    return {"cohort_loss_cap": cohort.arm_loss_cap_gbp - ex.cohort_loss.get(arm_id, 0.0) - (held.get(arm_id) or 0.0)
            - cost,
            "cohort_week_cap": cohort.week_loss_cap_gbp - (ex.cohort_week_loss or 0.0) - open_all - cost}
