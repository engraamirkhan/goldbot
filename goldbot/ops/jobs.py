"""The scheduler's jobs (design: Retraining and promotion, Bounded research loop, Execution costs).

* nightly_costs     per enabled account: spread table from its own ticks, slippage table from its own fills,
                    swap and commission as the terminal reports them (state/broker_terms_<account>.json, written by
                    the engine; settings commission and the swap prior while missing or older than
                    BROKER_TERMS_MAX_AGE_DAYS) -> state/costs_<account>.json (read by the engine every bar).
                    On Fridays it also re-runs the account classifier (two consecutive disagreements to change).
                    With `costs.publish_release` and a github-token it then publishes the canonical broker's table
                    (costs only) to release costs-v1 for research.yml (`publish_costs`).
* saturday_retrain  refresh bars from the data release (best effort), then per specialist: evaluate any challenger
                    that has a shadow record against the promotion gates (promote automatically when all pass),
                    and retrain on the rolling window. A family keeps at most one challenger in shadow; a new one
                    replaces it only after it is decided or has been in shadow for CHALLENGER_MAX_WEEKS.
* tournament        weekly after the retrain: the population round (fitness, retirement, promotion to live,
                    cloning, capital shares) -> state/agents.json for the dashboard's league table.
* research_director weekly before the staff agents: scores every family's evidence (trial registry, shadow book,
                    population) and splits what is left of the quarter's trial budget after the pre-registered
                    queue's reservation (research.reserved_trials_quarter) -> state/research_plan.json
                    (research/director.py). It decides what to research, never what goes live: promotion stays with
                    the gates. Trials scored on the held-out year are never used as evidence.
* model_watch       daily: a champion promoted in the last CUSUM_WINDOW_DAYS whose shadow returns trip the CUSUM
                    alarm against its backtest is replaced by the previous champion (design: Retraining and promotion).
* recalibrate       weekly after the retrain (proposal P9): refits only the probability map of every champion and
                    challenger on its recent counterfactual shadow outcomes (every candidate, taken or not), shrunk
                    toward the current calibration and capped per run; a minor version in the model registry with
                    before/after ECE (also state/recalibration.jsonl). Promotes and retires nothing.
* drift_watch       daily (design: Drift and health): per champion, PSI of recent candidates' inputs against the
                    training distribution, ECE/Brier on the trailing taken shadow trades, a residual CUSUM, and the
                    30-day drawdown against backtest -> state/drift.json (size factors, halted agents, system halt).
                    Halts are sticky until the owner clears them (`run.py drift-review --clear`) or a new champion
                    version replaces the halted one. Entries only: exits are never affected.
* gap_watch         daily after drift_watch (TRADER_LIFECYCLE section 3): deterministic gap detectors -> zero-capital
                    shadow founders (capped a month, reserved slots), on-demand runs of existing read-only staff roles
                    (capped a week, inside the monthly agent budget), hypotheses, BACKLOG suggestions ->
                    state/gaps.json (ops/gap_watch.py). Creates no live agent and no new family or role.
* monthly_research  bounded search: label-grid variants (+-step on target, stop and time limit) per specialist, as
                    many as the research plan's grid share gives the family (`trial_budget_per_month` each without a
                    fresh plan), only while research.label_grid_paused is false, never past the quarter's trial
                    budget less the pre-registered reservation and never into the held-out year, each a
                    walk-forward recorded in the trial registry whose count feeds the deflated Sharpe; a markdown summary is written to state/research_<YYYY-MM>.md.
* backup            daily: encrypted restic snapshot of the SQLite files (online backup API), state JSON, trial
                    registry, models and the brain's own Parquet to Oracle Object Storage, append only: retention
                    runs from the owner's Mac (scripts/backup_retention.sh) -> state/backup_last.json (health checks
                    backup_age, backup_prune).
* restore_drill     weekly: restores the latest snapshot to a temp dir and verifies it -> state/restore_drill_last.json.
* attribution       daily (BACKLOG 12, G12): deterministic attribution of the shadow book, engine fills and orders
                    against the canonical cost table -> state/attribution.json + attribution.md, which the improvement
                    agent and research analyst read (`read_attribution`). Reporting only: changes nothing.
* feed_reconcile    daily (D12): per account, the engine's tick-built 1m bars against the broker's own M1 for the last
                    day; divergence and unconfirmed spikes -> dq_events, broker M1 -> bars_1m_broker, report ->
                    state/reconcile_<account>.json (health check reconcile:<account>). Skipped without a broker.
* cpcv_quarterly    first Sunday of each quarter (M16): combinatorial purged CV (6 groups, 2 test: 15 splits, 5 paths)
                    of every trial that passed the gates, PBO against its family's other trials, attached to the
                    trial as evidence (not a trial) -> state/cpcv_<quarter>.md (research/cpcv.py).
"""
from __future__ import annotations

import functools
import itertools
import json
import logging
import random
from pathlib import Path
from typing import Any, Callable, cast

import numpy as np
import pandas as pd

from goldbot.agents.roles import ROLES
from goldbot.agents.runner import AgentRunner
from goldbot.base import Record, write_atomic
from goldbot.config import DecisionTimeframe, Settings
from goldbot.data.store import Store
from goldbot.engine.shadow import ShadowBook
from goldbot.execution.classifier import PersistentClassifier, classify
from goldbot.execution.costs import BrokerTerms, CostTable, build_cost_table, publishable
from goldbot.features.mtf import TF_LABEL, context_tfs
from goldbot.labels.triple_barrier import SwapSpec
from goldbot.ops import gap_watch as gap_watch_mod
from goldbot.ops.accounts import Account
from goldbot.ops.scheduler import Schedule, Scheduler
from goldbot.research.director import (
    GRID_RATIONALE,
    PLAN_FILE,
    AgentEvidence,
    ResearchPlan,
    ShadowEvidence,
    build_plan,
    holdout_window,
    load_attribution,
    load_hypotheses,
    quarter_budget,
    reserved_trials,
)
from goldbot.research.drift import AgentHealth, assess, system_halt_reasons
from goldbot.research.model import RecalibratedCalibrator, fit_recalibration
from goldbot.research.model_registry import ModelEntry, ModelRegistry
from goldbot.research.pipeline import ResearchResult, build_decision_frame, run_specialist
from goldbot.research.population import Population
from goldbot.research.promotion import PerfStats, cusum_alarm, evaluate_promotion
from goldbot.research.registry import TrialBudgetExceeded, TrialRegistry, quarter_of
from goldbot.research.registry_sync import read_rows
from goldbot.specialists import SPECIALISTS
from goldbot.specialists.base import Specialist

