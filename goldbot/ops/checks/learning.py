"""Learning liveness and model influence, judged by OUTPUT, never by a job's completion (S5, plan 2026-11-25 W1-C).

Owner red line, "learning must never be silently off" (memory learning-never-silently-off): on 11-13 the learner was
switched off and health stayed green for twelve days; on 11-25 seven learning tables held 0 rows and drift_watch and
model_watch watched 0 models, all reported ok. These checks read what the learners WROTE and what the watchers WATCH.

  learning_liveness  one line per learner (LEARNERS: an aux.db output table and the rows that count as output) and
                     per watcher (WATCHERS: drift.json, model_watch.json):
                     * a learner switched on with 0 output rows, or none within its cadence + slack, WARNs; once that
                       has lasted FAIL_AFTER_H (48 h: two daily cadences) it FAILs, and the alert loop tells the owner
                       on Telegram (health_watch, alert_confirm_polls). The clock of a learner that never wrote starts
                       at LIVENESS_FROM (the release that ships this check), so today's dead tables WARN first and the
                       team has 48 h to revive each one or delete it with a reason, as the plan asks;
                     * a learner switched off in settings WARNs with the flag and its reason, never FAILs: off is a
                       recorded decision, but it is never silent;
                     * output computed but not applied (the nightly Thompson weights under `allocator: equal`) WARNs:
                       influence is defined on applied output, so computed-not-applied counts as dead;
                     * a watcher watching 0 subjects while self_retrain holds a shadow or active model WARNs, then
                       FAILs after FAIL_AFTER_H; a watcher with nothing to watch is ok; a report older than two
                       cadences WARNs.
  model_influence    the share of the last INFLUENCE_WINDOW_H of exploration candidates a model scored (meta_p,
                     model_version or bandit_version set) and changed (a block reason a model gave, MODEL_BLOCK_PREFIXES).
                     WARN at 0 % scored with candidates in the window; never FAIL: a statistical gate that lets no
                     model through is honest, and a FAIL would push to loosen it (statistical gates are not limits).

Reads aux.db read-only (`mode=ro`) and the two watcher files. No order path, no write.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.ops.checks._base import Check, Status, _age, _read_json, _worst
from goldbot.ops.checks.registry import BLOCK_TAIL, register_check

if TYPE_CHECKING:
    from goldbot.ops.health import HealthContext

LIVENESS_FROM = pd.Timestamp("2026-11-26 22:00", tz="UTC")   # release train 1 (plan W1-C): the grace clock's start
FAIL_AFTER_H = 48.0                # a WARN that has held this long FAILs (SRE plan item 1: "FAIL after 48 h of WARN")
INFLUENCE_WINDOW_H = 24.0          # SRE plan item 1: "model-influence % is 0 over 24 h"
MODEL_BLOCK_PREFIXES = ("model_", "meta_")   # a candidate's block reason that a model, not a rule or a cap, gave
WATCH_STATUSES = ("active", "shadow")        # self_retrain models every watcher must cover (plan: "every shadow and
                                             # active model")


class Learner(FrozenRecord):
    """One learner as its OUTPUT shows it: the aux.db table it writes, the stamp column and the rows that count."""

    name: str
    table: str
    ts_col: str                          # an epoch-ns column: when the row was written or decided
    where: str = ""                      # a constant SQL filter of the rows that count as output (never user input)
    job: str | None = None               # the scheduler job that writes it: its kind and max_late_hours set the cadence
    cadence_h: float = 24.0              # without a job (or a learner slower than its job, H-62 retrains weekly)
    slack_h: float = 6.0
    switch: str | None = None            # dotted Settings path of the bool that turns it on; None: always on
    applied: tuple[str, str] | None = None   # (dotted Settings path, value) under which its output reaches a decision
    why: str                             # what it learns and what an empty table means: the reason in the WARN


LEARNERS: tuple[Learner, ...] = (
    Learner(name="self_retrain", table="self_retrain_runs", ts_col="run_ns", where="n_with_features > 0",
            job="self_retrain", switch="research.self_retrain.enabled",
            why="weekly per-arm meta-label filter (sr-v2): a run that trained on rows with features"),
    Learner(name="exploration_weights", table="exploration_broker_weights", ts_col="run_ns", job="daily_review",
            applied=("research.exploration.allocator", "thompson"),
            why="nightly Thompson weights per arm (H-61)"),
    Learner(name="meta_labeller", table="exploration_models", ts_col="recorded_ns", cadence_h=168.0,
            switch="research.exploration.meta_labeller",
            why="H-62 meta-labeller versions (retired 2026-11-13: 0 model rows, no candidate trainable)"),
    Learner(name="arm_priors", table="exploration_priors", ts_col="recorded_ns", cadence_h=168.0,
            why="H-60 per-arm history priors: no job writes them (exploration_data.write_prior has no caller)"),
    Learner(name="har_days", table="har_days", ts_col="available_ns", where="status = 'ok'", job="har_days",
            switch="research.har_abstention.enabled",
            why="N-3 HAR range forecast (fit rows; `off` rows are not output)"),
    Learner(name="long_tf_decisions", table="long_tf_decisions", ts_col="updated_ns",
            why="engine 1d/1w decisions (WS-4): the long timeframes decide nothing while their arms ship dark"),
    Learner(name="arm_lifecycle_verdicts", table="arm_lifecycle_verdicts", ts_col="recorded_ns", job="arm_lifecycle",
            why="judged reads of demo arms (build 2)"),
    Learner(name="swap_reconcile", table="swap_reconciliations", ts_col="exit_ns", cadence_h=168.0,
            why="broker swap of weekend holds vs the projection: the swap cost of record stays unmeasured"),
    Learner(name="cost_cells", table="exploration_cost_cells", ts_col="fitted_ns", job="daily_review",
            why="nightly slippage per broker x session (the measured cost the replay labels use)"),
)

WATCHERS: tuple[tuple[str, str, str], ...] = (
    ("drift_watch", "drift.json", "drift_watch"),           # (name, state file, scheduler job)
    ("model_watch", "model_watch.json", "model_watch"),
)


class LearnerRead(FrozenRecord):
    """What one learner's table holds: output rows and the newest stamp (None: no row or no table)."""

    name: str
    rows: int | None = None              # None: the table cannot be read (`error`)
    last: UtcTimestamp | None = None
    error: str | None = None


