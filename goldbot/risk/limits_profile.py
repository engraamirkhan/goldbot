"""Demo risk limits under the owner's delegation (ADR 0032): per-account ceilings, a controller seam, the revert.

The owner's words (2026-10-18): "approve demo risk limits: daily cap £50, min-lot cap 1%, weekly 10%, demo only".
Rules this module enforces (trading-safety conditions, ADR 0032):

* the approved values are CEILINGS, never targets: `risk_overrides.ceilings` in config/accounts.yaml, accepted only on
  a `mode: demo` account (goldbot/ops/accounts.py refuses them elsewhere) and never above `DEMO_ENVELOPE`;
* "the controller may only ever return <= defaults" on any account that is not demo with ceilings: `clamp` makes that
  true whatever a controller returns, so a future adaptive controller cannot widen live or tiny-live limits;
* on a demo account with ceilings every limit is clamped to [default .. ceiling]; the absolute daily loss limit
  (ADR 0004, £50) is not a ceiling here and is never raised: the gate takes min(daily_cap x equity, it);
* the 8% size-down stage reverts the account to the default limits, sticky until the owner resets it
  (`LimitsStore`, core.db; the engine's re-arm path), whatever the controller says;
* every record carries the profile and the values it was decided under (`LimitsTag`), so gate evidence under the demo
  profile is never mixed with default-profile evidence;
* ADR 0039: the per-strategy risk per trade (`strategy_risk`, with its why in `risk_terms`) is clamped the same way:
  on a demo account with ceilings within [0.25%, the 1% per-trade hard limit], anywhere else at most the default;
  reverted, none (the default for every strategy). A value that is not a finite number > 0 is dropped (the default).

`StaticController` returns the ceilings, so behaviour is exactly what the owner approved. ADR 0031's adaptive controller
plugs in through `LimitsController` (engine/limits_apply.py `AdaptiveLimitsController`, chosen by
ops/services/engine.py `engine_limits_policy`); no adaptive logic lives here, and `clamp` bounds whatever it returns.
"""
from __future__ import annotations

import logging
import math
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord, UtcTimestamp, load_record, load_record_json
from goldbot.data.timeutil import epoch_ns
from goldbot.db.connection import Database, connect, write
from goldbot.db.repo import RecordRepo
from goldbot.risk.risk_terms import RISK_MIN, RiskTerm

if TYPE_CHECKING:
    from goldbot.config import RiskSettings
    from goldbot.ops.accounts import Account
    from goldbot.ops.gates_phase import ClosedTrade
    from goldbot.risk.gate import RiskLimits

log = logging.getLogger(__name__)

DEFAULT_PROFILE = "default"
#: the core.db write runs on the tick thread: a locked file gives up after this; the in-memory revert still holds
WRITE_BUSY_TIMEOUT_MS = 500


class RiskCeilings(FrozenRecord):
    """The most a demo account's limits may be raised to (fractions of equity). None = that limit stays at its
    default. The field bounds are the owner's approval of 2026-10-18 (`DEMO_ENVELOPE`): raising one needs a new
    owner decision and ADR, never only a config edit."""

    daily: float | None = Field(None, gt=0, le=0.05)
    weekly: float | None = Field(None, gt=0, le=0.10)
    min_lot_risk_cap: float | None = Field(None, gt=0, le=0.01)


#: "daily cap £50 (5% of £1,000), min-lot cap 1%, weekly 10%, demo only" (the owner, 2026-10-18)
DEMO_ENVELOPE = RiskCeilings(daily=0.05, weekly=0.10, min_lot_risk_cap=0.01)
#: the most the supervisor's COMBINED (daily, weekly) caps may rise to: the CFO's envelope, deliberately below the
#: per-account ceilings (trading-safety review of 156cfbf, HIGH), so the combined cap still binds before an account's
SUPERVISOR_DEMO_ENVELOPE = (0.0375, 0.075)
#: records closed before this carry no limits_profile and all traded the defaults (no release before it had
#: overrides); an untagged record closed after it (an adopted orphan, a lost audit) is not default-profile evidence
ADR0032_CUTOVER = pd.Timestamp("2026-10-18", tz="UTC")


class RiskOverrides(FrozenRecord):
    """`risk_overrides` of one account in config/accounts.yaml. `profile` names the evidence these limits produce
    (`demo-<approval date>`); the open positions' stop risk always counts toward the absolute daily limit under it."""

    profile: str = Field(pattern=r"^demo-\d{4}-\d{2}-\d{2}$")
    ceilings: RiskCeilings


