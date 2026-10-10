"""Deterministic health checks over the state directory and settings (design: Deployment / observability).

Every check reads files the services already write and returns ok / warn / fail with a one-line reason; nothing here
talks to the network, a broker or Telegram, so the whole module is unit-tested with temporary state dirs.

  python -m goldbot.ops.run health                 # table, exit code 1 on any fail
  python -m goldbot.ops.run health --json          # machine-readable report
  python -m goldbot.ops.run health --static        # only checks that need no running service (used mid-update)
  python -m goldbot.ops.run health --out f.json    # also write the JSON report to a file (UTF-8)
  python -m goldbot.ops.run health --baseline f.json   # exit 1 only for fails that were not failing in f.json

Secrets are checked for PRESENCE only: the report names missing keys and never contains a value. The one network
probe, the MT5 bridge's /health through the tunnel, is injected (`HealthContext.bridge_probe`); tests leave it unset.

Operational alerts (design S5, R11, X6) are checks too: a service heartbeat silent for 5 minutes fails, and a tripped
daily or weekly loss cap and an order that failed after the retries warn with `NOTIFY_ON_WARN`, so the Telegram alert
path tells the owner once per incident. Alerts only add information: nothing here changes orders, sizing or halts.
The design's stop rule (P6) is a check too: a breach fails `stop_rule`, which alerts; it recommends stopping and halts
nothing by itself. `phase_gates` shows whether the next roadmap gate is met (P7); it records nothing.

`HealthWatch` is the alert half: fed a report, it returns the Telegram text for checks that turned fail (and the ones
that recovered) since the last report, and persists the last statuses in state/health_last.json so a restart of the
Telegram service neither repeats nor drops an alert.
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Literal, cast

import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord, Record, UtcTimestamp, write_atomic
from goldbot.config import DEFAULT_SETTINGS, Settings, settings_dict
from goldbot.data.calendar import DEFAULT_SESSIONS, SessionTable
from goldbot.data.crossfeed import check_reconciliation

Status = Literal["ok", "warn", "fail"]
_RANK: dict[str, int] = {"ok": 0, "warn": 1, "fail": 2}

# ---- thresholds (seconds unless named otherwise); the reasons quote them so an alert explains itself
SUPERVISOR_WARN_S = 20
SUPERVISOR_FAIL_S = 60            # engines block entries when the supervisor heartbeat is older than this
ENGINE_WARN_S = 20 * 60           # engines write their state at every 15m bar close
ENGINE_FAIL_S = 45 * 60
SCHEDULER_HEARTBEAT_FAIL_S = 5 * 60   # run_forever saves every 30 s while idle
JOB_OVERDUE_GRACE_S = 15 * 60
JOB_RUNNING_FAIL_H = 12.0         # a job "running" this long means the scheduler died mid-job
COSTS_WARN_DAYS = 4.0             # nightly on weekdays: Friday -> Monday is 3 days
COSTS_FAIL_DAYS = 8.0
COSTS_PUBLISHED_WARN_DAYS = 8.0   # the release costs-v1 copy research.yml reads (costs.publish_release only)
DQ_WINDOW_HOURS = 24              # data-quality error events within this window raise a warning
NEWS_STALE_POLLS = 3              # feed health older than this many poll intervals: the news service is down
SPEND_WARN_FRACTION = 0.8
STUCK_APPROVAL_GRACE_S = 120
DISK_FAIL_FREE_GB, DISK_FAIL_FREE_PCT = 2.0, 5.0
DISK_WARN_FREE_GB, DISK_WARN_FREE_PCT = 5.0, 10.0
REARM_APPLY_S = 10 * 60           # an engine applies a re-arm on its next tick
WATCH_STALE_S = 15 * 60           # health_last.json older than this: the Telegram alert loop is not running
HEARTBEAT_EVERY_S = 60            # services write state/heartbeat_<service>.json at most this often
HEARTBEAT_SILENT_S = 5 * 60       # design S5: "a heartbeat alert fires if any service has been silent for five minutes"
HEARTBEAT_SERVICES = ("supervisor", "scheduler", "telegram", "news", "api")   # engines: engine_<account>.json
FAILED_ORDER_WINDOW_H = 24        # failed sends (X6) in this window are listed, each alerted once
BRIDGE_TIMEOUT_S = 5.0
BACKUP_WARN_H = 26.0              # daily backup: one missed night warns
BACKUP_FAIL_H = 72.0              # three missed nights fail
DRILL_WARN_DAYS = 8.0             # weekly restore drill
PRUNE_WARN_DAYS = 45.0            # monthly retention from the owner's Mac (scripts/backup_retention.sh), two weeks' slack
BACKUP_REPO_WARN_BYTES = 15e9     # of the 20 GB Oracle Object Storage free tier

REQUIRED_SECRETS = ("telegram-bot-token", "tradingview-webhook-secret")
OPTIONAL_SECRETS = ("anthropic-api-key", "github-token")    # features switch off without them (logged at start)


class Check(FrozenRecord):
    name: str
    status: Status
    reason: str


class HealthReport(FrozenRecord):
    ts: UtcTimestamp
    status: Status
    checks: list[Check]

    def failing(self) -> set[str]:
        return {c.name for c in self.checks if c.status == "fail"}

    def counts(self) -> dict[str, int]:
        return {s: sum(c.status == s for c in self.checks) for s in ("ok", "warn", "fail")}


class AccountRef(FrozenRecord):
    """The part of an account the checks need (keeps tests free of accounts.yaml)."""

    account_id: str


class HealthContext(Record):
    state_dir: Path
    now: UtcTimestamp
    settings: Settings | None = None
    settings_error: str | None = None
    accounts: list[AccountRef] = Field(default_factory=list)
    accounts_error: str | None = None
    get_secret: Callable[[str], str | None]
    disk_usage: Callable[[str], Any] = shutil.disk_usage     # -> (total, used, free) in bytes
    sessions: SessionTable = DEFAULT_SESSIONS
    bridge_probe: Callable[[str], str | None] | None = None   # url -> None when /health answers, else why not

    @classmethod
    def from_runtime(cls, state_dir: str | Path = "state", settings_path: str | Path = DEFAULT_SETTINGS,
                     now: pd.Timestamp | None = None) -> "HealthContext":
        """Context for the VPS: settings re-read from disk (not the cached copy, so a bad edit shows up), enabled
        accounts (live ones only after the phase gate) and the keyring."""
        from goldbot.ops import accounts
        settings, s_err, accs, a_err = None, None, [], None
        try:
            settings = Settings.model_validate(settings_dict(settings_path))
        except Exception as exc:
            s_err = f"{type(exc).__name__}: {exc}"
        try:
            accs = [AccountRef(account_id=a.account_id) for a in accounts.enabled_accounts()]
        except Exception as exc:
            a_err = f"{type(exc).__name__}: {exc}"
        from goldbot.execution.bridge import probe_health
        return cls(state_dir=Path(state_dir), now=now if now is not None else pd.Timestamp.now("UTC"),
                   settings=settings, settings_error=s_err, accounts=accs, accounts_error=a_err,
                   get_secret=accounts.get_secret, bridge_probe=lambda url: probe_health(url, BRIDGE_TIMEOUT_S))


# --------------------------------------------------------------------------------------------- helpers
def _worst(statuses: list[Status]) -> Status:
    return max(statuses, key=lambda s: _RANK[s]) if statuses else "ok"


def _age(seconds: float) -> str:
    s = max(seconds, 0.0)
    if s < 120:
        return f"{s:.0f} s"
    if s < 7200:
        return f"{s / 60:.0f} min"
    if s < 172800:
        return f"{s / 3600:.1f} h"
    return f"{s / 86400:.1f} d"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _epoch(now: pd.Timestamp) -> float:
    return now.timestamp()


def _market_open_throughout(ctx: HealthContext, seconds: float) -> bool:
    """True when the gold market was open at `now` and `seconds` ago (no weekend or daily break to explain silence).
    Checking both ends is enough for the windows used here (minutes; the daily break is an hour)."""
    idx = pd.DatetimeIndex([ctx.now, ctx.now - pd.Timedelta(seconds=seconds)])
    return bool(ctx.sessions.is_open(idx).all())


def _skipped(name: str, why: str) -> Check:
    return Check(name=name, status="warn", reason=f"skipped: {why}")


# --------------------------------------------------------------------------------------------- static checks
def check_settings(ctx: HealthContext) -> Check:
    if ctx.settings is None:
        return Check(name="settings", status="fail", reason=f"config/settings.yaml does not load: {ctx.settings_error}"[:300])
    tg = "" if ctx.settings.telegram.allowed_user_ids else "; telegram.allowed_user_ids is empty (no approvals, no alerts)"
    return Check(name="settings", status="warn" if tg else "ok", reason=f"loaded{tg}")


def check_accounts(ctx: HealthContext) -> Check:
    if ctx.accounts_error is not None:
        return Check(name="accounts", status="fail", reason=f"config/accounts.yaml does not load: {ctx.accounts_error}"[:300])
    if not ctx.accounts:
        return Check(name="accounts", status="warn", reason="no enabled accounts: no engine will trade")
    return Check(name="accounts", status="ok", reason="enabled: " + ", ".join(a.account_id for a in ctx.accounts))


def check_secrets(ctx: HealthContext) -> Check:
    """Presence only. Values are never returned, compared or logged."""
    def present(key: str) -> bool:
        try:
            return bool(ctx.get_secret(key))
        except Exception:
            return False
    required = list(REQUIRED_SECRETS) + [f"mt5-{a.account_id}" for a in ctx.accounts]
    missing = [k for k in required if not present(k)]
    optional = [k for k in OPTIONAL_SECRETS if not present(k)]
    hint = " (python -m goldbot.ops.accounts set <key>)"
    if missing:
        extra = f"; optional missing: {', '.join(optional)}" if optional else ""
        return Check(name="secrets", status="fail", reason=f"missing in keyring: {', '.join(missing)}{extra}{hint}")
    if optional:
        return Check(name="secrets", status="warn", reason=f"optional missing: {', '.join(optional)}{hint}")
    return Check(name="secrets", status="ok", reason=f"{len(required) + len(OPTIONAL_SECRETS)} keys present")


def check_disk(ctx: HealthContext) -> Check:
    root = Path(ctx.settings.data_root) if ctx.settings is not None else Path(".")
    probe = root
    while not probe.exists() and probe != probe.parent:      # data_root not created yet: measure its volume
        probe = probe.parent
    try:
        total, _used, free = ctx.disk_usage(str(probe))
    except OSError as exc:
        return Check(name="disk", status="fail", reason=f"cannot stat {root}: {exc}")
    gb, pct = free / 1e9, 100.0 * free / total if total else 0.0
    msg = f"{gb:.1f} GB free ({pct:.0f}%) on the volume of {root}"
    if gb < DISK_FAIL_FREE_GB or pct < DISK_FAIL_FREE_PCT:
        return Check(name="disk", status="fail", reason=msg)
    if gb < DISK_WARN_FREE_GB or pct < DISK_WARN_FREE_PCT:
        return Check(name="disk", status="warn", reason=msg)
    return Check(name="disk", status="ok", reason=msg)


# --------------------------------------------------------------------------------------------- service checks
def check_supervisor(ctx: HealthContext) -> Check:
    f = ctx.state_dir / "supervisor.json"
    if not f.exists():
        return Check(name="supervisor", status="fail", reason="no supervisor.json: supervisor not running, engines block entries")
    try:
        s = _read_json(f)
    except (ValueError, OSError) as exc:
        return Check(name="supervisor", status="fail", reason=f"supervisor.json unreadable: {exc}")
    age = _epoch(ctx.now) - float(s.get("ts", 0))
    if age > SUPERVISOR_FAIL_S:
        return Check(name="supervisor", status="fail",
                     reason=f"heartbeat {_age(age)} old (> {SUPERVISOR_FAIL_S} s): engines block entries")
    if s.get("halt"):
        return Check(name="supervisor", status="warn",
                     reason=f"combined-cap HALT: {', '.join(s.get('reasons') or [])}; heartbeat {_age(age)}")
    if age > SUPERVISOR_WARN_S:
        return Check(name="supervisor", status="warn", reason=f"heartbeat {_age(age)} old")
    note = ", size-down" if s.get("size_down") else ""
    return Check(name="supervisor", status="ok", reason=f"heartbeat {_age(age)}{note}")


def check_engine(ctx: HealthContext, account_id: str) -> Check:
    name = f"engine:{account_id}"
    f = ctx.state_dir / f"engine_{account_id}.json"
    market = _market_open_throughout(ctx, ENGINE_FAIL_S)
    if not f.exists():
        return Check(name=name, status="fail" if market else "warn", reason=f"no engine_{account_id}.json (engine never ran)")
    try:
        e = _read_json(f)
    except (ValueError, OSError) as exc:
        return Check(name=name, status="fail", reason=f"engine state unreadable: {exc}")
    parts: list[str] = []
    statuses: list[Status] = []
    age = _epoch(ctx.now) - float(e.get("ts", 0))
    if not market:
        parts.append(f"market closed, state {_age(age)} old")
    elif age > ENGINE_FAIL_S:
        statuses.append("fail")
        parts.append(f"state {_age(age)} old (> {ENGINE_FAIL_S // 60} min while the market is open)")
    elif age > ENGINE_WARN_S:
        statuses.append("warn")
        parts.append(f"state {_age(age)} old")
    else:
        parts.append(f"state {_age(age)} old")
    stage = str(e.get("stage", "unknown"))
    if stage == "halted":
        statuses.append("fail")
        parts.append("stage HALTED (drawdown stage 2): no entries until re-armed")
        if e.get("rearm_refused"):
            parts.append(f"last re-arm refused: {e['rearm_refused']}")
    elif stage == "size_down":
        statuses.append("warn")
        parts.append("stage size_down")
    else:
        parts.append(f"stage {stage}")
    stale_tick = ctx.settings.risk.stale_tick_seconds if ctx.settings is not None else 20
    tick_age = float(e.get("last_tick_age_s") or 0.0)
    if market and tick_age > stale_tick:
        statuses.append("warn")
        parts.append(f"last tick {_age(tick_age)} old (> {stale_tick} s)")
    if market and e.get("stale_bars"):
        statuses.append("warn")
        parts.append("last bar older than one decision period: entries blocked")
    # broker reads failing (MT5 positions_get / history_deals_get returning None): a warning from the first failure,
    # a FAIL (alerted on Telegram) after risk.positions_unreadable_alert in a row
    unreadable = int(e.get("positions_unreadable") or 0)
    alert_after = ctx.settings.risk.positions_unreadable_alert if ctx.settings is not None else 5
    if unreadable:
        statuses.append("fail" if unreadable >= alert_after else "warn")
        parts.append(f"broker positions/deals unreadable {unreadable} time(s) in a row (terminal fault?): entries "
                     f"blocked, exits retried every tick, closes not confirmed until it reads again")
    other_dq = [c for c in e.get("dq_checks") or [] if c != "positions_unreadable"]
    if e.get("dq_error") and (other_dq or not unreadable):
        statuses.append("fail")
        checks = ", ".join(other_dq) or "stale feed"
        parts.append(f"data-quality error ({checks}): entries blocked")
    for w in e.get("dq_warnings") or []:
        statuses.append("warn")
        parts.append(f"data-quality warning: {w}")
    lost = e.get("closed_records_lost") or []
    if lost:
        statuses.append("fail")
        parts.append(f"closed-trade record LOST for position(s) {', '.join(str(x) for x in lost)} after repeated write "
                     f"failures: the phase gates and stop rule undercount (details in the engine log)")
    cls = str(e.get("account_class") or "unknown")
    if e.get("mode", "paper") != "paper":
        if cls == "unknown":
            statuses.append("warn")
            parts.append("account class unknown: entries blocked until the Friday classifier reads raw or standard")
        elif cls == "standard":
            parts.append("account class standard: 15m families off except session_open, edge must exceed 1.5x cost")
    b = e.get("blackout")
    if isinstance(b, dict) and b:
        parts.append(f"news blackout: {b.get('title', '?')}")
    return Check(name=name, status=_worst(statuses), reason="; ".join(parts))


def _control(ctx: HealthContext) -> dict[str, Any] | None:
    """control.json as a dict ({} when absent); None when unreadable."""
    f = ctx.state_dir / "control.json"
    if not f.exists():
        return {}
    try:
        c = _read_json(f)
        return c if isinstance(c, dict) else None
    except (ValueError, OSError):
        return None


def check_halt(ctx: HealthContext) -> Check:
    c = _control(ctx)
    if c is None:
        return Check(name="owner_halt", status="fail", reason="control.json unreadable: engines treat it as halted")
    rearm = ""
    if c.get("rearm_id"):
        at = pd.Timestamp(float(c.get("rearm_ts") or 0), unit="s", tz="UTC")
        rearm = f"; last owner re-arm by {c.get('rearm_by')} at {at:%Y-%m-%d %H:%M} UTC"
    if c.get("halted"):
        since = pd.Timestamp(float(c.get("ts") or 0), unit="s", tz="UTC")
        why = f" ({c['reason']})" if c.get("reason") else ""
        return Check(name="owner_halt", status="warn",
                     reason=f"HALTED by {c.get('by')}{why} since {since:%Y-%m-%d %H:%M} UTC; re-arm on the dashboard{rearm}")
    return Check(name="owner_halt", status="ok", reason="armed" + rearm)


def check_risk_state(ctx: HealthContext, account_id: str) -> Check:
    """state/risk_<account>.json: the engine's persisted risk periods, drawdown stage and the last re-arm it applied.
    It is read even while the engine is down, so a persisted drawdown halt is visible before a restart."""
    name = f"risk:{account_id}"
    f = ctx.state_dir / f"risk_{account_id}.json"
    if not f.exists():
        return Check(name=name, status="warn", reason=f"no risk_{account_id}.json yet (engine never ran)")
    try:
        r = _read_json(f)
        if not isinstance(r, dict):
            raise ValueError("not an object")
    except (ValueError, OSError) as exc:
        return Check(name=name, status="fail", reason=f"risk state unreadable (the engine refuses to start): {exc}")
    parts: list[str] = []
    statuses: list[Status] = []
    stage = str(r.get("stage", "normal"))
    if stage == "halted":
        statuses.append("fail")
        parts.append("drawdown HALT persisted: owner re-arm on the dashboard needed")
    else:
        parts.append(f"stage {stage}")
    last = r.get("last_reset")
    if last:
        days = (ctx.now - pd.Timestamp(last)).total_seconds() / 86400
        if days > COSTS_WARN_DAYS and _market_open_throughout(ctx, ENGINE_FAIL_S):
            statuses.append("warn")
            parts.append(f"loss-cap day last rolled {days:.1f} d ago")
    c = _control(ctx) or {}
    rid = c.get("rearm_id")
    if rid and r.get("rearm_seen") != rid and _epoch(ctx.now) - float(c.get("rearm_ts") or 0) > REARM_APPLY_S:
        statuses.append("warn")
        parts.append("the last owner re-arm has not been applied by this engine (engine down?)")
    return Check(name=name, status=_worst(statuses), reason="; ".join(parts))


def check_phase(ctx: HealthContext) -> Check:
    """state/phase_state.json (written by `run.py record-gate`): live accounts unlock only after paper_to_tiny_live."""
    from goldbot.ops.accounts import GATES
    f = ctx.state_dir / "phase_state.json"
    if not f.exists():
        return Check(name="phase", status="ok", reason="phase 0, no gate recorded (demo only)")
    try:
        st = _read_json(f)
        passed = list(st.get("gates_passed", []))
        phase = int(st.get("phase", 0))
    except (ValueError, OSError, AttributeError, TypeError) as exc:
        return Check(name="phase", status="fail", reason=f"phase_state.json unreadable: {exc}")
    if passed != list(GATES[:len(passed)]):
        return Check(name="phase", status="fail",
                     reason=f"gates out of order or unknown: {passed} (record with `python -m goldbot.ops.run record-gate`)")
    return Check(name="phase", status="ok", reason=f"phase {phase}, gates passed: {', '.join(passed) or 'none'}")


def check_stop_rule(ctx: HealthContext) -> Check:
    """P6, the design's stop rule (goldbot/ops/gates_phase.py): FAIL when, after 18 months of paper plus live, more than
    500 pooled trades still have a lower 90% bound on expectancy below zero, or when any single trade lost more than the
    weekly cap. The FAIL is the whole action: the alert loop tells the owner once, and the health CLI exits 1. It does
    not halt, close or resize anything (the RiskGate's own caps are unchanged); stopping is the owner's /halt."""
    from goldbot.ops import gates_phase as gp
    if ctx.settings is None:
        return _skipped("stop_rule", "settings do not load")
    trades, err = gp.load_closed_trades(ctx.state_dir)
    _, when, perr = gp.phase_record(ctx.state_dir)
    s = gp.evaluate_stop_rule(trades, when.get("backtest_to_paper"), ctx.settings.risk.weekly_cap, ctx.settings.gates, ctx.now)
    if s.breached:
        return Check(name="stop_rule", status="fail",
                     reason=("STOP RULE BREACHED: the design says the project stops; send /halt to stop entries. Nothing "
                             "was halted automatically (RiskGate caps still apply). " + "; ".join(s.reasons))[:600])
    problems = "; ".join(e for e in (err, perr) if e)
    if problems:
        return Check(name="stop_rule", status="warn", reason=f"record partly unreadable ({problems}); {s.detail}"[:400])
    return Check(name="stop_rule", status="ok", reason=s.detail)


def check_phase_gates(ctx: HealthContext) -> Check:
    """P7: the next unrecorded roadmap gate, met or not, from the evidence (`run.py gates` prints the detail).
    Informational: a gate is recorded only by `run.py record-gate`, and live needs unlock-live plus the typed phrase."""
    from goldbot.ops import gates_phase as gp
    if ctx.settings is None:
        return _skipped("phase_gates", "settings do not load")
    try:
        rep = gp.evaluate_gates(ctx.state_dir, ctx.settings, ctx.now)
    except Exception as exc:                 # evidence files are best effort here; the gate report shows the detail
        return Check(name="phase_gates", status="warn", reason=f"gate evaluation failed: {type(exc).__name__}: {exc}"[:300])
    nxt = next((g for g in rep.gates if not g.recorded), None)
    if nxt is None:
        return Check(name="phase_gates", status="ok", reason="every roadmap gate recorded")
    req = [i for i in nxt.items if i.required]
    missing = [i.name for i in req if not i.met]
    if nxt.met:
        return Check(name="phase_gates", status="ok",
                     reason=f"next gate {nxt.gate}: MET on the evidence; the owner records it with run.py record-gate")
    return Check(name="phase_gates", status="ok",
                 reason=f"next gate {nxt.gate}: not met ({len(req) - len(missing)}/{len(req)} items; missing {', '.join(missing)})"[:400])


def _running(job: dict[str, Any]) -> pd.Timestamp | None:
    started, finished = job.get("last_started"), job.get("last_finished")
    if not started:
        return None
    s = pd.Timestamp(started)
    if finished is None or s > pd.Timestamp(finished):
        return s
    return None


def check_scheduler(ctx: HealthContext) -> list[Check]:
    """One heartbeat check plus one check per configured job."""
    f = ctx.state_dir / "scheduler.json"
    if not f.exists():
        return [Check(name="scheduler", status="fail", reason="no scheduler.json: scheduler never started")]
    try:
        raw = _read_json(f)
        hb = pd.Timestamp(raw["ts"])
        jobs: dict[str, dict[str, Any]] = raw.get("jobs", {})
    except (ValueError, OSError, KeyError) as exc:
        return [Check(name="scheduler", status="fail", reason=f"scheduler.json unreadable: {exc}")]
    running = {n: s for n, j in jobs.items() if (s := _running(j)) is not None}
    hb_age = (ctx.now - hb).total_seconds()
    if hb_age <= SCHEDULER_HEARTBEAT_FAIL_S:
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


def check_costs(ctx: HealthContext, account_id: str) -> Check:
    name = f"costs:{account_id}"
    f = ctx.state_dir / f"costs_{account_id}.json"
    if not f.exists():
        return Check(name=name, status="warn", reason="no cost table yet (nightly_costs has not run): engine uses priors")
    try:
        raw = _read_json(f)
        built = pd.Timestamp(raw["built_utc"])
        built = built.tz_localize("UTC") if built.tzinfo is None else built
    except (ValueError, OSError, KeyError) as exc:
        return Check(name=name, status="fail", reason=f"cost table unreadable: {exc}")
    days = (ctx.now - built).total_seconds() / 86400
    msg = f"built {days:.1f} d ago"
    if days > COSTS_FAIL_DAYS:
        return Check(name=name, status="fail", reason=msg + f" (> {COSTS_FAIL_DAYS:.0f} d)")
    if days > COSTS_WARN_DAYS:
        return Check(name=name, status="warn", reason=msg)
    if raw.get("swap_long_usd_per_lot") is None or raw.get("swap_short_usd_per_lot") is None:
        # research and retraining then charge the settings' swap prior (see state/broker_terms_<account>.json)
        return Check(name=name, status="warn", reason=msg + "; no measured swap from the terminal: swap prior in use")
    return Check(name=name, status="ok", reason=msg)


def check_costs_published(ctx: HealthContext) -> Check:
    """With `costs.publish_release` on, research.yml charges the table on release costs-v1: warn when it was never
    published or is older than COSTS_PUBLISHED_WARN_DAYS (research is then on stale costs or the priors)."""
    name = "costs:published"
    f = ctx.state_dir / "costs_published.json"     # goldbot.ops.jobs.COSTS_PUBLISHED_FILE
    try:
        raw = _read_json(f) if f.exists() else {}
    except (ValueError, OSError) as exc:
        return Check(name=name, status="warn", reason=f"publish record unreadable: {exc}")
    last = f"; last attempt: {raw['last_result']}" if raw.get("last_result") else ""
    if not raw.get("published_utc"):
        return Check(name=name, status="warn",
                     reason="cost table never published to release costs-v1: research charges the priors" + last)
    published = pd.Timestamp(raw["published_utc"])
    published = published.tz_localize("UTC") if published.tzinfo is None else published
    days = (ctx.now - published).total_seconds() / 86400
    msg = f"published {days:.1f} d ago"
    if days > COSTS_PUBLISHED_WARN_DAYS:
        return Check(name=name, status="warn", reason=msg + f" (> {COSTS_PUBLISHED_WARN_DAYS:.0f} d): research uses a "
                                                            "stale table" + last)
    return Check(name=name, status="ok", reason=msg)


def check_data_quality(ctx: HealthContext) -> Check:
    """Design (Data quality): errors quarantine the batch and alert. Error-severity events (duplicate stamps, bid >
    ask, out-of-order bars) recorded by the loaders or the engines in the last DQ_WINDOW_HOURS warn here; their rows
    are in `bars_quarantine`, not in the bar tables, and an engine blocks entries while its feed is in error."""
    if ctx.settings is None:
        return Check(name="data_quality", status="warn", reason="settings unreadable: data root unknown")
    root = Path(ctx.settings.data_root)
    if not (root / "dq_events").exists():
        return Check(name="data_quality", status="ok", reason="no data-quality events recorded")
    from goldbot.data.store import Store
    try:
        ev = Store(root).read("dq_events", start=ctx.now - pd.Timedelta(hours=DQ_WINDOW_HOURS), end=ctx.now)
    except (ValueError, OSError) as exc:
        return Check(name="data_quality", status="warn", reason=f"dq_events unreadable: {exc}")
    errs = ev[ev["severity"] == "error"] if not ev.empty and "severity" in ev.columns else ev.iloc[0:0]
    if errs.empty:
        return Check(name="data_quality", status="ok", reason=f"no error events in {DQ_WINDOW_HOURS} h ({len(ev)} warnings)")
    kinds = ", ".join(f"{k} x{n}" for k, n in errs["check"].value_counts().items())
    return Check(name="data_quality", status="warn",
                 reason=f"{len(errs)} error events in {DQ_WINDOW_HOURS} h ({kinds}): rows quarantined, see bars_quarantine")


def check_drift(ctx: HealthContext) -> Check:
    """state/drift.json (daily drift_watch): system halt fails, agent halts and size-downs warn, a stale file warns."""
    f = ctx.state_dir / "drift.json"
    if not f.exists():
        return Check(name="drift", status="ok", reason="no drift report yet (no champion, or drift_watch not run)")
    try:
        d = _read_json(f)
    except (ValueError, OSError) as exc:
        return Check(name="drift", status="fail", reason=f"drift.json unreadable (entries halted): {exc}")
    if d.get("system_halt"):
        why = "; ".join(d["system_halt"].get("reasons") or [])
        return Check(name="drift", status="fail",
                     reason=f"SYSTEM HALT pending review ({why}): python -m goldbot.ops.run drift-review")
    parts, status = [], "ok"
    if d.get("halted"):
        status = "warn"
        parts.append(f"halted agents: {', '.join(sorted(d['halted']))}")
    if d.get("size_factor"):
        status = "warn"
        parts.append(f"sized down: {', '.join(sorted(d['size_factor']))}")
    age_h = (ctx.now - pd.Timestamp(d.get("ts"))).total_seconds() / 3600 if d.get("ts") else float("inf")
    if age_h > 72:
        status = "warn"
        parts.append(f"report {age_h:.0f} h old")
    return Check(name="drift", status=cast(Status, status), reason="; ".join(parts) or "no drift")


def check_model_watch(ctx: HealthContext) -> Check:
    """state/model_watch.json (daily model_watch): a new champion whose watch has too few trades for any CUSUM alarm
    at the 5% rate (`cannot_alarm`) warns, so a silent "no alarm" is never mistaken for a healthy watch."""
    f = ctx.state_dir / "model_watch.json"          # goldbot.ops.jobs.MODEL_WATCH_FILE
    if not f.exists():
        return Check(name="model_watch", status="ok", reason="no champion watch report yet")
    try:
        agents = (_read_json(f) or {}).get("agents") or {}
    except (ValueError, OSError, AttributeError) as exc:
        return Check(name="model_watch", status="warn", reason=f"model_watch.json unreadable: {exc}"[:300])
    blind = sorted(a for a, v in agents.items() if isinstance(v, dict) and v.get("action") == "cannot_alarm")
    if blind:
        return Check(name="model_watch", status="warn",
                     reason=f"champion watch cannot alarm (too few trades) for {', '.join(blind)}: relies on "
                            "drift_watch and the drawdown halt")
    return Check(name="model_watch", status="ok", reason=f"{len(agents)} champion(s) watched")


def check_deploy(ctx: HealthContext) -> Check:
    """The last deploy attempt (state/deploys.jsonl, written by goldbot-deploy): a rollback warns, a failed rollback
    fails; a deploy is only ever started by the owner (Telegram [Deploy] or `sudo goldbot-deploy`)."""
    f = ctx.state_dir / "deploys.jsonl"
    if not f.exists():
        return Check(name="deploy", status="ok", reason="no deploy recorded yet")
    try:
        last = json.loads(f.read_text().splitlines()[-1])
    except (ValueError, OSError, IndexError) as exc:
        return Check(name="deploy", status="warn", reason=f"deploys.jsonl unreadable: {exc}")
    msg = f"{last.get('result')} {str(last.get('to', ''))[:8]} on {last.get('role')} at {last.get('ts')}: {last.get('detail', '')}"
    status = {"failed": "fail", "rolled_back": "warn", "refused": "warn"}.get(str(last.get("result")), "ok")
    return Check(name="deploy", status=cast(Status, status), reason=msg)


def check_backup_age(ctx: HealthContext) -> Check:
    """Age of the last successful encrypted off-host backup (state/backup_last.json, goldbot/ops/backup.py). Error
    texts in the record are masked by the backup module before they are written."""
    name = "backup_age"
    f = ctx.state_dir / "backup_last.json"
    if not f.exists():
        return Check(name=name, status="warn", reason="no backup recorded yet (RUNBOOK 'Backups': restic keys, "
                                                      "then `goldbot run backup --init`)")
    try:
        rec = _read_json(f)
        last_ok = pd.Timestamp(rec["last_ok_ts"]) if rec.get("last_ok_ts") else None
        attempt = pd.Timestamp(rec["ts"])
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return Check(name=name, status="warn", reason=f"backup_last.json unreadable: {exc}"[:300])
    failed = "" if rec.get("ok") else f"; last attempt {attempt:%Y-%m-%d %H:%M} failed: {str(rec.get('error'))[:200]}"
    if last_ok is None:
        return Check(name=name, status="warn", reason="no successful backup yet" + failed)
    age_h = (ctx.now - last_ok).total_seconds() / 3600
    msg = f"last good backup {_age(age_h * 3600)} ago" + failed
    repo = rec.get("repo_bytes")
    if age_h > BACKUP_FAIL_H:
        return Check(name=name, status="fail", reason=msg + f" (> {BACKUP_FAIL_H:.0f} h)")
    if age_h > BACKUP_WARN_H:
        return Check(name=name, status="warn", reason=msg + f" (> {BACKUP_WARN_H:.0f} h)")
    if failed:
        return Check(name=name, status="warn", reason=msg)
    if isinstance(repo, (int, float)) and repo > BACKUP_REPO_WARN_BYTES:
        return Check(name=name, status="warn", reason=msg + f"; repository {repo / 1e9:.1f} GB of the 20 GB free tier")
    if rec.get("model_problems"):
        return Check(name=name, status="warn", reason=msg + "; " + "; ".join(rec["model_problems"])[:200])
    return Check(name=name, status="ok", reason=msg)


def check_backup_prune(ctx: HealthContext) -> Check:
    """Retention runs only from the owner's Mac (the brain's key appends): the newest retention marker, as the brain's
    last backup read it (state/backup_last.json `last_prune_ts`), warns after 45 days. With no marker yet it warns
    only once the first good backup is 45 days old."""
    name = "backup_prune"
    f = ctx.state_dir / "backup_last.json"
    if not f.exists():
        return Check(name=name, status="ok", reason="no backup yet")
    try:
        rec = _read_json(f)
        pruned = pd.Timestamp(rec["last_prune_ts"]) if rec.get("last_prune_ts") else None
        first = pd.Timestamp(rec["first_ok_ts"]) if rec.get("first_ok_ts") else None
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return Check(name=name, status="warn", reason=f"backup_last.json unreadable: {exc}"[:300])
    how = "run scripts/backup_retention.sh on your Mac (RUNBOOK 0.6)"
    if pruned is None:
        if first is not None and (ctx.now - first).total_seconds() / 86400 > PRUNE_WARN_DAYS:
            return Check(name=name, status="warn", reason=f"no retention run recorded in {PRUNE_WARN_DAYS:.0f} days of "
                                                          f"backups: {how}")
        return Check(name=name, status="ok", reason=f"no retention run yet (monthly; {how})")
    age_d = (ctx.now - pruned).total_seconds() / 86400
    msg = f"last retention run {_age(age_d * 86400)} ago"
    if age_d > PRUNE_WARN_DAYS:
        return Check(name=name, status="warn", reason=msg + f" (> {PRUNE_WARN_DAYS:.0f} d): {how}")
    return Check(name=name, status="ok", reason=msg)


def check_restore_drill(ctx: HealthContext) -> Check:
    """The weekly restore drill (state/restore_drill_last.json): a failed drill fails, a stale one warns."""
    name = "restore_drill"
    f = ctx.state_dir / "restore_drill_last.json"
    if not f.exists():
        return Check(name=name, status="warn", reason="no restore drill yet (weekly, Sunday 10:00 UTC; "
                                                      "`goldbot run restore-drill` runs one now)")
    try:
        rec = _read_json(f)
        ts = pd.Timestamp(rec["ts"])
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return Check(name=name, status="warn", reason=f"restore_drill_last.json unreadable: {exc}"[:300])
    if not rec.get("ok"):
        why = rec.get("error") or "; ".join(rec.get("problems") or [])
        return Check(name=name, status="fail", reason=f"drill at {ts:%Y-%m-%d %H:%M} failed: {str(why)[:300]}")
    age_d = (ctx.now - ts).total_seconds() / 86400
    msg = f"last drill {_age(age_d * 86400)} ago verified {rec.get('files', 0)} files"
    if age_d > DRILL_WARN_DAYS:
        return Check(name=name, status="warn", reason=msg + f" (> {DRILL_WARN_DAYS:.0f} d)")
    return Check(name=name, status="ok", reason=msg)


def check_news(ctx: HealthContext) -> Check:
    if ctx.settings is not None and not ctx.settings.news.feeds:
        return Check(name="news", status="ok", reason="no feeds configured")
    f = ctx.state_dir / "news_feeds.json"
    if not f.exists():
        return Check(name="news", status="warn", reason="no news_feeds.json yet: news service not started")
    try:
        feeds: dict[str, dict[str, Any]] = _read_json(f)
    except (ValueError, OSError) as exc:
        return Check(name="news", status="fail", reason=f"news_feeds.json unreadable: {exc}")
    if not feeds:
        return Check(name="news", status="warn", reason="news_feeds.json lists no feeds")
    poll = ctx.settings.news.poll_seconds if ctx.settings is not None else 300
    last = max(pd.Timestamp(h["ts"]) for h in feeds.values() if h.get("ts")) if any(h.get("ts") for h in feeds.values()) else None
    age = (ctx.now - last).total_seconds() if last is not None else float("inf")
    bad = sorted(n for n, h in feeds.items() if not h.get("ok"))
    if age > NEWS_STALE_POLLS * poll:
        return Check(name="news", status="fail", reason=f"last poll {_age(age)} ago (> {NEWS_STALE_POLLS} polls): news service down")
    if len(bad) == len(feeds):
        return Check(name="news", status="fail", reason=f"all {len(feeds)} feeds failing: no shock blackout possible")
    if bad:
        errs = "; ".join(f"{n}: {str(feeds[n].get('error', '?'))[:80]}" for n in bad)
        return Check(name="news", status="warn", reason=f"{len(bad)}/{len(feeds)} feeds failing ({errs})")
    return Check(name="news", status="ok", reason=f"{len(feeds)} feeds ok, last poll {_age(age)} ago")


def check_agent_spend(ctx: HealthContext) -> Check:
    cap = ctx.settings.agents.monthly_cap_usd if ctx.settings is not None else None
    if cap is None:
        return _skipped("agent_spend", "settings did not load")
    if cap <= 0:
        return Check(name="agent_spend", status="ok", reason="monthly cap is 0: staff agents and headline scoring off")
    f = ctx.state_dir / "agent_spend.json"
    try:
        spent = float(_read_json(f).get(f"{ctx.now:%Y-%m}", 0.0)) if f.exists() else 0.0
    except (ValueError, OSError, AttributeError) as exc:
        return Check(name="agent_spend", status="fail", reason=f"agent_spend.json unreadable: {exc}")
    frac = spent / cap
    msg = f"{ctx.now:%Y-%m}: ${spent:.2f} of ${cap:.2f} ({100 * frac:.0f}%)"
    if frac >= 1.0:
        return Check(name="agent_spend", status="fail", reason=msg + ": cap reached, agents and headline scoring stopped")
    if frac >= SPEND_WARN_FRACTION:
        return Check(name="agent_spend", status="warn", reason=msg)
    return Check(name="agent_spend", status="ok", reason=msg)


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


def check_alert_loop(ctx: HealthContext) -> Check:
    f = ctx.state_dir / WATCH_FILE
    if not f.exists():
        return Check(name="alerts", status="warn", reason="health alerts never ran (Telegram service not started)")
    try:
        ts = WatchState.model_validate_json(f.read_text(encoding="utf-8")).ts
    except (ValueError, OSError) as exc:
        return Check(name="alerts", status="warn", reason=f"health_last.json unreadable: {exc}")
    age = (ctx.now - ts).total_seconds() if ts is not None else float("inf")
    if age > WATCH_STALE_S:
        return Check(name="alerts", status="warn", reason=f"last health alert pass {_age(age)} ago: Telegram service down?")
    return Check(name="alerts", status="ok", reason=f"last pass {_age(age)} ago")


# --------------------------------------------------------------------------------------------- operational alerts
log = logging.getLogger("goldbot.health")


def heartbeat_path(state_dir: str | Path, service: str) -> Path:
    return Path(state_dir) / f"heartbeat_{service}.json"      # not engine_*: the supervisor globs those


class Heartbeat:
    """state/heartbeat_<service>.json, written at most every `every_s` (call `beat()` from a loop of any period).
    A write error is logged, never raised: a full disk must not stop the supervisor's loop."""

    def __init__(self, state_dir: str | Path, service: str, every_s: float = HEARTBEAT_EVERY_S,
                 clock: Callable[[], float] = time.time):
        self.path = heartbeat_path(state_dir, service)
        self.service, self.every_s, self.clock = service, every_s, clock
        self._last: float | None = None

    def beat(self) -> bool:
        now = self.clock()
        if self._last is not None and now - self._last < self.every_s:
            return False
        try:
            write_atomic(self.path, json.dumps({"service": self.service, "ts": now}), durable=False)
        except OSError:
            log.exception("heartbeat %s", self.service)
            return False
        self._last = now
        return True


