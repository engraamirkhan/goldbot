"""Trial registry: every evaluated variant is recorded with its config hash, fold results and a global
trial counter. The counter feeds the deflated-Sharpe correction, so the more we search the higher
the bar. Stored as JSON lines so it is diffable and survives without a database server.

Research discipline (docs/proposals/2026-10-design-improvements.md, P2):
* a quarterly budget of pre-registered trials: `check_budget` refuses a run that would exceed it; trials recorded with
  `budget_quarter` count against that quarter (rows from before the budget existed do not);
* a held-out window is scored at most once per configuration (`holdout_scored`, rows with status "holdout");
* the population's shadow -> live promotion needs a research trial of the configuration that passed the design's
  gates (`passed_gates`)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class TrialBudgetExceeded(ValueError):
    """The quarter's pre-registered trial budget would be exceeded."""


def config_hash(config: dict) -> str:
    return hashlib.sha1(json.dumps(config, sort_keys=True, default=str).encode()).hexdigest()[:12]


def quarter_of(ts: datetime | None = None) -> str:
    ts = ts or datetime.now(timezone.utc)
    return f"{ts.year}Q{(ts.month - 1) // 3 + 1}"


class TrialRegistry:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("")

    def _rows(self) -> list[dict]:
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    @property
    def n_trials(self) -> int:
        return len(self._rows())

    def record(self, *, agent_id: str, family: str, config: dict, feature_version: str, rationale: str,
               results: dict, status: str = "evaluated", budget_quarter: str | None = None) -> dict:
        row: dict[str, Any] = {
            "trial": self.n_trials + 1,
            "ts": datetime.now(timezone.utc).isoformat(),
            "agent_id": agent_id, "family": family, "config_hash": config_hash(config), "config": config,
            "feature_version": feature_version, "rationale": rationale, "results": results, "status": status,
        }
        if budget_quarter is not None:
            row["budget_quarter"] = budget_quarter
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
        return row

    def by_family(self, family: str) -> list[dict]:
        return [r for r in self._rows() if r["family"] == family]

    def best(self, family: str, key: str = "sharpe_ann") -> dict | None:
        rows = [r for r in self.by_family(family) if key in r.get("results", {})]
        return max(rows, key=lambda r: r["results"][key]) if rows else None

    # ------------------------------------------------------------------ research discipline
    def budget_used(self, quarter: str) -> int:
        return sum(1 for r in self._rows() if r.get("budget_quarter") == quarter)

    def check_budget(self, n_new: int, cap: int, now: datetime | None = None) -> str:
        """Raise TrialBudgetExceeded if `n_new` more trials would exceed this quarter's cap; returns the quarter."""
        q = quarter_of(now)
        used = self.budget_used(q)
        if used + n_new > cap:
            raise TrialBudgetExceeded(
                f"trial budget exceeded: {q} allows {cap} pre-registered trials, {used} already run, {n_new} requested. "
                f"Every trial raises the deflated-Sharpe bar for all later ones; pre-register fewer variants, wait for "
                f"next quarter, or raise research.trial_budget_quarter in config/settings.yaml (an owner decision).")
        return q

    def holdout_scored(self, family: str, config: dict) -> bool:
        h = config_hash(config)
        return any(r.get("status") == "holdout" and r.get("family") == family and r.get("config_hash") == h
                   for r in self._rows())

    def passed_gates(self, family: str, config: dict) -> bool:
        """A walk-forward trial (not a holdout scoring) of exactly this configuration passed the design's gates."""
        h = config_hash(config)
        return any(r.get("status") == "evaluated" and r.get("family") == family and r.get("config_hash") == h
                   and ((r.get("results") or {}).get("gates") or {}).get("passed") is True for r in self._rows())