class LimitsTag(FrozenRecord):
    """The limits a record was decided under, next to its `limits_profile`."""

    daily_cap: float
    weekly_cap: float
    min_lot_risk_cap: float | None = None
    project_open_risk: bool = False


class RiskLimitsCurrent(FrozenRecord):
    """What a controller says the account's limits are now, with why and on what evidence."""

    profile: str
    daily_cap: float
    weekly_cap: float
    min_lot_risk_cap: float | None = None
    project_open_risk: bool = False
    reason: str
    evidence_ids: tuple[str, ...] = ()
    # ADR 0031: the adaptive supervisor caps this account's controller decided (None: none decided). The supervisor
    # takes the smaller of them and the account caps, within its own [default .. envelope]: they only tighten
    supervisor_daily_cap: float | None = None
    supervisor_weekly_cap: float | None = None
    # ADR 0039: per model version, the risk per trade (fraction of equity) and why; a version not in it trades the
    # default risk per trade. Empty: today's fixed values
    strategy_risk: dict[str, float] = {}
    risk_terms: dict[str, RiskTerm] = {}
    # trading-safety review 2026-10-19 (MEDIUM): the engine's lowest same-day scale of the risk day (k_vol x k_dd x
    # k_day, engine/limits_apply.py `DayRisk`), so the change log holds it and a restart the same day keeps the cut.
    # `clamp` keeps it in (0, 1]; 1 = no cut
    risk_scale: float = 1.0
    # ...and (LOW) the nightly decision's account-level volatility regime (`AdaptiveState.vol_high`: the 20-day median
    # ATR15 at or above its 1-year p90), so the vol cut applies with no strategy terms and at the floors; None = unknown
    vol_high: bool | None = None

    def tag(self) -> LimitsTag:
        return LimitsTag(daily_cap=self.daily_cap, weekly_cap=self.weekly_cap, min_lot_risk_cap=self.min_lot_risk_cap,
                         project_open_risk=self.project_open_risk)


class LimitsEvidence(FrozenRecord):
    """What the controller may decide on. `reverted`: the 8% revert holds (the defaults are forced, see `clamp`).
    `drawdown`: from the high-water mark, fraction, None while unknown or unconfirmed; `now_ns`: the engine's clock
    (epoch ns), None = the controller's own (ADR 0031 adaptive controller; the static one reads neither)."""

    reverted: bool = False
    evidence_ids: tuple[str, ...] = ()
    drawdown: float | None = None
    now_ns: int | None = None


class LimitsController(Protocol):
    """The seam an adaptive controller plugs into. Whatever it returns passes `clamp` before the gate reads it."""

    def current(self, account: Account, evidence: LimitsEvidence) -> RiskLimitsCurrent: ...


def _defaults(defaults: RiskLimits, reason: str, evidence: LimitsEvidence) -> RiskLimitsCurrent:
    return RiskLimitsCurrent(profile=DEFAULT_PROFILE, daily_cap=defaults.daily_cap, weekly_cap=defaults.weekly_cap,
                             min_lot_risk_cap=defaults.min_lot_risk_cap, project_open_risk=defaults.project_open_risk,
                             reason=reason, evidence_ids=evidence.evidence_ids)


def active_overrides(account: Account) -> RiskOverrides | None:
    """The account's overrides when they may apply: exactly `mode == "demo"`; anything else (live, a missing or unknown
    mode on a record built outside load_accounts) gets the defaults."""
    ov = getattr(account, "risk_overrides", None)
    return ov if getattr(account, "mode", None) == "demo" and isinstance(ov, RiskOverrides) else None


class StaticController:
    """Returns the owner's ceilings on a demo account with overrides (not reverted), else the defaults."""

    def __init__(self, defaults: RiskLimits):
        self.defaults = defaults

    def current(self, account: Account, evidence: LimitsEvidence) -> RiskLimitsCurrent:
        ov = active_overrides(account)
        if ov is None:
            return _defaults(self.defaults, "default", evidence)
        if evidence.reverted:
            return _defaults(self.defaults, "reverted", evidence)
        c, d = ov.ceilings, self.defaults
        return RiskLimitsCurrent(profile=ov.profile, daily_cap=c.daily if c.daily is not None else d.daily_cap,
                                 weekly_cap=c.weekly if c.weekly is not None else d.weekly_cap,
                                 min_lot_risk_cap=c.min_lot_risk_cap if c.min_lot_risk_cap is not None
                                 else d.min_lot_risk_cap, project_open_risk=True,
                                 reason="static: the owner's ceilings", evidence_ids=evidence.evidence_ids)


