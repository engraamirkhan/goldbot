"""Shadow scoring: every frozen self-retrain model scores every exploration candidate of its arm, and the score is
logged with the model's sha256 and the feature version. No decision reads it (lifecycle plan W1, owner 2026-11-25:
"focus on proper model training and retraining with clear objectives"; ADR 0101).

The plan's rule (ml-researcher W1): "Every candidate gets the scores of every frozen model ..., logged with
`model_version` ... No gating." As built here:

* No gating. `submit` is called by `exploration.candidate` AFTER the candidate's row is written and returns nothing.
  The queue, `_send`, the budget, the caps and the RiskGate never read a score. A test runs one engine with models and
  one without and finds the same orders, refusals, gate calls and records.
* Never on the tick, and never beside it. The vector is `feature_snapshots.decision_features`, the nightly snapshot's
  own function, and it takes 0.1-0.5 s per decision. It is built on one background thread per exploration block. The
  tick only puts a job on a bounded queue (well under 5 ms per candidate, measured in tests/test_shadow_scoring.py).
  The worker shares the GIL with the tick (trading-safety F1 of #463: a pure-Python tick ran 1.6x slower during a
  build). So a job waits DEFER_S after it was queued (a bar close's send burst is over by then), never runs while an
  exploration pass is in progress, and runs one job per pass end (or per IDLE_S without a pass).
* Bounded memory (F4): at most QUEUE_MAX jobs, and one pinned 1m frame per (timeframe, decision), at most FRAMES_MAX
  of them. A job older than one bar of its timeframe (plus DEFER_S) is dropped. A full queue, a frame over the cap or
  an expired job is counted, logged and left unscored. Jobs queued at a restart are lost; health's model_influence
  counts them as eligible but unscored.
* Decision-time numbers, written once afterwards (ADR 0101). The vector reads only minutes visible at the decision. A
  model scores only candidates decided after its data cutoff (`cutoff_ns`), so every score is out of sample. The score
  is the number a synchronous scorer would have logged before the decision; it lands on the row a moment after the
  row is recorded.
* Provenance (ADR 0010: JSON through `load_record_json`, never a pickle; SRE gap "load refuses a mismatch"). A row is
  served only when all of these hold:
  - its spec is the trainer's `SPEC_VERSION` and its inputs are the trainer's `INPUTS`;
  - its stored `model_sha256` equals the SHA-256 of its stored JSON text;
  - its data cutoff precedes its activation;
  - its training feature versions are recorded.
  A refused row is logged once and named in the published state. A vector whose feature version the model never
  trained on is not scored by that model.
* Parity: scores use the trainer's arithmetic without pandas, `self_retrain.design_row` (signed columns, side) and
  `LogitModel.score_row`, bit for bit `design` then `LogitModel.score` (tested), so the gate decides on the tick. The
  vector's sha256 is the nightly snapshot's (`feature_snapshots.vector_sha256`), so an engine-vs-snapshot parity read
  is a join on `vector_sha256`.
* Write-once. Rows go to `exploration_model_scores`, which is append-only. `exploration_obs` gets `meta_p`,
  `model_version` and `feature_version` only where all three are still NULL. The `meta_p` value is the arm's ACTIVE
  model's score, or the score of its newest model when none is active. Health's `model_influence` counts these rows
  as scored, and its "changed" count stays 0 because nothing gates.
* Fail soft. Any error (unreadable table, no vector, a failed write) is logged, counted and skipped. Nothing raises
  into the engine.

Scope: the models are gold-only (the trainer is gold-only), so non-gold arms are never queued. The scorer reads
`self_retrain_models` and writes `exploration_model_scores` plus those three NULL columns of `exploration_obs`. It
never reaches an order, a close, a size, a limit or the gate.

The demo entry gate. Owner 2026-11-25: "everything should be live immediately", then "before deploying, the models
should be trained". The gate is built and shipped OFF. It acts only when every one of these holds:
- the account's `model_gate` flag is set (accounts.yaml, default false, set on no account) and the engine is a demo
  engine;
- `risk.gate.is_demo` says demo at the signal (`arm_registry.demo_now`);
- the arm is a strategy or learning arm's first unit, never a control, a replay_only, owner-experiment or cost arm,
  and never a pyramid add;
- the served model's spec is in GATE_SPECS, which is empty: today's sr-v2 models never gate. The first spec named
  there is the new pipeline's (objective.py, the feature store, history labels), after its training report.

What it does, in `exploration._send`, before the RiskGate:
- it keeps a candidate iff the model's stored rule keeps it (sr-v2: score >= tau);
- a rejected candidate with `explore_u(candidate) < EXPLORE_P` is still sent as an exploration sample (ADR 0098
  section 3). Its propensity is EXPLORE_P, logged on every rejected candidate, and its fill carries evidence class
  `explore_sample`, which no edge read or arm n counts (evidence_exclusions.NOT_EDGE_EVIDENCE); the money and
  kill stops do count it (NOT_STOP_EVIDENCE excludes only the cohort);
- the rest are refused with `model_gate:<model_version>`, recorded and replay-labelled like any refused candidate.

The vector is built on the worker. The queue holds the signal (`gate_waits`) at most GATE_WAIT_S after its bar
closes. With no vector, no model or any error, the gate fails open to the existing rule. Exits never pass here.
"""
from __future__ import annotations

