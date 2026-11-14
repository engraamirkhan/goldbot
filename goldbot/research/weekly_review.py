"""The weekly review (architect plan 2026-11-13 item 2, the review loop). Scheduler job `weekly_review` on Saturday
after `research_director`, over the seven days before its slot. It reads what is already recorded and writes one ranked
action list (aux.db `review_actions`, goldbot/research/review_actions.py):

* per family, net result against its matched control: the strategy ranking's figures (ADR 0053), handed in by the job
  as a RankingView over each version's life and one over the week. A family with no paired control result reads
  "unpaired", never an edge; with enough fills of its own that is a backlog suggestion to pair one. A recorded or
  interim edge whose interval lies below zero queues a `below_control` review for the research director; an interval
  spanning zero is "level" and queues nothing. The ranking stays descriptive: nothing here reads, promotes or stops by
  it (ADR 0053 6f), and this module never imports it;
* reads due at their registered checkpoint (config/arm_reads.yaml read sizes and session-day minimums, every unread
  unit of a row in the ranking's `read_units`): "one read, at the 300th admissible family fill on >= 20 session days"
  (H-81 section 4), so a read is due only once both counts are met; a unit already read, or retired unread before its
  read fell due (its stop, A5), has no progress and no read is due. An unreadable arm_reads.yaml queues no read and one
  `arm_reads_unreadable` action (fail closed). The evaluator records the read, the review only says it is due;
* loss attribution: the attribution job's report (state/attribution.json), cells signed negative after the row bar
  (`verdict_adj`, ADR 0022), at most `attribution_max_age_days` old;
* cost drift: the week's measured round trip per broker and session (the daily reviews' cost cells) above the cost of
  record's 90% interval (config/cost_of_record.yaml), with at least `min_cost_fills` fills; cheaper is never credited;
* exit diagnostics: per family, the stop exits that had reached `exit_mfe_r` R first (aux.db exploration_trades
  `mfe_r` / `mae_r` in the JSON body; rows without them are counted as not recorded, never guessed);
* the Monday roll-up's variant proposals (state/aux/daily_review/weekly-<sunday>.json), which nothing read before:
  each joins the list once, and the roll-up is marked read (`daily_review.mark_weekly_consumed`, by "weekly_review",
  for the feedback-loop health check) once the week's actions are stored; an unreadable roll-up is never marked.

"The only automatic actions are stops already registered in config/arm_stops.yaml (tighten only)": a `breached` arm in
the week's daily reviews (its registered stop, computed by the daily review) that exploration_arm_status does not hold
yet is retired there through daily_review.retire (INSERT OR IGNORE: an existing row and its day stand). Nothing here
deletes, re-opens, loosens or edits a stop, a threshold, a budget, a gate or an order; there is no order path.

Feedback: the research director consumes the unconsumed actions once as queue input (`review_actions.consume`); the
backlog kinds go to one GitHub issue labelled `review` a week through the daily review's scrubbed issue path (outbox
without a token); the Sunday digest counts the week's actions ("Changed this week").
"""
from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import pandas as pd
import yaml
from pydantic import Field

from goldbot.api.schema import ARM_READS_UNREADABLE
from goldbot.base import FrozenRecord, UtcTimestamp, load_record
from goldbot.data.timeutil import epoch_ns
from goldbot.db.connection import Database
from goldbot.research import review_actions as ra
from goldbot.research.arms import CostOfRecord
from goldbot.research.daily_review import (
    ALL,
    SESSIONS,
    STOP_EXITS,
    DailyReview,
    WeeklyRollup,
    _post,
    load_review,
    mark_weekly_consumed,
    retire,
    retired_arms,
    review_root,
    scrub,
)

if TYPE_CHECKING:
    from goldbot.api.schema import RankingView, RankRow
    from goldbot.config import Settings, WeeklyReviewSettings
    from goldbot.ops.github_issues import IssueSink

log = logging.getLogger("goldbot.weekly_review")