def _cap(value: float, floor: float, ceiling: float) -> float:
    """`value` within [floor, ceiling]; a value that is not a finite number > 0 is the default (`floor` on a demo
    account with ceilings, `ceiling` = the default elsewhere): a controller fault never widens anything."""
    if not (isinstance(value, (int, float)) and math.isfinite(value) and value > 0):
        return ceiling if floor == 0.0 else floor
    return min(max(float(value), floor), ceiling)


def _bounded(value: float | None, floor: float | None, ceiling: float | None) -> float | None:
    """The minimum-lot cap within [floor, ceiling], where None is "off": a None ceiling keeps it off, a None value
    or one that is not a finite number > 0 is the floor."""
    if ceiling is None:
        return None
    if value is None or not (math.isfinite(value) and value > 0):
        return floor
    return min(max(value, floor if floor is not None else 0.0), ceiling)


def _scale(value: float) -> float:
    """The same-day scale as a cut in (0, 1]; anything else (above 1, not a finite number > 0) is 1, no cut: the
    engine recomputes today's factors at its next refresh."""
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 < value <= 1
    return float(value) if ok else 1.0


def _strategy_risk(risk: dict[str, float], floor: float, ceiling: float) -> dict[str, float]:
    """Each value within [floor, ceiling]; one that is not a finite number > 0 is dropped (its strategy trades the
    default)."""
    return {s: min(max(float(r), floor), ceiling) for s, r in risk.items()
            if isinstance(r, (int, float)) and math.isfinite(r) and r > 0}


def _terms(terms: dict[str, RiskTerm], risk: dict[str, float]) -> dict[str, RiskTerm]:
    """The published why, with `r` = the clamped value (a term without a clamped risk is dropped). A term may arrive as
    its dump (a dict, e.g. read back from a journal): it is validated into a RiskTerm, and an invalid one is dropped."""
    out: dict[str, RiskTerm] = {}
    for s, t in terms.items():
        if s not in risk:
            continue
        try:
            term = t if isinstance(t, RiskTerm) else load_record(
                RiskTerm, {"reason": "", **dict(t), "r": risk[s]}, source="limits risk_terms")
        except Exception:                       # noqa: BLE001 - an unreadable why is dropped, the clamped risk stands
            continue
        out[s] = term.model_copy(update={"r": risk[s]})
    return out


def clamp(cur: RiskLimitsCurrent, defaults: RiskLimits, account: Account, evidence: LimitsEvidence) -> RiskLimitsCurrent:
    """The controller's answer made safe: reverted -> the defaults; a demo account with overrides -> each limit within
    [default .. ceiling] and open risk projected; any other account -> each limit at most its default (never raised).
    The profile is derived here, never trusted: `default` unless some limit is above its default."""
    ov = None if evidence.reverted else active_overrides(account)
    if evidence.reverted:            # the vol regime is a fact, not a limit: it keeps cutting under the defaults
        return _defaults(defaults, "reverted", evidence).model_copy(update={"vol_high": cur.vol_high})
    base = defaults.risk_per_trade
    if ov is None:
        risk = _strategy_risk(cur.strategy_risk, 0.0, base)     # no overrides (live): tighten only, never raised to a floor
        daily = _cap(cur.daily_cap, 0.0, defaults.daily_cap)
        weekly = _cap(cur.weekly_cap, 0.0, defaults.weekly_cap)
        mlr = _bounded(cur.min_lot_risk_cap, None, defaults.min_lot_risk_cap)
        return cur.model_copy(update={"profile": DEFAULT_PROFILE, "daily_cap": daily, "weekly_cap": weekly,
                                      "min_lot_risk_cap": mlr, "supervisor_daily_cap": None,
                                      "supervisor_weekly_cap": None, "strategy_risk": risk,
                                      "risk_terms": _terms(cur.risk_terms, risk), "risk_scale": _scale(cur.risk_scale),
                                      "project_open_risk": cur.project_open_risk or defaults.project_open_risk})
    c = ov.ceilings
    daily = _cap(cur.daily_cap, defaults.daily_cap, max(defaults.daily_cap, c.daily or defaults.daily_cap))
    weekly = _cap(cur.weekly_cap, defaults.weekly_cap, max(defaults.weekly_cap, c.weekly or defaults.weekly_cap))
    mlr_ceiling = c.min_lot_risk_cap if c.min_lot_risk_cap is not None else defaults.min_lot_risk_cap
    if mlr_ceiling is not None and defaults.min_lot_risk_cap is not None:
        mlr_ceiling = max(mlr_ceiling, defaults.min_lot_risk_cap)
    mlr = _bounded(cur.min_lot_risk_cap, defaults.min_lot_risk_cap, mlr_ceiling)
    risk = _strategy_risk(cur.strategy_risk, min(RISK_MIN, base), max(base, defaults.max_risk_per_trade))
    raised = daily > defaults.daily_cap or weekly > defaults.weekly_cap or \
        (mlr is not None and (defaults.min_lot_risk_cap is None or mlr > defaults.min_lot_risk_cap)) or \
        any(r > base + 1e-12 for r in risk.values())
    return cur.model_copy(update={"profile": ov.profile if raised else DEFAULT_PROFILE, "daily_cap": daily,
                                  "weekly_cap": weekly, "min_lot_risk_cap": mlr, "project_open_risk": True,
                                  "strategy_risk": risk, "risk_terms": _terms(cur.risk_terms, risk),
                                  "risk_scale": _scale(cur.risk_scale)})


