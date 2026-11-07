"""The novel-techniques entry refusals (signed plan 2026-11-07, WS-6; docs/research/prereg-2026-11-07-n3-har.md),
registered in `entry_refusals.REFUSALS` and reached only through its hook (the entry branch of `exploration._send`):
kill-only, fail closed, shadowed through the untaken-candidate path. Exits, stops, targets, holds and the RiskGate are
untouched.

Scope (`in_scope`): gold (XAUUSD) signals of `role: learning` arms and of the controls read only against learning arms
(`exploration_budget.pairs`: the LC* twins, ZCD). Strategy, cost and owner-experiment arms and shared controls (S0, S0R,
F1C, G1C, GS1C) are outside it: "learning arms and their controls only".

* `learning_icm_only` (N-1a): "Gold learning arms run on ICM" (allocation rule). An in-scope signal on an engine whose
  broker is not ICM is refused. Today no such arm is active (Z6V/Z7V/ZCV retired under A5 on 2026-11-06); the guard
  keeps a future registration from breaking the routing. Vantage gold cost probes never pass `_send`.
* `har_unavailable` (N-3, trading-safety objection 1: "A missing or stale HAR row gives har_unavailable plus an alert
  (fails closed)"): once the nightly job has written any row available at the decision time, an in-scope signal whose
  opportunity day has no `ok` row available then is refused, with a warning once a day. Before the job's first row
  nothing is refused ("If the nightly HAR job has not written a row ... N-3 is not registered"); after an `off` row
  (research.har_abstention.enabled false, the kill path) nothing is refused either.
* `har_low_range` (N-3): the day's row says low (forecast <= the trailing 250 forecasts' 20 % quantile): refused.

The decision time is the signal bar's close (`Pending.bar_ts`), its day the opportunity day rolling at 22:00 UTC. An
unreadable table raises, and the hook refuses as `refusal_error:<name>`.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

import pandas as pd

from goldbot.instruments import GOLD
from goldbot.research import har_abstention as har

if TYPE_CHECKING:
    from goldbot.engine.exploration import Arm, Exploration, Pending
    from goldbot.engine.runner import Engine

log = logging.getLogger(__name__)

LEARNING_ICM_ONLY = "learning_icm_only"
HAR_UNAVAILABLE = "har_unavailable"
HAR_LOW_RANGE = "har_low_range"
LEARNING_BROKER = "icm"
_warned: set[tuple[str, date]] = set()          # (account, day) whose har_unavailable was logged


def _ex(eng: Engine) -> Exploration:
    """The engine's exploration state; its absence raises, so the hook refuses (fail closed)."""
    if eng.exploration is None:
        raise RuntimeError("no exploration state")
    return eng.exploration


def in_scope(eng: Engine, arm: Arm) -> bool:
    """A gold learning arm, or a gold control whose paired arms (`exploration_budget.pairs`) are all learning arms."""
    if arm.instrument != GOLD:
        return False
    if arm.spec.role == "learning":
        return True
    if arm.spec.role != "control":
        return False
    from goldbot.engine.exploration_budget import pairs
    arms = _ex(eng).arms
    served = [a for a in pairs(arms).get(arm.arm_id, ()) if a in arms]
    return bool(served) and all(arms[a].spec.role == "learning" for a in served)


def _decision(s: Pending) -> pd.Timestamp:
    t = pd.Timestamp(s.bar_ts)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def learning_icm_only(eng: Engine, arm: Arm, s: Pending) -> bool:
    """N-1a: an in-scope signal on a non-ICM engine is refused."""
    return eng.cfg.broker_name != LEARNING_BROKER and in_scope(eng, arm)


def _today(eng: Engine, s: Pending) -> tuple[bool, har.HarRow | None]:
    """(registered, the signal day's `ok` row): registered once a row of any status is available and the latest is
    not `off`."""
    at = _decision(s)
    db = _ex(eng).db
    latest = har.latest_row(db, at)
    if latest is None or latest.status == "off":
        return False, None
    return True, har.row_for(db, har.signal_day(at), at)


def har_unavailable(eng: Engine, arm: Arm, s: Pending) -> bool:
    """N-3, fail closed: registered and no `ok` row for the signal's opportunity day available at its decision."""
    if not in_scope(eng, arm):
        return False
    registered, row = _today(eng, s)
    if not registered or row is not None:
        return False
    key = (str(getattr(eng.cfg, "account_id", "")), har.signal_day(_decision(s)))
    if key not in _warned:
        _warned.add(key)
        log.warning("har_days has no row for %s on %s: learning entries refused (har_unavailable)", key[1], key[0])
    return True


def har_low_range(eng: Engine, arm: Arm, s: Pending) -> bool:
    """N-3: the signal's opportunity day is a predicted bottom-quintile range day."""
    if not in_scope(eng, arm):
        return False
    registered, row = _today(eng, s)
    return registered and row is not None and row.low
