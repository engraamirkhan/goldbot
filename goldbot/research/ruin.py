"""Drawdown and risk-of-ruin Monte Carlo (playbook G-5, BACKLOG item 20). Reporting only: it changes no setting, trade,
model or trial budget.

Question: with this R distribution, this risk per trade and this many trades a week, how likely is it that the
configured loss limits trip by bad luck alone within N weeks, how deep do drawdowns get, and what is the risk of ruin?

Inputs
* R per trade, in order, with its cost basis stated in the report:
  - closed paper/live trades (`ClosedTrade.r`, state/closed_trades*.jsonl): **net** of every cost;
  - the shadow book (state/shadow_book.json), ONE version's taken closed trades (default: the agent's champion in the
    model registry; champion and challengers are pooled only with `pool=True`). R = ret x entry / |entry - stop|, so a
    scale-out policy's blended return is counted. Shadow R pays the spread only (no slippage, commission or swap) and
    a gap through the stop fills at the stop: **optimistic**;
  - a backtest's trades file (a column `r`, CSV or Parquet): cost basis **unknown** unless the file says;
  - parametric: win rate, average win and average loss in R (independent draws).
  `r_haircut` subtracts a fixed R from every trade and `demean` removes the sample's edge (a zero-edge stress run);
  both are recommended when the sample is small.
* The live limits (`RuinLimits.from_settings`, built from gate.RiskLimits and supervisor.SupervisorLimits as the
  engine builds them from `config/settings.yaml`): risk per trade (paper 0.5%, tiny-live 0.1%), the hard 1% maximum,
  the multiplier bounds, the daily 2% / weekly 5% caps, the supervisor's 1.5% / 4% combined caps, the 8% (size down)
  and 12% (halt) stages from the closed-equity high-water mark and the 5% stage-1 clear.

Method
* Circular block bootstrap of the R sequence (blocks of consecutive trades, so loss streaks shorter than a block
  survive resampling; default block min(n, max(5, round(n^(1/3))))). The headline is repeated at twice the block length
  as a sensitivity check. The parametric source is i.i.d.
* Trades are spread evenly over the week: trade j closes at (j + 1) / trades_per_week weeks, on trading day
  floor(j x days_per_week / trades_per_week). One position at a time. Daily and weekly cap probabilities rest on that.
* Sizing is the gate's own arithmetic (goldbot/risk/sizing.py `effective_risk`): one fixed model multiplier m, clamped
  to the bounds, capped at the hard maximum; in the 8% stage risk is halved AND m is capped at 0.5 (a quarter of the
  normal risk at m = 1).
* With `apply_rules` (default) the simulation acts like RiskGate: after a daily (or supervisor daily) cap trips, the
  rest of that day's trades are skipped; after a weekly one, the rest of the week's; the stage machine is the gate's
  `update_stage` (sizing.next_stage); at 12% the path stops (the owner re-arm is not modelled). The supervisor caps act
  on combined equity, which in this one-account simulation is the account's own equity.
* Drawdown is measured on closed equity after each trade. The gate measures floating equity against the closed
  high-water mark, so with a position open the halt fires about one open risk earlier than simulated.
* `compounding` (default): equity *= 1 + risk x R. Otherwise fixed dollars: equity += risk x R (start equity 1), the
  textbook gambler's-ruin walk.
* Risk of ruin: the probability that equity falls to (1 - ruin_level) x the starting equity within the horizon
  (default 50%). With the rules on, the 12% halt normally stops a path long before that: that is the switch working.
Deterministic for a given seed (paths are drawn chunk by chunk from one generator).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Literal, Sequence

import numpy as np
import pandas as pd
from pydantic import Field, model_validator

from goldbot.base import FrozenRecord
from goldbot.config import RiskSettings
from goldbot.data.timeutil import epoch_ns
from goldbot.risk.sizing import effective_risk

if TYPE_CHECKING:
    from goldbot.ops.gates_phase import ClosedTrade

_EPS = 1e-9          # ruin level reached up to float noise (the gate's own limits are compared exactly, as it does)
_CHUNK = 2000        # paths simulated at once (memory: chunk x trades floats)
_SMALL_SAMPLE = 100  # below this many trades the report recommends a demeaned or haircut run

CostBasis = Literal["net", "spread_only", "unknown", "parametric"]
_COST_NOTE = {
    "net": "cost basis: closed paper/live trades, net of every cost (spread, slippage, commission, swap)",
    "spread_only": "cost basis: shadow R pays the spread only (no slippage, commission or swap) and a gap through the "
                   "stop fills at the stop: optimistic; consider --r-haircut",
    "unknown": "cost basis: unknown (trades file): check whether its R is gross or net before reading the result",
    "parametric": "cost basis: as given (parametric win rate and R)",
}
Draw = Callable[[int, np.random.Generator], np.ndarray]    # (paths, rng) -> R matrix (paths x trades)
_COST_LABEL = {"net": "net", "spread_only": "spread only", "unknown": "unknown", "parametric": "as given"}


class RuinLimits(FrozenRecord):
    """The account's loss limits and sizing bounds as fractions of equity (gate.RiskLimits + supervisor limits)."""

    risk_per_trade: float = Field(gt=0, le=0.05)
    max_risk_per_trade: float = Field(gt=0, le=0.05)
    multiplier_bounds: tuple[float, float]
    daily_cap: float = Field(gt=0, lt=1)
    weekly_cap: float = Field(gt=0, lt=1)
    supervisor_daily_cap: float = Field(gt=0, lt=1)
    supervisor_weekly_cap: float = Field(gt=0, lt=1)
    drawdown_stage1: float = Field(gt=0, lt=1)
    drawdown_stage2: float = Field(gt=0, lt=1)
    drawdown_stage1_clear: float = Field(gt=0, lt=1)

    @classmethod
    def from_settings(cls, risk: RiskSettings, *, tiny_live: bool = False) -> RuinLimits:
        from goldbot.risk.gate import RiskLimits
        from goldbot.risk.supervisor import SupervisorLimits
        g = RiskLimits.from_settings(risk, tiny_live=tiny_live)
        s = SupervisorLimits.from_settings(risk)
        return cls(risk_per_trade=g.risk_per_trade, max_risk_per_trade=g.max_risk_per_trade,
                   multiplier_bounds=g.multiplier_bounds, daily_cap=g.daily_cap, weekly_cap=g.weekly_cap,
                   supervisor_daily_cap=s.daily_cap, supervisor_weekly_cap=s.weekly_cap,
                   drawdown_stage1=g.dd_stage1, drawdown_stage2=g.dd_stage2, drawdown_stage1_clear=g.dd_stage1_clear)


