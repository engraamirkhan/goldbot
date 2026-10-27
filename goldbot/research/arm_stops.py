"""The exploration arms' pre-registered stop rules as data (config/arm_stops.yaml): per family where the
pre-registration reads per family, per arm where H-61 does.

The rules, as registered and built here:

* H-61 (prereg-2026-10-25-high-volume.md "Per-arm stop (permanent)"): "running net R in exit order reaches -30 R
  within its first 300 trades (scalp) / -15 R within 100 (intraday) / -10 R within 40 (swing)". Unchanged, by the
  arm's style (telegram.labs ARM_STOP); every arm in no unit gets it, H-80's BXR arms included ("unchanged").
* W1 (arms-expansion-2026-10-26.md 2.1): "Per-family stop: -30 R within 300 fills"; "a family continues or stops as a
  whole". A `unit` pools its arms' admissible fills in exit order across every account and broker.
* W2 (arms-2026-10-25.md 8.0) and Z (prereg-2026-10-27-scalp-volume.md 4): the same, "or for futility at 300 (the
  one-sided 95% upper bound of the mean < +0.03 R)", SE clustered by UTC day (CR1); Z's read waits for ">= 20 session
  days". Futility is a read, so it uses the read's sample: evidence exclusions are left out (Amendment 4: void trades
  "still count toward H-61 stops", not toward the read); the R stop counts every admissible fill.
* F1C/G1C "stop with" F1A/G1A (prereg-2026-10-26-time-structure.md 2.2, 3.2); every other control never stops.
* Z 4 "Cost stop per stratum (independent of R). At >= 30 fills, if the 95% UB of signal-time cost per R is > 0.10,
  the stratum's share is halved (ADR 0047 ladder). Above 0.20 it is suspended until a dated amendment."

A stop only refuses an arm's NEW entries (aux.db `exploration_arm_status`, written by the daily review, read by the
engine) and a halved share only lowers its room for new entries: nothing here reads or changes an open position.
"""
from __future__ import annotations

import math
import sqlite3
from collections.abc import Collection, Sequence
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import pandas as pd
import yaml
from pydantic import Field, model_validator

from goldbot.base import FrozenRecord

if TYPE_CHECKING:
    from goldbot.engine.exploration import ExplorationRecord

RULES_PATH = Path(__file__).parents[2] / "config" / "arm_stops.yaml"
Z95 = 1.6448536269514722            # the one-sided 95% normal quantile
CostState = Literal["ok", "halved", "suspended"]
COST_SCALE: dict[str, float] = {"ok": 1.0, "halved": 0.5, "suspended": 0.0}


class StopUnit(FrozenRecord):
    """One stop unit: a family (or one arm) whose admissible fills are pooled in exit order."""
    arms: tuple[str, ...] = Field(min_length=1)
    stop_r: float = Field(lt=0)                 # running net R at or below this within `within` fills: stop
    within: int = Field(gt=0)
    futility_at: tuple[int, ...] = ()           # read sizes the futility stop is tested at (none: not registered)
    futility_ub: float = 0.03                   # stop when the one-sided 95% UB of the mean net R is below this
    min_days: int = Field(0, ge=0)              # the read waits until its fills span this many UTC days
    source: str


class CostStop(FrozenRecord):
    """Z 4's per-stratum cost stop."""
    min_fills: int = Field(30, gt=1)
    halve_above: float = Field(0.10, gt=0)
    suspend_above: float = Field(0.20, gt=0)
    arms: tuple[str, ...] = ()


class StopRules(FrozenRecord):
    units: dict[str, StopUnit] = Field(default_factory=dict)
    follows: dict[str, str] = Field(default_factory=dict)       # control -> the arm it stops with
    h61: tuple[str, ...] = ()                                   # documentation: the per-style default
    none: tuple[str, ...] = ()                                  # documentation: never stopped
    cost_stop: CostStop = CostStop()

    @model_validator(mode="after")
    def _once(self) -> StopRules:
        seen: dict[str, str] = {}
        for name, u in self.units.items():
            for a in u.arms:
                if a in seen:
                    raise ValueError(f"arm {a} is in stop units {seen[a]} and {name}")
                seen[a] = name
        for f, lead in self.follows.items():
            if f in seen or lead not in seen:
                raise ValueError(f"follower {f} must be in no unit and follow a unit's arm (not {lead})")
        return self

    def unit_of(self, arm_id: str) -> str | None:
        return next((n for n, u in self.units.items() if arm_id in u.arms), None)


@lru_cache(maxsize=4)
def _load(path: str, mtime_ns: int) -> StopRules:
    return StopRules.model_validate(yaml.safe_load(Path(path).read_text()) or {})


