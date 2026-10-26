"""The scheduler's jobs (design: Retraining and promotion, Bounded research loop, Execution costs).

* nightly_costs     per enabled account: spread table from its own ticks, slippage table from its own fills,
                    swap and commission as the terminal reports them (state/broker_terms_<account>.json, written by
                    the engine; settings commission and the swap prior while missing or older than
                    BROKER_TERMS_MAX_AGE_DAYS) -> state/costs_<account>.json (read by the engine every bar).
                    It also re-runs the account classifier: every night while the stored class is unknown (so a thin
                    first Friday does not block entries for a week), on Fridays only once a class is known (two
                    consecutive disagreements to change).
                    With at least `costs.spread_profile_min_days` recorded broker-tick days in the cost window
                    (tick_record) the table also carries their spread profile by UTC hour and session
                    (`recorded_spread_profile`, aggregates only, reported and not charged).
                    With `costs.publish_release` and a github-token it then publishes the canonical broker's table
                    (costs only) to release costs-v1 for research.yml (`publish_costs`).
                    Last, exploration arms (`exploration_learn`, research/exploration_data.py; H-61/H-62): the bar-
                    replayed net-R labels of resolved candidates, the arms' Thompson shares within each style (floor
                    5%, cap 30%) -> aux.db exploration_weights, and on Sundays the H-62 style models whose retrain is
                    due (a failure is reported and never stops the cost table; stale weights read as equal shares).
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
* model_watch       daily: a new champion whose shadow returns trip the CUSUM alarm against its backtest within its
                    watch (two weeks or its first cusum.WATCH_MIN_TRADES trades, whichever is later, capped at
                    cusum.WATCH_MAX_WEEKS) is replaced by the previous champion (design: Retraining and promotion).
                    A watch whose trade count cannot reach the 5%-rate h reports `cannot_alarm` (job output,
                    state/model_watch.json, a health warning): such slow agents rely on drift_watch and the drawdown
                    halt.
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
* tv_reconcile      daily (goldbot/data/tv_reconcile.py): the TradingView bars the direct pull read (a second feed, OANDA,
                    never a primary bar source) against the Dukascopy/broker M1 resampled, last 3 days; a close more than
                    2 points off flags the bar (`dq`) and sets its plots NaN in tv_indicators, an event to dq_events
                    (source tradingview). Reporting and data quality only; never blocks an entry or an exit.
* broker_ledger     daily (finance audit GAP-07/GAP-08, RC-8/RC-9): per account, read-only, a balance snapshot (balance,
                    equity, margin, currency) and the deposits, withdrawals and adjustments in the broker's deal history,
                    the balance tied out against the deals, and goldbot's closed records and open trades reconciled
                    with the broker by position id -> aux.db (ops/ledger.py; health check reconcile:<account>).
                    Skipped without a broker.
* tick_record       daily (BACKLOG 79, goldbot/data/ticks.py): per account, the broker's ticks of every closed server
                    day not yet recorded (copy_ticks, read-only, paged; missed days back-filled) ->
                    state/ticks/<account>/<date>.parquet + index.json, older than ticks.retain_days pruned (health
                    check ticks:<account>). Research data for the H-A holiday diagnostic. Skipped without a broker.
* limits_job        daily before the risk-day roll (ADR 0031, goldbot/ops/limits_job.py): per demo account with the
                    owner's ceilings, the adaptive limits for the next risk day from its demo fills and the shadow book
                    since each strategy's freeze (core.db adaptive_state / adaptive_changes; health check
                    limits_controller:<account>); an FYI to the owner on every change. Places nothing.
* daily_review      daily at 06:15, after the nightly learner and before the 06:45 owner report
                    (goldbot/research/daily_review.py): the learning hosts' costs, fill rate, refusals, volume against
                    the plan, arm health (breached / ok only) and integrity for the opportunity day before the slot ->
                    state/aux/daily_review/<day>.json; retires an arm at its pre-registered H-61 stop, refits a
                    (broker, session) slippage cell after 200 fills (never cheaper within a day), a learning-ops issue on
                    an incident or two low-volume days and one comment on #34 (github-token from the keyring, else
                    state/aux/daily_review/outbox/). Changes no arm, threshold, prior, cap or the bandit.
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
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Callable, cast

import numpy as np
import pandas as pd

from goldbot.agents.roles import ROLES
from goldbot.agents.runner import AgentRunner
from goldbot.base import Record, load_record, write_atomic
from goldbot.config import DecisionTimeframe, Settings
from goldbot.data.release import store_macro
from goldbot.data.store import Store
from goldbot.db.connection import Database
from goldbot.engine import cost_probe
from goldbot.engine.shadow import ShadowBook
from goldbot.execution.classifier import PersistentClassifier, classify
from goldbot.execution.costs import (
    BrokerTerms,
    CostTable,
    SpreadProfile,
    apply_probe_slippage,
    build_cost_table,
    probe_entry_fills,
    probe_slippage,
    publishable,
    spread_profile,
)
from goldbot.features.mtf import TF_LABEL, context_tfs
from goldbot.features.registry import versions_for
from goldbot.labels.triple_barrier import SwapSpec
from goldbot.ops import gap_watch as gap_watch_mod
from goldbot.ops.accounts import Account
from goldbot.ops.scheduler import Schedule, Scheduler
from goldbot.research.cusum import (
    WATCH_MAX_WEEKS,
    WATCH_MIN_TRADES,
    WATCH_WEEKS,
    calibrated_h,
    can_alarm,
    watch_trades,
)
from goldbot.research.director import (
    GRID_RATIONALE,
    PLAN_FILE,
    RETIRED_EXPLORATION,
    AgentEvidence,
    ResearchPlan,
    ShadowEvidence,
    build_plan,
    holdout_window,
    later_preregistration,
    later_refusal,
    load_attribution,
    load_hypotheses,
    pending_preregistration,
    quarter_budget,
    reserved_trials,
    retired_quarter_trials,
    rule_preregistration,
)
from goldbot.research.drift import AgentHealth, assess, system_halt_reasons
from goldbot.research.exploration_data import ReplayCosts, label_candidates, learn, read_obs, stamp_brokers
from goldbot.research.model import RecalibratedCalibrator, fit_recalibration
from goldbot.research.model_registry import ModelEntry, ModelRegistry
from goldbot.research.pipeline import (
    DEFAULT_FEATURE_NAMES,
    MACRO_FEATURE_NAMES,
    ResearchResult,
    build_decision_frame,
    run_specialist,
)
from goldbot.research.population import Population
from goldbot.research.promotion import EVIDENCE_START_UTC, PerfStats, cusum_alarm, evaluate_promotion
from goldbot.research.registry import TrialBudgetExceeded, TrialRegistry, config_hash, quarter_of
from goldbot.research.registry_sync import read_rows
from goldbot.specialists import SPECIALISTS
from goldbot.specialists.base import Specialist

log = logging.getLogger("goldbot.jobs")

CHALLENGER_MAX_WEEKS = 8
CLASSIFIER_WEEKDAY = 4          # Friday's nightly run re-classifies a known class (design: weekly, nightly cost job)
CONTEXT_EXTRA_MONTHS = 2        # daily/4h/1h context needs history before the decision window starts
WATCH_GRACE = pd.Timedelta(days=1)   # the daily job evaluates a champion watch once more after it closes
MODEL_WATCH_FILE = "model_watch.json"   # the last run's per-agent watch status (health: ops/health.check_model_watch)
CANNOT_ALARM_NOTE = ("too few trades for a CUSUM alarm at the 5% false-alarm rate: this champion relies on drift_watch "
                     "(quarterly residual CUSUM, calibration, PSI) and the drawdown halt")
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
    broker_for: Callable[[Account], Any] | None = None           # read-only Broker per account (feed_reconcile, broker_ledger)
    get_secret: Callable[[str], str | None] | None = None        # backup credentials (default: goldbot.ops.accounts)
    clock: Callable[[], pd.Timestamp] | None = None              # tick_record's wall clock (default: now, UTC)
    sleep: Callable[[float], None] | None = None                 # tick_record's pause between pages (default: time.sleep)


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
        profile, profile_note = recorded_spread_profile(ctx.state_dir, acc.account_id, slot,
                                                        window_days=r.cost_window_days,
                                                        min_days=ctx.settings.costs.spread_profile_min_days)
        table = build_cost_table(acc.account_id, ticks, fills, commission_per_lot_side_usd=commission,
                                 slippage_prior_usd=ctx.settings.costs.slippage_prior_usd,
                                 min_fills=r.min_fills_for_slippage, now=slot, swap=swap, commission_measured=measured,
                                 notes=[*notes, profile_note], spread_profile=profile)
        if measured and terms is not None:
            table = table.model_copy(update={"commission_lots": terms.commission_lots})
        n_probe = 0
        try:     # scheduled-probe fills raise a session's slippage when proven above the prior; never in `fills` (K7)
            window_start = slot - pd.Timedelta(days=r.fills_window_days)
            prows = probe_entry_fills(cost_probe_rows(ctx.state_dir, acc.account_id, window_start, slot,
                                                      held=_held_spans(ctx, acc, slot)))
            n_probe = len(prows)
            table = apply_probe_slippage(table, probe_slippage(prows, ctx.settings.costs.slippage_prior_usd), n_probe)
        except (sqlite3.Error, OSError, ValueError) as exc:     # unreadable aux.db: the prior stands
            log.warning("cost probes for %s unreadable (%s): slippage keeps the prior", acc.account_id, type(exc).__name__)
            table = table.model_copy(update={"notes": [*table.notes, "scheduled-probe fills unreadable: slippage keeps the prior"]})
        table.save(ctx.state_dir / f"costs_{acc.account_id}.json")
        row: dict[str, Any] = {"ticks": len(ticks), "fills": len(fills), "probe_fills": n_probe,
                               "round_trip_usd": {s: table.round_trip_usd_per_oz(s) for s in ("asia", "london", "newyork")},
                               "swap_measured": swap is not None, "commission_measured": measured,
                               "spread_profile_days": profile.days if profile is not None else 0}
        try:
            persistent = PersistentClassifier(ctx.state_dir / f"classifier_{acc.account_id}.json")
            if slot.dayofweek == CLASSIFIER_WEEKDAY or not persistent.is_known():
                c = classify(ticks, fills)
                row["account_class"] = persistent.update(c)
                row["classifier_reason"] = c.reason
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:   # a torn file: not the other accounts
            log.exception("classifier for %s failed", acc.account_id)
            row["classifier_error"] = f"{type(exc).__name__}: classifier file unreadable or invalid"
        out[acc.account_id] = row
    if ctx.upload_costs is not None:            # costs.publish_release and a github-token: research reads it
        out["publish"] = publish_costs(ctx.settings, ctx.accounts, ctx.state_dir, ctx.upload_costs, slot)
    try:          # exploration arms: never in the way of the cost table (a nightly slot of its own needs config)
        out["exploration"] = exploration_learn(ctx, slot)
    except Exception as exc:       # noqa: BLE001 - weights fall back to equal shares when stale
        log.exception("exploration learner failed")
        out["exploration"] = {"error": type(exc).__name__}
    return out


def cost_probe_rows(state_dir: Path, account_id: str, lo: pd.Timestamp, hi: pd.Timestamp,
                    held: Sequence[tuple[pd.Timestamp, pd.Timestamp, str]] = ()) -> list[Any]:
    """The account's probes sent in [lo, hi] that may feed its cost table: none void for the cost cells (aux.db
    evidence_exclusions, prereg Amendment 4: a scheduled probe entered inside a dq quarantine span), and, in memory
    only, no scheduled probe entered inside a `held` span (one whose engine restart is not recorded yet)."""
    from goldbot.research.evidence_exclusions import SCHEDULED_PURPOSE, excluded_ids
    rows = [p for p in cost_probe.probes(state_dir, account_id) if lo <= p.sent_utc <= hi]
    if not rows:
        return rows
    void = excluded_ids(Database.open(state_dir, "aux"), "cost_cells")
    return [p for p in rows if p.client_order_id not in void
            and not (p.purpose == SCHEDULED_PURPOSE and any(a <= p.sent_utc < b for a, b, _ in held))]


# ---------------------------------------------------------------------------------------------- exploration arms
EXPLORATION_LABEL_DAYS = 30      # candidates still unlabelled after this long are left (their bars are long gone)


def exploration_arms(ctx: JobContext, broker: str | None = None
                     ) -> tuple[dict[str, str], list[str], dict[str, float]]:
    """(strategy arm -> style, control arms, arm -> share ceiling) from each account's `exploration` block when it has
    one (read by attribute: accounts.yaml owns it), only `broker`'s accounts when given (the learner always gives it:
    one broker's arms never enter another's run); arms without a style are learnt from their candidates."""
    styles: dict[str, str] = {}
    controls: list[str] = []
    ceilings: dict[str, float] = {}
    for acc in ctx.accounts:
        if broker is not None and getattr(acc, "broker", None) != broker:
            continue
        arms = getattr(getattr(acc, "exploration", None), "arms", None) or {}
        items = arms.items() if isinstance(arms, dict) else ((getattr(a, "arm_id", None), a) for a in arms)
        for arm_id, a in items:
            if not arm_id:
                continue
            if getattr(a, "control_share", None) is not None or getattr(a, "is_control", False):
                controls.append(str(arm_id))
                continue
            if isinstance(style := getattr(a, "style", None), str):
                styles[str(arm_id)] = style
            if isinstance(cap := getattr(a, "max_share", None), (int, float)):
                ceilings[str(arm_id)] = float(cap)
    return styles, controls, ceilings


def broker_cost_table(accounts: list[Account], state_dir: Path, broker: str) -> tuple[CostTable, Account] | None:
    """The readable nightly cost table of an account on `broker` resting on the most fills (its accounts share one
    cost basis; the first in accounts.yaml order on a tie), with its account; None before one exists."""
    best: tuple[CostTable, Account] | None = None
    for acc in accounts:
        if acc.broker != broker:
            continue
        try:
            table = CostTable.load(state_dir / f"costs_{acc.account_id}.json")
        except ValueError:
            table = None
        if table is not None and (best is None or table.n_fills > best[0].n_fills):
            best = (table, acc)
    return best


def exploration_costs(ctx: JobContext, slot: pd.Timestamp, broker: str) -> ReplayCosts:
    """`broker`'s label replay cost model, point in time (ADR 0043 amendment, quant 2026-10-26: "costs are keyed by
    broker; never pooled across brokers"): its own measured table as built (slippage per leg from its market cells,
    its commission, its swap), else the conservative settings priors (the slippage prior, the dearest configured
    commission, the swap prior). Never another broker's measurements. A table resting on fewer than
    `research.min_fills_for_slippage` fills charges the slippage prior, whatever its cells say."""
    from goldbot.execution.costs import settings_swap
    prior_swap = settings_swap(ctx.settings)
    c = ctx.settings.costs
    found = broker_cost_table(ctx.accounts, ctx.state_dir, broker)
    if found is not None and found[0].built_utc <= slot:
        table, acc = found
        swap = table.swap_spec(acc.server_tz, prior_swap.triple_weekday)
        slips = [max(v.mean, 0.0) for k, v in table.slippage.items() if k.endswith(":market")]
        measured = table.n_fills >= ctx.settings.research.min_fills_for_slippage and slips
        costs = ReplayCosts(broker=broker, version=f"costs:{acc.account_id}:{table.built_utc:%Y%m%dT%H%M}",
                            effective_utc=table.built_utc,
                            slippage_usd=max(slips) if measured else max(table.slippage_prior_usd, c.slippage_prior_usd),
                            commission_rt_per_oz=2 * table.commission_per_lot_side_usd / 100.0,
                            swap=swap if swap is not None else prior_swap)
    else:
        costs = ReplayCosts(broker=broker, version=f"settings-priors:{broker}",
                            effective_utc=pd.Timestamp("2026-01-01", tz="UTC"), slippage_usd=c.slippage_prior_usd,
                            commission_rt_per_oz=2 * max(c.commission_per_lot_side_usd.values(), default=0.0) / 100.0,
                            swap=prior_swap)
    return _with_refit_cells(ctx.state_dir, broker, slot, costs)


def _with_refit_cells(state_dir: Path, broker: str, slot: pd.Timestamp, costs: ReplayCosts) -> ReplayCosts:
    """The daily review's refit slippage cells (research/daily_review.py), point in time: charged only when dearer
    than the slippage above, so a refit can raise a broker's replay cost and never lower it."""
    from goldbot.research.daily_review import replay_cells
    cells = replay_cells(state_dir, broker, slot)
    if cells is None or cells[0] <= costs.slippage_usd:
        return costs
    slip, day, effective = cells
    return costs.model_copy(update={"slippage_usd": slip, "version": f"{costs.version}+cells:{day}",
                                    "effective_utc": max(costs.effective_utc, effective)})


