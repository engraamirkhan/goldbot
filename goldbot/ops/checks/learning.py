"""Learning liveness and model influence, judged by OUTPUT, never by a job's completion (S5, plan 2026-11-25 W1-C).

Owner red line, "learning must never be silently off" (memory learning-never-silently-off): on 11-13 the learner was
switched off and health stayed green for twelve days; on 11-25 seven learning tables held 0 rows and drift_watch and
model_watch watched 0 models, all reported ok. These checks read what the learners WROTE and what the watchers WATCH.
One check per subject (SRE review of #456 H1: one combined line let a FAIL hide a later death and kept the dead man's
switch failing on any one of them):

  learning:<name>  a learner (LEARNERS: an aux.db output table and the rows that count as output). Switched on with 0
                   output rows, or none within its cadence + slack, WARNs; once that has held FAIL_AFTER_H (48 h) it
                   FAILs and the alert loop tells the owner on Telegram. Its grace starts at `grace_start`: the first
                   deploy whose commit contains this change (state/deploys.jsonl, git ancestry), never before
                   LIVENESS_FROM, so a slipped release still gets its 48 h.
                   Switched off in settings WARNs with its flag and never FAILs (off is a recorded decision, but never
                   silent). Output computed but not applied (the nightly weights under `allocator: equal`) reads ok and
                   names the allocator: that choice is model_influence's to report. Fill-driven learners
                   (cost_cells, arm_lifecycle_verdicts) have a FILL_DRIVEN_CADENCE_H cadence, so a weekend is no gap.
  learning:<name>  a RETIRED learner (name, date, reason) reads ok with its retirement: deleted from judgement with a
                   reason, as the plan asks ("each dead table revived or deleted with a reason").
  watch:<name>     a watcher by the subjects it must cover: drift_watch every self-retrain model in shadow or active
                   (WATCH_STATUSES), compared by model id with drift.json (`agents` and `self_retrain`); a missing one
                   WARNs, then FAILs after FAIL_AFTER_H from the later of its activation and the grace start.
                   model_watch is the champions' CUSUM: a self-retrain filter is judged forward by its own demotion
                   rule (research/self_retrain.py step 4), so model_watch must cover no filter; it WARNs only on a stale
                   report. A report older than two cadences WARNs.
  model_influence  the share of the last INFLUENCE_WINDOW_H of exploration candidates a model scored (meta_p,
                   model_version or bandit_version set) and changed (a block reason a model gave, MODEL_BLOCK_PREFIXES),
                   cached INFLUENCE_CACHE_S per host (index exploration_obs_decision, migration 44). WARN at 0 % scored
                   with candidates in the window; never FAIL: a statistical gate that lets no model through is honest,
                   and a FAIL would push to loosen it (statistical gates are not limits). Also from the database (ADR
                   0101, trading-safety F5 of #463): the eligible candidates (an arm with a served self-retrain model,
                   decided after its cutoff) and how many carry a score; WARN below MIN_ELIGIBLE_SCORED of them.
                   The reason names the active/shadow models shadow scoring refuses for missing provenance (no
                   model_sha256 or feature_versions: trained before migration 45), so 0 % scored says why (SRE 11-27).

Reads aux.db read-only (`mode=ro`), the watcher files, state/deploys.jsonl and git ancestry (read-only, cached). No
order path, no write.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.ops.checks._base import Check, Status, _age, _read_json
from goldbot.ops.checks.registry import BLOCK_TAIL, register_check

if TYPE_CHECKING:
    from goldbot.ops.health import HealthContext

LIVENESS_FROM = pd.Timestamp("2026-11-26 22:00", tz="UTC")   # release train 1 (plan W1-C): the earliest grace start
CHANGE_COMMIT = "67a2fa46b536c836c193c4d42ba38efccd500ab4"   # the commit that added these checks (PR #456)
REPO = Path(__file__).resolve().parents[3]                    # this checkout (/opt/goldbot on the brain)
GIT_TIMEOUT_S = 5.0
_CONTAINS: dict[str, bool | None] = {}                        # deploy commit -> contains CHANGE_COMMIT
FAIL_AFTER_H = 48.0                # a WARN that has held this long FAILs (SRE plan item 1: "FAIL after 48 h of WARN")
FILL_DRIVEN_CADENCE_H = 72.0       # SRE review M5: a learner fed by fills writes nothing over a weekend
INFLUENCE_WINDOW_H = 24.0          # SRE plan item 1: "model-influence % is 0 over 24 h"
MIN_ELIGIBLE_SCORED = 0.9          # ADR 0101: below this share of eligible candidates scored, shadow scoring WARNs
INFLUENCE_CACHE_S = 900.0          # SRE review M1: the 24 h count is read at most every 15 min per host
MODEL_BLOCK_PREFIXES = ("model_", "meta_")   # a candidate's block reason that a model, not a rule or a cap, gave
WATCH_STATUSES = ("active", "shadow")        # self_retrain models drift_watch must cover (plan: "every shadow and
                                             # active model")
DRIFT_SR_KEY = "self_retrain"                # drift.json: the self-retrain models drift_watch read (ops/jobs.py)


class Learner(FrozenRecord):
    """One learner as its OUTPUT shows it: the aux.db table it writes, the stamp column and the rows that count."""

    name: str
    table: str
    ts_col: str                          # an epoch-ns column: when the row was written or decided
    where: str = ""                      # a constant SQL filter of the rows that count as output (never user input)
    job: str | None = None               # the scheduler job that writes it: its kind and max_late_hours set the cadence
    cadence_h: float = 24.0              # the floor of the cadence (a fill-driven learner: FILL_DRIVEN_CADENCE_H)
    slack_h: float = 6.0
    switch: str | None = None            # dotted Settings path of the bool that turns it on; None: always on
    applied: tuple[str, str] | None = None   # (dotted Settings path, value) under which its output reaches a decision
    why: str                             # what it learns and what an empty table means: the reason in the WARN


class Retired(FrozenRecord):
    """A learner deleted from judgement, with its date and reason (reads ok; revive it by moving it to LEARNERS)."""

    name: str
    table: str
    date: str
    reason: str


LEARNERS: tuple[Learner, ...] = (
    Learner(name="self_retrain", table="self_retrain_runs", ts_col="run_ns", where="n_with_features > 0",
            job="self_retrain", switch="research.self_retrain.enabled",
            why="weekly per-arm meta-label filter (sr-v2): a run that trained on rows with features"),
    Learner(name="exploration_weights", table="exploration_broker_weights", ts_col="run_ns", job="daily_review",
            applied=("research.exploration.allocator", "thompson"),
            why="nightly Thompson weights per arm (H-61)"),
    Learner(name="har_days", table="har_days", ts_col="available_ns", where="status = 'ok'", job="har_days",
            switch="research.har_abstention.enabled",
            why="N-3 HAR range forecast (fit rows; `off` rows are not output)"),
    Learner(name="arm_lifecycle_verdicts", table="arm_lifecycle_verdicts", ts_col="recorded_ns", job="arm_lifecycle",
            cadence_h=FILL_DRIVEN_CADENCE_H, why="judged reads of demo arms (build 2)"),
    Learner(name="cost_cells", table="exploration_cost_cells", ts_col="fitted_ns", job="daily_review",
            cadence_h=FILL_DRIVEN_CADENCE_H,
            why="nightly slippage per broker x session (the measured cost the replay labels use)"),
)

# SRE review of #456 H1, per-learner decisions. Not judged at all (bookkeeping, not learners): long_tf_decisions (the
# engine's last 1d/1w decision per arm) and swap_reconciliations (written only when a weekend crypto hold exits; its
# own health check swap_reconcile judges it)
RETIRED: tuple[Retired, ...] = (
    Retired(name="meta_labeller", table="exploration_models", date="2026-11-13",
            reason="H-62 retired (0 model rows, no candidate trainable); replaced by self_retrain"),
    Retired(name="arm_priors", table="exploration_priors", date="2026-11-25",
            reason="H-60 history priors never wired (exploration_data.write_prior has no caller)"),
)

WATCHERS: tuple[tuple[str, str, str], ...] = (
    ("drift_watch", "drift.json", "drift_watch"),           # (name, state file, scheduler job)
    ("model_watch", "model_watch.json", "model_watch"),
)
MUST_COVER_SR = frozenset({"drift_watch"})   # the watchers that must cover every self-retrain shadow/active model


class LearnerRead(FrozenRecord):
    """What one learner's table holds: output rows and the newest stamp (None: no row or no table)."""

    name: str
    rows: int | None = None              # None: the table cannot be read (`error`)
    last: UtcTimestamp | None = None
    error: str | None = None