def start_heartbeat(state_dir: str | Path, service: str, every_s: float = HEARTBEAT_EVERY_S) -> threading.Thread:
    """A daemon thread beating for a service whose loop lives elsewhere (uvicorn, the Telegram bot, a scheduler
    blocked in a long job): it proves the process is alive; the service's own checks judge its loop."""
    hb = Heartbeat(state_dir, service, every_s)

    def loop() -> None:  # pragma: no cover - runs for the life of the process
        while True:
            hb.beat()
            time.sleep(every_s)
    t = threading.Thread(target=loop, name=f"heartbeat-{service}", daemon=True)
    t.start()
    return t


def check_heartbeat(ctx: HealthContext, service: str) -> Check:
    """S5: a service silent for HEARTBEAT_SILENT_S fails (the alert); one that never wrote a heartbeat warns."""
    name = f"heartbeat:{service}"
    f = heartbeat_path(ctx.state_dir, service)
    if not f.exists():
        return Check(name=name, status="warn", reason=f"no heartbeat_{service}.json: service never started (or predates heartbeats)")
    try:
        age = _epoch(ctx.now) - float(_read_json(f)["ts"])
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return Check(name=name, status="fail", reason=f"heartbeat unreadable: {exc}")
    if age > HEARTBEAT_SILENT_S:
        return Check(name=name, status="fail",
                     reason=f"{service} silent for {_age(age)} (> {HEARTBEAT_SILENT_S // 60} min): service down?")
    return Check(name=name, status="ok", reason=f"heartbeat {_age(age)}")


