"""Drawdown and risk-of-ruin Monte Carlo (playbook G-5, BACKLOG item 20). Reporting only: it changes no setting, trade,
model or trial budget.

Question: with this R distribution, this risk per trade and this many trades a week, how likely is it that the
configured loss limits trip by bad luck alone within N weeks, how deep do drawdowns get, and what is the risk of ruin?

Inputs
* R per trade, in order: closed paper/live trades (`ClosedTrade.r`, state/closed_trades*.jsonl), the shadow book's
  taken closed trades (state/shadow_book.json, R = side x (exit - entry) / |entry - stop|, which pays the spread the
  shadow book trades at but no other cost) or a backtest's trades file (a column `r`, CSV or Parquet). Or parametric:
  win rate, average win and average loss in R (independent draws).
* The live limits from `config/settings.yaml` `risk:` (`RuinLimits.from_settings`): risk per trade (paper 0.5%, or
  tiny-live 0.1%), the daily 2% and weekly 5% caps, the supervisor's 1.5% / 4% combined caps, the drawdown stages
  8% (size down) and 12% (halt), measured from the closed-equity high-water mark, and the 5% stage-1 clear.

Method
* Circular block bootstrap of the R sequence (blocks of consecutive trades, so loss streaks shorter than a block
  survive resampling; default block min(n, max(5, round(n^(1/3))))). The parametric source is i.i.d.
* Trades are spread evenly over the week: trade j closes at (j + 1) / trades_per_week weeks, on trading day
  floor(j x days_per_week / trades_per_week). One position at a time.
* With `apply_rules` (default) the simulation acts like RiskGate does (goldbot/risk/gate.py `check`/`update_stage`):
  after a daily (or supervisor daily) cap trips, the rest of that day's trades are skipped; after a weekly one, the rest
  of the week's; at stage 1 risk per trade is halved until drawdown falls below the clear level; at stage 2 the path
  stops (the owner re-arm is not modelled). The supervisor caps act on combined equity, which in this one-account
  simulation is the account's own equity; with two accounts that is an approximation.
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
from typing import TYPE_CHECKING, Literal, Sequence

import numpy as np
import pandas as pd
from pydantic import Field, model_validator

from goldbot.base import FrozenRecord
from goldbot.config import RiskSettings
from goldbot.data.timeutil import epoch_ns

if TYPE_CHECKING:
    from goldbot.ops.gates_phase import ClosedTrade

_EPS = 1e-9          # a level reached up to float noise counts as reached (the gate compares with >=)
_CHUNK = 2000        # paths simulated at once (memory: chunk x trades floats)


class RuinLimits(FrozenRecord):
    """The account's loss limits as fractions of equity (config/settings.yaml `risk:`)."""

    risk_per_trade: float = Field(gt=0, le=0.05)
    daily_cap: float = Field(gt=0, lt=1)
    weekly_cap: float = Field(gt=0, lt=1)
    supervisor_daily_cap: float = Field(gt=0, lt=1)
    supervisor_weekly_cap: float = Field(gt=0, lt=1)
    drawdown_stage1: float = Field(gt=0, lt=1)
    drawdown_stage2: float = Field(gt=0, lt=1)
    drawdown_stage1_clear: float = Field(gt=0, lt=1)

    @classmethod
    def from_settings(cls, risk: RiskSettings, *, tiny_live: bool = False) -> RuinLimits:
        return cls(risk_per_trade=risk.risk_per_trade_tiny_live if tiny_live else risk.risk_per_trade,
                   daily_cap=risk.daily_cap, weekly_cap=risk.weekly_cap,
                   supervisor_daily_cap=risk.supervisor_daily_cap, supervisor_weekly_cap=risk.supervisor_weekly_cap,
                   drawdown_stage1=risk.drawdown_stage1, drawdown_stage2=risk.drawdown_stage2,
                   drawdown_stage1_clear=risk.drawdown_stage1_clear)


class ParametricR(FrozenRecord):
    """Two-point R distribution: +avg_win_r with probability win_rate, else -avg_loss_r."""

    win_rate: float = Field(gt=0, lt=1)
    avg_win_r: float = Field(gt=0)
    avg_loss_r: float = Field(gt=0)


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