def load_stop_rules(path: str | Path = RULES_PATH) -> StopRules:
    """The rules file (cached until it changes on disk)."""
    p = Path(path)
    return _load(str(p), p.stat().st_mtime_ns)


def upper_bound(values: Sequence[float], days: Sequence[pd.Timestamp], z: float = Z95) -> float | None:
    """One-sided upper bound of the mean: mean + z x SE, SE clustered by UTC day (CR1, G/(G-1)). None below 2 days."""
    n = len(values)
    if n == 0 or n != len(days):
        return None
    mean = math.fsum(values) / n
    sums: dict[object, float] = {}
    for v, d in zip(values, days, strict=True):
        key = pd.Timestamp(d).tz_convert("UTC").date()
        sums[key] = sums.get(key, 0.0) + (v - mean)
    g = len(sums)
    if g < 2:
        return None
    var = g / (g - 1) * math.fsum(s * s for s in sums.values()) / (n * n)
    return mean + z * math.sqrt(var)


class UnitStop(FrozenRecord):
    """Where a unit stands: its band and, once stopped, the pooled fill count, exit and running R there."""
    band: Literal["running", "within_50pct", "at_stop"] = "running"
    stop_at: int | None = None
    stop_utc: pd.Timestamp | None = None
    stop_r: float | None = None                 # the running R at an R stop; None at a futility stop (a read)
    why: Literal["r", "futility"] | None = None


def unit_stop(unit: StopUnit, rows: Sequence[tuple[float, pd.Timestamp, bool]], band_share: float = 0.5) -> UnitStop:
    """`rows`: the unit's admissible fills in exit order as (net R, exit, counted toward the read). The first of its R
    stop (every fill) and its futility reads (counted fills only) that fires; else its band."""
    run, hit = 0.0, None
    for i, (r, at, _) in enumerate(rows[:unit.within], 1):
        run += r
        if run <= unit.stop_r + 1e-9:
            hit = UnitStop(band="at_stop", stop_at=i, stop_utc=at, stop_r=run, why="r")
            break
    for size in unit.futility_at:
        vals: list[float] = []
        days: list[pd.Timestamp] = []
        for i, (r, at, counted) in enumerate(rows, 1):
            if hit is not None and hit.stop_utc is not None and at > hit.stop_utc:
                break
            if not counted:
                continue
            vals.append(r)
            days.append(at)
            if len(vals) >= size and len({d.date() for d in days}) >= unit.min_days:
                ub = upper_bound(vals, days)
                if ub is not None and ub < unit.futility_ub:
                    hit = UnitStop(band="at_stop", stop_at=i, stop_utc=at, why="futility")
                break                               # one read at this size: the first point it is due
    if hit is not None:
        return hit
    if len(rows) <= unit.within and run <= band_share * unit.stop_r + 1e-9:
        return UnitStop(band="within_50pct")
    return UnitStop()


def cost_state(rows: Sequence[ExplorationRecord], cfg: CostStop) -> CostState:
    """Z 4 for one stratum: its filled orders' signal-time cost per R (`cost` / `stop_loss` at the send)."""
    pts = [(t.cost / t.stop_loss, t.sent_utc) for t in rows
           if t.fill_price is not None and t.cost is not None and t.stop_loss and t.stop_loss > 0]
    if len(pts) < cfg.min_fills:
        return "ok"
    ub = upper_bound([c for c, _ in pts], [d for _, d in pts])
    if ub is None:
        return "ok"
    return "suspended" if ub > cfg.suspend_above else "halved" if ub > cfg.halve_above else "ok"


def cost_states(conn: sqlite3.Connection, arms: Collection[str], *, until: pd.Timestamp | None = None,
                rules: StopRules | None = None) -> dict[str, CostState]:
    """The cost state of every registered cost-stop stratum among `arms` (its fills on every account), sent before
    `until` when given."""
    from goldbot.engine.exploration import LEDGER
    rules = rules if rules is not None else load_stop_rules()
    want = sorted(set(arms) & set(rules.cost_stop.arms))
    if not want:
        return {}
    marks = ",".join("?" * len(want))
    cut, args = ("", tuple(want)) if until is None else (" AND ts_ns < ?", (*want, int(pd.Timestamp(until).value)))
    by_arm: dict[str, list[ExplorationRecord]] = {a: [] for a in want}
    for t in LEDGER.select(conn, f"arm_id IN ({marks}){cut}", args):
        by_arm[t.arm_id].append(t)
    return {a: cost_state(rows, rules.cost_stop) for a, rows in by_arm.items()}


__all__ = ["COST_SCALE", "RULES_PATH", "CostState", "CostStop", "StopRules", "StopUnit", "UnitStop", "cost_state",
           "cost_states", "load_stop_rules", "unit_stop", "upper_bound"]