def check_loss_caps(ctx: HealthContext, account_id: str) -> list[Check]:
    """R11: the per-account daily and weekly loss caps from the engine's state (equity against the day/week-start
    equity, the RiskGate's own arithmetic). A tripped cap warns and is announced (NOTIFY_ON_WARN): the gate already
    blocks entries, this only tells the owner. Weekly stays tripped until the risk week rolls, so it is told once a week."""
    f = ctx.state_dir / f"engine_{account_id}.json"
    if ctx.settings is None or not f.exists():
        return []
    try:
        e = _read_json(f)
        eq = float(e.get("equity") or 0.0)
        starts = {"daily": float(e.get("day_start_equity") or 0.0), "weekly": float(e.get("week_start_equity") or 0.0)}
    except (ValueError, OSError, AttributeError, TypeError):
        return []                                   # check_engine reports the unreadable file
    caps = {"daily": ctx.settings.risk.daily_cap, "weekly": ctx.settings.risk.weekly_cap}
    out = []
    for period in ("daily", "weekly"):
        start = starts[period]
        loss = 1 - eq / start if start > 0 else 0.0
        msg = f"{account_id}: {period} loss {100 * loss:.2f}% of {'day' if period == 'daily' else 'week'}-start equity {start:,.2f}"
        if loss >= caps[period]:
            out.append(Check(name=f"{period}_cap:{account_id}", status="warn",
                             reason=f"{period.upper()} LOSS CAP hit: {msg} (cap {100 * caps[period]:.1f}%): no new entries until it rolls"))
        else:
            out.append(Check(name=f"{period}_cap:{account_id}", status="ok", reason=f"{msg} (cap {100 * caps[period]:.1f}%)"))
    return out


