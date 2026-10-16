"""Services and heartbeats health checks: scheduler and jobs, service heartbeats, schema skew, approvals, the alert loop, the dead man's switch."""
from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from goldbot.base import SkewSummary, load_record, load_record_json
from goldbot.data.timeutil import heartbeat_problem
from goldbot.ops.checks._base import (
    HEALTH_CLOCK_AHEAD_S,
    HEARTBEAT_SERVICES,
    HEARTBEAT_SILENT_S,
    JOB_OVERDUE_GRACE_S,
    JOB_RUNNING_FAIL_H,
    SCHEDULER_HEARTBEAT_FAIL_S,
    STUCK_APPROVAL_GRACE_S,
    WATCH_FILE,
    WATCH_STALE_S,
    Check,
    Status,
    WatchState,
    _age,
    _ahead_s,
    _epoch,
    _read_json,
    _worst,
    heartbeat_path,
)
from goldbot.ops.checks.registry import (
    BLOCK_ACCOUNT,
    BLOCK_HEARTBEATS,
    BLOCK_SCHEDULER,
    BLOCK_SERVICE_SKEW,
    BLOCK_TAIL,
    register_check,
    resolve,
)

if TYPE_CHECKING:
    from goldbot.ops.health import HealthContext


def _running(job: dict[str, Any]) -> pd.Timestamp | None:
    started, finished = job.get("last_started"), job.get("last_finished")
    if not started:
        return None
    s = pd.Timestamp(started)
    if finished is None or s > pd.Timestamp(finished):
        return s
    return None


@register_check("global", block=BLOCK_SCHEDULER, pos=10)
def check_scheduler(ctx: HealthContext) -> list[Check]:
    """One heartbeat check plus one check per configured job. Reads the heartbeat row in aux.db (state store step 3),
    which a failed scheduler.json export cannot leave stale; the file only when aux.db has no heartbeat yet."""
    from goldbot.db.bookkeeping import scheduler_status
    f = ctx.state_dir / "scheduler.json"
    db_doc = scheduler_status(ctx.state_dir)
    if db_doc is None and not f.exists():
        return [Check(name="scheduler", status="fail", reason="no scheduler.json: scheduler never started")]
    try:
        raw = db_doc if db_doc is not None else _read_json(f)
        hb = pd.Timestamp(raw["ts"])
        jobs: dict[str, dict[str, Any]] = raw.get("jobs", {})
    except (ValueError, OSError, KeyError) as exc:
        return [Check(name="scheduler", status="fail", reason=f"scheduler.json unreadable: {exc}")]
    running = {n: s for n, j in jobs.items() if (s := _running(j)) is not None}
    hb_age = (ctx.now - hb).total_seconds()
    if (ahead := _ahead_s(hb.timestamp(), ctx)) > HEALTH_CLOCK_AHEAD_S:   # even while busy (found 2026-10-16)
        out = [Check(name="scheduler", status="fail",
                     reason=f"heartbeat stamped {_age(ahead)} ahead of this server's clock (clock fault or bad "
                            "write): it cannot show the scheduler runs; check the host clock and restart the scheduler")]
    elif hb_age <= SCHEDULER_HEARTBEAT_FAIL_S:
        out = [Check(name="scheduler", status="ok", reason=f"heartbeat {_age(hb_age)}")]
    elif running:      # a job blocks the loop (a retrain takes hours); the job check judges how long
        out = [Check(name="scheduler", status="ok", reason=f"busy with {', '.join(sorted(running))}; heartbeat {_age(hb_age)}")]
    else:
        out = [Check(name="scheduler", status="fail", reason=f"heartbeat {_age(hb_age)} old: scheduler not running")]
    expected = list(type(ctx.settings.scheduler).model_fields) if ctx.settings is not None else []
    for name in sorted(set(expected) | set(jobs)):
        cname = f"job:{name}"
        j = jobs.get(name)
        if j is None:
            out.append(Check(name=cname, status="warn", reason="configured but not registered yet (scheduler not restarted?)"))
            continue
        statuses: list[Status] = []
        parts: list[str] = []
        if name in running:
            ran_h = (ctx.now - running[name]).total_seconds() / 3600
            if ran_h > JOB_RUNNING_FAIL_H:
                statuses.append("fail")
                parts.append(f"started {ran_h:.1f} h ago and never finished (scheduler died mid-job?)")
            else:
                parts.append(f"running for {ran_h:.1f} h")
        if j.get("last_ok") is False:
            statuses.append("fail")
            err = str(j.get("last_error") or "").strip().splitlines()
            parts.append(f"last run failed ({j.get('failures', 0)} failures): {err[0][:160] if err else '?'}")
        nxt = j.get("next_slot")
        if nxt and name not in running:
            late = (ctx.now - pd.Timestamp(nxt)).total_seconds()
            if late > JOB_OVERDUE_GRACE_S:
                statuses.append("fail")
                parts.append(f"overdue: slot {pd.Timestamp(nxt):%Y-%m-%d %H:%M} passed {_age(late)} ago")
        skipped = j.get("last_skipped")
        if skipped and j.get("last_slot") and pd.Timestamp(skipped) >= pd.Timestamp(j["last_slot"]) \
                and (j.get("last_started") is None or pd.Timestamp(skipped) > pd.Timestamp(j["last_started"])):
            statuses.append("warn")
            parts.append(f"slot {pd.Timestamp(j['last_slot']):%Y-%m-%d %H:%M} skipped (missed by more than max_late)")
        if not parts:
            last = j.get("last_finished")
            parts.append(f"ok, last ran {pd.Timestamp(last):%Y-%m-%d %H:%M}" if last else "waiting for its first slot")
            if nxt:
                parts.append(f"next {pd.Timestamp(nxt):%Y-%m-%d %H:%M}")
        out.append(Check(name=cname, status=_worst(statuses), reason="; ".join(parts)))
    return out


