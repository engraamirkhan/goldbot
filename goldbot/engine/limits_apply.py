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

ADR 0031 (adaptive limits, lane B): on a demo account with ceilings the controller is `AdaptiveLimitsController`, which
reads the nightly decision (goldbot/ops/limits_job.py) and never writes it. Rules it keeps, quoted:

* "Loosening applies only at the risk-day roll; intraday only tightening": the limits taken at the roll (or at the
  first read of a day) are held; later in the day a stored value only applies when it is lower. A restart the same day
  starts from the limits logged that day (core.db `limits_changes`), so a restart never loosens either;
* a decision for another risk day than today (the clock), a missing or unreadable one: the floors (the defaults);
* "DD ≥ 4% from HWM → floors" (may happen intraday): the engine's confirmed drawdown at 4% or more gives the floors for
  the rest of the day;
* the effective limits are the stricter of the controller and ADR 0032's state: `clamp` keeps them within
  [default .. ceiling] and the 8% revert gives the defaults whatever the controller says. Entries only: nothing here
  gates an exit.
"""
from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from goldbot.data.timeutil import epoch_ns
from goldbot.risk import sizing
from goldbot.risk.adaptive import DD_FLOOR, FIELDS, AdaptiveStore, LimitBounds, Limits, day_of_ns
from goldbot.risk.gate import AccountState, RiskGate, RiskLimits, Stage
from goldbot.risk.limits_profile import (
    DEFAULT_PROFILE,
    LimitsChange,
    LimitsEvidence,
    LimitsPolicy,
    LimitsState,
    LimitsStore,
    RiskLimitsCurrent,
    active_overrides,
)

if TYPE_CHECKING:
    from goldbot.engine.runner import Engine
    from goldbot.ops.accounts import Account

log = logging.getLogger("goldbot.engine")


def stricter(a: Limits, b: Limits) -> Limits:
    """Field by field the stricter of two limit sets; a minimum-lot cap of None ("off") is the strictest."""
    lot = None if a.min_lot_risk_cap is None or b.min_lot_risk_cap is None else min(a.min_lot_risk_cap,
                                                                                     b.min_lot_risk_cap)
    return Limits(daily_cap=min(a.daily_cap, b.daily_cap), weekly_cap=min(a.weekly_cap, b.weekly_cap),
                  supervisor_daily_cap=min(a.supervisor_daily_cap, b.supervisor_daily_cap),
                  supervisor_weekly_cap=min(a.supervisor_weekly_cap, b.supervisor_weekly_cap), min_lot_risk_cap=lot)


def _ns(ts: pd.Timestamp) -> int:
    return int(epoch_ns(pd.DatetimeIndex([ts]))[0])


class AdaptiveLimitsController:
    """ADR 0031 through ADR 0032's seam (`LimitsController`): the nightly decision for today, held through the day and
    only ever lowered within it (see the module docstring). Reads core.db `adaptive_state` at most every RELOAD_S
    seconds and at a new day; any fault reads as no decision (the floors). Writes nothing."""

    RELOAD_S = 60.0

    def __init__(self, state_dir: str | Path, account_id: str, bounds: LimitBounds, *,
                 clock: Callable[[], int] = time.time_ns, monotonic: Callable[[], float] = time.monotonic):
        self.store = AdaptiveStore(state_dir)
        self.account_id = account_id
        self.bounds = bounds
        self.clock = clock
        self.monotonic = monotonic
        self.held: Limits | None = None
        self.held_day: date | None = None
        self.why = ""
        self._seed: tuple[date, Limits] | None = None
        self._read: tuple[float, date, Limits | None, str] | None = None     # (when, for which day, limits, why)

    def seed(self, day: date, limits: Limits) -> None:
        """What the engine applied on `day` before a restart: that day's first read is never above it."""
        self._seed = (day, limits)

    def _stored(self, today: date) -> tuple[Limits | None, str]:
        now = self.monotonic()
        r = self._read
        if r is not None and r[1] == today and now - r[0] < self.RELOAD_S:
            return r[2], r[3]
        out: tuple[Limits | None, str]
        try:
            st = self.store.load(self.account_id)
        except Exception as exc:                   # unreadable: no decision, the floors
            log.warning("adaptive limits unreadable (%s): the floors", type(exc).__name__)
            out = (None, f"adaptive state unreadable ({type(exc).__name__})")
        else:
            if st is None:
                out = (None, "no adaptive decision yet")
            elif st.day > today:
                # the nightly job has decided tomorrow (it runs before the roll) and the row now holds that: tomorrow's
                # applies from tomorrow's roll only; today keeps today's (as read before, or as applied before a
                # restart), else the floors
                kept = r[2] if r is not None and r[1] == today else \
                    self._seed[1] if self._seed is not None and self._seed[0] == today else None
                out = (kept, f"today's decision (tomorrow's, for {st.day.isoformat()}, waits for the roll)") \
                    if kept is not None else (None, f"only tomorrow's decision ({st.day.isoformat()}) is stored")
            elif st.day != today:
                out = (None, f"the adaptive decision is for {st.day.isoformat()}, not today ({today.isoformat()})")
            else:                                  # reading 13: a changed ceiling never raises a stored value
                b = self.bounds
                out = (b.limits_at({f: b.level_of(f, getattr(st.limits, f)) for f in FIELDS}),
                       f"the decision for {st.day.isoformat()}")
        self._read = (now, today, *out)
        return out

    def current(self, account: Account, evidence: LimitsEvidence) -> RiskLimitsCurrent:
        today = day_of_ns(evidence.now_ns if evidence.now_ns is not None else self.clock())
        target, why = self._stored(today)
        if target is None:
            target, why = self.bounds.floors(), f"{why}: the floors"
        if evidence.drawdown is not None and evidence.drawdown >= DD_FLOOR:
            target, why = self.bounds.floors(), f"drawdown {evidence.drawdown:.1%} from the high-water mark: the floors"
        if self.held is None or self.held_day is None or today > self.held_day:     # the roll, or the first read
            if self._seed is not None and self._seed[0] == today:
                target = stricter(target, self._seed[1])                           # a restart never loosens
            self.held, self.held_day, self.why = target, today, why
        else:                                                                      # intraday: only lower
            lowered = stricter(self.held, target)
            if lowered != self.held:
                self.held, self.why = lowered, why
        h = self.held
        ov = active_overrides(account)
        return RiskLimitsCurrent(profile=ov.profile if ov is not None else DEFAULT_PROFILE, daily_cap=h.daily_cap,
                                 weekly_cap=h.weekly_cap, min_lot_risk_cap=h.min_lot_risk_cap, project_open_risk=True,
                                 supervisor_daily_cap=h.supervisor_daily_cap,
                                 supervisor_weekly_cap=h.supervisor_weekly_cap, reason=f"adaptive: {self.why}",
                                 evidence_ids=evidence.evidence_ids)


