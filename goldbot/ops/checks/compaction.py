"""Store compaction health (BACKLOG 242, follow-up of R146): the nightly `journal_compact` job is time-boxed, so
partitions it cannot reach are `deferred` to the next night, which is not a job failure (`job:journal_compact` stays
ok). A store whose backlog grows faster than one night drains would therefore stay silent, and a killed run can leave
DuckDB's spill files in `<data_root>/.compact_tmp` (goldbot/data/compact.py SPILL_DIR) on the brain's small disk.

  deferring   a table whose `deferred_streak` in the job's last result (goldbot.data.compact.deferred_streaks:
              consecutive runs with `deferred > 0`) is DEFERRED_RUNS_WARN or more: WARN naming the table and the count
  spill       `.compact_tmp` exists while no journal_compact run is in flight and nothing in it changed for
              SPILL_STALE_H: WARN naming its age and size

Not judged until compaction is in use (the job finished once, or a spill directory exists): `compaction_in_use`, the
registry's `when`, so the report gains the row only on a host that compacts. Scheduler status that cannot be read is
"can't check" (a WARN), never a pass. Reads only. Recovery: docs/RUNBOOK.md "Store compaction".

Imported by goldbot/ops/health.py (which re-exports the check): that import registers it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from goldbot.data.compact import SPILL_DIR
from goldbot.ops.checks._base import JOB_RUNNING_FAIL_H, Check, _age
from goldbot.ops.checks.registry import BLOCK_TAIL, register_check

if TYPE_CHECKING:
    from goldbot.ops.health import HealthContext

NAME = "compaction"
JOB = "journal_compact"
DEFERRED_RUNS_WARN = 3          # three nights in a row the time box ran out: the backlog is not draining
SPILL_STALE_H = 6.0             # far past the 30 min time box: no run of ours is still writing it


def _scheduler_doc(state_dir: Path) -> dict[str, Any] | None:
    """aux.db's scheduler status, else scheduler.json; None when there is none. Raises ValueError or OSError."""
    from goldbot.db.bookkeeping import scheduler_status
    doc = scheduler_status(state_dir)
    if doc is None:
        f = state_dir / "scheduler.json"
        if not f.exists():
            return None
        doc = json.loads(f.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or not isinstance(doc.get("jobs", {}), dict):
        raise ValueError("scheduler status is not an object")
    return doc


def _spill_dir(ctx: HealthContext) -> Path | None:
    return Path(ctx.settings.data_root).expanduser() / SPILL_DIR if ctx.settings is not None else None


def compaction_in_use(ctx: HealthContext) -> bool:
    """The registry's `when`: the job finished at least once, a spill directory exists, or the status is unreadable
    (judged as "can't check"). Never raises: run_checks calls it unguarded."""
    try:
        spill = _spill_dir(ctx)
        if spill is not None and spill.exists():
            return True
        doc = _scheduler_doc(ctx.state_dir)
    except Exception:
        return True
    job = (doc or {}).get("jobs", {}).get(JOB)
    return isinstance(job, dict) and bool(job.get("last_finished"))


def _running(job: dict[str, Any], now: pd.Timestamp) -> bool:
    """A run is in flight: started after it last finished, and not so long ago that the scheduler must have died
    mid-job (that is job:journal_compact's own FAIL, JOB_RUNNING_FAIL_H)."""
    started = job.get("last_started")
    if not started:
        return False
    s = pd.Timestamp(started)
    finished = job.get("last_finished")
    if finished and pd.Timestamp(finished) >= s:
        return False
    return (now - s).total_seconds() < JOB_RUNNING_FAIL_H * 3600


def _spill_state(spill: Path) -> tuple[float, int]:
    """(newest mtime in the directory or its direct entries, total bytes of those entries)."""
    newest, size = spill.stat().st_mtime, 0
    for p in spill.iterdir():
        st = p.stat()
        newest = max(newest, st.st_mtime)
        size += st.st_size if p.is_file() else 0
    return newest, size


@register_check("global", block=BLOCK_TAIL, pos=165, when=compaction_in_use)
def check_compaction(ctx: HealthContext) -> Check:
    """BACKLOG 242: WARN when journal_compact deferred a table on DEFERRED_RUNS_WARN runs in a row, or left its spill
    directory behind (module docstring)."""
    try:
        doc = _scheduler_doc(ctx.state_dir)
    except (ValueError, OSError) as exc:
        return Check(name=NAME, status="warn", reason=f"can't check: scheduler status unreadable ({type(exc).__name__})")
    job = (doc or {}).get("jobs", {}).get(JOB)
    job = job if isinstance(job, dict) else {}
    problems: list[str] = []
    notes: list[str] = []
    result = job.get("last_result")
    tables = result.get("tables") if isinstance(result, dict) else None
    for table, summary in sorted(tables.items()) if isinstance(tables, dict) else ():
        if not isinstance(summary, dict):
            continue
        streak, deferred = summary.get("deferred_streak"), summary.get("deferred")
        if isinstance(streak, int) and streak >= DEFERRED_RUNS_WARN:
            problems.append(f"{table}: {deferred} partition(s) deferred, {streak} runs in a row (time box "
                            "compact.time_box_s too short for the backlog)")
        elif isinstance(deferred, int) and deferred > 0:
            notes.append(f"{table}: {deferred} deferred ({streak or 1} run(s) in a row)")
    spill = _spill_dir(ctx)
    if spill is not None and spill.is_dir():
        try:
            newest, size = _spill_state(spill)
        except OSError as exc:
            return Check(name=NAME, status="warn", reason=f"can't check: {SPILL_DIR} unreadable ({type(exc).__name__})")
        idle_s = (ctx.now - pd.Timestamp(newest, unit="s", tz="UTC")).total_seconds()
        if _running(job, ctx.now):
            notes.append(f"{SPILL_DIR} in use by the running {JOB}")
        elif idle_s > SPILL_STALE_H * 3600:
            problems.append(f"{SPILL_DIR} left under data_root, unchanged for {_age(idle_s)} "
                            f"({size / 1e6:.1f} MB) and no {JOB} run in flight (a killed run's spill files)")
        else:
            notes.append(f"{SPILL_DIR} present, {_age(idle_s)} old")
    if problems:
        return Check(name=NAME, status="warn", reason="; ".join(problems))
    return Check(name=NAME, status="ok", reason="; ".join(notes) or "compaction keeps up; no spill directory left")