@register_check("global", block=BLOCK_TAIL, pos=110)
def check_approvals(ctx: HealthContext) -> Check:
    """Pending proposals past their window that no engine archived. Warn, not fail: the bus refuses decisions on them
    (nothing can be entered from them) and a dead engine already fails its own check; they mostly mean an engine
    restarted with a proposal open."""
    d = ctx.state_dir / "approvals" / "pending"
    now = _epoch(ctx.now)
    stuck: list[tuple[float, str]] = []
    for f in sorted(d.glob("*.json")) if d.exists() else []:
        try:
            p = _read_json(f)
            over = now - float(p["created"]) - float(p.get("window_s", 90))
        except (ValueError, OSError, KeyError, TypeError):
            stuck.append((float("inf"), f.stem))
            continue
        if over > STUCK_APPROVAL_GRACE_S and p.get("outcome") is None:
            stuck.append((over, f.stem))
    if not stuck:
        return Check(name="approvals", status="ok", reason="no proposal stuck past its window")
    oldest = max(stuck)
    return Check(name="approvals", status="warn",
                 reason=f"{len(stuck)} pending proposal(s) past their window, oldest {oldest[1]} "
                        f"({_age(oldest[0]) if oldest[0] != float('inf') else 'unreadable'}); the engine never archived them")


@register_check("global", block=BLOCK_TAIL, pos=130)
def check_alert_loop(ctx: HealthContext) -> Check:
    f = ctx.state_dir / WATCH_FILE
    if not f.exists():
        return Check(name="alerts", status="warn", reason="health alerts never ran (Telegram service not started)")
    try:
        ts = load_record_json(WatchState, f.read_text(encoding="utf-8"), source=WATCH_FILE).ts
    except (ValueError, OSError) as exc:
        return Check(name="alerts", status="warn", reason=f"health_last.json unreadable: {exc}")
    age = (ctx.now - ts).total_seconds() if ts is not None else float("inf")
    if ts is not None and (ahead := _ahead_s(ts.timestamp(), ctx)) > HEALTH_CLOCK_AHEAD_S:
        return Check(name="alerts", status="warn",
                     reason=f"last health alert pass stamped {_age(ahead)} ahead of this server's clock (clock fault or "
                            "bad write): it cannot show the Telegram alert loop runs")
    if age > WATCH_STALE_S:
        return Check(name="alerts", status="warn", reason=f"last health alert pass {_age(age)} ago: Telegram service down?")
    return Check(name="alerts", status="ok", reason=f"last pass {_age(age)} ago")