def exploration_learn(ctx: JobContext, slot: pd.Timestamp) -> dict[str, Any]:
    """Nightly (inside nightly_costs; research/exploration_data.py), per BROKER (ADR 0043 amendment: evidence and
    costs are never pooled across brokers): key any unkeyed rows by their account's broker, replay-label the
    candidates whose label resolved (bar replay on each account's own 1m bid/ask bars, with its broker's own cost
    model), then each broker's H-61 bandit (within-style shares from its own evidence and its own accounts' arms) ->
    aux.db exploration_broker_weights; on Sundays also each broker's H-62 style models whose retrain is due
    (state/models/h62/<broker>/)."""
    db = Database.open(ctx.state_dir, "aux")
    stamped = stamp_brokers(db, {acc.account_id: acc.broker for acc in ctx.accounts})
    try:        # prereg Amendment 4, before any read (criterion v1)
        void, held = evidence_exclusions(ctx, db, slot)
    except Exception as exc:       # noqa: BLE001 - fail closed: no posterior on evidence not screened (weights age out)
        log.exception("evidence exclusions not computed: the exploration learner does not run tonight")
        return {"error": f"evidence_exclusions:{type(exc).__name__}"}
    pending = read_obs(db)
    pending = pending[pending["label_sim_r"].isna()]
    unkeyed = int(pending["broker"].isna().sum())
    if unkeyed:
        log.warning("%d exploration candidates have no broker (account not in accounts.yaml): never learnt", unkeyed)
    pending = pending[pending["broker"].notna()]
    labelled: dict[str, int] = {}
    costs: dict[str, ReplayCosts] = {}
    start = max(pending["decision_utc"].min(), slot - pd.Timedelta(days=EXPLORATION_LABEL_DAYS)) \
        if not pending.empty else None
    keys = sorted({(str(a), str(b)) for a, b in zip(pending["account_id"], pending["broker"], strict=True)})
    for account, broker in keys:
        if broker not in costs:
            costs[broker] = exploration_costs(ctx, slot, broker)
        cm = costs[broker]
        bars = ctx.store.read("bars_1m", source=account, start=start, end=slot)
        n = label_candidates(db, bars, cm, cutoff=slot, account_id=account) if not bars.empty else 0
        labelled[account] = labelled.get(account, 0) + n
    seen = read_obs(db)["broker"].dropna()
    brokers = sorted({str(b) for b in seen} | {acc.broker for acc in ctx.accounts
                                               if getattr(getattr(acc, "exploration", None), "arms", None)})
    out: dict[str, Any] = {}
    for broker in brokers:
        styles, controls, ceilings = exploration_arms(ctx, broker)
        try:                           # one broker's failure never skips another's run
            out[broker] = learn(db, broker=broker, as_of=slot, arms=styles, controls=controls, ceilings=ceilings,
                                seed=int(slot.value % (2**32)), retrain=slot.dayofweek == 6,
                                models_dir=ctx.state_dir / "models", held_out=held)
        except Exception as exc:       # noqa: BLE001 - its weights age into equal shares; the others still learn
            log.exception("exploration learner failed for broker %s", broker)
            out[broker] = {"error": type(exc).__name__}
    return {"labelled": labelled, "cost_model": {b: m.version for b, m in costs.items()}, "stamped": stamped,
            "unkeyed": unkeyed, "evidence_exclusions": void, "brokers": out}