class ParametricR(FrozenRecord):
    """Two-point R distribution: +avg_win_r with probability win_rate, else -avg_loss_r."""

    win_rate: float = Field(gt=0, lt=1)
    avg_win_r: float = Field(gt=0)
    avg_loss_r: float = Field(gt=0)


class RSample(FrozenRecord):
    """An ordered R sample with where it came from."""

    r: np.ndarray
    exit_utc: pd.DatetimeIndex | None = None
    cost_basis: CostBasis
    versions: list[str] = Field(default_factory=list)


class RuinConfig(FrozenRecord):
    limits: RuinLimits
    trades_per_week: float = Field(gt=0)
    weeks: int = Field(52, ge=1)
    horizons_weeks: tuple[int, ...] = (26, 52)     # report P(trip) within each; the full `weeks` is always added
    n_paths: int = Field(10_000, ge=1)
    block_length: int | None = Field(None, ge=1)   # None: min(n, max(5, round(n^(1/3))))
    seed: int = 0
    apply_rules: bool = True
    compounding: bool = True
    ruin_level: float = Field(0.5, gt=0, le=1)     # loss from the starting equity that counts as ruin
    days_per_week: int = Field(5, ge=1, le=7)
    multiplier: float = Field(1.0, gt=0)           # the model multiplier every trade is sized with (gate-clamped)
    r_haircut: float = Field(0.0, ge=0)            # R subtracted from every trade
    demean: bool = False                           # remove the source's mean R (zero-edge stress run)

    @model_validator(mode="after")
    def _horizons_inside(self) -> RuinConfig:
        bad = [h for h in self.horizons_weeks if not 1 <= h <= self.weeks]
        if bad:
            raise ValueError(f"horizons {bad} must lie within 1..{self.weeks} simulated weeks")
        return self


class ThresholdTrip(FrozenRecord):
    name: str
    level: float
    basis: str                                      # what the level is measured against
    enforced: bool                                  # the simulation applied its effect (apply_rules)
    p_by_weeks: dict[int, float]                    # P(first trip within h weeks)
    median_weeks_to_first: float | None             # among paths that tripped within the simulated weeks


class Quantiles(FrozenRecord):
    mean: float
    median: float
    p95: float
    p99: float


