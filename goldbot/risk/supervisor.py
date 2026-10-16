"""Supervisor: reads each engine's heartbeat/equity file, enforces the combined caps (tighter than the
per-account caps because both engines trade the same instrument) and writes a HALT flag. Engines fail
closed if the supervisor heartbeat is older than 60 s, stamped more than a few seconds in the future
(`supervisor_heartbeat_future`: clock skew, a bad write or a restored backup would otherwise read as fresh forever) or
not a finite number (`supervisor_state_unreadable`); `goldbot.data.timeutil.heartbeat_problem`, found 2026-10-16.

The combined caps keep no period state of their own: they sum the engines' day/week-start equity, which each engine
rolls at the risk-day (00:00 UTC) and risk-week (Sunday 00:00 UTC) boundaries, so they roll with the engines.

Currencies (BACKLOG 28, ADR 0004 A8): equities are summed only when every engine reports the same deposit currency.
Engines in different currencies (or one that does not say, next to others) are not combined: the supervisor halts
(`mixed_account_currencies`), failing closed. Converting would need an FX rate the supervisor does not have, and a
stale or wrong rate would silently loosen the combined caps; a halt is visible and safe. Running a GBP and a USD
account together therefore needs a conversion here first (not built)."""
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

from goldbot.base import Record, write_atomic
from goldbot.config import RiskSettings
from goldbot.data.timeutil import heartbeat_problem


class SupervisorLimits(Record):
    daily_cap: float = 0.015
    weekly_cap: float = 0.04
    dd_stage1: float = 0.08
    dd_stage2: float = 0.12
    heartbeat_max_age_s: int = 60
    # BACKLOG 29: an explicit combined day-loss limit (account currency); the effective one is the smallest of this and
    # the per-account limits the engines publish (accounts.yaml). None and none published = off
    daily_loss_limit_abs: float | None = None

    @classmethod
    def from_settings(cls, risk: RiskSettings) -> SupervisorLimits:
        return cls(daily_cap=risk.supervisor_daily_cap, weekly_cap=risk.supervisor_weekly_cap,
                   dd_stage1=risk.drawdown_stage1, dd_stage2=risk.drawdown_stage2)


# the engine-file numbers the supervisor reads: `ts` must be present, the rest may be absent (read as 0 / off) but when
# present must be finite numbers. `daily_loss_limit_abs` may also be null (off)
_REQUIRED_NUMBERS = ("ts",)
_OPTIONAL_NUMBERS = ("equity", "day_start_equity", "week_start_equity", "balance_closed_hwm", "open_lots",
                     "open_notional")
_NULLABLE_NUMBERS = ("daily_loss_limit_abs",)


def _finite_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def engine_fault(e: dict) -> str | None:
    """Why an engine file's numbers cannot be used (a null, a string, a bool, NaN or infinity), None when they can.
    October safety batch, MEDIUM: such a file failed open (a NaN equity made every combined cap False) or crashed
    the supervisor (`None + 0`, `float("x")`), so it is treated exactly as an unparsable file."""
    for k in _REQUIRED_NUMBERS:
        if not _finite_number(e.get(k)):
            return k
    for k in _OPTIONAL_NUMBERS:
        if k in e and not _finite_number(e[k]):
            return k
    for k in _NULLABLE_NUMBERS:
        if e.get(k) is not None and not _finite_number(e[k]):
            return k
    return None


# what an engine start that failed keeps from the last engine file (review LOW-D): the account may still hold
# positions, so the supervisor must keep summing its exposure and money while entries halt
STARTUP_KEEP = ("open_lots", "open_notional", "equity", "day_start_equity", "week_start_equity", "balance_closed_hwm")


def write_startup_error(state_dir: str | Path, account_id: str, why: str, *, dq_checks: list[str],
                        currency: str | None = None) -> None:
    """engine_<account>.json for an engine start that failed (a refused terminal account, an unreadable positions read):
    `startup_error` and `dq_error` MERGED into the previous file, keeping its last-known exposure and equity
    (`STARTUP_KEEP`, finite numbers only), its currency and its absolute daily limit, so the combined caps still count
    this account (review LOW-D). The supervisor halts entries on such a file (`engine_startup_refused`). An unreadable
    or missing previous file gives the error payload alone. No login, server or balance is written."""
    path = Path(state_dir, f"engine_{account_id}.json")
    try:
        prev = json.loads(path.read_text())
    except (OSError, ValueError):
        prev = {}
    prev = prev if isinstance(prev, dict) else {}
    kept: dict[str, Any] = {k: prev[k] for k in STARTUP_KEEP if _finite_number(prev.get(k))}
    if _finite_number(prev.get("daily_loss_limit_abs")):
        kept["daily_loss_limit_abs"] = prev["daily_loss_limit_abs"]
    cur = (currency or "").strip().upper() or _currency(prev)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps({**kept, "account": account_id, "ts": time.time(), "dq_error": True,
                                   "dq_checks": list(dq_checks), "stage": "unknown", "currency": cur,
                                   "startup_error": why[:300]}), durable=False)


