"""ADR 0085 power-first rule, for research and demo promotion only (the owner's phase gate is pinned, see
`registry.phase_gate_refusal`).

The ruling (quant-reviewer, 2026-11-15, on docs/research/power-first-gates-2026-11.md): "a card starts only with
>= 0.5 power at every gate it must pass and >= 0.25 joint power; else a 'NOT STARTED (power)' registry row with no K
and no data read." Its conditions, each a part of this module:
(a) "min edge = max(0.10 R, the edge the GBP 50/day goal needs) and sd from a pre-set per-timeframe table ... never
    the card's own outcomes": `evaluate` reads `research.power_first.sd_r_by_tf`; a `PowerCard` has no sd field. The
    goal's edge is north_star_daily_gbp / (trades a day x GBP at risk a trade), GBP at risk = lot_fit_equity x
    risk.risk_per_trade, trades a day from the learn signal rate.
(b) "expected trades on holdout/forward gates extrapolated from the learn signal rate, never counted on holdout bars":
    a card states only its outcome-blind learn signal count and each window's length in years.
(c) "clustered effective n (day clusters; cross-instrument for pooled families, n_eff ~ n/(1+(m-1)rho))": the design
    effect (1 + (c - 1) rho_day)(1 + (m - 1) rho_instrument), c = learn signals per (instrument, session day).
(d) "joint power simulation shares data like the real gates": a gate reads the union of its windows; gates reading the
    same window see the same draw (`joint_power`).
(e) "every refusal logged": `start_or_refuse` writes the registry row (status NOT_STARTED_POWER, in
    `registry.NOT_TRIALS`, so no K) and a warning.

The rule only stops tests that cannot pass; it lowers no bar. A gate's `z` is the threshold at the batch index and
alpha it will actually receive (`holm_first_z` for the ADR 0085 confirm: Holm's first step, alpha_j / m_j).
"""
from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np
from pydantic import Field, model_validator
from scipy import stats

from goldbot.base import FrozenRecord
from goldbot.config import Settings
from goldbot.research.registry import NOT_STARTED_POWER, TrialRegistry

log = logging.getLogger(__name__)

__all__ = ["NOT_STARTED_POWER", "Gate", "GatePower", "PowerCard", "PowerVerdict", "Segment", "design_effect",
           "evaluate", "goal_edge_r", "holm_first_z", "joint_power", "start_or_refuse"]


class Segment(FrozenRecord):
    """A data window a gate reads (learn, TEST-OLD, holdout, a forward replay), by its length only."""
    name: str = Field(min_length=1)
    years: float = Field(gt=0)


class Gate(FrozenRecord):
    """A gate the card must pass: the windows it reads (their union) and the one-sided z its statistic must reach."""
    name: str = Field(min_length=1)
    segments: tuple[str, ...] = Field(min_length=1)
    z: float


class PowerCard(FrozenRecord):
    """What a card states before it starts. Outcome-blind only: signal counts and windows, never a return or an sd."""
    card_id: str = Field(min_length=1)
    family: str = Field(min_length=1)
    timeframe: str
    learn_signals: int = Field(ge=0)               # signals on the learn window (pooled over instruments)
    learn_signal_clusters: int = Field(ge=0)       # distinct (instrument, session day) with a learn signal
    learn_years: float = Field(gt=0)
    instruments: int = Field(1, ge=1)              # m: instruments a pooled family is counted across
    segments: list[Segment] = Field(min_length=1)
    gates: list[Gate] = Field(min_length=1)

    @model_validator(mode="after")
    def _known_segments(self) -> PowerCard:
        names = [s.name for s in self.segments]
        if len(set(names)) != len(names):
            raise ValueError(f"segment names repeat: {names}")
        if len({g.name for g in self.gates}) != len(self.gates):
            raise ValueError("gate names repeat")
        for g in self.gates:
            missing = [s for s in g.segments if s not in names]
            if missing:
                raise ValueError(f"gate {g.name!r} reads unknown segment(s) {missing}")
        if self.learn_signal_clusters > self.learn_signals:
            raise ValueError("more signal clusters than signals")
        return self


class GatePower(FrozenRecord):
    name: str
    n_expected: float
    n_eff: float
    z: float
    power: float                 # at the min edge: decides
    power_at_floor: float        # at the 0.10 R floor alone: reported, never decides (ADR 0085 open item 1)


class PowerVerdict(FrozenRecord):
    """The rule's reading of one card: started, or refused with every reason."""
    card_id: str
    family: str
    timeframe: str
    started: bool
    min_edge_r: float
    floor_edge_r: float
    goal_edge_r: float
    sd_r: float
    design_effect: float
    gates: list[GatePower]
    joint_power: float
    min_gate_power: float
    min_joint_power: float
    reasons: list[str]


def holm_first_z(alpha_j: float, m_j: int) -> float:
    """The one-sided z of Holm's first step at batch level alpha_j over m_j configs: the bar every config's power is
    read at (the strictest Holm threshold, so the power is never overstated)."""
    if not 0 < alpha_j < 1 or m_j < 1:
        raise ValueError(f"alpha_j in (0, 1) and m_j >= 1, got {alpha_j}, {m_j}")
    return float(stats.norm.isf(alpha_j / m_j))


