"""Candidate generator for the continuous research loop (docs/plans/continuous-research-loop.md, "Loop grid v1"; owner,
2026-10-18: "back test strategies on multiple time frames in parallel ... continue doing it indefinitely").

DESIGN "Bounded research loop": "bounded feature and parameter search, not discovery of novel strategies". So this is
a fixed, pre-declared grid, no model and no LLM. For every registered family that is neither under its pre-registered
screen (`Specialist.screening`: the loop never runs or varies a screen) nor in `research.retired_families`, and for each
timeframe the family allows, one dimension at a time moved off the default (never combined):
* the exit settings `target_atr`, `stop_atr`, `max_bars` at 75% and 125% of the timeframe's default (each through
  `population.check_founder_config`, so a survivor can later be spawned as a founder);
* `session_filter` (asia, london, newyork) on the 15m and 1h timeframes only (a session says nothing on 4h or 1d);
* `regime_filter` (low_vol, not_high_vol, trending, ranging) on every timeframe: point-in-time features of the decision
  bar (`specialists.loop_filters`).
`grid` is that set, a pure function of the settings; `grid_members` its (family, config hash) pairs, frozen as GRID_V1. `research_pass`
refuses any `rule_ref=loop` job that is not a member (`loop_refusal`).

The only pre-filters, none of which reads a return: the configuration (its full effective config hash, as
`research_pass` records it) is not already a trial in the registry. There is NO sizing pre-filter (owner/quant ruling
2026-10-18): every loop trial runs with `--lot-fit`, so whether the configuration fits the account is reported with its
result, and RiskGate still refuses what cannot be sized at execution.

Order: families by the research plan's share of the budget (largest first, then name), round-robin across families, so
a short batch spans families and timeframes. A pure function of (registry rows, settings, plan): same inputs, same
list. When nothing is left the grid is exhausted (`research_queue.py` says so); that is reported, never silent. The
search window ends where the held-out year starts (`Candidate.window_to`); the search never reads it.

Budget: a batch is at most `research.loop_trials_per_run` (a loop budget separate from `trial_budget_quarter`, ADR
0029). Every trial it runs is still a registry row, so the deflated Sharpe counts the full running K."""
from __future__ import annotations

import json
from itertools import zip_longest
from pathlib import Path
from typing import Any

from goldbot.base import FrozenRecord
from goldbot.config import Settings
from goldbot.research.director import ResearchPlan, later_preregistration, pending_preregistration
from goldbot.research.population import SpawnRefused, check_founder_config, founder_base
from goldbot.research.registry import LOOP_RULE_REF, config_hash, is_trial, quarter_of
from goldbot.specialists import SPECIALISTS
from goldbot.specialists.base import TIMEFRAME_KEY
from goldbot.specialists.loop_filters import FILTER_KEYS, REGIME_FILTER, REGIMES, SESSION_FILTER, SESSIONS

EXIT_KEYS = ("target_atr", "stop_atr", "max_bars")     # the numeric exit settings the grid moves
GRID_STEPS = (0.75, 1.25)                              # +-25% of the timeframe's default (DESIGN: bounded search)

# Loop grid v1, frozen (docs/plans/continuous-research-loop.md "never a silent edit"): the (family, config hash)
# pairs `grid_members` computes today. `loop_refusal` checks against this, not the computed set; a new specialist,
# an un-retired family or an edited default changes the computed grid, fails tests/test_research_generator.py and
# needs a new dated grid version (GRID_V2) in the plan, never an edit here.
GRID_V1: frozenset[tuple[str, str]] = frozenset({
    ("tsmom", "1c787d000a00"),
    ("tsmom", "1d724544cd69"),
    ("tsmom", "22485e6307fd"),
    ("tsmom", "23655a291dbd"),
    ("tsmom", "2ddd0fe33968"),
    ("tsmom", "2ecb8c6af93c"),
    ("tsmom", "2f1891403cb1"),
    ("tsmom", "323d9e230bd5"),
    ("tsmom", "3796868a2f03"),
    ("tsmom", "3930af6b2a5d"),
    ("tsmom", "5645b2af4294"),
    ("tsmom", "5a0fc09328b3"),
    ("tsmom", "5de96511309c"),
    ("tsmom", "5e4cd684825f"),
    ("tsmom", "5ea375684769"),
    ("tsmom", "677fc43a6818"),
    ("tsmom", "789f199c8e6f"),
    ("tsmom", "7d417d935828"),
    ("tsmom", "814d6703543e"),
    ("tsmom", "87d73acc6194"),
    ("tsmom", "8f71acf7c993"),
    ("tsmom", "9127c45681bd"),
    ("tsmom", "931f837482b1"),
    ("tsmom", "9396bdab4ca9"),
    ("tsmom", "a4bc9f017ee6"),
    ("tsmom", "b62398d2c7bc"),
    ("tsmom", "ba75413ac2a8"),
    ("tsmom", "c68c8101d05b"),
    ("tsmom", "c7bed6ff5cc1"),
    ("tsmom", "cea78634c6f1"),
    ("tsmom", "e2b460d0b150"),
    ("tsmom", "e3704b7b5a2f"),
    ("tsmom", "f7e5383cb4e6"),
    ("tsmom", "f9a276bc709e"),
    ("tsmom", "fbecfe1397de"),
    ("tsmom", "fc1eacdc2f2b"),
})
SESSION_TFS = ("15m", "1h")                          # a session filter means something only on intraday bars
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