def _failed_order_retcodes(ctx: HealthContext, account_id: str, since: pd.Timestamp) -> list[tuple[pd.Timestamp, str, Any]]:
    """(ts, agent, retcode) of failed `order` rows in the engine's decisions journal; [] when it cannot be read."""
    if ctx.settings is None:
        return []
    from goldbot.data.store import Store
    try:
        d = Store(ctx.settings.data_root).read("decisions", source=account_id, symbol=ctx.settings.symbol, start=since, end=ctx.now)
        if d.empty:
            return []
        d = d[d["action"] == "order"]
        out = []
        for _, row in d.iterrows():
            detail = json.loads(row["detail"]) if isinstance(row["detail"], str) else {}
            if detail.get("ok") is False:
                out.append((pd.Timestamp(row["ts_utc"]), str(row["agent_id"]), detail.get("retcode")))
        return out
    except Exception:     # the journal is best effort here: the orders file alone raises the alert
        log.exception("decisions journal")
        return []


def check_failed_orders(ctx: HealthContext, account_id: str) -> list[Check]:
    """X6: an order the engine sent that failed after the adapter's retries (pending_orders row `rejected`) or that a
    restart found never filled (`unfilled`), in the last FAILED_ORDER_WINDOW_H. One check per order so each failure is
    announced once (NOTIFY_ON_WARN), with account, side, lots and the retcode from the decisions journal."""
    f = ctx.state_dir / f"orders_{account_id}.json"
    if not f.exists():
        return []
    try:
        sent = _read_json(f).get("sent", {})
        rows = [(cid, r) for cid, r in sent.items() if r.get("status") in ("rejected", "unfilled")]
    except (ValueError, OSError, AttributeError) as exc:
        return [Check(name=f"orders:{account_id}", status="fail", reason=f"orders_{account_id}.json unreadable (the engine refuses to start): {exc}")]
    since = ctx.now - pd.Timedelta(hours=FAILED_ORDER_WINDOW_H)
    recent = [(cid, r, pd.Timestamp(r["ts_utc"])) for cid, r in rows if r.get("ts_utc") and pd.Timestamp(r["ts_utc"]) >= since]
    if not recent:
        return []
    journal = _failed_order_retcodes(ctx, account_id, since)
    out = []
    for cid, r, ts in sorted(recent, key=lambda x: x[2]):
        rc = next((code for jts, agent, code in journal
                   if agent == r.get("agent_id") and abs((jts - ts).total_seconds()) <= 120), None)
        side = "BUY" if int(r.get("side", 0)) > 0 else "SELL"
        what = "FAILED_EXEC" if r["status"] == "rejected" else "never filled (found on restart)"
        out.append(Check(name=f"order_failed:{account_id}:{cid}", status="warn",
                         reason=f"{what} at {ts:%Y-%m-%d %H:%M} UTC: account {account_id}, {side} {float(r.get('lots', 0)):g} lots, "
                                f"agent {r.get('agent_id')}, retcode {rc if rc is not None else '?'}"))
    return out