import json
import logging
import math
import queue
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pandas as pd

from goldbot.base import FrozenRecord, Record, load_record_json
from goldbot.db.connection import now_ns
from goldbot.instruments import GOLD

if TYPE_CHECKING:
    from goldbot.db import Database
    from goldbot.engine.exploration import Arm, Exploration, Pending
    from goldbot.engine.runner import Engine
    from goldbot.research.feature_snapshots import Snapshot

log = logging.getLogger("goldbot.shadow_scoring")

SERVED_STATUSES = ("active", "shadow")   # the frozen models scored (demoted and superseded ones are not)
RELOAD_S = 300.0                 # the models are re-read at most this often (worker's monotonic clock)
QUEUE_MAX = 200                  # jobs waiting at most; a full queue drops the job unscored (counted)
FRAMES_MAX = 8                   # distinct (timeframe, decision) 1m frames pinned by waiting jobs at most
DEFER_S = 10.0                   # a scoring job waits this long after it was queued (after the close's send burst)
IDLE_S = 2.0                     # the worker runs one job per exploration pass end, or per IDLE_S with no pass
VECTORS_KEPT = 64                # (timeframe, decision) vectors kept: arms deciding on one bar share one build
ERROR_WINDOW_S = 900.0           # the published state names the last failure while it is this recent (wall clock)
DROP_LOG_EVERY_S = 60.0          # a full queue is logged at most this often
# the demo gate (module docstring): spec versions whose models may gate; EMPTY until the new pipeline's first trained
# model and its training report (owner 2026-11-25, "before deploying, the models should be trained")
GATE_SPECS: frozenset[str] = frozenset()
GATED_ROLES = ("strategy", "learning")
EXPLORE_P = 0.10                 # ADR 0098 section 3: the randomised share of model-rejected candidates still traded
EXPLORE_EVIDENCE = "explore_sample"
GATE_REFUSAL = "model_gate:"     # blocked_reason prefix (health model_influence counts it as "changed")
GATE_WAIT_S = 5.0                # a gated signal waits at most this long after its bar close for its vector
# a 1m/5m signal never waits for a vector (trading-safety F3 of #463): the sr-v3 card allows them only after its three
# conditions, so they stay out of the gate in code
GATE_EXCLUDED_TFS = ("1m", "5m")


class ModelScoringConfig(Record):
    """The engine's two model switches (`runner.EngineConfig` inherits them; ADR 0023: runner.py only shrinks)."""
    # settings research.self_retrain.shadow_score (lifecycle W1, ADR 0101): every frozen self-retrain model scores each
    # gold exploration candidate of its arm on a background thread and the score is logged; no decision reads it.
    # Off by default (tests, tools); production switches it with the settings (ops/services/engine.py)
    shadow_score: bool = False
    # accounts.yaml `model_gate` on a demo account (ADR 0101; shipped off on every account): a gating model may refuse
    # exploration entries before the RiskGate, with a 10 % exploration sample still taken (module docstring)
    model_gate: bool = False