log = logging.getLogger("goldbot.jobs")

CHALLENGER_MAX_WEEKS = 8
CLASSIFIER_WEEKDAY = 4          # Friday's nightly run re-classifies (design: weekly, from the nightly cost job)
CONTEXT_EXTRA_MONTHS = 2        # daily/4h/1h context needs history before the decision window starts
CUSUM_WINDOW_DAYS = 14          # a new champion is watched for its first two weeks
BROKER_TERMS_MAX_AGE_DAYS = 7     # older terminal readings are not trusted (the engine refreshes them every few hours)
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
    upload_costs: Callable[[bytes], str] | None = None           # published cost table -> release costs-v1 (VPS only)
    broker_for: Callable[[Account], Any] | None = None           # read-only Broker per account (feed_reconcile), or None
    get_secret: Callable[[str], str | None] | None = None        # backup credentials (default: goldbot.ops.accounts)


# ---------------------------------------------------------------------------------------------- nightly costs
def nightly_costs(ctx: JobContext, slot: pd.Timestamp) -> dict[str, Any]:
    r = ctx.settings.research
    out: dict[str, Any] = {}
    for acc in ctx.accounts:
        ticks = ctx.store.read("ticks", source=acc.account_id, symbol=acc.symbol, start=slot - pd.Timedelta(days=r.cost_window_days))
        fills = ctx.store.read("fills", source=acc.account_id, symbol=acc.symbol, start=slot - pd.Timedelta(days=r.fills_window_days))
        terms, notes = _broker_terms(ctx, acc.account_id, slot)
        commission = ctx.settings.costs.commission_per_lot_side_usd.get(acc.broker, 0.0)
        measured = terms is not None and terms.commission_per_lot_round_trip_usd is not None
        if terms is not None and terms.commission_per_lot_round_trip_usd is not None:
            commission = terms.commission_per_lot_round_trip_usd / 2
        swap = terms.swap_spec(acc.server_tz, ctx.settings.costs.swap_triple_weekday) if terms is not None else None
        table = build_cost_table(acc.account_id, ticks, fills, commission_per_lot_side_usd=commission,
                                 slippage_prior_usd=ctx.settings.costs.slippage_prior_usd,
                                 min_fills=r.min_fills_for_slippage, now=slot, swap=swap, commission_measured=measured,
                                 notes=notes)
        if measured and terms is not None:
            table = table.model_copy(update={"commission_lots": terms.commission_lots})
        table.save(ctx.state_dir / f"costs_{acc.account_id}.json")
        row: dict[str, Any] = {"ticks": len(ticks), "fills": len(fills),
                               "round_trip_usd": {s: table.round_trip_usd_per_oz(s) for s in ("asia", "london", "newyork")},
                               "swap_measured": swap is not None, "commission_measured": measured}
        if slot.dayofweek == CLASSIFIER_WEEKDAY:
            c = classify(ticks, fills)
            row["account_class"] = PersistentClassifier(ctx.state_dir / f"classifier_{acc.account_id}.json").update(c)
            row["classifier_reason"] = c.reason
        out[acc.account_id] = row
    if ctx.upload_costs is not None:            # costs.publish_release and a github-token: research reads it
        out["publish"] = publish_costs(ctx.settings, ctx.accounts, ctx.state_dir, ctx.upload_costs, slot)
    return out


COSTS_PUBLISHED_FILE = "costs_published.json"    # state: when the table last reached release costs-v1 (health reads it)


def publish_costs(settings: Settings, accounts: list[Account], state_dir: Path, upload: Callable[[bytes], str],
                  now: pd.Timestamp) -> dict[str, Any]:
    """Upload the canonical broker's cost table to release costs-v1 (asset costs_measured.json) for research.yml.
    Costs only (`PublishedCostTable`: no account id, login, balance or equity). Refused while swap is unmeasured or
    there are fewer than `research.min_fills_for_slippage` fills, so a prior is never published as a measurement; an
    upload failure is recorded, never raised (the cost table itself is already saved). state/costs_published.json
    keeps the last success and the last attempt for the health check."""
    path = state_dir / COSTS_PUBLISHED_FILE
    try:
        rec: dict[str, Any] = json.loads(path.read_text()) if path.exists() else {}
    except (ValueError, OSError):
        rec = {}
    found = canonical_cost_table(settings, accounts, state_dir)
    if found is None:
        pub, reason = None, "no cost table on the canonical-cost broker yet"
    else:
        pub, reason = publishable(found[0], found[1].broker, min_fills=settings.research.min_fills_for_slippage)
    result: dict[str, Any] = {"published": False, "reason": reason}
    if pub is not None:
        try:
            url = upload(pub.model_dump_json(indent=1).encode())
            result = {"published": True, "reason": "ok", "measured_at": pub.measured_at.isoformat(), "url": url}
            rec.update(published_utc=now.isoformat(), measured_at=pub.measured_at.isoformat())
        except Exception as exc:                # network or GitHub error: keep the cost job green, report it
            log.warning("cost table upload failed: %s", exc)
            result = {"published": False, "reason": f"upload failed: {type(exc).__name__}: {exc}"[:300]}
    rec.update(last_attempt_utc=now.isoformat(), last_result=result["reason"])
    write_atomic(path, json.dumps(rec), durable=False)
    return result


