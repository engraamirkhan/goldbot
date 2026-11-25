"""ADR 0085 power-first rule, for research and demo promotion only (the owner's phase gate is pinned, see
`registry.phase_gate_refusal`).

The quant-reviewer's re-ruling of 2026-11-15 (on docs/research/power-first-gates-2026-11.md), as coded here:
* "delta = 0.10 R net per trade, fixed, NO max() with the goal edge. Record goal_edge_R (edge needed for GBP 50/day
  at the card's frequency/risk) as an info field only; it never gates" (`PowerVerdict.goal_edge_r`).
* "n_eff = n / (1 + (m_day-1)*rho_day) / (1 + (k-1)*rho_inst); n = expected trades at the gate from outcome-blind
  signal counts (holdout/forward: learn signal rate x window length); m_day = mean trades per (day, instrument)
  cluster floored at 1; k = instruments pooled" (`design_effect`; a `PowerCard` states only learn signal counts and
  window lengths).
* Overlap (quant Q3, 2026-11-25, amending the formula above; ADR 0085 amendment): "n_eff = sum(u_i)/DEFF_day;
  u_i = average uniqueness over label horizon pooled across tfs/arms per instrument; DEFF_day = 1+(m_w-1)*rho_day,
  m_w = sum M_d^2/sum M_d, M_d = sum u_i in day; then rho_inst factor" (`uniqueness`, Lopez de Prado 2018 ch. 4). A
  card that states its learn labels' measured `Uniqueness` is read with it; one that states counts only is read with
  its expectation on even arrivals of signals within and across days (quant C1): c = max(1, signals per instrument
  per trading day x `PowerCard.hold_days`) (`concurrency`, Little's law), m_eff = max(1, m_day / c), n_eff = n /
  [(1 + (m_eff - 1) rho_day) x c x (1 + (k - 1) rho_inst)]. That is n / H for a decision every day held H days
  (lag-j autocorrelation 1 - j/H, Hansen & Hodrick 1980) and unchanged while under one label is open on average.
  Clone arms and duplicated bets share their uniqueness, so they count once.
* rho_day per horizon (quant Q1, 2026-11-25): labels of <= 60 min use `rho_day_le_60m` (floor 0.05, set from the
  day-clustered ICC's upper 95 % CI on other families' learn candidates); longer horizons keep rho_day (floor 0.25)
  (`PowerFirstSettings.rho_day_for`).
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
import pandas as pd
from pydantic import Field, model_validator
from scipy import stats

from goldbot.base import FrozenRecord
from goldbot.config import Settings
from goldbot.data.timeutil import epoch_ns
from goldbot.research.registry import NOT_STARTED_POWER, TrialRegistry

log = logging.getLogger(__name__)

__all__ = ["NOT_STARTED_POWER", "Gate", "GatePower", "PowerCard", "PowerVerdict", "Segment", "Uniqueness",
           "across_day_overlap", "concurrency", "design_effect", "evaluate", "goal_edge_r", "holm_first_z", "is_precious_metal", "joint_power",
           "overlap_effect", "rho_instrument", "start_or_refuse", "uniqueness"]


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


class Uniqueness(FrozenRecord):
    """Quant Q3: the overlap of a card's learn labels, measured outcome-blind from their entry and exit times only
    (`uniqueness`). n_eff before rho_inst = sum_u / (1 + (m_w - 1) rho_day)."""
    n: int = Field(ge=0)                 # labels
    days: int = Field(ge=0)              # distinct (instrument, UTC entry day)
    sum_u: float = Field(ge=0)           # sum of each label's average uniqueness
    m_w: float = Field(ge=1)             # sum M_d^2 / sum M_d, floored at 1 (as m_day: a day never counts below one)

    @model_validator(mode="after")
    def _bounded(self) -> Uniqueness:
        if self.sum_u > self.n * (1 + 1e-9) or self.days > self.n:
            raise ValueError(f"uniqueness sum {self.sum_u} or days {self.days} exceed the {self.n} labels")
        return self

    def deff_day(self, rho_day: float) -> float:
        return 1.0 + (self.m_w - 1.0) * rho_day

    def n_eff_day(self, rho_day: float) -> float:
        """sum(u_i) / DEFF_day: the n_eff of one instrument's labels (no rho_inst term)."""
        return self.sum_u / self.deff_day(rho_day)


