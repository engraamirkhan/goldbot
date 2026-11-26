"""Champion/challenger promotion gates (design: Retraining and promotion).

A retrained model is a challenger that shadow-trades at live prices for at least four weeks AND 40 trades
(whichever comes later). It is promoted only when:
* shadow Sharpe is above 0.8 annualised and no more than 0.5 below its backtest Sharpe,
* shadow hit rate is within one binomial standard error of the backtest's expected hit rate,
* shadow max drawdown is under 1.5x the backtest's,
* turnover is within 30% of the current champion's (skipped when there is no champion yet).
Specialist promotion is automatic when every gate passes; allocator, rule and label changes need the owner.

Clustered effective N (research director ruling 2026-10-19): the shadow book holds up to 3 concurrent trades per agent,
so same-day trades share one move and carry less evidence than their count. The 40 counts INDEPENDENT CLUSTERS, one
per agent per session day (`timeutil.opportunity_day` of the entry: [D-1 22:00, D 22:00) UTC), not raw trades; the
shadow Sharpe is computed on the cluster sums (annualised by clusters a year), the hit rate's binomial SE uses the
cluster count, and `t_stat` is the cluster-robust t of the mean trade return. The 4-week minimum is unchanged.

ADR 0106 (owner, 2026-11-26), stricter only, as ruled by the quant on #490 (P1-P4, closing texts quoted):
* decision 6, "A challenger replaces the live model only after beating it for 30-60 days on paper or demo, after
  costs": `paired_day_test` reads the challenger minus the live model per session day (`session_days`, a book with no
  trade that day books 0), on their as-if real-money daily P&L. Fewer than MIN_PAIRED_DAYS paired days is not ready;
  the read stops at MAX_PAIRED_DAYS (the first 60 from the challenger's start: a rolling window could be shopped).
  "Looks happen only at paired days n in LOOK_DAYS = (30, 35, 40, 45, 50, 55, 60). On any other day the read is
  'waiting', with no bound computed or logged. The bound at look k is the basic one-sided bootstrap bound at alpha_k
  from the O'Brien-Fleming table (c = 1.8117 for these looks, overall one-sided 0.05 under independent increments;
  table pinned in code and in a test). Promote at the first look with LB > 0. At 60 the decision is final."
  The bootstrap is the card's stationary day-block draws (eval2.confirm). "Mean block = max(5, ceil(1.5 x the mean
  holding episode length in session days of either book over the window)), the ADR 0085 Q5 rule (E1 lag), recorded
  with the read." "Challenger and live are replayed as two separate as-if accounts with identical start equity,
  start day, ADR 0106 constants and cost model. Each runs as it would alone. The live book is the live model as
  deployed each day, with its sha logged per day." "The challenger is a procedure (ADR 0085 N18). Retrained children
  inside the window are pooled under the procedure, and every forward row is tagged with its scoring sha. Each
  challenger's paired read is one registry trial (K +1), booked at its first look" (`book_first_look`). With no live
  model the challenger is compared with a flat book.
* decision 2, "Every promotion read ... is judged on the trades as if sized under the real-money limits ..., and a
  model that would breach them is not promoted": `as_if` (research/as_if_sizing.py) must be supplied and clean; its
  sized session days cap the cluster count (a trade below the minimum lot never counts toward the 40).
* goal "Live Sharpe ratio above 1.0": MIN_SHADOW_SHARPE 1.0 (was 0.8).
Missing evidence (no paired read, no as-if read) is "not ready", never a retirement: the challenger waits.
"""
from __future__ import annotations

import hashlib
import math
from collections.abc import Hashable, Sequence
from datetime import date, timedelta
from typing import TYPE_CHECKING, Literal

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord
from goldbot.data.timeutil import opportunity_day
from goldbot.research.as_if_sizing import AsIfReport
from goldbot.research.cusum import DEFAULT_K, FALSE_ALARM_QUARTER, WATCH_WEEKS, calibrated_h

if TYPE_CHECKING:          # the engine imports this module: keep the registry out of its import graph
    from goldbot.research.registry import TrialRegistry

