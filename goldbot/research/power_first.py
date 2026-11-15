"""ADR 0085 power-first rule, for research and demo promotion only (the owner's phase gate is pinned, see
`registry.phase_gate_refusal`).

The quant-reviewer's re-ruling of 2026-11-15 (on docs/research/power-first-gates-2026-11.md), as coded here:
* "delta = 0.10 R net per trade, fixed, NO max() with the goal edge. Record goal_edge_R (edge needed for GBP 50/day
  at the card's frequency/risk) as an info field only; it never gates" (`PowerVerdict.goal_edge_r`).
* "n_eff = n / (1 + (m_day-1)*rho_day) / (1 + (k-1)*rho_inst); n = expected trades at the gate from outcome-blind
  signal counts (holdout/forward: learn signal rate x window length); m_day = mean trades per (day, instrument)
  cluster floored at 1; k = instruments pooled" (`design_effect`; a `PowerCard` states only learn signal counts and
  window lengths).
* "power_g = Phi(delta*sqrt(n_eff)/sd_tf - z_g), z_g = the gate's actual threshold at its batch index and alpha, Holm
  rank 1" (`holm_first_z`).
* "Start only if every power_g >= 0.5, simulated joint power >= 0.25, and n_eff >= n_eff_floor (200) at each
  fresh-data gate" (`evaluate`; the joint power lets gates reading the same window share one draw, `joint_power`).
* "sd_tf = max(1.2, demo-measured sd for that timeframe)" (`PowerFirstSettings.sd_r`); never the card's own.
* "rho_day 0.25 labelled prior ...; rho_inst 0.5 labelled prior, but 0.7 when >= half the pooled instruments are
  precious metals (XAU, XAG, XPT, XPD)" (`rho_instrument`).
* Every refusal is logged: `start_or_refuse` writes the registry row (status NOT_STARTED_POWER, in
  `registry.NOT_TRIALS`, no trial number, so no K) and a warning.
The rule only stops tests that cannot pass; it lowers no bar.
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
           "evaluate", "goal_edge_r", "holm_first_z", "is_precious_metal", "joint_power", "rho_instrument",
           "start_or_refuse"]


class Segment(FrozenRecord):
    """A data window a gate reads (learn, TEST-OLD, holdout, a forward replay), by its length only."""
    name: str = Field(min_length=1)
    years: float = Field(gt=0)


class Gate(FrozenRecord):
    """A gate the card must pass: the windows it reads (their union), the one-sided z its statistic must reach, and
    whether it is a fresh-data test (TEST-OLD, holdout, forward replay), where the n_eff floor applies."""
    name: str = Field(min_length=1)
    segments: tuple[str, ...] = Field(min_length=1)
    z: float
    fresh: bool = False


class PowerCard(FrozenRecord):
    """What a card states before it starts. Outcome-blind only: signal counts and windows, never a return or an sd."""
    card_id: str = Field(min_length=1)
    family: str = Field(min_length=1)
    timeframe: str
    learn_signals: int = Field(ge=0)               # signals on the learn window (pooled over instruments)
    learn_signal_clusters: int = Field(ge=0)       # distinct (instrument, session day) with a learn signal
    learn_years: float = Field(gt=0)
    instruments: list[str] = Field(min_length=1)   # the symbols a pooled family is counted across (k = len)
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
    fresh: bool
    power: float


class PowerVerdict(FrozenRecord):
    """The rule's reading of one card: started, or refused with every reason."""
    card_id: str
    family: str
    timeframe: str
    started: bool
    delta_r: float
    goal_edge_r: float           # information only: never gates (re-ruling 2026-11-15)
    sd_r: float
    rho_instrument: float
    design_effect: float
    gates: list[GatePower]
    joint_power: float
    min_gate_power: float
    min_joint_power: float
    n_eff_floor: float
    reasons: list[str]