COST_OF_RECORD = Path(__file__).resolve().parents[2] / "config" / "cost_of_record.yaml"
STOP_RULE = "weekly review: registered stop breached in the daily review (config/arm_stops.yaml)"
WINDOW_DAYS = 7
PROPOSAL_DAYS = 14                  # roll-ups this recent are read; an id already stored is never added again
LOSS_BREAKDOWNS = ("family", "timeframe", "session", "exit", "side", "regime")
VsControl = Literal["below", "level", "above", "unpaired"]


class FamilyRead(FrozenRecord):
    """One family's week against its control: `edge_r` is None whenever the reading is "unpaired"."""
    family: str
    n: int
    net_r_per_trade: float | None
    week_n: int = 0
    week_net_r_per_trade: float | None = None
    vs_control: VsControl
    edge_r: float | None = None
    edge_ci: tuple[float, float] | None = None
    edge_source: str | None = None


class CostDrift(FrozenRecord):
    broker: str
    session: str
    fills: int
    week_usd: float
    of_record_usd: float
    of_record_hi_usd: float


class ExitRead(FrozenRecord):
    family: str
    closed: int
    recorded: int                   # closed trades carrying mfe_r
    stop_exits: int                 # ... of which stop exits
    stops_after_mfe: int            # ... that had reached exit_mfe_r first
    median_mae_r: float | None = None


class WeeklyReview(FrozenRecord):
    week: str
    generated_utc: UtcTimestamp
    written: bool                   # False: the week was written by an earlier run, this is its stored set
    actions: list[ra.ReviewAction] = Field(default_factory=list)
    families: list[FamilyRead] = Field(default_factory=list)
    cost_drift: dict[str, CostDrift] = Field(default_factory=dict)
    exits: dict[str, ExitRead] = Field(default_factory=dict)
    stops_applied: list[str] = Field(default_factory=list)      # "broker arm" recorded now
    posted: str | None = None
    unreadable: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------------------------- the ranking's figures
def _reading(row: RankRow) -> FamilyRead:
    f = row.figures
    vs: VsControl = "unpaired"
    if f.edge_vs_control_r is not None:
        ci = f.edge_ci
        vs = ("level" if ci is None or ci[0] <= 0 <= ci[1] else "below" if ci[1] < 0 else "above")
    return FamilyRead(family=row.family, n=f.n, net_r_per_trade=f.net_r_per_trade, vs_control=vs,
                      edge_r=f.edge_vs_control_r if vs != "unpaired" else None,
                      edge_ci=f.edge_ci if vs != "unpaired" else None,
                      edge_source=f.edge_source if vs != "unpaired" else None)


def family_reads(week_view: RankingView | None, life_view: RankingView | None) -> list[FamilyRead]:
    """The exploration families of the life view (ranked and too few), with the week's n and net beside them."""
    if life_view is None:
        return []
    week = {r.key: r.figures for r in week_view.rows} if week_view is not None else {}
    out = []
    for row in life_view.rows:
        if row.kind != "exploration":
            continue
        w = week.get(row.key)
        out.append(_reading(row).model_copy(update={"week_n": w.n if w else 0,
                                                    "week_net_r_per_trade": w.net_r_per_trade if w else None}))
    return out