def uniqueness(events: pd.DataFrame) -> Uniqueness:
    """Q3 on labels `events` (`instrument`, `entry_utc`, `exit_utc`; every arm and timeframe of the card, pooled): per
    instrument, c_t = labels open at t; u_i = the mean of 1 / c_t over label i's [entry, exit); M_d = sum of u_i
    entered on (instrument, UTC day) d; m_w = sum M_d^2 / sum M_d floored at 1. Labels on different instruments never
    share uniqueness (rho_inst prices that). Exact on the piecewise-constant concurrency, O(n log n)."""
    if events.empty:
        return Uniqueness(n=0, days=0, sum_u=0.0, m_w=1.0)
    u_all, day_keys = [], []
    for inst, g in events.groupby("instrument", sort=True):
        a = epoch_ns(pd.DatetimeIndex(pd.to_datetime(g["entry_utc"], utc=True)))
        z = epoch_ns(pd.DatetimeIndex(pd.to_datetime(g["exit_utc"], utc=True)))
        if (z <= a).any():
            raise ValueError(f"{inst}: a label's exit must be after its entry")
        b = np.unique(np.concatenate([a, z]))
        ia, iz = np.searchsorted(b, a), np.searchsorted(b, z)
        step = np.zeros(len(b), np.int64)
        np.add.at(step, ia, 1)
        np.add.at(step, iz, -1)
        c = np.cumsum(step)[:-1]                                     # open labels on [b_k, b_k+1)
        seg = np.diff(b).astype(float) / 1e9                         # seconds: exact enough over decades
        cum = np.concatenate([[0.0], np.cumsum(np.where(c > 0, seg / np.maximum(c, 1), 0.0))])
        u_all.append((cum[iz] - cum[ia]) / ((z - a).astype(float) / 1e9))
        day_keys.append(str(inst) + "|" + pd.DatetimeIndex(pd.to_datetime(g["entry_utc"], utc=True)).strftime("%Y-%m-%d"))
    u = np.concatenate(u_all)
    m_d = pd.Series(u).groupby(np.concatenate(day_keys)).sum().to_numpy()
    m_w = float((m_d ** 2).sum() / m_d.sum()) if m_d.sum() > 0 else 1.0
    return Uniqueness(n=len(u), days=len(m_d), sum_u=float(u.sum()), m_w=max(m_w, 1.0))


class PowerCard(FrozenRecord):
    """What a card states before it starts. Outcome-blind only: signal counts and windows, never a return or an sd."""
    card_id: str = Field(min_length=1)
    family: str = Field(min_length=1)
    timeframe: str
    learn_signals: int = Field(ge=0)               # signals on the learn window (pooled over instruments)
    learn_signal_clusters: int = Field(ge=0)       # distinct (instrument, session day) with a learn signal
    learn_years: float = Field(gt=0)
    # the label horizon in trading days: the longest a signal's label stays open (its time barrier), outcome-blind;
    # required, so no card is credited non-overlapping holds by default (ADR 0096)
    hold_days: float = Field(gt=0)
    # Q3: the learn labels' measured overlap (`uniqueness`); None = the count-only expectation (`concurrency`)
    learn_uniqueness: Uniqueness | None = None
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
        if self.learn_uniqueness is not None and self.learn_uniqueness.n != self.learn_signals:
            raise ValueError(f"learn_uniqueness counts {self.learn_uniqueness.n} labels, the card "
                             f"{self.learn_signals} learn signals")
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
    # the overlap factor inside design_effect (Q3: n / sum u, or max(1, c) for a count-only card) and the rho_day used
    # (Q1). Verdicts written before them read 1.0 and 0.25: every such card (H-101's) held <= 1 day at rho_day 0.25
    overlap_effect: float = 1.0
    rho_day: float = 0.25
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


def overlap_effect(card: PowerCard, trading_days_per_year: float) -> float:
    """The card's overlap factor (>= 1): n / sum(u_i) when it states measured uniqueness, else max(1, c)."""
    u = card.learn_uniqueness
    if u is not None:
        return u.n / u.sum_u if u.sum_u > 0 else 1.0
    return max(concurrency(card, trading_days_per_year), 1.0)


def across_day_overlap(card: PowerCard, trading_days_per_year: float) -> float:
    """max(1, signal days per instrument per trading day x hold_days): the overlap of holds ACROSS days only, the
    floor a given verdict must credit (`_check_given`). A card that passes its own verdict (H-101, signed before quant
    C1) prices within-day overlap with its own rho_day; the measured `Uniqueness` replaces both when stated."""
    per_day = card.learn_signal_clusters / (len(card.instruments) * card.learn_years * trading_days_per_year)
    return max(1.0, min(per_day, 1.0) * card.hold_days)