def evidence_exclusions(ctx: JobContext, db: Database, slot: pd.Timestamp) -> tuple[dict[str, Any], frozenset[str]]:
    """Criterion v1 (research/evidence_exclusions.py) before the learner reads: the exclusions of every span whose
    engine restart (R) is recorded are written (append-only, idempotent); a span with no recorded R writes nothing
    (over-voiding cannot be undone) and its decisions from the span's start are returned to be held out in memory."""
    from goldbot.research import evidence_exclusions as ev
    inserted, held, open_ = 0, set[str](), list[str]()
    for symbol in sorted({getattr(acc, "symbol", "XAUUSD") for acc in ctx.accounts}):
        hosts = {acc.account_id: acc.broker for acc in ctx.accounts if getattr(acc, "symbol", "XAUUSD") == symbol}
        found = ev.screen(db, ctx.store, hosts=hosts, symbol=symbol, now=slot)
        inserted += ev.record(db, found.exclusions)
        held |= found.held_out
        open_ += found.unresolved
    if open_:
        log.warning("quarantine span with no recorded engine restart on %s: decisions held out of learning, not "
                    "voided (run scripts/evidence_exclusions.py)", ", ".join(sorted(open_)))
    return {"inserted": inserted, "held_out": len(held), "unresolved": sorted(open_)}, frozenset(held)