@dataclass(frozen=True)
class Job:
    """One candidate to score (no candidate id: a gate's vector build). Its 1m frame is held once per (timeframe,
    decision) by the scorer (`ShadowScorer.frames`): the engine's frame as it was at the first job of that decision.
    The engine replaces that frame on every update and never mutates it in place (broker_bars.append_bars, _replace;
    history), so the worker reads a frame no one writes."""
    candidate_id: str
    arm_id: str
    tf: str
    side: int
    decision: pd.Timestamp
    queued_at: float = 0.0           # monotonic, set by `ShadowScorer.submit`

    @property
    def key(self) -> tuple[str, int]:
        return self.tf, int(self.decision.value)


class ServedModel(FrozenRecord):
    """One frozen model that passed the provenance checks (`load_models`)."""
    model_version: str
    arm_id: str
    status: str
    spec_version: str
    sha256: str
    cutoff_ns: int
    activation_ns: int
    feature_versions: tuple[str, ...]
    model: Any                       # research.self_retrain.LogitModel (imported lazily: the engine starts without it)


class Scored(FrozenRecord):
    """One model's score of one candidate."""
    model_version: str
    sha256: str
    spec_version: str
    score: float
    primary: bool


def _provenance_problem(row: tuple[Any, ...]) -> tuple[str | None, ServedModel | None]:
    """(why the row is refused, None) or (None, the served model)."""
    from goldbot.research import self_retrain as sr
    from goldbot.research.self_retrain import LogitModel
    version, arm_id, status, spec, cutoff, activation, body, sha, fvs = row
    name = f"{arm_id} {version}"
    if spec != sr.SPEC_VERSION:
        return f"{name}: spec {spec} is not {sr.SPEC_VERSION}", None
    if not sha:
        return f"{name}: no recorded model_sha256 (trained before provenance was recorded)", None
    if sr.model_sha256(str(body)) != str(sha):
        return f"{name}: model sha256 mismatch (the stored JSON is not what was trained)", None
    if cutoff is None or activation is None or int(cutoff) >= int(activation):
        return f"{name}: data cutoff not before activation", None
    try:
        got = json.loads(fvs) if fvs else []
        versions = tuple(str(v) for v in got) if isinstance(got, list) else ()
    except ValueError:
        versions = ()
    if not versions:
        return f"{name}: no recorded training feature version", None
    try:
        model = load_record_json(LogitModel, str(body), source="aux.db self_retrain_models")   # ADR 0010
    except ValueError as exc:
        return f"{name}: model unreadable ({type(exc).__name__})", None
    n = len(sr.INPUTS)
    if model.inputs != sr.INPUTS or {len(model.medians), len(model.means), len(model.scales), len(model.coef)} != {n} \
            or not all(math.isfinite(v) for v in (*model.medians, *model.means, *model.scales, *model.coef,
                                                  model.intercept, model.tau)):
        return f"{name}: not the frozen {n} inputs or non-finite numbers", None
    return None, ServedModel(model_version=str(version), arm_id=str(arm_id), status=str(status),
                             spec_version=str(spec), sha256=str(sha), cutoff_ns=int(cutoff),
                             activation_ns=int(activation), feature_versions=versions, model=model)


def load_models(db: Database, broker: str) -> tuple[dict[str, tuple[ServedModel, ...]], list[str]]:
    """The broker's frozen models in SERVED_STATUSES by arm (provenance verified), and the rows refused with why.
    Read-only."""
    q = ", ".join("?" for _ in SERVED_STATUSES)
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT model_version, arm_id, status, spec_version, cutoff_ns, activation_ns, model, model_sha256, "
            f"feature_versions FROM self_retrain_models WHERE broker = ? AND status IN ({q}) "
            "ORDER BY arm_id, cutoff_ns, model_version", (broker, *SERVED_STATUSES)).fetchall()
    out: dict[str, list[ServedModel]] = {}
    problems: list[str] = []
    for row in rows:
        why, served = _provenance_problem(tuple(row))
        if why is not None or served is None:
            problems.append(str(why))
            continue
        out.setdefault(served.arm_id, []).append(served)
    return {a: tuple(ms) for a, ms in out.items()}, problems


