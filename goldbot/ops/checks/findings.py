"""The findings register's health check (owner 2026-11-20, "feedback must close itself"; goldbot/ops/findings.py).

  findings   WARN while a critical or high row of config/findings.yaml is past its due date (UTC day) and its last
             stored status is not closed; a row the job never stored counts as not closed. WARN "can't check" when the
             register or the store cannot be read. Otherwise ok, with the counts.

Present only once the daily `findings_check` job has stored a run (aux.db findings_history): before that,
job:findings_check says whether the job runs. Not on NOTIFY_ON_WARN on purpose: the job already announces each
overdue row on the status issue (and on Telegram for critical/high) once, and again every findings.remind_days; a
standing WARN told every pass would be the self-inflicted alert noise the SRE audit of 2026-11-20 measured (63 %).
Reads only: the register file and aux.db read-only. No order path.
"""
from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from goldbot.ops.checks._base import Check
from goldbot.ops.checks.registry import BLOCK_TAIL, register_check

if TYPE_CHECKING:
    from goldbot.ops.health import HealthContext

NAME = "findings"


def findings_stored(ctx: HealthContext) -> bool:
    from goldbot.ops.findings import stored_latest
    try:
        return bool(stored_latest(ctx.state_dir))
    except (sqlite3.Error, ValueError):
        return True                       # present, and the check itself says it cannot read


@register_check("global", block=BLOCK_TAIL, pos=161, when=findings_stored)
def check_findings(ctx: HealthContext) -> Check:
    """WARN while a critical/high finding is past due and not closed (module docstring)."""
    from goldbot.ops import findings as fm
    try:
        reg = fm.load_register(fm.register_path(ctx.settings), agents_dir=None)
        stored = fm.stored_latest(ctx.state_dir) or {}
    except (OSError, ValueError, sqlite3.Error) as exc:
        return Check(name=NAME, status="warn", reason=f"can't check: register or store unreadable ({type(exc).__name__})")
    today = ctx.now.date()
    late = fm.urgent_past_due(reg, stored, today)
    statuses = [stored[f.id] for f in reg.findings if f.id in stored]
    if late:
        names = "; ".join(f"{f.id} ({f.severity}, {f.owner}, due {f.due})" for f in late)
        return Check(name=NAME, status="warn", reason=f"{len(late)} critical/high finding(s) past due: {names}")
    return Check(name=NAME, status="ok", reason=f"no critical/high finding past due; {fm.counts(statuses)}")