def concurrency(card: PowerCard, trading_days_per_year: float) -> float:
    """c: the mean number of a card's labels open at once on one instrument, by Little's law on signals (quant C1):
    signals per instrument per trading day x hold_days. Outcome-blind: counts and the horizon."""
    per_day = card.learn_signals / (len(card.instruments) * card.learn_years * trading_days_per_year)
    return per_day * card.hold_days


def design_effect(mean_cluster: float, rho_day: float, instruments: int, rho_instrument: float, *,
                  concurrency: float) -> float:
    """(1 + (m_day - 1) rho_day) x max(1, c) x (1 + (k - 1) rho_inst), m_day floored at 1; n_eff = n / this.
    `concurrency` (c, or Q3's n / sum u with m_w for m_day) is required: a caller that forgets the hold must not be
    credited non-overlapping labels."""
    return ((1.0 + (max(mean_cluster, 1.0) - 1.0) * rho_day) * max(concurrency, 1.0)
            * (1.0 + (instruments - 1) * rho_instrument))


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
    u = card.learn_uniqueness
    c = overlap_effect(card, pf.trading_days_per_year)
    # Q3 measured: m_w; count-only (quant C1): the unique mass a day, m_eff = max(1, m_day / c)
    m_day = (u.m_w if u is not None else
             max(1.0, (card.learn_signals / card.learn_signal_clusters if card.learn_signal_clusters else 1.0) / c))
    rho_i = rho_instrument(card.instruments, pf.rho_instrument, pf.rho_instrument_metals)
    rho_d = pf.rho_day_for(card.hold_days)
    de = design_effect(m_day, rho_d, len(card.instruments), rho_i, concurrency=c)
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
                        overlap_effect=c, rho_day=rho_d, gates=gates, joint_power=joint,
                        min_gate_power=pf.min_gate_power, min_joint_power=pf.min_joint_power, n_eff_floor=pf.n_eff_floor, reasons=reasons)


def _check_given(v: PowerVerdict, card: PowerCard, settings: Settings) -> None:
    """A passed verdict: the card's, thresholds >= the settings' (the rule only tightens), and `started` only when
    every gate power, the joint power and every fresh gate's n_eff clear its own thresholds."""
    if (v.card_id, v.family) != (card.card_id, card.family):
        raise ValueError(f"verdict {v.card_id!r}/{v.family!r} is not card {card.card_id!r}/{card.family!r}")
    pf = settings.research.power_first
    loose = [k for k, mine, floor in (("min_gate_power", v.min_gate_power, pf.min_gate_power),
                                      ("min_joint_power", v.min_joint_power, pf.min_joint_power),
                                      ("n_eff_floor", v.n_eff_floor, pf.n_eff_floor), ("delta_r", v.delta_r, pf.delta_r),
                                      ("sd_r", v.sd_r, pf.sd_r_floor),
                                      ("rho_day", v.rho_day, pf.rho_day_for(card.hold_days))) if mine < floor]
    if loose:
        raise ValueError(f"verdict thresholds looser than research.power_first: {loose}")
    held = (overlap_effect(card, pf.trading_days_per_year) if card.learn_uniqueness is not None
            else across_day_overlap(card, pf.trading_days_per_year))
    if min(v.overlap_effect, v.design_effect) < held * (1 - 1e-9):
        raise ValueError(f"verdict credits less hold overlap than its card holds: overlap {v.overlap_effect:.3f}, "
                         f"design effect {v.design_effect:.3f} < {held:.3f} (quant Q3)")
    fails = (any(g.power < v.min_gate_power for g in v.gates) or v.joint_power < v.min_joint_power
             or any(g.fresh and g.n_eff < v.n_eff_floor for g in v.gates))
    if v.started and fails:
        raise ValueError("verdict says started but its gates contradict it")


def start_or_refuse(registry: TrialRegistry, card: PowerCard, settings: Settings, *, agent_id: str,
                    verdict: PowerVerdict | None = None) -> PowerVerdict:
    """The rule at registration: evaluate `card`; a refusal is written as a "NOT STARTED (power)" registry row (no K,
    no data read; the caller must not read) and logged (e). A started card writes nothing here: its pre-registration
    and trial rows follow the usual path.

    `verdict`: a card whose signed power section defines its own per-gate inputs (measured outcome-blind counts per
    window, a keep-share SE: H-101, research/model_zoo.py `stage0`) passes its verdict, computed with these settings'
    thresholds, so its refusal is recorded by this same path. It must name the card, carry thresholds no looser than
    these settings, and a start must agree with its own gates (`_check_given`)."""
    if verdict is not None:
        _check_given(verdict, card, settings)
    v = verdict if verdict is not None else evaluate(card, settings)
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