def _broker_terms(ctx: JobContext, account_id: str, slot: pd.Timestamp) -> tuple[BrokerTerms | None, list[str]]:
    """The engine's latest terminal reading of swap and commission, if fresh; else None and the reason."""
    path = ctx.state_dir / f"broker_terms_{account_id}.json"
    try:
        terms = BrokerTerms.load(path)
    except ValueError as exc:
        log.warning("%s unreadable, using the settings costs: %s", path.name, exc)
        return None, [f"broker terms unreadable ({exc}): settings commission and swap prior"]
    if terms is None:
        return None, ["no broker terms from the terminal yet: settings commission and swap prior"]
    age = slot - terms.measured_utc
    if age > pd.Timedelta(days=BROKER_TERMS_MAX_AGE_DAYS):
        log.warning("%s is %s old, using the settings costs", path.name, age)
        return None, [f"broker terms stale (measured {terms.measured_utc:%Y-%m-%d %H:%M}): settings commission and swap prior"]
    return terms, [f"broker terms measured {terms.measured_utc:%Y-%m-%d %H:%M}: " + "; ".join(terms.notes)]


# ---------------------------------------------------------------------------------------------- retrain
def _bars(ctx: JobContext, tf: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    return ctx.store.read(f"bars_{tf}", start=start, end=end).drop(columns=["source", "symbol", "year", "month"], errors="ignore")


def live_extra_cost_usd(ctx: JobContext) -> float:
    """Round-trip cost per oz the canonical-cost broker charges beyond the quoted spread, from its nightly cost table:
    entry and exit slippage (the measured mean, or the conservative prior until enough fills exist) plus commission
    both sides. Before the first table exists: the configured slippage prior plus commission (never 0, so research and
    retraining are always charged more than the spread)."""
    from goldbot.execution.costs import settings_extra_cost_usd
    found = canonical_cost_table(ctx.settings, ctx.accounts, ctx.state_dir)
    return found[0].extra_cost_usd() if found is not None else settings_extra_cost_usd(ctx.settings)


def canonical_cost_table(settings: Settings, accounts: list[Account], state_dir: Path) -> tuple[CostTable, Account] | None:
    """The first readable nightly cost table of an account on the canonical-cost broker (design: IC Markets' tables
    are the canonical ones for backtests), with its account; None before one exists."""
    canonical = {b for b, cfg in settings.brokers.items() if cfg.canonical_costs}
    for acc in accounts:
        if acc.broker not in canonical:
            continue
        try:
            table = CostTable.load(state_dir / f"costs_{acc.account_id}.json")
        except ValueError:
            table = None
        if table is not None:
            return table, acc
    return None


def live_swap(ctx: JobContext) -> SwapSpec:
    """Swap the canonical-cost broker charges, from its nightly cost table when the table carries the terminal's swap
    rates; otherwise the settings prior (`costs.swap_*`)."""
    from goldbot.execution.costs import settings_swap
    prior = settings_swap(ctx.settings)
    found = canonical_cost_table(ctx.settings, ctx.accounts, ctx.state_dir)
    spec = found[0].swap_spec(found[1].server_tz, prior.triple_weekday) if found is not None else None
    return spec if spec is not None else prior


def _walk_forward(ctx: JobContext, spec: Specialist, end: pd.Timestamp, months: int, n_trials: int = 1,
                  holdout: tuple[pd.Timestamp, pd.Timestamp] | None = None) -> ResearchResult | None:
    """Walk-forward on the store. Research trials pass the settings' holdout window (never seen); the Saturday retrain
    passes none, because a model that will trade must learn from the latest data."""
    start = end - pd.DateOffset(months=months)
    dec = _bars(ctx, spec.timeframe, start, end)
    if dec.empty:
        return None
    ctx_start = start - pd.DateOffset(months=CONTEXT_EXTRA_MONTHS)
    context = {TF_LABEL[tf]: _bars(ctx, tf, ctx_start, end) for tf in context_tfs(spec.timeframe)}
    years = max((pd.to_datetime(dec["ts_utc"].iloc[-1]) - pd.to_datetime(dec["ts_utc"].iloc[0])).days / 365.25, 1e-9)
    # learn against what the broker actually charges: the live cost table's slippage and commission
    res = run_specialist(spec, dec, context=context, n_trials=n_trials, extra_cost_usd=live_extra_cost_usd(ctx),
                         holdout=holdout, swap=live_swap(ctx))
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
        if spec.timeframe not in ctx.settings.walkforward:   # settings.walkforward: 15m, 1h, 4h (1d research-only)
            agent["retrain"] = f"skipped: no walk-forward window configured for {spec.timeframe}"
            out[m.agent_id] = agent
            continue
        wf = ctx.settings.walkforward[spec.timeframe]
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
        d = ctx.settings.drift
        # M25: h for a 5% false-alarm rate over the watch's own window (two weeks of the backtest's trade rate), on
        # two-point returns that win with the backtest's hit rate
        if cusum_alarm(rets, bt.mean_ret, bt.std_ret, k=d.cusum_k, trades_per_week=bt.trades_per_week,
                       false_alarm=d.cusum_false_alarm, p=bt.hit_rate if 0 < bt.hit_rate < 1 else None,
                       weeks=CUSUM_WINDOW_DAYS / 7.0):
            restored = ctx.models.restore_previous(agent_id, f"CUSUM alarm on {len(rets)} shadow trades within {CUSUM_WINDOW_DAYS} days of promotion")
            out[agent_id] = {"version": ch.version, "action": "restored_previous", "restored": restored.version, "trades": len(rets)}
        else:
            out[agent_id] = {"version": ch.version, "action": "ok", "trades": len(rets)}
    return out


# ---------------------------------------------------------------------------------------------- recalibration
def recalibrate(ctx: JobContext, slot: pd.Timestamp) -> dict[str, Any]:
    """Weekly bounded recalibration (proposal P9). For every champion and challenger: the closed shadow candidates of
    the last `recal_window_days` that were recorded with their decision and raw score (taken or not, so the sample is
    not selected by the model's own threshold), re-scored through the VALIDATED calibrator; with at least
    `recal_min_samples` of them a Platt layer shrunk toward the validated map (prior worth `recal_prior_trades`) and
    capped at +-`recal_max_shift` replaces any earlier layer and is stored as a minor version. Refitting from the
    validated map each week keeps overlapping windows from compounding. The trees, features, status and shadow record
    are untouched; promotion stays with the gates. A model that cannot be loaded is reported and skipped."""
    r = ctx.settings.research
    book = ShadowBook(ctx.state_dir)            # read-only view of the engine's shadow book
    since = slot - pd.Timedelta(days=r.recal_window_days)
    out: dict[str, Any] = {}
    for e in [x for x in ctx.models.entries if x.status in ("champion", "challenger")]:
        sample = [t for t in book.outcomes(e.version, since) if t.p_raw is not None]
        if len(sample) < r.recal_min_samples:
            out[e.version] = {"action": "skipped", "n": len(sample),
                              "reason": f"fewer than {r.recal_min_samples} recorded outcomes in {r.recal_window_days} days"}
            continue
        try:
            model = ctx.models.load(e)
        except (ValueError, TypeError, OSError) as exc:
            log.error("recalibrate: %s not loaded: %s", e.version, exc)
            out[e.version] = {"action": "error", "reason": str(exc)}
            continue
        validated = RecalibratedCalibrator.validated(model.calibrator)
        raw = np.array([t.p_raw for t in sample], dtype=float)
        p_val = np.asarray(validated.predict(raw) if validated is not None else raw, dtype=float)
        y = np.array([t.barrier == "target" for t in sample], dtype=float)
        fit = fit_recalibration(p_val, y, prior_weight=r.recal_prior_trades, max_shift=r.recal_max_shift,
                                min_samples=r.recal_min_samples)
        if fit is None:
            out[e.version] = {"action": "skipped", "n": len(sample), "reason": "no fit"}
            continue
        model.calibrator = RecalibratedCalibrator.replacing(model.calibrator, fit.layer())
        n_taken = int(sum(t.taken for t in sample))
        record = {"ts": slot.isoformat(), **fit.model_dump(), "n_taken": n_taken, "n_not_taken": len(sample) - n_taken,
                  "window_from": since.isoformat()}
        entry = ctx.models.recalibrate(e.version, model, record)
        with open(ctx.state_dir / "recalibration.jsonl", "a") as f:
            f.write(json.dumps({"version": e.version, "agent_id": e.agent_id, "status": entry.status, **record}) + "\n")
        out[e.version] = {"action": "recalibrated", "minor": len(entry.recalibrations), "n": fit.n, "n_taken": n_taken,
                          "n_not_taken": len(sample) - n_taken, "ece_before": fit.ece_before,
                          "ece_after": fit.ece_after, "max_abs_shift": fit.max_abs_shift}
    return out


# ---------------------------------------------------------------------------------------------- drift
DRIFT_WARMUP_DAYS = 90          # bars before the PSI window so every feature's lookback is filled


def _recent_candidates(ctx: JobContext, spec: Any, slot: pd.Timestamp, days: int) -> pd.DataFrame:
    """Decision-frame features (plus `side`) of the specialist's candidates in the last `days`, built exactly as for
    training (same context timeframes and feature registry)."""
    start = slot - pd.Timedelta(days=days + DRIFT_WARMUP_DAYS)
    dec = _bars(ctx, spec.timeframe, start, slot)
    if dec.empty:
        return pd.DataFrame()
    ctx_start = start - pd.DateOffset(months=CONTEXT_EXTRA_MONTHS)
    context = {TF_LABEL[tf]: _bars(ctx, tf, ctx_start, slot) for tf in context_tfs(spec.timeframe)}
    m, X = build_decision_frame(dec.reset_index(drop=True), context)
    cands = spec.candidates(m, X)
    if cands.empty:
        return pd.DataFrame()
    idx = cands["idx"].to_numpy()
    feats = X.iloc[idx].reset_index(drop=True)
    feats["side"] = cands["side"].to_numpy()
    recent = pd.to_datetime(feats["ts_utc"], utc=True) >= slot - pd.Timedelta(days=days)
    return feats[recent.to_numpy()].drop(columns=["ts_utc"]).replace([np.inf, -np.inf], np.nan).reset_index(drop=True)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return cast(dict[str, Any], json.loads(path.read_text())) if path.exists() else {}
    except (ValueError, OSError):
        return {}


def drift_watch(ctx: JobContext, slot: pd.Timestamp) -> dict[str, Any]:
    """Daily drift and health check of every champion (design: Drift and health) -> state/drift.json."""
    d = ctx.settings.drift
    prev = _read_json(ctx.state_dir / "drift.json")
    review = _read_json(ctx.state_dir / "drift_review.json")
    cleared = pd.Timestamp(review["cleared_utc"]) if review.get("cleared_utc") else None
    book = ShadowBook(ctx.state_dir)
    health: list[AgentHealth] = []
    errors: dict[str, str] = {}
    for e in [x for x in ctx.models.entries if x.status == "champion"]:
        member = ctx.population.members.get(e.agent_id)
        try:
            model = ctx.models.load(e)
            live = _recent_candidates(ctx, member.specialist(), slot, d.window_days) if member is not None else None
        except Exception as exc:                       # one broken model must not hide the others' health
            log.exception("drift_watch: %s", e.version)
            errors[e.agent_id] = f"{type(exc).__name__}: {exc}"
            continue
        vb = book.books.get(e.version)
        exited = [(pd.Timestamp(t.exit_ts), t) for t in (vb.closed if vb else []) if t.taken and t.exit_ts is not None]
        exited.sort(key=lambda x: x[0])
        closed = [t for _, t in exited]
        recent = [t for ts, t in exited if ts >= slot - pd.Timedelta(days=d.dd_window_days)]
        bt_dd = (e.backtest or {}).get("max_dd")
        tpw = (e.backtest or {}).get("trades_per_week")      # sets the CUSUM's h (M25: 5% quarterly false alarms)
        health.append(assess(e.agent_id, e.version, model=model, live=live, closed_taken=closed, recent_taken=recent,
                             backtest_dd=float(bt_dd) if bt_dd is not None else None, s=d,
                             trades_per_week=float(tpw) if tpw else None))
    # sticky: an agent stays halted while the same version is champion, until the owner clears it
    prev_halts = prev.get("halted") or {}
    halted: dict[str, Any] = {}
    for h in health:
        old = prev_halts.get(h.agent_id)
        still = old is not None and old.get("version") == h.version and \
            (cleared is None or cleared < pd.Timestamp(old["since"]))
        if h.halted or still:
            halted[h.agent_id] = old if still else {"version": h.version, "since": slot.isoformat(), "reasons": h.notes}
            h.halted = True
    reasons = system_halt_reasons(health, d)
    old_sys = prev.get("system_halt") or None
    if old_sys and (cleared is None or cleared < pd.Timestamp(old_sys["since"])):
        system = old_sys                               # pending the owner's review
    elif reasons:
        system = {"since": slot.isoformat(), "reasons": reasons}
    else:
        system = None
    out = {"ts": slot.isoformat(), "agents": {h.agent_id: h.model_dump() for h in health}, "halted": halted,
           "size_factor": {h.agent_id: h.size_factor for h in health if h.size_factor < 1}, "system_halt": system,
           "errors": errors}
    write_atomic(ctx.state_dir / "drift.json", json.dumps(out, default=str))
    return {"agents": len(health), "halted": sorted(halted), "size_down": sorted(out["size_factor"]),
            "system_halt": bool(system), "errors": errors}


# ---------------------------------------------------------------------------------------------- tournament
def tournament(ctx: JobContext, slot: pd.Timestamp) -> dict[str, Any]:
    """Weekly population round (research/population.py): fitness from shadow trades, retirement, shadow -> live
    promotion through the DSR gate, cloning of winners, capital shares; writes state/agents.json for the dashboard.
    Agents whose six months of retired shadow trading are over leave the model registry's book.

    Shadow -> live also needs a research trial of the agent's exact configuration that passed the design's gates; a
    clone has its own configuration hash, so it needs its own passed trial (no inheritance from its parent: the design
    counts real trials). Agents held back for that are reported under `awaiting_research`."""
    synced = _sync_trials(ctx)            # the research workflow's trials live on the release copy until synced
    summary = ctx.population.tournament(
        ShadowBook(ctx.state_dir), slot,
        research_passed=lambda m: ctx.trials.passed_gates(m.family, {**SPECIALISTS[m.family].default_config, **m.config}))
    for aid in summary["expired"]:
        ctx.models.retire_agent(aid, "retired agent's six months of shadow trading are over")
    ctx.population.save(ctx.state_dir / "agents.json")
    summary["registry_sync"] = synced
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
                      floor=r.director_floor, cap=cap, holdout=holdout_window(r),
                      attribution=load_attribution(ctx.state_dir), hypotheses=load_hypotheses(),
                      retired=r.retired_families, promoted={e.version: e.promoted_utc for e in ctx.models.entries},
                      reserved_setting=r.reserved_trials_quarter, grid_paused=r.label_grid_paused)
    plan.save(ctx.state_dir / PLAN_FILE)
    return {"quarter": plan.quarter, "quarter_used": plan.quarter_used, "quarter_reserved": plan.quarter_reserved,
            "budget": plan.budget, "grid_budget": plan.grid_budget, "unallocated": plan.unallocated,
            "focus": [f.family for f in plan.focus], "blocked": [s.family for s in plan.evidence if s.blocked],
            "retired": [s.family for s in plan.evidence if s.retired], "hypotheses_drift": plan.hypotheses_drift,
            "registry_sync": synced}


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
    recorded in the trial registry (so it counts toward the deflated Sharpe like every monthly-loop trial). It is
    charged to the quarter's pre-registered trial budget and never sees the holdout window."""
    def run(family: str, overrides: dict[str, Any], rationale: str) -> dict[str, Any]:
        end = now() if now is not None else pd.Timestamp.now("UTC")
        r = ctx.settings.research
        _sync_trials(ctx)                 # count the research workflow's trials before charging the budget
        with ctx.trials.locked():
            try:
                quarter = ctx.trials.check_budget(1, quarter_budget(r))
            except TrialBudgetExceeded as exc:
                return {"error": str(exc)}
            res = _walk_forward(ctx, SPECIALISTS[family](**overrides), end, 12 * 30, n_trials=ctx.trials.n_trials + 1,
                                holdout=r.holdout_window())
            if res is None:
                return {"error": "no bars in the store for this family's timeframe"}
            row = ctx.trials.record(agent_id=res.agent_id, family=family, config={**SPECIALISTS[family].default_config, **overrides},
                                    feature_version=res.feature_version, results=res.metrics, status="evaluated",
                                    rationale=rationale, budget_quarter=quarter)
        keep = ("n_candidates", "n_folds", "threshold", "all_candidates", "model_filtered", "trades_per_year", "rule_only",
                "gates")
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
    """Bounded label-grid search. Paused by default (research.label_grid_paused): barrier perturbations of rules with
    no signal only raise the deflated-Sharpe bar for every later trial. When enabled, each trial is charged to the
    quarter's pre-registered budget and the loop stops when what the pre-registered queue leaves is spent
    (budget - used - research.reserved_trials_quarter's reservation, director.reserved_trials); the holdout window is
    never seen."""
    r = ctx.settings.research
    synced_before = _sync_trials(ctx)     # count trials run elsewhere (the research workflow) before deflating
    if r.label_grid_paused:
        return {"paused": "label-grid loop paused (research.label_grid_paused in config/settings.yaml)",
                "registry_sync": synced_before}
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
            with ctx.trials.locked():           # check, run and record as one step: two writers cannot both pass
                reserved = reserved_trials(read_rows(ctx.trials.path), quarter_of(), r.reserved_trials_quarter).reserved
                try:
                    quarter = ctx.trials.check_budget(1, max(q_budget - reserved, 0))
                except TrialBudgetExceeded as exc:
                    stopped, out["budget"] = True, f"{exc} ({reserved} of the quarter held for the pre-registered queue)"
                    break
                res = _walk_forward(ctx, SPECIALISTS[family](**overrides), end, history_months,
                                    n_trials=ctx.trials.n_trials + 1, holdout=r.holdout_window())
                if res is None:
                    break
                row = ctx.trials.record(agent_id=res.agent_id, family=family, config={**SPECIALISTS[family].default_config, **overrides},
                                        feature_version=res.feature_version, results=res.metrics, status="evaluated",
                                        rationale=f"{GRID_RATIONALE} {slot:%Y-%m} (+-{r.label_grid_step:.0%})",
                                        budget_quarter=quarter)
            mf = res.metrics.get("model_filtered") or {}
            rows.append({"trial": row["trial"], **overrides, "n": mf.get("n", 0), "sharpe": mf.get("sharpe_ann"), "dsr": mf.get("dsr")})
        out[family] = {"trials": len(rows), "budget": budget, "timeframe": tf, "registry_total": ctx.trials.n_trials,
                       "quarter_budget_spent": stopped}
        lines += [f"## {family} ({len(rows)} of {budget} budgeted trials, registry total {ctx.trials.n_trials})", ""]
        if stopped:
            lines += [f"Stopped: the quarter's trial budget ({q_budget}) is spent, counting the trials held for the "
                      f"pre-registered queue (research.reserved_trials_quarter).", ""]
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


