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
from typing import TYPE_CHECKING, Literal, NamedTuple

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


class Safety(FrozenRecord):
    """time-structure 4 (WFRI): "At 26 Fridays: safety check only. Stop if the mean <= -20 bp per Friday or the
    cumulative net <= -10 R." Checked once, at the `at`-th admissible fill (every fill, as the R stop counts them)."""
    at: int = Field(gt=0)
    mean_bp_le: float
    cum_r_le: float


class Fill(NamedTuple):
    """One admissible fill of a unit, in exit order."""
    r: float                                    # net R (pnl / the loss at the gate's stop)
    at: pd.Timestamp                            # its exit
    counted: bool                               # counted toward the read (not void as evidence)
    arm: str                                    # its stratum
    bp: float | None = None                     # net return in basis points of the entry notional


class StopUnit(FrozenRecord):
    """One stop unit: a family (or one arm) whose admissible fills are pooled in exit order."""
    arms: tuple[str, ...] = Field(min_length=1)
    stop_r: float = Field(lt=0)                 # running net R at or below this within `within` fills: stop
    within: int = Field(gt=0)
    futility_at: tuple[int, ...] = ()           # read sizes the futility stop is tested at (none: not registered)
    futility_ub: float = 0.03                   # stop when the one-sided 95% UB of the mean net R is below this
    min_days: int = Field(0, ge=0)              # the read waits until its fills span this many UTC days
    safety: Safety | None = None                # a one-off safety check at a fill count (WFRI, time-structure 4)
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
    try:
        raw = yaml.safe_load(Path(path).read_text()) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: not YAML ({type(exc).__name__})") from exc
    return StopRules.model_validate(raw)


def load_stop_rules(path: str | Path | None = None) -> StopRules:
    """The rules file (cached until it changes on disk). Raises (OSError, ValueError) when it is missing or invalid."""
    p = Path(path if path is not None else RULES_PATH)
    return _load(str(p), p.stat().st_mtime_ns)


def upper_bound(values: Sequence[float], days: Sequence[pd.Timestamp], strata: Sequence[str] | None = None,
                z: float = Z95) -> float | None:
    """One-sided upper bound of the mean: mean + z x SE, SE clustered by UTC day (CR1, G/(G-1)). None below 2 days.
    With `strata` the mean is the equal-weight mean of the strata's means (arms-expansion 2.1 (2): "Each stratum's
    mean is weighted equally (not by fill count) ... The SE comes from the stacked fills, clustered by UTC day across
    all strata, with equal stratum weights"): fill i weighs 1 / (S x n_s), its influence w_i (x_i - mean_s) summed by
    day."""
    n = len(values)
    if n == 0 or n != len(days) or (strata is not None and len(strata) != n):
        return None
    keys = list(strata) if strata is not None else [""] * n
    count: dict[str, int] = {}
    total: dict[str, float] = {}
    for v, k in zip(values, keys, strict=True):
        count[k] = count.get(k, 0) + 1
        total[k] = total.get(k, 0.0) + v
    means = {k: total[k] / count[k] for k in count}
    centre = math.fsum(means.values()) / len(means)
    sums: dict[object, float] = {}
    for v, d, k in zip(values, days, keys, strict=True):
        day = pd.Timestamp(d).tz_convert("UTC").date()
        sums[day] = sums.get(day, 0.0) + (v - means[k]) / (len(means) * count[k])
    g = len(sums)
    if g < 2:
        return None
    var = g / (g - 1) * math.fsum(x * x for x in sums.values())
    return centre + z * math.sqrt(var)


def net_bp(t: ExplorationRecord, oz_per_lot: float = 100.0) -> float | None:
    """A fill's net return in basis points of its entry notional: the broker's net pnl over lots x oz x fill price in
    the account currency (None without the pnl, the fill or the rate)."""
    if t.pnl is None or t.fill_price is None or not t.fx_acct_per_usd or t.lots <= 0:
        return None
    return 1e4 * t.pnl / (t.lots * oz_per_lot * t.fill_price * t.fx_acct_per_usd)


class UnitStop(FrozenRecord):
    """Where a unit stands: its band and, once stopped, the pooled fill count, exit and running R there."""
    band: Literal["running", "within_50pct", "at_stop"] = "running"
    stop_at: int | None = None
    stop_utc: pd.Timestamp | None = None
    stop_r: float | None = None                 # the running R at an R stop; None at a futility stop (a read)
    why: Literal["r", "futility", "safety"] | None = None


def unit_stop(unit: StopUnit, rows: Sequence[Fill], band_share: float = 0.5) -> UnitStop:
    """`rows`: the unit's admissible fills in exit order. The first of its R stop (every fill), its safety check
    (every fill) and its futility reads (counted fills only, strata weighted equally) that fires; else its band."""
    run, hit = 0.0, None
    for i, f in enumerate(rows[:unit.within], 1):
        run += f.r
        if run <= unit.stop_r + 1e-9:
            hit = UnitStop(band="at_stop", stop_at=i, stop_utc=f.at, stop_r=run, why="r")
            break
    sc = unit.safety
    if sc is not None and len(rows) >= sc.at and (hit is None or (hit.stop_at or 0) > sc.at):
        first = rows[:sc.at]
        cum = math.fsum(f.r for f in first)
        bps = [f.bp for f in first if f.bp is not None]
        bad_mean = len(bps) == sc.at and math.fsum(bps) / sc.at <= sc.mean_bp_le + 1e-9
        if bad_mean or cum <= sc.cum_r_le + 1e-9:
            hit = UnitStop(band="at_stop", stop_at=sc.at, stop_utc=first[-1].at, why="safety")
    for size in unit.futility_at:
        read: list[Fill] = []
        seen: set[object] = set()
        for i, f in enumerate(rows, 1):
            if hit is not None and hit.stop_utc is not None and f.at > hit.stop_utc:
                break
            if not f.counted:
                continue
            read.append(f)
            seen.add(f.at.date())
            if len(read) >= size and len(seen) >= unit.min_days:
                ub = upper_bound([x.r for x in read], [x.at for x in read], [x.arm for x in read])
                if ub is not None and ub < unit.futility_ub:
                    hit = UnitStop(band="at_stop", stop_at=i, stop_utc=f.at, why="futility")
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


__all__ = ["COST_SCALE", "RULES_PATH", "CostState", "CostStop", "Fill", "Safety", "StopRules", "StopUnit", "UnitStop",
           "cost_state", "cost_states", "load_stop_rules", "net_bp", "unit_stop", "upper_bound"]