MIN_SHADOW_WEEKS = 4.0
MIN_SHADOW_TRADES = 40
MIN_SHADOW_SHARPE = 1.0          # ADR 0106 goal "Live Sharpe ratio above 1.0" (was 0.8); stricter only
# ADR 0106 decision 6: challenger vs live, paired by session day, after costs (module docstring)
MIN_PAIRED_DAYS = 30
MAX_PAIRED_DAYS = 60
PAIRED_ALPHA = 0.05              # one-sided, over every look of one challenger
LOOK_DAYS: tuple[int, ...] = (30, 35, 40, 45, 50, 55, 60)
# quant ruling on #490 (P1): O'Brien-Fleming for these 7 looks, alpha_k = 1 - Phi(OBF_C / sqrt(n_k / 60)); pinned
# (tests/test_promotion_adr0106.py recomputes it and checks the overall level)
OBF_C = 1.8117
OBF_ALPHA: dict[int, float] = {30: 0.00520, 35: 0.00884, 40: 0.01325, 45: 0.01822, 50: 0.02359, 55: 0.02923,
                               60: 0.03502}
MIN_BLOCK_DAYS = 5               # mean block floor; the rule is max(5, ceil(1.5 x mean holding episode days))
BLOCK_EPISODE_MULT = 1.5
PAIRED_RESAMPLES = 10_000       # = eval2.confirm.RESAMPLES_MIN (>= 20 beyond the 0.5% tail)
PAIRED_READ = "paired_read"     # registry status of a challenger procedure's paired read: one trial (K +1)
MAX_SHARPE_SHORTFALL = 0.5
MAX_DD_RATIO = 1.5
MAX_TURNOVER_CHANGE = 0.30

# H-52 (docs/research/prereg-2026-10-19-live-review.md 1.1, rollout-plan-2026-10-19.md action 5a): until the engine's
# history-gap fix was deployed on the brain (2026-10-19 13:49 UTC) the live decision frames had a two-week hole after
# every restart, so a shadow trade OPENED (entry_ts) before this instant is quarantined as `history_gap`: it stays in
# the book (no survivorship) but is never clustered, scored or counted as promotion evidence. Dated, never moved
# forward to admit trades; a later fault gets its own dated marker.
EVIDENCE_START_UTC = pd.Timestamp("2026-10-19 13:49", tz="UTC")
QUARANTINE_HISTORY_GAP = "history_gap"


def quarantine_reason(entry_ts: pd.Timestamp, evidence_start: pd.Timestamp | None = EVIDENCE_START_UTC) -> str | None:
    """`history_gap` for a trade opened before `evidence_start`, else None (None evidence_start: nothing quarantined)."""
    if evidence_start is None:
        return None
    return QUARANTINE_HISTORY_GAP if pd.Timestamp(entry_ts) < evidence_start else None


class PerfStats(FrozenRecord):
    n_trades: int = Field(ge=0)
    sharpe_ann: float
    hit_rate: float = Field(ge=0, le=1)
    max_dd: float = Field(ge=0)
    trades_per_week: float = Field(ge=0)
    weeks: float = Field(0.0, ge=0)
    mean_ret: float | None = None      # per-trade return moments, used by the CUSUM alarm
    std_ret: float | None = None
    # ruling 2026-10-19: independent clusters (agent x session day) and the cluster-robust t of the mean trade return.
    # None: a record written before clustering (a shadow record without it is never ready: fail closed)
    n_clusters: int | None = Field(None, ge=0)
    t_stat: float | None = None


def effective_n(stats: PerfStats) -> int:
    """The shadow sample size the gates read: independent clusters; 0 for a record without them (fail closed)."""
    return stats.n_clusters if stats.n_clusters is not None else 0


def cluster_sums(ret: Sequence[float], clusters: Sequence[Hashable]) -> np.ndarray:
    """One summed return per cluster, in first-seen order."""
    order: dict[Hashable, int] = {}
    for c in clusters:
        order.setdefault(c, len(order))
    out = np.zeros(len(order))
    for r, c in zip(ret, clusters, strict=True):
        out[order[c]] += float(r)
    return out


def cluster_t_stat(ret: Sequence[float], clusters: Sequence[Hashable]) -> float | None:
    """Cluster-robust (CR1) t of the mean trade return: var(mean) = G/(G-1) x sum over clusters of the squared summed
    residuals / N^2. None below two clusters or with no dispersion."""
    r = np.asarray(ret, dtype=float)
    n = len(r)
    if n == 0:
        return None
    m = float(r.mean())
    e = cluster_sums((r - m).tolist(), clusters)
    g = len(e)
    if g < 2:
        return None
    var = g / (g - 1) * float(np.sum(e ** 2)) / n ** 2
    return m / math.sqrt(var) if var > 0 else None


