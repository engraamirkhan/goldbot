"""The feedback-loop health check (R140, review loop item 1, 2026-11-13): FAIL when a writer the learning loop
depends on is stale or failing, so a silent loop is an alert, not a discovery.

  weights      the nightly learner's bandit weights (aux.db exploration_broker_weights.run_ns) per broker, older than
               WEIGHTS_STALE_H counted in weekday hours (the learner runs Mon-Fri with nightly_costs), or none at all
               for a broker whose exploration trades closed
  reviews      state/aux/daily_review/<day>.json for the daily_review job's latest due slot, and the Monday run's
               weekly-<sunday>.json
  proposals    a weekly roll-up with variant proposals that no consumer marked read (daily_review.mark_weekly_consumed)
               within PROPOSALS_UNREAD_DAYS of being written
  registry     the latest trial-registry sync a job reported (`registry_sync` in its result) failed, or rows waited in
               state/registry_outbox/ longer than OUTBOX_STALE_H for the Mac publish (scripts/registry_publish.sh).
               Incident 2026-11-13: the brain token is read-only by decision (2026-11-01), the outbox is published by
               hand, and rows waited six days behind an INFO log line; monthly_research and cpcv_quarterly carried a
               bare HTTP 403 in their results with no alert
  jobs         a scheduler job whose last run errored (also job:<name>; listed here so the ci-loop issue names it)

State that cannot be read is "can't check": a WARN that reaches the owner (NOTIFY_ON_WARN), never a pass. A writer
not in use yet (no aux.db, a job that never ran) is not judged. Reads only: files and aux.db read-only; no order path.

The health alert path carries the Check; the daily `loop_check` scheduler job (`run_loop_check`) also opens or comments
on the GitHub issue labelled `ci-loop` through the daily review's issue path (once a day, text scrubbed of logins and
hosts; without a token or on a failed post the text goes to state/aux/daily_review/outbox/).
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.ops.checks._base import Check, Status, _age
from goldbot.ops.checks.registry import BLOCK_TAIL, register_check

if TYPE_CHECKING:
    from goldbot.config import Settings
    from goldbot.ops.github_issues import IssueSink
    from goldbot.ops.health import HealthContext

NAME = "feedback_loop"
ISSUE_LABEL = "ci-loop"
WEIGHTS_STALE_H = 36.0          # weekday hours: one missed nightly learner run plus a working day's slack
REVIEW_GRACE_H = 12.0           # the daily_review job's max_late_hours: after it the slot is skipped, not late
PROPOSALS_UNREAD_DAYS = 8.0     # the research director runs Saturday; a Monday roll-up unread a week later is lost
PROPOSALS_LOOKBACK_DAYS = 35.0  # older roll-ups are moot: their proposals are judged stale with them
OUTBOX_STALE_H = 36.0           # registry rows waiting for the Mac publish
ERROR_CHARS = 200
REGISTRY_JOBS_HINT = "tournament, research_director"   # the jobs that sync it (BACKLOG 142 retired the rest)

Verdict = Literal["ok", "fail", "cant_check"]
_LONG_NUMBER = re.compile(r"\d{6,}")
_HOST = re.compile(r"\b\d{1,3}(\.\d{1,3}){3}\b")


class LoopItem(FrozenRecord):
    writer: str
    verdict: Verdict
    reason: str


def _clean(text: str, limit: int = ERROR_CHARS) -> str:
    """First line, ids and hosts masked: the text reaches a public issue (a 6+ digit run would also drop the whole
    line in daily_review.scrub, e.g. a release id in a GitHub URL)."""
    first = (text.strip().splitlines() or [""])[0]
    return _HOST.sub("<host>", _LONG_NUMBER.sub("<n>", first))[:limit]


def _weekday_hours(start: pd.Timestamp, end: pd.Timestamp) -> float:
    """Hours between `start` and `end` that fall on Monday-Friday (UTC)."""
    if end <= start:
        return 0.0
    total, t = 0.0, start
    while t < end:
        nxt = min(end, t.normalize() + pd.Timedelta(days=1))
        if t.dayofweek < 5:
            total += (nxt - t).total_seconds() / 3600
        t = nxt
    return total


# --------------------------------------------------------------------------------------------- the writers
def _scheduler_doc(state_dir: Path) -> dict[str, Any] | None:
    """The scheduler's status document (aux.db row, else scheduler.json); None when there is none at all. Raises
    ValueError or OSError when it cannot be read."""
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


def _weights(state_dir: Path, now: pd.Timestamp) -> LoopItem:
    p = state_dir / "aux.db"
    if not p.exists():
        return LoopItem(writer="weights", verdict="ok", reason="no aux.db: exploration not in use")
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
        try:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if "exploration_broker_weights" not in tables:
                return LoopItem(writer="weights", verdict="ok", reason="no bandit weights table: learner not in use")
            runs = dict(con.execute("SELECT broker, MAX(run_ns) FROM exploration_broker_weights "
                                    "WHERE broker IS NOT NULL GROUP BY broker").fetchall())
            traded: set[str] = set()
            if "exploration_obs" in tables:
                traded = {str(b) for (b,) in con.execute(
                    "SELECT DISTINCT broker FROM exploration_obs WHERE taken = 1 AND exit_ns IS NOT NULL "
                    "AND broker IS NOT NULL")}
        finally:
            con.close()
    except sqlite3.Error as exc:
        return LoopItem(writer="weights", verdict="cant_check", reason=f"aux.db unreadable ({type(exc).__name__})")
    if not runs and not traded:
        return LoopItem(writer="weights", verdict="ok", reason="no exploration trades yet")
    bad: list[str] = []
    good: list[str] = []
    for broker in sorted(set(runs) | traded):
        ns = runs.get(broker)
        if ns is None:
            bad.append(f"{broker}: no bandit weights ever, though its exploration trades closed")
            continue
        at = pd.Timestamp(int(ns), tz="UTC")
        hours = _weekday_hours(at, now)
        (bad if hours > WEIGHTS_STALE_H else good).append(
            f"{broker}: weights {at:%Y-%m-%d %H:%M} ({hours:.0f} weekday h)")
    if bad:
        return LoopItem(writer="weights", verdict="fail",
                        reason=f"bandit weights older than {WEIGHTS_STALE_H:.0f} weekday h: " + "; ".join(bad)
                        + " (nightly_costs -> exploration_learn)")
    return LoopItem(writer="weights", verdict="ok", reason="; ".join(good))


def _latest_slot(now: pd.Timestamp, at: str, weekdays: list[int], grace_h: float,
                 want_dow: int | None = None) -> pd.Timestamp | None:
    """The latest daily slot at `at` UTC on `weekdays` whose grace has passed by `now`; `want_dow`: only slots whose
    reviewed day (day_before) falls on that weekday."""
    from goldbot.ops.report_days import day_before
    hh, mm = (int(x) for x in at.split(":"))
    cutoff = now - pd.Timedelta(hours=grace_h)
    day = cutoff.normalize()
    for _ in range(15):
        slot = day + pd.Timedelta(hours=hh, minutes=mm)
        if slot <= cutoff and slot.dayofweek in weekdays and \
                (want_dow is None or pd.Timestamp(day_before(slot)).dayofweek == want_dow):
            return slot
        day -= pd.Timedelta(days=1)
    return None


def _reviews(state_dir: Path, now: pd.Timestamp, jobs: dict[str, Any], settings: Settings | None) -> LoopItem:
    from goldbot.ops.report_days import day_before
    from goldbot.research.daily_review import review_path, weekly_path
    job = jobs.get("daily_review")
    if not isinstance(job, dict) or not job.get("last_finished"):
        return LoopItem(writer="reviews", verdict="ok", reason="daily_review has not run yet")
    result = job.get("last_result")
    if isinstance(result, dict) and "skipped" in result:
        return LoopItem(writer="reviews", verdict="ok", reason=f"daily_review skipped: {_clean(str(result['skipped']))}")
    sched = settings.scheduler.daily_review if settings is not None else None
    at, weekdays = (sched.at, list(sched.weekdays)) if sched is not None else ("06:15", list(range(7)))
    grace = sched.max_late_hours if sched is not None else REVIEW_GRACE_H
    missing: list[str] = []
    have: list[str] = []
    slot = _latest_slot(now, at, weekdays, grace)
    if slot is not None:
        day = day_before(slot)
        (have if review_path(state_dir, day).exists() else missing).append(f"{day}.json")
    wslot = _latest_slot(now, at, weekdays, grace, want_dow=6)
    if wslot is not None:
        name = weekly_path(state_dir, day_before(wslot)).name
        (have if (state_dir / "aux" / "daily_review" / name).exists() else missing).append(name)
    if missing:
        return LoopItem(writer="reviews", verdict="fail",
                        reason=f"review file missing for its slot: {', '.join(missing)} (job daily_review)")
    return LoopItem(writer="reviews", verdict="ok", reason=f"{', '.join(have) or 'no slot due yet'} present")


def _proposals(state_dir: Path, now: pd.Timestamp) -> LoopItem:
    from goldbot.base import load_record_json
    from goldbot.research.daily_review import WeeklyRollup, review_root, weekly_consumed
    root = review_root(state_dir)
    files = sorted(root.glob("weekly-*.json")) if root.is_dir() else []
    if not files:
        return LoopItem(writer="proposals", verdict="ok", reason="no weekly roll-up yet")
    try:
        read = weekly_consumed(state_dir)
    except (ValueError, OSError) as exc:
        return LoopItem(writer="proposals", verdict="cant_check", reason=f"consumed.json unreadable ({type(exc).__name__})")
    unread, bad = [], []
    for f in files:
        try:
            week = load_record_json(WeeklyRollup, f.read_text(encoding="utf-8"), source=f.name)
        except (ValueError, OSError):
            bad.append(f.name)
            continue
        age_d = (now - week.generated_utc).total_seconds() / 86400
        if week.proposals and f.name not in read and PROPOSALS_UNREAD_DAYS < age_d <= PROPOSALS_LOOKBACK_DAYS:
            unread.append(f"{f.name} ({len(week.proposals)} proposal(s), written {age_d:.0f} d ago)")
    if unread:
        return LoopItem(writer="proposals", verdict="fail",
                        reason="weekly proposals unread past " f"{PROPOSALS_UNREAD_DAYS:.0f} d: " + "; ".join(unread)
                        + " (consumer: research director, daily_review.mark_weekly_consumed)")
    if bad:
        return LoopItem(writer="proposals", verdict="cant_check", reason=f"weekly roll-up unreadable: {', '.join(bad)}")
    return LoopItem(writer="proposals", verdict="ok", reason="every weekly proposal read in time")


def _sync_value(v: Any) -> Any:
    """A job's `registry_sync` result: the last sync it ran (`after` over `before` for the monthly loop)."""
    if isinstance(v, dict):
        return v["after"] if v.get("after") is not None else v.get("before")
    return v