def check_bridge(ctx: HealthContext, account_id: str) -> Check | None:
    """The MT5 bridge answers GET /health through the SSH tunnel (only when a bridge URL is in the keyring and a probe
    is configured). A bridge down means no ticks, no orders and no position management for that account."""
    try:
        url = ctx.get_secret(f"mt5-bridge-url-{account_id}")
    except Exception:
        url = None
    if not url or ctx.bridge_probe is None:
        return None
    name = f"bridge:{account_id}"
    why = ctx.bridge_probe(url)          # the URL is never quoted in a reason (it names the tunnel endpoint)
    if why is not None:
        return Check(name=name, status="fail", reason=f"bridge does not answer /health through the tunnel: {why[:160]}")
    return Check(name=name, status="ok", reason="bridge answers /health")


# --------------------------------------------------------------------------------------------- report
def run_checks(ctx: HealthContext, *, static_only: bool = False) -> HealthReport:
    """All checks in a fixed order. `static_only`: settings, accounts, secrets and disk (no service needs to run)."""
    checks = [check_settings(ctx), check_accounts(ctx), check_secrets(ctx), check_disk(ctx)]
    if not static_only:
        checks.append(check_supervisor(ctx))
        checks.append(check_halt(ctx))
        checks.append(check_phase(ctx))
        checks += [check_stop_rule(ctx), check_phase_gates(ctx)]
        for a in ctx.accounts:
            checks += [check_engine(ctx, a.account_id), check_risk_state(ctx, a.account_id)]
            checks += check_loss_caps(ctx, a.account_id) + check_failed_orders(ctx, a.account_id)
            bridge = check_bridge(ctx, a.account_id)
            checks += [bridge] if bridge is not None else []
        checks += [check_heartbeat(ctx, s) for s in HEARTBEAT_SERVICES]
        checks += check_scheduler(ctx)
        checks += [check_costs(ctx, a.account_id) for a in ctx.accounts]
        checks += [check_reconciliation(ctx, a.account_id) for a in ctx.accounts]   # D12 (goldbot/data/crossfeed.py)
        if ctx.settings is not None and ctx.settings.costs.publish_release:
            checks.append(check_costs_published(ctx))
        checks += [check_data_quality(ctx), check_drift(ctx), check_model_watch(ctx), check_deploy(ctx), check_backup_age(ctx), check_backup_prune(ctx), check_restore_drill(ctx), check_news(ctx), check_agent_spend(ctx), check_approvals(ctx), check_alert_loop(ctx)]
    return HealthReport(ts=ctx.now, status=_worst([c.status for c in checks]), checks=checks)


