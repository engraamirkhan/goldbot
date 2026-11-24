"""The self-retrain health check (owner 2026-11-24; goldbot/research/self_retrain.py).

  self_retrain   WARN when the weekly retrain is switched off, has not run for more than 8 days, its newest run failed,
                 or it trained on 0 labels with features ("learning must never be silently off": memory
                 learning-never-silently-off, verify-learning-outputs). Otherwise ok, with the label, feature, arm,
                 weight and active-model counts of the newest run.

Present once the job has stored a run, or whenever the job is switched off; before the first run, job:self_retrain
(the scheduler's check, `run_if_never_run`) says whether the job ran. Reads aux.db read-only. No order path.
"""
from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from goldbot.ops.checks._base import Check
from goldbot.ops.checks.registry import BLOCK_TAIL, register_check

if TYPE_CHECKING:
    from goldbot.ops.health import HealthContext

NAME = "self_retrain"


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
    return Check(name=NAME, status="ok" if status == "ok" else "warn", reason=reason[:600])
