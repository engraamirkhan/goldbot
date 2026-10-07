"""The scheduler's jobs (design: Retraining and promotion, Bounded research loop, Execution costs).

* nightly_costs     per enabled account: spread table from its own ticks, slippage table from its own fills,
                    commission from settings -> state/costs_<account>.json (read by the engine every bar).
                    On Fridays it also re-runs the account classifier (two consecutive disagreements to change).
* saturday_retrain  refresh bars from the data release (best effort), then per specialist: evaluate any challenger
                    that has a shadow record against the promotion gates (promote automatically when all pass),
                    and retrain on the rolling window. A family keeps at most one challenger in shadow; a new one
                    replaces it only after it is decided or has been in shadow for CHALLENGER_MAX_WEEKS.
* tournament        weekly after the retrain: the population round (fitness, retirement, promotion to live,
                    cloning, capital shares) -> state/agents.json for the dashboard's league table.
* research_director weekly before the staff agents: scores every family's evidence (trial registry, shadow book,
                    population) and splits what is left of the quarter's trial budget -> state/research_plan.json
                    (research/director.py). It decides what to research, never what goes live: promotion stays with
                    the gates. Trials scored on the held-out year are never used as evidence.
* model_watch       daily: a champion promoted in the last CUSUM_WINDOW_DAYS whose shadow returns trip the CUSUM
                    alarm against its backtest is replaced by the previous champion (design: Retraining and promotion).
* monthly_research  bounded search: label-grid variants (+-step on target, stop and time limit) per specialist, as
                    many as the research plan's grid share gives the family (`trial_budget_per_month` each without a
                    fresh plan), never past the quarter's trial budget and never into the held-out year, each a
                    walk-forward recorded in the trial registry whose count feeds the deflated Sharpe; a markdown summary is written to state/research_<YYYY-MM>.md.
"""
from __future__ import annotations

import functools
import itertools
import json
import logging
import random
from pathlib import Path
from typing import Any, Callable, cast

import pandas as pd

from goldbot.agents.roles import ROLES
from goldbot.agents.runner import AgentRunner
from goldbot.base import Record
from goldbot.config import DecisionTimeframe, Settings
from goldbot.data.store import Store
from goldbot.engine.shadow import ShadowBook
from goldbot.execution.classifier import PersistentClassifier, classify
from goldbot.execution.costs import build_cost_table
from goldbot.features.mtf import TF_LABEL, context_tfs
from goldbot.ops.accounts import Account
from goldbot.ops.scheduler import Schedule, Scheduler
from goldbot.research.director import (
    PLAN_FILE,
    AgentEvidence,
    ResearchPlan,
    ShadowEvidence,
    build_plan,
    holdout_window,
    quarter_budget,
    quarter_usage,
)
from goldbot.research.model_registry import ModelEntry, ModelRegistry
from goldbot.research.pipeline import ResearchResult, run_specialist
from goldbot.research.population import Population
from goldbot.research.promotion import PerfStats, cusum_alarm, evaluate_promotion
from goldbot.research.registry import TrialRegistry
from goldbot.research.registry_sync import read_rows
from goldbot.specialists import SPECIALISTS
from goldbot.specialists.base import Specialist

log = logging.getLogger("goldbot.jobs")

CHALLENGER_MAX_WEEKS = 8
CLASSIFIER_WEEKDAY = 4          # Friday's nightly run re-classifies (design: weekly, from the nightly cost job)
CONTEXT_EXTRA_MONTHS = 2        # daily/4h/1h context needs history before the decision window starts
CUSUM_WINDOW_DAYS = 14          # a new champion is watched for its first two weeks
PLAN_MAX_AGE_DAYS = 21          # an older research plan is stale evidence: monthly_research falls back to the flat budget


class JobContext(Record):
    settings: Settings
    store: Store
    state_dir: Path
    models: ModelRegistry
    trials: TrialRegistry
    accounts: list[Account]
    sync_bars: Callable[[Store], dict[str, int]] | None = None   # release -> store refresh before retraining
    sync_trials: Callable[[Path], int] | None = None            # union the trial registry with the release copy
    population: Population
    agent_runner: AgentRunner | None = None                      # None when no Anthropic API key is in the keyring
    fetch_calendar: Callable[[], str] | None = None              # Forex Factory weekly JSON (network, VPS only)


