"""The findings register (owner 2026-11-20: "feedback must close itself"). The system audit of 2026-11-20 found
findings that sat unowned for weeks (A4 failing a month, issues #392/#394 without an owner): reviewers worked, nothing
owned turning a finding into a fix. This module makes every finding and owner directive a row the system re-checks.

  config/findings.yaml   versioned, public-safe (no login, host, account number or money record: a row whose text
                         `daily_review.scrub` would drop, or that carries a currency amount, is refused at load). One
                         row per finding: id, title, owner role (an agent in .claude/agents/), severity, due date,
                         source, and a CHECK naming a registered check function with its parameters
                         (goldbot/ops/finding_checks.py; the parameters are validated at load)
  findings_check job     daily (settings scheduler.findings_check): every row evaluated on the brain's data, its status
                         stored in aux.db `findings_history`, and every new, regressed, closed or past-due row (an
                         overdue one again every `findings.remind_days`) announced in ONE comment on the status issue
                         (#34) a day at most, plus one Telegram line for critical/high events. Without a token or on a
                         failed post the events stay pending (`posted` 0) and go out with the next day's comment
  health `findings`      WARN while a critical or high row is past its due date and not closed (checks/findings.py)
  CLI                    python -m goldbot.ops.run findings [--stored] [--json]: the table, evaluated now (read-only,
                         nothing stored or posted) or as last stored

Status of a row, from its check's verdict and its due date (UTC day):
  closed    the check passes: the finding is measured fixed
  open      the check fails, not yet due
  overdue   past due and not closed (a fail, or a check that cannot read its data: no evidence is not a fix)
  unknown   the check cannot read its data, not yet due, and has never measured the row
A check that cannot read its data after a measurement keeps the last measured status (a weekend's empty window
neither closes nor reopens a row).

Reads only, as the checks do; writes only its own two aux.db tables and the Telegram notice queue. Nothing here
touches an order, a limit, an arm, a halt or a setting.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import yaml
from pydantic import Field, field_validator, model_validator

from goldbot.base import FrozenRecord, UtcTimestamp, load_record_json
from goldbot.config import ROOT, Settings
from goldbot.data.timeutil import epoch_ns
from goldbot.db.connection import Database
from goldbot.ops import finding_checks as fc

log = logging.getLogger("goldbot.findings")

Severity = Literal["critical", "high", "medium", "low"]
Status = Literal["closed", "open", "overdue", "unknown"]
Event = Literal["new", "regressed", "past_due", "reminder", "closed"]
URGENT: tuple[Severity, ...] = ("critical", "high")
_SEV_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_STATUS_RANK = {"overdue": 0, "open": 1, "unknown": 2, "closed": 3}
NOTICE_ROLE = "findings"
PRIVATE_NOTE = "see private notes"
AGENTS_DIR = ROOT / ".claude" / "agents"
_MONEY = re.compile(r"[£$€]\s?\d|\d\s?(gbp|usd|eur|pounds?|dollars?|euros?|quid)\b|\b(gbp|usd|eur)\s?\d",
                    re.IGNORECASE)


# --------------------------------------------------------------------------------------------- the register
class CheckRef(FrozenRecord):
    fn: str
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _known(self) -> CheckRef:
        fc.parse_params(self.fn, self.params)            # an unknown check or a bad parameter fails the load
        return self


class Finding(FrozenRecord):
    id: str = Field(..., pattern=r"^[A-Z][A-Z0-9]*(-[A-Z0-9]+)*$")
    title: str = Field(..., min_length=5, max_length=160)
    owner: str = Field(..., pattern=r"^[a-z][a-z-]*$")
    severity: Severity
    due: date
    source: str = Field(..., min_length=3)
    check: CheckRef
    # an open security weakness (security review of ee15815e): the public title names no specifics, and the comment
    # and Telegram line never carry its measurement (the detail stays in the private audit notes)
    private: bool = False


class Register(FrozenRecord):
    version: Literal[1]
    findings: list[Finding] = Field(..., min_length=1)

    @field_validator("findings")
    @classmethod
    def _unique(cls, v: list[Finding]) -> list[Finding]:
        seen: set[str] = set()
        for f in v:
            if f.id in seen:
                raise ValueError(f"finding id {f.id} appears twice")
            seen.add(f.id)
        return v


def public_problems(raw: str) -> list[str]:
    """Lines of the register text that must not be in a public file: a login, host, email or credential (the daily
    review's scrub rule) or a currency amount (the money record stays private)."""
    from goldbot.research.daily_review import scrub
    kept = set(scrub(raw).splitlines())
    return [f"line {i}" for i, line in enumerate(raw.splitlines(), 1)
            if line not in kept or _MONEY.search(line)]


def load_register(path: Path, *, agents_dir: Path | None = AGENTS_DIR) -> Register:
    """The register at `path`, validated: every check registered with valid parameters, every id once, public-safe,
    and every owner an agent role in `agents_dir` (None skips that: the brain's checkout carries .claude/agents/ too,
    but a test may not). Raises ValueError on any problem."""
    raw = path.read_text(encoding="utf-8")
    if bad := public_problems(raw):
        raise ValueError(f"{path.name}: not public-safe ({', '.join(bad)}): no login, host, email or money figure")
    reg = Register.model_validate(yaml.safe_load(raw))
    if agents_dir is not None and agents_dir.is_dir():
        roles = {p.stem for p in agents_dir.glob("*.md")}
        unknown = sorted({f.owner for f in reg.findings} - roles)
        if unknown:
            raise ValueError(f"{path.name}: owner(s) {', '.join(unknown)} are not agent roles in .claude/agents/")
    return reg


def register_path(settings: Settings | None) -> Path:
    rel = settings.findings.path if settings is not None else "config/findings.yaml"
    return ROOT / rel


# --------------------------------------------------------------------------------------------- evaluation
class FindingStatus(FrozenRecord):
    """One row's evaluation at one run (aux.db findings_history body)."""
    id: str
    title: str
    owner: str
    severity: Severity
    due: date
    run_utc: UtcTimestamp
    verdict: fc.Verdict
    status: Status
    reason: str
    value: str | None = None
    event: Event | None = None
    announced_utc: UtcTimestamp | None = None    # the latest event this row raised (a reminder counts from it)
    private: bool = False                        # Finding.private: the measurement is never published


def evaluate_one(f: Finding, ctx: fc.FindingsContext) -> fc.CheckResult:
    """The row's check, guarded: a check that raises is cant_check (never a pass)."""
    spec = fc.CHECKS[f.check.fn]
    try:
        return spec.fn(ctx, spec.params.model_validate(f.check.params))
    except Exception as exc:              # noqa: BLE001 - one broken check must not stop the register
        log.warning("findings: check %s of %s raised %s", f.check.fn, f.id, type(exc).__name__)
        return fc.CheckResult(verdict="cant_check", reason=f"check raised {type(exc).__name__}")


def classify(f: Finding, result: fc.CheckResult, now: pd.Timestamp, prev: FindingStatus | None,
             remind_days: int) -> FindingStatus:
    """The row's status and the event it raises against its previous stored status (module docstring). A check that
    cannot read its data this run (a weekend's empty window, a GitHub outage) keeps the row's last measured state: a
    closed row stays closed and an open one stays open (overdue past its due date), so missing data neither closes nor
    reopens a finding and raises no event of its own."""
    past_due = now.date() > f.due
    verdict, reason = result.verdict, result.reason
    if verdict == "cant_check" and prev is not None and prev.status != "unknown":
        verdict = "pass" if prev.status == "closed" else "fail"
        reason = f"can't check now ({reason}); last measured {prev.status}"
    status: Status
    if verdict == "pass":
        status = "closed"
    elif past_due:
        status = "overdue"
    else:
        status = "open" if verdict == "fail" else "unknown"
    event: Event | None = None
    if status == "closed":
        event = "closed" if prev is not None and prev.status in ("open", "overdue") else None   # never told: silent
    elif prev is not None and prev.status == "closed":
        event = "regressed"
    elif status == "overdue":
        if prev is None or prev.status != "overdue":
            event = "new" if prev is None else "past_due"
        elif prev.announced_utc is None or now - prev.announced_utc >= pd.Timedelta(days=remind_days):
            event = "reminder"
    elif verdict == "fail" and (prev is None or prev.status == "unknown"):
        event = "new"
    announced = now if event is not None else (prev.announced_utc if prev is not None else None)
    return FindingStatus(id=f.id, title=f.title, owner=f.owner, severity=f.severity, due=f.due, run_utc=now,
                         verdict=result.verdict, status=status, reason=reason, value=result.value, event=event,
                         private=f.private,
                         announced_utc=announced)


def evaluate(reg: Register, ctx: fc.FindingsContext, prev: dict[str, FindingStatus], remind_days: int
             ) -> list[FindingStatus]:
    return [classify(f, evaluate_one(f, ctx), ctx.now, prev.get(f.id), remind_days) for f in reg.findings]


def ordered(rows: list[FindingStatus]) -> list[FindingStatus]:
    """Worst first: overdue, open, unknown, closed; then severity, due date, id."""
    return sorted(rows, key=lambda r: (_STATUS_RANK[r.status], _SEV_RANK[r.severity], r.due, r.id))


# --------------------------------------------------------------------------------------------- the store
def _db(state_dir: Path) -> Database:
    return Database.open(state_dir, "aux")


def _ns(ts: pd.Timestamp) -> int:
    return int(epoch_ns(pd.DatetimeIndex([ts]))[0])


_LATEST = ("SELECT h.body FROM findings_history h JOIN (SELECT finding_id, MAX(run_ns) AS run_ns "
           "FROM findings_history GROUP BY finding_id) m USING (finding_id, run_ns)")


def _parse(rows: list[Any]) -> dict[str, FindingStatus]:
    out = [load_record_json(FindingStatus, b, source="db:findings_history") for (b,) in rows]
    return {r.id: r for r in out}


def latest(state_dir: Path, before: pd.Timestamp | None = None) -> dict[str, FindingStatus]:
    """The last stored status of every finding (aux.db, migrated on open: the job's read); with `before`, the last
    one stored for an earlier slot, so a re-run of a slot compares against the run before it, not against itself."""
    if before is None:
        with _db(state_dir).read() as conn:
            return _parse(conn.execute(_LATEST).fetchall())
    with _db(state_dir).read() as conn:
        return _parse(conn.execute(
            "SELECT h.body FROM findings_history h JOIN (SELECT finding_id, MAX(run_ns) AS run_ns FROM findings_history "
            "WHERE run_ns < ? GROUP BY finding_id) m USING (finding_id, run_ns)", (_ns(before),)).fetchall())


def stored_latest(state_dir: Path) -> dict[str, FindingStatus] | None:
    """The same, read-only (health, the CLI): None when aux.db or the table does not exist yet (the job never ran).
    Raises sqlite3.Error or ValueError when the store cannot be read."""
    import sqlite3

    from goldbot.db.connection import db_path
    p = db_path(state_dir, "aux")
    if not p.exists():
        return None
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
    try:
        if con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'findings_history'").fetchone() \
                is None:
            return None
        return _parse(con.execute(_LATEST).fetchall())
    finally:
        con.close()


def _store(state_dir: Path, rows: list[FindingStatus]) -> None:
    with _db(state_dir).write() as conn:
        # a re-run of a slot replaces its row; an event it already announced stays announced
        conn.executemany("INSERT INTO findings_history (finding_id, run_ns, status, event, posted, body) "
                         "VALUES (?, ?, ?, ?, 0, ?) ON CONFLICT (finding_id, run_ns) DO UPDATE SET "
                         "status = excluded.status, body = excluded.body, posted = CASE WHEN "
                         "findings_history.event IS excluded.event THEN findings_history.posted ELSE 0 END, "
                         "event = excluded.event",
                         [(r.id, _ns(r.run_utc), r.status, r.event, r.model_dump_json()) for r in rows])


def pending(state_dir: Path) -> list[tuple[str, int, FindingStatus]]:
    """Events not announced yet: (finding id, run_ns, status), oldest first."""
    with _db(state_dir).read() as conn:
        rows = conn.execute("SELECT finding_id, run_ns, body FROM findings_history WHERE event IS NOT NULL "
                            "AND posted = 0 ORDER BY run_ns").fetchall()
    return [(i, int(ns), load_record_json(FindingStatus, b, source="db:findings_history")) for i, ns, b in rows]


def _announced_today(state_dir: Path, day: str) -> bool:
    with _db(state_dir).read() as conn:
        return conn.execute("SELECT 1 FROM findings_posts WHERE day = ?", (day,)).fetchone() is not None


def _mark(state_dir: Path, keys: list[tuple[str, int]], day: str, now: pd.Timestamp, detail: dict[str, Any]) -> None:
    """The announced events marked posted and the day's announcement recorded, in one transaction."""
    with _db(state_dir).write() as conn:
        conn.executemany("UPDATE findings_history SET posted = 1 WHERE finding_id = ? AND run_ns = ?", keys)
        conn.execute("INSERT OR REPLACE INTO findings_posts (day, posted_ns, body) VALUES (?, ?, ?)",
                     (day, _ns(now), json.dumps(detail)))


# --------------------------------------------------------------------------------------------- the announcement
def _cell(text: str | None, limit: int = 160) -> str:
    t = (text or "").replace("|", "/").replace("\n", " ")
    return t if len(t) <= limit else t[: limit - 3] + "..."


def counts(rows: list[FindingStatus]) -> str:
    n = {s: sum(r.status == s for r in rows) for s in ("overdue", "open", "unknown", "closed")}
    return f"{n['overdue']} overdue, {n['open']} open, {n['unknown']} can't check, {n['closed']} closed"


def comment_body(events: list[FindingStatus], current: list[FindingStatus], now: pd.Timestamp) -> str:
    """The status-issue comment: what changed, then every other overdue row in one line. Scrubbed (public issue)."""
    from goldbot.research.daily_review import scrub
    lines = [f"**Findings register** {now:%Y-%m-%d} (config/findings.yaml): {counts(current)}.", "",
             "| Event | Finding | Severity | Owner | Due | Status | Measured |", "|---|---|---|---|---|---|---|"]
    for r in ordered(events):
        measured = PRIVATE_NOTE if r.private else _cell(r.reason)
        lines.append(f"| {r.event} | {r.id}: {_cell(r.title, 80)} | {r.severity} | {r.owner} | {r.due} | {r.status} | "
                     f"{measured} |")
    told = {r.id for r in events}
    rest = [r for r in ordered(current) if r.status == "overdue" and r.id not in told]
    if rest:
        lines += ["", "Still overdue: " + "; ".join(f"{r.id} ({r.owner}, due {r.due})" for r in rest)]
    lines += ["", "Detection: goldbot/ops/findings.py (daily `findings_check`); close a row by fixing what its check "
                  "measures; docs/RUNBOOK.md \"Findings register\"."]
    return scrub("\n".join(lines))


def telegram_text(events: list[FindingStatus]) -> str | None:
    urgent = [r for r in ordered(events) if r.severity in URGENT and r.event != "closed"]
    if not urgent:
        return None
    return "Findings: " + "; ".join(f"{r.id} {r.event} ({r.status}, {r.owner}, due {r.due})" for r in urgent[:6]) + \
        (f" and {len(urgent) - 6} more" if len(urgent) > 6 else "") + " (issue #34)"


def run_findings_check(state_dir: str | Path, now: pd.Timestamp, settings: Settings, sink: Any,
                       github: fc.RepoReader | None = None, *, register: Path | None = None,
                       closes_loader: Any = None, records_loader: Any = None,
                       agents_dir: Path | None = AGENTS_DIR) -> dict[str, Any]:
    """The daily `findings_check` job (module docstring). A register that does not load raises (the job FAILs and
    health says so); a check that raises is that row's cant_check. Events go out at most once a UTC day; until a
    post succeeds they stay pending and ride on the next one."""
    from goldbot.ops.limits_job import queue_notice
    sd = Path(state_dir)
    cfg = settings.findings
    reg = load_register(register or register_path(settings), agents_dir=agents_dir)
    ctx = fc.FindingsContext(state_dir=sd, repo_root=ROOT, now=now, settings=settings, github=github,
                             closes_loader=closes_loader, records_loader=records_loader)
    rows = evaluate(reg, ctx, latest(sd, before=now), cfg.remind_days)
    _store(sd, rows)
    out: dict[str, Any] = {"findings": len(rows), "summary": counts(rows),
                           "events": {r.id: r.event for r in rows if r.event is not None}}
    waiting = pending(sd)
    if not waiting:
        out["posted"] = "nothing to announce"
        return out
    day = f"{now:%Y-%m-%d}"
    if _announced_today(sd, day):
        out["posted"] = f"already announced on {day}: {len(waiting)} event(s) wait for tomorrow"
        return out
    latest_ev: dict[str, FindingStatus] = {}
    for _, _, r in waiting:
        latest_ev[r.id] = r                       # oldest first: the newest event of each row wins
    known = {f.id for f in reg.findings}
    events = [r for i, r in latest_ev.items() if i in known]
    detail: dict[str, Any] = {"events": sorted(latest_ev)}
    if sink is None:
        log.warning("findings: no github-token in the keyring; %d event(s) stay pending", len(waiting))
        out["posted"] = "no token: events pending"
        return out
    try:
        sink.comment(cfg.status_issue, comment_body(events, rows, now))
    except Exception as exc:              # noqa: BLE001 - a failed post never fails the job; the events stay pending
        log.warning("findings: the status-issue post failed (%s); events stay pending", type(exc).__name__)
        out["posted"] = f"post failed ({type(exc).__name__}): events pending"
        return out
    detail["issue"] = "posted"
    _mark(sd, [(i, ns) for i, ns, _ in waiting], day, now, detail)       # before anything else can fail: no re-post
    if cfg.telegram and (text := telegram_text(events)) is not None:
        detail["telegram"] = "queued" if queue_notice(sd, text, role=NOTICE_ROLE) else "failed"
    out["posted"] = f"issue #{cfg.status_issue}: {len(events)} event(s)" + \
        (f", telegram {detail['telegram']}" if "telegram" in detail else "")
    return out


def urgent_past_due(reg: Register, stored: dict[str, FindingStatus], today: date) -> list[Finding]:
    """Critical/high rows past their due date whose last stored status is not closed (never stored: not closed)."""
    return [f for f in reg.findings if f.severity in URGENT and today > f.due
            and (f.id not in stored or stored[f.id].status != "closed")]


# --------------------------------------------------------------------------------------------- CLI
def table(rows: list[FindingStatus]) -> str:
    head = f"{'id':<22} {'sev':<8} {'status':<8} {'due':<10} {'owner':<24} measured"
    lines = [head, "-" * len(head)]
    for r in ordered(rows):
        lines.append(f"{r.id:<22} {r.severity:<8} {r.status:<8} {r.due!s:<10} {r.owner:<24} {_cell(r.reason, 110)}")
    lines.append(f"\n{counts(rows)} ({len(rows)} findings)")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="findings", description="The findings register: every row, worst first.")
    p.add_argument("--state", default="state")
    p.add_argument("--register", default=None, help="the register file (default: settings findings.path)")
    p.add_argument("--stored", action="store_true", help="the last stored run instead of evaluating now")
    p.add_argument("--no-github", action="store_true", help="do not read GitHub (its checks read can't check)")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    from goldbot.config import load_settings
    settings = load_settings()
    sd = Path(a.state)
    path = Path(a.register) if a.register else register_path(settings)
    reg = load_register(path)
    stored = stored_latest(sd) or {}
    if a.stored:
        rows = [stored[f.id] for f in reg.findings if f.id in stored]
    else:
        github = None if a.no_github else _reader(settings)
        ctx = fc.FindingsContext(state_dir=sd, repo_root=ROOT, now=pd.Timestamp.now("UTC"), settings=settings,
                                 github=github)
        rows = evaluate(reg, ctx, stored, settings.findings.remind_days)      # nothing stored, nothing posted
    if a.json:
        print(json.dumps([r.model_dump(mode="json") for r in ordered(rows)], indent=1))
    else:
        print(table(rows))
    return 0


def _reader(settings: Settings) -> fc.RepoReader | None:
    from goldbot.ops.accounts import get_secret
    from goldbot.ops.github_issues import GitHubIssues
    token = get_secret("github-token")
    return GitHubIssues(token, settings.daily_review.repo) if token else None


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