def _of_current(cur: RiskLimitsCurrent, floor: Limits) -> Limits | None:
    """The adaptive fields of a logged current (a supervisor cap it does not carry is the floor, the strict side);
    None when its values cannot be adaptive limits."""
    try:
        return Limits(daily_cap=cur.daily_cap, weekly_cap=cur.weekly_cap,
                      supervisor_daily_cap=cur.supervisor_daily_cap or floor.supervisor_daily_cap,
                      supervisor_weekly_cap=cur.supervisor_weekly_cap or floor.supervisor_weekly_cap,
                      min_lot_risk_cap=cur.min_lot_risk_cap)
    except ValueError:
        return None


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
        ctl = policy.controller if policy is not None else None
        if isinstance(ctl, AdaptiveLimitsController):
            self._seed(ctl)

    def _seed(self, ctl: AdaptiveLimitsController) -> None:
        """ADR 0031 "intraday only tightening" across a restart: the last limits logged seed the controller (they
        apply only on their own risk day). Unreadable: the floors seed today (the strict side)."""
        try:
            rows = self.store.changes(self.account_id) if self.store is not None else []
        except Exception as exc:
            log.warning("limits change log unreadable (%s): today starts at the floors", type(exc).__name__)
            ctl.seed(day_of_ns(ctl.clock()), ctl.bounds.floors())
            return
        last = _of_current(rows[-1].current, ctl.bounds.floors()) if rows else None
        if rows and last is not None:
            ctl.seed(day_of_ns(_ns(rows[-1].ts_utc)), last)

    def apply(self, eng: Engine, now: pd.Timestamp) -> None:
        """Hand the gate the policy's current limits (every account refresh). A change is tagged on the decisions made
        before it, journalled (`limits_change`), logged and appended to core.db's `limits_changes` (value, reason,
        evidence ids). Without a policy the gate keeps the limits it was built with. The evidence carries the engine's
        clock and its confirmed drawdown from the high-water mark (ADR 0031 reads both)."""
        if self.policy is None:
            return
        st = eng.state
        dd = 1.0 - st.equity / st.balance_closed_hwm \
            if st.equity > 0 and st.balance_closed_hwm > 0 and not st.equity_unconfirmed else None
        lim, cur = self.policy.resolve(LimitsEvidence(reverted=self.reverted, drawdown=dd, now_ns=_ns(now)))
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

    def entry(self, prop: Any = None, gd: Any = None) -> dict[str, Any]:
        """The EntryAudit (and so the closed-trade record) fields of ADR 0032, the profile and the limits at entry, and
        of ADR 0031 (A1): whether the minimum-lot exception sized the order sent (`entry_exception`; the exploration
        budget counts these)."""
        return {"limits_profile": self.current.profile, "limits": self.current.tag(),
                "min_lot_exception": entry_exception(prop, gd)}

    def published(self) -> dict[str, Any]:
        """The engine file's ADR 0032 keys: the current limits and their profile; the supervisor's combined caps follow
        them in lockstep (an engine without a policy publishes none, which keeps the combined caps at their defaults)."""
        if self.policy is None:
            return {}
        return {"limits_current": self.current.model_dump(mode="json"), "limits_reverted": self.reverted}


def entry_exception(prop: Any, gd: Any) -> bool:
    """ADR 0031 (A1): whether the order sent was the minimum-lot exception. The gate decision the order is sent on (the
    re-check at approval, or the auto-mode decision) wins over the proposal card, which was sized earlier: an entry
    that became an exception at the click must count toward the 60-trade budget and the -10R stop."""
    if gd is not None:
        return "min_lot_exception" in (getattr(gd, "reasons", None) or [])
    return bool(getattr(prop, "min_lot_exception", False))


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