# ---------------------------------------------------------------------------------------------- nightly costs
def nightly_costs(ctx: JobContext, slot: pd.Timestamp) -> dict[str, Any]:
    r = ctx.settings.research
    out: dict[str, Any] = {}
    for acc in ctx.accounts:
        ticks = ctx.store.read("ticks", source=acc.account_id, symbol=acc.symbol, start=slot - pd.Timedelta(days=r.cost_window_days))
        fills = ctx.store.read("fills", source=acc.account_id, symbol=acc.symbol, start=slot - pd.Timedelta(days=r.fills_window_days))
        table = build_cost_table(acc.account_id, ticks, fills,
                                 commission_per_lot_side_usd=ctx.settings.costs.commission_per_lot_side_usd.get(acc.broker, 0.0),
                                 slippage_prior_usd=ctx.settings.costs.slippage_prior_usd,
                                 min_fills=r.min_fills_for_slippage, now=slot)
        table.save(ctx.state_dir / f"costs_{acc.account_id}.json")
        row: dict[str, Any] = {"ticks": len(ticks), "fills": len(fills),
                               "round_trip_usd": {s: table.round_trip_usd_per_oz(s) for s in ("asia", "london", "newyork")}}
        if slot.dayofweek == CLASSIFIER_WEEKDAY:
            c = classify(ticks, fills)
            row["account_class"] = PersistentClassifier(ctx.state_dir / f"classifier_{acc.account_id}.json").update(c)
            row["classifier_reason"] = c.reason
        out[acc.account_id] = row
    return out