def primary(models: tuple[ServedModel, ...]) -> ServedModel:
    """The model whose score goes on the candidate's row: the arm's ACTIVE model (the one a gate would use), else its
    newest by data cutoff (then version)."""
    active = [m for m in models if m.status == "active"]
    return max(active or models, key=lambda m: (m.cutoff_ns, m.model_version))


def score(models: tuple[ServedModel, ...], snap: Snapshot, side: int, decision_ns: int
          ) -> tuple[list[Scored], list[str]]:
    """Every model's score of one vector (pure). A model is skipped (with why) when the candidate was decided at or
    before its data cutoff (in sample) or when the vector's feature version is not one it trained on."""
    from goldbot.research import self_retrain as sr
    usable: list[ServedModel] = []
    skipped: list[str] = []
    for m in models:
        if decision_ns <= m.cutoff_ns:
            skipped.append(f"{m.model_version}: decided at or before its data cutoff (in sample)")
        elif snap.feature_version not in m.feature_versions:
            skipped.append(f"{m.model_version}: feature version {snap.feature_version} not trained on")
        else:
            usable.append(m)
    if not usable:
        return [], skipped
    x = sr.design_row(snap.features, side)              # one vector, no pandas: the gate scores on the tick
    got: list[tuple[ServedModel, float]] = []
    for m in usable:
        p = m.model.score_row(x)
        if math.isfinite(p):
            got.append((m, p))
        else:
            skipped.append(f"{m.model_version}: non-finite score")
    if not got:
        return [], skipped
    head = primary(tuple(m for m, _ in got))
    return [Scored(model_version=m.model_version, sha256=m.sha256, spec_version=m.spec_version, score=p,
                   primary=m is head) for m, p in got], skipped


def write(db: Database, candidate_id: str, decision_ns: int, snap: Snapshot, scored: list[Scored]) -> None:
    """The scores (append-only rows) and the candidate's primary score, model and feature version, written once: never
    over a value already recorded."""
    from goldbot.research.feature_snapshots import vector_sha256
    vsha, at = vector_sha256(snap), now_ns()
    head = next(s for s in scored if s.primary)
    with db.write() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO exploration_model_scores (candidate_id, model_version, model_sha256, spec_version, "
            "feature_version, vector_sha256, score, is_primary, decision_ns, scored_ns) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(candidate_id, s.model_version, s.sha256, s.spec_version, snap.feature_version, vsha, s.score,
              int(s.primary), decision_ns, at) for s in scored])
        conn.execute("UPDATE exploration_obs SET meta_p = ?, model_version = ?, feature_version = ? "
                     "WHERE candidate_id = ? AND meta_p IS NULL AND model_version IS NULL AND feature_version IS NULL",
                     (head.score, head.model_version, snap.feature_version, candidate_id))