@register_check("global", block=BLOCK_TAIL, pos=140)
def check_deadman(ctx: HealthContext) -> Check:
    """The outside dead man's switch (goldbot/ops/deadman.py) is configured: presence and shape of the keyring URL,
    never its value. Whether pings arrive is judged by the outside service itself (a missed ping is its alert)."""
    from goldbot.ops.deadman import DEADMAN_KEY, SET_HINT, url_problem
    try:
        url = (ctx.get_secret(DEADMAN_KEY) or "").strip()
    except Exception as exc:                 # noqa: BLE001 - presence check only
        return Check(name="deadman", status="warn", reason=f"deadman-url unreadable ({type(exc).__name__})")
    if not url:
        return Check(name="deadman", status="warn",
                     reason=f"dead man's switch not set: nobody is told if this server dies. Create a free "
                            f"Healthchecks.io check, then {SET_HINT} (docs/deploy/first-deploy.md)")
    problem = url_problem(url)
    if problem:
        return Check(name="deadman", status="warn", reason=f"{problem}; store the check's https ping URL with {SET_HINT}")
    every = ctx.settings.telegram.deadman_every_min if ctx.settings is not None else 2
    return Check(name="deadman", status="ok",
                 reason=f"set; the Telegram service pings every {every} min while healthy, <url>/fail on a FAIL")


@register_check("service", block=BLOCK_HEARTBEATS, pos=10, services=HEARTBEAT_SERVICES)
def check_heartbeat(ctx: HealthContext, service: str) -> Check:
    """S5: a service silent for HEARTBEAT_SILENT_S fails (the alert); one that never wrote a heartbeat warns."""
    name = f"heartbeat:{service}"
    f = heartbeat_path(ctx.state_dir, service)
    if not f.exists():
        return Check(name=name, status="warn", reason=f"no heartbeat_{service}.json: service never started (or predates heartbeats)")
    try:
        ts = float(_read_json(f)["ts"])
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return Check(name=name, status="fail", reason=f"heartbeat unreadable: {exc}")
    now = _epoch(ctx.now)
    if heartbeat_problem(ts, now, math.inf, ahead_s=math.inf) == "invalid":   # float() read NaN or infinity
        return Check(name=name, status="fail", reason=f"heartbeat unreadable: ts {ts!r} is not a finite number")
    if (ahead := _ahead_s(ts, ctx)) > HEALTH_CLOCK_AHEAD_S:
        return Check(name=name, status="fail",
                     reason=f"{service} heartbeat stamped {_age(ahead)} ahead of this server's clock (clock fault or "
                            f"bad write): it cannot show {service} runs; check the host clock and restart it")
    age = now - ts
    if age > HEARTBEAT_SILENT_S:
        return Check(name=name, status="fail",
                     reason=f"{service} silent for {_age(age)} (> {HEARTBEAT_SILENT_S // 60} min): service down?")
    return Check(name=name, status="ok", reason=f"heartbeat {_age(age)}")


SKEW_SERVICES = HEARTBEAT_SERVICES   # schema_skew:svc:<service> from heartbeat_<service>.json (engines: per account)
SKEW_CLICK_SOURCE = "approvals/decisions:"   # the approval bus's strict reader (bus.decisions_for)
SKEW_REFUSED_FAIL_S = 3600         # a refused record (a dropped click) FAILs this long after the refusal, then WARNs
SKEW_REASON_SOURCES = 5            # sources named in the reason (refused first); the rest as "+N more"
SKEW_REASON_CHARS = 400            # length of the named-sources part of the reason


def _skew_sources(s: SkewSummary) -> str:
    """At most SKEW_REASON_SOURCES sources (refused first, as published) in about SKEW_REASON_CHARS characters, then
    "+N more (health --json)" for the distinct sources not named."""
    shown = s.entries[:SKEW_REASON_SOURCES]
    text = ", ".join(f"{e.source} ({e.model}: {','.join(e.fields)})" + (" [refused]" if e.refused else "")
                     for e in shown)
    if len(text) > SKEW_REASON_CHARS:
        text = text[:SKEW_REASON_CHARS - 3].rstrip() + "..."
    left = s.count - len(shown)
    return text + (f" +{left} more (health --json)" if left > 0 else "")