def _grid(family: str, tf: str) -> list[dict[str, Any]]:
    """The overrides of one (family, timeframe): the default, then one dimension at a time."""
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
    if tf in SESSION_TFS:
        out += [{**own, SESSION_FILTER: s} for s in SESSIONS]
    out += [{**own, REGIME_FILTER: r} for r in REGIMES]
    return out


def _eligible(settings: Settings) -> list[str]:
    retired = {r.family for r in settings.research.retired_families}
    return [f for f in sorted(SPECIALISTS) if not SPECIALISTS[f].screening and f not in retired]


def _window_to(settings: Settings) -> str:
    window = settings.research.holdout_window()
    return window[0].date().isoformat() if window else f"{FROM_YEAR + 100}-01-01"


def _family_grid(family: str, window_to: str) -> list[Candidate]:
    cls = SPECIALISTS[family]
    out: list[Candidate] = []
    seen: set[str] = set()
    for tf in (cls.timeframe, *cls.timeframes):
        for ov in _grid(family, tf):
            exits = {k: v for k, v in ov.items() if k not in FILTER_KEYS}
            try:
                check_founder_config(family, exits)
                config = cls(**ov).config
            except (SpawnRefused, ValueError):
                continue
            h = config_hash(config)
            if h in seen:
                continue
            seen.add(h)
            out.append(Candidate(family=family, timeframe=tf, overrides=ov, config_hash=h, window_to=window_to,
                                 rationale=f"research loop grid v1: {family} {tf} {ov or 'defaults'}"))
    return out


def grid(settings: Settings) -> list[Candidate]:
    """The whole loop grid (see the module docstring), tried or not: the membership `research_pass` checks."""
    window_to = _window_to(settings)
    return [c for f in _eligible(settings) for c in _family_grid(f, window_to)]


def grid_members(settings: Settings) -> set[tuple[str, str]]:
    return {(c.family, c.config_hash) for c in grid(settings)}


def generate(rows: list[dict[str, Any]], settings: Settings, plan: ResearchPlan) -> list[Candidate]:
    """Grid members not yet tried (no trial row of the same family and config hash), in plan order (see the module
    docstring)."""
    window_to = _window_to(settings)
    tried = {(r.get("family"), r.get("config_hash")) for r in rows if is_trial(r)}
    per_family = [[c for c in _family_grid(f, window_to) if (c.family, c.config_hash) not in tried]
                  for f in sorted(_eligible(settings), key=lambda f: (-plan.budget.get(f, 0), f))]
    return [c for group in zip_longest(*per_family) for c in group if c is not None]


def batch(cands: list[Candidate], remaining: int) -> list[dict[str, Any]]:
    """The first `remaining` candidates as research.yml dispatch inputs (one trial each); none when remaining <= 0."""
    return [{"specialist": c.family, "variants": json.dumps([c.overrides]), "rationale": c.rationale,
             "from_year": str(FROM_YEAR), "to_year": c.window_to[:4], "rule_ref": LOOP_RULE_REF}
            for c in cands[:max(remaining, 0)]]


def grid_sizes(cands: list[Candidate], timeframes: tuple[str, ...]) -> dict[str, int]:
    """How many candidates there are on each timeframe, 0 for one with none: the queue prints it for the whole grid and
    for what is left of it, so an empty timeframe is visible."""
    return {tf: sum(c.timeframe == tf for c in cands) for tf in timeframes}


def loop_refusal(rows: list[dict[str, Any]], jobs: list[tuple[str, dict[str, Any]]], settings: Settings, *,
                 score_holdout: bool = False, discover: bool = False, skip_screen: bool = False,
                 lot_fit: bool = True) -> str | None:
    """Why a run citing `rule_ref=loop` is refused, or None (ADR 0029). The loop spends its own budget, so these are
    the checks the quarterly budget would otherwise make: the run has at most `research.loop_trials_per_run` jobs
    (0 turns the loop off); no job's config is in the registry already (no double dispatch of one trial); no family is
    retired or under its screen; no config is one a pre-registration queued (it must run against its own row); and a
    loop run never scores the holdout nor runs a discovery (both stay pre-registered, owner-gated trials). A job's (family, config hash) must be a member of `GRID_V1`, the frozen `grid` (the
    loop varies nothing the grid did not declare), a loop run never skips the screen and always runs `--lot-fit`."""
    res = settings.research
    if res.loop_trials_per_run <= 0:
        return "research loop: research.loop_trials_per_run is 0 (the loop is off)"
    if len(jobs) > res.loop_trials_per_run:
        return f"research loop: {len(jobs)} trials but research.loop_trials_per_run is {res.loop_trials_per_run}"
    if score_holdout:
        return "research loop: a loop run never scores the holdout (a pre-registered, spent-once trial)"
    if discover:
        return "research loop: a loop run never runs a discovery (a pre-registered trial charged at K_eff)"
    if skip_screen:
        return "research loop: a loop run never skips the screen"
    if not lot_fit:
        return "research loop: a loop run needs --lot-fit (account fit is reported with every loop trial)"
    members = GRID_V1
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
        if (family, h) not in members:
            return f"research loop: {family} config {h} is not a member of the loop grid"
        if (family, h) in tried:
            return f"research loop: {family} config {h} is already in the registry (no double dispatch of one trial)"
        if pending_preregistration(rows, quarter, family, h) or later_preregistration(rows, quarter, family, h):
            return f"research loop: {family} config {h} is pre-registered; it runs against its own row, not the loop"
    return None