class ShadowScorer:
    """One exploration block's scorer: a bounded job queue and one daemon worker thread that loads the models, builds
    each decision's vector once, scores and writes. Counters are for the engine state file (`published`)."""

    def __init__(self, db: Database, broker: str, *, queue_max: int = QUEUE_MAX, start: bool = True) -> None:
        self.db, self.broker = db, broker
        self.jobs: queue.Queue[Job] = queue.Queue(maxsize=queue_max)
        self.models: dict[str, tuple[ServedModel, ...]] = {}
        self.problems: list[str] = []
        self.loaded_at: float | None = None
        self.vectors: OrderedDict[tuple[str, int], Snapshot | None] = OrderedDict()
        self.counts: dict[str, int] = {"submitted": 0, "scored": 0, "scores": 0, "no_model": 0, "not_scored": 0,
                                       "failed": 0, "dropped": 0}
        self.counts |= {"gate_kept": 0, "gate_refused": 0, "gate_sampled": 0, "gate_fail_open": 0}
        self.error: str | None = None
        self.error_at: float | None = None
        self._dropped_logged: float = 0.0
        self.counts |= {"expired": 0}
        self.lock = threading.Lock()                       # guards `vectors`, `frames` and `warming`
        self.warming: set[tuple[str, int]] = set()
        self.frames: dict[tuple[str, int], tuple[pd.DataFrame, int]] = {}   # key -> (frame, jobs waiting on it)
        self.cv = threading.Condition()                    # the engine's pass marks (F1)
        self.in_pass = False
        self.pass_ends = 0
        self._used_gap = -1
        self._last_turn = 0.0
        self.thread = threading.Thread(target=self._loop, name=f"shadow-score-{broker}", daemon=True)
        if start:
            self.thread.start()

    # ------------------------------------------------------------------------------------------ the tick's side
    def submit(self, job: Job, minutes: pd.DataFrame) -> bool:
        """Queue one job with the engine's 1m frame; never blocks, never raises. False when it was dropped (the queue
        full, or a new decision's frame over FRAMES_MAX)."""
        self.counts["submitted"] += 1
        job = Job(**{**job.__dict__, "queued_at": time.monotonic()})
        with self.lock:
            have = job.key in self.frames or job.key in self.vectors
            ok = have or len(self.frames) < FRAMES_MAX
            if ok:
                try:
                    self.jobs.put_nowait(job)
                except queue.Full:
                    ok = False
            if ok and job.key not in self.vectors:
                frame, n = self.frames.get(job.key, (minutes, 0))
                self.frames[job.key] = (frame, n + 1)
        if not ok:
            self.counts["dropped"] += 1
            if time.monotonic() - self._dropped_logged >= DROP_LOG_EVERY_S:
                self._dropped_logged = time.monotonic()
                log.warning("shadow scoring full (%d jobs, %d frames): candidate %s left unscored (%d dropped so far)",
                            self.jobs.qsize(), len(self.frames), job.candidate_id, self.counts["dropped"])
        return ok

    def mark_pass(self, running: bool) -> None:
        """The engine's exploration pass started (True) or ended (False): the worker never runs during one (F1)."""
        with self.cv:
            self.in_pass = running
            if not running:
                self.pass_ends += 1
                self.cv.notify_all()

    def cached(self, tf: str, decision_ns: int) -> tuple[bool, Snapshot | None]:
        """(built, vector) of a (timeframe, decision): never builds."""
        with self.lock:
            key = (tf, decision_ns)
            return key in self.vectors, self.vectors.get(key)

    def prewarm(self, arm_id: str, tf: str, decision: pd.Timestamp, minutes: pd.DataFrame) -> bool:
        """True while the gate must wait for this decision's vector: the models are not loaded yet, or the arm has a
        gating model and its vector is not built. Queues the build once (a job with no candidate id)."""
        key = (tf, int(decision.value))
        with self.lock:
            if key in self.vectors:
                return False
            if self.loaded_at is not None and not gating_models(self.models.get(arm_id, ())):
                return False
            if key in self.warming:
                return True
            self.warming.add(key)
        if self.submit(Job(candidate_id="", arm_id=arm_id, tf=tf, side=1, decision=decision), minutes):
            return True
        self._warmed(key)                                  # dropped: no wait, the gate fails open
        return False

    def drain(self, timeout: float = 30.0) -> bool:
        """Wait until every queued job is done (tests and shutdown); False on timeout."""
        end = time.monotonic() + timeout
        with self.jobs.all_tasks_done:
            while self.jobs.unfinished_tasks:
                left = end - time.monotonic()
                if left <= 0:
                    return False
                self.jobs.all_tasks_done.wait(left)
        return True

    # ------------------------------------------------------------------------------------------ the worker
    def _loop(self) -> None:
        while True:
            job = self.jobs.get()
            try:
                self._turn(job)
                if time.monotonic() - job.queued_at > _bar_s(job.tf) + DEFER_S:
                    self.counts["expired"] += 1
                    log.info("shadow scoring of %s expired (older than one %s bar): skipped", job.candidate_id, job.tf)
                else:
                    self.run(job)
            except Exception as exc:          # noqa: BLE001 - one job's failure is logged and skipped
                self.fail(job.candidate_id, f"{type(exc).__name__}: {exc}")
            finally:
                self._release(job.key)
                if not job.candidate_id:
                    self._warmed(job.key)
                self.jobs.task_done()

    def _turn(self, job: Job) -> None:
        """Wait for this job's turn (F1): DEFER_S after it was queued (a gate's build does not wait for that), then
        outside a pass, one job per pass end or per IDLE_S without one."""
        if job.candidate_id:
            while (left := job.queued_at + DEFER_S - time.monotonic()) > 0:
                time.sleep(min(left, 1.0))
        with self.cv:
            while self.in_pass or (self.pass_ends == self._used_gap
                                   and time.monotonic() - self._last_turn < IDLE_S):
                self.cv.wait(timeout=IDLE_S)
            self._used_gap, self._last_turn = self.pass_ends, time.monotonic()

    def _release(self, key: tuple[str, int]) -> None:
        with self.lock:
            got = self.frames.get(key)
            if got is not None:
                if got[1] <= 1:
                    del self.frames[key]
                else:
                    self.frames[key] = (got[0], got[1] - 1)

    def fail(self, candidate_id: str, why: str) -> None:
        self.counts["failed"] += 1
        self.error, self.error_at = f"{candidate_id}: {why}"[:300], time.time()
        log.warning("shadow scoring of %s failed (%s): skipped, nothing else changes", candidate_id, why)

    def _models(self) -> dict[str, tuple[ServedModel, ...]]:
        now = time.monotonic()
        if self.loaded_at is None or now - self.loaded_at >= RELOAD_S:
            models, problems = load_models(self.db, self.broker)   # raises: no models, retried at the next job
            self.models, self.loaded_at = models, now
            if problems and problems != self.problems:
                log.warning("shadow scoring: %d model row(s) refused: %s", len(problems), "; ".join(problems)[:800])
            self.problems = problems
        return self.models

    def _vector(self, job: Job) -> Snapshot | None:
        from goldbot.research import feature_snapshots as fs
        key = job.key
        built, snap = self.cached(*key)
        if not built:
            with self.lock:
                got = self.frames.get(key)
            if got is None:
                raise RuntimeError("no 1m frame held for the decision")
            snap = fs.decision_features(got[0], job.tf, job.decision)
            with self.lock:
                self.vectors[key] = snap
                while len(self.vectors) > VECTORS_KEPT:
                    self.vectors.popitem(last=False)
        return snap

    def run(self, job: Job) -> None:
        """Score one job (the worker; tests call it directly). Raises only to `_loop`, which logs and skips. A job
        without a candidate id only loads the models and builds the vector a gate waits for."""
        try:
            models = self._models().get(job.arm_id)
        except Exception as exc:              # noqa: BLE001 - an unreadable table scores nothing this round
            self.models = {}
            self.fail(job.candidate_id or job.arm_id, f"model table unreadable: {type(exc).__name__}")
            return
        if not job.candidate_id:
            if gating_models(models or ()):
                self._vector(job)
            return
        if not models:
            self.counts["no_model"] += 1
            return
        decision_ns = int(job.decision.value)
        if all(decision_ns <= m.cutoff_ns for m in models):
            self.counts["not_scored"] += 1
            return
        snap = self._vector(job)
        if snap is None:
            self.fail(job.candidate_id, "no feature vector at the decision (too few bars)")
            return
        scored, skipped = score(models, snap, job.side, decision_ns)
        if not scored:
            self.counts["not_scored"] += 1
            if skipped:
                log.info("shadow scoring of %s: no model scored (%s)", job.candidate_id, "; ".join(skipped)[:300])
            return
        write(self.db, job.candidate_id, decision_ns, snap, scored)
        self.counts["scored"] += 1
        self.counts["scores"] += len(scored)

    def _warmed(self, key: tuple[str, int]) -> None:
        with self.lock:
            self.warming.discard(key)