def _currency(e: dict) -> str | None:
    """An engine file's deposit currency, upper case; None when it does not say."""
    return str(e.get("currency") or "").strip().upper() or None


class Supervisor:
    def __init__(self, state_dir: str | Path, limits: SupervisorLimits | None = None):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.limits = limits or SupervisorLimits()
        self.unreadable: list[str] = []

    def read_engines(self) -> list[dict]:
        """Engine state files; one that cannot be parsed, or whose numbers are not finite numbers (`engine_fault`),
        is listed in `self.unreadable` (engines write atomically, so an unreadable file is a real fault and evaluate()
        halts on it, `engine_state_unreadable`, rather than leaving the engine out). Its money and exposure are not
        summed: nothing in it can be trusted, and the halt blocks every entry meanwhile."""
        out = []
        self.unreadable = []
        for f in sorted(self.dir.glob("engine_*.json")):
            try:
                d = json.loads(f.read_text())
                if not isinstance(d, dict):
                    raise ValueError("not an object")
                bad = engine_fault(d)
                if bad is not None:
                    raise ValueError(f"{bad} is not a finite number")
                d["_file"] = f.name                     # named in the mixed-currency halt (health, Telegram)
                out.append(d)
            except (ValueError, OSError):
                self.unreadable.append(f.name)
        return out

    def evaluate(self) -> dict:
        engines = self.read_engines()
        # the currency check counts only engines that are running (heartbeat fresh): a file left by an engine that no
        # longer runs must not halt the book (review MEDIUM-1)
        now = time.time()
        fresh = [e for e in engines if now - e["ts"] <= self.limits.heartbeat_max_age_s]
        currencies = {str(e.get("account")): _currency(e) for e in fresh}
        currency_files = {str(e.get("account")): str(e.get("_file", "")) for e in fresh}
        mixed = len(fresh) > 1 and len({c or "?" for c in currencies.values()}) > 1
        # money is summed in ONE currency only (re-verification MEDIUM): a stale engine's equity, day/week start, HWM
        # and published limit count only when its currency equals the fresh engines' single currency. A different or
        # missing one stays out of every money sum (it would dilute the combined caps, the 12% halt and the notional
        # cap), while its open lots and notional stay in the exposure (the strict direction). With no fresh engine the
        # same rule applies to the stale files themselves: their money is summed only when they all report one
        # currency, otherwise no money is summed (lots and notional still are) and `no_fresh_engine` says so
        if fresh:
            kinds = set(currencies.values())
            single = next(iter(kinds)) if len(kinds) == 1 else None
            money = fresh + [e for e in engines if not any(e is f for f in fresh)
                             and single is not None and _currency(e) == single]
        else:
            stale_kinds = {_currency(e) for e in engines}
            one = len(stale_kinds) == 1 and None not in stale_kinds
            money = list(engines) if one else []
        excluded = sorted(str(e.get("_file", "")) for e in engines if not any(e is m for m in money))
        eq = sum(e.get("equity", 0.0) for e in money)
        day0 = sum(e.get("day_start_equity", 0.0) for e in money)
        wk0 = sum(e.get("week_start_equity", 0.0) for e in money)
        hwm = sum(e.get("balance_closed_hwm", 0.0) for e in money)
        day_loss = 1 - eq / day0 if day0 else 0.0
        week_loss = 1 - eq / wk0 if wk0 else 0.0
        dd = 1 - eq / hwm if hwm else 0.0
        reasons = []
        if day_loss >= self.limits.daily_cap:
            reasons.append("combined_daily_cap")
        if week_loss >= self.limits.weekly_cap:
            reasons.append("combined_weekly_cap")
        # the owner's absolute daily limit on the combined loss so far (one currency: mixed currencies halt below)
        day_loss_amount = day0 - eq
        published = [float(e["daily_loss_limit_abs"]) for e in money if e.get("daily_loss_limit_abs") is not None]
        explicit = [self.limits.daily_loss_limit_abs] if self.limits.daily_loss_limit_abs is not None else []
        abs_limit = min(explicit + published) if explicit or published else None
        # halts when the loss REACHES the limit (>=); the gate's projection refuses only when it would EXCEED it
        if abs_limit is not None and day_loss_amount >= abs_limit - 1e-9:
            reasons.append("combined_daily_loss_abs")
        if dd >= self.limits.dd_stage2:
            reasons.append("combined_drawdown_halt")
        if self.unreadable:
            reasons.append("engine_state_unreadable")     # fail closed: its equity cannot be counted
        # an engine whose start failed (review LOW-D): entries halt as for an unreadable file, but its last-known
        # exposure and money (kept by write_startup_error) are still summed, the strict direction for the caps
        refused = sorted(str(e.get("_file", "")) for e in engines if e.get("startup_error"))
        if refused:
            reasons.append("engine_startup_refused")
        if mixed:
            reasons.append("mixed_account_currencies")    # fail closed: GBP and USD equity are never summed
        # an engine file stamped ahead of this clock (engines and supervisor share the box): a clock fault or a bad
        # write, so the file cannot say whether that engine runs. It stays in `fresh` (its currency and money count,
        # the strict direction) and every entry halts until it is rewritten with a sane clock (found 2026-10-16)
        future = sorted(str(e.get("_file", "")) for e in engines if heartbeat_problem(e["ts"], now, math.inf) == "future")
        if future:
            reasons.append("engine_heartbeat_future")
        stale = [e.get("account") for e in engines if time.time() - e["ts"] > self.limits.heartbeat_max_age_s]
        # combined exposure: engines block entries that would take the sum past the cap (RiskGate)
        exposure = {str(e.get("account")): {"lots": float(e.get("open_lots", 0.0)), "notional": float(e.get("open_notional", 0.0)),
                                            # a file kept out of the money adds no equity (the gate's other_equity)
                                            "equity": float(e.get("equity", 0.0)) if any(e is m for m in money) else 0.0}
                    for e in engines}
        state = {"combined_open_lots": round(sum(x["lots"] for x in exposure.values()), 6),
                 "combined_open_notional": sum(x["notional"] for x in exposure.values()), "exposure": exposure,
                 "ts": time.time(), "halt": bool(reasons), "reasons": reasons, "size_down": dd >= self.limits.dd_stage1,
                 "combined_equity": eq, "day_loss": day_loss, "week_loss": week_loss, "drawdown": dd, "stale_engines": stale,
                 "unreadable_engines": self.unreadable, "startup_refused_engines": refused, "future_engines": future,
                 "currencies": currencies, "currency_files": currency_files,
                 "money_excluded": excluded, "no_fresh_engine": bool(engines) and not fresh, "day_loss_amount": day_loss_amount, "daily_loss_limit_abs": abs_limit,
                 # the dashboard's loss budget (goldbot/ops/account_view.py): the combined caps and their (one-currency) bases
                 "daily_cap": self.limits.daily_cap, "combined_day_start_equity": day0,
                 "weekly_cap": self.limits.weekly_cap, "combined_week_start_equity": wk0}
        write_atomic(self.dir / "supervisor.json", json.dumps(state), durable=False)   # heartbeat: never half-written
        return state

    def error_halt(self, exc: BaseException) -> dict:
        """The state published when evaluate() raised: a fresh heartbeat that halts every engine's entries
        (`supervisor_error`), so a supervisor bug fails closed and visibly instead of killing the service (engines
        would then halt only once the heartbeat went stale). Names the exception type, never its message."""
        state = {"ts": time.time(), "halt": True, "reasons": ["supervisor_error"], "error": type(exc).__name__,
                 "unreadable_engines": self.unreadable}
        write_atomic(self.dir / "supervisor.json", json.dumps(state), durable=False)
        return state

    @staticmethod
    def read_state(state_dir: str | Path) -> dict:
        """The last published supervisor state; {} when missing or unreadable (halt checks fail closed separately)."""
        try:
            d = json.loads((Path(state_dir) / "supervisor.json").read_text())
        except (ValueError, OSError):
            return {}
        return d if isinstance(d, dict) else {}

    @staticmethod
    def engine_should_halt(state_dir: str | Path, max_age_s: int = 60) -> tuple[bool, str]:
        """(halt, reasons) as every engine reads the supervisor before an entry. Fails closed on a missing file, an
        unreadable one (or not an object, or a `ts` that is not a finite number), a heartbeat older than `max_age_s`
        and one stamped more than CLOCK_AHEAD_TOLERANCE_S in the future: a future stamp made `now - ts` negative, so
        a dead supervisor whose last write carried a fast clock never halted the engines (found 2026-10-16). A file
        without `ts` reads as the oldest heartbeat (stale), as before."""
        f = Path(state_dir) / "supervisor.json"
        if not f.exists():
            return True, "no_supervisor_heartbeat"
        try:
            s = json.loads(f.read_text())
        except (ValueError, OSError):
            return True, "supervisor_state_unreadable"
        if not isinstance(s, dict):
            return True, "supervisor_state_unreadable"
        problem = heartbeat_problem(s.get("ts", 0), time.time(), max_age_s)
        if problem == "invalid":
            return True, "supervisor_state_unreadable"
        if problem == "future":
            return True, "supervisor_heartbeat_future"
        if problem == "stale":
            return True, "supervisor_heartbeat_stale"
        return bool(s.get("halt")), ",".join(s.get("reasons", []))