def _held_spans(ctx: JobContext, acc: Account, slot: pd.Timestamp) -> list[tuple[pd.Timestamp, pd.Timestamp, str]]:
    """The account's quarantine spans with no recorded engine restart (its scheduled probes inside them are held out
    of the cost table in memory)."""
    from goldbot.research import evidence_exclusions as ev
    spans = ev.spans_of(ctx.store, {acc.account_id: acc.broker}, symbol=acc.symbol, now=slot).get(acc.account_id, [])
    rel = ev.reloads(Database.open(ctx.state_dir, "aux")) if spans else {}
    return [s for s in spans if (acc.account_id, s[2]) not in rel]


COSTS_PUBLISHED_FILE = "costs_published.json"    # state: when the table last reached release costs-v1 (health reads it)


def publish_costs(settings: Settings, accounts: list[Account], state_dir: Path, upload: Callable[[bytes], str],
                  now: pd.Timestamp) -> dict[str, Any]:
    """Upload the canonical broker's cost table to release costs-v1 (asset costs_measured.json) for research.yml.
    Costs only (`PublishedCostTable`: no account id, login, balance or equity). Refused while swap or the spread is
    unmeasured (`publishable`); without enough fills the slippage is published as the prior and labelled so
    (costs-binding-2026-10-18.md K3), never as a measurement. An upload failure is recorded, never raised (the cost
    table itself is already saved). state/costs_published.json keeps the last success and the last attempt for the
    health check."""
    path = state_dir / COSTS_PUBLISHED_FILE
    try:
        rec: dict[str, Any] = json.loads(path.read_text()) if path.exists() else {}
    except (ValueError, OSError):
        rec = {}
    found = canonical_cost_table(settings, accounts, state_dir)
    if found is None:
        pub, reason = None, "no cost table on the canonical-cost broker yet"
    else:
        pub, reason = publishable(found[0], found[1].broker)
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


def recorded_spread_profile(state_dir: Path, account_id: str, slot: pd.Timestamp, *, window_days: int,
                            min_days: int) -> tuple[SpreadProfile | None, str]:
    """The spread profile of the broker ticks `tick_record` recorded (state/ticks/<account>/, read with
    `ticks.load_day`, sha256 checked) for the closed server dates of the last `window_days` days (the slot's own date is
    partial and never counted), and a note; None below
    `min_days` usable days (costs-binding-2026-10-18.md section 1.4 step 4: read only after >= 10 trading days, so the
    medians are not one quiet week's). A day that cannot be read or fails its digest is skipped and counted, never
    used. Only the aggregate leaves this function."""
    from goldbot.data.ticks import load_day, recorded_days
    first = (slot - pd.Timedelta(days=window_days)).date()
    try:
        days = [d for d in recorded_days(account_id, state_dir=state_dir) if first <= d < slot.date()]   # today: partial
    except (ValueError, OSError) as exc:                 # no such label, or an unreadable index: no profile
        return None, f"spread profile: tick record index unreadable ({type(exc).__name__})"
    skipped: list[str] = []

    def frames() -> Any:
        for d in days:
            try:
                yield load_day(account_id, d, state_dir=state_dir)
            except (ValueError, OSError, KeyError) as exc:   # digest mismatch, torn, missing file or column
                skipped.append(d.isoformat())
                log.warning("tick record %s %s not used for the spread profile: %s", account_id, d, exc)
    prof = spread_profile(frames(), first_day=days[0], last_day=days[-1]) if days else None
    used = prof.days if prof is not None else 0
    tail = f", {len(skipped)} skipped (unreadable or digest mismatch)" if skipped else ""
    if prof is None or used < min_days:
        return None, f"spread profile: {used} recorded days in {window_days} d{tail}, fewer than {min_days}: not built"
    kept = [d for d in days if d.isoformat() not in skipped]
    prof = prof.model_copy(update={"days_skipped": len(skipped), "first_day": kept[0], "last_day": kept[-1]})
    return prof, f"spread profile: {used} recorded days {kept[0]}..{kept[-1]}{tail}"


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


def live_cost_source(ctx: JobContext) -> str:
    """Where `live_extra_cost_usd` and `live_swap` come from, in the words research_pass.py records
    (`costs.research_costs`): the canonical broker's nightly cost table, or PRIORS ONLY before one exists. Recorded on
    every walk-forward row as `cost_source`, which `TrialRegistry.passed_gates` requires."""
    from goldbot.execution.costs import research_costs
    found = canonical_cost_table(ctx.settings, ctx.accounts, ctx.state_dir)
    return research_costs(ctx.settings, found[0] if found is not None else None)[2]


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
    res.metrics["cost_source"] = live_cost_source(ctx)        # with the swap in metrics: the row's cost treatment
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
    return load_record(PerfStats, json.loads(f.read_text()), source="shadow_<version>.json") if f.exists() else None


def _backtest_stats(entry: ModelEntry) -> PerfStats | None:
    """The backtest a model was registered with (models/registry.json), None when it has none."""
    return load_record(PerfStats, entry.backtest, source="models/registry.json:backtest") if entry.backtest else None