class BlockCheck(FrozenRecord):
    """The headline at another block length (sensitivity of the bootstrap to the streak length it keeps)."""

    block_length: int
    p_stage1: float
    p_stage2: float
    max_drawdown_p95: float


class RuinReport(FrozenRecord):
    source: Literal["bootstrap", "parametric"]
    cost_basis: CostBasis
    versions: list[str]                             # shadow-book versions the R came from (empty: other sources)
    n_source_trades: int | None
    source_mean_r: float
    source_sd_r: float
    simulated_mean_r: float                         # after the haircut / demeaning
    observed_trades_per_week: float | None          # from the source's exit times, when it has them
    block_length: int | None
    double_block: BlockCheck | None
    config: RuinConfig
    risk_normal: float                              # fraction of equity risked per trade (gate arithmetic)
    risk_size_down: float                           # the same in the 8% stage
    trades_per_path: int
    mean_trades_taken: float                        # after the caps' skipped trades and halts
    thresholds: list[ThresholdTrip]
    max_drawdown: Quantiles                         # from the closed-equity high-water mark, over the whole horizon
    final_equity: Quantiles                         # multiple of the starting equity
    risk_of_ruin: float
    notes: list[str]


# ------------------------------------------------------------------------------------------------ helpers
def gamblers_ruin_probability(p: float, start_units: int, target_units: int | None) -> float:
    """Classic gambler's ruin for a +-1 walk winning with probability p: P(hit 0 before `target_units`) from
    `start_units` (target None: no upper barrier, P = (q/p)^k for p > 1/2, else 1)."""
    q = 1 - p
    if target_units is None:
        return 1.0 if p <= 0.5 else float((q / p) ** start_units)
    if math.isclose(p, 0.5):
        return 1 - start_units / target_units
    r = q / p
    return float((r ** start_units - r ** target_units) / (1 - r ** target_units))


def longest_run(mask: np.ndarray) -> int:
    """Longest run of True values."""
    best = cur = 0
    for v in np.asarray(mask, dtype=bool):
        cur = cur + 1 if v else 0
        best = max(best, cur)
    return best


def default_block_length(n: int) -> int:
    return max(1, min(n, max(5, round(n ** (1 / 3)))))