def render(report: HealthReport) -> str:
    width = max((len(c.name) for c in report.checks), default=10)
    lines = [f"{c.status.upper():4}  {c.name:<{width}}  {c.reason}" for c in report.checks]
    n = report.counts()
    lines.append(f"overall: {report.status} ({n['fail']} fail, {n['warn']} warn, {n['ok']} ok) at {report.ts:%Y-%m-%d %H:%M:%S} UTC")
    return "\n".join(lines)


def exit_code(report: HealthReport, baseline: HealthReport | None = None) -> int:
    """1 when something fails; with a baseline, only fails that were not already failing in it count."""
    fails = report.failing() - (baseline.failing() if baseline is not None else set())
    return 1 if fails else 0


# --------------------------------------------------------------------------------------------- alerts
WATCH_FILE = "health_last.json"
MAX_ALERT = 3900                   # Telegram's limit is 4096 characters


class WatchState(Record):
    ts: UtcTimestamp | None = None
    statuses: dict[str, Status] = Field(default_factory=dict)


# warnings the owner is told about (R11, X6; a missed backup night, so a broken backup is not first seen at 72 h)
NOTIFY_ON_WARN = ("daily_cap:", "weekly_cap:", "order_failed:", "backup_age", "backup_prune")


def _alerting(name: str, status: str) -> bool:
    return status == "fail" or (status == "warn" and name.startswith(NOTIFY_ON_WARN))


