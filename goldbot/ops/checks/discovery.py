"""The discovery night's liveness check (ADR 0090, SRE GO 2026-11-18: "Telegram alerts on a failure and on a last
success older than 26 h, plus a health check").

A failed night already FAILs `job:discovery_nightly` (the job raises after `record_failed`), and a FAIL reaches the
owner through the health -> Telegram alert path like every other job. This check covers the silent case: the job is
enabled and no night has succeeded for DISCOVERY_STALE_H (one nightly slot, its 1.5 h max_late and the 2 h 10 min
window, with slack). It WARNs and is on NOTIFY_ON_WARN, so it is told: a stale research job is not a trading outage
and must not ping the dead man's switch or fail the deploy health gate, but it must not pass unseen either.

  successful night   an aux.db `discovery_runs` row with status ok, budget or over_budget (the night ran and recorded;
                     `killed` and `failed` rows are the job's failures)
  enabled lately    the scheduler's last slot ran the job disabled (a skip) BEFORE the settings changed (the enable,
                     `HealthContext.settings_changed`): ok for at most DISCOVERY_STALE_H from that change, its first
                     enabled night pending (a re-enable after a long pause is not stale). A disabled skip NEWER than
                     the change means the scheduler still runs the old settings: WARN "scheduler not restarted since
                     enable" (review on 2d0e11b8: otherwise pending held forever). Change time unknown: no pending
  no success yet     measured from the first recorded attempt; with no row at all, a WARN
  disabled           not checked at all (`discovery_enabled`): disabled means no alert

Reads only: aux.db read-only, scheduler state as check_scheduler reads it. No order path."""
from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

import pandas as pd

from goldbot.ops.checks._base import Check, _age, _read_json
from goldbot.ops.checks.registry import BLOCK_SCHEDULER, register_check

if TYPE_CHECKING:
    from goldbot.ops.health import HealthContext

NAME = "discovery_last_run"
JOB = "discovery_nightly"
DISCOVERY_STALE_H = 26.0
SUCCESS = ("ok", "budget", "over_budget")


def discovery_enabled(ctx: HealthContext) -> bool:
    """Enabled and not stopped (plan 2026-11-25 S6: `stopped` wins over a host's `enabled`)."""
    cfg = ctx.settings.research.discovery if ctx.settings is not None else None
    return cfg is not None and cfg.enabled and not cfg.stopped


def _runs(ctx: HealthContext) -> tuple[pd.Timestamp | None, pd.Timestamp | None, str | None]:
    """(newest successful run, first attempt of any status, the newest success's status) from aux.db, read-only."""
    from goldbot.db.connection import db_path
    p = db_path(ctx.state_dir, "aux")
    if not p.exists():
        return None, None, None
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
    try:
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='discovery_runs'").fetchone() is None:
            return None, None, None
        marks = ",".join("?" * len(SUCCESS))
        ok = con.execute(f"SELECT run_ns, status FROM discovery_runs WHERE status IN ({marks}) "
                         "ORDER BY run_ns DESC LIMIT 1", SUCCESS).fetchone()
        first = con.execute("SELECT MIN(run_ns) FROM discovery_runs").fetchone()[0]
    finally:
        con.close()

    def ts(ns: Any) -> pd.Timestamp | None:
        return None if ns is None else pd.Timestamp(int(ns), unit="ns", tz="UTC")
    return ts(ok[0] if ok else None), ts(first), (str(ok[1]) if ok else None)



def _job_state(ctx: HealthContext) -> dict[str, Any]:
    """The scheduler's state of the job, read as check_scheduler reads it ({} when none)."""
    from goldbot.db.bookkeeping import scheduler_status
    doc = scheduler_status(ctx.state_dir)
    if doc is None:
        f = ctx.state_dir / "scheduler.json"
        try:
            doc = _read_json(f) if f.exists() else None
        except (ValueError, OSError):
            doc = None
    jobs = (doc or {}).get("jobs") or {}
    return dict(jobs.get(JOB) or {})


def _disabled_skip_at(job: dict[str, Any]) -> pd.Timestamp | None:
    """When the scheduler last ran the job as disabled (its last result is the disabled skip), else None."""
    result = job.get("last_result") or {}
    if job.get("last_ok") is not True or "enabled is false" not in str(result.get("skipped", "")):
        return None
    at = job.get("last_finished") or job.get("last_slot")
    return pd.Timestamp(at) if at else None


@register_check("global", block=BLOCK_SCHEDULER, pos=20, when=discovery_enabled)
def check_discovery_last_run(ctx: HealthContext) -> Check:
    """WARN when the enabled discovery job's last successful night is older than DISCOVERY_STALE_H (module docstring)."""
    limit_s = DISCOVERY_STALE_H * 3600
    try:
        ok_at, first_at, status = _runs(ctx)
    except sqlite3.Error as exc:
        return Check(name=NAME, status="warn", reason=f"can't check: aux.db discovery_runs unreadable "
                                                       f"({type(exc).__name__})")
    age = (ctx.now - ok_at).total_seconds() if ok_at is not None else None
    if age is not None and age <= limit_s:
        return Check(name=NAME, status="ok", reason=f"last successful night {_age(age)} ago ({status})")
    skip, changed = _disabled_skip_at(_job_state(ctx)), ctx.settings_changed
    if skip is not None and changed is not None:
        if skip >= changed:
            since, ran = (ctx.now - changed).total_seconds(), (ctx.now - skip).total_seconds()
            return Check(name=NAME, status="warn",
                         reason=f"scheduler not restarted since enable: settings changed {_age(since)} ago, but "
                                f"job:{JOB} still ran disabled {_age(ran)} ago; restart the scheduler service")
        if (ctx.now - changed).total_seconds() <= limit_s:
            return Check(name=NAME, status="ok", reason=f"enabled since its last slot; first enabled night pending "
                                                        f"(job:{JOB} judges whether it runs)")
    if age is not None:
        return Check(name=NAME, status="warn",
                     reason=f"enabled, but the last successful discovery night was {_age(age)} ago (over "
                            f"{DISCOVERY_STALE_H:.0f} h): see job:{JOB} and state/autodisc/ (RUNBOOK 'Discovery "
                            "nightly on the brain'; rollback: research.discovery.enabled false)")
    if first_at is not None:
        age = (ctx.now - first_at).total_seconds()
        if age <= limit_s:
            return Check(name=NAME, status="ok", reason=f"no successful night yet; first attempt {_age(age)} ago "
                                                        f"(job:{JOB} shows its failure)")
        return Check(name=NAME, status="warn",
                     reason=f"enabled, but no successful discovery night since its first attempt {_age(age)} ago "
                            f"(over {DISCOVERY_STALE_H:.0f} h): see job:{JOB}")
    return Check(name=NAME, status="warn",
                 reason=f"enabled, but no discovery night is recorded (aux.db discovery_runs) and job:{JOB} has not "
                        "run disabled since: was it enabled on this host before the scheduler restarted?")