def _bar_s(tf: str) -> float:
    from goldbot.config import tf_seconds
    try:
        return float(tf_seconds(tf))
    except Exception:                         # noqa: BLE001 - an unknown timeframe: a day
        return 86_400.0


def mark_pass(ex: Exploration | None, running: bool) -> None:
    """`exploration.on_pass` / `on_pass_slow` bracket their work with this (F1); never raises."""
    sc: ShadowScorer | None = getattr(ex, "shadow_scorer", None)
    if sc is not None:
        try:
            sc.mark_pass(running)
        except Exception:                     # noqa: BLE001 - a mark never stops a pass
            log.warning("shadow scoring: pass mark failed")


def scorer(eng: Engine, ex: Exploration) -> ShadowScorer:
    """The block's scorer, started at first use."""
    sc: ShadowScorer | None = getattr(ex, "shadow_scorer", None)
    if sc is None:
        sc = ShadowScorer(ex.db, ex.broker)
        ex.shadow_scorer = sc
    return sc


def submit(eng: Engine, ex: Exploration, arm: Arm, s: Pending, candidate_id: str, decision: pd.Timestamp) -> None:
    """The engine's one call (`exploration.candidate`, after the row is written): queue the candidate for scoring when
    shadow scoring is on and the arm trades gold. Returns nothing and never raises: no decision can depend on it."""
    try:
        if not eng.cfg.shadow_score or arm.instrument != GOLD:
            return
        scorer(eng, ex).submit(Job(candidate_id=candidate_id, arm_id=arm.arm_id, tf=arm.tf, side=int(s.side),
                                   decision=pd.Timestamp(decision).tz_convert("UTC")), eng.bars_1m)
    except Exception as exc:                  # noqa: BLE001 - scoring never reaches the engine's path
        log.warning("shadow scoring: candidate %s not queued (%s)", candidate_id, type(exc).__name__)


