"""The self-retrain health check (owner 2026-11-24; goldbot/research/self_retrain.py).

  self_retrain   WARN when the weekly retrain is switched off, has not run for more than 8 days, its newest run failed,
                 or it trained on 0 labels with features ("learning must never be silently off": memory
                 learning-never-silently-off, verify-learning-outputs). Also WARN, judged by output (S5, plan
                 2026-11-25 W1-C; `extra_warnings`): the newest run started more than its max_late_hours after its
                 slot (11-24: 40.9 h late against 6 h, reported ok), it trained on under MIN_LABEL_USE of its labels
                 (11-20: 812 of 9,289), a broker had 0 labels (vantage), or 0 models are active (nothing it learnt
                 reaches a decision). WARN only, never FAIL: an honest gate that activates nothing must not be pushed
                 to loosen (statistical gates are not limits); a run that stops altogether FAILs through
                 learning:self_retrain. The reason names the run it comes from; these WARNs are expected until the
                 label unlock (#451, plan W1-D) and sr-v3 (W3-2) land. Otherwise ok, with the label, feature, arm, weight and active-model counts.

Present once the job has stored a run, or whenever the job is switched off; before the first run, job:self_retrain
(the scheduler's check, `run_if_never_run`) says whether the job ran. Reads aux.db read-only. No order path.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

import pandas as pd

from goldbot.ops.checks._base import Check
from goldbot.ops.checks.registry import BLOCK_TAIL, register_check

if TYPE_CHECKING:
    from goldbot.ops.health import HealthContext

NAME = "self_retrain"
MIN_LABEL_USE = 0.5     # SRE plan item 1: "labels-used/labels-available is under 50%" warns


def _brain(ctx: HealthContext) -> bool:
    """Only where the exploration store lives (aux.db) and settings loaded."""
    from goldbot.db.connection import db_path
    return ctx.settings is not None and db_path(ctx.state_dir, "aux").exists()


@register_check("global", block=BLOCK_TAIL, pos=162, when=_brain)
def check_self_retrain(ctx: HealthContext) -> Check | None:
    """WARN on an off, missed, failed or empty weekly self-retrain (module docstring)."""
    from goldbot.db.connection import db_path
    from goldbot.research import self_retrain as sr
    assert ctx.settings is not None
    enabled = ctx.settings.research.self_retrain.enabled
    try:
        con = sqlite3.connect(f"file:{db_path(ctx.state_dir, 'aux')}?mode=ro", uri=True, timeout=5)
        try:
            runs = sr.latest_runs(con)
        finally:
            con.close()
    except sqlite3.Error as exc:
        return Check(name=NAME, status="warn", reason=f"can't read self_retrain_runs: {type(exc).__name__}")
    if enabled and not runs:
        return None
    status, reason = sr.health(runs, ctx.now, enabled=enabled)
    sch = getattr(ctx.settings.scheduler, "self_retrain", None)
    extra = extra_warnings(runs, max_late_h=float(sch.max_late_hours) if sch is not None else 6.0) if enabled else []
    if extra:          # dated (SRE review of #456 M4): the run they come from, so a stale WARN reads as one
        run = pd.Timestamp(max(int(r["run_ns"]) for r in runs), unit="ns", tz="UTC")
        return Check(name=NAME, status="warn",
                     reason=(f"run of {run:%Y-%m-%d}: " + "; ".join(extra) + f"; {reason}")[:900])
    return Check(name=NAME, status="ok" if status == "ok" else "warn", reason=reason[:600])


def extra_warnings(runs: Sequence[Mapping[str, Any]], *, max_late_h: float) -> list[str]:
    """The output rules of the newest run (module docstring), each a short reason; [] when none applies."""
    if not runs:
        return []
    out: list[str] = []
    late_h = max((int(r["run_ns"]) - int(r["slot_ns"])) / 3.6e12 for r in runs)
    if late_h > max_late_h:
        out.append(f"newest run started {late_h:.1f} h late (> {max_late_h:g} h)")
    labels = sum(int(r.get("n_labels") or 0) for r in runs)
    used = sum(int(r.get("n_with_features") or 0) for r in runs)
    if labels and used / labels < MIN_LABEL_USE:
        out.append(f"trained on {used} of {labels} labels ({100 * used / labels:.1f} % of labels, < "
                   f"{100 * MIN_LABEL_USE:.0f} %)")
    out += [f"{r['broker']}: 0 labels" for r in runs if not int(r.get("n_labels") or 0)]
    if not sum(int(r.get("n_active") or 0) for r in runs):
        out.append("0 active models: nothing it learnt reaches a decision")
    return out
