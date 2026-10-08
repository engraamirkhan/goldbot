"""Supervisor: reads each engine's heartbeat/equity file, enforces the combined caps (tighter than the
per-account caps because both engines trade the same instrument) and writes a HALT flag. Engines fail
closed if the supervisor heartbeat is older than 60 s.

The combined caps keep no period state of their own: they sum the engines' day/week-start equity, which each engine
rolls at the risk-day (00:00 UTC) and risk-week (Sunday 00:00 UTC) boundaries, so they roll with the engines."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from goldbot.base import Record
from goldbot.config import RiskSettings


class SupervisorLimits(Record):
    daily_cap: float = 0.015
    weekly_cap: float = 0.04
    dd_stage1: float = 0.08
    dd_stage2: float = 0.12
    heartbeat_max_age_s: int = 60

    @classmethod
    def from_settings(cls, risk: RiskSettings) -> SupervisorLimits:
        return cls(daily_cap=risk.supervisor_daily_cap, weekly_cap=risk.supervisor_weekly_cap,
                   dd_stage1=risk.drawdown_stage1, dd_stage2=risk.drawdown_stage2)


class Supervisor:
    def __init__(self, state_dir: str | Path, limits: SupervisorLimits | None = None):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.limits = limits or SupervisorLimits()
        self.unreadable: list[str] = []

    def read_engines(self) -> list[dict]:
        """Engine state files; one that cannot be parsed is listed in `self.unreadable` (engines write atomically,
        so an unreadable file is a real fault and evaluate() halts on it rather than leaving the engine out)."""
        out = []
        self.unreadable = []
        for f in sorted(self.dir.glob("engine_*.json")):
            try:
                d = json.loads(f.read_text())
                if not isinstance(d, dict):
                    raise ValueError("not an object")
                out.append(d)
            except (ValueError, OSError):
                self.unreadable.append(f.name)
        return out

    def evaluate(self) -> dict:
        engines = self.read_engines()
        eq = sum(e.get("equity", 0.0) for e in engines)
        day0 = sum(e.get("day_start_equity", 0.0) for e in engines)
        wk0 = sum(e.get("week_start_equity", 0.0) for e in engines)
        hwm = sum(e.get("balance_closed_hwm", 0.0) for e in engines)
        day_loss = 1 - eq / day0 if day0 else 0.0
        week_loss = 1 - eq / wk0 if wk0 else 0.0
        dd = 1 - eq / hwm if hwm else 0.0
        reasons = []
        if day_loss >= self.limits.daily_cap:
            reasons.append("combined_daily_cap")
        if week_loss >= self.limits.weekly_cap:
            reasons.append("combined_weekly_cap")
        if dd >= self.limits.dd_stage2:
            reasons.append("combined_drawdown_halt")
        if self.unreadable:
            reasons.append("engine_state_unreadable")     # fail closed: its equity cannot be counted
        stale = [e["account"] for e in engines if time.time() - e.get("ts", 0) > self.limits.heartbeat_max_age_s]
        # combined exposure: engines block entries that would take the sum past the cap (RiskGate)
        exposure = {str(e.get("account")): {"lots": float(e.get("open_lots", 0.0)), "notional": float(e.get("open_notional", 0.0)),
                                            "equity": float(e.get("equity", 0.0))} for e in engines}
        state = {"combined_open_lots": round(sum(x["lots"] for x in exposure.values()), 6),
                 "combined_open_notional": sum(x["notional"] for x in exposure.values()), "exposure": exposure,
                 "ts": time.time(), "halt": bool(reasons), "reasons": reasons, "size_down": dd >= self.limits.dd_stage1,
                 "combined_equity": eq, "day_loss": day_loss, "week_loss": week_loss, "drawdown": dd, "stale_engines": stale,
                 "unreadable_engines": self.unreadable}
        tmp = self.dir / "supervisor.json.tmp"
        tmp.write_text(json.dumps(state))
        os.replace(tmp, self.dir / "supervisor.json")      # engines never read a half-written file
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
        f = Path(state_dir) / "supervisor.json"
        if not f.exists():
            return True, "no_supervisor_heartbeat"
        try:
            s = json.loads(f.read_text())
        except (ValueError, OSError):
            return True, "supervisor_state_unreadable"
        if time.time() - s.get("ts", 0) > max_age_s:
            return True, "supervisor_heartbeat_stale"
        return bool(s.get("halt")), ",".join(s.get("reasons", []))