def alert_text(prev: dict[str, Status], report: HealthReport) -> str | None:
    """The message for checks that turned fail (or a NOTIFY_ON_WARN warning) since `prev` and for alerted checks that
    recovered; None when nothing changed state. A check that stays failing is not repeated; one that flaps
    fail -> ok -> fail alerts again. A notice that simply ages out of the report (an old failed order) is not
    announced as recovered."""
    now = {c.name: c.status for c in report.checks}
    new = [c for c in report.checks if _alerting(c.name, c.status) and prev.get(c.name) != c.status
           and prev.get(c.name) != "fail"]
    recovered = sorted(n for n, s in prev.items() if _alerting(n, s) and not _alerting(n, now.get(n, "ok"))
                       and (s == "fail" or n in now))
    if not new and not recovered:
        return None
    lines = []
    if new:
        nf = sum(c.status == "fail" for c in new)
        heads = [f"{nf} new failure(s)"] if nf else []
        heads += [f"{len(new) - nf} new notice(s)"] if len(new) > nf else []
        lines.append("goldbot health: " + ", ".join(heads))
        lines += [f"{c.status.upper()} {c.name}: {c.reason}" for c in new]
    if recovered:
        lines.append("recovered: " + ", ".join(f"{n} ({now.get(n, 'gone')})" for n in recovered))
    still = sorted(report.failing() - {c.name for c in new})
    if still:
        lines.append("still failing: " + ", ".join(still))
    text = "\n".join(lines)
    return text if len(text) <= MAX_ALERT else text[:MAX_ALERT - 40].rstrip() + "\n... (python -m goldbot.ops.run health)"