class Verdict(FrozenRecord):
    name: str
    status: Status
    reason: str


class Influence(FrozenRecord):
    """K2: candidates in a window, those a model scored and those a model changed (plan §1b)."""

    n: int
    scored: int
    changed: int

    @property
    def scored_pct(self) -> float:
        return 100.0 * self.scored / self.n if self.n else 0.0

    @property
    def changed_pct(self) -> float:
        return 100.0 * self.changed / self.n if self.n else 0.0


# ---------------------------------------------------------------------------------------------- reads
def setting(settings: Any, path: str) -> Any:
    """The value at a dotted Settings path; None when any part is missing (an older settings file)."""
    cur = settings
    for part in path.split("."):
        cur = getattr(cur, part, None)
        if cur is None:
            return None
    return cur


def cadence(spec: Learner, settings: Any) -> tuple[float, float]:
    """(cadence, slack) in hours: the job's kind (daily 24, weekly 168, monthly 744) and max_late_hours, or the
    spec's own when it has no job or learns slower than its job runs."""
    sch = setting(settings, f"scheduler.{spec.job}") if spec.job else None
    if sch is None:
        return spec.cadence_h, spec.slack_h
    kind_h = {"daily": 24.0, "weekly": 168.0, "monthly": 744.0}.get(str(sch.kind), spec.cadence_h)
    return max(kind_h, spec.cadence_h), float(sch.max_late_hours)