def apply(defaults: RiskLimits, cur: RiskLimitsCurrent) -> RiskLimits:
    """The gate's limits: the defaults with the current caps (everything else, the absolute daily limit included, is
    the defaults'). ADR 0039: the per-strategy risk goes to `RiskLimits.strategy_risk` once the gate has that field
    (lane B, gate.py); until then the gate sizes every strategy at the default, today's behaviour."""
    up: dict[str, object] = {"daily_cap": cur.daily_cap, "weekly_cap": cur.weekly_cap,
                             "min_lot_risk_cap": cur.min_lot_risk_cap, "project_open_risk": cur.project_open_risk}
    if "strategy_risk" in type(defaults).model_fields:
        up["strategy_risk"] = dict(cur.strategy_risk)
    return defaults.model_copy(update=up)


class LimitsPolicy:
    """One account's limits: its defaults (settings.yaml `risk:` through ops.run.engine_limits), the controller and the
    clamp. `resolve` is what the engine reads every account refresh."""

    def __init__(self, account: Account, defaults: RiskLimits, controller: LimitsController | None = None):
        self.account = account
        self.defaults = defaults
        self.controller: LimitsController = controller or StaticController(defaults)

    @property
    def has_overrides(self) -> bool:
        return active_overrides(self.account) is not None

    def resolve(self, evidence: LimitsEvidence) -> tuple[RiskLimits, RiskLimitsCurrent]:
        try:
            raw = self.controller.current(self.account, evidence)
        except Exception as exc:        # a controller fault never widens anything: the defaults
            log.error("limits controller failed (%s): default limits", type(exc).__name__)
            raw = _defaults(self.defaults, f"controller_error:{type(exc).__name__}", evidence)
        cur = clamp(raw, self.defaults, self.account, evidence)
        return apply(self.defaults, cur), cur


# ---------------------------------------------------------------------------------------------- the state store
class LimitsState(FrozenRecord):
    """One account's revert (core.db `limits_state`): set when the account enters the 8% size-down stage under raised
    limits, cleared only by the owner's re-arm (TOTP) while the stage is normal."""

    account_id: str
    reverted: bool
    since_utc: UtcTimestamp | None = None
    reason: str = ""


class LimitsChange(FrozenRecord):
    """One change of an account's current limits (core.db `limits_changes`, append-only)."""

    account_id: str
    ts_utc: UtcTimestamp
    previous_profile: str | None = None
    current: RiskLimitsCurrent


def _ns(ts: pd.Timestamp) -> int:
    return int(epoch_ns(pd.DatetimeIndex([ts]))[0])


STATE: RecordRepo[LimitsState] = RecordRepo(
    "limits_state", LimitsState, key="account_id", tolerant=True,
    columns={"account_id": lambda s: s.account_id,
             "ts_ns": lambda s: _ns(s.since_utc) if s.since_utc is not None else 0})


