"""The engine's side of the demo risk limits (ADR 0032; the policy, clamp, revert store and change log are in
goldbot/risk/limits_profile.py). Kept out of engine/runner.py (ADR 0023: runner.py only shrinks): the runner holds one
`EngineLimits` and calls in from `__init__`, `_refresh_account` (after the stage step), `_poll_rearm`, `_execute` (the
entry audit), `flush_journal` (tagging) and `_write_state` (publishing). The published `limits`, the minimum lot's
stop risk and the daily loss allowance are pure functions here for the same reason.

Design rules (ADR 0032, trading-safety conditions):

* the account's limits policy is resolved on every account refresh and handed to the gate; without a policy the gate
  keeps the limits it was built with and the profile is "default";
* "when the account enters the 8% size-down stage, drop back to default limits and stay reverted until the owner
  resets" (also on the 12% halt and the supervisor's combined size-down). The revert is persisted in core.db before the
  gate reads the defaults; an unreadable revert loads as reverted (the defaults);
* the reset is the owner's re-arm (owner role + TOTP) seen while the stage was normal; otherwise, or when it cannot be
  saved, it is refused and journalled;
* every change of the current limits is journalled (`limits_change`), logged and appended to core.db's
  `limits_changes`, and every decision carries the profile it was made under (gate evidence never mixes profiles).
"""
from __future__ import annotations

import logging
import math
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from goldbot.risk import sizing
from goldbot.risk.gate import AccountState, RiskGate, RiskLimits, Stage
from goldbot.risk.limits_profile import (
    DEFAULT_PROFILE,
    LimitsChange,
    LimitsEvidence,
    LimitsPolicy,
    LimitsState,
    LimitsStore,
    RiskLimitsCurrent,
)

if TYPE_CHECKING:
    from goldbot.engine.runner import Engine

log = logging.getLogger("goldbot.engine")


class EngineLimits:
    """One account's current limits profile, its sticky revert and its change log, as the engine applies them."""

    def __init__(self, policy: LimitsPolicy | None, state_dir: str | Path, account_id: str, built: RiskLimits):
        self.policy = policy
        self.account_id = account_id
        self.store = LimitsStore(state_dir) if policy is not None else None
        self.reverted = self.store.load(account_id).reverted if self.store else False
        self.current = RiskLimitsCurrent(profile=DEFAULT_PROFILE, daily_cap=built.daily_cap,
                                         weekly_cap=built.weekly_cap, min_lot_risk_cap=built.min_lot_risk_cap,
                                         project_open_risk=built.project_open_risk, reason="default")
        self.logged = False                          # the first resolve is logged as a change from nothing

    def apply(self, eng: Engine, now: pd.Timestamp) -> None:
        """Hand the gate the policy's current limits (every account refresh). A change is tagged on the decisions made
        before it, journalled (`limits_change`), logged and appended to core.db's `limits_changes` (value, reason,
        evidence ids). Without a policy the gate keeps the limits it was built with."""
        if self.policy is None:
            return
        lim, cur = self.policy.resolve(LimitsEvidence(reverted=self.reverted))
        eng.gate.limits = lim
        if self.logged and cur == self.current:
            return
        prev = self.current.profile if self.logged else None
        self.tag(eng)                                # what was decided so far was decided under the previous limits
        self.current, self.logged = cur, True
        eng.decisions.append({"ts": time.time(), "action": "limits_change", "previous": prev, "profile": cur.profile,
                              "reason": cur.reason, "evidence_ids": list(cur.evidence_ids), **cur.tag().model_dump()})
        log.warning("limits %s -> %s (%s): daily %.4f, weekly %.4f, min-lot cap %s, open risk projected %s", prev,
                    cur.profile, cur.reason, cur.daily_cap, cur.weekly_cap, cur.min_lot_risk_cap, cur.project_open_risk)
        if self.store is not None:
            self.store.log_change(LimitsChange(account_id=self.account_id, ts_utc=now, previous_profile=prev,
                                               current=cur))

    def after_stage(self, eng: Engine, now: pd.Timestamp) -> None:
        """ADR 0032 revert: "when the account enters the 8% size-down stage, drop back to default limits and stay
        reverted until the owner resets". Also on the 12% halt and the supervisor's combined size-down. Persisted
        before the gate reads the defaults; a failed write keeps the revert in memory (logged by the store)."""
        st = eng.state
        if self.policy is not None and self.policy.has_overrides and not self.reverted \
                and (st.stage in (Stage.SIZE_DOWN, Stage.HALTED) or st.combined_size_down):
            self.reverted = True
            why = "combined size-down" if st.stage == Stage.NORMAL else f"stage {Stage(st.stage).value}"
            if self.store is not None:
                self.store.save(LimitsState(account_id=self.account_id, reverted=True, since_utc=now, reason=why))
            eng.decisions.append({"ts": time.time(), "action": "limits_reverted", "reason": why})
        self.apply(eng, now)

    def reset(self, eng: Engine, now: pd.Timestamp, *, by: str | None, rearm_id: str | None, normal: bool) -> None:
        """The owner's reset of the revert: a re-arm (owner role + TOTP) seen while the stage was normal (no size-down,
        no halt, no combined size-down). Otherwise refused and journalled; the owner re-arms again later. A reset that
        cannot be saved is refused too (a restart would otherwise find the revert again, the safe side, but say so)."""
        if not normal:
            eng.decisions.append({"ts": time.time(), "action": "limits_reset_refused", "by": by, "rearm_id": rearm_id,
                                  "reason": "stage not normal"})
            return
        if self.store is not None and not self.store.save(
                LimitsState(account_id=self.account_id, reverted=False, since_utc=now, reason="owner re-arm")):
            eng.decisions.append({"ts": time.time(), "action": "limits_reset_refused", "by": by, "rearm_id": rearm_id,
                                  "reason": "not saved"})
            return
        self.reverted = False
        eng.decisions.append({"ts": time.time(), "action": "limits_reset", "by": by, "rearm_id": rearm_id})
        self.apply(eng, now)

    def tag(self, eng: Engine) -> None:
        """Every decision not yet journalled carries the limits profile it was made under (gate evidence never mixes
        profiles)."""
        for d in eng.decisions[eng._journaled:]:
            d.setdefault("limits_profile", self.current.profile)

    def entry(self) -> dict[str, Any]:
        """The EntryAudit (and so the closed-trade record) fields of ADR 0032: the profile and the limits at entry."""
        return {"limits_profile": self.current.profile, "limits": self.current.tag()}

    def published(self) -> dict[str, Any]:
        """The engine file's ADR 0032 keys: the current limits and their profile; the supervisor's combined caps follow
        them in lockstep (an engine without a policy publishes none, which keeps the combined caps at their defaults)."""
        if self.policy is None:
            return {}
        return {"limits_current": self.current.model_dump(mode="json"), "limits_reverted": self.reverted}