# ---------------------------------------------------------------------------------------------- CPCV (M16)
CPCV_MAX_CONFIGS = 8            # PBO compares a passing trial with at most this many of its family's trials (itself in)


def cpcv_quarterly(ctx: JobContext, slot: pd.Timestamp) -> dict[str, Any]:
    """Quarterly (design: "Combinatorial purged CV (6 groups, 2 test, 15 paths) runs quarterly"; research/cpcv.py):
    for every walk-forward trial that passed the design's gates and has no CPCV evidence this quarter, CPCV on the
    store's bars up to the holdout (never into it), compared for PBO with its family's other walk-forward trials on
    the same timeframe (the configurations it was selected among, the most recent CPCV_MAX_CONFIGS). The result is
    attached to the passing trial as evidence: not a trial, no budget slot, no deflated-Sharpe count. It can only
    veto (ADR 0002: fragile when PBO > 0.5 or most paths negative), and its PBO is a lower bound (the selection set is
    every registered trial of the family on that timeframe, screened ones included). A trial whose recorded feature
    version differs from the one the store's bars build now is skipped, not re-run on different inputs. The evidence
    sidecar is local to the scheduler host (registry_sync does not carry it; docs/decisions/0002). Report ->
    state/cpcv_<quarter>.md. One trial's failure does not stop the others; the job fails at the end if any did."""
    from goldbot.research import cpcv
    from goldbot.research.registry import quarter_of
    synced = _sync_trials(ctx)
    r = ctx.settings.research
    holdout = r.holdout_window()
    q = quarter_of(slot.to_pydatetime())
    rows = read_rows(ctx.trials.path)
    done = {int(e["trial"]) for e in ctx.trials.evidence(kind=cpcv.EVIDENCE_KIND) if e.get("quarter") == q}

    def timeframe(row: dict[str, Any]) -> str | None:
        try:
            return cpcv.trial_timeframe(row) if cpcv.cpcv_eligible(row) is None else None
        except (KeyError, ValueError, TypeError):
            return None

    passing = [x for x in rows if timeframe(x) is not None
               and ((x.get("results") or {}).get("gates") or {}).get("passed") is True]
    end = min(slot, holdout[0]) if holdout is not None else slot
    start = end - pd.DateOffset(months=12 * 30)                 # everything the store has
    out: dict[str, Any] = {"quarter": q, "trials": {}, "errors": {}, "registry_sync": synced}
    lines = [f"# Combinatorial purged CV {q}", "",
             f"{len(passing)} trial(s) passed the gates. Evidence on those trials, not new trials.", ""]
    for row in passing:
        t = int(row["trial"])
        if t in done:
            out["trials"][t] = "already evaluated this quarter"
            continue
        try:
            tf = str(timeframe(row))
            peers = [x for x in rows if x.get("family") == row["family"] and int(x["trial"]) != t and timeframe(x) == tf]
            compare = [row, *peers[-(CPCV_MAX_CONFIGS - 1):]]
            dec = _bars(ctx, tf, start, end)
            if dec.empty:
                raise ValueError(f"no {tf} bars in the store before {end:%Y-%m-%d}")
            ctx_start = start - pd.DateOffset(months=CONTEXT_EXTRA_MONTHS)
            context = {TF_LABEL[x]: _bars(ctx, x, ctx_start, end) for x in context_tfs(tf)}
            res = cpcv.cpcv_trials(compare, dec, context, extra_cost_usd=live_extra_cost_usd(ctx), holdout=holdout,
                                   swap=live_swap(ctx), selection=cpcv.selection_set(rows, {str(row["family"])}, tf),
                                   skip_stale_features=True)
            mine = res["per_trial"][t]
            if "skipped" in mine:                # the trial's features are not what the data builds now
                out["trials"][t] = f"skipped: {mine['skipped']}"
                lines += [f"## Trial {t}: {row['family']} ({tf})", "", f"Skipped: {mine['skipped']}.", ""]
                continue
            ctx.trials.attach_evidence(t, cpcv.EVIDENCE_KIND, cpcv.evidence_payload(mine, res["pbo"], f"cpcv_quarterly {q}"),
                                       now=slot.to_pydatetime())
            out["trials"][t] = {"n_paths": mine.get("n_paths"), "sharpe": mine.get("sharpe"), "mean_r": mine.get("mean_r"),
                                "pbo": (res["pbo"] or {}).get("pbo"), "compared": [int(x["trial"]) for x in compare],
                                "fragile": cpcv.verdict(mine, res["pbo"])["fragile"]}
            lines += [f"## Trial {t}: {row['family']} ({tf})", "", *cpcv.report_lines(res["per_trial"], res["pbo"]), ""]
        except Exception as exc:                 # one trial's failure must not hide the others' evidence
            log.exception("cpcv_quarterly: trial %s", t)
            out["errors"][t] = f"{type(exc).__name__}: {exc}"
    report = ctx.state_dir / f"cpcv_{q}.md"
    report.write_text("\n".join(lines))
    out["report"] = str(report)
    if out["errors"]:
        raise RuntimeError(f"cpcv_quarterly failed for trials {sorted(out['errors'])}: {out['errors']} (report {report})")
    return out