def holm_first_z(alpha_j: float, m_j: int) -> float:
    """The one-sided z of Holm's first step at batch level alpha_j over m_j configs: the bar every config's power is
    read at (the strictest Holm threshold, so the power is never overstated)."""
    if not 0 < alpha_j < 1 or m_j < 1:
        raise ValueError(f"alpha_j in (0, 1) and m_j >= 1, got {alpha_j}, {m_j}")
    return float(stats.norm.isf(alpha_j / m_j))


PRECIOUS_METALS = ("XAU", "XAG", "XPT", "XPD")


def is_precious_metal(symbol: str) -> bool:
    return symbol.upper().startswith(PRECIOUS_METALS)


def rho_instrument(symbols: list[str], rho: float, rho_metals: float) -> float:
    """rho_inst: the metals value when at least half the pooled instruments are precious metals, else `rho`."""
    return rho_metals if 2 * sum(is_precious_metal(s) for s in symbols) >= len(symbols) else rho


def design_effect(mean_cluster: float, rho_day: float, instruments: int, rho_instrument: float) -> float:
    """(1 + (m_day - 1) rho_day) x (1 + (k - 1) rho_inst), m_day floored at 1; n_eff = n / this."""
    return (1.0 + (max(mean_cluster, 1.0) - 1.0) * rho_day) * (1.0 + (instruments - 1) * rho_instrument)


def goal_edge_r(trades_per_day: float, north_star_gbp: float, risk_gbp: float) -> float:
    """goal_edge_R: the net R a trade must earn for the card alone to make the ADR 0004 north-star a day.
    Information only: it never gates (re-ruling 2026-11-15)."""
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
    """The power-first reading of `card` (module docstring). Reads no data; goal_edge_R is recorded, never used."""
    pf = settings.research.power_first
    sd = pf.sd_r(card.timeframe)
    delta = pf.delta_r
    rate = card.learn_signals / card.learn_years                    # signals a year, from learn only
    risk_gbp = settings.research.lot_fit_equity * settings.risk.risk_per_trade
    goal = goal_edge_r(rate / pf.trading_days_per_year, settings.research.north_star_daily_gbp, risk_gbp)
    m_day = card.learn_signals / card.learn_signal_clusters if card.learn_signal_clusters else 1.0
    rho_i = rho_instrument(card.instruments, pf.rho_instrument, pf.rho_instrument_metals)
    de = design_effect(m_day, pf.rho_day, len(card.instruments), rho_i)
    years = {s.name: s.years for s in card.segments}
    n_eff = {s: rate * y / de for s, y in years.items()}
    gates = []
    for g in card.gates:
        n = rate * sum(years[s] for s in g.segments)
        ne = n / de
        gates.append(GatePower(name=g.name, n_expected=n, n_eff=ne, z=g.z, fresh=g.fresh,
                               power=_power(delta, ne, sd, g.z)))
    joint = joint_power(n_eff, card.gates, delta, sd, sims=pf.sims, seed=pf.seed) if rate > 0 else 0.0
    reasons = []
    if card.learn_signals == 0:
        reasons.append("no learn signals: no rate to extrapolate a gate's trades from")
    reasons += [f"gate {g.name}: power {g.power:.3f} < {pf.min_gate_power} (n_eff {g.n_eff:.0f}, z {g.z:.3f}, "
                f"delta {delta} R, sd {sd} R)" for g in gates if g.power < pf.min_gate_power]
    reasons += [f"gate {g.name}: fresh-data n_eff {g.n_eff:.0f} < {pf.n_eff_floor:.0f}"
                for g in gates if g.fresh and g.n_eff < pf.n_eff_floor]
    if joint < pf.min_joint_power:
        reasons.append(f"joint power {joint:.3f} < {pf.min_joint_power} over {len(gates)} gates")
    return PowerVerdict(card_id=card.card_id, family=card.family, timeframe=card.timeframe, started=not reasons,
                        delta_r=delta, goal_edge_r=goal, sd_r=sd, rho_instrument=rho_i, design_effect=de,
                        gates=gates, joint_power=joint, min_gate_power=pf.min_gate_power,
                        min_joint_power=pf.min_joint_power, n_eff_floor=pf.n_eff_floor, reasons=reasons)


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