class RuinReport(FrozenRecord):
    source: Literal["bootstrap", "parametric"]
    n_source_trades: int | None
    source_mean_r: float
    source_sd_r: float
    block_length: int | None
    config: RuinConfig
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


def _parametric(par: ParametricR, n_paths: int, n_trades: int, rng: np.random.Generator) -> np.ndarray:
    win = rng.random((n_paths, n_trades)) < par.win_rate
    return np.where(win, par.avg_win_r, -par.avg_loss_r)


def _quantiles(x: np.ndarray) -> Quantiles:
    return Quantiles(mean=round(float(np.mean(x)), 6), median=round(float(np.median(x)), 6),
                     p95=round(float(np.quantile(x, 0.95)), 6), p99=round(float(np.quantile(x, 0.99)), 6))


_BASIS = {"daily_cap": "loss from the risk-day's starting equity",
          "weekly_cap": "loss from the risk-week's starting equity",
          "supervisor_daily_cap": "combined daily loss (this account's equity in a one-account run)",
          "supervisor_weekly_cap": "combined weekly loss (this account's equity in a one-account run)",
          "drawdown_stage1": "drawdown from the closed-equity high-water mark (size down)",
          "drawdown_stage2": "drawdown from the closed-equity high-water mark (halt)"}


# ------------------------------------------------------------------------------------------------ simulation
def simulate(cfg: RuinConfig, *, r: np.ndarray | None = None, parametric: ParametricR | None = None) -> RuinReport:
    """Run the Monte Carlo on an ordered R sample (block bootstrap) or a parametric distribution."""
    if (r is None) == (parametric is None):
        raise ValueError("give exactly one of r and parametric")
    lim = cfg.limits
    n_trades = math.ceil(cfg.weeks * cfg.trades_per_week - _EPS)
    j = np.arange(n_trades)
    week = np.floor(j / cfg.trades_per_week + _EPS).astype(int)
    day = np.floor(j * cfg.days_per_week / cfg.trades_per_week + _EPS).astype(int)
    t_close = (j + 1) / cfg.trades_per_week
    levels = {"daily_cap": lim.daily_cap, "weekly_cap": lim.weekly_cap,
              "supervisor_daily_cap": lim.supervisor_daily_cap, "supervisor_weekly_cap": lim.supervisor_weekly_cap,
              "drawdown_stage1": lim.drawdown_stage1, "drawdown_stage2": lim.drawdown_stage2}
    notes: list[str] = []
    if r is not None:
        sample = np.asarray(r, dtype=float)
        sample = sample[np.isfinite(sample)]
        if len(sample) == 0:
            raise ValueError("no finite R values to resample")
        block = cfg.block_length or default_block_length(len(sample))
        block = min(block, len(sample))
        mean_r, sd_r = float(sample.mean()), float(sample.std(ddof=1)) if len(sample) > 1 else 0.0
        if len(sample) < 30:
            notes.append(f"only {len(sample)} trades: the bootstrap can only recycle what was seen; read as indicative")
    else:
        assert parametric is not None
        sample = np.empty(0)
        block = None
        p = parametric.win_rate
        mean_r = p * parametric.avg_win_r - (1 - p) * parametric.avg_loss_r
        sd_r = math.sqrt(p * (1 - p)) * (parametric.avg_win_r + parametric.avg_loss_r)
    if mean_r <= 0:
        notes.append(f"mean R {mean_r:.3f} <= 0: drawdowns grow without bound and every limit trips eventually")
    if not cfg.apply_rules:
        notes.append("rules off: limits are measured, not enforced (no skipped trades, no size-down, no halt)")

    rng = np.random.default_rng(cfg.seed)
    first = {k: np.empty(0) for k in levels}
    max_dd_all, final_all, taken_all, ruined_all = [], [], [], []
    for c0 in range(0, cfg.n_paths, _CHUNK):
        m = min(_CHUNK, cfg.n_paths - c0)
        if parametric is not None:
            R = _parametric(parametric, m, n_trades, rng)
        else:
            R = block_bootstrap(sample, n_paths=m, n_trades=n_trades, block_length=block or 1, rng=rng)
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
            risk = np.full(m, lim.risk_per_trade)
            if cfg.apply_rules:
                active &= ~halted & ~day_block & ~week_block
                risk = np.where(size_down, 0.5 * lim.risk_per_trade, risk)
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
                hit = (values[k] >= lvl - _EPS) & np.isinf(f[k])
                f[k][hit] = t_close[i]
            max_dd = np.maximum(max_dd, dd)
            ruined |= eq <= 1 - cfg.ruin_level + _EPS
            if cfg.apply_rules:
                halted |= dd >= lim.drawdown_stage2 - _EPS
                size_down = np.where(dd >= lim.drawdown_stage1 - _EPS, True,
                                     np.where(size_down & (dd < lim.drawdown_stage1_clear), False, size_down))
                day_block |= (dl >= lim.daily_cap - _EPS) | (dl >= lim.supervisor_daily_cap - _EPS)
                week_block |= (wl >= lim.weekly_cap - _EPS) | (wl >= lim.supervisor_weekly_cap - _EPS)
        for k in levels:
            first[k] = np.concatenate([first[k], f[k]])
        max_dd_all.append(max_dd)
        final_all.append(eq)
        taken_all.append(taken)
        ruined_all.append(ruined)

    horizons = sorted({*cfg.horizons_weeks, cfg.weeks})
    trips = []
    for k, lvl in levels.items():
        fk = first[k]
        tripped = fk[np.isfinite(fk)]
        trips.append(ThresholdTrip(
            name=k, level=lvl, basis=_BASIS[k], enforced=cfg.apply_rules,
            p_by_weeks={h: round(float(np.mean(fk <= h + _EPS)), 6) for h in horizons},
            median_weeks_to_first=round(float(np.median(tripped)), 4) if len(tripped) else None))
    return RuinReport(source="bootstrap" if r is not None else "parametric",
                      n_source_trades=len(sample) if r is not None else None,
                      source_mean_r=round(mean_r, 6), source_sd_r=round(sd_r, 6), block_length=block, config=cfg,
                      trades_per_path=n_trades, mean_trades_taken=round(float(np.mean(np.concatenate(taken_all))), 4),
                      thresholds=trips, max_drawdown=_quantiles(np.concatenate(max_dd_all)),
                      final_equity=_quantiles(np.concatenate(final_all)),
                      risk_of_ruin=round(float(np.mean(np.concatenate(ruined_all))), 6), notes=notes)