class WatcherReport(FrozenRecord):
    """A watcher's state file: the subject ids it watched and when (None: no file or torn)."""

    name: str
    subjects: frozenset[str] | None = None
    ts: UtcTimestamp | None = None
    cadence_h: float = 24.0


class Influence(FrozenRecord):
    """K2: candidates in a window, those a model scored and those a model changed (plan §1b)."""

    n: int
    scored: int
    changed: int
    # ADR 0101 (trading-safety F5 of #463), from the database: candidates a served model should have scored (its arm
    # has an active or shadow self-retrain model with recorded provenance whose data cutoff precedes the decision) and
    # those that carry a score. eligible - eligible_scored are the ones lost (a full queue, an expired job, a restart)
    eligible: int = 0
    eligible_scored: int = 0
    # SRE 11-27: active or shadow self-retrain models with recorded provenance (model_sha256 and feature_versions)
    # and those without, which shadow scoring refuses (goldbot/engine/shadow_scoring.py `_provenance_problem`): rows
    # trained before migration 45. The reason names them so "learning reaches nothing" says why
    served: int = 0
    refused_no_provenance: int = 0

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


def _kind_h(kind: str, default: float) -> float:
    return {"daily": 24.0, "weekly": 168.0, "monthly": 744.0}.get(kind, default)


