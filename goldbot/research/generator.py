"""Candidate generator for the continuous research loop (docs/plans/continuous-research-loop.md, phase 1; owner,
2026-10-18: "back test strategies on multiple time frames in parallel ... continue doing it indefinitely").

DESIGN "Bounded research loop": "bounded feature and parameter search, not discovery of novel strategies". So this is
a fixed grid, no model and no LLM: every registered family x each timeframe it allows x its exit settings
(`target_atr`, `stop_atr`, `max_bars`) moved one at a time to 75% and 125% of the timeframe's default, and each
candidate must pass `population.check_founder_config` (so any survivor can later be spawned as a founder).

Cheap pre-filters, none of which reads a return (so they cost no trial):
* the configuration (its full effective config hash, as `research_pass` records it) is not in the registry already;
* the family is not under its pre-registered screen (`Specialist.screening`) and is not in `research.retired_families`;
* 0.01 lot must not risk more than 1.2 x the target at `research.loop_equity_usd` (design "Sizing"): ATR only, and a
  timeframe with no ATR is dropped (fail closed).

Order: families by the research plan's share of the budget (largest first, then name), round-robin across families, so
a short batch spans families and timeframes. A pure function of (registry rows, settings, plan, ATR): same inputs, same
list. The search window ends where the held-out year starts (`Candidate.window_to`); the search never reads it.

Budget: a batch is at most `research.loop_trials_per_run` (a loop budget separate from `trial_budget_quarter`). Every
trial it runs is still a registry row, so the deflated Sharpe counts the full running K."""
from __future__ import annotations

import json
from collections.abc import Mapping
from itertools import zip_longest
from pathlib import Path
from typing import Any

from goldbot.base import FrozenRecord
from goldbot.config import Settings
from goldbot.execution.costs import CONTRACT_OZ
from goldbot.research.director import ResearchPlan, later_preregistration, pending_preregistration
from goldbot.research.population import SpawnRefused, check_founder_config, founder_base
from goldbot.research.registry import LOOP_RULE_REF, config_hash, is_trial, quarter_of
from goldbot.risk.sizing import MIN_LOT_TOLERANCE
from goldbot.specialists import SPECIALISTS
from goldbot.specialists.base import TIMEFRAME_KEY

EXIT_KEYS = ("target_atr", "stop_atr", "max_bars")     # the numeric exit settings the grid moves
GRID_STEPS = (0.75, 1.25)                              # +-25% of the timeframe's default (DESIGN: bounded search)
MIN_LOT = 0.01
FROM_YEAR = 2010                                       # the research window starts with the published bars


class Candidate(FrozenRecord):
    family: str
    timeframe: str
    overrides: dict[str, Any]       # research_pass --variants entry; the registry hashes the effective config
    config_hash: str                # of the effective config, as recorded in the registry
    window_to: str                  # ISO date the search window ends (exclusive): the holdout's first day
    rationale: str