# ---------------------------------------------------------------------------------------------- the demo gate
class GateVerdict(FrozenRecord):
    """The gate's answer for one candidate: kept by the model, or rejected and then sampled (sent anyway, propensity
    EXPLORE_P) or refused."""
    keep: bool
    sample: bool
    score: float
    model_version: str
    propensity: float | None = None          # EXPLORE_P on every rejected candidate
    u: float | None = None                   # the draw (`explore_u(candidate_id)`), journalled (F6)


def gating_models(models: tuple[ServedModel, ...]) -> tuple[ServedModel, ...]:
    """The models whose spec may gate (GATE_SPECS; none today)."""
    return tuple(m for m in models if m.spec_version in GATE_SPECS)


def explore_u(candidate_id: str) -> float:
    """The candidate's seeded exploration draw in [0, 1): SHA-256 of a salted id (independent of `exploration.draw`)."""
    import hashlib
    return int(hashlib.sha256(f"model_explore|{candidate_id}".encode()).hexdigest()[:13], 16) / float(16 ** 13)


def gate_applies(eng: Engine, arm: Arm, s: Pending) -> bool:
    """Static eligibility (module docstring): the account's flag on a demo engine, a gold strategy or learning arm's
    first unit; never a control, replay_only or pyramid add. The demo predicate is read again at the gate."""
    from goldbot.research.arm_rules_spec import SPEC_CONTROL
    spec = arm.spec
    return bool(eng.cfg.model_gate) and eng.cfg.mode == "demo" and arm.instrument == GOLD \
        and arm.tf not in GATE_EXCLUDED_TFS \
        and spec.role in GATED_ROLES and arm.control_share is None and spec.control_mode != "replay_only" \
        and spec.rule != SPEC_CONTROL and s.group_id is None