@register_check("account", block=BLOCK_ACCOUNT, pos=100)
def check_schema_skew(ctx: HealthContext, label: str, source: Path | None = None, *,
                      name: str | None = None) -> Check | None:
    """ADR 0010: "persisted state is read tolerantly, built strictly", and state written by a newer release (expected
    only after a rollback) must reach the owner, not only the log. From a process's bounded `schema_skew` block:
    `schema_skew:<label>` (or `name`) WARNs (NOTIFY_ON_WARN) while that process has read any such state, naming the sources, and
    FAILs for SKEW_REFUSED_FAIL_S after a strict reader refused a record (for the approval bus: an owner's
    approve/reject click dropped, the proposal expires); after that it WARNs naming the refusal and its time, and a
    new refusal FAILs (and alerts) again. A block without `last_refused_utc` (an older release) reads as a fresh
    refusal. `source`: the file holding the block, by default the engine's engine_<label>.json (label = account);
    every service publishes it in heartbeat_<service>.json (`Heartbeat`; check_service_schema_skew). A block that
    the process could not build ({"error": <type>}) WARNs as unreadable. The counts live in the
    process: they clear when it restarts and next writes the file (the engine on a tick or a bar, a heartbeat within
    HEARTBEAT_EVERY_S). None while the block is not published (no file, an older release, an unreadable file: the
    engine and heartbeat checks report those)."""
    f = source if source is not None else ctx.state_dir / f"engine_{label}.json"
    stable = f.name if source is not None else "engine_<account>.json"
    if not f.exists():
        return None
    try:
        raw = _read_json(f).get("schema_skew")
    except (ValueError, OSError, AttributeError, TypeError):
        return None
    if raw is None:
        return None
    name = name or f"schema_skew:{label}"
    if isinstance(raw, dict) and "error" in raw and "count" not in raw:
        return Check(name=name, status="warn", reason=f"{label}: schema_skew block unreadable: the process could not "
                                                      f"build it ({str(raw['error'])[:80]}; see its log)")
    try:
        s = load_record(SkewSummary, raw, source=f"{stable}:schema_skew")
    except ValueError as exc:
        return Check(name=name, status="warn", reason=f"{label}: schema_skew block unreadable ({type(exc).__name__})")
    if not s.count:
        return Check(name=name, status="ok", reason=f"{label}: no state from a newer release read")
    srcs = _skew_sources(s)
    if s.refused:
        at = s.last_refused_utc
        fresh = at is None or (ctx.now - at).total_seconds() <= SKEW_REFUSED_FAIL_S
        when = f"at {at:%Y-%m-%d %H:%M} UTC" if at is not None else "(time unknown)"
        click = any(e.refused and e.source.startswith(SKEW_CLICK_SOURCE) for e in s.entries)
        if fresh:
            what = ("so an owner's click was dropped and the proposal expires" if click
                    else "so that record was not acted on")
            return Check(name=name, status="fail",
                         reason=f"{label}: a strict reader refused state from a newer release {when}, {what}; "
                                f"roll forward: {srcs}")
        what = "an owner's click was refused" if click else "a record from a newer release was refused"
        tail = ", the proposal expired" if click else ""
        return Check(name=name, status="warn",
                     reason=f"{label}: {what} {when} (over {SKEW_REFUSED_FAIL_S // 60} min ago{tail}) and state "
                            f"from a newer release is read; roll forward: {srcs}")
    return Check(name=name, status="warn",
                 reason=f"{label}: state written by a newer release is read with its new fields ignored "
                        f"({s.count} source(s), {s.reads} read(s)), expected only after a rollback: {srcs}")


@register_check("service", block=BLOCK_SERVICE_SKEW, pos=10, services=SKEW_SERVICES, guard="schema_skew:svc")
def check_service_schema_skew(ctx: HealthContext, service: str) -> Check | None:
    """`schema_skew:svc:<service>` from heartbeat_<service>.json; the `svc:` part keeps it apart from an account of
    the same name (`schema_skew:<account>`) and still matches NOTIFY_ON_WARN. check_schema_skew is taken through
    goldbot.ops.health (`resolve`), the patch point."""
    return resolve(check_schema_skew)(ctx, service, heartbeat_path(ctx.state_dir, service),
                                      name=f"schema_skew:svc:{service}")