def _decide_challengers(ctx: JobContext, agent_id: str, slot: pd.Timestamp) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    champion = ctx.models.champion(agent_id)
    champ_shadow = shadow_stats(ctx, champion) if champion else None
    champ_turnover = champ_shadow or (_backtest_stats(champion) if champion else None)
    for ch in ctx.models.by_agent(agent_id, "challenger"):
        bt = _backtest_stats(ch)
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
            out.append({"version": ch.version, "action": "waiting", "shadow_trades": sh.n_trades if sh else 0,
                        "shadow_clusters": sh.n_clusters if sh else 0})
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
        if getattr(spec, "barriers_on_1m", False):    # labelled on 1m barriers: a frozen rule agent, never retrained
            agent["retrain"] = "skipped: a frozen rule agent labelled on 1m barriers (scored by its rule, ADR 0038)"
            out[m.agent_id] = agent
            continue
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
    """CUSUM watch of each new champion against its backtest (design: a new champion that trips the alarm early is
    replaced by the previous one). The watch covers the trades exited in the first two weeks or the first
    cusum.WATCH_MIN_TRADES, whichever is more, and closes at the later of two weeks and that trade's exit, never past
    cusum.WATCH_MAX_WEEKS (CUSUM re-verify: two weeks of a 2-2.5 trades/week agent are 4-5 trades, where no 5%-rate
    alarm can fire). h is calibrated for `cusum.watch_trades` trades. When the statistic cannot exceed h in the
    watch's trades (`cusum.can_alarm`), the agent is reported `cannot_alarm` instead of a silent "ok"; such agents
    rely on drift_watch and the drawdown halt. Writes state/model_watch.json for the health check."""
    book = ShadowBook(ctx.state_dir)   # read-only view of the engine's shadow book
    d = ctx.settings.drift
    cap_age = pd.Timedelta(weeks=WATCH_MAX_WEEKS)
    out: dict[str, Any] = {}
    for agent_id in ctx.models.agent_ids():
        ch = ctx.models.champion(agent_id)
        if ch is None or ch.promoted_utc is None or slot - ch.promoted_utc > cap_age + WATCH_GRACE:
            continue
        if not ctx.models.by_agent(agent_id, "previous"):
            continue                    # a first model has nothing to fall back to
        start = ch.promoted_utc
        closed = book.books[ch.version].closed if ch.version in book.books else []
        # (exit time, return) of the taken trades exited since promotion, oldest exit first
        trades = sorted((pd.Timestamp(t.exit_ts), float(t.ret)) for t in closed
                        if t.taken and t.ret is not None and t.exit_ts is not None
                        and start <= t.exit_ts <= min(slot, start + cap_age))
        two_weeks = start + pd.Timedelta(weeks=WATCH_WEEKS)
        window = trades[:max(sum(1 for ts, _ in trades if ts <= two_weeks), WATCH_MIN_TRADES)]
        closes = (max(two_weeks, window[WATCH_MIN_TRADES - 1][0]) if len(window) >= WATCH_MIN_TRADES
                  else start + cap_age)
        if slot > closes + WATCH_GRACE:
            continue                    # closed, and evaluated by the run after it closed
        is_open = slot < closes
        bt = _backtest_stats(ch)
        if bt is None or bt.mean_ret is None or bt.std_ret is None:
            out[agent_id] = {"version": ch.version, "watch": "no backtest moments; cannot run CUSUM"}
            continue
        p = bt.hit_rate if 0 < bt.hit_rate < 1 else None
        n_watch = watch_trades(bt.trades_per_week)
        # M25: h for a 5% false-alarm rate over the watch's own trade count, on two-point returns that win with the
        # backtest's hit rate
        h = calibrated_h(bt.trades_per_week, d.cusum_k, d.cusum_false_alarm, p=p, trades=n_watch)
        n_reach = (n_watch or WATCH_MIN_TRADES) if is_open else len(window)
        info = {"version": ch.version, "trades": len(window), "watch_trades": n_watch, "h": round(h, 4),
                "window": "open" if is_open else "closed", "closes": closes.isoformat()}
        rets = [r for _, r in window]
        if not can_alarm(h, n_reach, d.cusum_k, p):
            out[agent_id] = {**info, "action": "cannot_alarm", "note": CANNOT_ALARM_NOTE}
        elif cusum_alarm(rets, bt.mean_ret, bt.std_ret, k=d.cusum_k, h=h):
            restored = ctx.models.restore_previous(
                agent_id, f"CUSUM alarm on {len(rets)} shadow trades in the new-champion watch (h {h:.3f})")
            out[agent_id] = {**info, "action": "restored_previous", "restored": restored.version}
        else:
            out[agent_id] = {**info, "action": "ok"}
    write_atomic(ctx.state_dir / MODEL_WATCH_FILE, json.dumps({"ts": slot.isoformat(), "agents": out}, default=str))
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


def _recent_candidates(ctx: JobContext, spec: Any, slot: pd.Timestamp, days: int,
                       feature_version: str = "") -> pd.DataFrame | None:
    """Decision-frame features (plus `side`) of the specialist's candidates in the last `days`, built exactly as for
    training (same context timeframes and feature registry), at the champion's own `feature_version` (legacy
    implementations where it needs them, `registry.versions_for`), over the default set or, for a model trained with
    the macro drivers, the default set plus MACRO_FEATURE_NAMES with the store's macro rows as context. None when no
    registered implementation reproduces that version, or a macro model finds no macro rows: its PSI cannot be
    computed on the features it was trained on."""
    names: list[str] | None = None
    versions: dict[str, str] | None = {}
    feat_ctx: dict[str, Any] | None = None
    if feature_version:
        versions = versions_for(DEFAULT_FEATURE_NAMES, feature_version)
        if versions is not None:
            names = list(DEFAULT_FEATURE_NAMES)
        else:
            versions = versions_for([*DEFAULT_FEATURE_NAMES, *MACRO_FEATURE_NAMES], feature_version)
            if versions is None:
                return None
            macro = store_macro(ctx.store)
            if macro.empty:
                return None
            names, feat_ctx = [*DEFAULT_FEATURE_NAMES, *MACRO_FEATURE_NAMES], {"macro": macro}
    start = slot - pd.Timedelta(days=days + DRIFT_WARMUP_DAYS)
    dec = _bars(ctx, spec.timeframe, start, slot)
    if dec.empty:
        return pd.DataFrame()
    ctx_start = start - pd.DateOffset(months=CONTEXT_EXTRA_MONTHS)
    context = {TF_LABEL[tf]: _bars(ctx, tf, ctx_start, slot) for tf in context_tfs(spec.timeframe)}
    m, X = build_decision_frame(dec.reset_index(drop=True), context, feature_names=names, ctx=feat_ctx,
                                versions=versions)
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
        unbuildable = False
        try:
            model = ctx.models.load(e)
            live = None
            if member is not None:
                live = _recent_candidates(ctx, member.specialist(), slot, d.window_days, e.feature_version)
                unbuildable = live is None
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
        if unbuildable:
            health[-1].notes.append(f"feature version {e.feature_version} cannot be rebuilt by any registered "
                                    "implementation: PSI not computed")
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
        research_passed=lambda m: ctx.trials.passed_gates(m.family, {**SPECIALISTS[m.family].default_config, **m.config}),
        evidence_start=EVIDENCE_START_UTC)       # H-52: trades opened before the history-gap fix are quarantined
    for aid in summary["expired"]:
        ctx.models.retire_agent(aid, "retired agent's six months of shadow trading are over")
    ctx.population.save(ctx.state_dir / "agents.json")
    summary["registry_sync"] = synced
    return summary


# ---------------------------------------------------------------------------------------------- research director
def research_families() -> list[str]:
    """The families the research director, the label grid, the monthly loop and the research analyst plan and spend
    trials on: every registered family except one under its pre-registered rule-only screen (`Specialist.screening`,
    e.g. asia_drift / H-04), whose only trial is the pre-registered one (research.yml), as it is kept out of the
    default founders and the pools (quant review M1)."""
    return sorted(f for f, cls in SPECIALISTS.items() if not cls.screening and not cls.requires_tv)   # TV: TV_GRID_V1 only