def ranking_actions(reads: Sequence[FamilyRead], life_view: RankingView | None, week: str
                    ) -> list[ra.ReviewAction]:
    out: list[ra.ReviewAction] = []
    min_fills = life_view.min_fills if life_view is not None else 0
    for r in reads:
        if r.vs_control == "below" and r.edge_r is not None and r.edge_ci is not None:
            out.append(ra.ReviewAction(
                action_id=ra.action_id("below_control", week, r.family), week=week, kind="below_control",
                subject=r.family, source="ranking", score=-r.edge_r,
                title=f"{r.family}: below its control ({r.edge_source}), review before more trials",
                detail=f"net R/trade {r.edge_r:+.3f} vs control, interval [{r.edge_ci[0]:+.3f}, {r.edge_ci[1]:+.3f}] "
                       f"over {r.n} fills; descriptive until the read (ADR 0053), stops only by its registered rule"))
        elif r.vs_control == "unpaired" and r.n >= min_fills:
            out.append(ra.ReviewAction(
                action_id=ra.action_id("unpaired", week, r.family), week=week, kind="unpaired", subject=r.family,
                source="ranking", score=float(r.n),
                title=f"{r.family}: unpaired, no matched control result to compare with",
                detail=f"{r.n} fills of its own and no paired control result (ADR 0051 pairing): pair a control or "
                       "record why none applies; its result is not an edge until then"))
    if life_view is not None and ARM_READS_UNREADABLE in life_view.unreadable:
        out.append(ra.ReviewAction(
            action_id=ra.action_id("arm_reads_unreadable", week), week=week, kind="arm_reads_unreadable",
            subject="config/arm_reads.yaml", source="ranking", score=0.0,
            title="config/arm_reads.yaml unreadable: no read can be queued as due",
            detail="the read sizes, pooled units and session-day minimums could not be read (fail closed: every arm "
                   "would fall back to its own unit at 300 with no day minimum); fix the file, nothing is read meanwhile"))
    elif life_view is not None:
        queued: set[str] = set()
        for row in life_view.rows:
            if row.kind != "exploration":
                continue
            for p in row.read_units:                    # every unread unit: one short of its days hides no other
                if p.n_counted < p.n_read_at or p.n_days < p.min_days or p.unit in queued:
                    continue
                queued.add(p.unit)
                out.append(ra.ReviewAction(
                    action_id=ra.action_id("read_due", p.unit, str(p.n_read_at)), week=week, kind="read_due",
                    subject=p.unit, source="ranking", score=float(p.n_counted - p.n_read_at),
                    title=f"{row.family}: read due at its registered size ({p.n_read_at})",
                    detail=f"{p.n_counted} counted fills of {p.n_read_at}"
                           + (f" on {p.n_days} session days (minimum {p.min_days})" if p.min_days else "")
                           + ": the evaluator records the pre-registered "
                           "read (config/arm_reads.yaml); nothing is decided here"))
    return out


# ---------------------------------------------------------------------------------------------- attribution
def loss_actions(report: dict[str, Any] | None, now: pd.Timestamp, max_age_days: float, week: str
                 ) -> tuple[list[ra.ReviewAction], str | None]:
    """Cells signed negative after the row bar (ADR 0022) in a report at most `max_age_days` old."""
    if report is None:
        return [], "attribution report missing"
    try:
        as_of = pd.Timestamp(report["as_of"])
        as_of = as_of.tz_localize("UTC") if as_of.tzinfo is None else as_of.tz_convert("UTC")
    except (KeyError, ValueError, TypeError):
        return [], "attribution report unreadable"
    if as_of > now or now - as_of > pd.Timedelta(days=max_age_days):
        return [], f"attribution report of {as_of:%Y-%m-%d} not used (older than {max_age_days:g} days)"
    out = []
    for name in LOSS_BREAKDOWNS:
        for key, c in sorted((report.get("breakdowns") or {}).get(name, {}).items()):
            if not isinstance(c, dict) or c.get("verdict_adj") != "negative" or c.get("mean_r_net") is None:
                continue
            t = c.get("t_stat")
            out.append(ra.ReviewAction(
                action_id=ra.action_id("loss_driver", week, name, str(key)), week=week, kind="loss_driver",
                subject=f"{name}={key}", source="attribution", score=-float(t) if t is not None else 0.0,
                title=f"Loss driver: {name} {key}",
                detail=f"net {float(c['mean_r_net']):+.3f} R/trade over {int(c.get('n') or 0)} trades, negative after "
                       "the row bar (state/attribution.json)"))
    return out, None