class HealthWatch:
    """Persisted dedupe for health alerts (state/health_last.json). `poll()` is called by the Telegram service."""

    def __init__(self, state_dir: str | Path):
        self.path = Path(state_dir) / WATCH_FILE
        try:
            self.state = WatchState.model_validate_json(self.path.read_text(encoding="utf-8")) if self.path.exists() else WatchState()
        except (ValueError, OSError):
            self.state = WatchState()       # unreadable: start clean (current fails are announced once more)

    def alert(self, report: HealthReport) -> str | None:
        """The text to send for this report (None: nothing changed state). Does not record anything."""
        return alert_text(self.state.statuses, report)

    def record(self, report: HealthReport) -> None:
        """Remember this report's statuses; call after the alert was delivered so a failed send is retried."""
        self.state = WatchState(ts=report.ts, statuses={c.name: c.status for c in report.checks})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(self.state.model_dump_json(), encoding="utf-8")
        tmp.replace(self.path)

    def poll(self, report: HealthReport) -> str | None:
        text = self.alert(report)
        self.record(report)
        return text


# --------------------------------------------------------------------------------------------- CLI
def _load_report(path: str) -> HealthReport | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return HealthReport.model_validate_json(p.read_text(encoding="utf-8"))
    except ValueError:
        return None


def main(argv: list[str], ctx: HealthContext | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m goldbot.ops.run health")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    ap.add_argument("--static", action="store_true", help="only checks that need no running service")
    ap.add_argument("--out", help="also write the JSON report to this file (UTF-8)")
    ap.add_argument("--baseline", help="a previous --out report: exit 1 only for fails not failing there")
    ap.add_argument("--state-dir", default="state")
    args = ap.parse_args(argv)
    ctx = ctx or HealthContext.from_runtime(args.state_dir)
    report = run_checks(ctx, static_only=args.static)
    print(report.model_dump_json(indent=1) if args.json else render(report))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(report.model_dump_json(indent=1), encoding="utf-8")
    baseline = _load_report(args.baseline) if args.baseline else None
    if args.baseline and baseline is not None and baseline.failing():
        print(f"baseline already failing (ignored for the exit code): {', '.join(sorted(baseline.failing()))}", file=sys.stderr)
    return exit_code(report, baseline)
