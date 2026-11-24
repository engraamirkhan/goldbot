"""Serving the weekly self-retrain's ACTIVE per-arm filters at the exploration send (owner 2026-11-24: "self-retraining
is the core of the system"; goldbot/research/self_retrain.py writes them, aux.db `self_retrain_models`).

The design rule, from the pre-registration (docs/research/prereg-2026-11-24-self-retrain.md section 3, "What ACTIVE
may do"): "It may only drop candidates of a demo strategy arm before the RiskGate. It never adds an order, raises a
size, or touches a control, a `replay_only` arm, a risk limit or a live account." As built here:

* One call, in `exploration._send` after the engine-level entry refusals and before any budget, room, slot or cap check
  and the order core (`cost_probe._place`: `RiskGate.check`, the only path to an order). It returns a verdict or None;
  it never sends, closes, sizes or moves anything. A dropped candidate is recorded like every refused one (taken 0,
  mu 0) with blocked_reason `self_retrain:<model_version>` (quant requirement R11), so it is replay-labelled and stays
  in exploration_obs; every scored candidate, kept or dropped, carries its `meta_p` and `model_version`.
* Which arms (`eligible`): role `strategy` only, never a control (the random baseline the gate judges the filter
  against), a replay_only or spec control, a cost, learning or owner-experiment arm (GD-1's GD1D/GD1P are learning
  evidence arms the card does not cover), a pyramid add, a non-gold arm (the trainer is gold only), a 1m/5m arm (the
  vector's build does not fit the fast send path) or a 1d/1w arm (the engine's 90 000 minutes hold no 120 decision
  bars, so the trainer has no snapshot of one either).
* Where: an account `risk.gate.is_demo` says is demo, read at the signal (`arm_registry.demo_now`); serving switched on
  (`EngineConfig.meta_filter_serve`, production: settings research.self_retrain.enabled and .serve; off by default).
* What: the broker's models with status `active` (the table's status column is the only contract read: the job decides
  activation, demotion and supersession), from their `activation_ns` on. Re-read at most every RELOAD_S; a row that is
  not the trainer's current spec or inputs is skipped and named.
* The vector is the trainer's: `feature_snapshots.decision_features` (the nightly snapshot's own function) on the
  engine's minutes at the candidate's decision close, through `self_retrain.design` (signed columns, side), scored by
  the stored numbers (no pickle). It is built once per (timeframe, decision) and shared by every arm of it.
* Fail open: any error (the table unreadable, a vector that cannot be built, a non-finite score) leaves the candidate to
  the existing path, unfiltered; it is logged, counted and published (`published`: health WARNs on
  `meta_filter_error` for ERROR_WINDOW_S). Exits never pass here.
"""
from __future__ import annotations

import logging
import math
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

import pandas as pd
from pydantic import ValidationError

from goldbot.base import FrozenRecord
from goldbot.instruments import GOLD

if TYPE_CHECKING:
    from goldbot.db import Database
    from goldbot.engine.exploration import Arm, Exploration, Pending
    from goldbot.engine.runner import Engine
    from goldbot.research.feature_snapshots import Snapshot
    from goldbot.research.self_retrain import LogitModel

log = logging.getLogger("goldbot.meta_filter")

REFUSAL_PREFIX = "self_retrain:"     # blocked_reason of a dropped candidate: the prefix and the model version (R11)
RELOAD_S = 300.0                     # the active models are re-read at most this often (engine clock)
ERROR_WINDOW_S = 900.0               # health WARNs while the last fail-open is this recent (wall clock)
VECTORS_KEPT = 32                    # (timeframe, decision) vectors cached
FILTER_TFS = ("15m", "1h", "4h")     # see the module docstring: 1m/5m and 1d/1w are not served


def refusal(model_version: str) -> str:
    """The dropped candidate's blocked_reason and journal refusal (quant R11)."""
    return REFUSAL_PREFIX + model_version


class Verdict(FrozenRecord):
    """A scored candidate: dropped when its score is below the model's cut."""
    drop: bool
    meta_p: float
    model_version: str


class Served(FrozenRecord):
    """One ACTIVE model as served: its version, when it is in force from, and its numbers."""
    model_version: str
    activation_ns: int
    model: Any                       # research.self_retrain.LogitModel (imported lazily: the engine starts without it)


class MetaFilter:
    """The serving state of one exploration block: the broker's active models, the vectors built, the counts and the
    last fail-open (for health)."""

    def __init__(self) -> None:
        self.models: dict[str, Served] = {}
        self.loaded_at: pd.Timestamp | None = None
        self.vectors: OrderedDict[tuple[str, int], Snapshot | None] = OrderedDict()
        self.counts: dict[str, int] = {"scored": 0, "dropped": 0, "failed_open": 0}
        self.problems: list[str] = []
        self.error: str | None = None
        self.error_at: float | None = None

    def fail(self, arm_id: str, why: str) -> None:
        self.counts["failed_open"] += 1
        self.error, self.error_at = f"{arm_id}: {why}"[:300], time.time()
        log.warning("meta filter failed open for %s (%s): the candidate goes on unfiltered", arm_id, why)


def eligible(arm: Arm, s: Pending) -> bool:
    """A demo strategy arm's first-unit gold signal on 15m/1h/4h (module docstring)."""
    from goldbot.research.arm_rules_spec import SPEC_CONTROL
    spec = arm.spec
    return (spec.role == "strategy" and arm.control_share is None and spec.control_mode != "replay_only"
            and spec.rule != SPEC_CONTROL and arm.instrument == GOLD and arm.tf in FILTER_TFS and s.group_id is None)