def read_registry(path: str | Path) -> list[dict[str, Any]]:
    """The registry rows. A file that does not exist is an empty registry (research-v1 has no registry yet); a line
    that is not a JSON object raises ValueError, so an unreadable registry never yields a batch of 'new' trials."""
    p = Path(path)
    if not p.exists():
        return []
    rows: list[dict[str, Any]] = []
    for n, line in enumerate(p.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{p}:{n} is not JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{p}:{n} is not a registry row")
        rows.append(row)
    return rows


def sizing_feasible(*, stop_atr: float, atr_usd: float, equity_usd: float, risk_fraction: float) -> bool:
    """The minimum lot (0.01 = 1 oz) risks the stop distance in dollars; the gate takes it while that is at most
    MIN_LOT_TOLERANCE x the target risk (goldbot/risk/sizing.py). ATR only: no returns."""
    min_lot_risk = MIN_LOT * CONTRACT_OZ * stop_atr * atr_usd
    return min_lot_risk <= MIN_LOT_TOLERANCE * equity_usd * risk_fraction * (1 + 1e-12)


def _grid(family: str, tf: str) -> list[dict[str, Any]]:
    cls = SPECIALISTS[family]
    base = founder_base(family, tf)
    own: dict[str, Any] = {} if tf == cls.timeframe else {TIMEFRAME_KEY: tf}
    out = [dict(own)]
    for key in EXIT_KEYS:
        b = base.get(key)
        if isinstance(b, bool) or not isinstance(b, (int, float)):
            continue
        for step in GRID_STEPS:
            v: float = b * step
            v = max(1, round(v)) if isinstance(b, int) else round(v, 4)
            if v != b:
                out.append({**own, key: v})
    return out


def generate(rows: list[dict[str, Any]], settings: Settings, plan: ResearchPlan,
             atr: Mapping[str, float]) -> list[Candidate]:
    """Candidates not yet tried, in plan order (see the module docstring). `atr` maps a timeframe to its ATR in $/oz."""
    res = settings.research
    window = res.holdout_window()
    window_to = (window[0].date().isoformat() if window else f"{FROM_YEAR + 100}-01-01")
    retired = {r.family for r in res.retired_families}
    tried = {(r.get("family"), r.get("config_hash")) for r in rows}
    risk_fraction = settings.risk.risk_per_trade
    per_family: list[list[Candidate]] = []
    for family in sorted(SPECIALISTS, key=lambda f: (-plan.budget.get(f, 0), f)):
        cls = SPECIALISTS[family]
        if cls.screening or family in retired:
            continue
        mine: list[Candidate] = []
        for tf in (cls.timeframe, *cls.timeframes):
            for ov in _grid(family, tf):
                try:
                    check_founder_config(family, ov)
                    config = cls(**ov).config
                except (SpawnRefused, ValueError):
                    continue
                h = config_hash(config)
                if (family, h) in tried:
                    continue
                atr_tf = config.get("atr_tf") or tf
                if atr_tf not in atr or not sizing_feasible(
                        stop_atr=float(config.get("stop_atr", 1.5)), atr_usd=float(atr[atr_tf]),
                        equity_usd=res.loop_equity_usd, risk_fraction=risk_fraction):
                    continue
                tried.add((family, h))
                mine.append(Candidate(family=family, timeframe=tf, overrides=ov, config_hash=h, window_to=window_to,
                                      rationale=f"research loop grid: {family} {tf} {ov or 'defaults'}"))
        per_family.append(mine)
    return [c for group in zip_longest(*per_family) for c in group if c is not None]


def batch(cands: list[Candidate], remaining: int) -> list[dict[str, Any]]:
    """The first `remaining` candidates as research.yml dispatch inputs (one trial each); none when remaining <= 0."""
    return [{"specialist": c.family, "variants": json.dumps([c.overrides]), "rationale": c.rationale,
             "from_year": str(FROM_YEAR), "to_year": c.window_to[:4], "rule_ref": LOOP_RULE_REF}
            for c in cands[:max(remaining, 0)]]


def survivors_by_timeframe(cands: list[Candidate], timeframes: tuple[str, ...]) -> dict[str, int]:
    """How many candidates survived the pre-filters on each timeframe, 0 for one nothing survived on: the report that
    shows whether the sizing filter has emptied a timeframe (quant review of phase 1)."""
    return {tf: sum(c.timeframe == tf for c in cands) for tf in timeframes}


def loop_refusal(rows: list[dict[str, Any]], jobs: list[tuple[str, dict[str, Any]]], settings: Settings, *,
                 score_holdout: bool = False, discover: bool = False) -> str | None:
    """Why a run citing `rule_ref=loop` is refused, or None (ADR 0029). The loop spends its own budget, so these are
    the checks the quarterly budget would otherwise make: the run has at most `research.loop_trials_per_run` jobs
    (0 turns the loop off); no job's config is in the registry already (no double dispatch of one trial); no family is
    retired or under its screen; no config is one a pre-registration queued (it must run against its own row); and a
    loop run never scores the holdout nor runs a discovery (both stay pre-registered, owner-gated trials)."""
    res = settings.research
    if res.loop_trials_per_run <= 0:
        return "research loop: research.loop_trials_per_run is 0 (the loop is off)"
    if len(jobs) > res.loop_trials_per_run:
        return f"research loop: {len(jobs)} trials but research.loop_trials_per_run is {res.loop_trials_per_run}"
    if score_holdout:
        return "research loop: a loop run never scores the holdout (a pre-registered, spent-once trial)"
    if discover:
        return "research loop: a loop run never runs a discovery (a pre-registered trial charged at K_eff)"
    retired = {r.family for r in res.retired_families}
    tried = {(r.get("family"), r.get("config_hash")) for r in rows if is_trial(r)}
    quarter = quarter_of()
    for family, config in jobs:
        h = config_hash(config)
        if family in retired:
            return f"research loop: {family} is retired"
        cls = SPECIALISTS.get(family)
        if cls is None or cls.screening:
            return f"research loop: {family} is unknown or still under its pre-registered screen"
        if (family, h) in tried:
            return f"research loop: {family} config {h} is already in the registry (no double dispatch of one trial)"
        if pending_preregistration(rows, quarter, family, h) or later_preregistration(rows, quarter, family, h):
            return f"research loop: {family} config {h} is pre-registered; it runs against its own row, not the loop"
    return None