class LimitsStore:
    """The engine's handle on core.db for the revert and the change log. Fails to the strict side: a revert that
    cannot be read loads as reverted (the defaults), a write that fails keeps the in-memory state (logged)."""

    def __init__(self, state_dir: str | Path):
        self.state_dir = Path(state_dir)
        self.db: Database | None = None
        self._open()

    def _open(self) -> Database | None:
        try:
            self.db = Database.open(self.state_dir, "core")
        except Exception as exc:
            log.warning("core.db unavailable for the limits revert (%s): retried on the next write", type(exc).__name__)
        return self.db

    def load(self, account_id: str) -> LimitsState:
        """The stored revert; reverted (the strict side) when core.db or the row cannot be read; not reverted when
        confirmed absent (no row)."""
        db = self.db or self._open()
        if db is None:
            return LimitsState(account_id=account_id, reverted=True, reason="limits state unreadable")
        try:
            with db.connection() as conn:
                row = STATE.get(conn, account_id)
        except Exception as exc:
            log.warning("limits revert unreadable (%s): default limits", type(exc).__name__)
            return LimitsState(account_id=account_id, reverted=True, reason="limits state unreadable")
        return row if row is not None else LimitsState(account_id=account_id, reverted=False)

    def _write(self, fn: str, *rows: object) -> bool:
        db = self.db or self._open()
        if db is None:
            return False
        try:
            conn = connect(db.path, "core", busy_timeout_ms=WRITE_BUSY_TIMEOUT_MS)
            try:
                with write(conn):
                    if fn == "state":
                        for r in rows:
                            assert isinstance(r, LimitsState)
                            STATE.upsert(conn, r)
                    else:
                        for r in rows:
                            assert isinstance(r, LimitsChange)
                            conn.execute("INSERT INTO limits_changes (account_id, ts_ns, body) VALUES (?, ?, ?)",
                                         (r.account_id, _ns(r.ts_utc), r.model_dump_json()))
            finally:
                conn.close()
            return True
        except (sqlite3.Error, OSError, AssertionError) as exc:
            log.error("limits %s not saved (%s)", fn, type(exc).__name__)
            return False

    def save(self, state: LimitsState) -> bool:
        return self._write("state", state)

    def log_change(self, change: LimitsChange) -> bool:
        return self._write("change", change)

    def changes(self, account_id: str) -> list[LimitsChange]:
        db = self.db or self._open()
        if db is None:
            return []
        with db.connection() as conn:
            rows = conn.execute("SELECT body FROM limits_changes WHERE account_id = ? ORDER BY id",
                                (account_id,)).fetchall()
        return [load_record_json(LimitsChange, r[0], source="db:limits_changes") for r in rows]


def supervisor_ceilings(accounts: list[Account]) -> RiskCeilings | None:
    """The combined caps may rise only when every enabled account is demo with overrides: the smallest of the CFO
    envelope (`SUPERVISOR_DEMO_ENVELOPE`) and each account's ceiling, per limit (a limit some account leaves at its
    default stays at the default). None otherwise."""
    enabled = [a for a in accounts if a.enabled]
    ovs = [active_overrides(a) for a in enabled]
    if not enabled or any(o is None for o in ovs):
        return None
    cs = [o.ceilings for o in ovs if o is not None]
    daily = [c.daily for c in cs]
    weekly = [c.weekly for c in cs]
    env_daily, env_weekly = SUPERVISOR_DEMO_ENVELOPE
    return RiskCeilings(
        daily=None if any(v is None for v in daily) else min([env_daily] + [v for v in daily if v is not None]),
        weekly=None if any(v is None for v in weekly) else min([env_weekly] + [v for v in weekly if v is not None]))


def counts_as_default_evidence(t: ClosedTrade, risk: RiskSettings) -> bool:
    """ADR 0032: a closed trade counts as paper evidence for the phase gates only when it is a demo trade decided under
    the default limits: tagged `default` (and, when its limits are recorded, none above settings.yaml `risk:`), or
    untagged and closed before `ADR0032_CUTOVER`. A demo-profile trade, and an untagged one after the cutover, never
    count. The stop rule still reads every trade."""
    if t.mode != "demo":
        return False
    if t.limits_profile is None:
        return t.exit_utc < ADR0032_CUTOVER
    if t.limits_profile != DEFAULT_PROFILE:
        return False
    lim = t.limits
    if lim is None:
        return True
    if lim.daily_cap > risk.daily_cap + 1e-12 or lim.weekly_cap > risk.weekly_cap + 1e-12:
        return False
    if lim.min_lot_risk_cap is not None:
        return risk.min_lot_risk_cap is not None and lim.min_lot_risk_cap <= risk.min_lot_risk_cap + 1e-12
    return True