def research_budget(ctx: JobContext) -> tuple[int, int]:
    """(most trials the plan may hand out, per-family cap). The plan never exceeds the quarter's remaining budget
    (build_plan); on top of that it is bounded by the flat monthly loop (budget x families) when the grid runs, and
    by the quarter budget alone while the grid is paused; a family is capped at the label grid's size or the quarter
    budget, whichever is larger (pre-registered trials are not limited to grid variants)."""
    r = ctx.settings.research
    q = quarter_budget(r)
    families = research_families()
    grid = max(len(label_grid(SPECIALISTS[f].default_config, r.label_grid_step)) for f in families)
    total = min(q, r.trial_budget_per_month * len(families)) if r.trial_budget_per_month > 0 else q
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
    plan = build_plan(slot, research_families(), read_rows(ctx.trials.path), shadow, agents,
                      quarter_budget=quarter_budget(r), monthly_total=budget, trial_budget_per_month=r.trial_budget_per_month,
                      floor=r.director_floor, cap=cap, holdout=holdout_window(r),
                      attribution=load_attribution(ctx.state_dir), hypotheses=load_hypotheses(),
                      retired=r.retired_families, promoted={e.version: e.promoted_utc for e in ctx.models.entries},
                      reserved_setting=r.reserved_for(quarter_of(slot.tz_convert("UTC").to_pydatetime())),
                      grid_paused=r.label_grid_paused,
                      evidence_start=EVIDENCE_START_UTC)   # H-52: attribution rows opened before the fix are quarantined
    plan.save(ctx.state_dir / PLAN_FILE)
    return {"quarter": plan.quarter, "quarter_used": plan.quarter_used, "quarter_reserved": plan.quarter_reserved,
            "budget": plan.budget, "grid_budget": plan.grid_budget, "unallocated": plan.unallocated,
            "focus": [f.family for f in plan.focus], "blocked": [s.family for s in plan.evidence if s.blocked],
            "retired": [s.family for s in plan.evidence if s.retired], "retired_explore": plan.retired_explore,
            "hypotheses_drift": plan.hypotheses_drift,
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
    charged to the quarter's pre-registered trial budget and never sees the holdout window. Like the director and the
    label grid it spends only budget - used - reserved (`reserved_trials`, research.reserved_trials_quarter); only a
    trial of a configuration with a pending `preregistered` row of the quarter may use the reservation, and its row is
    linked to that pre-registration (so the reservation shrinks by one). A retired family (research.retired_families)
    that the director's current plan has not reinstated gets no trial from the open budget beyond the retired
    families' ONE shared exploration trial a quarter (director.RETIRED_EXPLORATION, counted from the registry as the
    director counts it); only a pending pre-registration of exactly that configuration lifts the refusal. A
    configuration queued for a later quarter is refused (`director.later_preregistration`)."""
    def run(family: str, overrides: dict[str, Any], rationale: str) -> dict[str, Any]:
        end = now() if now is not None else pd.Timestamp.now("UTC")
        r = ctx.settings.research
        if family in SPECIALISTS and SPECIALISTS[family].screening:
            return {"error": f"{family} is under its pre-registered screen: its only trial is the pre-registered one "
                             "(research.yml, docs/research/preregistration-2027Q1.md); the analyst runs none"}
        config = {**SPECIALISTS[family].default_config, **overrides}
        _sync_trials(ctx)                 # count the research workflow's trials before charging the budget
        with ctx.trials.locked():
            rows = read_rows(ctx.trials.path)
            q_now = quarter_of()          # the quarter the row will be stamped in (check_budget's)
            prereg = pending_preregistration(rows, q_now, family, config_hash(config))
            later = None if prereg is not None else later_preregistration(rows, q_now, family, config_hash(config))
            if later is not None:
                return {"error": later_refusal(family, later)}
            reserved = reserved_trials(rows, q_now, r.reserved_for(q_now)).reserved
            cap = quarter_budget(r, q_now) if prereg is not None else max(quarter_budget(r, q_now) - reserved, 0)
            try:
                quarter = ctx.trials.check_budget(1, cap)
            except TrialBudgetExceeded as exc:
                held = "" if prereg is not None else f" ({reserved} of the quarter held for the pre-registered queue)"
                return {"error": f"{exc}{held}"}
            retired = {e.family for e in r.retired_families}
            exploring = prereg is None and family in retired - _reinstated(ctx, end)
            if exploring:
                used = retired_quarter_trials(rows, quarter, retired)
                if used >= RETIRED_EXPLORATION:
                    return {"error": f"{family} is retired (research.retired_families) and not reinstated: the retired "
                                     f"families share {RETIRED_EXPLORATION} exploration trial a quarter and {quarter} "
                                     f"has used it ({used} retired-family trial(s)); pre-register the configuration "
                                     f"to run it from the queue"}
            link = prereg
            if prereg is None and r.open_budget_capped(quarter):
                # E-5 cap (research.open_budget_preregistered_only): the open budget is not a default pool
                link = rule_preregistration(rows, family, config_hash(config))
                if link is None and not exploring:
                    return {"error": f"E-5 cap: {quarter}'s open budget is spent only on an ad-hoc pre-registration "
                                     f"naming a pre-registered rule (TrialRegistry.preregister(..., rule_ref=)) or on "
                                     f"the retired families' shared exploration trial; {family} "
                                     f"{config_hash(config)} has neither (research.open_budget_preregistered_only)"}
            res = _walk_forward(ctx, SPECIALISTS[family](**overrides), end, 12 * 30, n_trials=ctx.trials.n_trials + 1,
                                holdout=r.holdout_window())
            if res is None:
                return {"error": "no bars in the store for this family's timeframe"}
            row = ctx.trials.record(agent_id=res.agent_id, family=family, config=config,
                                    feature_version=res.feature_version, results=res.metrics, status="evaluated",
                                    rationale=rationale, budget_quarter=quarter, preregistration=link)
        keep = ("n_candidates", "n_folds", "threshold", "all_candidates", "model_filtered", "trades_per_year", "rule_only",
                "gates")
        return {"trial": row["trial"], "registry_total": ctx.trials.n_trials, **{k: res.metrics[k] for k in keep if k in res.metrics}}
    return run


def _reinstated(ctx: JobContext, slot: pd.Timestamp) -> set[str]:
    """Retired families the director's current plan reinstated (attribution after the retirement date); none without
    a fresh plan, so a retired family stays retired when the evidence cannot be read."""
    plan = current_plan(ctx, slot)
    return {s.family for s in plan.evidence if s.retired_id is not None and not s.retired} if plan else set()


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
    capped = quarter_of(slot.tz_convert("UTC").to_pydatetime())
    if r.open_budget_capped(capped):      # E-5: grid variants name no pre-registered rule
        return {"paused": f"label-grid loop off in {capped}: E-5 cap (research.open_budget_preregistered_only)",
                "registry_sync": synced_before}
    rng = random.Random(f"{slot:%Y-%m}")        # reproducible choice of variants for the month
    plan = current_plan(ctx, slot)
    source = f"research plan of {plan.created_utc:%Y-%m-%d}" if plan else f"flat budget {r.trial_budget_per_month} per family (no fresh research plan)"
    lines = [f"# Research loop {slot:%Y-%m}", "", f"Trial budget: {source}.", ""]
    out: dict[str, Any] = {"plan": plan.created_utc.isoformat() if plan else None}
    q_budget = quarter_budget(r)
    # the reservation of the quarter the slot belongs to (a late run near a quarter boundary still plans the slot's
    # quarter, as the director does with its slot)
    slot_quarter = quarter_of(slot.tz_convert("UTC").to_pydatetime())
    h_start, _ = holdout_window(r)
    end = min(slot, h_start)              # the held-out year is never searched
    for family in research_families():
        grid = label_grid(SPECIALISTS[family].default_config, r.label_grid_step)
        rng.shuffle(grid)
        tf = SPECIALISTS[family].timeframe
        history_months = 12 * 30          # everything the store has
        budget = min(plan.grid_budget.get(family, r.trial_budget_per_month), r.trial_budget_per_month) if plan \
            else r.trial_budget_per_month
        rows = []
        skipped: list[str] = []
        stopped = False
        for overrides in grid[:budget]:
            with ctx.trials.locked():           # check, run and record as one step: two writers cannot both pass
                reg_rows = read_rows(ctx.trials.path)
                later = later_preregistration(reg_rows, slot_quarter, family,
                                              config_hash({**SPECIALISTS[family].default_config, **overrides}))
                if later is not None:           # queued for a later quarter: not this quarter's grid variant
                    skipped.append(later_refusal(family, later))
                    continue
                reserved = reserved_trials(reg_rows, slot_quarter, r.reserved_for(slot_quarter)).reserved
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
                       "quarter_budget_spent": stopped, "skipped": skipped}
        lines += [f"## {family} ({len(rows)} of {budget} budgeted trials, registry total {ctx.trials.n_trials})", ""]
        if stopped:
            lines += [f"Stopped: the quarter's trial budget ({q_budget}) is spent, counting the trials held for the "
                      f"pre-registered queue (research.reserved_trials_quarter).", ""]
        lines += [f"Skipped: {x}" for x in skipped] + ([""] if skipped else [])
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
    veto (ADR 0003: fragile when PBO > 0.5 or most paths negative), and its PBO is a lower bound (the selection set is
    every registered trial of the family on that timeframe, screened ones included). A trial whose recorded feature
    version differs from the one the store's bars build now is skipped, not re-run on different inputs. The evidence
    sidecar is local to the scheduler host (registry_sync does not carry it; docs/decisions/0002). Report ->
    state/cpcv_<quarter>.md. One trial's failure does not stop the others; the job fails at the end if any did."""
    from goldbot.research import cpcv
    from goldbot.research.registry import gate_pass_counts, quarter_of
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

    passing = [x for x in rows if timeframe(x) is not None and gate_pass_counts(x)]
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
                       orders=orders, tables=tables, evidence_start=EVIDENCE_START_UTC)   # H-52 quarantine
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