# ---------------------------------------------------------------------------------------------- gap watch
def champion_windows(ctx: JobContext) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Each champion's training window as the Saturday retrain builds it: train_months + 4 x test_months up to the
    day its model was fitted (the 1h window for a timeframe without its own)."""
    out = []
    for e in ctx.models.entries:
        if e.status != "champion":
            continue
        m = ctx.population.members.get(e.agent_id)
        tf = gap_watch_mod.member_timeframe(m.family, m.config) if m is not None else "1h"
        wf = ctx.settings.walkforward.get(cast(DecisionTimeframe, tf)) or ctx.settings.walkforward["1h"]
        end = e.created_utc
        out.append((end - pd.DateOffset(months=wf.train_months + 4 * wf.test_months), end))
    return out


def gap_watch(ctx: JobContext, slot: pd.Timestamp) -> dict[str, Any]:
    """Daily after drift_watch (docs/TRADER_LIFECYCLE.md section 3): detects gaps and answers them within the caps
    (ops/gap_watch.py) -> state/gaps.json. Spawns zero-capital shadow founders only; promotion stays with the gates."""
    g = ctx.settings.gaps
    try:
        dq = ctx.store.read("dq_events", start=slot - pd.Timedelta(days=g.dq_window_days), end=slot)
    except (ValueError, OSError) as exc:
        log.warning("gap_watch: dq_events unreadable: %s", exc)
        dq = pd.DataFrame()
    try:
        vol = gap_watch_mod.daily_realised_vol(_bars(ctx, "1h", slot - pd.Timedelta(days=g.regime_history_days), slot))
    except (ValueError, OSError, KeyError) as exc:
        log.warning("gap_watch: 1h bars unreadable: %s", exc)
        vol = pd.Series(dtype=float)
    stages = {f.stem.removeprefix("engine_"): str(_read_json(f).get("stage", "normal"))
              for f in sorted(ctx.state_dir.glob("engine_*.json"))}
    report = gap_watch_mod.run_gap_watch(
        now=slot, settings=g, population=ctx.population, state_dir=ctx.state_dir,
        walkforward_tfs=set(ctx.settings.walkforward), drift=_read_json(ctx.state_dir / "drift.json"), dq=dq,
        daily_vol=vol, champion_windows=champion_windows(ctx), plan=_read_json(ctx.state_dir / PLAN_FILE) or None,
        trials=read_rows(ctx.trials.path), engine_stages=stages, runner=ctx.agent_runner)
    if report.spawned:
        ctx.population.save(ctx.state_dir / "agents.json")
    return {"gaps": [x.gap_id for x in report.gaps], "spawned": report.spawned,
            "staff_runs": [a.target for a in report.actions if a.action == "staff_run"],
            "refused": [f"{r.action} {r.target}: {r.reason}" for r in report.refused]}



# ---------------------------------------------------------------------------------------------- attribution (BACKLOG 12)
def attribution(ctx: JobContext, slot: pd.Timestamp) -> dict[str, Any]:
    """Daily deterministic attribution (research/attribution.py) -> state/attribution.json and attribution.md.
    Reads the shadow book, every account's fills and pending_orders table, the cost tables and 1h bars (regime);
    writes nothing else, so no trade, setting, model or budget changes. A missing input degrades the report (it
    says so in its notes), never the job."""
    from goldbot.execution.costs import settings_swap
    from goldbot.research.attribution import CostModel, VersionRole, build_report, save_report
    a = ctx.settings.attribution
    since = slot - pd.Timedelta(days=a.window_days)
    trades = [t for b in ShadowBook(ctx.state_dir).books.values() for t in b.closed]
    found = canonical_cost_table(ctx.settings, ctx.accounts, ctx.state_dir)
    costs = CostModel.from_table(found[0], live_swap(ctx)) if found is not None else CostModel.from_settings(ctx.settings)
    try:
        vol = gap_watch_mod.daily_realised_vol(_bars(ctx, "1h", since - pd.Timedelta(days=365), slot))
    except (ValueError, OSError, KeyError) as exc:
        log.warning("attribution: 1h bars unreadable, regime unknown: %s", exc)
        vol = pd.Series(dtype=float)
    fills, orders, tables = {}, {}, {}
    for acc in ctx.accounts:
        try:
            fills[acc.account_id] = ctx.store.read("fills", source=acc.account_id, start=since, end=slot)
        except (ValueError, OSError) as exc:
            log.warning("attribution: fills of %s unreadable: %s", acc.account_id, exc)
            fills[acc.account_id] = pd.DataFrame()
        orders[acc.account_id] = _read_json(ctx.state_dir / f"orders_{acc.account_id}.json")
        try:
            table = CostTable.load(ctx.state_dir / f"costs_{acc.account_id}.json")
        except ValueError:
            table = None
        tables[acc.account_id] = CostModel.from_table(table, settings_swap(ctx.settings)) if table is not None else None
    roles = {e.version: VersionRole(status=e.status, promoted_utc=e.promoted_utc) for e in ctx.models.entries}
    rep = build_report(trades, now=slot, costs=costs, settings=a, daily_vol=vol, roles=roles, fills=fills,
                       orders=orders, tables=tables)
    save_report(rep, ctx.state_dir)
    return {"taken": rep.n_taken, "candidates": rep.n_candidates, "net_r": rep.overall.mean_r_net,
            "verdict": rep.overall.verdict, "costs": costs.source}


# ---------------------------------------------------------------------------------------------- feed reconcile (D12)
def feed_reconcile(ctx: JobContext, slot: pd.Timestamp) -> dict[str, Any]:
    """Daily (design: Data architecture, D12/D20): per enabled account, the engine's tick-built 1m bars against the
    broker's own M1 over the last day (goldbot/data/crossfeed.py) -> dq_events, bars_1m_broker, state/reconcile_<acc>.json."""
    from goldbot.data.crossfeed import reconcile_account
    if ctx.broker_for is None:
        return {"skipped": "no broker connection configured for the scheduler"}
    out: dict[str, Any] = {}
    failed: list[str] = []
    for acc in ctx.accounts:
        broker = None
        try:                                  # one unreachable terminal does not stop the other account's check
            broker = ctx.broker_for(acc)
            if broker is None:
                out[acc.account_id] = {"skipped": "no read-only broker connection for this account"}
                continue
            rep = reconcile_account(ctx.store, broker, acc.account_id, acc.symbol, slot, state_dir=ctx.state_dir)
            out[acc.account_id] = rep.model_dump(mode="json", exclude={"account_id"})
        except Exception as exc:
            failed.append(f"{acc.account_id}: {type(exc).__name__}: {exc}")
        finally:
            if broker is not None and hasattr(broker, "shutdown"):
                broker.shutdown()
    if failed:                                # the scheduler records the failure (health: scheduler check)
        raise RuntimeError("feed_reconcile failed for " + "; ".join(failed) + f" (done: {sorted(out)})")
    return out