def _has_table(con: sqlite3.Connection, table: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone() is not None


def read_learner(con: sqlite3.Connection, spec: Learner) -> LearnerRead:
    """Output rows and the newest stamp of one learner (table, column and filter are module constants)."""
    try:
        if not _has_table(con, spec.table):
            return LearnerRead(name=spec.name, rows=0)
        where = f" WHERE {spec.where}" if spec.where else ""
        n, last = con.execute(f"SELECT COUNT(*), MAX({spec.ts_col}) FROM {spec.table}{where}").fetchone()
    except sqlite3.Error as exc:
        return LearnerRead(name=spec.name, error=type(exc).__name__)
    return LearnerRead(name=spec.name, rows=int(n or 0),
                       last=pd.Timestamp(int(last), unit="ns", tz="UTC") if last is not None else None)


def models_to_watch(con: sqlite3.Connection) -> tuple[int, pd.Timestamp | None]:
    """(count, oldest activation) of the self_retrain models every watcher must cover (WATCH_STATUSES)."""
    if not _has_table(con, "self_retrain_models"):
        return 0, None
    q = ", ".join("?" for _ in WATCH_STATUSES)
    n, first = con.execute(f"SELECT COUNT(*), MIN(activation_ns) FROM self_retrain_models WHERE status IN ({q})",
                           WATCH_STATUSES).fetchone()
    return int(n or 0), (pd.Timestamp(int(first), unit="ns", tz="UTC") if first is not None else None)


def influence(con: sqlite3.Connection, *, since: pd.Timestamp, until: pd.Timestamp) -> Influence:
    """K2 over exploration candidates decided in [since, until): scored = a model's number travelled with the
    decision (meta_p, model_version or bandit_version), changed = a model blocked it (MODEL_BLOCK_PREFIXES)."""
    if not _has_table(con, "exploration_obs"):
        return Influence(n=0, scored=0, changed=0)
    like = " OR ".join("blocked_reason LIKE ?" for _ in MODEL_BLOCK_PREFIXES)
    row = con.execute(
        "SELECT COUNT(*), "
        "SUM(CASE WHEN meta_p IS NOT NULL OR model_version IS NOT NULL OR bandit_version IS NOT NULL THEN 1 ELSE 0 END), "
        f"SUM(CASE WHEN {like} THEN 1 ELSE 0 END) "
        "FROM exploration_obs WHERE decision_ns >= ? AND decision_ns < ?",
        (*(f"{p}%" for p in MODEL_BLOCK_PREFIXES), int(since.value), int(until.value))).fetchone()
    return Influence(n=int(row[0] or 0), scored=int(row[1] or 0), changed=int(row[2] or 0))


# ---------------------------------------------------------------------------------------------- judgements
def _over(now: pd.Timestamp, since: pd.Timestamp) -> bool:
    """True once a WARN that began at `since` (never before LIVENESS_FROM) has held FAIL_AFTER_H."""
    return (now - max(since, LIVENESS_FROM)).total_seconds() / 3600 >= FAIL_AFTER_H


def judge_learner(spec: Learner, read: LearnerRead, *, enabled: bool, applied: bool, now: pd.Timestamp,
                  cadence_h: float | None = None, slack_h: float | None = None) -> Verdict:
    """One learner's verdict (module docstring): off WARNs, never FAILs; on with no output in its cadence WARNs,
    then FAILs once that has held FAIL_AFTER_H; output computed but not applied WARNs."""
    cad = spec.cadence_h if cadence_h is None else cadence_h
    slack = spec.slack_h if slack_h is None else slack_h
    if read.error is not None:
        return Verdict(name=spec.name, status="warn", reason=f"{spec.name}: {spec.table} unreadable ({read.error})")
    if not enabled:
        return Verdict(name=spec.name, status="warn",
                       reason=f"{spec.name}: switched off ({spec.switch}), {read.rows or 0} rows: {spec.why}")
    if not read.rows or read.last is None:
        status: Status = "fail" if _over(now, LIVENESS_FROM) else "warn"
        return Verdict(name=spec.name, status=status, reason=f"{spec.name}: 0 rows in {spec.table}: {spec.why}")
    age_h = (now - read.last).total_seconds() / 3600
    due = read.last + pd.Timedelta(hours=cad + slack)
    if now > due:
        status = "fail" if _over(now, due) else "warn"
        return Verdict(name=spec.name, status=status,
                       reason=f"{spec.name}: no output for {_age(age_h * 3600)} (cadence {cad:g} h + {slack:g} h): "
                              f"{spec.why}")
    if not applied:
        assert spec.applied is not None
        return Verdict(name=spec.name, status="warn",
                       reason=f"{spec.name}: computed, not applied ({spec.applied[0]} is not {spec.applied[1]}): "
                              f"influence 0")
    return Verdict(name=spec.name, status="ok", reason=f"{spec.name}: {read.rows} rows, newest {_age(age_h * 3600)} old")


def judge_watcher(name: str, *, subjects: int | None, ts: pd.Timestamp | None, models_to_watch: int,
                  models_since: pd.Timestamp | None, cadence_h: float, now: pd.Timestamp) -> Verdict:
    """A watcher (drift_watch, model_watch) by its subjects: none watched while a model exists WARNs, then FAILs
    after FAIL_AFTER_H from the oldest such model's activation; a report older than two cadences WARNs."""
    if subjects is None or ts is None:
        if models_to_watch:
            return Verdict(name=name, status="warn",
                           reason=f"{name}: no report while {models_to_watch} model(s) need watching")
        return Verdict(name=name, status="ok", reason=f"{name}: no report, nothing to watch")
    if models_to_watch and subjects == 0:
        status: Status = "fail" if _over(now, models_since or LIVENESS_FROM) else "warn"
        return Verdict(name=name, status=status,
                       reason=f"{name}: watches 0 of {models_to_watch} model(s) (self_retrain shadow/active): "
                              "drift and halts are blind")
    age_s = (now - ts).total_seconds()
    if age_s > 2 * cadence_h * 3600:
        return Verdict(name=name, status="warn", reason=f"{name}: report {_age(age_s)} old")
    if subjects == 0:
        return Verdict(name=name, status="ok", reason=f"{name}: nothing to watch")
    return Verdict(name=name, status="ok", reason=f"{name}: {subjects} subject(s)")


def judge_all(reads: Mapping[str, LearnerRead], *, settings: Any, now: pd.Timestamp,
              watchers: Iterable[tuple[str, int | None, pd.Timestamp | None, float]], models_to_watch: int,
              models_since: pd.Timestamp | None) -> list[Verdict]:
    """Every learner's and watcher's verdict. `watchers`: (name, subjects, report time, cadence h)."""
    out: list[Verdict] = []
    for spec in LEARNERS:
        enabled = True if spec.switch is None else bool(setting(settings, spec.switch))
        applied = True if spec.applied is None else str(setting(settings, spec.applied[0])) == spec.applied[1]
        cad, slack = cadence(spec, settings)
        out.append(judge_learner(spec, reads.get(spec.name, LearnerRead(name=spec.name, rows=0)), enabled=enabled,
                                 applied=applied, now=now, cadence_h=cad, slack_h=slack))
    for name, subjects, ts, cad_h in watchers:
        out.append(judge_watcher(name, subjects=subjects, ts=ts, models_to_watch=models_to_watch,
                                 models_since=models_since, cadence_h=cad_h, now=now))
    return out


def summary(verdicts: Sequence[Verdict]) -> tuple[Status, str]:
    """(worst status, reason): the non-ok lines first (fail, then warn), then the ok names."""
    bad = sorted((v for v in verdicts if v.status != "ok"), key=lambda v: v.status != "fail")
    ok = [v.name for v in verdicts if v.status == "ok"]
    head = f"{len(bad)} of {len(verdicts)} learners/watchers dead or off" if bad else \
        f"{len(verdicts)} learners/watchers live"
    parts = [head, *(f"{v.status.upper()} {v.reason}" for v in bad)]
    if ok:
        parts.append(f"ok: {', '.join(ok)}")
    return _worst([v.status for v in verdicts]), "; ".join(parts)


# ---------------------------------------------------------------------------------------------- checks
def on_brain(ctx: HealthContext) -> bool:
    """Only where the exploration book has recorded at least one candidate (aux.db `exploration_obs`) and settings
    loaded: there the learners have input, so empty output means a dead learner. A fresh or test host with an empty
    aux.db has nothing to learn from and gets no line (the brain holds tens of thousands of candidates)."""
    from goldbot.db.connection import db_path
    path = db_path(ctx.state_dir, "aux")
    if ctx.settings is None or not path.exists():
        return False
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            return _has_table(con, "exploration_obs") and \
                con.execute("SELECT 1 FROM exploration_obs LIMIT 1").fetchone() is not None
        finally:
            con.close()
    except sqlite3.Error:
        return True          # unreadable: the check runs and says so


def _ro(ctx: HealthContext) -> sqlite3.Connection:
    from goldbot.db.connection import db_path
    return sqlite3.connect(f"file:{db_path(ctx.state_dir, 'aux')}?mode=ro", uri=True, timeout=5)


def watcher_reports(state_dir: Path, settings: Any) -> list[tuple[str, int | None, pd.Timestamp | None, float]]:
    """(name, subjects, report time, cadence h) of each watcher's state file; (None, None) when absent or torn."""
    out: list[tuple[str, int | None, pd.Timestamp | None, float]] = []
    for name, file, job in WATCHERS:
        cad = 24.0
        sch = setting(settings, f"scheduler.{job}")
        if sch is not None:
            cad = {"daily": 24.0, "weekly": 168.0}.get(str(sch.kind), 24.0)
        try:
            d = _read_json(state_dir / file)
            agents = d.get("agents") or {}
            ts = pd.Timestamp(d["ts"])
            ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
            out.append((name, len(agents), ts, cad))
        except (OSError, ValueError, KeyError, AttributeError, TypeError):
            out.append((name, None, None, cad))
    return out


@register_check("global", block=BLOCK_TAIL, pos=163, when=on_brain)
def check_learning_liveness(ctx: HealthContext) -> Check:
    """Every learner writes and every watcher watches (module docstring)."""
    try:
        con = _ro(ctx)
        try:
            reads = {spec.name: read_learner(con, spec) for spec in LEARNERS}
            n_models, since = models_to_watch(con)
        finally:
            con.close()
    except sqlite3.Error as exc:
        return Check(name="learning_liveness", status="warn", reason=f"aux.db unreadable: {type(exc).__name__}")
    verdicts = judge_all(reads, settings=ctx.settings, now=ctx.now, watchers=watcher_reports(ctx.state_dir, ctx.settings),
                         models_to_watch=n_models, models_since=since)
    status, reason = summary(verdicts)
    return Check(name="learning_liveness", status=status, reason=reason[:1500])


@register_check("global", block=BLOCK_TAIL, pos=164, when=on_brain)
def check_model_influence(ctx: HealthContext) -> Check:
    """K2 over the last INFLUENCE_WINDOW_H: WARN at 0 % scored with candidates; never FAIL (module docstring)."""
    since = ctx.now - pd.Timedelta(hours=INFLUENCE_WINDOW_H)
    try:
        con = _ro(ctx)
        try:
            inf = influence(con, since=since, until=ctx.now)
        finally:
            con.close()
    except sqlite3.Error as exc:
        return Check(name="model_influence", status="warn", reason=f"aux.db unreadable: {type(exc).__name__}")
    win = f"{INFLUENCE_WINDOW_H:g} h"
    if inf.n == 0:
        return Check(name="model_influence", status="ok", reason=f"no candidates in the last {win}")
    line = (f"{inf.scored} of {inf.n} candidates in {win} scored by a model ({inf.scored_pct:.1f} % scored), "
            f"{inf.changed} changed ({inf.changed_pct:.1f} %)")
    if inf.scored == 0:
        return Check(name="model_influence", status="warn",
                     reason=f"{line}: no model touches a decision (learning reaches nothing)")
    return Check(name="model_influence", status="ok", reason=line)