def block_bootstrap(r: np.ndarray, *, n_paths: int, n_trades: int, block_length: int,
                    rng: np.random.Generator) -> np.ndarray:
    """Circular block bootstrap: each path is consecutive blocks of `block_length` trades from uniformly drawn
    starting points (wrapping at the end), so streaks within a block are kept. Shape (n_paths, n_trades)."""
    r = np.asarray(r, dtype=float)
    n = len(r)
    if n == 0:
        raise ValueError("no trades to resample")
    L = min(block_length, n)
    n_blocks = -(-n_trades // L)
    starts = rng.integers(0, n, size=(n_paths, n_blocks))
    idx = (starts[:, :, None] + np.arange(L)) % n
    return r[idx.reshape(n_paths, n_blocks * L)[:, :n_trades]]


def next_stage_vec(size_down: np.ndarray, halted: np.ndarray, dd: np.ndarray, stage1: float, stage2: float,
                   clear: float) -> tuple[np.ndarray, np.ndarray]:
    """sizing.next_stage (the gate's `update_stage`) over arrays of paths: (size_down, halted) after drawdown `dd`.
    Halted is sticky; a halted path's size_down flag is irrelevant."""
    new_halted = halted | (dd >= stage2)
    new_size_down = np.where(dd >= stage1, True, np.where(size_down & (dd < clear), False, size_down))
    return np.where(halted, size_down, new_size_down), new_halted


def _parametric(par: ParametricR, n_paths: int, n_trades: int, rng: np.random.Generator) -> np.ndarray:
    win = rng.random((n_paths, n_trades)) < par.win_rate
    return np.where(win, par.avg_win_r, -par.avg_loss_r)


def _quantiles(x: np.ndarray) -> Quantiles:
    return Quantiles(mean=round(float(np.mean(x)), 6), median=round(float(np.median(x)), 6),
                     p95=round(float(np.quantile(x, 0.95)), 6), p99=round(float(np.quantile(x, 0.99)), 6))


def observed_trades_per_week(exit_utc: pd.DatetimeIndex | None) -> float | None:
    """(n - 1) trades over the span from the first to the last exit, per week; None under two trades or no span."""
    if exit_utc is None or len(exit_utc) < 2:
        return None
    ns = np.sort(epoch_ns(exit_utc))
    span_weeks = (ns[-1] - ns[0]) / (7 * 86400 * 1e9)
    return float((len(ns) - 1) / span_weeks) if span_weeks > 0 else None


_BASIS = {"daily_cap": "loss from the risk-day's starting equity",
          "weekly_cap": "loss from the risk-week's starting equity",
          "supervisor_daily_cap": "combined daily loss (this account's equity in a one-account run)",
          "supervisor_weekly_cap": "combined weekly loss (this account's equity in a one-account run)",
          "drawdown_stage1": "drawdown from the closed-equity high-water mark (size down)",
          "drawdown_stage2": "drawdown from the closed-equity high-water mark (halt)"}


class _Paths(FrozenRecord):
    first: dict[str, np.ndarray]
    max_dd: np.ndarray
    final: np.ndarray
    taken: np.ndarray
    ruined: np.ndarray


def _run(cfg: RuinConfig, draw: Draw, n_trades: int, shift: float, risk_normal: float, risk_down: float,
         rng: np.random.Generator) -> _Paths:
    """Simulate cfg.n_paths paths; `draw(m, rng)` returns an (m, n_trades) R matrix before `shift` is subtracted."""
    lim = cfg.limits
    j = np.arange(n_trades)
    week = np.floor(j / cfg.trades_per_week + _EPS).astype(int)
    day = np.floor(j * cfg.days_per_week / cfg.trades_per_week + _EPS).astype(int)
    t_close = (j + 1) / cfg.trades_per_week
    levels = _levels(lim)
    first: dict[str, list[np.ndarray]] = {k: [] for k in levels}
    out: dict[str, list[np.ndarray]] = {"max_dd": [], "final": [], "taken": [], "ruined": []}
    for c0 in range(0, cfg.n_paths, _CHUNK):
        m = min(_CHUNK, cfg.n_paths - c0)
        R = draw(m, rng) - shift
        eq, hwm, day_start, week_start = (np.ones(m) for _ in range(4))
        halted, ruined, size_down, day_block, week_block = (np.zeros(m, dtype=bool) for _ in range(5))
        f = {k: np.full(m, np.inf) for k in levels}
        max_dd, taken = np.zeros(m), np.zeros(m)
        prev_day = prev_week = -1
        for i in range(n_trades):
            if day[i] != prev_day:
                day_start, prev_day = eq.copy(), day[i]
                day_block[:] = False
            if week[i] != prev_week:
                week_start, prev_week = eq.copy(), week[i]
                week_block[:] = False
            active = ~ruined
            risk = np.full(m, risk_normal)
            if cfg.apply_rules:
                active &= ~halted & ~day_block & ~week_block
                risk = np.where(size_down, risk_down, risk)
            step = risk * R[:, i]
            new = eq * (1 + step) if cfg.compounding else eq + step
            eq = np.where(active, np.maximum(new, 0.0), eq)
            taken += active
            hwm = np.maximum(hwm, eq)
            dd = 1 - eq / hwm
            dl = 1 - eq / np.maximum(day_start, 1e-300)
            wl = 1 - eq / np.maximum(week_start, 1e-300)
            values = {"daily_cap": dl, "weekly_cap": wl, "supervisor_daily_cap": dl, "supervisor_weekly_cap": wl,
                      "drawdown_stage1": dd, "drawdown_stage2": dd}
            for k, lvl in levels.items():
                hit = (values[k] >= lvl) & np.isinf(f[k])
                f[k][hit] = t_close[i]
            max_dd = np.maximum(max_dd, dd)
            ruined |= eq <= 1 - cfg.ruin_level + _EPS
            if cfg.apply_rules:
                size_down, halted = next_stage_vec(size_down, halted, dd, lim.drawdown_stage1, lim.drawdown_stage2,
                                                   lim.drawdown_stage1_clear)
                day_block |= (dl >= lim.daily_cap) | (dl >= lim.supervisor_daily_cap)
                week_block |= (wl >= lim.weekly_cap) | (wl >= lim.supervisor_weekly_cap)
        for k in levels:
            first[k].append(f[k])
        for k, v in (("max_dd", max_dd), ("final", eq), ("taken", taken), ("ruined", ruined)):
            out[k].append(v)
    cat = {k: np.concatenate(v) for k, v in out.items()}
    return _Paths(first={k: np.concatenate(v) for k, v in first.items()}, **cat)


def _levels(lim: RuinLimits) -> dict[str, float]:
    return {"daily_cap": lim.daily_cap, "weekly_cap": lim.weekly_cap,
            "supervisor_daily_cap": lim.supervisor_daily_cap, "supervisor_weekly_cap": lim.supervisor_weekly_cap,
            "drawdown_stage1": lim.drawdown_stage1, "drawdown_stage2": lim.drawdown_stage2}


# ------------------------------------------------------------------------------------------------ simulation
def simulate(cfg: RuinConfig, *, r: np.ndarray | None = None, parametric: ParametricR | None = None,
             cost_basis: CostBasis | None = None, versions: Sequence[str] = (),
             exit_utc: pd.DatetimeIndex | None = None) -> RuinReport:
    """Run the Monte Carlo on an ordered R sample (block bootstrap) or a parametric distribution."""
    if (r is None) == (parametric is None):
        raise ValueError("give exactly one of r and parametric")
    lim = cfg.limits
    n_trades = math.ceil(cfg.weeks * cfg.trades_per_week - _EPS)
    eff_normal = effective_risk(lim, False, cfg.multiplier)
    eff_down = effective_risk(lim, True, cfg.multiplier)
    notes: list[str] = []
    sample = np.empty(0)
    block: int | None = None
    if r is not None:
        sample = np.asarray(r, dtype=float)
        sample = sample[np.isfinite(sample)]
        if len(sample) == 0:
            raise ValueError("no finite R values to resample")
        block = min(cfg.block_length or default_block_length(len(sample)), len(sample))
        mean_r, sd_r = float(sample.mean()), float(sample.std(ddof=1)) if len(sample) > 1 else 0.0
        if len(sample) < 30:
            notes.append(f"only {len(sample)} trades: the bootstrap can only recycle what was seen; read as indicative")
        if len(sample) < _SMALL_SAMPLE and not cfg.demean and cfg.r_haircut == 0:
            notes.append(f"{len(sample)} trades (< {_SMALL_SAMPLE}): also run with --demean (zero edge) or "
                         "--r-haircut to see the limits under a weaker edge than the sample shows")
    else:
        assert parametric is not None
        p = parametric.win_rate
        mean_r = p * parametric.avg_win_r - (1 - p) * parametric.avg_loss_r
        sd_r = math.sqrt(p * (1 - p)) * (parametric.avg_win_r + parametric.avg_loss_r)
    basis: CostBasis = cost_basis or ("unknown" if r is not None else "parametric")
    shift = (mean_r if cfg.demean else 0.0) + cfg.r_haircut
    sim_mean = mean_r - shift
    notes.append(_COST_NOTE[basis])
    if cfg.demean or cfg.r_haircut:
        notes.append(f"stress run: R shifted by {-shift:+.3f} (demean {'on' if cfg.demean else 'off'}, haircut "
                     f"{cfg.r_haircut:g}); simulated mean R {sim_mean:+.3f}")
    if sim_mean <= 0:
        notes.append(f"simulated mean R {sim_mean:.3f} <= 0: drawdowns grow without bound and every limit trips "
                     "eventually")
    observed = observed_trades_per_week(exit_utc)
    if observed is not None:
        notes.append(f"observed {observed:.2f} trades/week in the source")
        if abs(cfg.trades_per_week - observed) > 0.25 * observed:
            notes.append(f"--trades-per-week {cfg.trades_per_week:g} differs from the observed {observed:.2f} by more "
                         "than 25%")
    notes.append("daily and weekly cap probabilities assume trades evenly spaced through the week and one position at "
                 "a time; clustered or overlapping trades trip them more often")
    lo, hi = lim.multiplier_bounds
    notes.append(f"multiplier fixed at m = {cfg.multiplier:g} (gate-clamped to {eff_normal.mult:g}; live sizing varies "
                 f"m per trade within {lo:g}-{hi:g}): risk {eff_normal.target:.3%} per trade, {eff_down.target:.3%} in "
                 f"the {lim.drawdown_stage1:.0%} stage (risk halved and m capped at 0.5)")
    notes.append(f"drawdown is measured on closed equity after each trade; the gate measures floating equity against "
                 f"the closed high-water mark, so with a position open the {lim.drawdown_stage2:.0%} halt fires about "
                 f"one open risk (~{eff_normal.target:.2%}) earlier than simulated")
    if not cfg.apply_rules:
        notes.append("rules off: limits are measured, not enforced (no skipped trades, no size-down, no halt)")

    def draw_for(L: int | None) -> Draw:
        if parametric is not None:
            par = parametric
            return lambda m, g: _parametric(par, m, n_trades, g)
        return lambda m, g: block_bootstrap(sample, n_paths=m, n_trades=n_trades, block_length=L or 1, rng=g)

    paths = _run(cfg, draw_for(block), n_trades, shift, eff_normal.target, eff_down.target,
                 np.random.default_rng(cfg.seed))
    double: BlockCheck | None = None
    if block is not None and 2 * block <= len(sample):
        d = _run(cfg, draw_for(2 * block), n_trades, shift, eff_normal.target, eff_down.target,
                 np.random.default_rng(cfg.seed))
        double = BlockCheck(block_length=2 * block,
                            p_stage1=round(float(np.mean(np.isfinite(d.first["drawdown_stage1"]))), 6),
                            p_stage2=round(float(np.mean(np.isfinite(d.first["drawdown_stage2"]))), 6),
                            max_drawdown_p95=round(float(np.quantile(d.max_dd, 0.95)), 6))

    horizons = sorted({*cfg.horizons_weeks, cfg.weeks})
    trips = []
    for k, lvl in _levels(lim).items():
        fk = paths.first[k]
        tripped = fk[np.isfinite(fk)]
        trips.append(ThresholdTrip(
            name=k, level=lvl, basis=_BASIS[k], enforced=cfg.apply_rules,
            p_by_weeks={h: round(float(np.mean(fk <= h + _EPS)), 6) for h in horizons},
            median_weeks_to_first=round(float(np.median(tripped)), 4) if len(tripped) else None))
    return RuinReport(source="bootstrap" if r is not None else "parametric", cost_basis=basis, versions=list(versions),
                      n_source_trades=len(sample) if r is not None else None,
                      source_mean_r=round(mean_r, 6), source_sd_r=round(sd_r, 6), simulated_mean_r=round(sim_mean, 6),
                      observed_trades_per_week=round(observed, 6) if observed is not None else None,
                      block_length=block, double_block=double, config=cfg,
                      risk_normal=eff_normal.target, risk_size_down=eff_down.target,
                      trades_per_path=n_trades, mean_trades_taken=round(float(np.mean(paths.taken)), 4),
                      thresholds=trips, max_drawdown=_quantiles(paths.max_dd), final_equity=_quantiles(paths.final),
                      risk_of_ruin=round(float(np.mean(paths.ruined)), 6), notes=notes)


# ------------------------------------------------------------------------------------------------ R sources
def r_from_closed_trades(trades: Sequence[ClosedTrade], *, agent_id: str | None = None,
                         mode: str | None = None) -> RSample:
    """R of closed paper/live trades (gates_phase.ClosedTrade, net of costs) in exit order; trades without `r` are
    skipped."""
    rows = [t for t in trades if t.r is not None and (agent_id is None or t.agent_id == agent_id)
            and (mode is None or t.mode == mode)]
    if not rows:
        return RSample(r=np.empty(0), cost_basis="net")
    ts = pd.DatetimeIndex([t.exit_utc for t in rows])
    order = np.argsort(epoch_ns(ts), kind="stable")
    return RSample(r=np.array([rows[i].r for i in order], dtype=float), exit_utc=ts[order], cost_basis="net")


def _champion_versions(models_dir: Path | None, agent_id: str | None) -> list[str]:
    """Champion versions in the model registry file (read only), for one agent when given."""
    if models_dir is None:
        return []
    path = Path(models_dir) / "registry.json"
    if not path.exists():
        return []
    return [str(e["version"]) for e in json.loads(path.read_text())
            if e.get("status") == "champion" and (agent_id is None or e.get("agent_id") == agent_id)]


def r_from_shadow_book(state_dir: Path, *, version: str | None = None, agent_id: str | None = None,
                       pool: bool = False, models_dir: Path | None = None) -> RSample:
    """R of ONE shadow-book version's taken, closed trades in exit order: ret x entry / |entry - stop| (the trade's
    return in units of its initial risk, so a scale-out's blended return counts). Version: `version`, else the
    agent's champion in the registry, else the book's only version; several versions are pooled only with `pool`."""
    from goldbot.engine.shadow import VersionBook
    path = Path(state_dir) / "shadow_book.json"
    if not path.exists():
        return RSample(r=np.empty(0), cost_basis="spread_only")
    books = {k: VersionBook.model_validate(v) for k, v in json.loads(path.read_text()).items()}
    if version is not None:
        if version not in books:
            raise ValueError(f"version {version} is not in the shadow book (has {', '.join(sorted(books))})")
        chosen = [version]
    elif pool:
        chosen = sorted(books)
    else:
        champs = [v for v in _champion_versions(models_dir, agent_id) if v in books]
        if len(champs) == 1:
            chosen = champs
        elif len(books) == 1:
            chosen = list(books)
        else:
            raise ValueError(f"the shadow book has {len(books)} versions ({', '.join(sorted(books))}) and no single "
                             "champion was found: pass --version V (or --agent with the registry), or --pool-versions "
                             "to pool champion and challengers")
    rows = [t for v in chosen for t in books[v].closed
            if t.taken and t.ret is not None and t.exit_ts is not None and t.entry != t.stop
            and (agent_id is None or t.agent_id == agent_id)]
    if not rows:
        return RSample(r=np.empty(0), cost_basis="spread_only", versions=chosen)
    ts = pd.DatetimeIndex([t.exit_ts for t in rows])
    order = np.argsort(epoch_ns(ts), kind="stable")
    r = [float(rows[i].ret or 0.0) * rows[i].entry / abs(rows[i].entry - rows[i].stop) for i in order]
    return RSample(r=np.array(r, dtype=float), exit_utc=ts[order], cost_basis="spread_only", versions=chosen)


def r_from_file(path: Path) -> RSample:
    """A backtest's trades: a CSV or Parquet file with a column `r`, in trade order (cost basis unknown)."""
    df = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
    if "r" not in df.columns:
        raise ValueError(f"{path} has no column 'r'")
    return RSample(r=df["r"].to_numpy(dtype=float), cost_basis="unknown")


# ------------------------------------------------------------------------------------------------ CLI
def format_report(rep: RuinReport) -> str:
    c = rep.config
    src = (f"bootstrap of {rep.n_source_trades} trades (block {rep.block_length})" if rep.source == "bootstrap"
           else "parametric")
    if rep.versions:
        src += f" from shadow version(s) {', '.join(rep.versions)}"
    lines = [f"Drawdown Monte Carlo: {src}, cost basis {_COST_LABEL[rep.cost_basis]}, mean R {rep.source_mean_r:+.3f} "
             f"(sd {rep.source_sd_r:.3f}, simulated {rep.simulated_mean_r:+.3f}), risk {rep.risk_normal:.2%}/trade "
             f"({rep.risk_size_down:.3%} in stage 1), {c.trades_per_week:g} trades/week"
             + (f" (observed {rep.observed_trades_per_week:.2f})" if rep.observed_trades_per_week is not None else "")
             + f", {c.weeks} weeks, {c.n_paths} paths, seed {c.seed}, rules {'on' if c.apply_rules else 'off'}",
             "threshold              level  " + "  ".join(f"P(<= {h}w)" for h in sorted(rep.thresholds[0].p_by_weeks))
             + "  median weeks to first"]
    for t in rep.thresholds:
        ps = "  ".join(f"{p:>9.1%}" for _, p in sorted(t.p_by_weeks.items()))
        med = f"{t.median_weeks_to_first:.1f}" if t.median_weeks_to_first is not None else "-"
        lines.append(f"{t.name:<22} {t.level:>5.1%}  {ps}  {med}")
    d = rep.max_drawdown
    lines.append(f"max drawdown: median {d.median:.2%}, 95th {d.p95:.2%}, 99th {d.p99:.2%}")
    if rep.double_block is not None:
        b = rep.double_block
        lines.append(f"at block {b.block_length}: P({c.limits.drawdown_stage1:.0%}) {b.p_stage1:.1%}, "
                     f"P({c.limits.drawdown_stage2:.0%}) {b.p_stage2:.1%}, max drawdown 95th {b.max_drawdown_p95:.2%}")
    lines.append(f"final equity x start: median {rep.final_equity.median:.3f}; trades taken per path "
                 f"{rep.mean_trades_taken:.0f} of {rep.trades_per_path}")
    halted = f" (halted at {c.limits.drawdown_stage2:.0%}; see rules-off run)" if c.apply_rules else ""
    lines.append(f"risk of ruin (equity <= {1 - c.ruin_level:.0%} of start within {c.weeks} weeks): "
                 f"{rep.risk_of_ruin:.2%}{halted}")
    lines += [f"note: {n}" for n in rep.notes]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="run.py ruin", description="Drawdown and risk-of-ruin Monte Carlo (G-5).")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--closed-trades", type=Path, help="state dir with closed_trades*.jsonl (paper/live R, net)")
    src.add_argument("--shadow", type=Path, help="state dir with shadow_book.json (one version's taken closed trades)")
    src.add_argument("--r-file", type=Path, help="backtest trades, CSV or Parquet with a column r")
    src.add_argument("--win-rate", type=float, help="parametric (with --avg-win and --avg-loss)")
    ap.add_argument("--avg-win", type=float, help="average win in R (parametric)")
    ap.add_argument("--avg-loss", type=float, help="average loss in R, positive (parametric)")
    ap.add_argument("--agent", default=None, help="only this agent's trades (closed-trade and shadow sources)")
    ap.add_argument("--version", default=None, help="shadow-book version (default: the agent's champion)")
    ap.add_argument("--pool-versions", action="store_true", help="pool every shadow-book version (champion and "
                                                                 "challengers)")
    ap.add_argument("--models-dir", type=Path, default=None, help="model registry folder (default: settings)")
    ap.add_argument("--trades-per-week", type=float, required=True)
    ap.add_argument("--weeks", type=int, default=52)
    ap.add_argument("--horizons", type=int, nargs="*", default=None, help="weeks to report P(trip) within (default 26 52)")
    ap.add_argument("--paths", type=int, default=10_000)
    ap.add_argument("--block", type=int, default=None, help="bootstrap block length in trades")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--risk", type=float, default=None, help="risk per trade (default: settings risk.risk_per_trade)")
    ap.add_argument("--tiny-live", action="store_true", help="use risk.risk_per_trade_tiny_live")
    ap.add_argument("--multiplier", type=float, default=1.0, help="model multiplier (clamped like the gate)")
    ap.add_argument("--r-haircut", type=float, default=0.0, help="subtract this R from every trade")
    ap.add_argument("--demean", action="store_true", help="remove the source's mean R (zero-edge stress run)")
    ap.add_argument("--no-rules", action="store_true", help="measure the limits without enforcing them")
    ap.add_argument("--ruin-level", type=float, default=0.5)
    ap.add_argument("--settings", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=None, help="write the report as JSON")
    args = ap.parse_args(argv)

    from goldbot.config import load_settings
    settings = load_settings(args.settings) if args.settings else load_settings()
    limits = RuinLimits.from_settings(settings.risk, tiny_live=args.tiny_live)
    if args.risk is not None:
        limits = limits.model_copy(update={"risk_per_trade": args.risk})
    horizons = tuple(h for h in (args.horizons if args.horizons is not None else (26, 52)) if h <= args.weeks)
    cfg = RuinConfig(limits=limits, trades_per_week=args.trades_per_week, weeks=args.weeks, horizons_weeks=horizons,
                     n_paths=args.paths, block_length=args.block, seed=args.seed, apply_rules=not args.no_rules,
                     ruin_level=args.ruin_level, multiplier=args.multiplier, r_haircut=args.r_haircut,
                     demean=args.demean)
    sample: RSample | None = None
    par: ParametricR | None = None
    try:
        if args.closed_trades is not None:
            from goldbot.ops.gates_phase import load_closed_trades
            trades, err = load_closed_trades(args.closed_trades)
            if err:
                print(f"warning: {err}", file=sys.stderr)
            sample = r_from_closed_trades(trades, agent_id=args.agent)
        elif args.shadow is not None:
            models = args.models_dir if args.models_dir is not None else Path(settings.research.models_dir)
            sample = r_from_shadow_book(args.shadow, version=args.version, agent_id=args.agent,
                                        pool=args.pool_versions, models_dir=models)
        elif args.r_file is not None:
            sample = r_from_file(args.r_file)
        elif None not in (args.win_rate, args.avg_win, args.avg_loss):
            par = ParametricR(win_rate=args.win_rate, avg_win_r=args.avg_win, avg_loss_r=args.avg_loss)
        else:
            print("no R source: give --closed-trades, --shadow, --r-file or --win-rate/--avg-win/--avg-loss",
                  file=sys.stderr)
            return 2
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if sample is not None and len(sample.r) == 0:
        print("no trades with R in the source: nothing to resample (results need data)", file=sys.stderr)
        return 2
    if sample is not None:
        rep = simulate(cfg, r=sample.r, cost_basis=sample.cost_basis, versions=sample.versions,
                       exit_utc=sample.exit_utc)
    else:
        rep = simulate(cfg, parametric=par)
    print(format_report(rep))
    if args.out:
        args.out.write_text(rep.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