def outbox_pending_since(outbox: Path) -> pd.Timestamp | None:
    """When the registry outbox's rows first waited: its `pending_since` stamp, else its oldest entry's mtime; None
    when nothing waits. Raises ValueError or OSError when it cannot be read."""
    from goldbot.research.registry_sync import OUTBOX_SINCE, _outbox_files
    entries = _outbox_files(outbox)
    if not entries:
        return None
    marker = outbox / OUTBOX_SINCE
    if marker.exists():
        return pd.Timestamp(marker.read_text(encoding="utf-8").strip()).tz_convert("UTC")
    return pd.Timestamp(min(p.stat().st_mtime for p in entries), unit="s", tz="UTC")


def _registry(state_dir: Path, now: pd.Timestamp, jobs: dict[str, Any]) -> LoopItem:
    from goldbot.research.registry_sync import OUTBOX
    reports = []
    for name, job in jobs.items():
        res = job.get("last_result") if isinstance(job, dict) else None
        if isinstance(res, dict) and "registry_sync" in res and job.get("last_finished"):
            reports.append((pd.Timestamp(job["last_finished"]), name, _sync_value(res["registry_sync"])))
    problems = []
    if reports:
        at, name, value = max(reports, key=lambda r: r[0])
        if isinstance(value, str) and value.startswith("failed"):
            problems.append(f"latest sync ({name}, {at:%Y-%m-%d %H:%M}) {_clean(value)}")
    try:
        since = outbox_pending_since(state_dir / OUTBOX)
    except (ValueError, OSError) as exc:
        return LoopItem(writer="registry", verdict="cant_check",
                        reason=f"registry outbox unreadable ({type(exc).__name__})"
                        + (f"; {problems[0]}" if problems else ""))
    if since is not None and (waited := (now - since).total_seconds()) > OUTBOX_STALE_H * 3600:
        problems.append(f"registry rows waiting {_age(waited)} in {OUTBOX}/ for the Mac publish: run "
                        "scripts/registry_publish.sh on the Mac (the brain token cannot write the release)")
    if problems:
        return LoopItem(writer="registry", verdict="fail", reason="trial registry sync failing: " + "; ".join(problems))
    if not reports:
        return LoopItem(writer="registry", verdict="ok", reason=f"no sync reported yet ({REGISTRY_JOBS_HINT})")
    return LoopItem(writer="registry", verdict="ok",
                    reason="synced" + (f"; outbox waiting {_age((now - since).total_seconds())}" if since else ""))