def session_days(start: date, end: date) -> list[date]:
    """The Monday-Friday session days (`timeutil.opportunity_day` labels) from `start` to `end`, both included."""
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


class PairedRead(FrozenRecord):
    """ADR 0106 decision 6: the challenger minus the live model, per session day, after costs (module docstring)."""
    procedure: str                     # the challenger procedure (ADR 0085 N18)
    status: Literal["not_ready", "waiting", "look"]
    n_days: int = Field(ge=0)
    window_start: date
    window_end: date                   # the last paired day read (the 60th when capped)
    capped: bool                       # the window held more than MAX_PAIRED_DAYS: read at MAX_PAIRED_DAYS
    live_present: bool                 # False: compared with a flat book (no live model)
    mean_diff: float                   # mean daily difference, fraction of the day-start as-if equity
    lower_bound: float | None          # one-sided at alpha_look; None unless status is "look"
    alpha_look: float | None
    mean_episode_days: float           # the longer mean holding episode of the two books
    mean_block: float
    resamples: int
    challenger_shas: list[str]         # every scoring sha pooled under the procedure
    live_shas_by_day: dict[date, list[str]] = Field(default_factory=dict)   # the live model as deployed each day


def mean_episode_days(rep: AsIfReport, grid: Sequence[date]) -> float:
    """Mean length, in session days of `grid`, of the runs of consecutive grid days on which the book holds at least
    one sized trade (entry to exit day, both included). 0 without one."""
    pos = {d: i for i, d in enumerate(grid)}
    held = np.zeros(len(grid), dtype=bool)
    for t in rep.trades:
        if t.status != "sized":
            continue
        lo = next((pos[d] for d in grid if d >= t.entry_day), None)
        hi = max((pos[d] for d in grid if d <= t.day), default=None)
        if lo is not None and hi is not None and hi >= lo:
            held[lo:hi + 1] = True
    runs, n = [], 0
    for h in held:
        if h:
            n += 1
        elif n:
            runs.append(n)
            n = 0
    if n:
        runs.append(n)
    return float(np.mean(runs)) if runs else 0.0


def paired_day_test(challenger: AsIfReport, live: AsIfReport | None, *, procedure: str, end: date,
                    resamples: int = PAIRED_RESAMPLES) -> PairedRead:
    """The paired read of ADR 0106 decision 6 (module docstring). Two separate as-if accounts: `live` must share the
    challenger's start and start equity (ValueError otherwise), and every sized challenger row must carry its
    scoring sha. The grid is every session day from the challenger's start day to `end`, plus any day either book
    has P&L on (a weekend crypto close), in order, cut to the first MAX_PAIRED_DAYS. A day missing from a book is 0
    for it; `live` None: a flat book."""
    if challenger.start_utc is None:
        raise ValueError("the challenger's as-if account needs its start (as_if_real_money(start=...))")
    if live is not None and (live.start_utc != challenger.start_utc or live.equity_start != challenger.equity_start):
        raise ValueError("challenger and live must be replayed from the same start and start equity (two accounts)")
    unsigned = [t.index for t in challenger.trades if t.status == "sized" and not t.scoring_sha]
    if unsigned:
        raise ValueError(f"{len(unsigned)} challenger row(s) without their scoring sha (ADR 0085 N18)")
    start = opportunity_day(pd.DatetimeIndex([challenger.start_utc]))[0].date()
    ch, lv = challenger.daily, (live.daily if live is not None else {})
    grid = sorted({*session_days(start, end), *(d for d in (*ch, *lv) if start <= d <= end)})
    capped = len(grid) > MAX_PAIRED_DAYS
    grid = grid[:MAX_PAIRED_DAYS]
    x = np.array([float(ch.get(d, 0.0)) - float(lv.get(d, 0.0)) for d in grid])
    if not np.isfinite(x).all():
        raise ValueError("every paired day must be finite")
    n = len(x)
    mean = float(x.mean()) if n else 0.0
    episode = max(mean_episode_days(challenger, grid), mean_episode_days(live, grid) if live is not None else 0.0)
    block = float(max(MIN_BLOCK_DAYS, math.ceil(BLOCK_EPISODE_MULT * episode)))
    status: Literal["not_ready", "waiting", "look"] = (
        "not_ready" if n < MIN_PAIRED_DAYS else "look" if n in LOOK_DAYS else "waiting")
    lb: float | None = None
    alpha = OBF_ALPHA.get(n) if status == "look" else None
    if alpha is not None:
        # the card's day-block draws; imported here so the engine's import of this module stays light
        from goldbot.research.eval2.confirm import _day_count_chunks, _DayBlocks
        blocks = _DayBlocks(pd.DataFrame({"day": np.arange(n)}))
        key = hashlib.sha256(f"adr0106-paired|{procedure}|{grid[0]}|{grid[-1]}".encode()).hexdigest()
        t = np.concatenate([(c @ x) / n for c in _day_count_chunks(blocks, mean_block=block, resamples=resamples,
                                                                  seed_key=key)])
        lb = 2.0 * mean - float(np.quantile(t, 1.0 - alpha))
    in_window = set(grid)
    return PairedRead(procedure=procedure, status=status, n_days=n, window_start=start,
                      window_end=grid[-1] if grid else start, capped=capped, live_present=live is not None,
                      mean_diff=mean, lower_bound=lb, alpha_look=alpha, mean_episode_days=episode, mean_block=block,
                      resamples=resamples,
                      challenger_shas=sorted({t.scoring_sha for t in challenger.trades
                                              if t.status == "sized" and t.scoring_sha}),
                      live_shas_by_day={d: v for d, v in (live.shas_by_day if live is not None else {}).items()
                                        if d in in_window})