def load_active(db: Database, broker: str) -> tuple[dict[str, Served], list[str]]:
    """The broker's ACTIVE models by arm, and the rows skipped (another spec or inputs, or unreadable numbers).
    Read-only; reads the status column, nothing of the job's internals."""
    from goldbot.research import self_retrain as sr
    with db.connection() as conn:
        rows = conn.execute("SELECT arm_id, model_version, spec_version, activation_ns, model FROM self_retrain_models "
                            "WHERE broker = ? AND status = 'active' ORDER BY status_ns", (broker,)).fetchall()
    out: dict[str, Served] = {}
    problems: list[str] = []
    for arm_id, version, spec, activation, body in rows:
        if spec != sr.SPEC_VERSION:
            problems.append(f"{arm_id} {version}: spec {spec} is not {sr.SPEC_VERSION}")
            continue
        try:
            model = sr.LogitModel.model_validate_json(body)
        except ValidationError as exc:
            problems.append(f"{arm_id} {version}: model unreadable ({exc.error_count()} errors)")
            continue
        n = len(sr.INPUTS)
        if model.inputs != sr.INPUTS or {len(model.medians), len(model.means), len(model.scales),
                                         len(model.coef)} != {n} or not all(
                math.isfinite(v) for v in (*model.coef, *model.scales, model.intercept, model.tau)):
            problems.append(f"{arm_id} {version}: not the frozen {n} inputs or non-finite numbers")
            continue
        out[str(arm_id)] = Served(model_version=str(version), activation_ns=int(activation), model=model)
    return out, problems


def _models(ex: Exploration, mf: MetaFilter, now: pd.Timestamp) -> dict[str, Served]:
    if mf.loaded_at is None or (now - mf.loaded_at).total_seconds() >= RELOAD_S or now < mf.loaded_at:
        mf.loaded_at = now
        mf.models = {}                                   # fail open: an unreadable table serves nothing
        mf.models, problems = load_active(ex.db, ex.broker)
        if problems and problems != mf.problems:
            log.warning("meta filter: %d active model row(s) skipped: %s", len(problems), "; ".join(problems)[:500])
        mf.problems = problems
    return mf.models


def _vector(eng: Engine, mf: MetaFilter, tf: str, at: pd.Timestamp) -> Snapshot | None:
    from goldbot.engine import books
    from goldbot.research import feature_snapshots as fs
    key = (tf, int(at.value))
    if key not in mf.vectors:
        with books.phase(eng, "exploration:meta_filter"):
            mf.vectors[key] = fs.decision_features(eng.bars_1m, tf, at)
        while len(mf.vectors) > VECTORS_KEPT:
            mf.vectors.popitem(last=False)
    return mf.vectors[key]


def check(eng: Engine, ex: Exploration, arm: Arm, s: Pending, now: pd.Timestamp) -> Verdict | None:
    """The candidate's verdict from its arm's ACTIVE model, or None (no model, not served here, or a fail-open: the
    candidate goes on exactly as without the filter). Never raises."""
    try:
        mf: MetaFilter | None = getattr(ex, "meta_filter", None)
        if mf is None or not eng.cfg.meta_filter_serve or not eligible(arm, s):
            return None
    except Exception:
        return None
    try:
        served = _models(ex, mf, now).get(arm.arm_id)
        if served is None or int(now.value) < served.activation_ns:
            return None
        from goldbot.engine import arm_registry, long_bars
        if not arm_registry.demo_now(eng):
            return None
        snap = _vector(eng, mf, arm.tf, long_bars.origin(arm.tf, s.bar_ts, s.decision_utc))
        if snap is None:
            mf.fail(arm.arm_id, "no feature vector at the decision (too few bars)")
            return None
        from goldbot.research import self_retrain as sr
        model: LogitModel = served.model
        p = float(model.score(sr.design(pd.DataFrame([{**snap.features, "side": float(s.side)}])))[0])
        if not math.isfinite(p):
            mf.fail(arm.arm_id, "non-finite score")
            return None
        drop = p < model.tau
        mf.counts["scored"] += 1
        mf.counts["dropped"] += int(drop)
        return Verdict(drop=drop, meta_p=p, model_version=served.model_version)
    except Exception as exc:                 # fail open: the existing path decides, never the filter's fault
        mf.fail(arm.arm_id, f"{type(exc).__name__}: {exc}")
        return None


def published(eng: Engine) -> dict[str, Any]:
    """For the engine state file: the primary book's serving counts, and its last fail-open while it is recent (health
    WARNs on `meta_filter_error`). Nothing without an exploration block."""
    ex = getattr(getattr(eng, "primary", None), "exploration", None) or getattr(eng, "exploration", None)
    mf: MetaFilter | None = getattr(ex, "meta_filter", None)
    if mf is None:
        return {}
    out: dict[str, Any] = {"meta_filter": {**mf.counts, "active_models": len(mf.models),
                                           "skipped_models": len(mf.problems)}}
    if mf.error is not None and mf.error_at is not None and time.time() - mf.error_at <= ERROR_WINDOW_S:
        out |= {"meta_filter_error": mf.error, "meta_filter_error_at": float(mf.error_at)}
    return out