# ---------------------------------------------------------------------------------------------- cost drift
def load_cost_of_record(path: Path = COST_OF_RECORD) -> CostOfRecord | None:
    try:
        return load_record(CostOfRecord, yaml.safe_load(path.read_text(encoding="utf-8")), source=path.name)
    except (OSError, ValueError, yaml.YAMLError):
        log.warning("weekly review: cost of record %s unreadable", path.name)
        return None


def cost_drift(reviews: Sequence[DailyReview], record: CostOfRecord | None, min_fills: int) -> dict[str, CostDrift]:
    """The week's fill-weighted round trip per (broker, session) above the cost of record's 90% upper bound."""
    if record is None:
        return {}
    sums: dict[tuple[str, str], tuple[float, int]] = {}
    for r in reviews:
        for c in r.costs:
            if c.host != ALL or c.session not in SESSIONS or c.round_trip_usd is None or c.fills <= 0:
                continue
            s, n = sums.get((c.broker, c.session), (0.0, 0))
            sums[(c.broker, c.session)] = (s + c.round_trip_usd * c.fills, n + c.fills)
    out = {}
    for (broker, session), (s, n) in sorted(sums.items()):
        cell = record.cells.get(broker, {}).get(session)
        if cell is None or n < min_fills:
            continue
        mean = s / n
        if mean > cell.round_trip_ci90[1]:
            out[f"{broker} {session}"] = CostDrift(broker=broker, session=session, fills=n, week_usd=round(mean, 4),
                                                   of_record_usd=cell.round_trip_usd,
                                                   of_record_hi_usd=cell.round_trip_ci90[1])
    return out


def cost_actions(drift: dict[str, CostDrift], version: str, week: str) -> list[ra.ReviewAction]:
    return [ra.ReviewAction(
        action_id=ra.action_id("cost_drift", week, key), week=week, kind="cost_drift", subject=key,
        source="cost_of_record", score=d.week_usd / d.of_record_usd if d.of_record_usd else d.week_usd,
        title=f"Cost drift on {key}: ${d.week_usd:.3f}/oz this week against ${d.of_record_usd:.3f} of record",
        detail=f"{d.fills} fills, above the 90% upper bound ${d.of_record_hi_usd:.3f} of {version}: re-measure the cost "
               "of record before a screen charges it") for key, d in drift.items()]