def book_first_look(registry: TrialRegistry, read: PairedRead, *, agent_id: str, feature_version: str) -> dict | None:
    """Book the procedure's paired read as one registry trial (K +1) at its first look, once: None when the read is
    not a look or the procedure already has its row. Under the registry lock."""
    if read.status != "look":
        return None
    config = {"procedure": read.procedure, "window_start": read.window_start.isoformat()}
    with registry.locked():
        if any(r.get("status") == PAIRED_READ and (r.get("config") or {}).get("procedure") == read.procedure
               for r in registry._rows()):
            return None
        return registry.record(agent_id=agent_id, family=f"challenger:{read.procedure}", config=config,
                               feature_version=feature_version, status=PAIRED_READ,
                               rationale="ADR 0106 decision 6: challenger vs live paired read, first look",
                               results={"paired_read": read.model_dump(mode="json")})


class GateCheck(FrozenRecord):
    name: str
    passed: bool
    detail: str


class PromotionDecision(FrozenRecord):
    promote: bool
    ready: bool                 # enough shadow history to judge at all
    checks: list[GateCheck]

    @property
    def failed(self) -> list[str]:
        return [c.name for c in self.checks if not c.passed]


def evaluate_promotion(backtest: PerfStats, shadow: PerfStats, champion: PerfStats | None = None, *,
                       paired: PairedRead | None = None, as_if: AsIfReport | None = None) -> PromotionDecision:
    """Every gate of the module docstring. `ready` is True only when the decision is final: promoted, a limit breached
    under as-if sizing, or the paired window complete (MAX_PAIRED_DAYS) with a gate failed. Missing or short evidence
    is not ready (the challenger waits; it is never retired for evidence it has not had time to build)."""
    n_eff = effective_n(shadow)
    sized = as_if.n_sized_days if as_if is not None else 0
    n_power = min(n_eff, sized)
    enough = shadow.weeks >= MIN_SHADOW_WEEKS and n_power >= MIN_SHADOW_TRADES
    n_paired = paired.n_days if paired is not None else 0
    checks = [GateCheck(name="shadow_length", passed=enough,
                        detail=f"{shadow.weeks:.1f} weeks / {n_eff} independent clusters ({shadow.n_trades} trades), "
                               f"{sized} as-if sized session days (need {MIN_SHADOW_WEEKS:.0f} and "
                               f"{MIN_SHADOW_TRADES} clusters, as-if sized)"),
              GateCheck(name="paired_window", passed=n_paired >= MIN_PAIRED_DAYS,
                        detail=f"{n_paired} paired session days vs the live model (need {MIN_PAIRED_DAYS}, read at "
                               f"{MAX_PAIRED_DAYS}; ADR 0106)" if paired is not None else "no paired read vs the live model"),
              GateCheck(name="as_if_real_money", passed=as_if is not None and as_if.clean,
                        detail=as_if.text() if as_if is not None else "no as-if real-money read (ADR 0106 decision 2)")]
    if as_if is not None and not as_if.clean:          # final: more days cannot undo a breach
        return PromotionDecision(promote=False, ready=True, checks=checks)
    if paired is None or not all(c.passed for c in checks):
        return PromotionDecision(promote=False, ready=False, checks=checks)
    if paired.status != "look":                        # off a look day: waiting, no bound computed or logged
        checks.append(GateCheck(name="paired_vs_live", passed=False,
                                detail=f"waiting: paired day {paired.n_days} is not a look day {LOOK_DAYS}"))
        return PromotionDecision(promote=False, ready=False, checks=checks)
    lb = paired.lower_bound
    checks.append(GateCheck(name="paired_vs_live", passed=lb is not None and lb > 0,
                            detail=f"challenger - {'live' if paired.live_present else 'flat book (no live model)'}: "
                                   f"mean {paired.mean_diff:+.5f}/day, one-sided lower bound "
                                   f"{'n/a' if lb is None else format(lb, '+.5f')} at O'Brien-Fleming alpha "
                                   f"{paired.alpha_look or 0:.5f}, look day {paired.n_days}, mean block "
                                   f"{paired.mean_block:.0f} (needs > 0)"))
    checks.append(GateCheck(name="sharpe_floor", passed=shadow.sharpe_ann > MIN_SHADOW_SHARPE,
                            detail=f"shadow Sharpe {shadow.sharpe_ann:.2f} vs floor {MIN_SHADOW_SHARPE} (ADR 0106)"))
    checks.append(GateCheck(name="sharpe_vs_backtest", passed=shadow.sharpe_ann >= backtest.sharpe_ann - MAX_SHARPE_SHORTFALL,
                            detail=f"shadow {shadow.sharpe_ann:.2f} vs backtest {backtest.sharpe_ann:.2f} (max shortfall {MAX_SHARPE_SHORTFALL})"))
    p = backtest.hit_rate
    se = math.sqrt(max(p * (1 - p), 1e-12) / n_power)        # effective N: the as-if sized cluster count
    checks.append(GateCheck(name="hit_rate", passed=abs(shadow.hit_rate - p) <= se,
                            detail=f"shadow {shadow.hit_rate:.3f} vs expected {p:.3f} ± {se:.3f} (1 binomial SE)"))
    dd_cap = MAX_DD_RATIO * backtest.max_dd
    checks.append(GateCheck(name="drawdown", passed=shadow.max_dd < dd_cap,
                            detail=f"shadow max DD {shadow.max_dd:.3f} vs cap {dd_cap:.3f}"))
    if champion is not None and champion.trades_per_week > 0:
        change = abs(shadow.trades_per_week - champion.trades_per_week) / champion.trades_per_week
        checks.append(GateCheck(name="turnover", passed=change <= MAX_TURNOVER_CHANGE,
                                detail=f"{shadow.trades_per_week:.1f}/week vs champion {champion.trades_per_week:.1f} ({change:.0%} change)"))
    promote = all(c.passed for c in checks)
    return PromotionDecision(promote=promote, ready=promote or n_paired >= MAX_PAIRED_DAYS, checks=checks)