def design_effect(mean_cluster: float, rho_day: float, instruments: int, rho_instrument: float) -> float:
    """(c): (1 + (c - 1) rho_day) x (1 + (m - 1) rho_instrument); n_eff = n / this."""
    return (1.0 + (max(mean_cluster, 1.0) - 1.0) * rho_day) * (1.0 + (instruments - 1) * rho_instrument)


def goal_edge_r(trades_per_day: float, north_star_gbp: float, risk_gbp: float) -> float:
    """(a): the net R a trade must earn for the card alone to make the ADR 0004 north-star a day."""
    if trades_per_day <= 0 or risk_gbp <= 0:
        return math.inf
    return north_star_gbp / (trades_per_day * risk_gbp)


def joint_power(n_eff: dict[str, float], gates: list[Gate], edge: float, sd: float, *, sims: int, seed: int) -> float:
    """(d): P(every gate passes) when each window's mean R is one draw, N(edge, sd^2 / n_eff), shared by every gate
    that reads it; a gate's statistic is the n_eff-weighted mean of its windows' draws, t = mean sqrt(N) / sd."""
    rng = np.random.default_rng(seed)
    names = sorted(n_eff)
    draws = {s: rng.normal(edge, sd / math.sqrt(n_eff[s]), sims) if n_eff[s] > 0 else np.full(sims, -math.inf)
             for s in names}
    ok = np.ones(sims, bool)
    for g in gates:
        n = sum(n_eff[s] for s in g.segments)
        if n <= 0:
            return 0.0
        mean = sum(n_eff[s] * draws[s] for s in g.segments) / n
        ok &= mean * math.sqrt(n) / sd >= g.z
    return float(ok.mean())


def _power(edge: float, n_eff: float, sd: float, z: float) -> float:
    """One-sided normal power of a gate at `z` for a true mean `edge` R on `n_eff` effective trades."""
    return float(stats.norm.cdf(edge * math.sqrt(n_eff) / sd - z)) if n_eff > 0 else 0.0


def evaluate(card: PowerCard, settings: Settings) -> PowerVerdict:
    """The power-first reading of `card` (module docstring for conditions (a)-(d)). Reads no data."""
    pf = settings.research.power_first
    sd = pf.sd_r(card.timeframe)
    rate = card.learn_signals / card.learn_years                    # signals a year, from learn only (b)
    risk_gbp = settings.research.lot_fit_equity * settings.risk.risk_per_trade
    goal = goal_edge_r(rate / pf.trading_days_per_year, settings.research.north_star_daily_gbp, risk_gbp)
    edge = max(pf.min_edge_floor_r, goal)
    c = card.learn_signals / card.learn_signal_clusters if card.learn_signal_clusters else 1.0
    de = design_effect(c, pf.rho_day, card.instruments, pf.rho_instrument)
    years = {s.name: s.years for s in card.segments}
    n_eff = {s: rate * y / de for s, y in years.items()}
    gates = []
    for g in card.gates:
        n = rate * sum(years[s] for s in g.segments)
        ne = n / de
        gates.append(GatePower(name=g.name, n_expected=n, n_eff=ne, z=g.z, power=_power(edge, ne, sd, g.z),
                               power_at_floor=_power(pf.min_edge_floor_r, ne, sd, g.z)))
    joint = joint_power(n_eff, card.gates, edge, sd, sims=pf.sims, seed=pf.seed) if rate > 0 else 0.0
    reasons = []
    if card.learn_signals == 0:
        reasons.append("no learn signals: no rate to extrapolate a gate's trades from")
    reasons += [f"gate {g.name}: power {g.power:.3f} < {pf.min_gate_power} (n_eff {g.n_eff:.0f}, z {g.z:.3f}, "
                f"edge {edge:.3f} R, sd {sd} R)" for g in gates if g.power < pf.min_gate_power]
    if joint < pf.min_joint_power:
        reasons.append(f"joint power {joint:.3f} < {pf.min_joint_power} over {len(gates)} gates")
    return PowerVerdict(card_id=card.card_id, family=card.family, timeframe=card.timeframe, started=not reasons,
                        min_edge_r=edge, floor_edge_r=pf.min_edge_floor_r, goal_edge_r=goal, sd_r=sd,
                        design_effect=de, gates=gates, joint_power=joint, min_gate_power=pf.min_gate_power,
                        min_joint_power=pf.min_joint_power, reasons=reasons)


def start_or_refuse(registry: TrialRegistry, card: PowerCard, settings: Settings, *, agent_id: str) -> PowerVerdict:
    """The rule at registration: evaluate `card`; a refusal is written as a "NOT STARTED (power)" registry row (no K,
    no data read; the caller must not read) and logged (e). A started card writes nothing here: its pre-registration
    and trial rows follow the usual path."""
    v = evaluate(card, settings)
    if v.started:
        return v
    results: dict[str, Any] = {"power_first": v.model_dump(mode="json"), "card": card.model_dump(mode="json")}
    with registry.locked():
        registry.record(agent_id=agent_id, family=card.family, config={"card_id": card.card_id},
                        feature_version="n/a (not started)", rationale="NOT STARTED (power): " + "; ".join(v.reasons),
                        results=results, status=NOT_STARTED_POWER)
    log.warning("NOT STARTED (power): card %s (%s %s): %s", card.card_id, card.family, card.timeframe,
                "; ".join(v.reasons))
    return v