# ---------------------------------------------------------------------------------------------- TradingView cross-feed
TV_RECONCILE_DAYS = 3                 # lookback: late-arriving history bars and a missed run are still caught
TV_DROP_COLUMNS = ["source", "symbol", "year", "month"]      # hive columns Store.read adds; not part of the rows


def _tv_reference(ctx: JobContext, start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DataFrame, str]:
    """The reference M1: Dukascopy, else the first account's broker M1 (bars_1m_broker) that has the window."""
    for table, source in (("bars_1m", "dukascopy"), *(("bars_1m_broker", a.account_id) for a in ctx.accounts)):
        ref = ctx.store.read(table, source=source, start=start, end=end)
        if not ref.empty:
            return ref, source
    return pd.DataFrame(), ""


def tv_reconcile(ctx: JobContext, slot: pd.Timestamp) -> dict[str, Any]:
    """Daily: the pulled TradingView bars (second feed) against the reference M1 over the last TV_RECONCILE_DAYS days.
    A bar more than 2 points off is flagged `dq` and its plots set NaN in `tv_indicators` (a rewrite on the store key,
    so a flagged bar stays NaN); each is one dq_events row (source tradingview). Reporting only: no order path."""
    from goldbot.data.crossfeed import CLOSE_TOL_POINTS, POINT_DEFAULT
    from goldbot.data.quality import DQEvent, events_frame
    from goldbot.data.tv_reconcile import PULL_ORIGINS, apply_dq, compare_closes, reconcile_counts
    end = pd.Timestamp(slot).tz_convert("UTC").floor("min")
    start = end - pd.Timedelta(days=TV_RECONCILE_DAYS)
    ref, ref_source = _tv_reference(ctx, start - pd.Timedelta(hours=8), end)
    out: dict[str, Any] = {"reference": ref_source or None, "flagged": 0, "compared": 0}
    tdir = ctx.store.table_dir("tv_indicators")
    if ref.empty or not tdir.exists():
        out["skipped"] = "no reference M1 bars" if ref.empty else "no TradingView rows"
        return out
    tol = CLOSE_TOL_POINTS * POINT_DEFAULT
    events: list[DQEvent] = []
    for d in sorted(tdir.glob("source=*")):
        source = d.name.removeprefix("source=")
        long = ctx.store.read("tv_indicators", source=source, start=start - pd.Timedelta(hours=8), end=end)
        if long.empty or "origin" not in long.columns:
            continue
        flagged_rows: list[pd.DataFrame] = []
        for tf in sorted(set(long.loc[long["origin"].isin(PULL_ORIGINS), "tf"].astype(str))):
            cmp = compare_closes(long, ref, tf=tf, tolerance=tol, start=start, end=end)
            counts = reconcile_counts(long, ref, tf=tf, tolerance=tol, start=start, end=end)
            out["compared"] += len(cmp)
            out.setdefault("by_source", {})[f"{source}:{tf}"] = {
                "compared": len(cmp), "matched": counts["matched"], "max_close_diff": counts["max_close_diff"]}
            bad = cmp[cmp["dq"]]
            if bad.empty:
                continue
            fixed = apply_dq(long[long["tf"].astype(str) == tf], cmp)
            flagged_rows.append(fixed[fixed["dq"] == "tv_close_diff"])
            for r in bad.itertuples():
                events.append(DQEvent(ts_utc=cast(pd.Timestamp, r.ts_utc), check="tv_close_diff", severity="warning",
                                      detail=f"{source} {tf}: TradingView close {r.tv_close:.2f} vs {ref_source} "
                                             f"{r.ref_close:.2f} ({r.diff:.2f} off, limit {tol:.2f}); plots set NaN"))
        if flagged_rows:
            rows = pd.concat(flagged_rows, ignore_index=True)
            ctx.store.append("tv_indicators", rows.drop(columns=TV_DROP_COLUMNS, errors="ignore"), source=source)
            out["flagged"] += int(rows["bar_time"].nunique())
    if events:
        ctx.store.append("dq_events", events_frame(events), source="tradingview", dedupe=False)
    return out