# ---------------------------------------------------------------------------------------------- retrain
def _bars(ctx: JobContext, tf: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    return ctx.store.read(f"bars_{tf}", start=start, end=end).drop(columns=["source", "symbol", "year", "month"], errors="ignore")


def _walk_forward(ctx: JobContext, spec: Specialist, end: pd.Timestamp, months: int, n_trials: int = 1) -> ResearchResult | None:
    start = end - pd.DateOffset(months=months)
    dec = _bars(ctx, spec.timeframe, start, end)
    if dec.empty:
        return None
    ctx_start = start - pd.DateOffset(months=CONTEXT_EXTRA_MONTHS)
    context = {TF_LABEL[tf]: _bars(ctx, tf, ctx_start, end) for tf in context_tfs(spec.timeframe)}
    years = max((pd.to_datetime(dec["ts_utc"].iloc[-1]) - pd.to_datetime(dec["ts_utc"].iloc[0])).days / 365.25, 1e-9)
    res = run_specialist(spec, dec, context=context, n_trials=n_trials)
    if res.n_candidates:
        res.metrics["trades_per_year"] = res.n_candidates / years
    return res


def backtest_stats(res: ResearchResult) -> PerfStats | None:
    s = res.metrics.get("model_filtered") or {}
    if not s.get("n"):
        return None
    tpy = float(res.metrics.get("trades_per_year", 0.0)) * s["n"] / max(res.metrics.get("all_candidates", {}).get("n", s["n"]), 1)
    return PerfStats(n_trades=int(s["n"]), sharpe_ann=float(s["sharpe_ann"]), hit_rate=float(s["hit_rate"]),
                     max_dd=float(s["max_dd"]), trades_per_week=tpy / 52.0, mean_ret=s.get("mean_ret"), std_ret=s.get("std_ret"))


def shadow_stats(ctx: JobContext, entry: ModelEntry) -> PerfStats | None:
    """Shadow record written by the engine's shadow book (state/shadow_<version>.json), if any yet."""
    f = ctx.state_dir / f"shadow_{entry.version}.json"
    return PerfStats.model_validate(json.loads(f.read_text())) if f.exists() else None


def _decide_challengers(ctx: JobContext, agent_id: str, slot: pd.Timestamp) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    champion = ctx.models.champion(agent_id)
    champ_shadow = shadow_stats(ctx, champion) if champion else None
    champ_turnover = champ_shadow or (PerfStats.model_validate(champion.backtest) if champion and champion.backtest else None)
    for ch in ctx.models.by_agent(agent_id, "challenger"):
        bt = PerfStats.model_validate(ch.backtest) if ch.backtest else None
        sh = shadow_stats(ctx, ch)
        if bt is not None and sh is not None:
            d = evaluate_promotion(bt, sh, champ_turnover)
            if d.promote:
                ctx.models.promote(ch.version, now=slot, note="all promotion gates passed")
                out.append({"version": ch.version, "action": "promoted"})
                continue
            if d.ready:
                ctx.models.retire(ch.version, "failed promotion gates: " + ", ".join(d.failed))
                out.append({"version": ch.version, "action": "retired", "failed": d.failed})
                continue
        if slot - ch.created_utc > pd.Timedelta(weeks=CHALLENGER_MAX_WEEKS):
            ctx.models.retire(ch.version, f"no decision after {CHALLENGER_MAX_WEEKS} weeks in shadow")
            out.append({"version": ch.version, "action": "expired"})
        else:
            out.append({"version": ch.version, "action": "waiting", "shadow_trades": sh.n_trades if sh else 0})
    return out


def saturday_retrain(ctx: JobContext, slot: pd.Timestamp) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if ctx.sync_bars is not None:
        try:
            out["bars_synced"] = ctx.sync_bars(ctx.store)
        except Exception as exc:   # network trouble must not stop the retrain on what we already have
            log.warning("bar sync failed, retraining on the store as is: %s", exc)
            out["bars_synced"] = f"failed: {exc}"
    ctx.population.ensure_founders(slot)
    for m in sorted(ctx.population.active("live", "shadow"), key=lambda m: m.agent_id):
        agent: dict[str, Any] = {"challengers": _decide_challengers(ctx, m.agent_id, slot)}
        if ctx.models.by_agent(m.agent_id, "challenger"):
            agent["retrain"] = "skipped: a challenger is still in shadow"
            out[m.agent_id] = agent
            continue
        spec = m.specialist()
        if spec.timeframe not in ("15m", "1h"):
            agent["retrain"] = f"skipped: no walk-forward window configured for {spec.timeframe}"
            out[m.agent_id] = agent
            continue
        wf = ctx.settings.walkforward[cast(DecisionTimeframe, spec.timeframe)]
        # rolling train window plus enough test history for out-of-fold backtest stats
        res = _walk_forward(ctx, spec, slot, wf.train_months + 4 * wf.test_months)
        if res is None or res.model is None:
            agent["retrain"] = "no model: not enough bars or candidates"
        else:
            bt = backtest_stats(res)
            e = ctx.models.add_challenger(res.model, family=m.family, agent_id=m.agent_id,
                                          backtest=bt.model_dump() if bt else {}, now=slot,
                                          notes=[f"walk-forward {res.n_folds} folds, {res.n_candidates} candidates"])
            # the challenger shadow-trades from now on; it becomes this agent's champion only through the gates
            agent["retrain"] = {"challenger": e.version, "backtest": e.backtest}
        out[m.agent_id] = agent
    ctx.population.save(ctx.state_dir / "agents.json")
    return out


# ---------------------------------------------------------------------------------------------- model watch
def model_watch(ctx: JobContext, slot: pd.Timestamp) -> dict[str, Any]:
    book = ShadowBook(ctx.state_dir)   # read-only view of the engine's shadow book
    out: dict[str, Any] = {}
    for agent_id in ctx.models.agent_ids():
        ch = ctx.models.champion(agent_id)
        if ch is None or ch.promoted_utc is None or slot - ch.promoted_utc > pd.Timedelta(days=CUSUM_WINDOW_DAYS):
            continue
        if not ctx.models.by_agent(agent_id, "previous"):
            continue                    # a first model has nothing to fall back to
        bt = PerfStats.model_validate(ch.backtest) if ch.backtest else None
        rets = book.returns_since(ch.version, ch.promoted_utc)
        if bt is None or bt.mean_ret is None or bt.std_ret is None:
            out[agent_id] = {"version": ch.version, "watch": "no backtest moments; cannot run CUSUM"}
            continue
        if cusum_alarm(rets, bt.mean_ret, bt.std_ret):
            restored = ctx.models.restore_previous(agent_id, f"CUSUM alarm on {len(rets)} shadow trades within {CUSUM_WINDOW_DAYS} days of promotion")
            out[agent_id] = {"version": ch.version, "action": "restored_previous", "restored": restored.version, "trades": len(rets)}
        else:
            out[agent_id] = {"version": ch.version, "action": "ok", "trades": len(rets)}
    return out


# ---------------------------------------------------------------------------------------------- tournament
def tournament(ctx: JobContext, slot: pd.Timestamp) -> dict[str, Any]:
    """Weekly population round (research/population.py): fitness from shadow trades, retirement, shadow -> live
    promotion through the DSR gate, cloning of winners, capital shares; writes state/agents.json for the dashboard.
    Agents whose six months of retired shadow trading are over leave the model registry's book."""
    summary = ctx.population.tournament(ShadowBook(ctx.state_dir), slot)
    for aid in summary["expired"]:
        ctx.models.retire_agent(aid, "retired agent's six months of shadow trading are over")
    ctx.population.save(ctx.state_dir / "agents.json")
    return summary


# ---------------------------------------------------------------------------------------------- research director
def research_budget(ctx: JobContext) -> tuple[int, int]:
    """(most trials the plan may hand out, per-family cap). The plan never exceeds the quarter's remaining budget
    (build_plan); on top of that it is bounded by the flat monthly loop (budget x families) when the grid runs, and
    by the quarter budget alone while the grid is paused; a family is capped at the label grid's size or the quarter
    budget, whichever is larger (pre-registered trials are not limited to grid variants)."""
    r = ctx.settings.research
    q = quarter_budget(r)
    grid = max(len(label_grid(cls.default_config, r.label_grid_step)) for cls in SPECIALISTS.values())
    total = min(q, r.trial_budget_per_month * len(SPECIALISTS)) if r.trial_budget_per_month > 0 else q
    return total, max(grid, q)


def director_evidence(ctx: JobContext) -> tuple[list[ShadowEvidence], list[AgentEvidence]]:
    shadow = []
    for e in ctx.models.entries:
        st = shadow_stats(ctx, e)
        if st is not None:
            shadow.append(ShadowEvidence(family=e.family, version=e.version, stats=st))
    agents = [AgentEvidence(family=m.family, agent_id=m.agent_id, status=m.status, n=int(m.stats.get("n", 0)),
                            sharpe_per_trade=float(m.stats.get("sharpe_per_trade", 0.0)), fitness=m.fitness,
                            ece=float(m.stats.get("ece", 1.0)))
              for m in ctx.population.members.values() if m.in_shadow_book]
    return shadow, agents


def research_director(ctx: JobContext, slot: pd.Timestamp) -> dict[str, Any]:
    """Weekly research plan (research/director.py): evidence per family -> trial budget per family and a ranked focus
    list, saved to state/research_plan.json for monthly_research and the research director agent. Decides what to
    research; promotes nothing."""
    synced = _sync_trials(ctx)            # count the research workflow's trials (and their lookahead checks) too
    budget, cap = research_budget(ctx)
    shadow, agents = director_evidence(ctx)
    r = ctx.settings.research
    plan = build_plan(slot, sorted(SPECIALISTS), read_rows(ctx.trials.path), shadow, agents,
                      quarter_budget=quarter_budget(r), monthly_total=budget, trial_budget_per_month=r.trial_budget_per_month,
                      floor=r.director_floor, cap=cap, holdout=holdout_window(r))
    plan.save(ctx.state_dir / PLAN_FILE)
    return {"quarter": plan.quarter, "quarter_used": plan.quarter_used, "budget": plan.budget,
            "grid_budget": plan.grid_budget, "unallocated": plan.unallocated, "focus": [f.family for f in plan.focus],
            "blocked": [s.family for s in plan.evidence if s.blocked], "registry_sync": synced}


def current_plan(ctx: JobContext, slot: pd.Timestamp) -> ResearchPlan | None:
    """The director's plan if one exists and is at most PLAN_MAX_AGE_DAYS old at `slot`."""
    try:
        plan = ResearchPlan.load(ctx.state_dir / PLAN_FILE)
    except ValueError as exc:               # a corrupt plan is no plan: the flat budget applies
        log.warning("research plan unreadable, using the flat budget: %s", exc)
        return None
    if plan is None or slot - plan.created_utc > pd.Timedelta(days=PLAN_MAX_AGE_DAYS):
        return None
    return plan


# ---------------------------------------------------------------------------------------------- staff agents
def _run_agents(ctx: JobContext, slot: pd.Timestamp, cadence: str) -> dict[str, Any]:
    if ctx.agent_runner is None:
        return {"skipped": "no anthropic-api-key in the keyring (python -m goldbot.ops.accounts set anthropic-api-key)"}
    out: dict[str, Any] = {}
    for role in ROLES.values():
        if role.cadence == cadence:
            run = ctx.agent_runner.run(role, slot)
            out[role.name] = {"status": run.status, "cost_usd": round(run.cost_usd, 4), "turns": run.turns,
                              "report": run.report_path, "detail": run.detail}
    return out


def agents_daily(ctx: JobContext, slot: pd.Timestamp) -> dict[str, Any]:
    return _run_agents(ctx, slot, "daily")


def agents_weekly(ctx: JobContext, slot: pd.Timestamp) -> dict[str, Any]:
    return _run_agents(ctx, slot, "weekly")


def agents_presession(ctx: JobContext, slot: pd.Timestamp) -> dict[str, Any]:
    return _run_agents(ctx, slot, "presession")


# ---------------------------------------------------------------------------------------------- calendar
def calendar_archive(ctx: JobContext, slot: pd.Timestamp) -> dict[str, Any]:
    """Archive this week's Forex Factory calendar; engines read tier-1 events from it for the news blackout."""
    from goldbot.data.econ_calendar import parse_ff_week
    if ctx.fetch_calendar is None:
        return {"skipped": "no calendar fetcher configured"}
    df = parse_ff_week(ctx.fetch_calendar(), slot, ctx.settings.risk.blackout.events)
    if df.empty:
        raise RuntimeError("calendar feed returned no events: the feed format may have changed")
    n = ctx.store.append("calendar_events", df, source="forexfactory")
    tier1 = df[(df["tier"] == 1) & (df["ts_utc"] >= slot)].sort_values("ts_utc")
    return {"events": n, "by_tier": {str(k): int(v) for k, v in df["tier"].value_counts().items()},
            "next_tier1": [f"{t:%a %H:%M} {x}" for t, x in zip(tier1["ts_utc"], tier1["title"])][:8]}


# ---------------------------------------------------------------------------------------------- research loop
def label_grid(base: dict[str, Any], step: float) -> list[dict[str, Any]]:
    """+-step around target_atr, stop_atr and max_bars (the base itself excluded: it is the champion's config)."""
    f = (1 - step, 1.0, 1 + step)
    out = []
    for a, b, c in itertools.product(f, f, f):
        if (a, b, c) == (1.0, 1.0, 1.0):
            continue
        out.append({"target_atr": round(base["target_atr"] * a, 4), "stop_atr": round(base["stop_atr"] * b, 4),
                    "max_bars": max(1, int(round(base["max_bars"] * c)))})
    return out


def make_trial_runner(ctx: JobContext, now: Callable[[], pd.Timestamp] | None = None
                      ) -> Callable[[str, dict[str, Any], str], dict[str, Any]]:
    """The research analyst's trial: the family's default config with overrides, walk-forward over the whole store,
    recorded in the trial registry (so it counts toward the deflated Sharpe like every monthly-loop trial)."""
    def run(family: str, overrides: dict[str, Any], rationale: str) -> dict[str, Any]:
        end = now() if now is not None else pd.Timestamp.now("UTC")
        res = _walk_forward(ctx, SPECIALISTS[family](**overrides), end, 12 * 30, n_trials=ctx.trials.n_trials + 1)
        if res is None:
            return {"error": "no bars in the store for this family's timeframe"}
        row = ctx.trials.record(agent_id=res.agent_id, family=family, config={**SPECIALISTS[family].default_config, **overrides},
                                feature_version=res.feature_version, results=res.metrics, status="evaluated", rationale=rationale)
        keep = ("n_candidates", "n_folds", "threshold", "all_candidates", "model_filtered", "trades_per_year")
        return {"trial": row["trial"], "registry_total": ctx.trials.n_trials, **{k: res.metrics[k] for k in keep if k in res.metrics}}
    return run


def _num(v: float | None, fmt: str) -> str:
    return "" if v is None else format(v, fmt)


def _sync_trials(ctx: JobContext) -> int | str | None:
    if ctx.sync_trials is None:
        return None
    try:
        return ctx.sync_trials(ctx.trials.path)
    except Exception as exc:   # the loop still runs; the next sync unions whatever was written meanwhile
        log.warning("trial registry sync failed: %s", exc)
        return f"failed: {exc}"


def monthly_research(ctx: JobContext, slot: pd.Timestamp) -> dict[str, Any]:
    r = ctx.settings.research
    synced_before = _sync_trials(ctx)     # count trials run elsewhere (the research workflow) before deflating
    rng = random.Random(f"{slot:%Y-%m}")        # reproducible choice of variants for the month
    plan = current_plan(ctx, slot)
    source = f"research plan of {plan.created_utc:%Y-%m-%d}" if plan else f"flat budget {r.trial_budget_per_month} per family (no fresh research plan)"
    lines = [f"# Research loop {slot:%Y-%m}", "", f"Trial budget: {source}.", ""]
    out: dict[str, Any] = {"plan": plan.created_utc.isoformat() if plan else None}
    q_budget = quarter_budget(r)
    h_start, _ = holdout_window(r)
    end = min(slot, h_start)              # the held-out year is never searched
    for family in sorted(SPECIALISTS):
        grid = label_grid(SPECIALISTS[family].default_config, r.label_grid_step)
        rng.shuffle(grid)
        tf = SPECIALISTS[family].timeframe
        history_months = 12 * 30          # everything the store has
        budget = min(plan.grid_budget.get(family, r.trial_budget_per_month), r.trial_budget_per_month) if plan \
            else r.trial_budget_per_month
        rows = []
        stopped = False
        for overrides in grid[:budget]:
            if quarter_usage(read_rows(ctx.trials.path), slot, q_budget)[2] <= 0:
                stopped = True
                break
            res = _walk_forward(ctx, SPECIALISTS[family](**overrides), end, history_months, n_trials=ctx.trials.n_trials + 1)
            if res is None:
                break
            row = ctx.trials.record(agent_id=res.agent_id, family=family, config={**SPECIALISTS[family].default_config, **overrides},
                                    feature_version=res.feature_version, results=res.metrics, status="evaluated",
                                    rationale=f"monthly bounded label-grid search {slot:%Y-%m} (+-{r.label_grid_step:.0%})")
            mf = res.metrics.get("model_filtered") or {}
            rows.append({"trial": row["trial"], **overrides, "n": mf.get("n", 0), "sharpe": mf.get("sharpe_ann"), "dsr": mf.get("dsr")})
        out[family] = {"trials": len(rows), "budget": budget, "timeframe": tf, "registry_total": ctx.trials.n_trials,
                       "quarter_budget_spent": stopped}
        lines += [f"## {family} ({len(rows)} of {budget} budgeted trials, registry total {ctx.trials.n_trials})", ""]
        if stopped:
            lines += [f"Stopped: the quarter's trial budget ({q_budget}) is spent.", ""]
        reasons = next((f.reasons for f in plan.focus if f.family == family), []) if plan else []
        lines += [f"- director: {x}" for x in reasons] + ([""] if reasons else [])
        lines += ["| trial | target | stop | max bars | n | Sharpe | DSR |", "|---:|---:|---:|---:|---:|---:|---:|"]
        for x in sorted(rows, key=lambda x: -(x["dsr"] or 0)):
            lines.append(f"| {x['trial']} | {x['target_atr']} | {x['stop_atr']} | {x['max_bars']} | {x['n']} | "
                         f"{_num(x['sharpe'], '.2f')} | {_num(x['dsr'], '.3f')} |")
        lines += ["", "Variants are candidates for the research analyst; none is promoted by this job.", ""]
    report = ctx.state_dir / f"research_{slot:%Y-%m}.md"
    report.write_text("\n".join(lines))
    out["report"] = str(report)
    out["registry_sync"] = {"before": synced_before, "after": _sync_trials(ctx)}
    return out


# ---------------------------------------------------------------------------------------------- wiring
JOBS: dict[str, Callable[[JobContext, pd.Timestamp], dict[str, Any]]] = {
    "nightly_costs": nightly_costs,
    "saturday_retrain": saturday_retrain,
    "model_watch": model_watch,
    "tournament": tournament,
    "research_director": research_director,
    "agents_daily": agents_daily,
    "agents_weekly": agents_weekly,
    "monthly_research": monthly_research,
    "calendar_archive": calendar_archive,
    "agents_presession": agents_presession,
}


def build_scheduler(ctx: JobContext, clock: Callable[[], pd.Timestamp] | None = None) -> Scheduler:
    sch = Scheduler(ctx.state_dir / "scheduler.json", clock=clock)
    cfg = ctx.settings.scheduler
    for name, fn in JOBS.items():
        s = getattr(cfg, name)
        sch.add(name, Schedule(kind=s.kind, at=s.at, weekdays=tuple(s.weekdays), weekday=s.weekday, day=s.day,
                               max_late_hours=s.max_late_hours), functools.partial(fn, ctx))
    return sch