def cadence(spec: Learner, settings: Any) -> tuple[float, float]:
    """(cadence, slack) in hours: the job's kind (daily 24, weekly 168) and max_late_hours, never under the spec's
    own floor (a weekly H-62, a fill-driven learner)."""
    sch = setting(settings, f"scheduler.{spec.job}") if spec.job else None
    if sch is None:
        return spec.cadence_h, spec.slack_h
    return max(_kind_h(str(sch.kind), spec.cadence_h), spec.cadence_h), float(sch.max_late_hours)


def _has_table(con: sqlite3.Connection, table: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone() is not None


def _count(con: sqlite3.Connection, table: str, ts_col: str, where: str = "") -> tuple[int, pd.Timestamp | None]:
    if not _has_table(con, table):
        return 0, None
    w = f" WHERE {where}" if where else ""
    n, last = con.execute(f"SELECT COUNT(*), MAX({ts_col}) FROM {table}{w}").fetchone()
    return int(n or 0), (pd.Timestamp(int(last), unit="ns", tz="UTC") if last is not None else None)


def read_learner(con: sqlite3.Connection, spec: Learner) -> LearnerRead:
    """Output rows and the newest stamp of one learner (table, column and filter are module constants)."""
    try:
        n, last = _count(con, spec.table, spec.ts_col, spec.where)
    except sqlite3.Error as exc:
        return LearnerRead(name=spec.name, error=type(exc).__name__)
    return LearnerRead(name=spec.name, rows=n, last=last)


def sr_models(con: sqlite3.Connection) -> dict[str, pd.Timestamp]:
    """{model_version: activation} of the self-retrain models in WATCH_STATUSES (drift_watch must cover each)."""
    if not _has_table(con, "self_retrain_models"):
        return {}
    q = ", ".join("?" for _ in WATCH_STATUSES)
    rows = con.execute(f"SELECT model_version, activation_ns FROM self_retrain_models WHERE status IN ({q})",
                       WATCH_STATUSES).fetchall()
    return {str(v): pd.Timestamp(int(a), unit="ns", tz="UTC") for v, a in rows}


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
    eligible = eligible_scored = 0
    if _has_table(con, "self_retrain_models") and "model_sha256" in {
            r[1] for r in con.execute("PRAGMA table_info(self_retrain_models)")}:
        got = con.execute(
            "SELECT COUNT(*), SUM(CASE WHEN o.meta_p IS NOT NULL THEN 1 ELSE 0 END) FROM exploration_obs o "
            "WHERE o.decision_ns >= ? AND o.decision_ns < ? AND EXISTS (SELECT 1 FROM self_retrain_models m "
            "WHERE m.broker = o.broker AND m.arm_id = o.arm_id AND m.status IN ('active', 'shadow') "
            "AND m.model_sha256 IS NOT NULL AND m.cutoff_ns < o.decision_ns)",
            (int(since.value), int(until.value))).fetchone()
        eligible, eligible_scored = int(got[0] or 0), int(got[1] or 0)
        prov = con.execute(
            "SELECT SUM(CASE WHEN model_sha256 IS NOT NULL AND feature_versions IS NOT NULL "
            "AND feature_versions NOT IN ('', '[]') THEN 1 ELSE 0 END), COUNT(*) FROM self_retrain_models "
            "WHERE status IN ('active', 'shadow')").fetchone()
        served = int(prov[0] or 0)
        refused = int(prov[1] or 0) - served
    else:
        served = refused = 0
    return Influence(n=int(row[0] or 0), scored=int(row[1] or 0), changed=int(row[2] or 0), eligible=eligible,
                     eligible_scored=eligible_scored, served=served, refused_no_provenance=refused)


def _contains_change(to: str, repo: Path) -> bool | None:
    """True when commit `to` contains this change (CHANGE_COMMIT is its ancestor), False when it does not, None when
    git cannot tell (no repo, unknown commit, timeout). Read-only git, cached per commit for the process."""
    hit = _CONTAINS.get(to)
    if hit is not None or to in _CONTAINS:
        return hit
    try:
        r = subprocess.run(["git", "-c", f"safe.directory={repo}", "-C", str(repo), "merge-base", "--is-ancestor",
                            CHANGE_COMMIT, to], capture_output=True, timeout=GIT_TIMEOUT_S, check=False)
        got: bool | None = {0: True, 1: False}.get(r.returncode)
    except (OSError, subprocess.SubprocessError):
        got = None
    _CONTAINS[to] = got
    return got


def grace_start(state_dir: Path, repo: Path = REPO) -> pd.Timestamp:
    """When the 48 h grace of a learner that never wrote begins (SRE review of #456 L1): the first successful deploy
    whose commit contains this change (`git merge-base --is-ancestor CHANGE_COMMIT <to>`, state/deploys.jsonl is
    append-only, goldbot/ops/deploy.py), never before LIVENESS_FROM. A deploy git cannot judge is skipped; with none
    found (a host without the deploy tool, a test) LIVENESS_FROM."""
    try:
        lines = (state_dir / "deploys.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return LIVENESS_FROM
    for line in lines:
        try:
            rec = json.loads(line)
            if not str(rec.get("result", "")).startswith("deployed") or not rec.get("to"):
                continue
            if _contains_change(str(rec["to"]), repo):
                ts = pd.Timestamp(rec["ts"])
                ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
                return max(ts, LIVENESS_FROM)
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    return LIVENESS_FROM


# ---------------------------------------------------------------------------------------------- judgements
def _over(now: pd.Timestamp, since: pd.Timestamp, grace: pd.Timestamp) -> bool:
    """True once a WARN that began at `since` (never before the grace start) has held FAIL_AFTER_H."""
    return (now - max(since, grace)).total_seconds() / 3600 >= FAIL_AFTER_H


def judge_learner(spec: Learner, read: LearnerRead, *, enabled: bool, applied: bool, now: pd.Timestamp,
                  cadence_h: float | None = None, slack_h: float | None = None,
                  grace: pd.Timestamp = LIVENESS_FROM) -> Check:
    """`learning:<name>` (module docstring): off WARNs, never FAILs; on with no output in its cadence WARNs, then
    FAILs once that has held FAIL_AFTER_H past the grace start; computed but not applied reads ok, named."""
    name = f"learning:{spec.name}"
    cad = spec.cadence_h if cadence_h is None else cadence_h
    slack = spec.slack_h if slack_h is None else slack_h
    if read.error is not None:
        return Check(name=name, status="warn", reason=f"{spec.table} unreadable ({read.error})")
    if not enabled:
        return Check(name=name, status="warn",
                     reason=f"switched off ({spec.switch}), {read.rows or 0} rows: {spec.why}")
    if not read.rows or read.last is None:
        status: Status = "fail" if _over(now, grace, grace) else "warn"
        return Check(name=name, status=status, reason=f"0 rows in {spec.table}: {spec.why}")
    age_s = (now - read.last).total_seconds()
    due = read.last + pd.Timedelta(hours=cad + slack)
    if now > due:
        status = "fail" if _over(now, due, grace) else "warn"
        return Check(name=name, status=status,
                     reason=f"no output for {_age(age_s)} (cadence {cad:g} h + {slack:g} h): {spec.why}")
    tail = ""
    if not applied:
        assert spec.applied is not None
        tail = f"; computed, not applied ({spec.applied[0]} is not {spec.applied[1]}: model_influence reports it)"
    return Check(name=name, status="ok", reason=f"{read.rows} rows, newest {_age(age_s)} old{tail}")


def judge_retired(r: Retired, rows: int | None) -> Check:
    return Check(name=f"learning:{r.name}", status="ok",
                 reason=f"retired {r.date}: {r.reason} ({rows if rows is not None else '?'} rows in {r.table})")


def judge_watcher(rep: WatcherReport, *, must_cover: Mapping[str, pd.Timestamp], now: pd.Timestamp,
                  grace: pd.Timestamp = LIVENESS_FROM) -> Check:
    """`watch:<name>`: every id in `must_cover` ({model id: activation}) among its subjects, else WARN, then FAIL
    FAIL_AFTER_H after the later of the missing model's activation and the grace start (SRE review M2: compared by
    id, not by count); a report older than two cadences WARNs."""
    name = f"watch:{rep.name}"
    if rep.subjects is None or rep.ts is None:
        if must_cover:
            first = min(must_cover.values())
            status: Status = "fail" if _over(now, first, grace) else "warn"
            return Check(name=name, status=status, reason=f"no report while {len(must_cover)} model(s) need watching")
        return Check(name=name, status="ok", reason="no report, nothing it must watch")
    missing = {m: t for m, t in must_cover.items() if m not in rep.subjects}
    if missing:
        status = "fail" if _over(now, min(missing.values()), grace) else "warn"
        ids = ", ".join(sorted(missing)[:5]) + (" ..." if len(missing) > 5 else "")
        return Check(name=name, status=status,
                     reason=f"watches {len(must_cover) - len(missing)} of {len(must_cover)} self-retrain model(s); "
                            f"missing {ids}: drift is blind for them")
    age_s = (now - rep.ts).total_seconds()
    if age_s > 2 * rep.cadence_h * 3600:
        return Check(name=name, status="warn", reason=f"report {_age(age_s)} old ({len(rep.subjects)} subject(s))")
    return Check(name=name, status="ok", reason=f"{len(rep.subjects)} subject(s), report {_age(age_s)} old")


def judge_all(reads: Mapping[str, LearnerRead], *, settings: Any, now: pd.Timestamp,
              watchers: Iterable[WatcherReport], models: Mapping[str, pd.Timestamp],
              retired_rows: Mapping[str, int | None] | None = None,
              grace: pd.Timestamp = LIVENESS_FROM) -> list[Check]:
    """Every learner's, retired learner's and watcher's check, in that order."""
    out: list[Check] = []
    for spec in LEARNERS:
        enabled = True if spec.switch is None else bool(setting(settings, spec.switch))
        applied = True if spec.applied is None else str(setting(settings, spec.applied[0])) == spec.applied[1]
        cad, slack = cadence(spec, settings)
        out.append(judge_learner(spec, reads.get(spec.name, LearnerRead(name=spec.name, rows=0)), enabled=enabled,
                                 applied=applied, now=now, cadence_h=cad, slack_h=slack, grace=grace))
    out += [judge_retired(r, (retired_rows or {}).get(r.name)) for r in RETIRED]
    for rep in watchers:
        out.append(judge_watcher(rep, must_cover=models if rep.name in MUST_COVER_SR else {}, now=now, grace=grace))
    return out


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


def watcher_reports(state_dir: Path, settings: Any) -> list[WatcherReport]:
    """Each watcher's state file: its subject ids (drift.json `agents` and DRIFT_SR_KEY; model_watch.json `agents`)
    and report time; no subjects when absent or torn."""
    out: list[WatcherReport] = []
    for name, file, job in WATCHERS:
        sch = setting(settings, f"scheduler.{job}")
        cad = _kind_h(str(sch.kind), 24.0) if sch is not None else 24.0
        try:
            d = _read_json(state_dir / file)
            ids = {str(k) for k in (d.get("agents") or {})} | {str(k) for k in (d.get(DRIFT_SR_KEY) or {})}
            ts = pd.Timestamp(d["ts"])
            ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
            out.append(WatcherReport(name=name, subjects=frozenset(ids), ts=ts, cadence_h=cad))
        except (OSError, ValueError, KeyError, AttributeError, TypeError):
            out.append(WatcherReport(name=name, cadence_h=cad))
    return out


@register_check("global", block=BLOCK_TAIL, pos=163, when=on_brain)
def check_learning_liveness(ctx: HealthContext) -> list[Check]:
    """`learning:<name>` per learner and retired learner, `watch:<name>` per watcher (module docstring)."""
    try:
        con = _ro(ctx)
        try:
            reads = {spec.name: read_learner(con, spec) for spec in LEARNERS}
            retired: dict[str, int | None] = {}
            for r in RETIRED:
                try:
                    retired[r.name] = _count(con, r.table, "rowid")[0]
                except sqlite3.Error:
                    retired[r.name] = None
            models = sr_models(con)
        finally:
            con.close()
    except sqlite3.Error as exc:
        return [Check(name="learning:aux", status="warn", reason=f"aux.db unreadable: {type(exc).__name__}")]
    return judge_all(reads, settings=ctx.settings, now=ctx.now, watchers=watcher_reports(ctx.state_dir, ctx.settings),
                     models=models, retired_rows=retired, grace=grace_start(ctx.state_dir))


_INFLUENCE_CACHE: dict[str, tuple[pd.Timestamp, Influence]] = {}


@register_check("global", block=BLOCK_TAIL, pos=164, when=on_brain)
def check_model_influence(ctx: HealthContext) -> Check:
    """K2 over the last INFLUENCE_WINDOW_H, read at most every INFLUENCE_CACHE_S: WARN at 0 % scored with candidates;
    never FAIL (module docstring)."""
    key = str(ctx.state_dir)
    hit = _INFLUENCE_CACHE.get(key)
    if hit is not None and 0 <= (ctx.now - hit[0]).total_seconds() < INFLUENCE_CACHE_S:
        inf = hit[1]
    else:
        try:
            con = _ro(ctx)
            try:
                inf = influence(con, since=ctx.now - pd.Timedelta(hours=INFLUENCE_WINDOW_H), until=ctx.now)
            finally:
                con.close()
        except sqlite3.Error as exc:
            return Check(name="model_influence", status="warn", reason=f"aux.db unreadable: {type(exc).__name__}")
        _INFLUENCE_CACHE[key] = (ctx.now, inf)
    win = f"{INFLUENCE_WINDOW_H:g} h"
    if inf.n == 0:
        return Check(name="model_influence", status="ok", reason=f"no candidates in the last {win}")
    line = (f"{inf.scored} of {inf.n} candidates in {win} scored by a model ({inf.scored_pct:.1f} % scored), "
            f"{inf.changed} changed ({inf.changed_pct:.1f} %); {inf.eligible_scored} of {inf.eligible} eligible "
            "(an arm with a served model, decided after its cutoff) scored")
    if inf.refused_no_provenance:
        line += (f"; {inf.served} served: {inf.refused_no_provenance} rows refused (no provenance, trained before "
                 "migration 45)")
    if inf.scored == 0:
        return Check(name="model_influence", status="warn",
                     reason=f"{line}: no model touches a decision (learning reaches nothing)")
    if inf.eligible and inf.eligible_scored < MIN_ELIGIBLE_SCORED * inf.eligible:
        return Check(name="model_influence", status="warn",
                     reason=f"{line}: shadow scoring is losing candidates (below {MIN_ELIGIBLE_SCORED:.0%})")
    return Check(name="model_influence", status="ok", reason=line)