def cusum_alarm(returns: list[float], expected_mean: float, expected_std: float, k: float = DEFAULT_K,
                h: float | None = None, trades_per_week: float | None = None,
                false_alarm: float = FALSE_ALARM_QUARTER, p: float | None = None,
                weeks: float = WATCH_WEEKS, trades: int | None = None) -> bool:
    """One-sided CUSUM on standardised trade returns for a downward shift from the backtest's mean (design: a new
    champion that trips the alarm in its first two weeks is replaced by the previous one). k is the allowance and
    h the decision interval, both in standard deviations. Row M25: unless `h` is given, h is tuned so that returns
    at the backtest's mean alarm within the watch's expected trades (backtest `trades_per_week` x `weeks`, two weeks)
    with probability at most `false_alarm` (5%; research/cusum.py). In control a trade wins with the backtest's hit
    rate `p`, so its standardised return is two-point and h is simulated that way (normal when p is None); without a
    trade rate the fixed FALLBACK_H applies. `trades`: calibrate for exactly this many trades (the watch's
    `cusum.watch_trades`) instead of `weeks` of the trade rate."""
    if expected_std <= 0 or not returns:
        return False
    h = calibrated_h(trades_per_week, k, false_alarm, p=p, weeks=weeks, trades=trades) if h is None else h
    s = 0.0
    for r in returns:
        s = max(0.0, s + (expected_mean - r) / expected_std - k)
        if s > h:
            return True
    return False