def published_limits(lim: RiskLimits, min_lot_risk: float | None) -> dict[str, Any]:
    """`limits` in engine_<account>.json, the RiskGate limits the dashboard's account view reads (loss budget,
    ladder): risk per trade and its maximum, multiplier bounds, daily and weekly caps; the account's absolute daily
    loss limit (accounts.yaml through ops.run.engine_limits, account currency; null when off), the minimum-lot
    exception cap (null when off, always null in tiny-live) and the minimum lot's stop risk in the account currency
    at the latest ATR stop (null when unknown)."""
    mlr = min_lot_risk
    return {"risk_per_trade": lim.risk_per_trade, "max_risk_per_trade": lim.max_risk_per_trade,
            "multiplier_bounds": list(lim.multiplier_bounds), "daily_cap": lim.daily_cap,
            "weekly_cap": lim.weekly_cap, "daily_loss_limit_abs": lim.daily_loss_limit_abs,
            "min_lot_risk_cap": lim.min_lot_risk_cap,
            # BACKLOG 19/35: the open-risk heat cap and whether the daily projection counts open risk (both off by default)
            "heat": lim.heat.model_dump(), "project_open_risk": lim.project_open_risk,
            "min_lot_risk_account": round(mlr, 2) if mlr is not None and math.isfinite(mlr) else None}


def min_lot_risk(terms: dict[str, Any], stop_distance: float) -> float | None:
    """The minimum lot's stop risk in the account currency at the latest ATR stop (volume_min x stop x contract x
    fx), published as `limits.min_lot_risk_account`; None when the terms or the conversion are unknown."""
    fx = sizing.account_fx(str(terms.get("account_currency", "")), terms.get("acct_per_usd"))
    vmin, contract = terms.get("volume_min"), terms.get("contract_oz")
    if fx is None or vmin is None or contract is None or not (math.isfinite(stop_distance) and stop_distance > 0):
        return None
    return float(vmin) * stop_distance * float(contract) * fx


def daily_allowance(gate: RiskGate, st: AccountState) -> dict[str, float | None]:
    """min(daily cap x day-start equity, risk.daily_loss_limit_abs) and that limit less today's loss so far, for the
    engine state and health; None while unknown (no day start yet and no absolute limit)."""
    limit = gate.daily_loss_limit(st)
    if not math.isfinite(limit):
        return {"daily_loss_limit": None, "daily_loss_remaining": None}
    lost = max(0.0, st.day_start_equity - st.equity) if st.day_start_equity > 0 else 0.0
    return {"daily_loss_limit": round(limit, 2), "daily_loss_remaining": round(max(0.0, limit - lost), 2)}