# ---------------------------------------------------------------------------------------------- broker ledger (GAP-07/08)
def broker_ledger(ctx: JobContext, slot: pd.Timestamp) -> dict[str, Any]:
    """Daily, per enabled account (goldbot/ops/ledger.py): RC-9 "Opening balance + net closed P&L + deposits -
    withdrawals = closing balance" and RC-8 "Every engine position ... in the broker's deals has exactly one closed
    record or is still open". Read-only on the broker; snapshot, balance deals and report -> aux.db. The output holds
    counts only: amounts stay in aux.db."""
    from goldbot.db import Database
    from goldbot.ops.ledger import run_ledger
    if ctx.broker_for is None:
        return {"skipped": "no broker connection configured for the scheduler"}
    db = Database.open(ctx.state_dir, "aux")
    out: dict[str, Any] = {}
    failed: list[str] = []
    for acc in ctx.accounts:
        broker = None
        try:                                  # one unreachable terminal does not stop the other account's ledger
            broker = ctx.broker_for(acc)
            if broker is None:
                out[acc.account_id] = {"skipped": "no read-only broker connection for this account"}
                continue
            out[acc.account_id] = run_ledger(db, broker, acc, ctx.state_dir, slot, ctx.settings.ledger).summary()
        except Exception as exc:
            failed.append(f"{acc.account_id}: {type(exc).__name__}: {exc}")
        finally:
            if broker is not None and hasattr(broker, "shutdown"):
                broker.shutdown()
    if failed:                                # the scheduler records the failure (health: scheduler check)
        raise RuntimeError("broker_ledger failed for " + "; ".join(failed) + f" (done: {sorted(out)})")
    return out


# ---------------------------------------------------------------------------------------------- broker tick record (BACKLOG 79)
def tick_record(ctx: JobContext, slot: pd.Timestamp) -> dict[str, Any]:
    """Daily, per enabled account (goldbot/data/ticks.py): the broker's ticks of every closed server day not yet on
    disk -> state/ticks/<account>/<date>.parquet (copy_ticks, read-only, paged), then retention. Health: ticks:<acc>.
    Runs only while gold is closed (daily break or weekend, `market_quiet`), time-boxed and paced, so the shared
    bridge is never busy in trading hours (trading-safety review); `slot` is not used as the time."""
    import time

    from goldbot.data.ticks import market_quiet, record_ticks
    if ctx.broker_for is None:
        return {"skipped": "no broker connection configured for the scheduler"}
    clock = ctx.clock or (lambda: pd.Timestamp.now("UTC"))
    if not market_quiet(clock()):
        return {"skipped": f"market open at {clock().isoformat()}: runs only in the daily break or at the weekend"}
    out: dict[str, Any] = {}
    failed: list[str] = []
    for acc in ctx.accounts:
        broker = None
        try:                                  # one unreachable terminal does not stop the other account's record
            broker = ctx.broker_for(acc)
            if broker is None:
                out[acc.account_id] = {"skipped": "no read-only broker connection for this account"}
                continue
            out[acc.account_id] = record_ticks(broker, ctx.state_dir, acc.account_id, acc.symbol, acc.server_tz,
                                               ctx.settings.ticks, clock=clock, sleep=ctx.sleep or time.sleep)
        except Exception as exc:
            failed.append(f"{acc.account_id}: {type(exc).__name__}: {exc}")
        finally:
            if broker is not None and hasattr(broker, "shutdown"):
                broker.shutdown()
    if failed:                                # the scheduler records the failure (health: scheduler check)
        raise RuntimeError("tick_record failed for " + "; ".join(failed) + f" (done: {sorted(out)})")
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
    try:
        return backup_mod.backup_job(paths, get_secret, host=b.host, timeout_s=b.timeout_s)
    finally:   # also after a failed backup: a full bucket is a likely cause (health: object_storage, BACKLOG 67)
        try:
            from goldbot.ops import storage_size
            storage_size.record_size(ctx.state_dir, get_secret)
        except Exception as exc:   # the probe must never replace the backup's own result or error
            log.warning("object storage probe failed: %s", type(exc).__name__)


def restore_drill(ctx: JobContext, slot: pd.Timestamp) -> dict[str, Any]:
    """Weekly: restore the latest snapshot to a temp dir, verify checksums, integrity_check, schema versions, row
    counts and model artefacts, `restic check` a data subset; state/restore_drill_last.json (health: restore_drill)."""
    from goldbot.ops import backup as backup_mod
    paths, get_secret = _backup_args(ctx)
    b = ctx.settings.backup
    return backup_mod.drill_job(paths, get_secret, host=b.host, check_subset=b.check_subset, timeout_s=b.timeout_s)


# ---------------------------------------------------------------------------------------------- wiring
def _limits_job(ctx: JobContext, slot: pd.Timestamp) -> dict[str, Any]:
    from goldbot.ops.limits_job import limits_job
    return limits_job(ctx, slot)


def daily_review(ctx: JobContext, slot: pd.Timestamp) -> dict[str, Any]:
    """The daily learning-ops review of the opportunity day before `slot` (research/daily_review.py) over the enabled
    accounts with exploration on; issue text goes to GitHub with the keyring's github-token, else to state."""
    from goldbot.ops.report_days import day_before
    from goldbot.research.daily_review import run_daily_review, sink_for, summary, trials_left
    hosts = {a.account_id: a.broker for a in ctx.accounts if a.exploration.enabled}
    if not hosts:
        return {"skipped": "no enabled account has exploration on"}
    review = run_daily_review(settings=ctx.settings, state_dir=ctx.state_dir, store=ctx.store, hosts=hosts,
                              day=day_before(slot), now=slot, sink=sink_for(ctx.settings, ctx.get_secret),
                              trials_left=trials_left(ctx.settings, ctx.trials, slot))
    return summary(review)


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
    "calendar_archive_sunday": calendar_archive,  # the new Forex Factory week before the weekly open (review M2)
    "agents_presession": agents_presession,
    "recalibrate": recalibrate,
    "drift_watch": drift_watch,
    "gap_watch": gap_watch,
    "feed_reconcile": feed_reconcile,             # D12 daily reconciliation against the broker's M1
    "tv_reconcile": tv_reconcile,                 # TradingView bars (second feed) vs the reference M1; dq flag + NaN plots
    "broker_ledger": broker_ledger,               # GAP-07/08: balance snapshot, positions and balance vs the broker
    "backup": backup,                             # encrypted off-host backup (ops/backup.py)
    "restore_drill": restore_drill,               # weekly restore + verification of the latest backup
    "cpcv_quarterly": cpcv_quarterly,             # M16 combinatorial purged CV + PBO of gate-passing trials
    "attribution": attribution,                   # BACKLOG 12: daily attribution for the staff agents (reporting only)
    "tick_record": tick_record,                   # BACKLOG 79: broker ticks per server day for research (H-A)
    "tick_record_winter": tick_record,            # ... the same job in the winter daily break (one slot skips)
    "limits_job": _limits_job,                    # ADR 0031: adaptive limits for the next risk day (ops/limits_job.py)
    "daily_review": daily_review,                 # learning-ops review before the owner report (research/daily_review.py)
}


def build_scheduler(ctx: JobContext, clock: Callable[[], pd.Timestamp] | None = None) -> Scheduler:
    sch = Scheduler(ctx.state_dir / "scheduler.json", clock=clock)
    cfg = ctx.settings.scheduler
    for name, fn in JOBS.items():
        s = getattr(cfg, name)
        sch.add(name, Schedule(kind=s.kind, at=s.at, weekdays=tuple(s.weekdays), weekday=s.weekday, day=s.day,
                               months=tuple(s.months), max_late_hours=s.max_late_hours), functools.partial(fn, ctx))
    return sch