# ------------------------------------------------------------------------------------------------ R sources
def r_from_closed_trades(trades: Sequence[ClosedTrade], *, agent_id: str | None = None, mode: str | None = None) -> np.ndarray:
    """R of closed paper/live trades (gates_phase.ClosedTrade) in exit order; trades without `r` are skipped."""
    rows = [t for t in trades if t.r is not None and (agent_id is None or t.agent_id == agent_id)
            and (mode is None or t.mode == mode)]
    if not rows:
        return np.empty(0)
    order = np.argsort(epoch_ns(pd.DatetimeIndex([t.exit_utc for t in rows])), kind="stable")
    return np.array([rows[i].r for i in order], dtype=float)


def r_from_shadow_book(state_dir: Path, *, agent_id: str | None = None) -> np.ndarray:
    """R of the shadow book's taken, closed trades (state/shadow_book.json, every version) in exit order:
    side x (exit - entry) / |entry - stop|."""
    from goldbot.engine.shadow import VersionBook
    path = Path(state_dir) / "shadow_book.json"
    if not path.exists():
        return np.empty(0)
    raw = json.loads(path.read_text())
    rows = [t for v in raw.values() for t in VersionBook.model_validate(v).closed
            if t.taken and t.exit is not None and t.exit_ts is not None and t.entry != t.stop
            and (agent_id is None or t.agent_id == agent_id)]
    if not rows:
        return np.empty(0)
    order = np.argsort(epoch_ns(pd.DatetimeIndex([t.exit_ts for t in rows])), kind="stable")
    return np.array([rows[i].side * (float(rows[i].exit or 0.0) - rows[i].entry) / abs(rows[i].entry - rows[i].stop)
                     for i in order], dtype=float)


def r_from_file(path: Path) -> np.ndarray:
    """A backtest's trades: a CSV or Parquet file with a column `r`, in trade order."""
    df = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
    if "r" not in df.columns:
        raise ValueError(f"{path} has no column 'r'")
    return df["r"].to_numpy(dtype=float)