# ---------------------------------------------------------------------------------------------- backups (state store step 2)
def _backup_args(ctx: JobContext) -> tuple[Any, Callable[[str], str | None]]:
    from goldbot.ops import backup as backup_mod
    if ctx.get_secret is not None:
        get_secret = ctx.get_secret
    else:
        from goldbot.ops.accounts import get_secret
    return backup_mod.BackupPaths.from_settings(ctx.settings, ctx.state_dir), get_secret


def backup(ctx: JobContext, slot: pd.Timestamp) -> dict[str, Any]:
    """Daily: SQLite (online backup API), state JSON, trial registry, models and the brain's own Parquet -> restic,
    encrypted, to Oracle Object Storage, append only (retention runs from the owner's Mac,
    scripts/backup_retention.sh); state/backup_last.json (health: backup_age, backup_prune)."""
    from goldbot.ops import backup as backup_mod
    paths, get_secret = _backup_args(ctx)
    b = ctx.settings.backup
    return backup_mod.backup_job(paths, get_secret, host=b.host, timeout_s=b.timeout_s)


def restore_drill(ctx: JobContext, slot: pd.Timestamp) -> dict[str, Any]:
    """Weekly: restore the latest snapshot to a temp dir, verify checksums, integrity_check, schema versions, row
    counts and model artefacts, `restic check` a data subset; state/restore_drill_last.json (health: restore_drill)."""
    from goldbot.ops import backup as backup_mod
    paths, get_secret = _backup_args(ctx)
    b = ctx.settings.backup
    return backup_mod.drill_job(paths, get_secret, host=b.host, check_subset=b.check_subset, timeout_s=b.timeout_s)


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
    "recalibrate": recalibrate,
    "drift_watch": drift_watch,
    "gap_watch": gap_watch,
    "feed_reconcile": feed_reconcile,             # D12 daily reconciliation against the broker's M1
    "backup": backup,                             # encrypted off-host backup (ops/backup.py)
    "restore_drill": restore_drill,               # weekly restore + verification of the latest backup
    "cpcv_quarterly": cpcv_quarterly,             # M16 combinatorial purged CV + PBO of gate-passing trials
    "attribution": attribution,                   # BACKLOG 12: daily attribution for the staff agents (reporting only)
}


def build_scheduler(ctx: JobContext, clock: Callable[[], pd.Timestamp] | None = None) -> Scheduler:
    sch = Scheduler(ctx.state_dir / "scheduler.json", clock=clock)
    cfg = ctx.settings.scheduler
    for name, fn in JOBS.items():
        s = getattr(cfg, name)
        sch.add(name, Schedule(kind=s.kind, at=s.at, weekdays=tuple(s.weekdays), weekday=s.weekday, day=s.day,
                               months=tuple(s.months), max_late_hours=s.max_late_hours), functools.partial(fn, ctx))
    return sch