def gate_waits(eng: Engine, ex: Exploration, s: Pending, now: pd.Timestamp) -> bool:
    """True while a gated signal should stay queued for its vector (at most GATE_WAIT_S after its bar close); never
    raises (an error: no wait, the gate then fails open)."""
    try:
        from goldbot.engine import long_bars
        arm = ex.arms[s.arm_id]
        if not gate_applies(eng, arm, s) or not GATE_SPECS or now >= s.bar_ts + pd.Timedelta(seconds=GATE_WAIT_S):
            return False
        decision = pd.Timestamp(long_bars.origin(arm.tf, s.bar_ts, s.decision_utc)).tz_convert("UTC")
        return scorer(eng, ex).prewarm(arm.arm_id, arm.tf, decision, eng.bars_1m)
    except Exception as exc:                  # noqa: BLE001 - never holds an entry on an error
        log.warning("model gate: no wait for %s (%s)", s.arm_id, type(exc).__name__)
        return False


def gate(eng: Engine, ex: Exploration, arm: Arm, s: Pending, candidate_id: str) -> GateVerdict | None:
    """The candidate's verdict from its arm's gating model, or None: not gated (off, not eligible, no gating model,
    not demo) or a fail-open (no vector in time, an error). Never raises; reads only the cached vector."""
    sc: ShadowScorer | None = getattr(ex, "shadow_scorer", None)
    try:
        if sc is None or not GATE_SPECS or not gate_applies(eng, arm, s):
            return None
        models = gating_models(sc.models.get(arm.arm_id, ()))
        if not models:
            return None
        from goldbot.engine import arm_registry, long_bars
        if not arm_registry.demo_now(eng):
            return None
        decision = pd.Timestamp(long_bars.origin(arm.tf, s.bar_ts, s.decision_utc)).tz_convert("UTC")
        _built, snap = sc.cached(arm.tf, int(decision.value))
        if snap is None:
            return _open(sc, candidate_id, "no feature vector in time")
        scored, _skipped = score(models, snap, s.side, int(decision.value))
        if not scored:
            return _open(sc, candidate_id, "no gating model could score it")
        head = next(x for x in scored if x.primary)
        model = next(m for m in models if m.model_version == head.model_version).model
        if head.score >= float(model.tau):
            sc.counts["gate_kept"] += 1
            return GateVerdict(keep=True, sample=False, score=head.score, model_version=head.model_version)
        u = explore_u(candidate_id)
        sample = u < EXPLORE_P
        sc.counts["gate_sampled" if sample else "gate_refused"] += 1
        return GateVerdict(keep=False, sample=sample, score=head.score, model_version=head.model_version,
                           propensity=EXPLORE_P, u=u)
    except Exception as exc:                  # noqa: BLE001 - fail open: the existing rule decides
        return _open(sc, candidate_id, f"{type(exc).__name__}: {exc}") if sc is not None else None


def _open(sc: ShadowScorer, candidate_id: str, why: str) -> GateVerdict | None:
    """A fail-open: counted, published and logged; None (the existing rule decides)."""
    sc.counts["gate_fail_open"] += 1
    sc.error, sc.error_at = f"gate {candidate_id}: {why}"[:300], time.time()
    log.warning("model gate failed open for %s (%s): the existing rule decides", candidate_id, why)
    return None


def published(eng: Engine) -> dict[str, Any]:
    """For the engine state file: the gold book's scoring counts, the models served and refused, and the last failure
    while it is recent. Nothing until a candidate was queued."""
    ex = getattr(getattr(eng, "primary", None), "exploration", None) or getattr(eng, "exploration", None)
    sc: ShadowScorer | None = getattr(ex, "shadow_scorer", None)
    if sc is None:
        return {}
    out: dict[str, Any] = {**sc.counts, "queued": sc.jobs.qsize(), "frames": len(sc.frames),
                           "models": sum(len(m) for m in sc.models.values()), "refused_models": len(sc.problems)}
    if sc.error is not None and sc.error_at is not None and time.time() - sc.error_at <= ERROR_WINDOW_S:
        out |= {"last_error": sc.error, "last_error_at": float(sc.error_at)}
    return {"shadow_scoring": out}