# ------------------------------------------------------------------------------------------------ CLI
def format_report(rep: RuinReport) -> str:
    c = rep.config
    src = (f"bootstrap of {rep.n_source_trades} trades (block {rep.block_length})" if rep.source == "bootstrap"
           else "parametric")
    lines = [f"Drawdown Monte Carlo: {src}, mean R {rep.source_mean_r:+.3f} (sd {rep.source_sd_r:.3f}), "
             f"risk {c.limits.risk_per_trade:.2%}/trade, {c.trades_per_week:g} trades/week, {c.weeks} weeks, "
             f"{c.n_paths} paths, seed {c.seed}, rules {'on' if c.apply_rules else 'off'}",
             "threshold              level  " + "  ".join(f"P(<= {h}w)" for h in sorted(rep.thresholds[0].p_by_weeks))
             + "  median weeks to first"]
    for t in rep.thresholds:
        ps = "  ".join(f"{p:>9.1%}" for _, p in sorted(t.p_by_weeks.items()))
        med = f"{t.median_weeks_to_first:.1f}" if t.median_weeks_to_first is not None else "-"
        lines.append(f"{t.name:<22} {t.level:>5.1%}  {ps}  {med}")
    d = rep.max_drawdown
    lines.append(f"max drawdown: median {d.median:.2%}, 95th {d.p95:.2%}, 99th {d.p99:.2%}")
    lines.append(f"final equity x start: median {rep.final_equity.median:.3f}; trades taken per path "
                 f"{rep.mean_trades_taken:.0f} of {rep.trades_per_path}")
    lines.append(f"risk of ruin (equity <= {1 - c.ruin_level:.0%} of start within {c.weeks} weeks): {rep.risk_of_ruin:.2%}")
    lines += [f"note: {n}" for n in rep.notes]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="run.py ruin", description=__doc__.split("\n\n")[0] if __doc__ else None)
    src = ap.add_argument_group("R source (one)")
    src.add_argument("--closed-trades", type=Path, help="state dir with closed_trades*.jsonl (paper/live R)")
    src.add_argument("--shadow", type=Path, help="state dir with shadow_book.json (taken closed shadow trades)")
    src.add_argument("--r-file", type=Path, help="backtest trades, CSV or Parquet with a column r")
    src.add_argument("--win-rate", type=float)
    src.add_argument("--avg-win", type=float, help="average win in R (parametric)")
    src.add_argument("--avg-loss", type=float, help="average loss in R, positive (parametric)")
    ap.add_argument("--agent", default=None, help="only this agent's trades (closed-trade and shadow sources)")
    ap.add_argument("--trades-per-week", type=float, required=True)
    ap.add_argument("--weeks", type=int, default=52)
    ap.add_argument("--horizons", type=int, nargs="*", default=None, help="weeks to report P(trip) within (default 26 52)")
    ap.add_argument("--paths", type=int, default=10_000)
    ap.add_argument("--block", type=int, default=None, help="bootstrap block length in trades")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--risk", type=float, default=None, help="risk per trade (default: settings risk.risk_per_trade)")
    ap.add_argument("--tiny-live", action="store_true", help="use risk.risk_per_trade_tiny_live")
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
                     ruin_level=args.ruin_level)
    r: np.ndarray | None = None
    par: ParametricR | None = None
    if args.closed_trades is not None:
        from goldbot.ops.gates_phase import load_closed_trades
        trades, err = load_closed_trades(args.closed_trades)
        if err:
            print(f"warning: {err}", file=sys.stderr)
        r = r_from_closed_trades(trades, agent_id=args.agent)
    elif args.shadow is not None:
        r = r_from_shadow_book(args.shadow, agent_id=args.agent)
    elif args.r_file is not None:
        r = r_from_file(args.r_file)
    elif None not in (args.win_rate, args.avg_win, args.avg_loss):
        par = ParametricR(win_rate=args.win_rate, avg_win_r=args.avg_win, avg_loss_r=args.avg_loss)
    else:
        print("no R source: give --closed-trades, --shadow, --r-file or --win-rate/--avg-win/--avg-loss",
              file=sys.stderr)
        return 2
    if r is not None and len(r) == 0:
        print("no trades with R in the source: nothing to resample (results need data)", file=sys.stderr)
        return 2
    rep = simulate(cfg, r=r, parametric=par)
    print(format_report(rep))
    if args.out:
        args.out.write_text(rep.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