# ---------------------------------------------------------------------------------------------- exits
def _num(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if np.isfinite(v) else None


def exit_reads(db: Database, lo: pd.Timestamp, hi: pd.Timestamp, mfe_r: float) -> dict[str, ExitRead]:
    """Per family, the closed exploration trades exited in [lo, hi) and their MFE/MAE where recorded."""
    lo_ns, hi_ns = (int(x) for x in epoch_ns(pd.DatetimeIndex([lo, hi])))
    with db.connection() as conn:
        rows = conn.execute("SELECT body FROM exploration_trades WHERE closed = 1 AND exit_ns >= ? AND exit_ns < ?",
                            (lo_ns, hi_ns)).fetchall()
    per: dict[str, list[tuple[str, float | None, float | None]]] = {}
    for (b,) in rows:
        try:
            body = json.loads(b)
        except ValueError:
            continue
        per.setdefault(str(body.get("family") or "?"), []).append(
            (str(body.get("exit_reason") or ""), _num(body.get("mfe_r")), _num(body.get("mae_r"))))
    out = {}
    for fam, xs in sorted(per.items()):
        rec = [x for x in xs if x[1] is not None]
        stops = [x for x in rec if x[0] in STOP_EXITS]
        maes = [x[2] for x in rec if x[2] is not None]
        out[fam] = ExitRead(family=fam, closed=len(xs), recorded=len(rec), stop_exits=len(stops),
                            stops_after_mfe=sum(1 for x in stops if x[1] is not None and x[1] >= mfe_r),
                            median_mae_r=round(float(np.median(maes)), 3) if maes else None)
    return out


def exit_actions(reads: dict[str, ExitRead], cfg: WeeklyReviewSettings, week: str) -> list[ra.ReviewAction]:
    out = []
    for fam, e in reads.items():
        if e.recorded < cfg.exit_min_trades or not e.stop_exits:
            continue
        share = e.stops_after_mfe / e.stop_exits
        if share >= cfg.exit_share:
            out.append(ra.ReviewAction(
                action_id=ra.action_id("exit_diagnostic", week, fam), week=week, kind="exit_diagnostic", subject=fam,
                source="exits", score=share,
                title=f"{fam}: {share:.0%} of stop exits had reached +{cfg.exit_mfe_r:g} R first",
                detail=f"{e.stops_after_mfe} of {e.stop_exits} stop exits ({e.recorded} of {e.closed} closed trades "
                       "carry MFE/MAE): an exit variant (break-even or trail) costs one registry trial if "
                       "pre-registered"))
    return out


# ---------------------------------------------------------------------------------------------- stops and proposals
def stop_actions(db: Database, reviews: Sequence[DailyReview], week: str) -> list[ra.ReviewAction]:
    """Arms the week's daily reviews found `breached` at their registered stop and not yet retired on that broker."""
    breached: dict[tuple[str, str], str] = {}
    for r in reviews:
        for broker, arms in r.arm_health.items():
            for arm, h in arms.items():
                if h == "breached":
                    breached.setdefault((broker, arm), r.day)
    out = []
    retired = {b: retired_arms(db, b) for b in {b for b, _ in breached}}
    for (broker, arm), day in sorted(breached.items()):
        if arm in retired[broker]:
            continue
        out.append(ra.ReviewAction(
            action_id=ra.action_id("stop", broker, arm), week=week, kind="stop", subject=arm, source="daily_review",
            automatic=True, broker=broker, rule=STOP_RULE,
            title="Stopped at its registered rule (new entries refused; open positions keep every exit)",
            detail=f"breached in the daily review of {day} and not recorded on the broker yet"))
    return out


def proposal_actions(state_dir: Path, now: pd.Timestamp, week: str
                     ) -> tuple[list[ra.ReviewAction], list[str], list[str]]:
    """(the roll-ups' proposals as actions, the roll-ups read, the unreadable ones). An unreadable roll-up is never
    marked read."""
    out: list[ra.ReviewAction] = []
    bad: list[str] = []
    read: list[str] = []
    root = review_root(state_dir)
    if not root.exists():
        return out, read, bad
    for p in sorted(root.glob("weekly-*.json")):
        day = p.stem.removeprefix("weekly-")
        try:
            ending = pd.Timestamp(day, tz="UTC")
        except ValueError:
            continue
        if not now - pd.Timedelta(days=PROPOSAL_DAYS) <= ending < now:
            continue
        try:
            roll = load_record(WeeklyRollup, json.loads(p.read_text(encoding="utf-8")), source=p.name)
        except (OSError, ValueError):
            bad.append(p.name)
            continue
        read.append(p.name)
        for i, v in enumerate(roll.proposals):
            out.append(ra.ReviewAction(
                action_id=ra.action_id("variant_proposal", roll.week_ending, v.title), week=week,
                kind="variant_proposal", subject=v.title, source="rollup", score=-float(i),
                title=v.title, detail=f"{v.rationale} (roll-up of the week ending {roll.week_ending}; {v.cost})"))
    return out, read, bad


def rank(actions: Sequence[ra.ReviewAction], limit: int) -> list[ra.ReviewAction]:
    """Kinds in KIND_ORDER, larger score first, then subject; every stop kept, the rest capped at `limit`."""
    ordered = sorted(actions, key=lambda a: (ra.KIND_ORDER.index(a.kind), -a.score, a.subject))
    stops = [a for a in ordered if a.automatic]
    return [*stops, *[a for a in ordered if not a.automatic][:limit]]


# ---------------------------------------------------------------------------------------------- the run
def _issue(week: str, actions: Sequence[ra.ReviewAction]) -> tuple[str, str] | None:
    items = [a for a in actions if a.kind in ra.BACKLOG_KINDS]
    if not items:
        return None
    lines = [f"Weekly review of the week to {week}: backlog suggestions (none is applied automatically).", ""]
    lines += [f"- **{a.title}**: {a.detail}" for a in items]
    return f"Weekly review {week}: {len(items)} backlog suggestion{'s' if len(items) > 1 else ''}", scrub("\n".join(
        lines))


def _daily_reviews(state_dir: Path, now: pd.Timestamp) -> list[DailyReview]:
    days = [f"{now.normalize() - pd.Timedelta(days=d):%Y-%m-%d}" for d in range(WINDOW_DAYS, 0, -1)]
    return [r for d in days if (r := load_review(state_dir, d)) is not None and not r.dry_run]


def run_weekly_review(*, settings: Settings, state_dir: str | Path, week_view: RankingView | None,
                      life_view: RankingView | None, now: pd.Timestamp, sink: IssueSink | None = None,
                      cost_of_record: Path = COST_OF_RECORD) -> WeeklyReview:
    """Review the seven days before `now` and write the week's ranked action list (first run of the week stands)."""
    cfg = settings.weekly_review
    sd = Path(state_dir)
    now = pd.Timestamp(now).tz_convert("UTC")
    week = f"{now:%Y-%m-%d}"
    lo = now - pd.Timedelta(days=WINDOW_DAYS)
    db = Database.open(sd, "aux")
    unreadable: list[str] = []
    if life_view is None:
        unreadable.append("ranking")
    reviews = _daily_reviews(sd, now)
    reads = family_reads(week_view, life_view)
    from goldbot.research.attribution import load_report
    try:
        report = load_report(sd)
    except (OSError, ValueError):
        report = None
    losses, note = loss_actions(report, now, cfg.attribution_max_age_days, week)
    if note is not None:
        unreadable.append(note)
    record = load_cost_of_record(cost_of_record)
    drift = cost_drift(reviews, record, cfg.min_cost_fills)
    try:
        exits = exit_reads(db, lo, now, cfg.exit_mfe_r)
    except sqlite3.Error as exc:
        exits = {}
        unreadable.append(f"exploration_trades ({type(exc).__name__})")
    proposals, rollups, bad = proposal_actions(sd, now, week)
    unreadable += bad
    found = [*stop_actions(db, reviews, week), *ranking_actions(reads, life_view, week), *losses,
             *cost_actions(drift, record.version if record is not None else "?", week),
             *exit_actions(exits, cfg, week), *proposals]
    stored, written = ra.write_week(db, week, rank(found, cfg.max_actions), now)
    for name in rollups:        # read once their proposals are stored (the loop health check, R140); first mark stands
        mark_weekly_consumed(sd, name, by="weekly_review", now=now)
    applied = [f"{a.broker} {a.subject}" for a in stored
               if a.automatic and a.kind == "stop" and a.broker is not None
               and retire(db, broker=a.broker, arm_id=a.subject, day=week, now=now, rule=a.rule or STOP_RULE)]
    issue = _issue(week, stored)
    posted = _post(sd, week, "review", issue[1], sink, title=issue[0], label=cfg.issue_label) if issue else None
    return WeeklyReview(week=week, generated_utc=now, written=written, actions=stored, families=reads,
                        cost_drift=drift, exits=exits, stops_applied=applied, posted=posted, unreadable=unreadable)


def summary(r: WeeklyReview) -> dict[str, Any]:
    """The job's output line: counts only (no arm id, no figure)."""
    kinds: dict[str, int] = {}
    for a in r.actions:
        kinds[a.kind] = kinds.get(a.kind, 0) + 1
    return {"week": r.week, "written": r.written, "actions": len(r.actions), "kinds": kinds,
            "stops_applied": len(r.stops_applied), "posted": r.posted, "unreadable": r.unreadable}