def _errored(jobs: dict[str, Any]) -> LoopItem:
    bad = [f"{n}: {_clean(str(j.get('last_error') or '?'), 120)}" for n, j in sorted(jobs.items())
           if isinstance(j, dict) and j.get("last_ok") is False]
    if bad:
        return LoopItem(writer="jobs", verdict="fail", reason="last run errored: " + "; ".join(bad))
    return LoopItem(writer="jobs", verdict="ok", reason="no job's last run errored")


def loop_items(state_dir: Path, now: pd.Timestamp, settings: Settings | None) -> list[LoopItem]:
    """Every writer's verdict, in a fixed order."""
    out = [_weights(state_dir, now)]
    try:
        doc = _scheduler_doc(state_dir)
    except (ValueError, OSError) as exc:
        out.append(LoopItem(writer="scheduler", verdict="cant_check",
                            reason=f"scheduler status unreadable ({type(exc).__name__}): reviews, registry sync and "
                                   "jobs not judged"))
        out.append(_proposals(state_dir, now))
        return out
    if doc is None:
        out.append(LoopItem(writer="scheduler", verdict="cant_check", reason="no scheduler status: never started"))
        out.append(_proposals(state_dir, now))
        return out
    jobs: dict[str, Any] = doc.get("jobs") or {}
    out += [_reviews(state_dir, now, jobs, settings), _proposals(state_dir, now), _registry(state_dir, now, jobs),
            _errored(jobs)]
    return out


