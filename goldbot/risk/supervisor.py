"""Supervisor: reads each engine's heartbeat/equity file, enforces the combined caps (tighter than the
per-account caps because both engines trade the same instrument) and writes a HALT flag. Engines fail
closed if the supervisor heartbeat is older than 60 s."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SupervisorLimits:
    daily_cap: float = 0.015
    weekly_cap: float = 0.04
    dd_stage1: float = 0.08
    dd_stage2: float = 0.12
    heartbeat_max_age_s: int = 60


class Supervisor:
    def __init__(self, state_dir: str | Path, limits: SupervisorLimits | None = None):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.limits = limits or SupervisorLimits()

    def read_engines(self) -> list[dict]:
        out = []
        for f in self.dir.glob("engine_*.json"):
            try:
                out.append(json.loads(f.read_text()))
            except json.JSONDecodeError:
                continue
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
        stale = [e["account"] for e in engines if time.time() - e.get("ts", 0) > self.limits.heartbeat_max_age_s]
        state = {"ts": time.time(), "halt": bool(reasons), "reasons": reasons, "size_down": dd >= self.limits.dd_stage1,
                 "combined_equity": eq, "day_loss": day_loss, "week_loss": week_loss, "drawdown": dd, "stale_engines": stale}
        (self.dir / "supervisor.json").write_text(json.dumps(state))
        return state

    @staticmethod
    def engine_should_halt(state_dir: str | Path, max_age_s: int = 60) -> tuple[bool, str]:
        f = Path(state_dir) / "supervisor.json"
        if not f.exists():
            return True, "no_supervisor_heartbeat"
        s = json.loads(f.read_text())
        if time.time() - s.get("ts", 0) > max_age_s:
            return True, "supervisor_heartbeat_stale"
        return bool(s.get("halt")), ",".join(s.get("reasons", []))