def summarise(items: list[LoopItem]) -> Check:
    fails = [i for i in items if i.verdict == "fail"]
    cant = [i for i in items if i.verdict == "cant_check"]
    status: Status = "fail" if fails else "warn" if cant else "ok"
    if status == "ok":
        return Check(name=NAME, status="ok", reason="; ".join(f"{i.writer}: {i.reason}" for i in items))
    parts = [f"{i.writer}: {i.reason}" for i in fails] + [f"can't check {i.writer}: {i.reason}" for i in cant]
    return Check(name=NAME, status=status, reason="; ".join(parts))


@register_check("global", block=BLOCK_TAIL, pos=160)
def check_feedback_loop(ctx: HealthContext) -> Check:
    """R140: every feedback writer fresh and succeeding (module docstring); FAIL names each that is not."""
    return summarise(loop_items(ctx.state_dir, ctx.now, ctx.settings))


# --------------------------------------------------------------------------------------------- the daily job
def issue_body(check: Check, now: pd.Timestamp) -> str:
    from goldbot.research.daily_review import scrub
    lines = [f"Feedback-loop health check `{NAME}` is **{check.status.upper()}** at {now:%Y-%m-%d %H:%M} UTC.", ""]
    lines += [f"- {_clean(part, 600)}" for part in check.reason.split("; ")]
    lines += ["", "Detection: goldbot/ops/checks/loop.py; recovery steps: docs/RUNBOOK.md \"Feedback loop\"."]
    return scrub("\n".join(lines))


def run_loop_check(state_dir: Path, now: pd.Timestamp, settings: Settings | None,
                   sink: IssueSink | None) -> dict[str, Any]:
    """The daily `loop_check` job: run the check and, when it is not ok, open or comment on the `ci-loop` issue once a
    day (daily_review._post: the posted marker, the outbox without a token or on a failed post). Never raises for a
    failing loop: the result carries it, and health alerts on the check itself."""
    from goldbot.ops.report_days import report_day
    from goldbot.research.daily_review import _post
    check = summarise(loop_items(state_dir, now, settings))
    out: dict[str, Any] = {"status": check.status, "reason": _clean(check.reason, 600)}
    if check.status != "ok":
        out["posted"] = _post(state_dir, report_day(now), ISSUE_LABEL, issue_body(check, now), sink,
                              title=f"Feedback loop {check.status}: {_clean(check.reason, 80)}", label=ISSUE_LABEL)
    return out
